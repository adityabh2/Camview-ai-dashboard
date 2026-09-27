"""
geo.py — Operations map (V2): city/centre positions (geocoded once, cached, admin-overridable) and per-location
alert counts.

Camview sends no coordinates. A camera carries its centre (`center`, `centerCode`) and the centre's `city` and
`state`. Positions therefore come from two honest sources only:

* **admin**  — an administrator ('settings.manage') set the exact position of a centre or a city. Always wins.
* **osm**    — the *city* (city + state strings, nothing else) was looked up ONCE in OpenStreetMap Nominatim,
               server-side, at most one request per second, in a background thread, and cached forever
               (a "not found" answer is cached too).

A centre without its own position is drawn around its city's position with a small deterministic offset and
marked "approximate (city)". Anything without a position is listed as "not on the map" — no position is invented.

Table geo_places(key, lat, lng, source, label, status, updated_at, updated_by):
    key  "city|<city>|<state>"                 (lower-case, trimmed)
         "centre|<projectId>|<centreCode>"
    status 'found' | 'not_found' (osm rows) · 'admin' rows always carry lat/lng.

Geocoding is disabled when CAMVIEW_GEOCODE=0 or CAMVIEW_TESTING=1 (tests never touch the network).
"""

import logging
import math
import os
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import changes
import db

log = logging.getLogger("camview.geo")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "CAMVIEW-Command-Center/1.0"
MIN_INTERVAL = 1.1              # Nominatim usage policy: max 1 request per second
RETRY_AFTER = 15 * 60           # a network error (not a "not found") is retried after this many seconds
SPREAD_DEG = 0.012              # ~1.3 km: centres of one city are spread in a small circle around it

RULE = ("ALARM = at least one critical or high alert waiting for review · WARNING = other alerts waiting for "
        "review · OK = nothing waiting for review. Same rule as the centre board.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS geo_places (
    key TEXT PRIMARY KEY,
    lat REAL,
    lng REAL,
    source TEXT NOT NULL,
    label TEXT,
    status TEXT,
    updated_at TEXT,
    updated_by TEXT
);
"""


def init_schema():
    """Creates this module's tables (called by bootstrap.run)."""
    with db.quiet(), db.connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------

def _norm(s):
    return " ".join(str(s or "").split()).strip()


def city_key(city, state):
    city, state = _norm(city).lower(), _norm(state).lower()
    return f"city|{city}|{state}" if city else None


def centre_key(project_id, centre_code):
    return f"centre|{project_id}|{centre_code}" if centre_code not in (None, "") else None


def valid_key(key):
    if not isinstance(key, str) or len(key) > 300:
        return False
    parts = key.split("|")
    return (parts[0] == "city" and len(parts) == 3 and bool(parts[1].strip())) or \
           (parts[0] == "centre" and len(parts) == 3 and bool(parts[1].strip()) and bool(parts[2].strip()))


# ---------------------------------------------------------------------------
# stored positions
# ---------------------------------------------------------------------------

def places():
    """{key: row} of every stored position / cached lookup."""
    try:
        return {r["key"]: r for r in db.rows("SELECT * FROM geo_places")}
    except Exception:                            # table missing on a very old DB: no positions, never an error
        return {}


def set_admin(key, lat, lng, label, user):
    old = db.one("SELECT * FROM geo_places WHERE key = ?", (key,))
    db.execute("INSERT INTO geo_places (key, lat, lng, source, label, status, updated_at, updated_by) "
               "VALUES (?,?,?,'admin',?,'admin',?,?) ON CONFLICT(key) DO UPDATE SET lat=excluded.lat, "
               "lng=excluded.lng, source='admin', label=excluded.label, status='admin', "
               "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
               (key, lat, lng, label, db.now_iso(), (user or {}).get("id")))
    db.audit("map.place.set", user, "geo_place", key, old and {"lat": old["lat"], "lng": old["lng"], "source": old["source"]},
             {"lat": lat, "lng": lng, "label": label})
    return db.one("SELECT * FROM geo_places WHERE key = ?", (key,))


