"""
routes_hms.py — HMS board: alerts per classification, in real time.

  GET /api/hms?code=<project code>&projectId=<id>&tzOffset=<minutes>

For every alert type of the CamviewAI feature list (and any unknown code that arrived): how many alerts are
LIVE now (latest occurrence within CAMVIEW_LIVE_MINUTES, by the alert's own time), how many happened today and
in the loaded window, how many still wait for a decision, and when the latest one happened. Plus the camera
health Camview reports with the alerts (online / offline / frame sync failed).

The screen re-reads this on every data change (push updates), so the counts follow Camview in real time.
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import alarms
import config
import rbac

bp = Blueprint("hms", __name__)


def _local_day(iso, offset):
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t + timedelta(minutes=offset)).date()


def _by_code(items, offset, today):
    """One row per registered project code (and "Other projects" for monitored ones without a code): alerts per
    classification, plus live / today / total — the project x alert-type table of the HMS board."""
    import project_codes
    owner = {}
    for e in project_codes.entries():
        if e.get("archived"):
            continue
        for p in e.get("projectIds", []):
            owner.setdefault(str(p), e["code"])
    rows = {}
    for a in items:
        if a.get("eventKind") == "camera_status":
            continue
        code = owner.get(str(a.get("projectId")), "")
        r = rows.setdefault(code, {"code": code or None, "label": code or "Other projects", "types": {},
                                   "live": 0, "today": 0, "total": 0, "pending": 0})
        tid = str(a.get("alarmType"))
        r["types"][tid] = r["types"].get(tid, 0) + 1
        r["total"] += 1
        r["live"] += bool(a.get("live"))
        r["today"] += _local_day(a.get("lastInstance") or a.get("firstInstance"), offset) == today
        r["pending"] += a.get("decision") == "pending"
    return sorted(rows.values(), key=lambda r: (-r["live"], -r["today"], -r["total"]))


@bp.route("/api/hms")
@rbac.internal("live.view", "alarm.view", any_of=True)
def hms():
    from routes_queue import _all_alarms
    user = rbac.current_user()
    try:
        offset = int(request.args.get("tzOffset") or 330)
    except ValueError:
        offset = 330
    items, freshness = _all_alarms(user, request.args.get("projectId"), request.args.get("code"))
    today = (datetime.now(timezone.utc) + timedelta(minutes=offset)).date()

    types = {tid: {"type": tid, "short": short, "name": name, "known": True,
                   "live": 0, "today": 0, "total": 0, "pending": 0, "latestAt": None}
             for tid, (short, name, _c) in alarms.FEATURES.items()}
    cams = {}
    for a in items:
        if a.get("eventKind") == "camera_status":
            continue
        tid = a.get("alarmType")
        t = types.get(tid)
        if t is None:                         # a code outside the feature list still shows (and is counted)
            t = types[tid] = {"type": tid, "short": "?", "name": a.get("alarmTypeName") or alarms.unknown_type_name(tid),
                              "known": False, "live": 0, "today": 0, "total": 0, "pending": 0, "latestAt": None}
        t["total"] += 1
        t["live"] += bool(a.get("live"))
        t["today"] += _local_day(a.get("lastInstance") or a.get("firstInstance"), offset) == today
        t["pending"] += a.get("decision") == "pending"
        last = a.get("lastInstance") or a.get("firstInstance")
        if last and (t["latestAt"] is None or last > t["latestAt"]):
            t["latestAt"] = last
        sync = str(a.get("frameSyncStatus") or "").upper()
        if a.get("cameraId") not in (None, ""):
            cams[(str(a.get("projectId")), str(a.get("cameraId")))] = sync
    rows = sorted(types.values(), key=lambda t: (-t["live"], -t["today"], -t["total"], t["type"] if isinstance(t["type"], int) else 999))
    totals = {k: sum(t[k] for t in rows) for k in ("live", "today", "total", "pending")}
    camera = {"seen": len(cams), "online": sum(1 for s in cams.values() if s in ("SYNCED", "ONLINE")),
              "offline": sum(1 for s in cams.values() if s in ("OFFLINE", "DISCONNECTED")),
              "syncFailed": sum(1 for s in cams.values() if s in ("FAILED", "SYNC_FAILED", "NOT_SYNCED"))}
    fr = list(freshness.values())
    by_code = None if request.args.get("code") or request.args.get("projectId") else _by_code(items, offset, today)
    return jsonify({"types": rows, "totals": totals, "cameras": camera, "liveMinutes": config.LIVE_MINUTES, "byCode": by_code,
                    "updatedAt": max((f.get("lastSuccessAt") or "" for f in fr), default="") or None,
                    "feedsFailing": sum(1 for f in fr if f.get("lastError")), "projects": len(fr)})
