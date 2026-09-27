"""
datasource.py — the alarm "working set" and the enrichment pipeline.

    RAW (Camview or demo)  ->  normalize  ->  context (nomenclature)
        ->  review / workflow / visibility / assignment  ->  derived flags

* Live mode: every alarm of each project (all pages, read in parallel, up to
  CAMVIEW_WINDOW_PAGES x 100), refreshed every CAMVIEW_CACHE_SECONDS in the
  background: screens always get the current data instantly and never wait for
  Camview (stale-while-refresh); last good data kept if a refresh fails.
* Demo mode: deterministic demo alarms plus a simulated live feed.
* New-alarm detection is real: an alarm id first seen after the baseline
  load is "new". New critical alarms create notifications.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import alarms as normalizer
import changes
import config
import db
import mock_data
import nomenclature
import workflow
from camview_client import ApiError, list_page

log = logging.getLogger("camview.data")

_feeds = {}
_feeds_lock = threading.Lock()
_enriched = {}              # project id -> (cache key, enriched items): see enriched()
_enriched_lock = threading.Lock()
ENRICHED_MAX_AGE = 60       # seconds an enriched working set may be reused when nothing changed
DEMO_NEW_ALARM_EVERY = 45   # seconds between simulated arrivals in demo mode
MISSING_GRACE = 5           # refreshes a record stays in the window after Camview's shifting pages skipped it
# Fields whose change means "the alarm data changed" for the screens. Media links are re-signed by Camview on
# every read and are deliberately not part of it: a new signature on the same frame is not a new alert.
SIGNATURE_FIELDS = ("alarmId", "lastActionType", "lastInstance", "totalTimesReported", "frameSyncStatus", "alarmState",
                    "priorityLevel", "ticketId", "suppressed")


class Feed:
    def __init__(self, project_id):
        self.project_id = str(project_id)
        self.items = []            # normalized (not user-specific) alarms
        self.fetched_at = 0.0
        self.last_success_at = None
        self.last_error = None
        self.lock = threading.Lock()
        self.seen = None
        self.new_ids = {}          # alarmId -> first-seen epoch
        self.total_elements = None
        self.truncated = False
        self.demo_tick = 0.0
        self.latest_alert_at = None    # newest lastInstance of a detection alert Camview has for the project
        self.latest_event_at = None    # newest camera status event
        self.missing = {}              # alarmId -> consecutive refreshes it was not returned (page shift)
        self.signature = None          # hash of SIGNATURE_FIELDS over the window: did the data itself change?
        self.changed_at = None         # last refresh that brought different alarm data
        self.enrich_lock = threading.Lock()


def reset():
    with _feeds_lock:
        _feeds.clear()
    with _enriched_lock:
        _enriched.clear()


def _signature(items):
    def h(v):
        return v if isinstance(v, (str, int, float, bool, type(None))) else repr(v)
    return hash(tuple(sorted(tuple(h(a.get(k)) for k in SIGNATURE_FIELDS) for a in items)))


def _feed(project_id):
    with _feeds_lock:
        f = _feeds.get(str(project_id))
        if f is None:
            f = _feeds[str(project_id)] = Feed(project_id)
        return f


# ---------------------------------------------------------------------------
# Dictionaries (alarm types / priorities come from the DB, not the UI)
# ---------------------------------------------------------------------------

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def dictionaries():
    types = {r["id"]: r for r in db.rows("SELECT * FROM alarm_types")}
    prios = {r["value"]: r for r in db.rows("SELECT * FROM priority_levels")}
    return types, prios


def _normalize(raw_items):
    types, prios = dictionaries()
    names = {k: v["name"] for k, v in types.items()}
    labels = {k: v["label"] for k, v in prios.items()} or config.PRIORITY_LABELS
    out, seen, skipped = [], set(), 0
    for it in raw_items or []:
        alarm = it.get("alarm") if isinstance(it, dict) else None
        src = alarm if isinstance(alarm, dict) else it if isinstance(it, dict) else {}
        aid = src.get("alarmId")
        derived = False
        if aid in (None, "") and src.get("cameraId") not in (None, "") and src.get("firstInstance"):
            # Camview sends some events (e.g. camera online/offline, type 10) with alarmId = null.
            # A stable id from the event's own fields keeps them visible and openable; repeats merge.
            stamp = "".join(ch for ch in str(src["firstInstance"]) if ch.isdigit())[:17]
            aid = f"EVT-{src.get('projectId')}-{src['cameraId']}-{src.get('alarmType')}-{stamp}"
            it = {**it, "alarm": {**src, "alarmId": aid}} if isinstance(alarm, dict) else {**it, "alarmId": aid}
            derived = True
        if not isinstance(it, dict) or aid in (None, "") or str(aid) in seen:
            skipped += 1                      # not an object, no alarmId (cannot be opened/decided) or duplicate
            continue
        try:
            a = normalizer.normalize(it, names, labels, config.HALL_FIELD)
        except Exception:                     # malformed fields: skip this record only
            log.warning("Skipped a malformed Camview record (alarmId=%s)", aid)
            skipped += 1
            continue
        seen.add(a["alarmId"])
        a["alarmIdDerived"] = derived
        t = types.get(a["alarmType"])
        a["alarmTypeSeverity"] = t["severity"] if t else None
        a["alarmTypeKnown"] = t is not None
        p = prios.get(a["priorityLevel"])
        a["priorityRank"] = p["rank"] if p else 99
        a["priorityConfirmed"] = bool(p and p["confirmed"])
        # Camview sends priority 1 on every record. Until an administrator confirms that mapping, the priority
        # shown is the alert TYPE's severity from the Dictionary (mobile phone = critical, lab activity = medium…);
        # a type without a severity is "medium". That severity is a fact an administrator set, so it counts as confirmed.
        if not a["priorityConfirmed"]:
            sev = (t or {}).get("severity") if t else None
            a["priority"] = sev if sev in SEVERITY_RANK else "medium"
            a["priorityRank"] = SEVERITY_RANK[a["priority"]]
            a["prioritySource"] = "type" if sev in SEVERITY_RANK else "default"
            a["priorityConfirmed"] = sev in SEVERITY_RANK
        else:
            a["prioritySource"] = "camview"
        out.append(a)
    if skipped:
        log.warning("Skipped %s malformed/duplicate Camview record(s) out of %s", skipped, len(raw_items or []))
    _normalize.skipped = skipped
    return out


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _fetch_live(feed):
    """Every page of the project (up to CAMVIEW_WINDOW_PAGES), pages 2..n fetched in parallel.
    Records are de-duplicated in _normalize (pages can shift while being read)."""
    from concurrent.futures import ThreadPoolExecutor
    first, data = list_page({"projectId": feed.project_id, "page": 1, "size": 100})
    total = data.get("totalElements")
    collected = list(first)
    if not data.get("totalPages"):                    # no page count: walk pages until hasNext is false
        page, truncated = 1, False
        while data.get("hasNext") and first:
            page += 1
            if page > config.WINDOW_PAGES:
                truncated = True
                break
            first, data = list_page({"projectId": feed.project_id, "page": page, "size": 100})
            collected.extend(first)
        return _normalize(collected), total, truncated
    pages = int(data["totalPages"])
    wanted = min(pages, config.WINDOW_PAGES)
    truncated = pages > config.WINDOW_PAGES
    if wanted > 1:
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix=f"camview-{feed.project_id}") as pool:
            for items, _ in pool.map(lambda p: list_page({"projectId": feed.project_id, "page": p, "size": 100}),
                                     range(2, wanted + 1)):
                collected.extend(items)
    return _normalize(collected), total, truncated


def _fetch_demo(feed):
    now = time.time()
    if not feed.items:
        project = next((p for p in mock_data.PROJECTS if str(p["projectId"]) == feed.project_id), None)
        if project is None:
            return [], 0, False
        items = _normalize(mock_data.generate_project_alarms(project))
        feed.demo_tick = now
        return items, len(items), False
    if now - feed.demo_tick >= DEMO_NEW_ALARM_EVERY:
        arrived = min(2, int((now - feed.demo_tick) // DEMO_NEW_ALARM_EVERY))
        fresh = _normalize([mock_data.generate_live_alarm(feed.project_id) for _ in range(arrived)])
        feed.demo_tick = now
        items = fresh + feed.items
        return items, len(items), False
    return feed.items, len(feed.items), False


_share_running = set()
_share_lock = threading.Lock()
SHARE_INLINE = os.environ.get("CAMVIEW_SHARE_INLINE") == "1"  # unit tests run it synchronously


def auto_share(project_id, items):
    """Delivers Camview-VALID alerts to their clients (tickets.auto_sync) in a background
    thread — a first run can mean hundreds of deliveries and must not stall the dashboard.
    One run per project at a time; the next refresh picks up anything left."""
    def run():
        try:
            with db.batch("auto-share"):          # hundreds of deliveries = one data change for the screens
                d, w = tickets_mod().auto_sync(items)
            if d or w:
                log.info("Auto-share project %s: %s delivered, %s withdrawn", project_id, d, w)
        except Exception:
            log.exception("auto-share failed for project %s", project_id)
        finally:
            with _share_lock:
                _share_running.discard(str(project_id))

    with _share_lock:
        if str(project_id) in _share_running:
            return
        _share_running.add(str(project_id))
    if SHARE_INLINE:
        run()
    else:
        threading.Thread(target=run, name=f"auto-share-{project_id}", daemon=True).start()


def tickets_mod():
    import tickets
    return tickets


def _refresh_in_background(project_id):
    feed = _feed(project_id)
    if feed.lock.locked():
        return                                          # already refreshing
    threading.Thread(target=refresh, args=(project_id, True), name=f"refresh-{project_id}", daemon=True).start()


def all_project_ids():
    """Every project the system monitors: the configured default (CAMVIEW_PROJECT_ID), the extra ids
    from Settings › Monitored projects, and projects from imported master data. A project whose
    nodes were built automatically from Camview data does not keep itself monitored: change the
    configured project and the old one disappears from every screen."""
    ids = []
    if config.DEFAULT_PROJECT_ID:
        ids.append(config.DEFAULT_PROJECT_ID)
    for extra in db.get_setting("extra_projects", []) or []:
        if str(extra) not in ids:
            ids.append(str(extra))
    for p in nomenclature.projects(include_auto=False):
        if p["externalId"] not in ids:
            ids.append(p["externalId"])
    return [p for p in ids if p not in config.EXCLUDED_PROJECTS]


_codes_synced = {"at": 0.0}


def sync_project_codes(force=False):
    """Gives every monitored project its code: CAMVIEW_PROJECT_CODES first, then Camview's own project
    record when the key may read it. Runs at start-up and once an hour."""
    if config.MODE != "live" or (not force and time.time() - _codes_synced["at"] < 3600):
        return 0
    _codes_synced["at"] = time.time()
    from camview_client import project_info
    n = nomenclature.apply_project_codes(config.PROJECT_CODES)
    known = {p["externalId"]: p for p in nomenclature.projects()}
    for pid in all_project_ids():
        if pid in config.PROJECT_CODES:
            continue
        cur = known.get(pid)
        if cur and cur["code"] != pid:
            continue                                   # a code is already set (Settings or an earlier sync)
        info = project_info(pid)
        if info and info.get("code"):
            nomenclature.set_project_code(pid, info["code"], info.get("name"))
            n += 1
    return n


def prune_stale_projects():
    """Live mode: a project whose tree was built automatically from Camview data and that is no longer
    monitored (the configured project changed) is removed with its centres, rooms and cameras, so an
    old exam never lingers next to the running one. Reviews, tickets and the audit trail are kept."""
    if config.MODE != "live":
        return 0
    monitored = set(all_project_ids())
    removed = 0
    extras = [str(x) for x in (db.get_setting("extra_projects", []) or [])]
    if any(x in config.EXCLUDED_PROJECTS for x in extras):                  # excluded ids leave the extra list too
        db.set_setting("extra_projects", [x for x in extras if x not in config.EXCLUDED_PROJECTS])
    for p in nomenclature.projects():
        if p.get("source") == "camview" and p["externalId"] not in monitored:
            n = nomenclature.delete_auto_project(p["externalId"]) or 0
            db.audit("nomenclature.auto_project_pruned", None, "nomenclature", p["externalId"], None,
                     {"nodes": n, "reason": "no longer monitored"})
            log.info("Removed old auto-built project %s (%s nodes) — no longer monitored", p["externalId"], n)
            removed += 1
    return removed


_warmer = {"thread": None}


def start_warmer():
    """Live mode: keep every project fresh on schedule even when nobody is looking,
    so new alerts (and automatic client delivery) never wait for a page view."""
    if _warmer["thread"] or config.MODE != "live":
        return

    def loop():
        while True:
            try:
                prune_stale_projects()
                sync_project_codes()
                for pid in all_project_ids():
                    if time.time() - _feed(pid).fetched_at >= config.CACHE_SECONDS:
                        refresh(pid, force=True)
            except Exception:
                log.exception("background refresh loop")
            time.sleep(max(15, config.CACHE_SECONDS // 5))

    _warmer["thread"] = threading.Thread(target=loop, name="camview-warmer", daemon=True)
    _warmer["thread"].start()
    log.info("Live refresh running in the background every %ss", config.CACHE_SECONDS)


def refresh(project_id, force=False):
    """Returns the Feed, refreshing it if the cache expired. Never raises for
    upstream failures: the last good data stays and `last_error` is set."""
    feed = _feed(project_id)
    ttl = config.CACHE_SECONDS if config.MODE == "live" else 2
    if not force and time.time() - feed.fetched_at < ttl and (feed.items or feed.last_error):
        return feed
    if not force and config.MODE == "live" and feed.items and not SHARE_INLINE:
        _refresh_in_background(project_id)           # show current data now; newer data follows
        return feed
    if not feed.lock.acquire(blocking=False):
        # another request is already fetching this project: dedupe by waiting for it
        with feed.lock:
            return feed
    try:
        if not force and time.time() - feed.fetched_at < ttl and feed.items:
            return feed
        try:
            items, total, truncated = _fetch_live(feed) if config.MODE == "live" else _fetch_demo(feed)
            if config.MODE == "live":
                items = _merge_missing(feed, items)
            _detect_new(feed, items)
            had_error = feed.last_error is not None
            feed.items, feed.total_elements, feed.truncated = items, total, truncated
            feed.latest_alert_at = max((a.get("lastInstance") or "" for a in items if a.get("eventKind") != "camera_status"),
                                       default="") or None
            feed.latest_event_at = max((a.get("lastInstance") or "" for a in items if a.get("eventKind") == "camera_status"),
                                       default="") or None
            feed.last_success_at = db.now_iso()
            feed.last_error = None
            sig = _signature(items)
            if sig != feed.signature or had_error:
                # different alarm data (or the feed is back after a failure): every open screen re-reads now
                feed.signature, feed.changed_at = sig, feed.last_success_at
                changes.bump("feed")
            if config.MODE == "live" and workflow.policy().get("autoNomenclature", True):
                try:                          # locations from Camview camera data (imported master data wins)
                    added = nomenclature.sync_from_alarms(project_id, items)
                    if added:
                        log.info("Nomenclature: %s cameras added from Camview for project %s", added, project_id)
                except Exception:
                    log.exception("nomenclature sync failed for project %s", project_id)
            if config.MODE == "live":
                try:                          # camera connection as Camview reports it (camera.frameSyncStatus)
                    import health as health_mod
                    with db.quiet():          # heartbeat timestamps alone are not a change worth a re-read…
                        saved, events = health_mod.sync_from_camview(project_id, items)
                    if events:                # …a camera going offline / online is
                        changes.bump("camera-health")
                        _notify_camera_events(project_id, events, items)
                except Exception:
                    log.exception("camera health sync failed for project %s", project_id)
            if config.MODE == "live":
                try:                          # map positions for new cities are looked up in the background, once
                    import geo
                    geo.request({(a.get("cameraCity"), a.get("cameraState")) for a in items if a.get("cameraCity")})
                except Exception:
                    log.exception("map position lookup failed for project %s", project_id)
            auto_share(project_id, items)     # Camview VALID alerts → client (background; never blocks a request)
        except ApiError as e:
            if feed.last_error is None:       # a feed that just stopped answering is shown as DELAYED at once
                changes.bump("feed-error")
            feed.last_error = {"code": e.code, "message": e.message, "at": db.now_iso()}
            log.warning("Refresh failed for project %s: %s", project_id, e.message)
            import notify
            hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
            notify.to_permission("settings.manage", "system", "Camview data refresh failed",
                                 e.message, "#/settings?tab=system", project_id=project_id,
                                 dedupe=f"refresh_fail:{project_id}:{hour}")
        feed.fetched_at = time.time()
        feed.last_attempt_at = db.now_iso()
        return feed
    finally:
        feed.lock.release()


def _merge_missing(feed, items):
    """Camview's pages are not sorted and shift while a busy project is read page by page, so one read can
    return a record twice and skip another. Camview never deletes alarms, so a record that was in the
    window and is missing from this read is kept (with its last data) for MISSING_GRACE refreshes;
    an alert being reviewed never becomes "not available" between two refreshes."""
    if not feed.items:
        return items
    ids = {a["alarmId"] for a in items}
    kept = []
    for a in feed.items:
        if a["alarmId"] in ids:
            feed.missing.pop(a["alarmId"], None)
            continue
        n = feed.missing.get(a["alarmId"], 0) + 1
        if n <= MISSING_GRACE:
            feed.missing[a["alarmId"]] = n
            kept.append(a)
        else:
            feed.missing.pop(a["alarmId"], None)
    if kept:
        log.info("Kept %s record(s) skipped by Camview's shifting pages for project %s", len(kept), feed.project_id)
    return items + kept


def _notify_camera_events(project_id, events, items):
    """A camera that Camview now reports OFFLINE (transition observed between two refreshes) →
    one in-app notification per camera per day to everyone who reviews alerts for the project."""
    import notify
    by_cam = {}
    for a in items:
        cid = str(a.get("cameraId") or "")
        if cid and (cid not in by_cam or (a.get("lastInstance") or "") > (by_cam[cid].get("lastInstance") or "")):
            by_cam[cid] = a
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    for e in events:
        if e.get("event") != "CAMERA_DISCONNECTED":
            continue
        cid = str(e["cameraId"])
        a = by_cam.get(cid) or {}
        ctx = nomenclature.resolve(project_id, cid)
        label = location_label(ctx, project_id, cid, a.get("centreCode"), a.get("cameraNumber"), a.get("cameraSubLocation"))
        centre = a.get("centreName") or (ctx.get("centre") or {}).get("name") or ""
        notify.to_permission("alarm.view", "operational", f"Camera offline: {label}",
                             (f"{centre} — " if centre else "") + "Camview reports the camera is no longer sending frames.",
                             f"#/cameras/{cid}?projectId={project_id}", project_id=project_id,
                             dedupe=f"cam_off:{project_id}:{cid}:{day}", severity="high")


def _detect_new(feed, items):
    ids = {a["alarmId"] for a in items}
    now = time.time()
    if feed.seen is None:
        feed.seen = ids                       # first load is the baseline — nothing is "new"
        return
    fresh = [a for a in items if a["alarmId"] not in feed.seen]
    feed.seen |= ids
    for a in fresh:
        feed.new_ids[a["alarmId"]] = now
    # forget "new" markers after 10 minutes
    feed.new_ids = {k: v for k, v in feed.new_ids.items() if now - v < 600}
    if fresh:
        import notify
        try:
            notify.notify_watchers(enrich(fresh))
        except Exception:
            log.exception("watchlist notification failed")
    # "Critical" comes from the priority dictionary. Camview sends priority 1 on every record of a project, so
    # until an administrator confirms the mapping (Nomenclature › Dictionary) nothing is announced as critical,
    # and camera status events are never announced as alarms.
    crit = [a for a in fresh if a.get("priority") == "critical" and a.get("priorityConfirmed")
            and a.get("eventKind") != "camera_status"]
    if crit:
        import notify
        enriched = enrich(crit)
        for a in enriched:
            cam = (a.get("context") or {}).get("camera", {}).get("code", f"camera {a.get('cameraId')}")
            notify.to_permission("alarm.view", "operational", f"Critical alarm: {a['alarmTypeName']}",
                                 f"{a['alarmId']} at {cam} — pending review.", f"#/alerts/{a['alarmId']}?projectId={a.get('projectId')}",
                                 alarm=a, dedupe=f"crit:{a['alarmId']}", severity="critical")


def freshness(feed):
    state = "live"
    if feed.last_error and not feed.items:
        state = "disconnected"
    elif feed.last_error:
        state = "delayed"
    elif feed.last_success_at:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(feed.last_success_at.replace("Z", "+00:00"))) \
            .total_seconds()
        if age > max(60, config.CACHE_SECONDS * 6):
            state = "delayed"
    else:
        state = "connecting"
    # "live" means the connection to Camview works; whether Camview is still PRODUCING alerts is a separate fact
    quiet_h = _age_minutes(feed.latest_alert_at)
    return {"state": state, "lastSuccessAt": feed.last_success_at, "lastError": feed.last_error,
            "lastAttemptAt": getattr(feed, "last_attempt_at", None), "changedAt": feed.changed_at,
            "mode": config.MODE, "windowSize": len(feed.items), "totalElements": feed.total_elements,
            "truncated": feed.truncated, "cacheSeconds": config.CACHE_SECONDS,
            "latestAlertAt": feed.latest_alert_at, "latestEventAt": feed.latest_event_at,
            "quietHours": None if quiet_h is None else round(quiet_h / 60, 1)}


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def _age_minutes(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 60
    except (AttributeError, ValueError, TypeError):
        return None


def discover_projects(start=1, end=2000, workers=12):
    """Which project ids the configured Camview key can read, with how many alarms each has and the
    newest alarm seen. Camview has no "list projects" call and pages are not time-ordered, so small
    projects (up to 5 pages) are read completely; larger ones report the newest alarm on a sample of
    pages (first, middle, last) and say so. Only in live mode."""
    from concurrent.futures import ThreadPoolExecutor
    if config.MODE != "live":
        raise ApiError("live_mode_not_configured", "Project discovery reads the live Camview feed (demo mode is on).", 503)
    start, end = max(1, int(start)), min(int(end), int(start) + 1999)   # ids grow over time: newest project = highest id
    known = set(all_project_ids())

    def newest(items):
        alerts = [i.get("alarm") or {} for i in items]
        det = [a for a in alerts if a.get("alarmType") != 10]
        return (max((a.get("lastInstance") or "" for a in det), default="") or None,
                max((a.get("lastInstance") or "" for a in alerts), default="") or None)

    def probe(pid):
        try:
            first, data = list_page({"projectId": pid, "page": 1, "size": 100})
        except ApiError as e:
            return {"projectId": str(pid), "error": e.code} if e.code not in ("upstream_error",) else None
        total = data.get("totalElements") or 0
        if not first and not total:
            return None
        pages = int(data.get("totalPages") or 1)
        sample = [1] if pages <= 5 else sorted({1, max(1, pages // 2), pages})
        extra = [p for p in (range(2, pages + 1) if pages <= 5 else sample[1:])]
        items = list(first)
        for p in extra:
            try:
                items.extend(list_page({"projectId": pid, "page": p, "size": 100})[0])
            except ApiError:
                pass
        latest_alert, latest_any = newest(items)
        return {"projectId": str(pid), "total": total, "pages": pages, "complete": pages <= 5,
                "cameras": len({(i.get("camera") or {}).get("id") for i in items} - {None}),
                "latestAlertAt": latest_alert, "latestAt": latest_any, "monitored": str(pid) in known,
                "quietHours": None if not latest_alert else round((_age_minutes(latest_alert) or 0) / 60, 1)}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="camview-discover") as pool:
        found = [r for r in pool.map(probe, range(start, end + 1)) if r]
    found = [r for r in found if r["projectId"] not in config.EXCLUDED_PROJECTS]
    found.sort(key=lambda r: (r.get("latestAlertAt") or r.get("latestAt") or ""), reverse=True)
    return {"from": start, "to": end, "projects": found, "scannedAt": db.now_iso()}


COMPLETENESS_LEVELS = ["project", "tc", "centre", "building", "floor", "room", "camera"]


def completeness(ctx, project_levels=None):
    """Context completeness = share of the project's own hierarchy levels resolved for this camera
    (a data-quality measure, NOT a confidence score). A tree built from Camview data has project,
    centre, room and camera, so a camera with all four is 100 % — no floor is expected."""
    if project_levels is None:
        project_levels = nomenclature.project_levels((ctx.get("project") or {}).get("externalId"))
    expected = [lvl for lvl in COMPLETENESS_LEVELS if lvl in ("project", "camera") or lvl in project_levels]
    if len(expected) <= 2:                                # nothing known about the project yet
        expected = ["project", "centre", "room", "camera"]
    available = [lvl for lvl in expected if ctx.get(lvl) and not ctx[lvl].get("unmapped")]
    missing = [lvl for lvl in expected if lvl not in available]
    return {"percent": round(100 * len(available) / len(expected)), "available": available,
            "missing": missing, "expected": expected}


def location_label(ctx, project_id, camera_id, centre_code=None, camera_number=None, sub_location=None):
    """Location naming, in the words the data arrives with:
      * master data (Nomenclature import):  PROJECT CODE - TC CODE - CAMERA CODE
      * Camview only (no master data):      CENTRE CODE - SUB-LOCATION - CAMERA NUMBER
                                            (camera.centerCode / subLocation / cameraNumber, exactly as sent)
      * nothing known:                      PROJECT-<id> - TC not mapped - CAM-<id>"""
    cam_node = ctx.get("camera") or {}
    cam_code = cam_node.get("code") if not cam_node.get("unmapped") else None
    unknown = f"CAM-{camera_id}" if camera_id not in (None, "") else "camera unknown"
    tc = (ctx.get("tc") or {}).get("code")
    if tc:
        project = (ctx.get("project") or {}).get("code") or str(project_id)
        return f"{project} - {tc} - {cam_code or camera_number or unknown}"
    centre = ctx.get("centre") or {}
    centre_code = centre_code or (centre.get("code") if centre and not centre.get("unmapped") else None)
    sub_location = sub_location or (ctx.get("room") or {}).get("code")
    cam = camera_number or cam_code or unknown
    if centre_code:
        return " - ".join(p for p in (centre_code, sub_location, cam) if p)
    project = (ctx.get("project") or {}).get("code") or str(project_id)
    return f"{project} - TC not mapped - {cam}"


def enrich(items, new_ids=None, pol=None):
    """Adds context, review, workflow, visibility, assignment and flags. Returns copies."""
    import exams as exams_mod
    import tickets as tickets_mod
    pol = pol or workflow.policy()
    ids = [a["alarmId"] for a in items]
    reviews = db.reviews_for(ids)
    pubs = workflow.publications_for(ids)
    assigns = workflow.assignments_for(ids)
    tix = tickets_mod.for_alarms(ids)
    import health as health_mod
    healths = health_mod.lookup((a.get("projectId"), a.get("cameraId")) for a in items)
    client_cache = {}
    out = []
    for base in items:
        a = dict(base)
        # ---- automatic exam + client resolution (from Administration › Exams mapping)
        exam = exams_mod.resolve(a.get("projectId"), a.get("firstInstance"))
        a["exam"] = {"id": exam["id"], "code": exam["code"], "name": exam["name"]} if exam else None
        ck = (str(a.get("projectId")), exam["id"] if exam else None)
        if ck not in client_cache:
            client_cache[ck] = [{"id": c["id"], "name": c["name"]} for c in exams_mod.clients_for(a.get("projectId"), exam)]
        a["clients"] = client_cache[ck]
        a["client"] = a["clients"][0] if len(a["clients"]) == 1 else None
        t = tix.get(a["alarmId"])
        a["ticket"] = {"id": t["id"], "ref": t["ref"], "status": t["status"], "deliveryStatus": t["deliveryStatus"],
                       "clientId": t["clientId"]} if t else None
        a["context"] = nomenclature.resolve(a.get("projectId"), a.get("cameraId"))
        cam = a["context"].get("camera") or {}
        if cam.get("name") and not cam.get("unmapped"):
            a["cameraName"] = cam["name"]
        # camera code: master data → Camview's own cameraNumber → CAM-<Camview id> (never invented beyond that)
        a["cameraCode"] = (cam.get("code") if not cam.get("unmapped") else None) or a.get("cameraNumber") \
            or f"CAM-{a.get('cameraId')}"
        a["locationLabel"] = location_label(a["context"], a.get("projectId"), a.get("cameraId"), a.get("centreCode"),
                                            a.get("cameraNumber"), a.get("cameraSubLocation"))
        ctx = a["context"]
        a["contextCompleteness"] = completeness(ctx, nomenclature.project_levels(a.get("projectId")))
        if ctx.get("mapped") and ctx.get("centre"):
            a["hall"] = ctx["centre"]["code"] + (f" · Room {ctx['room']['code']}" if ctx.get("room") else "")
        else:
            a["hall"] = "Unmapped"
        r = reviews.get(a["alarmId"])
        a["review"] = {"status": r["status"], "by": r["updatedBy"], "at": r["updatedAt"],
                       "validatedById": r["validatedById"]} if r else {"status": "unreviewed"}
        a["opsReviewStatus"] = a["review"]["status"]
        a["publications"] = pubs.get(a["alarmId"], [])
        a["visibility"] = workflow.visibility_summary(a["publications"])
        a["assignment"] = assigns.get(a["alarmId"])
        a["workflowState"] = workflow.derive_workflow(a["review"]["status"], a["publications"], a["assignment"])
        a["workflowLabel"] = workflow.WORKFLOW_LABELS[a["workflowState"]]
        images = a.get("imageUrls") or []
        a["evidence"] = {"images": len(images), "video": bool(a.get("videoUrl")),
                         "count": len(images) + (1 if a.get("videoUrl") else 0)}
        # camera / recording health: a separate, REAL source (health.py) — never inferred from the alarm
        a["health"] = healths.get((str(a.get("projectId")), str(a.get("cameraId")))) or health_mod.unavailable()
        # what the alarm itself reported (an event at a point in time, not current health)
        md = a.get("alarmMetadata") if isinstance(a.get("alarmMetadata"), dict) else {}
        a["alarmEvent"] = {"status": md.get("status"), "reason": md.get("reason") or md.get("message"),
                           "at": md.get("timestamp")} if md else None
        age = _age_minutes(a.get("firstInstance") or a.get("lastInstance"))
        span = None
        if a.get("firstInstance") and a.get("lastInstance"):
            span = max(0, (_age_minutes(a["firstInstance"]) or 0) - (_age_minutes(a["lastInstance"]) or 0))
        a["ageMinutes"] = None if age is None else round(age, 1)
        a["spanMinutes"] = None if span is None else round(span, 1)
        pending = a.get("lastActionType") == 0 and a["review"]["status"] in ("unreviewed", "acknowledged")
        # one decision state for the review queue: the operator's decision wins over Camview's
        rs = a["review"]["status"]
        a["decision"] = {"marked_valid": "valid", "marked_invalid": "invalid", "marked_exception": "exception"}.get(rs) \
            or ("pending" if pending else {1: "valid", 2: "invalid", 3: "exception"}.get(a.get("lastActionType"), "pending"))
        a["decisionSource"] = "operator" if rs in ("marked_valid", "marked_invalid", "marked_exception") else "camview"
        a["flags"] = {
            "pending": pending,
            # Camview sends priority 1 on every record: "critical" is only a fact once the mapping is confirmed
            "criticalPending": pending and a.get("priority") == "critical" and bool(a.get("priorityConfirmed")),
            "repeated": (a.get("totalTimesReported") or 1) >= pol["repeatThreshold"]
                        and (span is None or span <= pol["repeatWindowMinutes"]),
            "evidence": a["evidence"]["count"] > 0,
            "suppressed": bool(a.get("suppressed")),
            "new": bool(new_ids and a["alarmId"] in new_ids),
            "longPending": bool(pending and pol.get("longPendingMinutes") and age is not None
                                and age >= float(pol["longPendingMinutes"])),
        }
        sla_t, sla_w = pol.get("slaTargetMinutes"), pol.get("slaWarnMinutes")
        if pending and sla_t and age is not None:
            a["sla"] = "attention" if age >= float(sla_t) else \
                "approaching" if sla_w and age >= float(sla_w) else "within"
        else:
            a["sla"] = None
        out.append(a)
    return out


def enriched(feed):
    """The feed's enriched working set, computed once per (Camview refresh, data version) and shared by
    every request and every user until something changes. Enrichment joins reviews, tickets, publications,
    assignments, nomenclature, exams and health for every alarm in the window — far too much to redo for
    each of the several requests a screen makes every few seconds. Returns shallow copies, so a caller
    that annotates a record never leaks into another request."""
    def cached():
        with _enriched_lock:
            hit = _enriched.get(feed.project_id)
        if hit and hit[0] == key and time.time() - hit[2] < ENRICHED_MAX_AGE:
            return hit[1]
        return None

    # keyed on the alarm data itself (signature), not on when Camview was last read: a re-read that brought the
    # same records keeps the cache; time-dependent fields (age, SLA, NEW) are refreshed by ENRICHED_MAX_AGE
    key = (feed.signature, changes.version(), id(feed.items), len(feed.items), tuple(sorted(feed.new_ids)))
    out = cached()
    if out is None:
        with feed.enrich_lock:                    # concurrent requests wait for one computation instead of repeating it
            out = cached()
            if out is None:
                out = enrich(feed.items, feed.new_ids)
                with _enriched_lock:
                    _enriched[feed.project_id] = (key, out, time.time())
    return [dict(a) for a in out]


def working_set(user, project_id, force=False):
    """Enriched alarms for one project, filtered to the user's scope."""
    import rbac
    if not rbac.project_allowed(user, project_id):
        raise ApiError("not_found", "Project unavailable.", 404)
    feed = refresh(project_id, force=force)
    return [a for a in enriched(feed) if rbac.alarm_in_scope(user, a)], feed