def clear_admin(key, user):
    """Removes an administrator's position. A city then goes back to its (re-)geocoded position."""
    old = db.one("SELECT * FROM geo_places WHERE key = ? AND source = 'admin'", (key,))
    if not old:
        return False
    db.execute("DELETE FROM geo_places WHERE key = ? AND source = 'admin'", (key,))
    db.audit("map.place.clear", user, "geo_place", key, {"lat": old["lat"], "lng": old["lng"]}, None)
    return True


# ---------------------------------------------------------------------------
# geocoding (OpenStreetMap Nominatim, background, 1 req/s, cached forever)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_pending = {}          # key -> (city, state)
_failed = {}           # key -> monotonic time of the last network failure (retried after RETRY_AFTER)
_thread = None
_last_call = 0.0


def enabled():
    return os.environ.get("CAMVIEW_GEOCODE", "1").strip() != "0" and os.environ.get("CAMVIEW_TESTING") != "1"


def _nominatim(city, state):
    """One lookup. Only the city and state strings are sent. Returns (lat, lng, label) or None when not found;
    raises on a network / HTTP error."""
    import requests
    params = {"format": "json", "limit": 1, "countrycodes": "in", "city": city}
    if state:
        params["state"] = state
    r = requests.get(NOMINATIM_URL, params=params, timeout=15,
                     headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    hit = data[0]
    return float(hit["lat"]), float(hit["lon"]), str(hit.get("display_name") or "")[:200]


def request(pairs, known=None):
    """Queues the (city, state) pairs that have no cached answer yet; never blocks. Returns how many are queued."""
    if not enabled():
        return 0
    known = places() if known is None else known
    now = time.monotonic()
    with _lock:
        for city, state in pairs:
            key = city_key(city, state)
            if not key or key in known or key in _pending:
                continue
            if key in _failed and now - _failed[key] < RETRY_AFTER:
                continue
            _pending[key] = (_norm(city), _norm(state))
        start = bool(_pending) and (_thread is None or not _thread.is_alive())
    if start:
        _start()
    return len(_pending)


def _start():
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_run, name="camview-geocoder", daemon=True)
        _thread.start()


def _run():
    wrote = False
    while True:
        try:
            done = work_once()
        except Exception:                                       # never let the thread die silently
            log.exception("Geocoder step failed")
            done = None
        if done is None:
            break
        wrote = wrote or done
    if wrote:
        changes.bump("geo")                                     # one re-read for everything resolved in this run


def work_once(fetch=None):
    """Resolves one queued city. Returns None when the queue is empty, True when a row was written,
    False on a network error (kept for a later retry)."""
    global _last_call
    with _lock:
        if not _pending:
            return None
        key, (city, state) = next(iter(_pending.items()))
    if db.one("SELECT 1 FROM geo_places WHERE key = ?", (key,)):
        with _lock:
            _pending.pop(key, None)
        return False
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    try:
        hit = (fetch or _nominatim)(city, state)
    except Exception as e:                                      # network / HTTP error: retry later, not cached
        log.warning("Geocoding %s, %s failed: %s", city, state, e)
        with _lock:
            _pending.pop(key, None)
            _failed[key] = time.monotonic()
        return False
    label = ", ".join(x for x in (city, state) if x)
    with db.quiet():
        if hit:
            db.execute("INSERT OR IGNORE INTO geo_places (key, lat, lng, source, label, status, updated_at) "
                       "VALUES (?,?,?,'osm',?,'found',?)", (key, hit[0], hit[1], label, db.now_iso()))
        else:
            db.execute("INSERT OR IGNORE INTO geo_places (key, lat, lng, source, label, status, updated_at) "
                       "VALUES (?,NULL,NULL,'osm',?,'not_found',?)", (key, label, db.now_iso()))
    with _lock:
        _pending.pop(key, None)
        _failed.pop(key, None)
    return True


