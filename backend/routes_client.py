"""
routes_client.py — the CLIENT PORTAL API (audience: client only).

Every response is produced from the client dataset
(workflow.client_visible_alarms / build_client_visible_alarm). Client users
can never reach an internal endpoint (rbac audience wall) and never receive
an internal alarm object, internal notes, unshared evidence, or aggregates
computed from hidden alarms.
"""

import requests
from flask import Blueprint, Response, jsonify, request

import analytics
import db
import notify
import rbac
import reports
import workflow
from camview_client import ApiError
from routes_common import body, project_label

bp = Blueprint("client", __name__)


def _user():
    return rbac.current_user()


def _pub_row(user, alarm_id):
    r = db.one("SELECT * FROM publications WHERE alarm_id=? AND client_id=?", (alarm_id, user["clientId"]))
    v = workflow.build_client_visible_alarm(user, r) if r else None
    if not v:
        # identical answer for "doesn't exist", "not shared", "other client", "withdrawn"
        raise ApiError("not_found", "Alert not available.", 404)
    return r, v


@bp.route("/api/client/overview")
@rbac.client("client.portal")
def overview():
    user = _user()
    data = workflow.client_visible_alarms(user)
    import exams as exams_mod
    my_exams = []
    for e in exams_mod.list_exams(include_inactive=False):
        if e["clientId"] == user["clientId"] and set(e["projectIds"]) & user["clientProjects"]                 and (not user["scopes"].get("exam") or e["id"] in user["scopes"]["exam"]):
            mine = [a for a in data if (a.get("exam") or {}).get("id") == e["id"]]
            my_exams.append({"id": e["id"], "name": e["name"], "code": e["code"], "alerts": len(mine),
                             "critical": sum(1 for a in mine if a.get("priority") == "critical"),
                             "latest": mine[0]["sharedAt"] if mine else None})
    return jsonify({
        "client": user["client"], "projects": [project_label(p) for p in sorted(user["clientProjects"])],
        "exams": my_exams,
        "metrics": {"shared": len(data), "critical": sum(1 for a in data if a.get("priority") == "critical"),
                    "awaitingAcknowledgement": sum(1 for a in data if not a.get("acknowledgedAt")),
                    "acknowledged": sum(1 for a in data if a.get("acknowledgedAt")),
                    "withEvidence": sum(1 for a in data if a.get("evidence"))},
        "recent": data[:8],
        "priorityDistribution": analytics.priority_distribution(data),
        "reports": _reports(user)[:5],
        "unreadNotifications": notify.unread_count(user["id"]),
    })


@bp.route("/api/client/alerts")
@rbac.client("client.portal")
def alerts():
    user = _user()
    data = everything = workflow.client_visible_alarms(user)
    f = request.args
    if f.get("priority"):
        data = [a for a in data if a.get("priority") == f["priority"]]
    if f.get("exam"):
        data = [a for a in data if (a.get("exam") or {}).get("id") == f["exam"]]
    if f.get("ack") == "pending":
        data = [a for a in data if not a.get("acknowledgedAt")]
    elif f.get("ack") == "done":
        data = [a for a in data if a.get("acknowledgedAt")]
    s = (f.get("search") or "").lower()
    if s:
        data = [a for a in data if s in a["alarmId"].lower() or s in (a.get("alarmTypeName") or "").lower()
                or any(s in c["code"].lower() for c in a.get("context", []))]
    exams_seen = sorted({(a["exam"]["id"], a["exam"]["name"]) for a in everything if a.get("exam")}, key=lambda x: x[1])
    return jsonify({"items": data, "total": len(data), "exams": [{"id": i, "name": n} for i, n in exams_seen]})


@bp.route("/api/client/alerts/<alarm_id>")
@rbac.client("client.portal")
def alert_detail(alarm_id):
    user = _user()
    r, v = _pub_row(user, alarm_id)
    if not r["viewed_at"]:
        db.execute("UPDATE publications SET viewed_at=? WHERE alarm_id=? AND client_id=?",
                   (db.now_iso(), alarm_id, user["clientId"]))
        db.audit("client.view", user, "alarm", alarm_id, client_id=user["clientId"], project_id=r["project_id"])
        v["viewedAt"] = db.now_iso()
    v["canAcknowledge"] = "client.acknowledge" in user["permissions"]
    v["canComment"] = "client.acknowledge" in user["permissions"]
    v["messages"] = workflow.messages_for(alarm_id, user["clientId"])
    return jsonify(v)


@bp.route("/api/client/alerts/<alarm_id>/messages", methods=["POST"])
@rbac.client("client.acknowledge")
def client_message(alarm_id):
    user = _user()
    r, _ = _pub_row(user, alarm_id)
    b = body()
    kind = b.get("kind") if b.get("kind") in ("comment", "clarification") else "comment"
    return jsonify({"messages": workflow.add_message(alarm_id, user["clientId"], user, kind, b.get("body"),
                                                     r["project_id"])})


@bp.route("/api/client/alerts/<alarm_id>/acknowledge", methods=["POST"])
@rbac.client("client.acknowledge")
def acknowledge(alarm_id):
    user = _user()
    r, _ = _pub_row(user, alarm_id)
    if r["acknowledged_at"]:
        raise ApiError("conflict", "Already acknowledged.", 409)
    comment = str(body().get("comment") or "").strip()[:2000] or None
    now = db.now_iso()
    db.execute("UPDATE publications SET acknowledged_by=?, acknowledged_at=?, ack_comment=?, updated_at=? "
               "WHERE alarm_id=? AND client_id=?", (user["name"], now, comment, now, alarm_id, user["clientId"]))
    db.audit("client.acknowledge", user, "alarm", alarm_id, "shared", "acknowledged", r["project_id"], user["clientId"],
             note=comment)
    notify.to_permission("alarm.publish", "client", f"Client acknowledged {alarm_id}",
                         f"{user['name']} ({user['client']['name']})" + (f": “{comment[:120]}”" if comment else ""),
                         f"#/investigations/{alarm_id}", project_id=r["project_id"], dedupe=f"ack:{alarm_id}:{user['clientId']}")
    return jsonify(_pub_row(user, alarm_id)[1])