def find_alarm(user, alarm_id, project_id=None):
    """Looks an alarm up in the working sets the user may see; falls back to
    the snapshot stored with a review/publication/assignment (Camview has no
    get-by-id endpoint). Returns None when unavailable OR out of scope —
    callers answer both the same way so hidden records aren't revealed."""
    import rbac
    candidates = [str(project_id)] if project_id else [p for p in allowed_projects(user)]
    for pid in candidates:
        if not rbac.project_allowed(user, pid):
            continue
        feed = refresh(pid)
        for base in feed.items:
            if base["alarmId"] == alarm_id:
                a = enrich([base], feed.new_ids)[0]
                a["source"] = "live" if config.MODE == "live" else "demo"
                return a if rbac.alarm_in_scope(user, a) else None
    snap = None
    for sql in ("SELECT snapshot FROM publications WHERE alarm_id=? AND snapshot IS NOT NULL",
                "SELECT snapshot FROM assignments WHERE alarm_id=? AND snapshot IS NOT NULL",
                "SELECT snapshot FROM ops_review WHERE alarm_id=? AND snapshot IS NOT NULL"):
        r = db.one(sql, (alarm_id,))
        if r:
            snap = db.jload(r["snapshot"], None)
            if snap and snap.get("projectId") is not None:
                break
    if not snap or snap.get("projectId") is None or not rbac.project_allowed(user, snap.get("projectId")):
        return None
    base = {k: snap.get(k) for k in snap if k != "context"}
    base.setdefault("alarmId", alarm_id)
    base.setdefault("imageUrls", [])
    a = enrich([base])[0]
    a["source"] = "snapshot"
    return a if rbac.alarm_in_scope(user, a) else None


