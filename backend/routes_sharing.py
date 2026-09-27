"""
routes_sharing.py — Client Sharing Center (internal).

Nothing is ever published implicitly. Every state change goes through the
eligibility engine (workflow.client_share_eligible) on the server, requires
explicit confirmation for publish/withdraw, and is audited.
"""

from flask import Blueprint, jsonify, request

import datasource
import db
import rbac
import workflow
from camview_client import ApiError
from routes_common import body, paginate, project_param
from routes_ops import slim

bp = Blueprint("sharing", __name__)

TABS = ("candidates", "ready_for_review", "approved", "shared", "withdrawn")


def _alarm(user, alarm_id, project_id=None):
    a = datasource.find_alarm(user, alarm_id, project_id)
    if not a:
        raise ApiError("not_found", "Alarm not available.", 404)
    return a


def _client_or_404(client_id):
    c = db.one("SELECT id, name, status FROM clients WHERE id=?", (client_id,))
    if not c:
        raise ApiError("bad_request", "Unknown client.", 400)
    return c


@bp.route("/api/sharing")
@rbac.internal("alarm.approve", "alarm.publish", "alarm.validate", "client.view", any_of=True)
def sharing_queue():
    user = rbac.current_user()
    pid = project_param(user)
    items, _ = datasource.working_set(user, pid)
    tab = request.args.get("tab", "candidates")
    if tab not in TABS:
        raise ApiError("bad_request", "Unknown tab.", 400)
    pol = workflow.policy()
    rows = []
    for a in items:
        pubs = a["publications"]
        if tab == "candidates":
            # valid and not yet in any client's pipeline (or withdrawn everywhere)
            if workflow.is_valid(a, pol) and all(p["status"] in ("withdrawn", "archived") for p in pubs):
                rows.append((a, None))
        else:
            for p in pubs:
                if p["status"] == tab:
                    rows.append((a, p))
    # also include publications whose alarm dropped out of the working window (from snapshot)
    if tab != "candidates":
        seen = {(a["alarmId"], (p or {}).get("clientId")) for a, p in rows}
        for r in db.rows("SELECT p.*, c.name AS client_name FROM publications p JOIN clients c ON c.id=p.client_id "
                         "WHERE p.status=? AND p.project_id=?", (tab, pid)):
            if (r["alarm_id"], r["client_id"]) in seen:
                continue
            a = datasource.find_alarm(user, r["alarm_id"], pid)
            if a:
                rows.append((a, workflow.pub_public(dict(r))))
    f = request.args
    out = []
    for a, p in rows:
        ctx = a.get("context") or {}
        if f.get("clientId") and p and p["clientId"] != f["clientId"]:
            continue
        if any(f.get(lvl) and (ctx.get(lvl) or {}).get("code") != f[lvl] for lvl in ("tc", "centre")):
            continue
        if f.get("priority") and a.get("priority") != f["priority"]:
            continue
        s = (f.get("search") or "").lower()
        if s and s not in a["alarmId"].lower() and s not in (a.get("alarmTypeName") or "").lower() \
                and s not in (a.get("cameraCode") or "").lower():
            continue
        if f.get("from") and (a.get("firstInstance") or "") < f["from"]:
            continue
        if f.get("to") and (a.get("firstInstance") or "") > f["to"]:
            continue
        out.append({"alarm": slim(a), "publication": p})
    key = {"candidates": lambda r: r["alarm"].get("lastInstance") or "",
           "ready_for_review": lambda r: r["publication"]["requestedAt"] or "",
           "approved": lambda r: r["publication"]["approvedAt"] or "",
           "shared": lambda r: r["publication"]["sharedAt"] or "",
           "withdrawn": lambda r: r["publication"]["withdrawnAt"] or ""}[tab]
    out.sort(key=key, reverse=True)
    counts = {}
    for t in TABS:
        if t == "candidates":
            counts[t] = sum(1 for a in items if workflow.is_valid(a, pol)
                            and all(p["status"] in ("withdrawn", "archived") for p in a["publications"]))
        else:
            counts[t] = db.one("SELECT COUNT(*) AS n FROM publications WHERE status=? AND project_id=?", (t, pid))["n"]
    clients = db.rows("SELECT c.id, c.name, c.status FROM clients c JOIN client_projects cp ON cp.client_id=c.id "
                      "WHERE cp.project_id=? ORDER BY c.name", (pid,))
    page = paginate(out, request.args.get("page"), request.args.get("size") or 50)
    page.update(counts=counts, clients=clients, policy={k: pol[k] for k in ("requireApproval", "fourEyes", "validSource",
                                                                               "requireEvidence", "requireContext")})
    return jsonify(page)


