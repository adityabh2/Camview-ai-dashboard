"""
incidents.py — Incident management (V2): correlated alerts become incidents with owner, lifecycle, comments and audit.

An incident groups the alerts of one situation (e.g. several phones detected in the same centre within
minutes) so operators handle it once. Two parts:

* Correlation suggestions — computed on request from the alerts the user may see. Every suggestion
  carries the plain-English reason it was made (the rule, the window and the alert types); there is no
  score and no AI: the reason IS the complete logic.
    - same centre: at least `min_alerts` detection alerts of one centre, each within `gap_minutes` of
      the previous one;
    - same camera + same alert type repeated: 2+ separate alerts of one type on one camera within the
      same window (only for alerts not already part of a centre suggestion).
  Camera status events (online / offline, no evidence) and alerts marked INVALID are never correlated,
  and alerts already linked to an incident are left out.
* Persistent incidents — tables below. An alarm belongs to at most one OPEN incident (open or
  investigating); resolving or closing an incident releases its alarms. Every write goes through
  db.connect() (so screens learn about it through the data version) and is audited.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import db
from camview_client import ApiError

STATUSES = ("open", "investigating", "resolved", "closed")
ACTIVE = ("open", "investigating")                  # an alarm may be in only one of these at a time
SEVERITIES = ("critical", "high", "medium", "low")
STATUS_LABELS = {"open": "Open", "investigating": "Investigating", "resolved": "Resolved", "closed": "Closed"}
DEFAULT_GAP_MINUTES = 30
DEFAULT_MIN_ALERTS = 3
REPEAT_MIN_ALERTS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    severity TEXT NOT NULL DEFAULT 'medium',
    owner_id TEXT,
    owner_name TEXT,
    project_id TEXT,
    centre TEXT,
    reason TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT
);
CREATE INDEX IF NOT EXISTS ix_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS ix_incidents_project ON incidents(project_id);
CREATE TABLE IF NOT EXISTS incident_alarms (
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    alarm_id TEXT NOT NULL,
    project_id TEXT,
    added_at TEXT NOT NULL,
    added_by TEXT,
    snapshot TEXT,
    PRIMARY KEY (incident_id, alarm_id)
);
CREATE INDEX IF NOT EXISTS ix_incident_alarms_alarm ON incident_alarms(alarm_id);
CREATE TABLE IF NOT EXISTS incident_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    user_id TEXT,
    user_name TEXT,
    kind TEXT NOT NULL,
    body TEXT
);
CREATE INDEX IF NOT EXISTS ix_incident_events_incident ON incident_events(incident_id);
"""


def init_schema():
    """Creates this module's tables (called by bootstrap.run)."""
    with db.connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ts(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _when(a):
    """The moment an alert was raised (first instance; last instance when Camview sent no first)."""
    return _ts(a.get("firstInstance")) or _ts(a.get("lastInstance"))


def centre_of(a):
    """(code, label) of the alert's centre: resolved context first, then Camview's own centre code."""
    c = (a.get("context") or {}).get("centre") or {}
    if not c:
        c = next((n for n in (a.get("context") or {}).get("path", []) if n.get("level") == "centre"), {})
    code = c.get("code") or a.get("centreCode")
    if not code:
        return None, None
    name = c.get("name") if c.get("name") and c.get("name") != code else a.get("centreName")
    return str(code), f"{code} · {name}" if name and name != code else str(code)


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if t else None


def severity_from(alarms):
    ranks = [a.get("priority") for a in alarms if a.get("priority") in SEVERITIES]
    return min(ranks, key=SEVERITIES.index) if ranks else "medium"


def _minutes(first, last):
    return max(1, round((last - first).total_seconds() / 60)) if first and last else 1


def _types_line(alarms):
    counts = {}
    for a in alarms:
        t = a.get("alarmTypeName") or f"Type {a.get('alarmType')}"
        counts[t] = counts.get(t, 0) + 1
    return ", ".join(f"{t} ×{n}" for t, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])))


def linked_alarm_ids(active_only=False):
    q = "SELECT ia.alarm_id FROM incident_alarms ia JOIN incidents i ON i.id = ia.incident_id"
    if active_only:
        q += " WHERE i.status IN ('open','investigating')"
    return {r["alarm_id"] for r in db.rows(q)}


def _clusters(alarms, gap):
    """Splits time-ordered alerts into runs where each alert is within `gap` of the previous one."""
    out, run, prev = [], [], None
    for a in sorted(alarms, key=_when):
        t = _when(a)
        if run and prev and t - prev > gap:
            out.append(run)
            run = []
        run.append(a)
        prev = t
    if run:
        out.append(run)
    return out


