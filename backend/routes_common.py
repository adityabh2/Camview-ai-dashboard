"""
routes_common.py — helpers shared by every blueprint + auth, notifications,
saved views (endpoints used by internal AND client users).
"""

import csv
import io
import threading
import time

from flask import Blueprint, Response, jsonify, request, session, stream_with_context
from werkzeug.security import check_password_hash, generate_password_hash

import changes
import config
import datasource
import db
import nomenclature
import notify
import rbac
from camview_client import ApiError

bp = Blueprint("common", __name__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def body():
    return request.get_json(silent=True) or {}


def project_param(user, required=True):
    """The project a request is about, validated against the user's scope."""
    pid = request.args.get("projectId") or (request.get_json(silent=True) or {}).get("projectId")
    allowed = datasource.allowed_projects(user)
    if pid in (None, ""):
        if allowed:
            return allowed[0]
        if required:
            raise ApiError("no_project", "No project is available to you. An administrator must import master data "
                                         "or set a default project, and grant you access.", 404)
        return None
    pid = str(pid)
    if not rbac.project_allowed(user, pid) or pid not in allowed:
        raise ApiError("not_found", "Project unavailable.", 404)
    return pid


def paginate(items, page, size):
    try:
        page = max(1, int(page or 1))
        size = max(1, min(200, int(size or 25)))
    except (TypeError, ValueError):
        raise ApiError("bad_request", "page and size must be numbers.", 400)
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    return {"items": items[(page - 1) * size: page * size], "page": page, "size": size, "totalElements": total,
            "totalPages": pages, "hasNext": page < pages}


def csv_response(rows, header, filename):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def project_label(pid):
    for p in nomenclature.projects():
        if p["externalId"] == str(pid):
            return p
    return {"id": None, "code": str(pid), "name": None, "externalId": str(pid)}


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

_fail_lock = threading.Lock()
_failures = {}   # ip -> [timestamps]


def _too_many(ip):
    now = time.time()
    with _fail_lock:
        recent = [t for t in _failures.get(ip, []) if now - t < 300]
        _failures[ip] = recent
        return len(recent) >= 8


def _record_fail(ip):
    with _fail_lock:
        _failures.setdefault(ip, []).append(time.time())


EVENTS_KEEPALIVE = 20          # seconds between comment lines that keep the stream (and proxies) alive
EVENTS_COALESCE = 0.4          # seconds to wait after a change so a burst (auto-share of 100 tickets) is one event


@bp.route("/api/events")
@rbac.authenticated
def events():
    """Push channel (Server-Sent Events): one `version` event whenever alarm data or Command Center data
    changed. The browser then re-reads what it shows through the normal API — this stream carries no
    data of its own, so scope and permissions stay where they are enforced. Polling keeps working
    without it (a proxy that buffers, an old browser): this only makes updates immediate."""
    def gen():
        last = changes.version()
        yield f"retry: 3000\nevent: version\ndata: {last}\n\n"
        while True:
            now = changes.wait(last, EVENTS_KEEPALIVE)
            if now == last:
                yield ": keepalive\n\n"
                continue
            time.sleep(EVENTS_COALESCE)
            last = changes.version()
            yield f"event: version\ndata: {last}\n\n"

    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@bp.route("/api/auth/session")
def auth_session():
    """Public: tells the login screen what mode we're in. Never includes secrets."""
    user = rbac.current_user()
    out = {"authenticated": bool(user), "mode": config.MODE, "features": config.FEATURES,
           "product": {"name": "CAMVIEW", "product": "Command Center",
                       "tagline": "Alarm Intelligence • Live Operations • Investigation • Collaboration"}}
    if config.MODE == "demo":
        out["demoUsers"] = [{"email": r["email"], "name": r["name"], "role": r["role_name"], "audience": r["audience"]}
                            for r in db.rows("SELECT u.email, u.name, r.name AS role_name, r.audience FROM users u "
                                             "JOIN roles r ON r.id=u.role_id WHERE u.is_demo=1 AND u.status='active' "
                                             "ORDER BY r.audience DESC, u.rowid")]
        out["demoPassword"] = "demo"
    if user:
        out["user"] = rbac.public_user(user)
        out["projects"] = [project_label(p) for p in datasource.allowed_projects(user)] \
            if user["audience"] == "internal" else [project_label(p) for p in sorted(user["clientProjects"])]
        out["apiConfigured"] = bool(config.API_KEY)
    return jsonify(out)


@bp.route("/api/auth/login", methods=["POST"])
def login():
    if not request.is_json:
        raise ApiError("bad_request", "Requests must be JSON.", 415)
    ip = request.remote_addr or "?"
    if _too_many(ip):
        raise ApiError("rate_limited", "Too many failed sign-in attempts. Try again in a few minutes.", 429)
    b = body()
    email, password = str(b.get("email") or "").strip(), str(b.get("password") or "")
    row = db.one("SELECT id, name, password_hash, status FROM users WHERE email = ?", (email,))
    if not row or not row["password_hash"] or not check_password_hash(row["password_hash"], password):
        _record_fail(ip)
        db.audit("auth.login_failed", None, "user", email or "?", details={"ip": ip})
        raise ApiError("invalid_credentials", "Email or password is incorrect.", 401)
    if row["status"] != "active":
        raise ApiError("account_disabled", "This account is disabled.", 403)
    user = rbac.load_user(row["id"])
    if not user or not user["permissions"]:
        raise ApiError("account_disabled", "This account has no access (inactive client or empty role).", 403)
    session.clear()
    session["uid"] = row["id"]
    session.permanent = True
    # keep the previous visit for "Since last visit"
    db.execute("UPDATE users SET prev_login_at=last_login_at, last_login_at=? WHERE id=?", (db.now_iso(), row["id"]))
    db.audit("auth.login", user, "user", user["id"], details={"ip": ip})
    return auth_session()


@bp.route("/api/auth/logout", methods=["POST"])
def logout():
    user = rbac.current_user()
    if user:
        db.audit("auth.logout", user, "user", user["id"])
    session.clear()
    return jsonify({"ok": True})


@bp.route("/api/auth/password", methods=["POST"])
@rbac.authenticated
def change_password():
    user = rbac.current_user()
    b = body()
    row = db.one("SELECT password_hash FROM users WHERE id=?", (user["id"],))
    if not check_password_hash(row["password_hash"] or "", str(b.get("current") or "")):
        raise ApiError("invalid_credentials", "Current password is incorrect.", 400)
    new = str(b.get("new") or "")
    if len(new) < 10:
        raise ApiError("bad_request", "New password must be at least 10 characters.", 400)
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), user["id"]))
    db.audit("user.password_change", user, "user", user["id"])
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# notifications (both audiences)
# ---------------------------------------------------------------------------