def progress(city_keys, known):
    """{enabled, total, resolved, pending, failed, notFound, disabled} for the cities on the map.
    `failed` counts both cached "not found" answers and network failures waiting for a retry."""
    out = {"enabled": enabled(), "total": len(city_keys), "resolved": 0, "pending": 0, "failed": 0,
           "notFound": 0, "disabled": 0}
    for k in city_keys:
        st = city_status(k, known)
        if st in ("found", "admin"):
            out["resolved"] += 1
        elif st == "not_found":
            out["notFound"] += 1
            out["failed"] += 1
        elif st == "failed":
            out["failed"] += 1
        elif st == "disabled":
            out["disabled"] += 1
        else:
            out["pending"] += 1
    return out


def city_status(key, known):
    row = known.get(key)
    if row and row["lat"] is not None:
        return "admin" if row["source"] == "admin" else "found"
    if row:
        return "not_found"
    with _lock:
        if key in _pending:
            return "pending"
        if key in _failed:
            return "failed"
    return "pending" if enabled() else "disabled"


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def in_range(a, rng, now=None):
    if rng == "window":
        return True
    ts = a.get("lastInstance") or ""
    now = now or datetime.now(timezone.utc)
    if rng == "today":
        return ts[:10] == now.strftime("%Y-%m-%d")
    d = _parse(ts)
    return bool(d) and d >= now - timedelta(hours=24)


def spread(lat, lng, i, n):
    """Deterministic offset of the i-th of n centres around a city position (sunflower spiral)."""
    if n <= 1:
        return lat, lng
    golden = math.pi * (3 - math.sqrt(5))
    r = SPREAD_DEG * math.sqrt((i + 0.5) / n) * min(2.5, max(1.0, math.sqrt(n / 6)))
    t = i * golden
    return (round(lat + r * math.sin(t), 6),
            round(lng + r * math.cos(t) / max(0.2, math.cos(math.radians(lat))), 6))


def _centre_of(a):
    """(code, name) of an alarm's centre: the resolved context first, Camview's centre code otherwise."""
    c = (a.get("context") or {}).get("centre") or {}
    if c and not c.get("unmapped") and c.get("code"):
        return str(c["code"]), c.get("name") or a.get("centreName")
    if a.get("centreCode"):
        return str(a["centreCode"]), a.get("centreName")
    return None, None