# ---------------------------------------------------------------------------
# Correlation suggestions (explained)
# ---------------------------------------------------------------------------

def suggestions(alarms, gap_minutes=DEFAULT_GAP_MINUTES, min_alerts=DEFAULT_MIN_ALERTS, exclude=None):
    """Explained groups of related detection alerts. `alarms` are enriched, scope-filtered alarms;
    `exclude` the alarm ids already linked to an incident. Returns a list, most urgent first."""
    exclude = exclude if exclude is not None else linked_alarm_ids()
    gap = timedelta(minutes=gap_minutes)
    pool = [a for a in alarms
            if (a.get("eventKind") or "alert") != "camera_status"      # status events carry no evidence
            and a.get("decision") != "invalid"                          # a false alarm is not part of a situation
            and a["alarmId"] not in exclude and _when(a)]
    out, used = [], set()

    by_centre = {}
    for a in pool:
        code, _ = centre_of(a)
        if code:
            by_centre.setdefault((str(a.get("projectId")), code), []).append(a)
    for (pid, code), group in by_centre.items():
        for run in _clusters(group, gap):
            if len(run) < min_alerts:
                continue
            first, last = _when(run[0]), max(_ts(a.get("lastInstance")) or _when(a) for a in run)
            label = centre_of(run[0])[1]
            reason = (f"{len(run)} alerts at centre {code} within {_minutes(first, _when(run[-1]))} minutes: "
                      f"{_types_line(run)}")
            out.append(_suggestion("centre", run, pid, code, label, first, last, reason,
                                   f"Same centre, each alert within {gap_minutes} min of the previous one, "
                                   f"at least {min_alerts} alerts"))
            used.update(a["alarmId"] for a in run)

    by_camera = {}
    for a in pool:
        if a["alarmId"] in used or a.get("cameraId") in (None, ""):
            continue
        by_camera.setdefault((str(a.get("projectId")), str(a.get("cameraId")), str(a.get("alarmType"))), []).append(a)
    for (pid, cam, _t), group in by_camera.items():
        for run in _clusters(group, gap):
            if len(run) < REPEAT_MIN_ALERTS:
                continue
            first, last = _when(run[0]), max(_ts(a.get("lastInstance")) or _when(a) for a in run)
            code, label = centre_of(run[0])
            camera = run[0].get("cameraCode") or run[0].get("cameraNumber") or cam
            reason = (f"{run[0].get('alarmTypeName') or 'Same alert type'} ×{len(run)} on camera {camera}"
                      f"{f' (centre {code})' if code else ''} within {_minutes(first, _when(run[-1]))} minutes")
            out.append(_suggestion("camera", run, pid, code, label, first, last, reason,
                                   f"Same camera and same alert type repeated, each within {gap_minutes} min "
                                   f"of the previous one"))

    out.sort(key=lambda s: (SEVERITIES.index(s["severity"]), -(_ts(s["lastAt"]) or datetime.min.replace(
        tzinfo=timezone.utc)).timestamp(), -s["count"]))
    return out


def _suggestion(rule, run, pid, code, label, first, last, reason, rule_text):
    ids = sorted(a["alarmId"] for a in run)
    sid = "sg-" + hashlib.sha1(f"{pid}|{'|'.join(ids)}".encode()).hexdigest()[:12]
    sev = severity_from(run)
    kind = "centre" if rule == "centre" else "camera"
    title = (f"{len(run)} alerts at centre {code}" if kind == "centre"
             else f"Repeated {run[0].get('alarmTypeName') or 'alert'} on camera "
                  f"{run[0].get('cameraCode') or run[0].get('cameraId')}")
    return {"id": sid, "rule": rule, "ruleText": rule_text, "reason": reason, "title": title,
            "projectId": pid, "centre": code, "centreLabel": label, "count": len(run),
            "alarmIds": ids, "alarms": [{"alarmId": a["alarmId"], "projectId": a.get("projectId")} for a in run],
            "firstAt": _iso(first), "lastAt": _iso(last),
            "severity": sev, "highestPriority": sev, "_run": run}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _row(r):
    return {"id": r["id"], "ref": r["ref"], "title": r["title"], "status": r["status"],
            "statusLabel": STATUS_LABELS.get(r["status"], r["status"]), "severity": r["severity"],
            "owner": {"id": r["owner_id"], "name": r["owner_name"]} if r["owner_id"] else None,
            "projectId": r["project_id"], "centre": r["centre"], "reason": r["reason"],
            "createdBy": r["created_by"], "createdAt": r["created_at"], "updatedAt": r["updated_at"],
            "resolvedAt": r["resolved_at"], "resolution": r["resolution"],
            "alarmCount": r.get("alarm_count", 0)}