@bp.route("/api/client/evidence/<alarm_id>/<kind>/<int:index>")
@rbac.client("client.evidence")
def evidence(alarm_id, kind, index):
    """Evidence proxy: the client never gets the raw internal URL, and only
    items explicitly marked shared are served."""
    user = _user()
    r, _ = _pub_row(user, alarm_id)
    shared = [e for e in (db.jload(r["evidence"], []) or []) if e.get("shared")]
    if not any(e["kind"] == kind and e["index"] == index for e in shared):
        raise ApiError("not_found", "Evidence not available.", 404)
    snap = db.jload(r["snapshot"], {}) or {}
    src = snap
    import datasource
    for base in datasource.refresh(str(r["project_id"])).items:          # Camview signs media URLs for 7 days
        if base["alarmId"] == alarm_id:
            src = base
            break
    urls = src.get("imageUrls") or snap.get("imageUrls") or []
    url = (urls[index] if index < len(urls) else None) if kind == "image" else (src.get("videoUrl") or snap.get("videoUrl"))
    if not url:
        raise ApiError("not_found", "Evidence not available.", 404)
    if url.startswith("/demo-evidence/"):
        import config
        import demo_evidence
        from urllib.parse import parse_qs, urlparse
        if config.MODE != "demo":
            raise ApiError("not_found", "Evidence not available.", 404)
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        name = u.path.rsplit("/", 1)[-1].removesuffix(".svg")
        svg = demo_evidence.render(name, q.get("cam", ""), q.get("room", ""), q.get("ts", ""), q.get("type", ""),
                                   int(q.get("f", "0") or 0))
        return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "private, max-age=300"})
    try:
        upstream = requests.get(url, stream=True, timeout=15,
                                headers={"Range": request.headers["Range"]} if "Range" in request.headers else {})
    except requests.RequestException:
        raise ApiError("evidence_unavailable", "Evidence could not be loaded right now.", 502)
    if upstream.status_code >= 400:
        raise ApiError("evidence_unavailable", "Evidence could not be loaded right now.", 502)
    headers = {k: upstream.headers[k] for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
               if k in upstream.headers}
    headers["Cache-Control"] = "private, max-age=300"
    return Response(upstream.iter_content(64 * 1024), status=upstream.status_code, headers=headers)


def _reports(user):
    if "client.report.view" not in user["permissions"]:
        return []
    return db.rows("SELECT id, title, type, generated_at FROM reports WHERE client_id=? AND audience='client' "
                   "AND shared_with_client=1 ORDER BY id DESC", (user["clientId"],))


@bp.route("/api/client/reports")
@rbac.client("client.report.view")
def client_reports():
    return jsonify({"items": _reports(_user())})


def _client_report(user, rid):
    r = db.one("SELECT * FROM reports WHERE id=? AND client_id=? AND audience='client' AND shared_with_client=1",
               (rid, user["clientId"]))
    if not r:
        raise ApiError("not_found", "Report not available.", 404)
    return r


@bp.route("/api/client/reports/<int:rid>")
@rbac.client("client.report.view")
def client_report(rid):
    user = _user()
    r = _client_report(user, rid)
    db.audit("client.report_view", user, "report", rid, client_id=user["clientId"])
    return jsonify({"id": rid, **(db.jload(r["data"], {}) or {})})


@bp.route("/api/client/reports/<int:rid>/csv")
@rbac.client("client.report.view")
def client_report_csv(rid):
    user = _user()
    r = _client_report(user, rid)
    db.audit("client.report_export", user, "report", rid, client_id=user["clientId"])
    return Response(reports.to_csv(db.jload(r["data"], {})), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="report-{rid}.csv"'})


@bp.route("/api/client/analytics")
@rbac.client("client.analytics")
def client_analytics():
    """Client-visible analytics: computed ONLY from the client dataset."""
    user = _user()
    data = workflow.client_visible_alarms(user)
    daily = {}
    for a in data:
        d = (a.get("firstInstance") or "")[:10]
        if d:
            daily[d] = daily.get(d, 0) + 1
    return jsonify({"basis": f"{len(data)} alerts shared with {user['client']['name']}",
                    "total": len(data), "priorityDistribution": analytics.priority_distribution(data),
                    "typeDistribution": analytics.type_distribution(data),
                    "daily": [{"date": d, "count": c} for d, c in sorted(daily.items())],
                    "acknowledged": sum(1 for a in data if a.get("acknowledgedAt"))})


@bp.route("/api/client/presentation")
@rbac.client("presentation.view")
def client_presentation():
    user = _user()
    data = workflow.client_visible_alarms(user)
    return jsonify({"audience": "client", "client": user["client"], "total": len(data),
                    "critical": sum(1 for a in data if a.get("priority") == "critical"),
                    "acknowledged": sum(1 for a in data if a.get("acknowledgedAt")),
                    "priorityDistribution": analytics.priority_distribution(data),
                    "recent": data[:5], "generatedAt": db.now_iso()})


@bp.route("/api/client/profile")
@rbac.client("client.portal")
def profile():
    user = _user()
    return jsonify({"user": rbac.public_user(user),
                    "projects": [project_label(p) for p in sorted(user["clientProjects"])]})