def build(items, rng="window", known=None, label_for=None, now=None):
    """Per-centre points + per-city aggregates for the given (already scope-filtered) alarms."""
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    known = places() if known is None else known
    label_for = label_for or (lambda pid: {"code": str(pid), "name": None})
    groups = {}
    for a in items:
        pid = str(a.get("projectId") or "")
        code, name = _centre_of(a)
        key = centre_key(pid, code) or f"nocentre|{pid}"
        g = groups.setdefault(key, {"key": key, "code": code, "name": name, "projectId": pid, "alarms": [],
                                    "cities": Counter()})
        if not g["name"] and name:
            g["name"] = name
        g["alarms"].append(a)
        if a.get("cameraCity"):
            g["cities"][(_norm(a["cameraCity"]), _norm(a.get("cameraState")))] += 1

    points = []
    for g in groups.values():
        allk = g["alarms"]
        alerts = [a for a in allk if a.get("eventKind") != "camera_status"]
        ranged = [a for a in alerts if in_range(a, rng, now)]
        pending = [a for a in ranged if a.get("decision") == "pending"]
        urgent = [a for a in pending if a.get("priority") in ("critical", "high")]
        cams = {}
        for a in sorted(allk, key=lambda x: x.get("lastInstance") or ""):
            h = a.get("health") or {}
            cid = str(a.get("cameraId"))
            if h.get("available"):
                cams[cid] = (h.get("camera") or {}).get("state"), "FRAME_SYNC_FAILED" in (h.get("conditions") or [])
            else:
                cams.setdefault(cid, (None, False))
        latest = max(ranged, key=lambda a: a.get("lastInstance") or "", default=None)
        (city, state), _ = (g["cities"].most_common(1) or [((None, None), 0)])[0]
        proj = label_for(g["projectId"]) or {}
        points.append({
            "key": g["key"], "code": g["code"], "name": g["name"] or g["code"] or "Cameras without a centre",
            "noCentre": g["code"] is None,
            "city": city, "state": state, "cityKey": city_key(city, state),
            "projectId": g["projectId"], "projectCode": proj.get("code") or g["projectId"],
            "projectName": proj.get("name"),
            "counts": {
                "today": sum(1 for a in alerts if (a.get("lastInstance") or "")[:10] == today),
                "alerts": len(ranged), "pending": len(pending), "urgent": len(urgent),
                "critical": sum(1 for a in pending if a.get("priority") == "critical"),
                "valid": sum(1 for a in ranged if a.get("decision") == "valid"),
                "cameras": len(cams),
                "camerasOffline": sum(1 for s, _ in cams.values() if s == "offline"),
                "syncFailed": sum(1 for _, f in cams.values() if f),
            },
            "status": "alarm" if urgent else "warning" if pending else "ok",
            "lastAlarmAt": latest.get("lastInstance") if latest else None,
            "topTypes": [{"name": n, "count": c} for n, c in
                         Counter(a.get("alarmTypeName") for a in ranged if a.get("alarmTypeName")).most_common(3)],
        })

    # positions: admin centre → city (admin or osm) with a spread → none
    by_city = {}
    for p in points:
        if p["cityKey"]:
            by_city.setdefault(p["cityKey"], []).append(p)
    for ps in by_city.values():
        ps.sort(key=lambda p: p["key"])
    for p in points:
        own = known.get(p["key"]) if not p["noCentre"] else None
        city_row = known.get(p["cityKey"]) if p["cityKey"] else None
        if own and own["lat"] is not None:
            p.update(lat=own["lat"], lng=own["lng"], precision="admin", reason=None)
        elif city_row and city_row["lat"] is not None and not p["noCentre"]:
            sib = [x for x in by_city[p["cityKey"]] if not x["noCentre"] and not (
                known.get(x["key"]) and known[x["key"]]["lat"] is not None)]
            i = next((n for n, x in enumerate(sib) if x["key"] == p["key"]), 0)
            lat, lng = spread(city_row["lat"], city_row["lng"], i, len(sib))
            p.update(lat=lat, lng=lng, precision="city", reason=None)
        else:
            p.update(lat=None, lng=None, precision=None,
                     reason="no_centre" if p["noCentre"] else "no_city" if not p["cityKey"]
                     else city_status(p["cityKey"], known))
    order = {"alarm": 0, "warning": 1, "ok": 2}
    points.sort(key=lambda p: (order[p["status"]], -p["counts"]["urgent"], -p["counts"]["pending"],
                               p["code"] or "~"))

    cities = []
    for ck, ps in by_city.items():
        row = known.get(ck)
        c = {"key": ck, "city": ps[0]["city"], "state": ps[0]["state"],
             "label": ", ".join(x for x in (ps[0]["city"], ps[0]["state"]) if x),
             "lat": row["lat"] if row else None, "lng": row["lng"] if row else None,
             "source": row["source"] if row and row["lat"] is not None else None,
             "geocode": city_status(ck, known),
             "centres": len(ps), "centreKeys": [p["key"] for p in ps]}
        for k in ("today", "alerts", "pending", "urgent", "critical", "valid", "cameras", "camerasOffline", "syncFailed"):
            c[k] = sum(p["counts"][k] for p in ps)
        c["status"] = "alarm" if c["urgent"] else "warning" if c["pending"] else "ok"
        cities.append(c)
    cities.sort(key=lambda c: (order[c["status"]], -c["pending"], c["label"]))

    located = [p for p in points if p["lat"] is not None]
    bounds = None
    if located:
        bounds = [[min(p["lat"] for p in located), min(p["lng"] for p in located)],
                  [max(p["lat"] for p in located), max(p["lng"] for p in located)]]
    counts = {s: sum(1 for p in points if p["status"] == s) for s in order}
    totals = {k: sum(p["counts"][k] for p in points) for k in
              ("today", "alerts", "pending", "urgent", "critical", "valid", "cameras", "camerasOffline", "syncFailed")}
    return {"points": points, "cities": cities, "bounds": bounds, "counts": counts, "totals": totals,
            "located": len(located), "unlocated": len(points) - len(located),
            "geocoding": progress(list(by_city), known), "range": rng, "rule": RULE}