@bp.route("/api/sharing/eligibility", methods=["POST"])
@rbac.internal("alarm.approve", "alarm.publish", "alarm.validate", any_of=True)
def eligibility():
    """Bulk eligibility (spec: selected 12 / eligible 10 / not eligible 2 + reasons)."""
    user = rbac.current_user()
    b = body()
    client = _client_or_404(b.get("clientId"))
    action = b.get("action", "request")
    if action not in ("request", "approve", "publish"):
        raise ApiError("bad_request", "Unknown action.", 400)
    results = []
    for aid in (b.get("alarmIds") or [])[:200]:
        a = datasource.find_alarm(user, str(aid), b.get("projectId"))
        if not a:
            results.append({"alarmId": aid, "eligible": False,
                            "checks": [{"id": "available", "ok": False, "text": "Alarm is available to you"}]})
            continue
        r = workflow.client_share_eligible(a, user, client, action)
        results.append({"alarmId": aid, "eligible": r["eligible"], "checks": r["checks"],
                        "lastActionLabel": a.get("lastActionLabel"), "review": a["review"]["status"],
                        "alarmTypeName": a.get("alarmTypeName"), "priority": a.get("priority")})
    return jsonify({"selected": len(results), "eligible": sum(1 for r in results if r["eligible"]),
                    "notEligible": sum(1 for r in results if not r["eligible"]),
                    "valid": sum(1 for r in results if any(c["id"] == "valid" and c["ok"] for c in r["checks"])),
                    "results": results, "client": client, "action": action})


@bp.route("/api/sharing/preview")
@rbac.internal("alarm.publish", "alarm.approve", any_of=True)
def preview():
    """Exactly what the client will see, plus what stays internal."""
    user = rbac.current_user()
    a = _alarm(user, request.args.get("alarmId", ""), request.args.get("projectId"))
    client = _client_or_404(request.args.get("clientId"))
    pol = workflow.policy()
    levels = [x for x in (request.args.get("context") or ",".join(pol["clientContextLevels"])).split(",") if x]
    sel = [x for x in (request.args.get("evidence") or "").split(",") if x]
    if not request.args.get("evidence") and request.args.get("evidence") is None:
        sel = [f"image:{i}" for i in range(len(a.get("imageUrls") or []))] + (["video:0"] if a.get("videoUrl") else [])
    ctx = a.get("context") or {}
    clients_users = db.rows("SELECT u.name, u.email, r.name AS role FROM users u JOIN roles r ON r.id=u.role_id "
                            "WHERE u.client_id=? AND u.status='active' ORDER BY u.name", (client["id"],))
    pub = next((p for p in a["publications"] if p["clientId"] == client["id"]), None)
    return jsonify({
        "alarm": {"alarmId": a["alarmId"], "alarmTypeName": a.get("alarmTypeName"), "priority": a.get("priority"),
                  "firstInstance": a.get("firstInstance"), "lastInstance": a.get("lastInstance"),
                  "totalTimesReported": a.get("totalTimesReported"), "shiftLabel": a.get("shiftLabel"),
                  "status": "Validated", "internalState": a.get("lastActionLabel"), "review": a["review"]["status"],
                  "ticketId": a.get("ticketId")},
        "context": [{"level": n["level"], "code": n["code"], "name": n.get("name"), "shared": n["level"] in levels}
                    for n in ctx.get("path", [])],
        "evidence": ([{"key": f"image:{i}", "kind": "image", "url": u, "shared": f"image:{i}" in sel}
                      for i, u in enumerate(a.get("imageUrls") or [])]
                     + ([{"key": "video:0", "kind": "video", "url": a["videoUrl"], "shared": "video:0" in sel}]
                        if a.get("videoUrl") else [])),
        "summary": (pub or {}).get("clientSummary") or workflow.client_safe_summary(a, levels),
        "summarySource": "existing" if (pub or {}).get("clientSummary") else "template",
        "internalNotShared": workflow.internal_not_shared(a, levels, sel),
        "client": client, "recipients": clients_users,
        "publishingScope": f"Visible to {len(clients_users)} active user(s) of {client['name']} with access to "
                           f"project {a.get('projectId')}.",
        "publication": pub,
        "eligibility": {act: workflow.client_share_eligible(a, user, client, act, pol)
                        for act in ("request", "approve", "publish")},
        "policy": {k: pol[k] for k in ("requireApproval", "fourEyes", "clientContextLevels", "clientShowTicket")},
    })