def get(iid):
    r = db.one("SELECT i.*, (SELECT COUNT(*) FROM incident_alarms WHERE incident_id=i.id) AS alarm_count "
               "FROM incidents i WHERE i.id = ?", (iid,))
    return _row(r) if r else None


def list_incidents(project_ids, f=None):
    """Incidents of the given projects, filtered; returns (items, counts per status)."""
    f = f or {}
    if not project_ids:
        return [], {s: 0 for s in STATUSES}
    ph = ",".join("?" for _ in project_ids)
    rows = db.rows("SELECT i.*, (SELECT COUNT(*) FROM incident_alarms WHERE incident_id=i.id) AS alarm_count "
                   f"FROM incidents i WHERE i.project_id IN ({ph}) ORDER BY i.updated_at DESC, i.id DESC",
                   [str(p) for p in project_ids])
    if f.get("alarm"):
        with_alarm = {r["incident_id"] for r in db.rows("SELECT incident_id FROM incident_alarms WHERE alarm_id=?",
                                                        (f["alarm"],))}
        rows = [r for r in rows if r["id"] in with_alarm]
    owner, s = f.get("owner"), (f.get("search") or "").strip().lower()
    if owner:
        rows = [r for r in rows if (owner == "none" and not r["owner_id"]) or str(r["owner_id"]) == str(owner)]
    if s:
        alarm_hits = {r["incident_id"] for r in db.rows("SELECT incident_id FROM incident_alarms WHERE LOWER(alarm_id) LIKE ?",
                                                        (f"%{s}%",))}
        rows = [r for r in rows if r["id"] in alarm_hits or any(
            s in str(r[k] or "").lower() for k in ("ref", "title", "centre", "reason", "owner_name"))]
    counts = {st: sum(1 for r in rows if r["status"] == st) for st in STATUSES}
    counts["active"] = counts["open"] + counts["investigating"]
    counts["all"] = len(rows)
    st = f.get("status") or "active"
    if st == "active":
        rows = [r for r in rows if r["status"] in ACTIVE]
    elif st in STATUSES:
        rows = [r for r in rows if r["status"] == st]
    return [_row(r) for r in rows], counts


def alarm_links(iid):
    return [{"alarmId": r["alarm_id"], "projectId": r["project_id"], "addedAt": r["added_at"], "addedBy": r["added_by"],
             "snapshot": db.jload(r["snapshot"], {}) or {}}
            for r in db.rows("SELECT * FROM incident_alarms WHERE incident_id=? ORDER BY added_at, alarm_id", (iid,))]


def events(iid):
    return [{"id": r["id"], "at": r["at"], "userId": r["user_id"], "userName": r["user_name"], "kind": r["kind"],
             "body": r["body"]}
            for r in db.rows("SELECT * FROM incident_events WHERE incident_id=? ORDER BY id", (iid,))]


def owners():
    """Who can own an incident: active internal users."""
    return db.rows("SELECT u.id, u.name FROM users u JOIN roles r ON r.id = u.role_id "
                   "WHERE r.audience = 'internal' AND u.status = 'active' ORDER BY u.name")


def resolved_on(project_ids, day, tz):
    """How many incidents of these projects were resolved on `day` (a date in the viewer's time zone)."""
    if not project_ids:
        return 0
    ph = ",".join("?" for _ in project_ids)
    n = 0
    for r in db.rows(f"SELECT resolved_at FROM incidents WHERE resolved_at IS NOT NULL AND project_id IN ({ph})",
                     [str(p) for p in project_ids]):
        t = _ts(r["resolved_at"])
        n += bool(t and t.astimezone(tz).date() == day)
    return n


# ---------------------------------------------------------------------------
# Writes — each in one transaction through db.connect(), each audited
# ---------------------------------------------------------------------------

def _event(conn, iid, user, kind, body, at):
    conn.execute("INSERT INTO incident_events (incident_id, at, user_id, user_name, kind, body) VALUES (?,?,?,?,?,?)",
                 (iid, at, (user or {}).get("id"), (user or {}).get("name"), kind, body))