def live_alarms(project_id, alarm_ids):
    """The current Camview records for these alarm ids (from the live working set, never
    blocking on Camview). Media links are re-signed by Camview on every read, so these
    are always playable — unlike the links frozen in a ticket snapshot. Returns (by_id, feed)."""
    feed = refresh(project_id)
    want = set(alarm_ids)
    return {a["alarmId"]: a for a in feed.items if a["alarmId"] in want}, feed


_range_cache = {}


def fetch_range(user, project_id, start, end):
    """Alarms raised in [start, end] for analytics/reports (scope-filtered).
    Live: up to CAMVIEW_KPI_MAX_PAGES x 100 newest alarms, or Camview's
    documented history query when CAMVIEW_KPI_USE_HISTORY=1."""
    import analytics
    import rbac
    if not rbac.project_allowed(user, project_id):
        raise ApiError("not_found", "Project unavailable.", 404)
    truncated = False
    if config.MODE == "demo":
        items = refresh(project_id).items
    else:
        key = (str(project_id), start.strftime("%Y%m%d%H"), end.strftime("%Y%m%d%H"))
        cached = _range_cache.get(key)
        if cached and time.time() - cached[0] < 60:
            items, truncated = cached[1]
        else:
            collected, last_key = [], None
            for page in range(1, config.KPI_MAX_PAGES + 1):
                body = {"projectId": project_id, "page": page, "size": 100}
                if config.KPI_USE_HISTORY:
                    body.update(startTime=int(start.timestamp() * 1000), endTime=int(end.timestamp() * 1000),
                                useHistory=True)
                    if last_key:
                        body["lastKey"] = last_key
                raw, data = list_page(body)
                collected.extend(raw)
                last_key = data.get("lastKey")
                if not data.get("hasNext") or not raw:
                    break
            else:
                truncated = True
            items = _normalize(collected)
            _range_cache[key] = (time.time(), (items, truncated))
    enriched = [a for a in enrich(analytics.in_range(items, start, end)) if rbac.alarm_in_scope(user, a)]
    return enriched, truncated


def allowed_projects(user):
    """Project ids the user may select (the monitored projects, see all_project_ids, within scope)."""
    import rbac
    ids = [p["externalId"] for p in nomenclature.projects(include_auto=False)]
    if config.MODE == "live" and config.DEFAULT_PROJECT_ID and config.DEFAULT_PROJECT_ID not in ids:
        ids.append(config.DEFAULT_PROJECT_ID)
    for extra in db.get_setting("extra_projects", []) or []:
        if str(extra) not in ids:
            ids.append(str(extra))
    return [p for p in ids if rbac.project_allowed(user, p) and p not in config.EXCLUDED_PROJECTS]
