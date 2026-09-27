"""
routes_ops.py — internal operations API (audience: internal only).

Command Center, Live Operations, Investigation, History, Intelligent Alerts,
My Work, Cameras, Context Explorer, Search, Analytics, Evidence, Shift
Control, Presentation and export. Every route checks permission + scope on
the server; the UI hiding things is only a convenience.
"""

import logging
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import analytics
import changes
import config
import datasource
import db
import intelligence
import kpis
import mock_data
import nomenclature
import notify
import rbac
import reports
import workflow
from camview_client import ApiError, list_page
from routes_common import body, csv_response, paginate, project_label, project_param

bp = Blueprint("ops", __name__)

LIST_KEYS = ("alarmId", "projectId", "cameraId", "cameraCode", "cameraName", "alarmType", "alarmTypeName",
             "alarmTypeSeverity", "priority", "priorityLevel", "priorityRank", "priorityConfirmed", "lastActionType",
             "lastActionLabel", "firstInstance", "lastInstance", "totalTimesReported", "ticketId", "shiftLabel",
             "suppressed", "workflowState", "workflowLabel", "evidence", "flags", "ageMinutes", "spanMinutes", "sla",
             "hall", "contextCompleteness", "locationLabel", "centreCode", "centreName", "cameraSubLocation", "health",
             "alarmEvent", "exam", "clients", "client", "ticket", "decision", "decisionSource", "eventKind",
             "cameraNumber", "deviceId", "frameSyncStatus", "alarmIdDerived", "prioritySource")


def slim(a):
    out = {k: a.get(k) for k in LIST_KEYS}
    try:                                        # the alert frame for lists and cards (evidence.view only)
        u = rbac.current_user()
        show = bool(u and "evidence.view" in u["permissions"])
    except Exception:                           # outside a request
        show = False
    out["imageUrl"] = (a.get("imageUrls") or [None])[0] if show else None
    out["hasVideo"] = bool(a.get("videoUrl")) if show else False
    out["context"] = {"mapped": (a.get("context") or {}).get("mapped"),
                      "path": (a.get("context") or {}).get("path", [])}
    out["review"] = {"status": (a.get("review") or {}).get("status"), "by": (a.get("review") or {}).get("by")}
    vis = a.get("visibility") or {}
    out["visibility"] = {"state": vis.get("state"), "label": vis.get("label"), "acknowledged": vis.get("acknowledged"),
                         "clients": vis.get("clients", [])}
    out["assignment"] = (a.get("assignment") or {}).get("name")
    return out


def _user():
    return rbac.current_user()


_tick_state = {"at": 0.0, "version": None, "lock": threading.Lock()}
TICK_SECONDS = 10           # time-based checks (escalations) run at most this often, whoever polls


def _tick(alarms_, pol):
    """Lightweight periodic work, run when clients poll /api/status — once per TICK_SECONDS for
    everyone (not once per browser tab), and at once whenever something changed (new alarm data,
    a rule or schedule saved) so nothing waits for the timer."""
    with _tick_state["lock"]:
        v = changes.version()
        if time.time() - _tick_state["at"] < TICK_SECONDS and v == _tick_state["version"]:
            return
        _tick_state["at"], _tick_state["version"] = time.time(), v
    try:
        with db.batch("tick"):                 # new notifications from all three checks = one data change
            notify.check_schedules()
            notify.check_escalations(alarms_, pol)
            notify.check_rules(alarms_, _rules())
    except Exception:  # never let background work break the status call
        logging.getLogger("camview").exception("status tick failed")
    with _tick_state["lock"]:
        _tick_state["version"] = changes.version()   # the tick's own notifications must not trigger another tick


def _tz():
    try:
        return int(request.args.get("tzOffset", "0") or 0)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# status / freshness
# ---------------------------------------------------------------------------

@bp.route("/api/status")
@rbac.internal("dashboard.view", "live.view", "alarm.view", any_of=True)
def status():
    user = _user()
    pid = project_param(user)
    items, feed = datasource.working_set(user, pid)
    pol = workflow.policy()
    _tick(items, pol)
    new_count = sum(1 for a in items if a["flags"]["new"])
    # overall feed state across every project the user may see (the top bar must not claim LIVE when one is stale)
    rank = {"live": 0, "connecting": 1, "delayed": 2, "disconnected": 3}
    per = {p: datasource.freshness(datasource.refresh(p)) for p in datasource.allowed_projects(user)}
    worst = max(per.values(), key=lambda f: rank.get(f.get("state"), 1), default=datasource.freshness(feed))
    succ = [f["lastSuccessAt"] for f in per.values() if f.get("lastSuccessAt")]
    latest = [f["latestAlertAt"] for f in per.values() if f.get("latestAlertAt")]
    overall = {**worst, "lastSuccessAt": min(succ) if succ else None, "projects": len(per),
               "failing": [p for p, f in per.items() if f.get("lastError")],
               "latestAlertAt": max(latest) if latest else None,
               "quietHours": min((f["quietHours"] for f in per.values() if f.get("quietHours") is not None), default=None)}
    return jsonify({"projectId": pid, "freshness": datasource.freshness(feed), "overall": overall, "newAlarms": new_count,
                    "newAlarmIds": [a["alarmId"] for a in items if a["flags"]["new"]][:50],
                    "unreadNotifications": notify.unread_count(user["id"]), "serverTime": db.now_iso(),
                    # changes whenever alarm data or Command Center data changed: screens re-read only then
                    "dataVersion": changes.version()})


# ---------------------------------------------------------------------------
# Command Center
# ---------------------------------------------------------------------------

def _insights(items, m):
    out = []
    if not items:
        return out
    top = analytics.top_cameras(items, 1)
    if top:
        out.append({"text": f"Most active camera: {top[0]['code']} with {top[0]['count']} alarms "
                            f"({top[0]['occurrences']} occurrences).", "provenance": "derived"})
    if m["falseAlarmRate"] is not None:
        out.append({"text": f"Camview false-alarm rate in this window: {m['falseAlarmRate'] * 100:.0f}% "
                            f"({m['invalid']} invalid of {m['valid'] + m['invalid']} resolved).", "provenance": "derived"})
    hours = Counter()
    for a in items:
        try:
            hours[datetime.fromisoformat(a["firstInstance"].replace("Z", "+00:00"))
                  .astimezone(timezone(timedelta(minutes=_tz()))).hour] += 1
        except (AttributeError, ValueError, TypeError):
            pass
    if hours:
        h, n = hours.most_common(1)[0]
        out.append({"text": f"Busiest hour of day: {h:02d}:00–{(h + 1) % 24:02d}:00 with {n} alarms.",
                    "provenance": "derived"})
    types = Counter(a.get("alarmTypeName") for a in items).most_common(1)
    if types:
        out.append({"text": f"Most frequent alarm type: {types[0][0]} ({types[0][1]}).", "provenance": "derived"})
    unmapped = sum(1 for a in items if not (a.get("context") or {}).get("mapped"))
    if unmapped:
        out.append({"text": f"{unmapped} alarms come from cameras without nomenclature mapping.",
                    "provenance": "derived"})
    return out