def _conflicts(conn, alarm_ids, except_iid=None):
    """Alarm ids already in another OPEN incident → [(alarm_id, ref)]."""
    if not alarm_ids:
        return []
    ph = ",".join("?" for _ in alarm_ids)
    q = (f"SELECT ia.alarm_id, i.ref FROM incident_alarms ia JOIN incidents i ON i.id = ia.incident_id "
         f"WHERE i.status IN ('open','investigating') AND ia.alarm_id IN ({ph})")
    params = list(alarm_ids)
    if except_iid is not None:
        q += " AND i.id != ?"
        params.append(except_iid)
    return [(r["alarm_id"], r["ref"]) for r in conn.execute(q, params).fetchall()]


def _conflict_error(found):
    refs = sorted({ref for _, ref in found})
    ids = ", ".join(a for a, _ in found[:5]) + ("…" if len(found) > 5 else "")
    raise ApiError("conflict", f"Already part of an open incident ({', '.join(refs)}): {ids}. "
                               f"An alert can belong to only one open incident.", 409)


def _snap(slim_alarm):
    return json.dumps(slim_alarm, separators=(",", ":"), default=str)


def create(user, alarms, title, reason, severity=None, centre=None, slim=lambda a: a):
    """New incident from enriched alarms (all of one project, already scope-checked by the caller)."""
    if not alarms:
        raise ApiError("bad_request", "Choose at least one alert for the incident.", 400)
    pids = {str(a.get("projectId")) for a in alarms}
    if len(pids) > 1:
        raise ApiError("bad_request", "An incident groups alerts of one project; these alerts belong to "
                                      f"{len(pids)} projects.", 400)
    pid = pids.pop()
    severity = severity if severity in SEVERITIES else severity_from(alarms)
    centre = centre or centre_of(alarms[0])[0]
    title = (title or "").strip()[:200] or f"{len(alarms)} related alerts" + (f" at centre {centre}" if centre else "")
    now = db.now_iso()
    ids = [a["alarmId"] for a in alarms]
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")                 # the one-open-incident check and the insert are atomic
        found = _conflicts(conn, ids)
        if found:
            _conflict_error(found)
        iid = conn.execute("INSERT INTO incidents (title, status, severity, project_id, centre, reason, created_by, "
                           "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (title, "open", severity, pid, centre, (reason or "")[:1000] or None, user["name"], now, now)
                           ).lastrowid
        ref = f"INC-{iid:06d}"
        conn.execute("UPDATE incidents SET ref=? WHERE id=?", (ref, iid))
        conn.executemany("INSERT INTO incident_alarms (incident_id, alarm_id, project_id, added_at, added_by, snapshot) "
                         "VALUES (?,?,?,?,?,?)", [(iid, a["alarmId"], pid, now, user["name"], _snap(slim(a))) for a in alarms])
        _event(conn, iid, user, "created", f"Created with {len(alarms)} alert{'s' if len(alarms) != 1 else ''}"
                                           + (f" — {reason}" if reason else ""), now)
    db.audit("incident.create", user, "incident", ref, None, {"title": title, "severity": severity, "alarms": ids},
             project_id=pid, note=reason)
    return get(iid)