def _transition(fn_name):
    user = rbac.current_user()
    b = body()
    a = _alarm(user, str(b.get("alarmId") or ""), b.get("projectId"))
    if fn_name == "request":
        pub = workflow.request_share(a, user, b.get("clientId"))
    elif fn_name == "approve":
        pub = workflow.approve(a, user, b.get("clientId"))
    else:
        if b.get("confirm") is not True:
            raise ApiError("confirmation_required", "Publishing requires explicit confirmation.", 400)
        pub = workflow.publish(a, user, b.get("clientId"), b.get("clientSummary"), b.get("evidence"), b.get("context"))
    return jsonify({"publication": pub})


@bp.route("/api/sharing/request", methods=["POST"])
@rbac.internal("alarm.validate", "alarm.publish", "alarm.approve", any_of=True)
def share_request():
    return _transition("request")


@bp.route("/api/sharing/approve", methods=["POST"])
@rbac.internal("alarm.approve")
def share_approve():
    return _transition("approve")


@bp.route("/api/sharing/publish", methods=["POST"])
@rbac.internal("alarm.publish")
def share_publish():
    return _transition("publish")


@bp.route("/api/sharing/withdraw", methods=["POST"])
@rbac.internal("alarm.withdraw")
def share_withdraw():
    user = rbac.current_user()
    b = body()
    if b.get("confirm") is not True:
        raise ApiError("confirmation_required", "Withdrawing requires explicit confirmation.", 400)
    return jsonify({"publication": workflow.withdraw(str(b.get("alarmId") or ""), user, b.get("clientId"),
                                                     b.get("reason"))})


@bp.route("/api/sharing/bulk-request", methods=["POST"])
@rbac.internal("alarm.validate", "alarm.publish", "alarm.approve", any_of=True)
def bulk_request():
    """Controlled bulk: only eligible alarms are processed; the rest are
    reported with reasons. Bulk *publishing* is intentionally not offered —
    each publish needs its own client-safe preview and confirmation."""
    user = rbac.current_user()
    b = body()
    client = _client_or_404(b.get("clientId"))
    done, skipped = [], []
    for aid in (b.get("alarmIds") or [])[:100]:
        a = datasource.find_alarm(user, str(aid), b.get("projectId"))
        if not a:
            skipped.append({"alarmId": aid, "reasons": ["Alarm is not available to you"]})
            continue
        r = workflow.client_share_eligible(a, user, client, "request")
        if not r["eligible"]:
            skipped.append({"alarmId": aid, "reasons": [c["text"] for c in r["checks"] if not c["ok"]]})
            continue
        workflow.request_share(a, user, client["id"])
        done.append(aid)
    return jsonify({"requested": done, "skipped": skipped})


@bp.route("/api/sharing/respond", methods=["POST"])
@rbac.internal("alarm.publish", "alarm.approve", any_of=True)
def share_respond():
    """Operations-team response in the client thread of a SHARED alert."""
    user = rbac.current_user()
    b = body()
    a = _alarm(user, str(b.get("alarmId") or ""), b.get("projectId"))
    client = _client_or_404(b.get("clientId"))
    pub = next((p for p in a["publications"] if p["clientId"] == client["id"]), None)
    if not pub or pub["status"] != "shared":
        raise ApiError("not_eligible", "You can only respond on alerts currently shared with this client.", 409)
    return jsonify({"messages": workflow.add_message(a["alarmId"], client["id"], user, "response", b.get("body"),
                                                     a.get("projectId"))})


@bp.route("/api/sharing/history")
@rbac.internal("alarm.view")
def share_history():
    user = rbac.current_user()
    alarm_id = request.args.get("alarmId", "")
    a = _alarm(user, alarm_id, request.args.get("projectId"))
    events = [e for e in db.get_audit_trail(a["alarmId"]) if e["action"].startswith("share.")]
    return jsonify({"publications": a["publications"], "events": events})