@bp.route("/api/overview")
@rbac.internal("dashboard.view")
def overview():
    user = _user()
    pid = project_param(user)
    items, feed = datasource.working_set(user, pid)
    pol = workflow.policy()
    m = analytics.metrics(items)
    alerts, skipped = intelligence.evaluate(items, pol, datasource.freshness(feed), _rules())
    recent = sorted(items, key=lambda a: a.get("lastInstance") or "", reverse=True)[:12]
    by_state = Counter(a["workflowState"] for a in items)
    queue = {
        "pendingValidation": sorted([a for a in items if a["flags"]["pending"]],
                                    key=lambda a: (a["priorityRank"], -(a.get("ageMinutes") or 0)))[:8],
        "needsInvestigation": [a for a in items if a["workflowState"] == "INVESTIGATING"][:8],
        "readyForApproval": [a for a in items if a["workflowState"] == "READY_FOR_APPROVAL"][:8],
        "readyForClient": [a for a in items if a["workflowState"] == "READY_FOR_CLIENT"][:8],
        "recentlyShared": sorted([a for a in items if a["workflowState"] in ("SHARED", "CLIENT_ACKNOWLEDGED")],
                                 key=lambda a: max((c.get("sharedAt") or "") for c in a["visibility"]["clients"]) or "",
                                 reverse=True)[:8],
    }
    return jsonify({
        "project": project_label(pid), "freshness": datasource.freshness(feed), "metrics": m,
        "statusDistribution": analytics.status_distribution(items),
        "priorityDistribution": analytics.priority_distribution(items),
        "shiftDistribution": analytics.shift_distribution(items),
        "hourly": analytics.hourly(items, 24, _tz()),
        "topCameras": analytics.top_cameras(items, 8),
        "recent": [slim(a) for a in recent],
        "alerts": alerts[:10], "alertCount": len(alerts),
        "alertCounts": dict(Counter(a["category"] for a in alerts)),
        "skippedIntelligence": skipped,
        "reviewQueue": {k: [slim(a) for a in v] for k, v in queue.items()},
        "reviewQueueCounts": {"pendingValidation": m["readyForReview"], "needsInvestigation": by_state["INVESTIGATING"],
                              "readyForApproval": by_state["READY_FOR_APPROVAL"],
                              "readyForClient": by_state["READY_FOR_CLIENT"],
                              "recentlyShared": by_state["SHARED"] + by_state["CLIENT_ACKNOWLEDGED"]},
        "sharingQueue": {"ready": by_state["READY_FOR_APPROVAL"] + by_state["READY_FOR_CLIENT"],
                         "approved": by_state["APPROVED"],
                         "shared": by_state["SHARED"] + by_state["CLIENT_ACKNOWLEDGED"],
                         "acknowledged": by_state["CLIENT_ACKNOWLEDGED"], "withdrawn": by_state["WITHDRAWN"]},
        "insights": _insights(items, m),
        "policy": {"fourEyes": pol["fourEyes"], "requireApproval": pol["requireApproval"]},
    })


def _rules():
    return [{**r, "conditions": db.jload(r["conditions"], [])}
            for r in db.rows("SELECT * FROM alert_rules WHERE enabled=1")]


# ---------------------------------------------------------------------------
# Live operations
# ---------------------------------------------------------------------------

SORTS = {"priority": lambda a: (a["priorityRank"], a.get("lastInstance") or ""),
         "lastInstance": lambda a: a.get("lastInstance") or "",
         "firstInstance": lambda a: a.get("firstInstance") or "",
         "totalTimesReported": lambda a: a.get("totalTimesReported") or 0,
         "alarmId": lambda a: a["alarmId"], "alarmTypeName": lambda a: a.get("alarmTypeName") or "",
         "cameraCode": lambda a: a.get("cameraCode") or "", "workflowState": lambda a: a["workflowState"],
         "ageMinutes": lambda a: a.get("ageMinutes") or 0, "lastActionType": lambda a: a.get("lastActionType") or 0,
         "shiftLabel": lambda a: a.get("shiftLabel") or "",
         "visibility": lambda a: workflow.VISIBILITY_ORDER.index(a["visibility"]["state"])
         if a["visibility"]["state"] in workflow.VISIBILITY_ORDER else 0}

QUICK = {
    "critical": lambda a, u: a.get("priority") == "critical",
    "pending": lambda a, u: a["flags"]["pending"],
    "validated": lambda a, u: a.get("lastActionType") == 1 or a["review"]["status"] == "marked_valid",
    "repeated": lambda a, u: a["flags"]["repeated"],
    "evidence": lambda a, u: a["flags"]["evidence"],
    "suppressed": lambda a, u: a["flags"]["suppressed"],
    "readyForClient": lambda a, u: a["workflowState"] == "READY_FOR_CLIENT",
    "approval": lambda a, u: a["workflowState"] == "READY_FOR_APPROVAL",
    "shared": lambda a, u: a["visibility"]["state"] == "shared",
    "new": lambda a, u: a["flags"]["new"],
    "toReview": lambda a, u: a["flags"]["pending"],
    "unmapped": lambda a, u: not ((a.get("context") or {}).get("centre") and not a["context"]["centre"].get("unmapped")),
    "valid": lambda a, u: a["review"]["status"] == "marked_valid"
                          or (a.get("lastActionType") == 1 and a["review"]["status"] in ("unreviewed", "acknowledged")),
    "invalid": lambda a, u: a["review"]["status"] == "marked_invalid"
                            or (a.get("lastActionType") == 2 and a["review"]["status"] in ("unreviewed", "acknowledged")),
    "exception": lambda a, u: a["review"]["status"] == "marked_exception"
                              or (a.get("lastActionType") == 3 and a["review"]["status"] in ("unreviewed", "acknowledged")),
    "mine": lambda a, u: (a.get("assignment") or {}).get("userId") == u["id"],
}


def filter_alarms(items, args, user):
    f = {k: args.get(k) for k in reports.FILTER_KEYS + ("from", "to")}
    items = reports.apply_filters(items, f)
    for q in [x for x in (args.get("quick") or "").split(",") if x]:
        fn = QUICK.get(q)
        if fn:
            items = [a for a in items if fn(a, user)]
    s = (args.get("search") or "").strip().lower()
    if s:
        def hit(a):
            hay = [a["alarmId"], str(a.get("ticketId") or ""), a.get("cameraCode") or "", a.get("cameraName") or "",
                   a.get("alarmTypeName") or "", str(a.get("cameraId"))]
            hay += [n["code"] for n in (a.get("context") or {}).get("path", [])]
            return any(s in str(x).lower() for x in hay)
        items = [a for a in items if hit(a)]
    key = args.get("sort") or "lastInstance"
    fn = SORTS.get(key, SORTS["lastInstance"])
    desc = (args.get("dir") or ("asc" if key == "priority" else "desc")) == "desc"
    return sorted(items, key=fn, reverse=desc)


@bp.route("/api/alarms")
@rbac.internal("live.view", "alarm.view", any_of=True)
def alarms_list():
    user = _user()
    pid = project_param(user)
    items, feed = datasource.working_set(user, pid)
    filtered = filter_alarms(items, request.args, user)
    page = paginate(filtered, request.args.get("page"), request.args.get("size"))
    page["items"] = [slim(a) for a in page["items"]]
    page["freshness"] = datasource.freshness(feed)
    page["facets"] = {
        "alarmTypes": sorted({(a.get("alarmType"), a.get("alarmTypeName")) for a in items if a.get("alarmType") is not None},
                             key=lambda x: str(x[1])),
        "shifts": sorted({a.get("shiftLabel") for a in items if a.get("shiftLabel")}),
        "tcs": sorted({a["context"]["tc"]["code"] for a in items if a["context"].get("tc")}),
        "centres": sorted({a["context"]["centre"]["code"] for a in items if a["context"].get("centre")}),
    }
    page["windowNote"] = (f"Newest {len(items)} alarms from Camview (working window)"
                          if config.MODE == "live" else f"{len(items)} demo alarms")
    return jsonify(page)


@bp.route("/api/export/alarms.csv")
@rbac.internal("alarm.export")
def export_alarms():
    user = _user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    filtered = filter_alarms(items, request.args, user)
    cols, rows = reports._alarm_rows(filtered, limit=100000)
    db.audit("alarm.export", user, "project", pid, details={"records": len(rows), "filters": dict(request.args)},
             project_id=pid)
    return csv_response(rows, cols, f"camview-alarms-{pid}-{datetime.now():%Y%m%d-%H%M}.csv")