def update(iid, user, changes_, owner_lookup):
    """Status / owner / severity / title / resolution. Returns the updated incident."""
    inc = get(iid)
    now = db.now_iso()
    sets, evs, old, new = {}, [], {}, {}
    if "title" in changes_ and (changes_["title"] or "").strip() and changes_["title"].strip()[:200] != inc["title"]:
        sets["title"] = changes_["title"].strip()[:200]
        evs.append(("title", f"Title changed to “{sets['title']}”"))
    if "severity" in changes_ and changes_["severity"] != inc["severity"]:
        if changes_["severity"] not in SEVERITIES:
            raise ApiError("bad_request", f"Severity must be one of {', '.join(SEVERITIES)}.", 400)
        sets["severity"] = changes_["severity"]
        evs.append(("severity", f"Severity {inc['severity']} → {changes_['severity']}"))
    if "ownerId" in changes_:
        oid = changes_["ownerId"] or None
        if str(oid or "") != str((inc["owner"] or {}).get("id") or ""):
            name = None
            if oid:
                name = owner_lookup.get(str(oid))
                if not name:
                    raise ApiError("bad_request", "The owner must be an active internal user.", 400)
            sets["owner_id"], sets["owner_name"] = oid, name
            evs.append(("owner", f"Owner set to {name}" if name else "Owner removed"))
    resolution = (changes_.get("resolution") or "").strip()[:2000] if "resolution" in changes_ else None
    status = changes_.get("status")
    if status and status != inc["status"]:
        if status not in STATUSES:
            raise ApiError("bad_request", f"Status must be one of {', '.join(STATUSES)}.", 400)
        if status == "resolved" and not (resolution or inc["resolution"]):
            raise ApiError("bad_request", "Say how the incident was resolved.", 400)
        sets["status"] = status
        if status == "resolved":
            sets["resolved_at"] = now
        elif status in ACTIVE:
            sets["resolved_at"] = None
        text = f"Status {STATUS_LABELS[inc['status']]} → {STATUS_LABELS[status]}"
        if status == "resolved" and resolution:
            text += f" — {resolution}"
        evs.append(("status", text))
    if resolution is not None and resolution != (inc["resolution"] or ""):
        sets["resolution"] = resolution or None
        if sets.get("status") != "resolved":           # a resolve already carries its resolution
            evs.append(("status", f"Resolution: {resolution}" if resolution else "Resolution cleared"))
    if not sets:
        return inc
    sets["updated_at"] = now
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if sets.get("status") in ACTIVE and inc["status"] not in ACTIVE:
            # re-opening: its alarms must not have joined another open incident meanwhile
            found = _conflicts(conn, [r["alarm_id"] for r in conn.execute(
                "SELECT alarm_id FROM incident_alarms WHERE incident_id=?", (iid,)).fetchall()], except_iid=iid)
            if found:
                _conflict_error(found)
        conn.execute(f"UPDATE incidents SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", [*sets.values(), iid])
        for kind, text in evs:
            _event(conn, iid, user, kind, text, now)
    for k in sets:
        if k != "updated_at":
            old[k], new[k] = {"owner_id": (inc["owner"] or {}).get("id"), "owner_name": (inc["owner"] or {}).get("name"),
                              "resolved_at": inc["resolvedAt"]}.get(k, inc.get(k)), sets[k]
    action = "incident.status" if "status" in sets else "incident.owner" if "owner_id" in sets else "incident.update"
    db.audit(action, user, "incident", inc["ref"], old, new, project_id=inc["projectId"])
    return get(iid)


def comment(iid, user, text):
    text = (text or "").strip()
    if not text:
        raise ApiError("bad_request", "Write a comment first.", 400)
    text = text[:4000]
    inc = get(iid)
    now = db.now_iso()
    with db.connect() as conn:
        _event(conn, iid, user, "comment", text, now)
        conn.execute("UPDATE incidents SET updated_at=? WHERE id=?", (now, iid))
    db.audit("incident.comment", user, "incident", inc["ref"], None, None, project_id=inc["projectId"], note=text)
    return events(iid)


def change_alarms(iid, user, add=(), remove=(), slim=lambda a: a):
    """Adds enriched alarms / removes alarm ids. Adding needs an open incident; one alert stays at least."""
    inc = get(iid)
    add = [a for a in add if str(a.get("projectId")) == str(inc["projectId"])] if add else []
    remove = [str(x) for x in (remove or [])]
    now = db.now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        have = {r["alarm_id"] for r in conn.execute("SELECT alarm_id FROM incident_alarms WHERE incident_id=?", (iid,))}
        add = [a for a in add if a["alarmId"] not in have]
        remove = [x for x in remove if x in have]
        if add and inc["status"] not in ACTIVE:
            raise ApiError("conflict", "Re-open the incident before adding alerts.", 409)
        if len(have) + len(add) - len(remove) < 1:
            raise ApiError("bad_request", "An incident keeps at least one alert — close it instead.", 400)
        found = _conflicts(conn, [a["alarmId"] for a in add], except_iid=iid)
        if found:
            _conflict_error(found)
        for a in add:
            conn.execute("INSERT INTO incident_alarms (incident_id, alarm_id, project_id, added_at, added_by, snapshot) "
                         "VALUES (?,?,?,?,?,?)", (iid, a["alarmId"], inc["projectId"], now, user["name"], _snap(slim(a))))
            _event(conn, iid, user, "alarm_added",
                   f"Added alert {a['alarmId']} ({a.get('alarmTypeName') or 'alert'})", now)
        for x in remove:
            conn.execute("DELETE FROM incident_alarms WHERE incident_id=? AND alarm_id=?", (iid, x))
            _event(conn, iid, user, "alarm_removed", f"Removed alert {x}", now)
        if add or remove:
            conn.execute("UPDATE incidents SET updated_at=? WHERE id=?", (now, iid))
    if add or remove:
        db.audit("incident.alarms", user, "incident", inc["ref"], None,
                 {"added": [a["alarmId"] for a in add], "removed": remove}, project_id=inc["projectId"])
    return {"added": [a["alarmId"] for a in add], "removed": remove}
