"""
alert_store.py — the durable, canonical copy of every alert received from Camview.

The live feed (datasource.Feed) is a working set in memory: it is rebuilt on every refresh, Camview's pages
shift while they are read, and a restart empties it. An alert that was ever listed must stay openable anyway,
so every normalized record is also written here, and alert lookups fall back to it (alert_service.get):

    live feed (freshest)  →  alerts table (last record Camview sent)  →  review / ticket snapshot (legacy)

Writes are cheap: a record is rewritten only when its alarm data changed (fingerprint of the fields that
matter plus the media paths) or when its stored copy is older than REWRITE_SECONDS — the latter keeps the
signed media links in the store reasonably fresh (Camview signs them for 7 days) without rewriting
thousands of unchanged rows on every 30-second refresh. All writes are quiet: storing is not a data change
any screen has to re-read.
"""

import hashlib
import json
import logging
import threading
import time
from urllib.parse import urlsplit

import db

log = logging.getLogger("camview.store")

REWRITE_SECONDS = 6 * 3600
FINGERPRINT_FIELDS = ("alarmId", "projectId", "cameraId", "alarmType", "alarmTypeName", "lastActionType", "alarmState",
                      "priorityLevel", "priority", "firstInstance", "lastInstance", "totalTimesReported", "ticketId",
                      "suppressed", "frameSyncStatus", "eventKind", "centreCode", "cameraNumber", "cameraSubLocation")

_known = {}                 # (db path, alarm id) -> (fingerprint, written epoch)
_loaded = set()             # (db path, project id) whose fingerprints were read from the table
_lock = threading.Lock()


def reset():
    with _lock:
        _known.clear()
        _loaded.clear()
    HEALTH.update(ok=True, failing={}, lastError=None, lastFailureAt=None, lastSuccessAt=None)


def _path_only(u):
    return urlsplit(u)._replace(query="", fragment="").geturl() if isinstance(u, str) else None


def fingerprint(a):
    base = [a.get(k) for k in FINGERPRINT_FIELDS]
    base.append([_path_only(u) for u in a.get("imageUrls") or []])
    base.append(_path_only(a.get("videoUrl")))
    return hashlib.sha1(json.dumps(base, default=str, separators=(",", ":")).encode()).hexdigest()


def _load_project(pid):
    key = (db.DB_PATH, pid)
    if key in _loaded:
        return
    rows = db.rows("SELECT alarm_id, fingerprint, updated_at FROM alerts WHERE project_id=?", (pid,))
    now = time.time()
    with _lock:
        for r in rows:
            # age of the stored copy decides when the media links are rewritten; unknown age = rewrite soon
            _known[(db.DB_PATH, r["alarm_id"])] = (r["fingerprint"], now - REWRITE_SECONDS / 2)
        _loaded.add(key)


class StoreError(Exception):
    """Alerts were received but could not be persisted. The refresh must NOT be reported as fully successful."""


# persistence health, shown in freshness, /readyz and /api/admin/metrics. Per project: one project storing
# fine must never hide another project whose alerts are not being persisted.
HEALTH = {"ok": True, "failing": {}, "lastError": None, "lastFailureAt": None, "lastSuccessAt": None}


def _mark(pid, ok, error=None):
    if ok:
        HEALTH["failing"].pop(pid, None)
        HEALTH["lastSuccessAt"] = db.now_iso()
    else:
        HEALTH["failing"][pid] = {"error": str(error)[:200], "at": db.now_iso()}
        HEALTH.update(lastError=str(error)[:200], lastFailureAt=db.now_iso())
    HEALTH["ok"] = not HEALTH["failing"]


def _collisions(pid, ids):
    """Alarm ids already stored for ANOTHER project. The alert identity is (alarmId, projectId): a record from a
    different project with the same alarmId must never overwrite the stored one — it is reported instead."""
    out = {}
    for r in db._in_chunks("SELECT alarm_id, project_id FROM alerts", ids, "alarm_id"):
        if r["project_id"] != pid:
            out[r["alarm_id"]] = r["project_id"]
    return out


def save(project_id, items):
    """Upserts every changed record of one project. Returns how many rows were written. Raises StoreError when the
    records could not be persisted (the caller marks the refresh "received, not persisted")."""
    pid = str(project_id)
    try:
        _load_project(pid)
        now, iso = time.time(), db.now_iso()
        todo = []
        for a in items:
            aid = a.get("alarmId")
            if not aid:
                continue
            fp = fingerprint(a)
            with _lock:
                hit = _known.get((db.DB_PATH, aid))
            if hit and hit[0] == fp and now - hit[1] < REWRITE_SECONDS:
                continue
            todo.append((aid, fp, a))
        if not todo:
            _mark(pid, True)
            return 0
        clash = _collisions(pid, [t[0] for t in todo])
        if clash:
            import dq
            for aid in clash:
                dq.record("alarm_id_collision", pid, aid, f"alarmId already belongs to project {clash[aid]}; "
                                                          "the stored record was not overwritten")
            todo = [t for t in todo if t[0] not in clash]
        with db.quiet(), db.connect() as conn:
            conn.executemany(
                "INSERT INTO alerts (alarm_id, project_id, event_kind, last_instance, record, fingerprint, first_seen_at, "
                "last_seen_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(alarm_id) DO UPDATE SET project_id=excluded.project_id, event_kind=excluded.event_kind, "
                "last_instance=excluded.last_instance, record=excluded.record, fingerprint=excluded.fingerprint, "
                "last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at",
                [(aid, pid, a.get("eventKind"), a.get("lastInstance"), json.dumps(a, default=str, separators=(",", ":")),
                  fp, iso, iso, iso) for aid, fp, a in todo])
        with _lock:
            for aid, fp, _ in todo:
                _known[(db.DB_PATH, aid)] = (fp, now)
        _mark(pid, True)
        return len(todo)
    except Exception as e:
        log.exception("Could not store alerts of project %s", pid)
        _mark(pid, False, f"{type(e).__name__}: {e}")
        import obs
        obs.count("store.failure")
        raise StoreError(str(e)) from e


def _row(r):
    rec = db.jload(r["record"], None)
    if not isinstance(rec, dict):
        return None
    rec["_stored"] = {"firstSeenAt": r["first_seen_at"], "lastSeenAt": r["last_seen_at"], "updatedAt": r["updated_at"]}
    return rec


def get(alarm_id):
    r = db.one("SELECT * FROM alerts WHERE alarm_id=?", (str(alarm_id),))
    return _row(r) if r else None


def get_many(alarm_ids):
    out = {}
    for r in db._in_chunks("SELECT * FROM alerts", [str(i) for i in alarm_ids], "alarm_id"):
        rec = _row(r)
        if rec:
            out[r["alarm_id"]] = rec
    return out


def project_items(project_id, limit=20000):
    """The stored records of one project, newest first — the working set shown while Camview is unreachable
    (clearly marked as not live; see datasource.freshness)."""
    rows = db.rows("SELECT * FROM alerts WHERE project_id=? ORDER BY last_instance DESC LIMIT ?", (str(project_id), limit))
    return [x for x in (_row(r) for r in rows) if x]


def stats():
    r = db.one("SELECT COUNT(*) AS n, COUNT(DISTINCT project_id) AS p, MAX(updated_at) AS last FROM alerts")
    return {"alerts": r["n"], "projects": r["p"], "lastWriteAt": r["last"]}