@bp.route("/api/notifications")
@rbac.authenticated
def notifications():
    user = rbac.current_user()
    items = notify.list_for(user["id"], include_archived=request.args.get("archived") == "1",
                            include_snoozed=request.args.get("snoozed") == "1")
    cat = request.args.get("category")
    if cat:
        items = [n for n in items if n["category"] == cat]
    sev = request.args.get("severity")
    if sev:
        items = [n for n in items if n["severity"] == sev]
    if request.args.get("unread") == "1":
        items = [n for n in items if not n["read"]]
    counts = {}
    for n in notify.list_for(user["id"]):
        if not n["read"]:
            counts[n["category"]] = counts.get(n["category"], 0) + 1
    return jsonify({"items": items, "unread": notify.unread_count(user["id"]), "unreadByCategory": counts,
                    "channels": notify.CHANNELS, "dataVersion": changes.version()})


@bp.route("/api/notifications/mark", methods=["POST"])
@rbac.authenticated
def notifications_mark():
    user = rbac.current_user()
    b = body()
    ids = b.get("ids")
    if b.get("action") == "snooze":
        from datetime import datetime, timedelta, timezone
        until = b.get("until")
        if b.get("minutes") is not None:
            try:
                minutes = int(b["minutes"])
            except (TypeError, ValueError):
                raise ApiError("bad_request", "minutes must be a number.", 400)
            if not 1 <= minutes <= 24 * 60:
                raise ApiError("bad_request", "Snooze between 1 minute and 24 hours.", 400)
            until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if not until or ids == "all":
            raise ApiError("bad_request", "Choose notifications and a snooze duration.", 400)
        skipped = notify.snooze(user["id"], [int(i) for i in (ids or [])], until)
        return jsonify({"ok": True, "unread": notify.unread_count(user["id"]), "criticalNotSnoozed": skipped})
    field = {"read": "read_at", "archive": "archived_at"}.get(b.get("action"))
    if not field:
        raise ApiError("bad_request", "action must be read, archive or snooze.", 400)
    notify.mark(user["id"], "all" if ids == "all" else [int(i) for i in (ids or [])], field)
    return jsonify({"ok": True, "unread": notify.unread_count(user["id"])})


@bp.route("/api/me/preferences")
@rbac.authenticated
def my_prefs():
    return jsonify({"notifications": notify.get_prefs(rbac.current_user()["id"])})


@bp.route("/api/me/preferences", methods=["PUT"])
@rbac.authenticated
def my_prefs_update():
    user = rbac.current_user()
    prefs = notify.set_prefs(user["id"], body().get("notifications") or {})
    db.audit("user.preferences", user, "user", user["id"], None, prefs)
    return jsonify({"notifications": prefs})


# ---------------------------------------------------------------------------
# saved views (per user; replayed through the same permission-checked APIs)
# ---------------------------------------------------------------------------

@bp.route("/api/views")
@rbac.authenticated
def views_list():
    user = rbac.current_user()
    return jsonify({"items": [{"id": r["id"], "name": r["name"], "route": r["route"], "query": r["query"]}
                              for r in db.rows("SELECT * FROM saved_views WHERE user_id=? ORDER BY name", (user["id"],))]})


@bp.route("/api/views", methods=["POST"])
@rbac.authenticated
def views_create():
    user = rbac.current_user()
    b = body()
    name, route = str(b.get("name") or "").strip(), str(b.get("route") or "").strip()
    if not name or not route.startswith("/"):
        raise ApiError("bad_request", "A name and a route are required.", 400)
    vid = db.execute("INSERT INTO saved_views (user_id, name, route, query, created_at) VALUES (?,?,?,?,?)",
                     (user["id"], name[:80], route[:200], str(b.get("query") or "")[:1000], db.now_iso()))
    return jsonify({"id": vid})


@bp.route("/api/views/<int:vid>", methods=["DELETE"])
@rbac.authenticated
def views_delete(vid):
    user = rbac.current_user()
    db.execute("DELETE FROM saved_views WHERE id=? AND user_id=?", (vid, user["id"]))
    return jsonify({"ok": True})
