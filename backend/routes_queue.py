"""
routes_queue.py — the automated alert-review product surface.

  GET  /api/queue                 one queue across every exam/project the user may see
  GET  /api/queue/summary         "what needs my decision right now?"
  GET  /api/queue/<id>            everything needed to review one alert (evidence first)
  POST /api/queue/<id>/decide     VALID | INVALID | EXCEPTION  (the only human action)
  GET  /api/tickets               tickets created automatically from VALID decisions
  POST /api/tickets/<id>/send     controlled delivery: one click
  POST /api/tickets/<id>/withdraw
  GET|POST|PUT /api/exams         exam ↔ client ↔ project mapping (administration)
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import datasource
import db
import exams
import health as health_mod
import media
import rbac
import tickets
import workflow
from camview_client import ApiError
from routes_common import body, paginate, project_label
from routes_ops import slim

bp = Blueprint("queue", __name__)

PRIORITY_SORT_RULE = ("Sorted automatically: priority first (critical → low), then most recent, then most "
                      "repeated. New alerts are added in place; your current review is never moved.")

# Two kinds of records arrive from Camview: detections to decide (image + video attached) and camera
# status events (camera online / offline, no media). The queue shows one kind at a time so the status
# events never bury the alerts that have evidence.
KINDS = {"alert": "alert", "alerts": "alert", "camera": "camera_status", "camera_status": "camera_status", "all": "all"}


def _user():
    return rbac.current_user()


def _tz():
    try:
        return timezone(timedelta(minutes=int(request.args.get("tzOffset", "0") or 0)))
    except ValueError:
        return timezone.utc


def _all_alarms(user, project=None):
    """Merged, scope-filtered working sets of every project the user may see."""
    pids = [str(project)] if project else datasource.allowed_projects(user)
    items, freshness = [], {}
    for pid in pids:
        if not rbac.project_allowed(user, pid):
            continue
        got, feed = datasource.working_set(user, pid)
        items.extend(got)
        freshness[pid] = datasource.freshness(feed)
    return items, freshness


def _smart_key(a):
    return (a.get("priorityRank", 99), -(datetime.fromisoformat((a.get("lastInstance") or "1970-01-01T00:00:00Z")
                                                                  .replace("Z", "+00:00")).timestamp()),
            -(a.get("totalTimesReported") or 1))


def _filter(items, f):
    out = []
    s = (f.get("search") or "").lower().strip()
    kind = KINDS.get(f.get("kind") or "alert", "alert")
    for a in items:
        if kind != "all" and (a.get("eventKind") or "alert") != kind:
            continue
        if f.get("status") and f["status"] != "all" and a["decision"] != f["status"]:
            continue
        if f.get("by") == "team" and a.get("decisionSource") != "operator":
            continue                                  # decided by the backend operations team
        if f.get("by") == "camview" and (a.get("decisionSource") == "operator" or a["decision"] == "pending"):
            continue                                  # status only from Camview, no operator decision yet
        if f.get("client") and not any(c["id"] == f["client"] for c in a["clients"]):
            continue
        if f.get("exam") and (a.get("exam") or {}).get("id") != f["exam"]:
            continue
        if f.get("priority") and a.get("priority") != f["priority"]:
            continue
        if f.get("type") not in (None, "") and str(a.get("alarmType")) != str(f["type"]):
            continue
        ctx = a.get("context") or {}
        if f.get("centre") and (ctx.get("centre") or {}).get("code") != f["centre"]:
            continue
        if f.get("camera") and a.get("cameraCode") != f["camera"] and str(a.get("cameraId")) != f["camera"]:
            continue
        if f.get("city") and (a.get("cameraCity") or "").strip().lower() != f["city"].strip().lower():
            continue
        if f.get("from") and (a.get("lastInstance") or "") < f["from"]:
            continue
        if f.get("to") and (a.get("firstInstance") or "") > f["to"]:
            continue
        if s:
            hay = [a["alarmId"], a.get("alarmTypeName") or "", a.get("cameraCode") or "", (a.get("exam") or {}).get("name", ""),
                   " ".join(c["name"] for c in a["clients"])] + [n["code"] for n in ctx.get("path", [])]
            if not any(s in str(x).lower() for x in hay):
                continue
        out.append(a)
    return out


def _counts(items):
    c = {"all": len(items), "pending": 0, "valid": 0, "invalid": 0, "exception": 0}
    for a in items:
        c[a["decision"]] = c.get(a["decision"], 0) + 1
    return c


@bp.route("/api/queue")
@rbac.internal("live.view", "alarm.view", any_of=True)
def queue():
    user = _user()
    items, freshness = _all_alarms(user, request.args.get("projectId"))
    f = {k: request.args.get(k) for k in ("status", "client", "exam", "priority", "centre", "camera", "search", "from", "to",
                                          "kind", "type", "city", "by")}
    f["status"] = f["status"] or "pending"
    f["kind"] = KINDS.get(f["kind"] or "alert", "alert")
    both = _filter(items, {**f, "status": "all", "kind": "all", "by": None})
    base = [a for a in both if f["kind"] == "all" or (a.get("eventKind") or "alert") == f["kind"]]
    if f.get("by"):                                   # "decided by" narrows the tab counts too
        base = _filter(base, {"status": "all", "kind": "all", "by": f["by"]})
    filtered = _filter(base, {"status": f["status"], "kind": "all"})
    filtered.sort(key=_smart_key)
    page = paginate(filtered, request.args.get("page"), request.args.get("size") or 50)
    page["items"] = [slim(a) for a in page["items"]]          # slim carries imageUrl / hasVideo for evidence.view
    page["counts"] = _counts(base)
    page["kind"] = f["kind"]
    page["kinds"] = {"alert": sum(1 for a in both if (a.get("eventKind") or "alert") == "alert"),
                     "camera_status": sum(1 for a in both if a.get("eventKind") == "camera_status")}
    page["sortRule"] = PRIORITY_SORT_RULE
    page["facets"] = {
        "clients": sorted({(c["id"], c["name"]) for a in items for c in a["clients"]}, key=lambda x: x[1]),
        "exams": sorted({(a["exam"]["id"], a["exam"]["name"]) for a in items if a.get("exam")}, key=lambda x: x[1]),
        "centres": sorted({a["context"]["centre"]["code"] for a in items if (a.get("context") or {}).get("centre")}),
        "cameras": sorted({a.get("cameraCode") for a in items if a.get("cameraCode")})[:500],
        "types": sorted({(str(a.get("alarmType")), a.get("alarmTypeName") or "") for a in items if a.get("alarmType") is not None},
                        key=lambda x: x[1]),
        "projects": [[p["externalId"], p["code"] + (f" · {p['name']}" if p.get("name") and p["name"] != p["code"] else "")]
                     for p in (project_label(x) for x in (datasource.allowed_projects(user)))],
    }
    page["manualReview"] = bool(workflow.policy().get("manualReview"))
    page["freshness"] = freshness
    return jsonify(page)


@bp.route("/api/queue/summary")
@rbac.internal("live.view", "alarm.view", any_of=True)
def queue_summary():
    user = _user()
    items, freshness = _all_alarms(user, request.args.get("projectId"))
    cam_events = [a for a in items if a.get("eventKind") == "camera_status"]
    items = [a for a in items if a.get("eventKind") != "camera_status"]        # KPIs count alerts, not status events
    tz = _tz()
    today = datetime.now(tz).date().isoformat()
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")

    def local_day(iso):
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz).date().isoformat()
        except (AttributeError, ValueError):
            return None

    def decided_today(a, state):
        if a["decision"] != state:
            return False
        when = (a.get("review") or {}).get("at") if a["decisionSource"] == "operator" else a.get("lastInstance")
        return local_day(when) == today

    pending = sorted([a for a in items if a["decision"] == "pending"], key=_smart_key)
    pids = datasource.allowed_projects(user)
    ph = ",".join("?" for _ in pids) or "''"
    delivered = db.one(f"SELECT COUNT(*) AS n FROM tickets WHERE delivery_status='delivered' AND status='open' "
                       f"AND project_id IN ({ph})", pids)["n"] if pids else 0
    ready = db.one(f"SELECT COUNT(*) AS n FROM tickets WHERE delivery_status IN ('ready','needs_client') AND status='open' "
                   f"AND project_id IN ({ph})", pids)["n"] if pids else 0
    exams_seen = {}
    for a in items:
        if a.get("exam"):
            e = exams_seen.setdefault(a["exam"]["id"], {**a["exam"], "clients": a["clients"], "pending": 0, "total": 0})
            e["total"] += 1
            e["pending"] += a["decision"] == "pending"
    counts = _counts(items)
    no_client = {}
    for a in items:
        if a["decision"] == "valid" and not a["clients"]:
            p = str(a.get("projectId"))
            no_client[p] = no_client.get(p, 0) + 1
    latest = max((a.get("lastInstance") or "" for a in items), default=None) or None
    pol = workflow.policy()
    # how many alerts of each type (whole window and today), every verdict separately
    by_type = {}
    for a in items:
        t = by_type.setdefault(a.get("alarmTypeName") or "Unknown", {"type": a.get("alarmTypeName") or "Unknown",
                                                                     "alarmType": a.get("alarmType"), "total": 0, "today": 0,
                                                                     "pending": 0, "valid": 0, "invalid": 0, "exception": 0})
        t["total"] += 1
        t[a["decision"]] = t.get(a["decision"], 0) + 1
        if local_day(a.get("lastInstance")) == today:
            t["today"] += 1
    by_type = sorted(by_type.values(), key=lambda t: (-t["today"], -t["total"], t["type"]))
    # alerts per hour, per verdict, for today — or for the latest day with alerts when today has none yet
    chart_day = today if any(local_day(a.get("lastInstance")) == today for a in items) else (local_day(latest) if latest else today)
    hourly = [{"hour": h, "pending": 0, "valid": 0, "invalid": 0, "exception": 0} for h in range(24)]
    for a in items:
        if local_day(a.get("lastInstance")) != chart_day:
            continue
        try:
            h = datetime.fromisoformat(a["lastInstance"].replace("Z", "+00:00")).astimezone(tz).hour
        except (AttributeError, ValueError, TypeError):
            continue
        hourly[h][a["decision"]] = hourly[h].get(a["decision"], 0) + 1
    # camera connection (real health source, keyed per camera — one camera raises many alerts)
    cams = {}
    for a in items + cam_events:
        h = a.get("health") or {}
        if h.get("available"):
            cams[(str(a.get("projectId")), str(a.get("cameraId")))] = h
    cameras = {"reporting": len(cams),
               "offline": sum(1 for h in cams.values() if h["camera"]["state"] == "offline"),
               "online": sum(1 for h in cams.values() if h["camera"]["state"] == "online"),
               "syncFailed": sum(1 for h in cams.values() if "FRAME_SYNC_FAILED" in h["conditions"]),
               "source": health_mod.source_mode(),
               "events": len(cam_events), "eventsPending": sum(1 for a in cam_events if a["decision"] == "pending"),
               "latestEventAt": max((a.get("lastInstance") or "" for a in cam_events), default=None) or None}
    return jsonify({
        "cameras": cameras,
        "totals": {**counts, "today": sum(1 for a in items if local_day(a.get("lastInstance")) == today),
                   "todayByDecision": {d: sum(1 for a in items if a["decision"] == d and local_day(a.get("lastInstance")) == today)
                                       for d in ("pending", "valid", "invalid", "exception")},
                   "latestAlertAt": latest},
        "byType": by_type,
        "hourly": hourly, "hourlyDay": chart_day,
        "unroutedValid": [{"projectId": p, "count": n, "project": project_label(p)} for p, n in sorted(no_client.items())],
        "projects": [{**project_label(p), "total": sum(1 for a in items if str(a.get("projectId")) == p),
                      "valid": sum(1 for a in items if str(a.get("projectId")) == p and a["decision"] == "valid"),
                      "freshness": f} for p, f in freshness.items()],
        "manualReview": bool(pol.get("manualReview")), "autoShareValid": bool(pol.get("autoShareValid")),
        "deliveryTrigger": pol.get("deliveryTrigger", "valid"),
        "kpis": {
            "newAlerts": sum(1 for a in items if (a.get("firstInstance") or "") >= hour_ago),
            "pending": len(pending),
            "validToday": sum(1 for a in items if decided_today(a, "valid")),
            "invalidToday": sum(1 for a in items if decided_today(a, "invalid")),
            "exceptions": sum(1 for a in items if a["decision"] == "exception" and a["decisionSource"] == "operator"),
            "clientAlerts": delivered, "readyToSend": ready,
        },
        "priorityAlerts": [slim(a) for a in pending[:12]],
        "exams": sorted(exams_seen.values(), key=lambda e: (-e["pending"], e["name"])),
        "deliveryMode": workflow.policy().get("deliveryMode", "controlled"),
        "freshness": freshness, "sortRule": PRIORITY_SORT_RULE,
    })


@bp.route("/api/queue/<alarm_id>")
@rbac.internal("alarm.view")
def review_payload(alarm_id):
    user = _user()
    a = datasource.find_alarm(user, alarm_id, request.args.get("projectId"))
    if not a:
        raise ApiError("not_found", "Alert not available.", 404)
    ev = _evidence(user, a)
    p = user["permissions"]
    return jsonify({
        "alarm": a, "evidence": ev, "ticket": tickets.for_alarm(alarm_id),
        # what the alert itself detected (labels + boxes from Camview's metadata file), when there is a frame
        "detections": media.detections(a.get("metadataUrl")) if ev and a.get("metadataUrl") else None,
        # no media on this record (camera status events never have any): the latest frame from the same camera
        "cameraEvidence": _camera_evidence(user, a) if not ev else None,
        "deliveryMode": workflow.policy().get("deliveryMode", "controlled"),
        "deliveryTrigger": workflow.policy().get("deliveryTrigger", "valid"),
        "requireRemarks": bool(workflow.policy().get("requireRemarks")),
        "manualReview": bool(workflow.policy().get("manualReview")),
        "autoShareValid": bool(workflow.policy().get("autoShareValid")),
        "can": {"valid": "alarm.validate" in p, "invalid": "alarm.invalidate" in p, "exception": "alarm.exception" in p,
                "send": "alarm.publish" in p, "withdraw": "alarm.withdraw" in p,
                "download": "evidence.download" in p, "details": "alarm.investigate" in p},
        "publications": [{"clientName": x["clientName"], "status": x["status"], "sharedAt": x["sharedAt"],
                          "viewedAt": x["viewedAt"], "acknowledgedAt": x["acknowledgedAt"]} for x in a["publications"]],
    })


@bp.route("/api/queue/<alarm_id>/decide", methods=["POST"])
@rbac.internal("alarm.view")
def decide(alarm_id):
    user = _user()
    b = body()
    a = datasource.find_alarm(user, alarm_id, b.get("projectId"))
    if not a:
        raise ApiError("not_found", "Alert not available.", 404)
    result = tickets.decide(a, user, str(b.get("result") or ""), b.get("note"), b.get("clientId"))
    return jsonify(result)


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

@bp.route("/api/tickets")
@rbac.internal("alarm.view")
def tickets_list():
    user = _user()
    f = {k: request.args.get(k) for k in ("status", "delivery", "client", "exam", "search")}
    items, counts = tickets.list_tickets(user, f, datasource.allowed_projects(user))
    page = paginate(items, request.args.get("page"), request.args.get("size") or 50)
    page["freshness"] = _attach_live(user, page["items"])
    page.update(counts=counts, deliveryMode=workflow.policy().get("deliveryMode", "controlled"),
                deliveryTrigger=workflow.policy().get("deliveryTrigger", "valid"),
                clients=db.rows("SELECT id, name FROM clients ORDER BY name"),
                exams=[{"id": e["id"], "name": e["name"]} for e in exams.list_exams()],
                canSend="alarm.publish" in user["permissions"], canWithdraw="alarm.withdraw" in user["permissions"])
    return jsonify(page)


def _evidence(user, a):
    if "evidence.view" not in user["permissions"]:
        return []
    ev = [{"kind": "video", "index": 0, "url": a["videoUrl"]}] if a.get("videoUrl") else []
    return ev + [{"kind": "image", "index": i, "url": u} for i, u in enumerate(a.get("imageUrls") or [])]


def _camera_evidence(user, a):
    """The most recent image / video Camview attached to ANY alert of the same camera (same project,
    so within the viewer's scope). Labelled as such by the UI — it is not evidence of this record."""
    pid, cid = str(a.get("projectId") or ""), str(a.get("cameraId") or "")
    if "evidence.view" not in user["permissions"] or not pid or not cid:
        return None
    feed = datasource.refresh(pid)
    cands = [x for x in feed.items if str(x.get("cameraId")) == cid and x["alarmId"] != a["alarmId"]
             and (x.get("imageUrls") or x.get("videoUrl"))]
    if not cands:
        return None
    x = max(cands, key=lambda x: x.get("lastInstance") or "")
    return {"alarmId": x["alarmId"], "projectId": x.get("projectId"), "alarmTypeName": x.get("alarmTypeName"),
            "at": x.get("lastInstance"), "evidence": _evidence(user, x)}


def _attach_live(user, items):
    """Adds `live` to each ticket: the alert as Camview reports it NOW (status, repeats,
    fresh photo/video links). Falls back to the ticket snapshot when the alert is no
    longer in Camview's list. Returns the freshness of every feed used."""
    by_project, freshness = {}, {}
    for t in items:
        by_project.setdefault(str(t["projectId"]), []).append(t["alarmId"])
    live = {}
    for pid, ids in by_project.items():
        if not rbac.project_allowed(user, pid):
            continue
        found, feed = datasource.live_alarms(pid, ids)
        live.update(found)
        freshness[pid] = datasource.freshness(feed)
    for t in items:
        a = live.get(t["alarmId"])
        src = a or t["snapshot"]
        ev = _evidence(user, src)
        t["live"] = {
            "source": "live" if a else "snapshot",
            "fetchedAt": (freshness.get(str(t["projectId"])) or {}).get("lastSuccessAt") if a else None,
            **{k: src.get(k) for k in ("alarmTypeName", "priority", "lastActionType", "lastActionLabel", "lastInstance",
                                       "totalTimesReported", "cameraName", "centreName", "centreCode")},
            "imageUrl": next((e["url"] for e in ev if e["kind"] == "image"), None),
            "hasVideo": any(e["kind"] == "video" for e in ev),
            "evidenceCount": len(ev),
        }
    return freshness


@bp.route("/api/tickets/<int:tid>")
@rbac.internal("alarm.view")
def ticket_detail(tid):
    """One ticket with its live alert and playable evidence (video first, then photos)."""
    user = _user()
    t = tickets.get(tid)
    if not t or not rbac.project_allowed(user, t["projectId"]):
        raise ApiError("not_found", "Ticket not found.", 404)
    a = datasource.find_alarm(user, t["alarmId"], t["projectId"])
    if not a:
        raise ApiError("not_found", "The alert for this ticket is not available to you.", 404)
    p = user["permissions"]
    return jsonify({"ticket": t, "alarm": a, "evidence": _evidence(user, a),
                    "freshness": datasource.freshness(datasource.refresh(t["projectId"])),
                    "can": {"send": "alarm.publish" in p, "withdraw": "alarm.withdraw" in p,
                            "download": "evidence.download" in p}})


def _ticket_alarm(user, t):
    a = datasource.find_alarm(user, t["alarmId"], t["projectId"])
    if not a:
        raise ApiError("not_found", "The alert for this ticket is not available to you.", 404)
    return a


@bp.route("/api/tickets/<int:tid>/send", methods=["POST"])
@rbac.internal("alarm.publish")
def ticket_send(tid):
    user = _user()
    t = tickets.get(tid)
    if not t or not rbac.project_allowed(user, t["projectId"]):
        raise ApiError("not_found", "Ticket not found.", 404)
    return jsonify({"ticket": tickets.send(tid, user, _ticket_alarm(user, t), body().get("clientId"))})


@bp.route("/api/tickets/<int:tid>/withdraw", methods=["POST"])
@rbac.internal("alarm.withdraw")
def ticket_withdraw(tid):
    user = _user()
    t = tickets.get(tid)
    if not t or not rbac.project_allowed(user, t["projectId"]):
        raise ApiError("not_found", "Ticket not found.", 404)
    ticket, n = tickets.withdraw(tid, user, body().get("reason"))
    return jsonify({"ticket": ticket, "withdrawn": n})


# ---------------------------------------------------------------------------
# Exams (mapping managed in Administration)
# ---------------------------------------------------------------------------

@bp.route("/api/exams")
@rbac.internal("alarm.view", "client.view", any_of=True)
def exams_list():
    user = _user()
    allowed = set(datasource.allowed_projects(user))
    items = [e for e in exams.list_exams() if set(e["projectIds"]) & allowed or "client.manage" in user["permissions"]]
    counts = {}
    for e in items:
        pid_set = [p for p in e["projectIds"] if p in allowed]
        ph = ",".join("?" for _ in pid_set) or "''"
        counts[e["id"]] = db.one(f"SELECT COUNT(*) AS n FROM tickets WHERE exam_id=? AND status='open' "
                                 f"AND project_id IN ({ph})", [e["id"], *pid_set])["n"]
        e["tickets"] = counts[e["id"]]
        e["projects"] = [project_label(p) for p in e["projectIds"]]
        # client logins that can see this exam: all logins of the exam's client, unless limited to other exams
        e["logins"] = [] if not e.get("clientId") else [
            {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role_name"], "status": u["status"],
             "lastLoginAt": u["last_login_at"],
             "exams": [x["scope_value"] for x in db.rows("SELECT scope_value FROM user_scopes WHERE user_id=? AND scope_type='exam'", (u["id"],))]}
            for u in db.rows("SELECT u.id, u.name, u.email, u.status, u.last_login_at, r.name AS role_name FROM users u "
                             "JOIN roles r ON r.id=u.role_id WHERE u.client_id=? ORDER BY u.name", (e["clientId"],))]
        e["logins"] = [u for u in e["logins"] if not u["exams"] or e["id"] in u["exams"]]
        # what the project code says (Camview sends only the number; the code is entered once)
        code = next((p["code"] for p in e["projects"] if p.get("code") and p["code"] != p["externalId"]), None)
        parsed = exams.parse_project_code(code) if code else None
        e["fromCode"] = {"projectCode": code, "client": parsed["client"], "exam": f"{parsed['client']}/{parsed['examBase']}",
                         "examWithYear": parsed["exam"], "date": parsed["date"]} if parsed else None
    return jsonify({"items": items, "clients": db.rows("SELECT id, name, status FROM clients ORDER BY name"),
                    "projects": [project_label(p) for p in sorted(allowed)],
                    "canManage": "client.manage" in user["permissions"],
                    "canManageLogins": "user.manage" in user["permissions"],
                    "clientRoles": db.rows("SELECT id, name, description FROM roles WHERE audience='client' ORDER BY name")})


@bp.route("/api/exams/<exam_id>/names-from-code", methods=["POST"])
@rbac.internal("client.manage")
def exams_names_from_code(exam_id):
    """One click: exam name/code and client name taken from the project code (MPESB/G2SG4-CRT-2026/220926/…
    → client MPESB, exam MPESB/G2SG4-CRT, start date 22 Sep 2026). Nothing is typed by hand."""
    user = _user()
    e = exams.get(exam_id)
    if not e:
        raise ApiError("not_found", "Exam not found.", 404)
    code = next((project_label(p)["code"] for p in e["projectIds"] if project_label(p)["code"] != str(p)), None)
    parsed = exams.parse_project_code(code) if code else None
    if not parsed:
        raise ApiError("bad_request", "Set the project's code first (e.g. MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL).", 400)
    name = f"{parsed['client']}/{parsed['examBase']}"
    clash = db.one("SELECT id FROM exams WHERE UPPER(code)=? AND id<>?", (name.upper(), exam_id))
    body_ = {"name": name, "code": parsed["exam"] if clash else name, "startDate": e.get("startDate") or parsed["date"]}
    out = exams.update(exam_id, body_, user)
    if e.get("clientId") and body().get("renameClient", True):
        old = db.one("SELECT name FROM clients WHERE id=?", (e["clientId"],))
        if old and old["name"] != parsed["client"] and not db.one("SELECT 1 FROM clients WHERE UPPER(name)=? AND id<>?",
                                                                   (parsed["client"], e["clientId"])):
            db.execute("UPDATE clients SET name=? WHERE id=?", (parsed["client"], e["clientId"]))
            db.audit("client.rename", user, "client", e["clientId"], old["name"], parsed["client"], client_id=e["clientId"],
                     note="Named from the project code")
    return jsonify({"exam": exams.get(exam_id), "client": parsed["client"], "updated": out is not None})


@bp.route("/api/exams", methods=["POST"])
@rbac.internal("client.manage")
def exams_create():
    return jsonify(exams.create(body(), _user()))


@bp.route("/api/exams/<exam_id>", methods=["PUT"])
@rbac.internal("client.manage")
def exams_update(exam_id):
    return jsonify(exams.update(exam_id, body(), _user()))