# ---------------------------------------------------------------------------
# Investigation
# ---------------------------------------------------------------------------

_view_audit = {}


def _audit_view(user, alarm, action):
    key = (user["id"], alarm["alarmId"], action)
    if time.time() - _view_audit.get(key, 0) > 3600:      # at most once an hour per user/alarm
        _view_audit[key] = time.time()
        db.audit(action, user, "alarm", alarm["alarmId"], project_id=alarm.get("projectId"))


def _get_alarm(user, alarm_id):
    a = datasource.find_alarm(user, alarm_id, request.args.get("projectId"))
    if not a:
        raise ApiError("not_found", "Alarm not available.", 404)   # same answer for missing and out-of-scope
    return a


def _related(user, a):
    items, _ = datasource.working_set(user, str(a["projectId"])) if rbac.project_allowed(user, a["projectId"]) \
        else ([], None)
    out = []
    t0 = datetime.fromisoformat((a.get("lastInstance") or a.get("firstInstance") or db.now_iso()).replace("Z", "+00:00"))
    ctx = a.get("context") or {}
    for b in items:
        if b["alarmId"] == a["alarmId"]:
            continue
        try:
            tb = datetime.fromisoformat((b.get("lastInstance") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        gap = abs((tb - t0).total_seconds()) / 60
        reasons = []
        if b.get("cameraId") == a.get("cameraId") and gap <= 24 * 60:
            reasons.append("Same camera")
        bctx = b.get("context") or {}
        if ctx.get("mapped") and bctx.get("mapped") and gap <= 60:
            for lvl in ("room", "centre"):
                if ctx.get(lvl) and bctx.get(lvl) and ctx[lvl]["id"] == bctx[lvl]["id"]:
                    reasons.append(f"Same {lvl} ({ctx[lvl]['code']})")
                    break
        if reasons:
            reasons.append(f"{gap:.0f} min apart")
            if b.get("alarmType") == a.get("alarmType"):
                reasons.append("Same alarm type")
            out.append({"alarm": slim(b), "reasons": reasons, "gapMinutes": round(gap, 1)})
    out.sort(key=lambda r: r["gapMinutes"])
    return out[:15]


def _clients_for_project(pid):
    return db.rows("SELECT c.id, c.name, c.status FROM clients c JOIN client_projects cp ON cp.client_id=c.id "
                   "WHERE cp.project_id=? ORDER BY c.name", (str(pid),))


@bp.route("/api/alarms/<alarm_id>")
@rbac.internal("alarm.view")
def alarm_detail(alarm_id):
    user = _user()
    a = _get_alarm(user, alarm_id)
    _audit_view(user, a, "alarm.view")
    pol = workflow.policy()
    can_inv = "alarm.investigate" in user["permissions"]
    clients = _clients_for_project(a["projectId"])
    sharing = []
    for c in clients:
        pub = next((p for p in a["publications"] if p["clientId"] == c["id"]), None)
        entry = {"client": c, "publication": pub, "actions": {},
                 "messages": workflow.messages_for(a["alarmId"], c["id"]) if pub else []}
        for action in ("request", "approve", "publish"):
            entry["actions"][action] = workflow.client_share_eligible(a, user, c, action, pol)
        sharing.append(entry)
    bm = db.one("SELECT 1 FROM bookmarks WHERE user_id=? AND alarm_id=?", (user["id"], alarm_id))
    return jsonify({
        "alarm": a,
        "notes": workflow.notes_for(alarm_id) if can_inv else [],
        "audit": db.get_audit_trail(alarm_id) if (can_inv or "audit.view" in user["permissions"]) else [],
        "related": _related(user, a) if can_inv else [],
        "sharing": sharing,
        "clientSafeSummary": workflow.client_safe_summary(a, pol["clientContextLevels"]),
        "bookmarked": bool(bm),
        "policy": {"fourEyes": pol["fourEyes"], "requireApproval": pol["requireApproval"],
                   "validSource": pol["validSource"]},
        "valid": workflow.is_valid(a, pol),
        "evidenceItems": ([{"kind": "image", "index": i, "url": u} for i, u in enumerate(a.get("imageUrls") or [])]
                          + ([{"kind": "video", "index": 0, "url": a["videoUrl"]}] if a.get("videoUrl") else []))
        if "evidence.view" in user["permissions"] else [],
        "canDownloadEvidence": "evidence.download" in user["permissions"],
    })


REVIEW_PERMS = {"acknowledge": ("alarm.investigate",), "mark_valid": ("alarm.validate",),
                "mark_invalid": ("alarm.invalidate",), "mark_exception": ("alarm.exception",),
                "reopen": ("alarm.validate", "alarm.invalidate"), "note": ("alarm.comment",)}


@bp.route("/api/alarms/<alarm_id>/review", methods=["POST"])
@rbac.internal("alarm.view")
def alarm_review(alarm_id):
    user = _user()
    b = body()
    action = b.get("action")
    if action not in REVIEW_PERMS:
        raise ApiError("bad_request", "Unknown review action.", 400)
    if not any(p in user["permissions"] for p in REVIEW_PERMS[action]):
        raise ApiError("forbidden", "You don't have permission to do that.", 403)
    a = _get_alarm(user, alarm_id)
    if action == "reopen" and a["visibility"]["state"] in ("shared", "approved", "ready_for_review"):
        raise ApiError("not_eligible", "Withdraw the client share before reopening this alarm.", 409)
    snap = workflow._snapshot(a)
    result = db.apply_action(alarm_id, action, user["name"], b.get("note"), snapshot=snap, user=user,
                             project_id=a.get("projectId"))
    if action == "mark_valid" and workflow.policy()["requireApproval"]:
        notify.to_permission("alarm.approve", "approval", f"Validated: {alarm_id}",
                             f"{user['name']} marked {a.get('alarmTypeName')} valid — ready for client review.",
                             f"#/investigations/{alarm_id}", alarm=a, dedupe=f"validated:{alarm_id}", exclude=user["id"])
    return jsonify(result)


@bp.route("/api/alarms/<alarm_id>/notes", methods=["POST"])
@rbac.internal("alarm.comment")
def alarm_note(alarm_id):
    user = _user()
    a = _get_alarm(user, alarm_id)
    b = body()
    workflow.add_note(alarm_id, b.get("kind") or "internal", b.get("body"), user, a.get("projectId"))
    return jsonify({"notes": workflow.notes_for(alarm_id)})


@bp.route("/api/alarms/<alarm_id>/assign", methods=["POST"])
@rbac.internal("alarm.assign")
def alarm_assign(alarm_id):
    user = _user()
    a = _get_alarm(user, alarm_id)
    return jsonify({"assignment": workflow.assign(a, body().get("userId"), user)})


@bp.route("/api/alarms/<alarm_id>/bookmark", methods=["POST"])
@rbac.internal("alarm.view")
def alarm_bookmark(alarm_id):
    user = _user()
    a = _get_alarm(user, alarm_id)
    if body().get("on", True):
        db.execute("INSERT OR REPLACE INTO bookmarks (user_id, alarm_id, created_at, snapshot) VALUES (?,?,?,?)",
                   (user["id"], alarm_id, db.now_iso(), db.jdump(workflow._snapshot(a))))
    else:
        db.execute("DELETE FROM bookmarks WHERE user_id=? AND alarm_id=?", (user["id"], alarm_id))
    return jsonify({"bookmarked": bool(body().get("on", True))})


@bp.route("/api/bookmarks")
@rbac.internal("alarm.view")
def bookmarks():
    user = _user()
    out = []
    for r in db.rows("SELECT * FROM bookmarks WHERE user_id=? ORDER BY created_at DESC", (user["id"],)):
        snap = db.jload(r["snapshot"], {}) or {}
        if not rbac.project_allowed(user, snap.get("projectId")):
            continue            # scope changed since bookmarking -> hide
        out.append({"alarmId": r["alarm_id"], "createdAt": r["created_at"], "alarmTypeName": snap.get("alarmTypeName"),
                    "priority": snap.get("priority"), "projectId": snap.get("projectId"),
                    "camera": ((snap.get("context") or {}).get("camera") or {}).get("code"),
                    "lastInstance": snap.get("lastInstance")})
    return jsonify({"items": out})


@bp.route("/api/users/assignable")
@rbac.internal("alarm.assign")
def assignable():
    user = _user()
    pid = project_param(user)
    out = []
    for r in db.rows("SELECT id FROM users WHERE status='active'"):
        u = rbac.load_user(r["id"])
        if u and u["audience"] == "internal" and "alarm.investigate" in u["permissions"] and rbac.project_allowed(u, pid):
            out.append({"id": u["id"], "name": u["name"], "role": u["roleName"]})
    return jsonify({"items": out})


# ---------------------------------------------------------------------------
# History (Camview documented history query)
# ---------------------------------------------------------------------------

def _epoch_ms(iso):
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except (AttributeError, ValueError):
        raise ApiError("bad_request", "Dates must be ISO format.", 400)


@bp.route("/api/history")
@rbac.internal("history.view")
def history():
    user = _user()
    pid = project_param(user)
    args = request.args
    page, size = int(args.get("page") or 1), min(100, int(args.get("size") or 50))
    if config.MODE == "demo":
        items = datasource.refresh(pid).items
        enriched = [a for a in datasource.enrich(items) if rbac.alarm_in_scope(user, a)]
        filtered = filter_alarms(enriched, args, user)
        out = paginate(filtered, page, size)
        out["items"] = [slim(a) for a in out["items"]]
        out["source"] = "demo"
        return jsonify(out)
    req = {"projectId": pid, "page": page, "size": size, "useHistory": True}
    if args.get("from"):
        req["startTime"] = _epoch_ms(args["from"])
    if args.get("to"):
        req["endTime"] = _epoch_ms(args["to"])
    for k in ("alarmType", "lastActionType", "shiftLabel", "lastKey"):
        if args.get(k) not in (None, ""):
            req[k] = args[k]
    raw, data = list_page(req)
    enriched = [a for a in datasource.enrich(datasource._normalize(raw)) if rbac.alarm_in_scope(user, a)]
    local = {k: v for k, v in args.items() if k not in ("alarmType", "lastActionType", "shiftLabel", "from", "to")}
    enriched = filter_alarms(enriched, local, user)
    return jsonify({"items": [slim(a) for a in enriched], "page": (data.get("page") or 0) + 1,
                    "size": data.get("size") or size, "totalElements": data.get("totalElements"),
                    "totalPages": data.get("totalPages"), "hasNext": bool(data.get("hasNext")),
                    "lastKey": data.get("lastKey"), "source": "camview-history"})


# ---------------------------------------------------------------------------
# Intelligent alerts & My Work
# ---------------------------------------------------------------------------

@bp.route("/api/alerts")
@rbac.internal("alert.view")
def alerts():
    user = _user()
    pid = project_param(user)
    items, feed = datasource.working_set(user, pid)
    out, skipped = intelligence.evaluate(items, workflow.policy(), datasource.freshness(feed), _rules())
    cat = request.args.get("category")
    if cat:
        out = [a for a in out if a["category"] == cat]
    return jsonify({"items": out, "skipped": skipped, "policy": workflow.policy()})


@bp.route("/api/work")
@rbac.internal("work.view")
def work():
    user = _user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    p = user["permissions"]
    by_prio = lambda xs: sorted(xs, key=lambda a: (a["priorityRank"], -(a.get("ageMinutes") or 0)))  # noqa: E731
    sections = []

    def add(key, title, xs, perm=None, link=None, empty="Nothing here."):
        if perm and perm not in p:
            return
        sections.append({"key": key, "title": title, "count": len(xs), "items": [slim(a) for a in xs[:25]],
                         "link": link, "empty": empty})

    if any(x in p for x in ("alarm.validate", "alarm.invalidate")):
        add("pending", "Pending review", by_prio([a for a in items if a["flags"]["pending"]]), link="#/live?quick=pending",
            empty="No alarms are waiting for review.")
    add("mine", "My investigations", [a for a in items if (a.get("assignment") or {}).get("userId") == user["id"]
                                      and a["workflowState"] not in ("CLOSED",)],
        perm="alarm.investigate", link="#/live?quick=mine", empty="No investigations are assigned to you.")
    add("critical", "Critical activity", by_prio([a for a in items if a["flags"]["criticalPending"]]),
        link="#/live?quick=critical", empty="No critical alarms pending.")
    add("approvals", "Approval requests", [a for a in items if a["workflowState"] == "READY_FOR_APPROVAL"],
        perm="alarm.approve", link="#/sharing?tab=ready_for_review", empty="No approval requests.")
    add("sharing", "Client sharing", [a for a in items if a["workflowState"] in ("READY_FOR_CLIENT", "APPROVED")],
        perm="alarm.publish", link="#/sharing", empty="Nothing waiting to be shared.")
    add("evidence", "Evidence to review", by_prio([a for a in items if a["flags"]["pending"] and a["flags"]["evidence"]]),
        perm="evidence.view", link="#/evidence?pending=1", empty="No pending evidence.")
    queue_rules = [r for r in _rules() if r.get("action") == "queue"]
    if queue_rules:
        flagged = [a for a in items if a["flags"]["pending"]
                   and any(intelligence.rule_matches(r, a) for r in queue_rules)]
        add("rules", "Flagged by alert rules", by_prio(flagged), link="#/insights",
            empty="No pending alarms match your work-queue rules.")
    add("escalations", "Escalations / attention", [a for a in items if a.get("sla") == "attention" or a["flags"]["longPending"]],
        link="#/insights", empty="No items over the configured time limits (or no limits configured).")
    rep = db.rows("SELECT id, title, audience, generated_at FROM reports WHERE generated_by=? ORDER BY id DESC LIMIT 5",
                  (user["id"],)) if "report.view" in p else []
    return jsonify({"sections": sections, "reports": rep, "unreadNotifications": notify.unread_count(user["id"]),
                    "role": user["roleName"]})


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------

@bp.route("/api/cameras")
@rbac.internal("camera.view")
def cameras():
    user = _user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    stats = {str(c["cameraId"]): c for c in analytics.top_cameras(items, 10000)}
    proj = next((p for p in nomenclature.projects() if p["externalId"] == pid), None)
    master = set()
    if proj:
        for cam in nomenclature.descendant_cameras(proj["id"]):
            master.add(cam)
    rows = []
    for cam_id in master | set(stats):
        s = stats.get(cam_id)
        if s is None:
            ctx = nomenclature.resolve(pid, cam_id)
            probe = {"projectId": pid, "cameraId": cam_id, "context": ctx}
            if not rbac.alarm_in_scope(user, probe):
                continue
            s = {"cameraId": cam_id, "code": ctx["camera"]["code"], "name": ctx["camera"].get("name"),
                 "location": " / ".join(n["code"] for n in ctx["path"][:-1]), "mapped": ctx["mapped"], "count": 0,
                 "critical": 0, "pending": 0, "occurrences": 0, "latest": None, "latestAlarmId": None}
        rows.append(s)
    sort = request.args.get("sort", "count")
    keyf = {"count": lambda r: -r["count"], "recent": lambda r: r["latest"] or "", "occurrences": lambda r: -r["occurrences"],
            "code": lambda r: r["code"] or ""}.get(sort, lambda r: -r["count"])
    rows.sort(key=keyf, reverse=(sort == "recent"))
    return jsonify({"items": rows, "masterCameras": len(master)})


@bp.route("/api/cameras/<cam_id>")
@rbac.internal("camera.view")
def camera_detail(cam_id):
    user = _user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    cam_items = [a for a in items if str(a.get("cameraId")) == str(cam_id)]
    ctx = nomenclature.resolve(pid, cam_id)
    if not cam_items and not ctx["mapped"]:
        raise ApiError("not_found", "Camera not available.", 404)
    if not cam_items and not rbac.alarm_in_scope(user, {"projectId": pid, "cameraId": cam_id, "context": ctx}):
        raise ApiError("not_found", "Camera not available.", 404)
    hours = Counter()
    for a in cam_items:
        try:
            hours[datetime.fromisoformat(a["firstInstance"].replace("Z", "+00:00"))
                  .astimezone(timezone(timedelta(minutes=_tz()))).hour] += 1
        except (ValueError, AttributeError, TypeError):
            pass
    peak = hours.most_common(1)[0] if hours else None
    related_inv = [slim(a) for a in cam_items if a.get("assignment") or a["workflowState"] in ("INVESTIGATING",)]
    return jsonify({
        "camera": {"cameraId": cam_id, "context": ctx, "code": ctx["camera"]["code"], "name": ctx["camera"].get("name")},
        "metrics": analytics.metrics(cam_items),
        "priorityDistribution": analytics.priority_distribution(cam_items),
        "typeDistribution": analytics.type_distribution(cam_items),
        "statusDistribution": analytics.status_distribution(cam_items),
        "hourly": analytics.hourly(cam_items, 24, _tz()),
        "daily": analytics.daily(cam_items, 14, _tz()),
        "peak": {"hour": peak[0], "count": peak[1]} if peak else None,
        "occurrences": sum(a.get("totalTimesReported") or 1 for a in cam_items),
        "latest": slim(max(cam_items, key=lambda a: a.get("lastInstance") or "")) if cam_items else None,
        "alarms": [slim(a) for a in sorted(cam_items, key=lambda a: a.get("lastInstance") or "", reverse=True)[:100]],
        "evidence": [{"alarmId": a["alarmId"], "images": a.get("imageUrls") or [], "video": a.get("videoUrl"),
                      "at": a.get("lastInstance")} for a in cam_items if a["flags"]["evidence"]][:24]
        if "evidence.view" in user["permissions"] else [],
        "investigations": related_inv,
    })


# ---------------------------------------------------------------------------
# Context explorer / nomenclature (read)
# ---------------------------------------------------------------------------

def _count_tree(nodes, by_cam):
    for n in nodes:
        _count_tree(n["children"], by_cam)
        if n["level"] == "camera":
            items = by_cam.get(n["externalId"], [])
            n["count"] = len(items)
            n["critical"] = sum(1 for a in items if a.get("priority") == "critical")
            n["pending"] = sum(1 for a in items if a["flags"]["pending"])
        else:
            n["count"] = sum(c["count"] for c in n["children"])
            n["critical"] = sum(c["critical"] for c in n["children"])
            n["pending"] = sum(c["pending"] for c in n["children"])


@bp.route("/api/context/tree")
@rbac.internal("nomenclature.view")
def context_tree():
    user = _user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    by_cam = {}
    for a in items:
        by_cam.setdefault(str(a.get("cameraId")), []).append(a)
    tree = [n for n in nomenclature.tree() if n["externalId"] == pid]
    _count_tree(tree, by_cam)
    return jsonify({"tree": tree, "levels": nomenclature.LEVEL_LABELS,
                    "unmappedAlarms": sum(1 for a in items if not a["context"]["mapped"])})


@bp.route("/api/context/node/<path:node_id>")
@rbac.internal("nomenclature.view")
def context_node(node_id):
    user = _user()
    n = nomenclature.node(node_id)
    if not n or not n["path"] or n["path"][0]["level"] != "project":
        raise ApiError("not_found", "Node not available.", 404)
    pid = n["path"][0]["externalId"]
    if not rbac.project_allowed(user, pid):
        raise ApiError("not_found", "Node not available.", 404)
    items, _ = datasource.working_set(user, pid)
    cams = nomenclature.descendant_cameras(node_id)
    sub = [a for a in items if str(a.get("cameraId")) in cams] if n["level"] != "project" else items
    child_rows = []
    for c in n["children"]:
        ccams = nomenclature.descendant_cameras(c["id"])
        citems = [a for a in sub if str(a.get("cameraId")) in ccams]
        m = analytics.metrics(citems)
        child_rows.append({**c, "total": m["total"], "critical": m["critical"], "pending": m["pending"],
                           "valid": m["valid"], "invalid": m["invalid"], "shared": m["sharedWithClient"]})
    return jsonify({"node": n, "metrics": analytics.metrics(sub), "children": child_rows,
                    "hourly": analytics.hourly(sub, 24, _tz()), "priorityDistribution": analytics.priority_distribution(sub),
                    "recent": [slim(a) for a in sorted(sub, key=lambda a: a.get("lastInstance") or "", reverse=True)[:20]]})


@bp.route("/api/context/quality")
@rbac.internal("nomenclature.view")
def context_quality():
    user = _user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    known = {r["id"] for r in db.rows("SELECT id FROM alarm_types")}
    return jsonify(nomenclature.quality(items, known))


# ---------------------------------------------------------------------------
# Global search (Ctrl+K)
# ---------------------------------------------------------------------------

@bp.route("/api/search")
@rbac.internal("alarm.view")
def search():
    user = _user()
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"groups": {}})
    ql = q.lower()
    groups = {}
    for pid in datasource.allowed_projects(user):
        items, _ = datasource.working_set(user, pid)
        for a in items:
            if ql in a["alarmId"].lower() or ql == str(a.get("ticketId") or "").lower():
                key = "tickets" if ql == str(a.get("ticketId") or "").lower() else "alarms"
                groups.setdefault(key, [])
                if len(groups[key]) < 8:
                    groups[key].append({"id": a["alarmId"], "label": a["alarmId"], "sub": f"{a.get('alarmTypeName')} · "
                                        f"{a.get('cameraCode')}" + (f" · ticket #{a['ticketId']}" if a.get("ticketId") else ""),
                                        "link": f"#/investigations/{a['alarmId']}?projectId={pid}"})
            if (a.get("assignment") and ql in a["alarmId"].lower()):
                groups.setdefault("investigations", [])
    if "nomenclature.view" in user["permissions"]:
        for level, nodes in nomenclature.search(q).items():
            vis = []
            for n in nodes:
                proj = n["path"][0] if n["path"] else None
                if not proj or not rbac.project_allowed(user, proj["externalId"]):
                    continue
                link = f"#/cameras/{n['externalId']}?projectId={proj['externalId']}" if level == "camera" \
                    else f"#/context?node={n['id']}&projectId={proj['externalId']}"
                vis.append({"id": n["id"], "label": n["code"], "sub": " / ".join(x["code"] for x in n["path"][:-1])
                            + (f" · {n['name']}" if n["name"] and n["name"] != n["code"] else ""), "link": link})
            if vis:
                groups[{"project": "projects", "tc": "tc", "centre": "centres", "camera": "cameras"}
                       .get(level, level)] = vis
    return jsonify({"groups": groups})


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def _range(args):
    tz = _tz()
    name = args.get("range", "24h")
    if name == "custom" and args.get("from") and args.get("to"):
        start = datetime.fromisoformat(args["from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(args["to"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone(timedelta(minutes=tz)))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone(timedelta(minutes=tz)))
        days = (end - start).total_seconds() / 86400
        return start, end, "hour" if days <= 1.01 else "day", "Custom range"
    return kpis.resolve_range(name, tz)


@bp.route("/api/analytics")
@rbac.internal("analytics.view")
def analytics_view():
    user = _user()
    pid = project_param(user)
    start, end, bucket, label = _range(request.args)
    items, truncated = datasource.fetch_range(user, pid, start, end)
    tz = _tz()
    days = max(1, int((end - start).total_seconds() // 86400) + 1)
    verdicts = kpis.compute(items, db.reviews_for([a["alarmId"] for a in items]),
                            db.list_reviews(kpis.iso(start), kpis.iso(end + timedelta(seconds=1))),
                            db.audit_stats(kpis.iso(start), kpis.iso(end + timedelta(seconds=1))),
                            start, end, bucket, label, scanned=len(items), truncated=truncated)
    shared = db.rows("SELECT shared_at FROM publications WHERE project_id=? AND shared_at >= ? AND shared_at <= ?",
                     (pid, kpis.iso(start), kpis.iso(end)))
    shared_daily = Counter(r["shared_at"][:10] for r in shared if r["shared_at"])
    return jsonify({
        "range": {"start": start.isoformat(), "end": end.isoformat(), "bucket": bucket, "label": label},
        "truncated": truncated, "count": len(items),
        "metrics": analytics.metrics(items),
        "statusDistribution": analytics.status_distribution(items),
        "priorityDistribution": analytics.priority_distribution(items),
        "typeDistribution": analytics.type_distribution(items),
        "shiftDistribution": analytics.shift_distribution(items),
        "byTc": analytics.level_breakdown(items, "tc"),
        "byCentre": analytics.level_breakdown(items, "centre"),
        "topCameras": analytics.top_cameras(items, 15),
        "hourly": analytics.hourly(items, 24, tz, end),
        "daily": analytics.daily(items, min(max(days, 7), 62), tz, end),
        "heatmap": analytics.heatmap(items, min(max(days, 7), 31), tz, end),
        "recurrence": analytics.recurrence(items),
        "suppressed": sum(1 for a in items if a.get("suppressed")),
        "clientShared": [{"date": d, "count": c} for d, c in sorted(shared_daily.items())],
        "verdicts": verdicts,
    })


# ---------------------------------------------------------------------------
# Evidence center
# ---------------------------------------------------------------------------

@bp.route("/api/evidence")
@rbac.internal("evidence.view")
def evidence():
    user = _user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    items = [a for a in filter_alarms(items, request.args, user) if a["flags"]["evidence"]]
    if request.args.get("pending") == "1":
        items = [a for a in items if a["flags"]["pending"]]
    page = paginate(items, request.args.get("page"), request.args.get("size") or 24)
    out = []
    for a in page["items"]:
        shared = {}
        for p in a["publications"]:
            for e in p["evidence"]:
                if e.get("shared") and p["status"] == "shared":
                    shared.setdefault(f"{e['kind']}:{e['index']}", []).append(p["clientName"])
        out.append({**slim(a), "imageUrls": a.get("imageUrls") or [], "videoUrl": a.get("videoUrl"),
                    "sharedEvidence": shared})
    page["items"] = out
    return jsonify(page)


@bp.route("/api/evidence/log", methods=["POST"])
@rbac.internal("evidence.view")
def evidence_log():
    """Audit an evidence view/download (downloads need evidence.download)."""
    user = _user()
    b = body()
    action = "evidence.download" if b.get("download") else "evidence.view"
    if action == "evidence.download" and "evidence.download" not in user["permissions"]:
        raise ApiError("forbidden", "You don't have permission to download evidence.", 403)
    a = _get_alarm(user, str(b.get("alarmId")))
    db.audit(action, user, "alarm", a["alarmId"], project_id=a.get("projectId"),
             details={"kind": b.get("kind"), "index": b.get("index")})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Shift control & handover
# ---------------------------------------------------------------------------

def _shift_snapshot(items, label):
    today = datetime.now(timezone(timedelta(minutes=_tz()))).date().isoformat()
    cur = [a for a in items if a.get("shiftLabel") == label and (a.get("firstInstance") or "")[:10] >= today[:10]] \
        if label else []
    return {
        "activeAlarms": len(cur),
        "pendingReviews": sum(1 for a in items if a["flags"]["pending"]),
        "criticalPending": sum(1 for a in items if a["flags"]["criticalPending"]),
        "openInvestigations": sum(1 for a in items if a["workflowState"] == "INVESTIGATING"),
        "clientApprovals": sum(1 for a in items if a["workflowState"] == "READY_FOR_APPROVAL"),
        "readyToShare": sum(1 for a in items if a["workflowState"] in ("APPROVED", "READY_FOR_CLIENT")),
    }


@bp.route("/api/shift")
@rbac.internal("shift.view")
def shift():
    user = _user()
    pid = project_param(user)
    items, feed = datasource.working_set(user, pid)
    latest = max(items, key=lambda a: a.get("lastInstance") or "", default=None)
    label = latest.get("shiftLabel") if latest else None
    snap = _shift_snapshot(items, label)
    handovers = [{**h, "snapshot": db.jload(h["snapshot"], {})} for h in
                 db.rows("SELECT * FROM handovers WHERE project_id=? ORDER BY id DESC LIMIT 20", (pid,))]
    return jsonify({"currentShift": label, "currentShiftSource": "derived from the most recent alarm's shiftLabel",
                    "snapshot": snap, "handovers": handovers,
                    "critical": [slim(a) for a in items if a["flags"]["criticalPending"]][:10],
                    "freshness": datasource.freshness(feed)})


@bp.route("/api/handovers", methods=["POST"])
@rbac.internal("shift.handover")
def create_handover():
    user = _user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    b = body()
    snap = _shift_snapshot(items, b.get("fromShift"))
    snap["openItems"] = snap["pendingReviews"] + snap["openInvestigations"] + snap["clientApprovals"]
    snap["watchlist"] = [f"{w['entity_type'].upper()} {w['label'] or w['entity_id']}" for w in
                         db.rows("SELECT * FROM watchlist WHERE user_id=? AND project_id=?", (user["id"], pid))]
    snap["watchlist"] += [str(x).strip()[:120] for x in (b.get("watchlist") or []) if str(x).strip()]
    hid = db.execute("INSERT INTO handovers (from_shift, to_shift, project_id, notes, snapshot, created_by, created_by_name, "
                     "created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (b.get("fromShift"), b.get("toShift"), pid, (b.get("notes") or "").strip()[:4000], db.jdump(snap),
                      user["id"], user["name"], db.now_iso()))
    db.audit("shift.handover", user, "handover", hid, project_id=pid, details=snap)
    notify.to_permission("shift.view", "operational", f"Shift handover: {b.get('fromShift')} → {b.get('toShift')}",
                         (b.get("notes") or "")[:200], "#/shift", project_id=pid, dedupe=f"handover:{hid}",
                         exclude=user["id"])
    return jsonify({"id": hid})


# ---------------------------------------------------------------------------
# Presentation mode (internal)
# ---------------------------------------------------------------------------

@bp.route("/api/presentation")
@rbac.internal("presentation.view")
def presentation():
    user = _user()
    pid = project_param(user)
    items, feed = datasource.working_set(user, pid)
    m = analytics.metrics(items)
    return jsonify({"audience": "internal", "project": project_label(pid), "freshness": datasource.freshness(feed),
                    "metrics": m, "hourly": analytics.hourly(items, 24, _tz()),
                    "priorityDistribution": analytics.priority_distribution(items),
                    "shiftDistribution": analytics.shift_distribution(items),
                    "statusDistribution": analytics.status_distribution(items),
                    "byTc": analytics.level_breakdown(items, "tc")[:6],
                    "critical": [slim(a) for a in items if a["flags"]["criticalPending"]][:5]})


# ---------------------------------------------------------------------------
# Activity groups (noise reduction) — raw alarms are always one click away
# ---------------------------------------------------------------------------

@bp.route("/api/groups")
@rbac.internal("live.view", "alarm.view", any_of=True)
def activity_groups():
    user = _user()
    pid = project_param(user)
    items, feed = datasource.working_set(user, pid)
    filtered = filter_alarms(items, request.args, user)
    by = request.args.get("by", "camera")
    if by not in ("camera", "room", "centre", "tc"):
        raise ApiError("bad_request", "by must be camera, room, centre or tc.", 400)
    try:
        gap = max(1.0, min(240.0, float(request.args.get("gap") or 10)))
    except ValueError:
        raise ApiError("bad_request", "gap must be a number of minutes.", 400)
    groups, rule = analytics.group_activity(filtered, by, gap)
    if request.args.get("multiOnly") == "1":
        groups = [g for g in groups if g["count"] > 1]
    by_id = {a["alarmId"]: a for a in filtered}
    page = paginate(groups, request.args.get("page"), request.args.get("size") or 25)
    for g in page["items"]:
        g["alarms"] = [slim(by_id[i]) for i in g["alarmIds"] if i in by_id]
    page.update(rawAlarms=len(filtered), groupCount=len(groups), alarmsInGroups=sum(g["count"] for g in groups),
                rule=rule, by=by, gapMinutes=gap,
                freshness=datasource.freshness(feed))
    return jsonify(page)


# ---------------------------------------------------------------------------
# Since last visit / daily brief / comparison
# ---------------------------------------------------------------------------

@bp.route("/api/since-last-visit")
@rbac.internal("dashboard.view")
def since_last_visit():
    user = _user()
    pid = project_param(user)
    row = db.one("SELECT prev_login_at FROM users WHERE id=?", (user["id"],))
    since = row and row["prev_login_at"]
    if not since:
        return jsonify({"available": False, "reason": "This is your first sign-in, so there is no previous visit to "
                                                      "compare with."})
    items, feed = datasource.working_set(user, pid)
    oldest = min((a.get("firstInstance") or "" for a in items), default="")
    reliable = bool(items) and oldest <= since
    new = [a for a in items if (a.get("firstInstance") or "") > since]
    audit = db.rows("SELECT action, COUNT(*) AS n FROM audit_events WHERE at > ? AND (project_id=? OR project_id IS NULL) "
                    "GROUP BY action", (since, pid))
    counts = {r["action"]: r["n"] for r in audit}
    return jsonify({
        "available": True, "since": since, "reliable": reliable,
        "note": None if reliable else "Your previous visit is older than the monitored window, so alarm counts "
                                      "cover only the newest alarms available.",
        "newAlarms": len(new), "newCritical": sum(1 for a in new if a.get("priority") == "critical"),
        "newValidated": counts.get("review.mark_valid", 0), "newInvalidated": counts.get("review.mark_invalid", 0),
        "newShares": counts.get("share.publish", 0), "newApprovalRequests": counts.get("share.request", 0),
        "newAcknowledgements": counts.get("client.acknowledge", 0),
        "pendingNow": sum(1 for a in items if a["flags"]["pending"]),
        "sample": [slim(a) for a in sorted(new, key=lambda a: a.get("firstInstance") or "", reverse=True)[:6]],
    })


@bp.route("/api/brief")
@rbac.internal("dashboard.view")
def daily_brief():
    """DAILY OPERATIONS BRIEF — derived from data for one day (viewer's time zone)."""
    user = _user()
    pid = project_param(user)
    tz = timezone(timedelta(minutes=_tz()))
    day = request.args.get("date") or datetime.now(tz).date().isoformat()
    try:
        start = datetime.fromisoformat(day).replace(tzinfo=tz)
    except ValueError:
        raise ApiError("bad_request", "date must be YYYY-MM-DD.", 400)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    items, truncated = datasource.fetch_range(user, pid, start, end)
    m = analytics.metrics(items)
    hours = analytics.hourly(items, 24, _tz(), end)
    peak = max(hours, key=lambda h: h["total"], default=None)
    cams = analytics.top_cameras(items, 3)
    shared = db.one("SELECT COUNT(*) AS n FROM publications WHERE project_id=? AND shared_at BETWEEN ? AND ?",
                    (pid, kpis.iso(start), kpis.iso(end)))["n"]
    validated = db.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='review.mark_valid' AND project_id=? "
                       "AND at BETWEEN ? AND ?", (pid, kpis.iso(start), kpis.iso(end)))["n"]
    current, _ = datasource.working_set(user, pid)
    return jsonify({
        "date": day, "project": project_label(pid), "truncated": truncated,
        "totals": {"alarms": m["total"], "critical": m["critical"], "pending": m["pending"], "valid": m["valid"],
                   "invalid": m["invalid"], "exception": m["exception"], "suppressed": m["suppressed"],
                   "validatedByOps": validated, "sharedWithClients": shared},
        "peakPeriod": {"start": peak["start"], "count": peak["total"]} if peak and peak["total"] else None,
        "highestActivity": cams,
        "openInvestigations": sum(1 for a in current if a["workflowState"] == "INVESTIGATING"),
        "byShift": analytics.shift_distribution(items), "byPriority": analytics.priority_distribution(items),
        "hourly": hours, "provenance": "derived",
    })


@bp.route("/api/compare")
@rbac.internal("analytics.view")
def compare_entities():
    """Compare projects / TCs / centres on volume, priority and status (authorized data only)."""
    user = _user()
    level = request.args.get("level", "project")
    if level not in ("project", "tc", "centre"):
        raise ApiError("bad_request", "level must be project, tc or centre.", 400)
    start, end, bucket, label = _range(request.args)
    pids = datasource.allowed_projects(user) if level == "project" else [project_param(user)]
    rows = []
    for pid in pids:
        items, truncated = datasource.fetch_range(user, pid, start, end)
        if level == "project":
            m = analytics.metrics(items)
            p = project_label(pid)
            rows.append({"code": p["code"], "name": p["name"], **_cmp_row(items, m), "truncated": truncated})
        else:
            groups = {}
            for a in items:
                node = (a.get("context") or {}).get(level)
                groups.setdefault((node or {}).get("code", "Unmapped"), ((node or {}).get("name"), []))[1].append(a)
            for code, (name, members) in groups.items():
                rows.append({"code": code, "name": name or code, **_cmp_row(members, analytics.metrics(members)),
                             "truncated": truncated})
    wanted = [x for x in (request.args.get("ids") or "").split(",") if x]
    if wanted:
        rows = [r for r in rows if r["code"] in wanted]
    rows.sort(key=lambda r: -r["total"])
    return jsonify({"level": level, "range": {"start": start.isoformat(), "end": end.isoformat(), "label": label},
                    "rows": rows})


def _cmp_row(items, m):
    return {"total": m["total"], "critical": m["critical"], "pending": m["pending"], "valid": m["valid"],
            "invalid": m["invalid"], "exception": m["exception"], "shared": m["sharedWithClient"],
            "falseAlarmRate": m["falseAlarmRate"],
            "priority": {d["key"]: d["count"] for d in analytics.priority_distribution(items)},
            "cameras": len({a.get("cameraId") for a in items})}


# ---------------------------------------------------------------------------
# Watchlist (alarms, cameras, projects, TC, centres, rooms)
# ---------------------------------------------------------------------------

WATCH_TYPES = ("alarm", "camera", "project", "tc", "centre", "room")


@bp.route("/api/watchlist")
@rbac.internal("alarm.view")
def watchlist():
    user = _user()
    rows = db.rows("SELECT * FROM watchlist WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
    cache = {}
    out = []
    for w in rows:
        pid = w["project_id"]
        if not pid or not rbac.project_allowed(user, pid):
            continue                       # scope changed since it was watched
        if pid not in cache:
            cache[pid] = datasource.working_set(user, pid)[0]
        matches = [a for a in cache[pid] if notify.watch_matches(w, a)]
        latest = max(matches, key=lambda a: a.get("lastInstance") or "", default=None)
        out.append({"entityType": w["entity_type"], "entityId": w["entity_id"], "label": w["label"], "projectId": pid,
                    "createdAt": w["created_at"], "alarms": len(matches),
                    "pending": sum(1 for a in matches if a["flags"]["pending"]),
                    "critical": sum(1 for a in matches if a.get("priority") == "critical"),
                    "newSinceWatched": sum(1 for a in matches if (a.get("firstInstance") or "") > (w["created_at"] or "")),
                    "latest": slim(latest) if latest else None})
    return jsonify({"items": out, "types": WATCH_TYPES})


@bp.route("/api/watchlist", methods=["POST"])
@rbac.internal("alarm.view")
def watch_add():
    user = _user()
    b = body()
    etype, eid = b.get("entityType"), str(b.get("entityId") or "").strip()
    if etype not in WATCH_TYPES or not eid:
        raise ApiError("bad_request", "entityType and entityId are required.", 400)
    pid = str(b.get("projectId") or project_param(user))
    if not rbac.project_allowed(user, pid):
        raise ApiError("not_found", "Project unavailable.", 404)
    db.execute("INSERT OR REPLACE INTO watchlist (user_id, entity_type, entity_id, label, project_id, created_at) "
               "VALUES (?,?,?,?,?,?)", (user["id"], etype, eid, str(b.get("label") or eid)[:120], pid, db.now_iso()))
    return jsonify({"ok": True})


@bp.route("/api/watchlist/remove", methods=["POST"])
@rbac.internal("alarm.view")
def watch_remove():
    user = _user()
    b = body()
    db.execute("DELETE FROM watchlist WHERE user_id=? AND entity_type=? AND entity_id=?",
               (user["id"], b.get("entityType"), str(b.get("entityId") or "")))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Monitor: centre alarm-status board (simple home screen)
# ---------------------------------------------------------------------------

HEALTH_RULE = ("ALARM = at least one critical or high alarm waiting for review · "
               "WARNING = other alarms waiting for review · OK = nothing waiting for review. "
               "This reflects alarm status only — not camera or network health.")


@bp.route("/api/health")
@rbac.internal("live.view", "alarm.view", any_of=True)
def health_board():
    user = _user()
    pid = project_param(user)
    items, feed = datasource.working_set(user, pid)
    tiles = {}
    proj = next((p for p in nomenclature.projects() if p["externalId"] == pid), None)
    if proj:                                     # every centre in master data appears, even with no alarms
        stack = [nomenclature.node(proj["id"])]
        while stack:
            n = stack.pop()
            if not n:
                continue
            if n["level"] == "centre":
                path = {x["level"]: x for x in n["path"]}
                tiles[n["code"]] = {"code": n["code"], "name": n["name"], "nodeId": n["id"],
                                    "tc": (path.get("tc") or {}).get("code"),
                                    "alarms": []}
            else:
                stack.extend(nomenclature.node(c["id"]) for c in n["children"] if c["level"] in
                             ("tc", "centre"))
    for a in items:
        centre = (a.get("context") or {}).get("centre")
        key = centre["code"] if centre and not centre.get("unmapped") else "__unmapped__"
        if key not in tiles:
            tiles[key] = {"code": "Unmapped cameras" if key == "__unmapped__" else key,
                          "name": "Cameras not in the nomenclature" if key == "__unmapped__" else (centre or {}).get("name"),
                          "nodeId": None, "tc": None, "alarms": [], "unmapped": key == "__unmapped__"}
        tiles[key]["alarms"].append(a)
    tz = timezone(timedelta(minutes=_tz()))           # "today" and the hourly trend in the viewer's time zone
    today = datetime.now(tz).date()

    def local(iso):
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz)
        except (AttributeError, ValueError, TypeError):
            return None

    out = []
    for key, t in tiles.items():
        alerts_only = [a for a in t["alarms"] if a.get("eventKind") != "camera_status"]
        cams, sync_failed = {}, set()
        for a in t["alarms"]:
            h = a.get("health") or {}
            if h.get("available"):
                cams[str(a.get("cameraId"))] = h["camera"]["state"]
                if "FRAME_SYNC_FAILED" in (h.get("conditions") or []):
                    sync_failed.add(str(a.get("cameraId")))
        t["camerasOffline"] = sum(1 for s in cams.values() if s == "offline")
        t["camerasOnline"] = sum(1 for s in cams.values() if s == "online")
        hourly = [0] * 24
        for a in alerts_only:
            d = local(a.get("lastInstance"))
            if d and d.date() == today:
                hourly[d.hour] += 1
        t["hourly"], t["today"], t["syncFailed"] = hourly, sum(hourly), len(sync_failed)
        places = Counter((a.get("cameraCity"), a.get("cameraState")) for a in t["alarms"] if a.get("cameraCity"))
        t["city"], t["state"] = places.most_common(1)[0][0] if places else (None, None)
        if not t.get("name"):
            t["name"] = next((a.get("centreName") for a in t["alarms"] if a.get("centreName")), None)
        t["typeTop"] = Counter(a.get("alarmTypeName") for a in alerts_only).most_common(2)
        t["alarms"] = alerts_only
        todo = [a for a in t["alarms"] if a["flags"]["pending"]]
        urgent = [a for a in todo if a.get("priority") in ("critical", "high")]
        state = "alarm" if urgent else "warning" if todo else "ok"
        latest = max(t["alarms"], key=lambda a: a.get("lastInstance") or "", default=None)
        out.append({"key": key, "code": t["code"], "name": t["name"], "nodeId": t["nodeId"], "tc": t["tc"],
                    "unmapped": t.get("unmapped", False), "state": state,
                    "toReview": len(todo), "urgent": len(urgent),
                    "critical": sum(1 for a in todo if a.get("priority") == "critical"),
                    "total": len(t["alarms"]), "cameras": len({a.get("cameraId") for a in t["alarms"]}),
                    "lastAlarmAt": latest.get("lastInstance") if latest else None,
                    "new": sum(1 for a in t["alarms"] if a["flags"]["new"]),
                    "today": t.get("today", 0), "camerasOffline": t.get("camerasOffline", 0),
                    "camerasOnline": t.get("camerasOnline", 0), "syncFailed": t.get("syncFailed", 0),
                    "city": t.get("city"), "state_name": t.get("state"), "hourly": t.get("hourly") or [0] * 24,
                    "topTypes": [x for x, _ in t.get("typeTop", [])]})
    order = {"alarm": 0, "warning": 1, "ok": 2}
    out.sort(key=lambda t: (order[t["state"]], -t["urgent"], -t["toReview"], t["code"]))
    counts = {s: sum(1 for t in out if t["state"] == s) for s in order}
    return jsonify({"tiles": out, "counts": counts, "rule": HEALTH_RULE, "freshness": datasource.freshness(feed),
                    "toReview": sum(1 for a in items if a["flags"]["pending"]), "project": project_label(pid)})


# ---------------------------------------------------------------------------
# Demo evidence frames (DEMO MODE ONLY — synthetic, watermarked)
# ---------------------------------------------------------------------------

@bp.route("/demo-evidence/<name>.svg")
@rbac.internal()
def demo_evidence_frame(name):
    if config.MODE != "demo":
        raise ApiError("not_found", "Not available.", 404)
    import demo_evidence
    from flask import Response
    try:
        frame = int(request.args.get("f") or 0)
    except ValueError:
        frame = 0
    svg = demo_evidence.render(name, request.args.get("cam", ""), request.args.get("room", ""),
                               request.args.get("ts", ""), request.args.get("type", ""), frame)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})
