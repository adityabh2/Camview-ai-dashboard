"""
dq.py — data-quality events.

A malformed Camview record must never break a refresh, and must never disappear silently either. Records that
cannot be used are skipped by the refresh but recorded here, one row per distinct problem (repeats increase
`count`), with a short sample of the offending record (never a signed media link or a key):

  malformed_record    not an object / a field that cannot be normalized
  missing_alarm_id    no alarmId and not enough fields to derive one
  duplicate_record    the same alarmId twice in one read (Camview's shifting pages)

Live counters computed from the current working set complete the picture (see summary()):
  unknownAlarmTypes · missingMappings (exam / client / location) · missingEvidence (detections only)
"""

import hashlib
import json
import logging
import re

import db

log = logging.getLogger("camview.dq")

KINDS = ("malformed_record", "missing_alarm_id", "duplicate_record", "alarm_id_collision")
_SIGNED = re.compile(r"(https?://[^\s\"'?]+)\?[^\s\"']*")


def _sample(raw):
    try:
        s = json.dumps(raw, default=str)[:600]
    except (TypeError, ValueError):
        s = repr(raw)[:600]
    return _SIGNED.sub(r"\1?…", s)                   # a signed link is a credential for its file


def record(kind, project_id=None, alarm_id=None, detail=None, raw=None):
    """Stores (or counts again) one data-quality event. Never raises."""
    try:
        key = hashlib.sha1(f"{kind}|{project_id}|{alarm_id}|{detail}".encode()).hexdigest()
        now = db.now_iso()
        with db.quiet():
            db.execute("INSERT INTO dq_events (key, kind, project_id, alarm_id, detail, sample, count, first_at, last_at) "
                       "VALUES (?,?,?,?,?,?,1,?,?) ON CONFLICT(key) DO UPDATE SET count=count+1, last_at=excluded.last_at, "
                       "sample=excluded.sample",
                       (key, kind, None if project_id is None else str(project_id),
                        None if alarm_id is None else str(alarm_id), (detail or "")[:300], _sample(raw), now, now))
    except Exception:
        log.exception("could not record a data-quality event (%s)", kind)


def events(kind=None, limit=100):
    q, params = "SELECT * FROM dq_events", []
    if kind:
        q += " WHERE kind=?"
        params.append(kind)
    return db.rows(q + " ORDER BY last_at DESC LIMIT ?", params + [limit])


def stored_counts():
    out = {k: 0 for k in KINDS}
    for r in db.rows("SELECT kind, SUM(count) AS n FROM dq_events GROUP BY kind"):
        out[r["kind"]] = r["n"]
    return out


def summary(items=None):
    """Counters for the Data Quality page: stored events plus what the current working set shows."""
    stored = stored_counts()
    items = items or []
    alerts = [a for a in items if a.get("eventKind") != "camera_status"]
    return {
        "malformedRecords": stored["malformed_record"] + stored["missing_alarm_id"],
        "duplicateRecords": stored["duplicate_record"],
        "idCollisions": stored["alarm_id_collision"],
        "unknownAlarmTypes": len({a.get("alarmType") for a in items if a.get("alarmTypeKnown") is False}),
        "unknownAlarmTypeRecords": sum(1 for a in items if a.get("alarmTypeKnown") is False),
        "missingMappings": sum(1 for a in alerts if not a.get("exam") or not a.get("clients")
                               or not (a.get("context") or {}).get("mapped") and not a.get("centreCode")),
        "missingExamMapping": sum(1 for a in alerts if not a.get("exam")),
        "missingClientMapping": sum(1 for a in alerts if not a.get("clients")),
        "missingEvidence": sum(1 for a in alerts if not (a.get("imageUrls") or a.get("videoUrl"))),
        "recentEvents": [{"kind": r["kind"], "projectId": r["project_id"], "alarmId": r["alarm_id"], "detail": r["detail"],
                          "count": r["count"], "firstAt": r["first_at"], "lastAt": r["last_at"], "sample": r["sample"]}
                         for r in events(limit=25)],
    }
