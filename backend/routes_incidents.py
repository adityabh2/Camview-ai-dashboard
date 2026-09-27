"""
routes_incidents.py — incidents (V2): related alerts handled once (audience: internal only).

  GET  /api/incidents                     list (status / owner / search / alarm filters, counts per status)
  GET  /api/incidents/suggestions         explained correlation suggestions from the alerts you may see
  POST /api/incidents                     create from a suggestion or from chosen alerts   (alarm.investigate)
  GET  /api/incidents/<id>                incident + its alerts as Camview reports them now + timeline
  PUT  /api/incidents/<id>                status / owner / severity / title / resolution    (alarm.investigate)
  POST /api/incidents/<id>/comments       timeline comment                (alarm.comment or alarm.investigate)
  POST /api/incidents/<id>/alarms         add / remove alerts                                (alarm.investigate)

Scope: an incident is visible only when its project is; its alerts go through datasource.find_alarm, so
alerts outside the viewer's scope are hidden (and counted as hidden), never shown.
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import config
import datasource
import db
import incidents
import rbac
import routes_queue
from camview_client import ApiError
from routes_common import body, paginate, project_label
from routes_ops import slim

bp = Blueprint("incidents", __name__)


def _user():
    user = rbac.current_user()
    if not config.FEATURES.get("ENABLE_INCIDENTS", True):
        raise ApiError("not_found", "Incident management is turned off.", 404)
    return user


def _tz():
    try:
        return timezone(timedelta(minutes=int(request.args.get("tzOffset", "0") or 0)))
    except ValueError:
        return timezone.utc


def _int_arg(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(request.args.get(name) or default)))
    except ValueError:
        raise ApiError("bad_request", f"{name} must be a number.", 400)


def _incident(user, iid):
    """The incident, or 404 when it does not exist or its project is outside the user's scope."""
    inc = incidents.get(iid)
    if not inc or not rbac.project_allowed(user, inc["projectId"]):
        raise ApiError("not_found", "Incident not available.", 404)
    return inc


def _owner_lookup():
    return {str(o["id"]): o["name"] for o in incidents.owners()}


def _can(user):
    p = user["permissions"]
    return {"manage": "alarm.investigate" in p, "comment": "alarm.investigate" in p or "alarm.comment" in p,
            "evidence": "evidence.view" in p}


def _suggest(user):
    """(suggestions, alarms by id, freshness, settings) for the current user and query."""
    gap = _int_arg("gapMinutes", incidents.DEFAULT_GAP_MINUTES, 1, 720)
    min_alerts = _int_arg("minAlerts", incidents.DEFAULT_MIN_ALERTS, 2, 50)
    items, freshness = routes_queue._all_alarms(user, request.args.get("projectId"))
    enabled = bool(config.FEATURES.get("ENABLE_ADVANCED_CORRELATION", True))
    found = incidents.suggestions(items, gap, min_alerts) if enabled else []
    return found, freshness, {"gapMinutes": gap, "minAlerts": min_alerts, "enabled": enabled}


def _public_suggestion(s, thumbs=4):
    run = s["_run"]
    out = {k: v for k, v in s.items() if k != "_run"}
    out["preview"] = [slim(a) for a in sorted(run, key=lambda a: a.get("priorityRank", 99))[:thumbs]]
    out["types"] = sorted({a.get("alarmTypeName") or "" for a in run} - {""})
    return out


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

@bp.route("/api/incidents")
@rbac.internal("alarm.view")
def incidents_list():
    user = _user()
    pids = datasource.allowed_projects(user)
    f = {k: request.args.get(k) for k in ("status", "owner", "search", "alarm")}
    if f.get("owner") == "me":
        f["owner"] = user["id"]
    items, counts = incidents.list_incidents(pids, f)
    page = paginate(items, request.args.get("page"), request.args.get("size") or 50)
    for it in page["items"]:
        it["project"] = project_label(it["projectId"])
    tz = _tz()
    page.update(counts=counts, resolvedToday=incidents.resolved_on(pids, datetime.now(tz).date(), tz),
                owners=incidents.owners(), can=_can(user), status=f.get("status") or "active")
    return jsonify(page)


@bp.route("/api/incidents/suggestions")
@rbac.internal("alarm.view")
def incidents_suggestions():
    user = _user()
    found, freshness, settings = _suggest(user)
    limit = _int_arg("limit", 30, 1, 200)
    if request.args.get("id"):                     # one suggestion with every alert ("View alerts")
        s = next((x for x in found if x["id"] == request.args["id"]), None)
        if not s:
            raise ApiError("not_found", "This suggestion is no longer current. Reload the suggestions.", 404)
        return jsonify({"suggestion": {**_public_suggestion(s), "alarmsFull": [
            slim(a) for a in sorted(s["_run"], key=lambda a: a.get("firstInstance") or "")]}, "can": _can(user)})
    return jsonify({"items": [_public_suggestion(s) for s in found[:limit]], "total": len(found),
                    "settings": settings, "freshness": freshness, "can": _can(user),
                    "explanation": "Suggestions group detection alerts you can see: the same centre with each alert "
                                   f"within {settings['gapMinutes']} minutes of the previous one (at least "
                                   f"{settings['minAlerts']}), or the same alert type repeated on one camera. Camera "
                                   "status events, INVALID alerts and alerts already in an incident are left out."})


@bp.route("/api/incidents/<int:iid>")
@rbac.internal("alarm.view")
def incident_detail(iid):
    user = _user()
    inc = _incident(user, iid)
    alarms, hidden = [], 0
    for link in incidents.alarm_links(iid):
        a = datasource.find_alarm(user, link["alarmId"], link["projectId"])
        if a:
            s = slim(a)
            s["source"] = a.get("source") or "live"
        else:
            snap = link["snapshot"]
            if not snap or not _snapshot_in_scope(user, snap):
                hidden += 1
                continue
            s = dict(snap)
            s["source"] = "snapshot"
            if "evidence.view" not in user["permissions"]:
                s["imageUrl"], s["hasVideo"] = None, False
        s["addedAt"], s["addedBy"] = link["addedAt"], link["addedBy"]
        alarms.append(s)
    alarms.sort(key=lambda a: a.get("firstInstance") or a.get("lastInstance") or "")
    return jsonify({"incident": {**inc, "project": project_label(inc["projectId"])}, "alarms": alarms,
                    "hiddenAlarms": hidden, "events": incidents.events(iid), "owners": incidents.owners(),
                    "can": _can(user), "statuses": list(incidents.STATUSES), "severities": list(incidents.SEVERITIES)})


def _snapshot_in_scope(user, snap):
    """Scope check on a stored slim alarm: its context path is turned back into levels."""
    if not rbac.project_allowed(user, snap.get("projectId")):
        return False
    levels = {n.get("level"): n for n in (snap.get("context") or {}).get("path", []) if n.get("level")}
    return rbac.alarm_in_scope(user, {**snap, "context": levels})


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------

def _refs(raw):
    """[{alarmId, projectId}] from a list of ids or objects."""
    out = []
    for x in raw or []:
        if isinstance(x, dict):
            out.append((str(x.get("alarmId") or ""), x.get("projectId")))
        else:
            out.append((str(x), None))
    return [(a, p) for a, p in out if a][:200]


def _resolve(user, refs):
    """Enriched alarms for refs; any alert the user may not see is refused the same way as a missing one."""
    alarms = []
    for aid, pid in refs:
        a = datasource.find_alarm(user, aid, pid)
        if not a:
            raise ApiError("not_found", f"Alert {aid} is not available.", 404)
        if a.get("eventKind") == "camera_status":
            raise ApiError("bad_request", f"{aid} is a camera status event, not an alert — it cannot join an incident.", 400)
        alarms.append(a)
    return alarms


@bp.route("/api/incidents", methods=["POST"])
@rbac.internal("alarm.investigate")
def incidents_create():
    user = _user()
    b = body()
    reason, centre = (b.get("reason") or "").strip() or None, None
    if b.get("suggestionId"):
        found, _fresh, _settings = _suggest(user)
        s = next((x for x in found if x["id"] == b["suggestionId"]), None)
        if not s:
            raise ApiError("not_found", "This suggestion is no longer current (its alerts changed or joined an "
                                        "incident). Reload the suggestions.", 404)
        alarms, reason, centre = s["_run"], s["reason"], s["centre"]
        title = (b.get("title") or "").strip() or s["title"]
    else:
        refs = _refs(b.get("alarms") or b.get("alarmIds"))
        if not refs:
            raise ApiError("bad_request", "Choose at least one alert (alarmIds) or a suggestion (suggestionId).", 400)
        alarms = _resolve(user, refs)
        title = b.get("title")
    inc = incidents.create(user, alarms, title, reason, b.get("severity"), centre, slim=slim)
    if b.get("ownerId"):
        inc = incidents.update(inc["id"], user, {"ownerId": b["ownerId"]}, _owner_lookup())
    return jsonify({"incident": inc}), 201


@bp.route("/api/incidents/<int:iid>", methods=["PUT"])
@rbac.internal("alarm.investigate")
def incidents_update(iid):
    user = _user()
    _incident(user, iid)
    b = body()
    allowed = {k: b[k] for k in ("status", "ownerId", "severity", "title", "resolution") if k in b}
    return jsonify({"incident": incidents.update(iid, user, allowed, _owner_lookup()), "events": incidents.events(iid)})


@bp.route("/api/incidents/<int:iid>/comments", methods=["POST"])
@rbac.internal("alarm.comment", "alarm.investigate", any_of=True)
def incidents_comment(iid):
    user = _user()
    _incident(user, iid)
    return jsonify({"events": incidents.comment(iid, user, body().get("body") or body().get("text"))}), 201


@bp.route("/api/incidents/<int:iid>/alarms", methods=["POST"])
@rbac.internal("alarm.investigate")
def incidents_alarms(iid):
    user = _user()
    inc = _incident(user, iid)
    b = body()
    add = _resolve(user, [(a, p or inc["projectId"]) for a, p in _refs(b.get("add"))])
    wrong = [a["alarmId"] for a in add if str(a.get("projectId")) != str(inc["projectId"])]
    if wrong:
        raise ApiError("bad_request", f"Only alerts of the incident's project can be added ({', '.join(wrong)}).", 400)
    links = {x["alarmId"]: x for x in incidents.alarm_links(iid)}
    # only alerts the user can see may be removed (a hidden alert is never confirmed to exist)
    remove = [a for a, _ in _refs(b.get("remove")) if a in links and (
        datasource.find_alarm(user, a, links[a]["projectId"]) or _snapshot_in_scope(user, links[a]["snapshot"] or {}))]
    result = incidents.change_alarms(iid, user, add, remove, slim=slim)
    return jsonify({**result, "incident": incidents.get(iid)})
