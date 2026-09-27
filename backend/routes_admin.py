"""
routes_admin.py — administration (internal only): users, roles & permissions,
clients, settings (workflow policy, connection, mode), nomenclature master
data, alarm type dictionary, priority configuration, alert rules, schedules,
audit trail and the Report Center.
"""

import re
import secrets
import time
import uuid

from flask import Blueprint, Response, jsonify, request, session
from werkzeug.security import generate_password_hash

import config
import datasource
import db
import intelligence
import nomenclature
import notify
import rbac
import reports
import workflow
from camview_client import ApiError, list_page, validate_api_url
from routes_common import body, project_label, project_param

bp = Blueprint("admin", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")   # sign-in names like "admin" or "mpesb.control"


def _user():
    return rbac.current_user()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _user_row(r):
    scopes = db.rows("SELECT scope_type, scope_value FROM user_scopes WHERE user_id=?", (r["id"],))
    return {"id": r["id"], "name": r["name"], "email": r["email"], "roleId": r["role_id"], "roleName": r["role_name"],
            "audience": r["audience"], "clientId": r["client_id"], "clientName": r.get("client_name"),
            "status": r["status"], "lastLoginAt": r["last_login_at"], "createdAt": r["created_at"],
            "isDemo": bool(r["is_demo"]), "scopes": [{"type": s["scope_type"], "value": s["scope_value"]} for s in scopes]}


@bp.route("/api/users")
@rbac.internal("user.view")
def users_list():
    rows = db.rows("SELECT u.*, r.name AS role_name, r.audience, c.name AS client_name FROM users u "
                   "JOIN roles r ON r.id=u.role_id LEFT JOIN clients c ON c.id=u.client_id ORDER BY r.audience DESC, u.name")
    return jsonify({"items": [_user_row(r) for r in rows], "scopeTypes": rbac.SCOPE_TYPES})


def _validate_user_payload(b, creating):
    name, email = str(b.get("name") or "").strip(), str(b.get("email") or "").strip()
    if creating or "name" in b:
        if not name:
            raise ApiError("bad_request", "Name is required.", 400)
    if creating or "email" in b:
        if not (EMAIL_RE.match(email) or USERNAME_RE.match(email)):
            raise ApiError("bad_request", "Enter an email or a username (3–64 letters, digits, dot, dash, underscore).", 400)
    role = db.one("SELECT id, audience FROM roles WHERE id=?", (b.get("roleId"),)) if b.get("roleId") else None
    if (creating or "roleId" in b) and not role:
        raise ApiError("bad_request", "Unknown role.", 400)
    if role and role["audience"] == "client":
        if not b.get("clientId") or not db.one("SELECT 1 FROM clients WHERE id=?", (b["clientId"],)):
            raise ApiError("bad_request", "Client users must be assigned to a client.", 400)
    scopes = b.get("scopes")
    if scopes is not None:
        client_role = bool(role and role["audience"] == "client")
        for s in scopes:
            allowed = rbac.CLIENT_SCOPE_TYPES if client_role else rbac.SCOPE_TYPES
            if s.get("type") not in allowed or not str(s.get("value") or "").strip():
                raise ApiError("bad_request", "Invalid scope.", 400)
            if client_role:                          # only exams that belong to this login's client
                ex = db.one("SELECT client_id FROM exams WHERE id=?", (str(s["value"]),))
                if not ex or ex["client_id"] != b.get("clientId"):
                    raise ApiError("bad_request", "A client login can only be limited to its own client's exams.", 400)
    return name, email, role


def _guard_role_change(actor, target_role_id):
    """Only role managers can grant the super admin role."""
    if target_role_id == "super_admin" and "role.manage" not in actor["permissions"]:
        raise ApiError("forbidden", "Only users who manage roles can assign Super Admin.", 403)


@bp.route("/api/users", methods=["POST"])
@rbac.internal("user.manage")
def users_create():
    actor = _user()
    b = body()
    name, email, role = _validate_user_payload(b, True)
    _guard_role_change(actor, role["id"])
    if db.one("SELECT 1 FROM users WHERE email=?", (email,)):
        raise ApiError("conflict", "A user with that email already exists.", 409)
    password = str(b.get("password") or "") or secrets.token_urlsafe(9)
    if len(password) < config.PASSWORD_MIN:
        raise ApiError("bad_request", f"Password must be at least {config.PASSWORD_MIN} characters.", 400)
    uid = "u-" + uuid.uuid4().hex[:10]
    with db.connect() as conn:
        conn.execute("INSERT INTO users (id, name, email, password_hash, role_id, client_id, status, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (uid, name, email, generate_password_hash(password), role["id"],
                                                 b.get("clientId") if role["audience"] == "client" else None,
                                                 "active", db.now_iso()))
        for s in (b.get("scopes") or []):          # internal: project/centre…; client: exam
            conn.execute("INSERT OR IGNORE INTO user_scopes (user_id, scope_type, scope_value) VALUES (?,?,?)",
                         (uid, s["type"], str(s["value"]).strip()))
    db.audit("user.create", actor, "user", uid, None, {"email": email, "role": role["id"], "scopes": b.get("scopes")})
    return jsonify({"id": uid, "temporaryPassword": None if b.get("password") else password})


@bp.route("/api/users/<uid>", methods=["DELETE"])
@rbac.internal("user.manage")
def users_delete(uid):
    """Deletes a login for good (its notifications, views and scopes go with it; the audit trail keeps its name).
    Never yourself, never the last active Super Admin, and a Super Admin only by someone who manages roles."""
    actor = _user()
    u = db.one("SELECT id, email, name, role_id, client_id, status FROM users WHERE id=?", (uid,))
    if not u:
        raise ApiError("not_found", "User not found.", 404)
    if uid == actor["id"]:
        raise ApiError("bad_request", "You can't delete your own account.", 400)
    if u["role_id"] == "super_admin":
        if "role.manage" not in actor["permissions"]:
            raise ApiError("forbidden", "Only users who manage roles can delete a Super Admin.", 403)
        others = db.one("SELECT COUNT(*) AS n FROM users WHERE role_id='super_admin' AND status='active' AND id<>?", (uid,))
        if others["n"] == 0:
            raise ApiError("bad_request", "At least one active Super Admin is required.", 400)
    with db.connect() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
    db.audit("user.delete", actor, "user", uid, {"email": u["email"], "name": u["name"], "role": u["role_id"],
                                                 "client": u["client_id"]}, None, client_id=u["client_id"])
    return jsonify({"deleted": uid})


@bp.route("/api/users/<uid>", methods=["PUT"])
@rbac.internal("user.manage")
def users_update(uid):
    actor = _user()
    b = body()
    existing = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not existing:
        raise ApiError("not_found", "User not found.", 404)
    _, _, role = _validate_user_payload({**b, "roleId": b.get("roleId", existing["role_id"]),
                                         "clientId": b.get("clientId", existing["client_id"])}, False)
    if b.get("roleId") and b["roleId"] != existing["role_id"]:
        _guard_role_change(actor, b["roleId"])
        if existing["role_id"] == "super_admin" and "role.manage" not in actor["permissions"]:
            raise ApiError("forbidden", "You can't change a Super Admin's role.", 403)
    if uid == actor["id"] and (b.get("status") == "disabled" or (b.get("roleId") and b["roleId"] != existing["role_id"])):
        raise ApiError("bad_request", "You can't disable yourself or change your own role.", 400)
    if existing["role_id"] == "super_admin" and b.get("status") == "disabled":
        others = db.one("SELECT COUNT(*) AS n FROM users WHERE role_id='super_admin' AND status='active' AND id<>?", (uid,))
        if others["n"] == 0:
            raise ApiError("bad_request", "At least one active Super Admin is required.", 400)
    old = _user_row({**existing, "role_name": None, "audience": None, "client_name": None})
    fields = {}
    for k, col in (("name", "name"), ("email", "email"), ("roleId", "role_id"), ("status", "status"),
                   ("clientId", "client_id")):
        if k in b:
            fields[col] = b[k]
    if "status" in fields and fields["status"] not in ("active", "disabled"):
        raise ApiError("bad_request", "Status must be active or disabled.", 400)
    if role and role["audience"] == "internal":
        fields["client_id"] = None
    with db.connect() as conn:
        if fields:
            conn.execute(f"UPDATE users SET {', '.join(f'{c}=?' for c in fields)} WHERE id=?", [*fields.values(), uid])
        if b.get("scopes") is not None:
            conn.execute("DELETE FROM user_scopes WHERE user_id=?", (uid,))
            if True:                                     # validated above for the role's audience
                for s in b["scopes"]:
                    conn.execute("INSERT OR IGNORE INTO user_scopes (user_id, scope_type, scope_value) VALUES (?,?,?)",
                                 (uid, s["type"], str(s["value"]).strip()))
        if b.get("password"):
            if len(str(b["password"])) < config.PASSWORD_MIN:
                raise ApiError("bad_request", f"Password must be at least {config.PASSWORD_MIN} characters.", 400)
            # a reset signs the account out of every browser it is signed in on
            conn.execute("UPDATE users SET password_hash=?, pw_changed_at=? WHERE id=?",
                         (generate_password_hash(str(b["password"])), db.now_iso(), uid))
    new = {k: v for k, v in b.items() if k != "password"}
    db.audit("user.update", actor, "user", uid, {k: old.get(k) for k in new}, new,
             note="password reset" if b.get("password") else None)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Roles & permissions
# ---------------------------------------------------------------------------

@bp.route("/api/roles")
@rbac.internal("role.view")
def roles_list():
    roles = db.rows("SELECT * FROM roles ORDER BY audience DESC, is_system DESC, name")
    for r in roles:
        r["permissions"] = sorted(x["permission"] for x in
                                  db.rows("SELECT permission FROM role_permissions WHERE role_id=?", (r["id"],)))
        r["users"] = db.one("SELECT COUNT(*) AS n FROM users WHERE role_id=?", (r["id"],))["n"]
    return jsonify({"items": roles, "catalog": rbac.PERMISSIONS, "clientPermissions": sorted(rbac.CLIENT_PERMS),
                    "internalPermissions": sorted(rbac.INTERNAL_ONLY)})


@bp.route("/api/roles", methods=["POST"])
@rbac.internal("role.manage")
def roles_create():
    actor = _user()
    b = body()
    name = str(b.get("name") or "").strip()
    audience = b.get("audience") if b.get("audience") in ("internal", "client") else "internal"
    if not name:
        raise ApiError("bad_request", "Role name is required.", 400)
    rid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or uuid.uuid4().hex[:8]
    if db.one("SELECT 1 FROM roles WHERE id=?", (rid,)):
        rid += "_" + uuid.uuid4().hex[:4]
    allowed = rbac.CLIENT_PERMS if audience == "client" else rbac.INTERNAL_ONLY
    perms = [p for p in (b.get("permissions") or []) if p in allowed]
    with db.connect() as conn:
        conn.execute("INSERT INTO roles (id, name, description, audience, is_system) VALUES (?,?,?,?,0)",
                     (rid, name, str(b.get("description") or ""), audience))
        conn.executemany("INSERT INTO role_permissions (role_id, permission) VALUES (?,?)", [(rid, p) for p in perms])
    db.audit("role.create", actor, "role", rid, None, {"name": name, "audience": audience, "permissions": perms})
    return jsonify({"id": rid})


@bp.route("/api/roles/<rid>", methods=["PUT"])
@rbac.internal("role.manage")
def roles_update(rid):
    actor = _user()
    role = db.one("SELECT * FROM roles WHERE id=?", (rid,))
    if not role:
        raise ApiError("not_found", "Role not found.", 404)
    b = body()
    if rid == "super_admin" and b.get("permissions") is not None and \
            not set(rbac.INTERNAL_ONLY) <= set(b["permissions"]):
        raise ApiError("bad_request", "Super Admin must keep every internal permission.", 400)
    if rid == actor["roleId"] and b.get("permissions") is not None and "role.manage" not in b["permissions"]:
        raise ApiError("bad_request", "You can't remove role management from your own role.", 400)
    old = sorted(x["permission"] for x in db.rows("SELECT permission FROM role_permissions WHERE role_id=?", (rid,)))
    allowed = rbac.CLIENT_PERMS if role["audience"] == "client" else rbac.INTERNAL_ONLY
    with db.connect() as conn:
        if b.get("name"):
            conn.execute("UPDATE roles SET name=?, description=? WHERE id=?",
                         (str(b["name"]).strip(), str(b.get("description", role["description"]) or ""), rid))
        if b.get("permissions") is not None:
            perms = sorted({p for p in b["permissions"] if p in allowed})
            conn.execute("DELETE FROM role_permissions WHERE role_id=?", (rid,))
            conn.executemany("INSERT INTO role_permissions (role_id, permission) VALUES (?,?)", [(rid, p) for p in perms])
    new = sorted(x["permission"] for x in db.rows("SELECT permission FROM role_permissions WHERE role_id=?", (rid,)))
    if new != old:
        db.audit("role.update", actor, "role", rid, {"removed": sorted(set(old) - set(new))},
                 {"added": sorted(set(new) - set(old))})
    return jsonify({"ok": True, "permissions": new})


@bp.route("/api/roles/<rid>", methods=["DELETE"])
@rbac.internal("role.manage")
def roles_delete(rid):
    actor = _user()
    role = db.one("SELECT * FROM roles WHERE id=?", (rid,))
    if not role:
        raise ApiError("not_found", "Role not found.", 404)
    if role["is_system"]:
        raise ApiError("bad_request", "Built-in roles can't be deleted (edit their permissions instead).", 400)
    if db.one("SELECT COUNT(*) AS n FROM users WHERE role_id=?", (rid,))["n"]:
        raise ApiError("bad_request", "Move users to another role first.", 400)
    db.execute("DELETE FROM roles WHERE id=?", (rid,))
    db.audit("role.delete", actor, "role", rid, role["name"], None)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def _client_out(c):
    cid = c["id"]
    counts = {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) AS n FROM publications WHERE client_id=? "
                                                   "GROUP BY status", (cid,))}
    return {"id": cid, "name": c["name"], "status": c["status"], "contact": c["contact"], "createdAt": c["created_at"],
            "projects": [r["project_id"] for r in db.rows("SELECT project_id FROM client_projects WHERE client_id=?", (cid,))],
            "users": [{**u, "exams": [x["scope_value"] for x in db.rows(
                "SELECT scope_value FROM user_scopes WHERE user_id=? AND scope_type='exam'", (u["id"],))]}
                for u in db.rows("SELECT u.id, u.name, u.email, u.role_id AS roleId, r.name AS role, u.status, "
                                 "u.last_login_at AS lastLoginAt FROM users u JOIN roles r ON r.id=u.role_id "
                                 "WHERE u.client_id=? ORDER BY u.name", (cid,))],
            "exams": db.rows("SELECT id, name, code, status FROM exams WHERE client_id=? ORDER BY name", (cid,)),
            "sharedAlerts": counts.get("shared", 0), "publicationCounts": counts,
            "acknowledged": db.one("SELECT COUNT(*) AS n FROM publications WHERE client_id=? AND acknowledged_at IS NOT NULL",
                                   (cid,))["n"],
            "reports": db.one("SELECT COUNT(*) AS n FROM reports WHERE client_id=? AND shared_with_client=1", (cid,))["n"],
            "notificationPrefs": db.jload(c["notification_prefs"], {}) or {},
            "visibilityPolicy": db.jload(c["visibility_policy"], {}) or {}}


@bp.route("/api/clients")
@rbac.internal("client.view")
def clients_list():
    return jsonify({"clientRoles": db.rows("SELECT id, name FROM roles WHERE audience='client' ORDER BY name"),
                    "canManageLogins": "user.manage" in _user()["permissions"],
                    "items": [_client_out(c) for c in db.rows("SELECT * FROM clients ORDER BY name")],
                    "projects": [project_label(p) for p in datasource.allowed_projects(_user())]})


@bp.route("/api/clients", methods=["POST"])
@rbac.internal("client.manage")
def clients_create():
    actor = _user()
    b = body()
    name = str(b.get("name") or "").strip()
    if not name:
        raise ApiError("bad_request", "Client name is required.", 400)
    cid = "client-" + uuid.uuid4().hex[:8]
    db.execute("INSERT INTO clients (id, name, status, contact, notification_prefs, visibility_policy, created_at) "
               "VALUES (?,?,?,?,?,?,?)", (cid, name, "active", str(b.get("contact") or ""),
                                          db.jdump(b.get("notificationPrefs") or {"inApp": True}),
                                          db.jdump(b.get("visibilityPolicy") or {"showTicket": False}), db.now_iso()))
    _set_client_projects(cid, b.get("projects") or [], actor)
    db.audit("client.create", actor, "client", cid, None, {"name": name, "projects": b.get("projects") or []},
             client_id=cid)
    return jsonify({"id": cid})


def _set_client_projects(cid, projects, actor):
    old = {r["project_id"] for r in db.rows("SELECT project_id FROM client_projects WHERE client_id=?", (cid,))}
    new = {str(p) for p in projects}
    with db.connect() as conn:
        conn.execute("DELETE FROM client_projects WHERE client_id=?", (cid,))
        conn.executemany("INSERT INTO client_projects (client_id, project_id) VALUES (?,?)", [(cid, p) for p in new])
    if old != new:
        db.audit("client.assignment", actor, "client", cid, sorted(old), sorted(new), client_id=cid,
                 note="Access to shared data follows the current assignment (removed projects are no longer visible).")


@bp.route("/api/clients/<cid>/preview")
@rbac.internal("client.view")
def client_preview(cid):
    """What this client's logins see right now (every exam; a login limited to exams sees a subset): the exact
    client firewall output, for administrators to check. Nothing is marked as viewed."""
    c = db.one("SELECT id, name, status FROM clients WHERE id=?", (cid,))
    if not c:
        raise ApiError("not_found", "Client not found.", 404)
    as_client = {"id": "preview", "audience": "client", "clientId": cid, "client": c, "scopes": {},
                 "permissions": {"client.portal", "client.evidence"},
                 "clientProjects": {r["project_id"] for r in db.rows("SELECT project_id FROM client_projects WHERE client_id=?", (cid,))}}
    items = workflow.client_visible_alarms(as_client) if c["status"] == "active" else []
    waiting = db.rows("SELECT ref, alarm_id, delivery_status, delivery_note FROM tickets WHERE status='open' AND "
                      "validated_by_id IS NOT NULL AND COALESCE(delivery_status,'') <> 'delivered' AND "
                      "(client_id=? OR client_id IS NULL) ORDER BY id DESC LIMIT 20", (cid,))
    return jsonify({"client": c, "items": items[:50], "total": len(items), "waiting": waiting,
                    "rule": workflow.policy().get("clientsSeeOperatorValidOnly", True)})


@bp.route("/api/clients/<cid>", methods=["PUT"])
@rbac.internal("client.manage")
def clients_update(cid):
    actor = _user()
    c = db.one("SELECT * FROM clients WHERE id=?", (cid,))
    if not c:
        raise ApiError("not_found", "Client not found.", 404)
    b = body()
    fields = {}
    if "name" in b:
        fields["name"] = str(b["name"]).strip() or c["name"]
    if "status" in b:
        if b["status"] not in ("active", "inactive"):
            raise ApiError("bad_request", "Status must be active or inactive.", 400)
        fields["status"] = b["status"]
    if "contact" in b:
        fields["contact"] = str(b["contact"] or "")
    if "notificationPrefs" in b:
        fields["notification_prefs"] = db.jdump(b["notificationPrefs"] or {})
    if "visibilityPolicy" in b:
        fields["visibility_policy"] = db.jdump(b["visibilityPolicy"] or {})
    if fields:
        db.execute(f"UPDATE clients SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), cid])
        db.audit("client.update", actor, "client", cid, {k: c.get(k) for k in fields}, fields, client_id=cid)
    if b.get("projects") is not None:
        _set_client_projects(cid, b["projects"], actor)
    import tickets
    tickets.deliver_pending_valid()              # projects just mapped: pending VALID alerts can go out
    return jsonify(_client_out(db.one("SELECT * FROM clients WHERE id=?", (cid,))))


# ---------------------------------------------------------------------------
# Settings: workflow policy, features, system status, connection, mode
# ---------------------------------------------------------------------------

def _setup_allowed():
    from urllib.parse import urlparse
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return False
    return config.ALLOW_REMOTE_SETUP or request.remote_addr in ("127.0.0.1", "::1")


@bp.route("/api/settings")
@rbac.internal("settings.view")
def settings_get():
    user = _user()
    feeds = {}
    for pid in datasource.allowed_projects(user):
        feeds[pid] = datasource.freshness(datasource.refresh(pid))
    return jsonify({
        "policy": workflow.policy(), "policyDefaults": workflow.POLICY_DEFAULTS, "features": config.FEATURES,
        "mode": config.MODE,
        "connection": {"apiConfigured": bool(config.API_KEY), "apiUrl": config.API_URL,
                       "defaultProjectId": config.DEFAULT_PROJECT_ID or None, "timeout": config.TIMEOUT,
                       "windowPages": config.WINDOW_PAGES, "cacheSeconds": config.CACHE_SECONDS,
                       "setupAllowed": _setup_allowed() and "settings.manage" in user["permissions"]},
        "system": {"feeds": feeds, "channels": notify.CHANNELS, "database": "demo" if config.MODE == "demo" else "live",
                   "extraProjects": db.get_setting("extra_projects", []) or [],
                   "monitoredProjects": _monitored_projects(), "autoDiscovery": datasource.auto_status(),
                   "staleProjects": [p for p in nomenclature.projects() if p.get("source") == "camview"
                                     and p["externalId"] not in datasource.all_project_ids()]},
    })


@bp.route("/api/settings/policy", methods=["PUT"])
@rbac.internal("settings.manage")
def settings_policy():
    b = body()
    clean = {}
    for k, v in b.items():
        if k not in workflow.POLICY_DEFAULTS:
            continue
        default = workflow.POLICY_DEFAULTS[k]
        if isinstance(default, bool):
            clean[k] = bool(v)
        elif k == "deliveryMode":
            if v not in ("automatic", "controlled"):
                raise ApiError("bad_request", "deliveryMode must be automatic or controlled.", 400)
            clean[k] = v
        elif k == "deliveryTrigger":
            if v not in ("arrival", "valid"):
                raise ApiError("bad_request", "deliveryTrigger must be arrival or valid.", 400)
            clean[k] = v
        elif k == "autoEvidence":
            if v not in ("all", "first", "none"):
                raise ApiError("bad_request", "autoEvidence must be all, first or none.", 400)
            clean[k] = v
        elif k == "validSource":
            if v not in ("camview", "ops", "either"):
                raise ApiError("bad_request", "validSource must be camview, ops or either.", 400)
            clean[k] = v
        elif k == "clientContextLevels":
            clean[k] = [x for x in (v or []) if x in nomenclature.LEVELS]
        elif k == "escalation":
            steps = []
            for s in v or []:
                try:
                    steps.append({"afterMinutes": float(s["afterMinutes"]), "roleId": str(s["roleId"])})
                except (KeyError, TypeError, ValueError):
                    raise ApiError("bad_request", "Escalation steps need afterMinutes and roleId.", 400)
            clean[k] = steps
        else:
            if v in (None, ""):
                if default is None:
                    clean[k] = None
                    continue
                raise ApiError("bad_request", f"{k} is required.", 400)
            try:
                num = float(v)
            except (TypeError, ValueError):
                raise ApiError("bad_request", f"{k} must be a number.", 400)
            if num < 0:
                raise ApiError("bad_request", f"{k} must be positive.", 400)
            clean[k] = int(num) if float(num).is_integer() and not isinstance(default, float) else num
    pol = workflow.set_policy(clean, _user())
    import tickets
    tickets.enforce_operator_valid_only()        # switching the rule on withdraws what clients should not see
    return jsonify({"policy": pol})


def _monitored_projects():
    extras = {str(x) for x in (db.get_setting("extra_projects", []) or [])}
    auto = db.get_setting("auto_projects", {}) or {}
    master = {p["externalId"] for p in nomenclature.projects(include_auto=False)}
    out = []
    for pid in datasource.all_project_ids():
        src = "default" if pid == config.DEFAULT_PROJECT_ID else "auto" if pid in auto and pid in extras \
            else "extra" if pid in extras else "master"
        f = datasource.freshness(datasource.refresh(pid))
        out.append({**project_label(pid), "source": src, "inMasterData": pid in master,
                    "totalElements": f.get("totalElements"), "latestAlertAt": f.get("latestAlertAt"),
                    "quietHours": f.get("quietHours"), "state": f.get("state")})
    return out


@bp.route("/api/nomenclature/project-codes", methods=["PUT"])
@rbac.internal("settings.manage", "nomenclature.manage", any_of=True)
def nomenclature_project_codes():
    """Bulk mapping "project id, code[, name]" per line — matches every Camview project id to the exam's
    own project code (e.g. 2773, MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL)."""
    mapping = nomenclature.parse_code_mapping(body().get("text") or "")
    if not mapping:
        raise ApiError("bad_request", "No valid lines. Use one line per project: <project id>, <code>[, <name>].", 400)
    for pid, (code, name) in mapping.items():
        nomenclature.set_project_code(pid, code, name)
    datasource.reset()
    db.audit("nomenclature.project_codes", _user(), "nomenclature", "projects", None, {pid: c for pid, (c, _) in mapping.items()})
    return jsonify({"applied": [project_label(pid) for pid in mapping]})


@bp.route("/api/nomenclature/projects/<external_id>", methods=["PUT"])
@rbac.internal("settings.manage", "nomenclature.manage", any_of=True)
def nomenclature_project_code(external_id):
    """Sets the project's own code (e.g. MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL) and optional name.
    Camview only sends the numeric id; this code is shown everywhere instead."""
    pid = str(external_id).strip()
    if not pid.isdigit():
        raise ApiError("bad_request", "The project id is the numeric Camview projectId.", 400)
    b = body()
    out = nomenclature.set_project_code(pid, str(b.get("code") or ""), b.get("name") if "name" in b else None)
    datasource.reset()
    db.audit("nomenclature.project_code", _user(), "nomenclature", pid, None, {"code": out["code"], "name": out.get("name")})
    return jsonify(project_label(pid))


@bp.route("/api/nomenclature/projects/<external_id>", methods=["DELETE"])
@rbac.internal("settings.manage", "nomenclature.manage", any_of=True)
def nomenclature_project_delete(external_id):
    """Removes an OLD project that was built automatically from Camview data (its centres, rooms and
    cameras) and drops it from the extra ids. Imported master data and the configured default project
    are never deleted here."""
    pid = str(external_id).strip()
    if pid == config.DEFAULT_PROJECT_ID:
        raise ApiError("conflict", "This is the configured project (CAMVIEW_PROJECT_ID). Change it in Settings › "
                                   "Connection first.", 409)
    extras = [str(x) for x in (db.get_setting("extra_projects", []) or [])]
    if pid in extras:
        db.set_setting("extra_projects", [x for x in extras if x != pid])
    datasource.ignore_auto([pid])                     # removed by hand: auto-discovery never adds it back
    removed = nomenclature.delete_auto_project(pid)
    if removed is None and pid not in extras:
        raise ApiError("not_found", "No automatically built project with that id (imported master data is removed "
                                    "with a new import).", 404)
    datasource.reset()
    db.audit("settings.project_removed", _user(), "settings", "projects", None, {"projectId": pid, "nodes": removed or 0})
    return jsonify({"projectId": pid, "nodesRemoved": removed or 0, "extraProjects": [x for x in extras if x != pid]})


@bp.route("/api/settings/projects/discover", methods=["POST"])
@rbac.internal("settings.manage")
def settings_projects_discover():
    """Scans a range of project ids with the configured key: which projects exist, how many alarms
    they hold and when the newest one was raised — to find the project that is actually running."""
    b = body()
    try:
        start, end = int(b.get("from") or 1), int(b.get("to") or 2000)
    except (TypeError, ValueError):
        raise ApiError("bad_request", "from and to must be numbers.", 400)
    out = datasource.discover_projects(start, end)
    db.audit("settings.projects_discover", _user(), "settings", "projects", None,
             {"from": out["from"], "to": out["to"], "found": [p["projectId"] for p in out["projects"]]})
    return jsonify(out)


@bp.route("/api/settings/projects/auto-discover", methods=["POST"])
@rbac.internal("settings.manage")
def settings_projects_auto_discover():
    """Runs the automatic search for running projects now (in the background) instead of waiting."""
    if config.MODE != "live":
        raise ApiError("live_mode_not_configured", "Automatic project discovery reads the live Camview feed (demo mode is on).", 503)
    if not config.AUTO_DISCOVER:
        raise ApiError("conflict", "Automatic discovery is off (CAMVIEW_AUTO_DISCOVER=0).", 409)
    started = datasource.auto_discover(force=True)
    db.audit("settings.projects_auto_scan", _user(), "settings", "projects", None, {"started": started})
    return jsonify({"started": started, "autoDiscovery": datasource.auto_status()})


@bp.route("/api/settings/projects", methods=["PUT"])
@rbac.internal("settings.manage")
def settings_projects():
    """Extra project ids to monitor in live mode when master data isn't imported yet."""
    ids = [str(x).strip() for x in (body().get("projects") or []) if str(x).strip().isdigit()]
    before = {str(x) for x in (db.get_setting("extra_projects", []) or [])}
    datasource.ignore_auto(before - set(ids))          # removed by hand: auto-discovery never adds them back
    datasource.ignore_auto(ids, ignored=False)
    db.set_setting("extra_projects", ids)
    db.audit("settings.projects", _user(), "settings", "extra_projects", None, ids)
    return jsonify({"projects": ids})


_SETUP_FIELDS = {"apiUrl": "CAMVIEW_API_URL", "apiKey": "CAMVIEW_API_KEY", "projectId": "CAMVIEW_PROJECT_ID"}


@bp.route("/api/config/setup", methods=["POST"])
@rbac.internal("settings.manage")
def config_setup():
    """Saves the Camview connection to backend/.env; the key is never returned."""
    if not _setup_allowed():
        raise ApiError("forbidden", "Connection settings can only be changed from the computer running the backend "
                                    "(open http://localhost:5000 there), or by editing backend/.env.", 403)
    b = body()
    updates = {}
    for field, env_key in _SETUP_FIELDS.items():
        if b.get(field) is None:
            continue
        value = str(b[field]).strip()
        if field == "apiKey":
            if not value:
                continue
            if value.lower().startswith("bearer "):
                value = value[7:].strip()
        updates[env_key] = value
    if updates.get("CAMVIEW_API_URL"):
        validate_api_url(updates["CAMVIEW_API_URL"])
    if updates.get("CAMVIEW_PROJECT_ID") and not updates["CAMVIEW_PROJECT_ID"].isdigit():
        raise ApiError("bad_request", "Project ID must be a number.", 400)
    config.write_env(updates)
    import os
    os.environ.update(updates)
    config.apply()
    datasource.reset()
    db.audit("settings.connection", _user(), "settings", "connection", None,
             {k: ("***" if k == "CAMVIEW_API_KEY" else v) for k, v in updates.items()})
    return jsonify({"ok": True, "apiConfigured": bool(config.API_KEY), "apiUrl": config.API_URL})


@bp.route("/api/config/test", methods=["POST"])
@rbac.internal("settings.manage")
def config_test():
    b = body()
    started = time.time()
    raw, data = list_page({"projectId": b.get("projectId") or config.DEFAULT_PROJECT_ID, "page": 1, "size": 5})
    first_cam = raw[0].get("camera") if raw else None
    return jsonify({"ok": True, "apiUrl": config.API_URL, "latencyMs": int((time.time() - started) * 1000),
                    "totalElements": data.get("totalElements", len(raw)), "returned": len(raw),
                    "alarmTypesSeen": sorted({(i.get("alarm") or {}).get("alarmType") for i in raw} - {None}),
                    "cameraFields": sorted(first_cam) if isinstance(first_cam, dict) else []})


@bp.route("/api/config/raw", methods=["POST"])
@rbac.internal("settings.manage")
def config_raw():
    b = body()
    _, data = list_page({"projectId": b.get("projectId") or config.DEFAULT_PROJECT_ID, "page": 1,
                         "size": min(20, int(b.get("size") or 5))})
    return jsonify(data)


@bp.route("/api/config/mode", methods=["POST"])
@rbac.internal("settings.manage")
def config_mode():
    mode = body().get("mode")
    if mode not in ("demo", "live"):
        raise ApiError("bad_request", "mode must be demo or live.", 400)
    if mode == "live" and not config.API_KEY:
        raise ApiError("bad_request", "Configure the Camview API key before switching to live mode.", 400)
    db.audit("settings.mode", _user(), "settings", "mode", config.MODE, mode)
    config.write_env({"CAMVIEW_MODE": mode})
    import os
    os.environ["CAMVIEW_MODE"] = mode
    config.apply()
    import bootstrap
    bootstrap.run()
    session.clear()   # users differ between the demo and live databases
    return jsonify({"ok": True, "mode": config.MODE, "reloginRequired": True})


# ---------------------------------------------------------------------------
# Nomenclature, alarm types, priorities
# ---------------------------------------------------------------------------

@bp.route("/api/nomenclature/import", methods=["POST"])
@rbac.internal("nomenclature.manage")
def nomenclature_import():
    b = body()
    fmt = b.get("format")
    if fmt not in ("csv", "json"):
        raise ApiError("bad_request", "format must be csv or json.", 400)
    try:
        result = nomenclature.import_data(b.get("data") if fmt == "json" else str(b.get("data") or ""), fmt,
                                          replace=b.get("replace", True))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ApiError("bad_request", f"Could not import: {e}", 400)
    db.audit("nomenclature.import", _user(), "nomenclature", "tree", None, result["counts"],
             details={"rows": result["rows"], "errors": len(result["errors"])})
    return jsonify(result)


@bp.route("/api/nomenclature/nodes/<path:node_id>", methods=["PUT"])
@rbac.internal("nomenclature.manage")
def nomenclature_rename(node_id):
    name = str(body().get("name") or "").strip()[:160]
    out = nomenclature.rename(node_id, name)
    if not out:
        raise ApiError("not_found", "Node not found.", 404)
    db.audit("nomenclature.rename", _user(), "nomenclature", node_id, None, name)
    return jsonify(out)


@bp.route("/api/camera-health/status")
@rbac.internal("alarm.view", "settings.view", any_of=True)
def camera_health_status():
    import health
    return jsonify(health.status())


@bp.route("/api/camera-health/cameras")
@rbac.internal("alarm.view")
def camera_health_cameras():
    """Health of every reporting camera in the user's projects — problems first. Real source only."""
    import health
    allowed = set(datasource.allowed_projects(_user()))
    rows = [r for r in db.rows("SELECT project_id, camera_id FROM camera_health") if r["project_id"] in allowed]
    views = health.lookup((r["project_id"], r["camera_id"]) for r in rows)
    # what Camview itself says about each camera (centre, city, sub-location, camera number): latest record per camera
    seen = {}
    for p in {r["project_id"] for r in rows}:
        for a in datasource.refresh(p).items:
            k = (str(a.get("projectId")), str(a.get("cameraId")))
            if k not in seen or (a.get("lastInstance") or "") > (seen[k].get("lastInstance") or ""):
                seen[k] = a
    items = []
    for (p, c), v in views.items():
        ctx = nomenclature.resolve(p, c)
        cam, centre = ctx.get("camera") or {}, ctx.get("centre") or {}
        a = seen.get((str(p), str(c))) or {}
        items.append({"projectId": p, "project": project_label(p)["code"], "cameraId": c, "health": v, "mapped": ctx["mapped"],
                      "code": cam.get("code") or a.get("cameraNumber") or f"CAM-{c}",
                      "name": cam.get("name") if not cam.get("unmapped") else None,
                      "centre": centre.get("code") or a.get("centreCode"), "centreName": a.get("centreName") or centre.get("name"),
                      "city": a.get("cameraCity"), "state": a.get("cameraState"), "subLocation": a.get("cameraSubLocation"),
                      "cameraNumber": a.get("cameraNumber"), "lastAlertAt": a.get("lastInstance"),
                      "label": datasource.location_label(ctx, p, c, a.get("centreCode"), a.get("cameraNumber"),
                                                         a.get("cameraSubLocation"))})
    rank = lambda h: (0 if h["camera"]["state"] == "offline" else 1 if "FRAME_SYNC_FAILED" in h["conditions"]
                      else 2 if "CAMERA_ONLINE_NO_RECORDING" in h["conditions"]
                      else 3 if "HEARTBEAT_STALE" in h["conditions"] else 4)
    items.sort(key=lambda x: (rank(x["health"]), x["projectId"], x["label"]))
    cond = [x["health"]["conditions"] for x in items]
    return jsonify({"status": health.status(), "items": items[:2000],
                    "counts": {"offline": sum("CAMERA_OFFLINE" in c for c in cond),
                               "online": sum("CAMERA_ONLINE" in c for c in cond),
                               "syncFailed": sum("FRAME_SYNC_FAILED" in c for c in cond),
                               "onlineNoRecording": sum("CAMERA_ONLINE_NO_RECORDING" in c for c in cond),
                               "recording": sum("RECORDING_ACTIVE" in c for c in cond),
                               "stale": sum("HEARTBEAT_STALE" in c for c in cond)}})


@bp.route("/api/camera-health/ingest", methods=["POST"])
def camera_health_ingest():
    """Real health source → Command Center. Machine-to-machine: authorised by the
    server-side CAMVIEW_HEALTH_INGEST_TOKEN (Authorization header), not a session."""
    import hmac
    import health
    token = health.ingest_token()
    given = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not token or not given or not hmac.compare_digest(token, given):
        raise ApiError("unauthorized", "Health ingest token missing or wrong.", 401)
    b = body()
    saved, events = health.ingest(b.get("cameras") if isinstance(b, dict) else b, source=str(b.get("source") or "push")[:40]
                                  if isinstance(b, dict) else "push")
    return jsonify({"saved": saved, "events": events})


@bp.route("/api/nomenclature/export.csv")
@rbac.internal("nomenclature.view")
def nomenclature_export():
    return Response(nomenclature.export_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="nomenclature.csv"'})


@bp.route("/api/dictionary")
@rbac.internal("nomenclature.view")
def dictionary():
    return jsonify({"alarmTypes": db.rows("SELECT * FROM alarm_types ORDER BY id"),
                    "priorities": db.rows("SELECT * FROM priority_levels ORDER BY rank"),
                    "levels": nomenclature.LEVEL_LABELS})


@bp.route("/api/dictionary/alarm-types", methods=["PUT"])
@rbac.internal("nomenclature.manage")
def dictionary_types():
    items = body().get("items") or []
    with db.connect() as conn:
        for t in items:
            try:
                tid = int(t["id"])
            except (KeyError, TypeError, ValueError):
                raise ApiError("bad_request", "Every alarm type needs a numeric id.", 400)
            if not str(t.get("name") or "").strip():
                raise ApiError("bad_request", f"Alarm type {tid} needs a name.", 400)
            policy = t.get("clientSharePolicy") or t.get("client_share_policy") or "allowed"
            if policy not in ("allowed", "never", "review"):
                raise ApiError("bad_request", "Client share policy must be allowed, review or never.", 400)
            conn.execute("INSERT INTO alarm_types (id, name, description, icon, severity, workflow, client_share_policy) "
                         "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                         "description=excluded.description, icon=excluded.icon, severity=excluded.severity, "
                         "workflow=excluded.workflow, client_share_policy=excluded.client_share_policy",
                         (tid, str(t["name"]).strip(), t.get("description"), t.get("icon"), t.get("severity"),
                          t.get("workflow"), policy))
        if body().get("delete"):
            for tid in body()["delete"]:
                conn.execute("DELETE FROM alarm_types WHERE id=?", (int(tid),))
    db.audit("dictionary.alarm_types", _user(), "dictionary", "alarm_types", None, {"count": len(items)})
    datasource.reset()
    return dictionary()


@bp.route("/api/dictionary/priorities", methods=["PUT"])
@rbac.internal("nomenclature.manage")
def dictionary_priorities():
    items = body().get("items") or []
    with db.connect() as conn:
        conn.execute("DELETE FROM priority_levels")
        for p in items:
            try:
                conn.execute("INSERT INTO priority_levels (value, label, rank, confirmed) VALUES (?,?,?,?)",
                             (int(p["value"]), str(p["label"]).strip().lower(), int(p["rank"]),
                              1 if p.get("confirmed") else 0))
            except (KeyError, TypeError, ValueError):
                raise ApiError("bad_request", "Each priority needs value, label and rank.", 400)
    db.audit("dictionary.priorities", _user(), "dictionary", "priorities", None, items)
    datasource.reset()
    return dictionary()


# ---------------------------------------------------------------------------
# Alert rules & schedules
# ---------------------------------------------------------------------------

RULE_STATUSES = ("active", "draft", "disabled")
RULE_FIELDS = ("name", "status", "conditions", "scope", "severity", "recipients", "channel", "escalation", "action",
               "template")


def _rule_out(r):
    return {**r, "conditions": db.jload(r["conditions"], []), "recipients": db.jload(r["recipients"], []),
            "escalation": db.jload(r["escalation"], None), "enabled": bool(r["enabled"]),
            "status": r.get("status") or ("active" if r["enabled"] else "disabled"), "version": r.get("version") or 1,
            "versions": db.one("SELECT COUNT(*) AS n FROM rule_versions WHERE rule_id=?", (r["id"],))["n"]}


@bp.route("/api/alert-rules")
@rbac.internal("alert.view")
def rules_list():
    groups = [{**g, "roles": db.jload(g["roles"], []), "userIds": db.jload(g["user_ids"], [])}
              for g in db.rows("SELECT * FROM recipient_groups ORDER BY name")]
    return jsonify({"items": [_rule_out(r) for r in db.rows("SELECT * FROM alert_rules ORDER BY id")],
                    "fields": {k: v[0] for k, v in intelligence.FIELDS.items()}, "ops": intelligence.OPS,
                    "channels": notify.CHANNELS, "templates": notify.TEMPLATES,
                    "scopes": ["camera", "room", "centre", "tc", "project", "global"],
                    "severities": ["critical", "high", "warning", "info"], "statuses": RULE_STATUSES,
                    "actions": {"notify": "Notify recipients", "queue": "Add matching alarms to My Work"},
                    "groups": groups,
                    "roles": db.rows("SELECT id, name FROM roles WHERE audience='internal' ORDER BY name")})


def _rule_payload(b):
    name = str(b.get("name") or "").strip()
    if not name:
        raise ApiError("bad_request", "Rule name is required.", 400)
    conds = b.get("conditions") or []
    if not conds:
        raise ApiError("bad_request", "At least one condition is required.", 400)
    for c in conds:
        if c.get("field") not in intelligence.FIELDS or c.get("op") not in intelligence.OPS:
            raise ApiError("bad_request", "Invalid condition.", 400)
    channel = b.get("channel") or "in_app"
    if not notify.CHANNELS.get(channel):
        raise ApiError("bad_request", f"The '{channel}' channel is not integrated yet — use in-app.", 400)
    status = b.get("status") or ("active" if b.get("enabled", True) else "disabled")
    if status not in RULE_STATUSES:
        raise ApiError("bad_request", "Status must be active, draft or disabled.", 400)
    action = b.get("action") or "notify"
    if action not in ("notify", "queue"):
        raise ApiError("bad_request", "Action must be notify or queue.", 400)
    return {"name": name, "status": status, "conditions": conds, "scope": b.get("scope") or "camera",
            "severity": b.get("severity") or "warning", "recipients": b.get("recipients") or [], "channel": channel,
            "escalation": b.get("escalation") or None, "action": action, "template": b.get("template")}


def _save_version(rid, version, payload, user, summary):
    db.execute("INSERT INTO rule_versions (rule_id, version, status, snapshot, summary, changed_by, changed_at) "
               "VALUES (?,?,?,?,?,?,?)", (rid, version, payload["status"], db.jdump(payload), summary, user["name"],
                                          db.now_iso()))


def _write_rule(rid, p, user, version, creating=False):
    vals = (p["name"], 1 if p["status"] == "active" else 0, "alarm", db.jdump(p["conditions"]), p["scope"],
            p["severity"], db.jdump(p["recipients"]), p["channel"],
            db.jdump(p["escalation"]) if p["escalation"] else None, p["action"], p["template"], p["status"], version,
            db.now_iso(), user["name"])
    if creating:
        return db.execute("INSERT INTO alert_rules (name, enabled, event, conditions, scope, severity, recipients, channel, "
                          "escalation, action, template, status, version, updated_at, updated_by, created_by, created_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*vals, user["name"], db.now_iso()))
    db.execute("UPDATE alert_rules SET name=?, enabled=?, event=?, conditions=?, scope=?, severity=?, recipients=?, "
               "channel=?, escalation=?, action=?, template=?, status=?, version=?, updated_at=?, updated_by=? "
               "WHERE id=?", (*vals, rid))
    return rid


@bp.route("/api/alert-rules", methods=["POST"])
@rbac.internal("alert.manage")
def rules_create():
    user = _user()
    p = _rule_payload(body())
    rid = _write_rule(None, p, user, 1, creating=True)
    _save_version(rid, 1, p, user, "Created")
    db.audit("alert_rule.create", user, "alert_rule", rid, None, p["name"], details={"status": p["status"]})
    return jsonify({"id": rid, "version": 1})


@bp.route("/api/alert-rules/<int:rid>", methods=["PUT", "DELETE"])
@rbac.internal("alert.manage")
def rules_update(rid):
    user = _user()
    existing = db.one("SELECT * FROM alert_rules WHERE id=?", (rid,))
    if not existing:
        raise ApiError("not_found", "Rule not found.", 404)
    if request.method == "DELETE":
        db.execute("DELETE FROM alert_rules WHERE id=?", (rid,))
        db.audit("alert_rule.delete", user, "alert_rule", rid, existing["name"], None)
        return jsonify({"ok": True})
    b = body()
    if set(b) <= {"status"}:          # status-only change (activate / draft / disable)
        old = _rule_out(existing)
        b = {**{k: old[k] for k in RULE_FIELDS}, "status": b.get("status")}
    p = _rule_payload(b)
    old = _rule_out(existing)
    changed = [k for k in RULE_FIELDS if old.get(k) != p.get(k)]
    if not changed:
        return jsonify({"ok": True, "version": old["version"], "changed": []})
    version = (existing.get("version") or 1) + 1
    _write_rule(rid, p, user, version)
    _save_version(rid, version, p, user, "Changed: " + ", ".join(changed))
    db.audit("alert_rule.update", user, "alert_rule", rid, {k: old.get(k) for k in changed},
             {k: p.get(k) for k in changed}, details={"version": version})
    return jsonify({"ok": True, "version": version, "changed": changed})


@bp.route("/api/alert-rules/<int:rid>/versions")
@rbac.internal("alert.view")
def rules_versions(rid):
    return jsonify({"items": [{"version": v["version"], "status": v["status"], "summary": v["summary"],
                               "changedBy": v["changed_by"], "changedAt": v["changed_at"],
                               "snapshot": db.jload(v["snapshot"], {})}
                              for v in db.rows("SELECT * FROM rule_versions WHERE rule_id=? ORDER BY version DESC", (rid,))]})


@bp.route("/api/alert-rules/test", methods=["POST"])
@rbac.internal("alert.view")
def rules_test():
    """Dry-run (never changes production behaviour): which alarms in the chosen
    period would the rule match?"""
    from datetime import datetime, timedelta, timezone
    user = _user()
    pid = project_param(user)
    b = body()
    try:
        hours = max(1, min(24 * 31, int(b.get("hours") or 24)))
    except (TypeError, ValueError):
        raise ApiError("bad_request", "hours must be a number.", 400)
    end = datetime.now(timezone.utc)
    items, truncated = datasource.fetch_range(user, pid, end - timedelta(hours=hours), end)
    rule = {"conditions": b.get("conditions") or []}
    for c in rule["conditions"]:
        if c.get("field") not in intelligence.FIELDS or c.get("op") not in intelligence.OPS:
            raise ApiError("bad_request", "Invalid condition.", 400)
    hits = [a for a in items if intelligence.rule_matches(rule, a)]
    from routes_ops import slim
    return jsonify({"matches": len(hits), "scanned": len(items), "hours": hours, "truncated": truncated,
                    "sample": [a["alarmId"] for a in hits[:10]],
                    "items": [slim(a) for a in sorted(hits, key=lambda a: a.get("lastInstance") or "", reverse=True)[:50]],
                    "explanation": [intelligence.describe_condition(c) for c in rule["conditions"]]})


# ---------------------------------------------------------------------------
# Recipient groups (Control Room, Supervisor Team, Management, …)
# ---------------------------------------------------------------------------

def _group_payload(b):
    name = str(b.get("name") or "").strip()
    if not name:
        raise ApiError("bad_request", "Group name is required.", 400)
    roles = [r["id"] for r in db.rows("SELECT id FROM roles WHERE audience='internal'")]
    bad = [r for r in (b.get("roles") or []) if r not in roles]
    if bad:
        raise ApiError("bad_request", "Recipient groups may only contain internal roles.", 400)
    users = [u["id"] for u in db.rows("SELECT u.id FROM users u JOIN roles r ON r.id=u.role_id WHERE r.audience='internal'")]
    if [u for u in (b.get("userIds") or []) if u not in users]:
        raise ApiError("bad_request", "Recipient groups may only contain internal users.", 400)
    return name, str(b.get("description") or ""), db.jdump(b.get("roles") or []), db.jdump(b.get("userIds") or [])


@bp.route("/api/recipient-groups")
@rbac.internal("alert.view")
def groups_list():
    return jsonify({"items": [{**g, "roles": db.jload(g["roles"], []), "userIds": db.jload(g["user_ids"], [])}
                              for g in db.rows("SELECT * FROM recipient_groups ORDER BY name")]})


@bp.route("/api/recipient-groups", methods=["POST"])
@rbac.internal("alert.manage")
def groups_create():
    vals = _group_payload(body())
    gid = db.execute("INSERT INTO recipient_groups (name, description, roles, user_ids, created_at) VALUES (?,?,?,?,?)",
                     (*vals, db.now_iso()))
    db.audit("recipient_group.create", _user(), "recipient_group", gid, None, vals[0])
    return jsonify({"id": gid})


@bp.route("/api/recipient-groups/<int:gid>", methods=["PUT", "DELETE"])
@rbac.internal("alert.manage")
def groups_update(gid):
    if request.method == "DELETE":
        db.execute("DELETE FROM recipient_groups WHERE id=?", (gid,))
        db.audit("recipient_group.delete", _user(), "recipient_group", gid)
        return jsonify({"ok": True})
    vals = _group_payload(body())
    db.execute("UPDATE recipient_groups SET name=?, description=?, roles=?, user_ids=? WHERE id=?", (*vals, gid))
    db.audit("recipient_group.update", _user(), "recipient_group", gid, None, vals[0])
    return jsonify({"ok": True})


@bp.route("/api/schedules")
@rbac.internal("alert.view")
def schedules_list():
    return jsonify({"items": [{**s, "recipients": db.jload(s["recipients"], []), "enabled": bool(s["enabled"])}
                              for s in db.rows("SELECT * FROM schedules ORDER BY at")],
                    "phases": notify.PHASES, "templates": notify.TEMPLATES, "channels": notify.CHANNELS})


@bp.route("/api/schedules", methods=["POST"])
@rbac.internal("alert.manage")
def schedules_create():
    b = body()
    if b.get("phase") not in notify.PHASES or not b.get("at") or not str(b.get("name") or "").strip():
        raise ApiError("bad_request", "Name, phase and time are required.", 400)
    sid = db.execute("INSERT INTO schedules (name, phase, at, template, message, recipients, enabled, created_by, created_at) "
                     "VALUES (?,?,?,?,?,?,1,?,?)",
                     (str(b["name"]).strip(), b["phase"], b["at"], b.get("template"), b.get("message"),
                      db.jdump(b.get("recipients") or []), _user()["name"], db.now_iso()))
    db.audit("schedule.create", _user(), "schedule", sid, None, b["name"])
    return jsonify({"id": sid})


@bp.route("/api/schedules/<int:sid>", methods=["DELETE", "PUT"])
@rbac.internal("alert.manage")
def schedules_update(sid):
    if request.method == "DELETE":
        db.execute("DELETE FROM schedules WHERE id=?", (sid,))
    else:
        db.execute("UPDATE schedules SET enabled=? WHERE id=?", (1 if body().get("enabled") else 0, sid))
    db.audit("schedule.update", _user(), "schedule", sid, None, request.method)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@bp.route("/api/audit")
@rbac.internal("audit.view")
def audit_list():
    args = request.args
    total, items = db.audit_query({k: args.get(k) for k in ("action", "user", "resourceType", "resourceId",
                                                             "projectId", "clientId", "since", "until")},
                                  limit=min(500, int(args.get("size") or 100)),
                                  offset=max(0, (int(args.get("page") or 1) - 1) * int(args.get("size") or 100)))
    return jsonify({"items": items, "total": total})


# ---------------------------------------------------------------------------
# Report Center
# ---------------------------------------------------------------------------

def _build_report(user, b):
    rtype = b.get("type")
    if rtype not in reports.REPORT_TYPES:
        raise ApiError("bad_request", "Unknown report type.", 400)
    _, audience = reports.REPORT_TYPES[rtype]
    filters = b.get("filters") or {}
    if audience == "client":
        if "report.share" not in user["permissions"] and "alarm.publish" not in user["permissions"]:
            raise ApiError("forbidden", "You don't have permission to build client reports.", 403)
        client = db.one("SELECT id FROM clients WHERE id=?", (b.get("clientId"),))
        if not client:
            raise ApiError("bad_request", "Choose a client for a client report.", 400)
        return reports.build_client(rtype, user, client["id"], filters), client["id"]
    pid = str(b.get("projectId") or project_param(user))
    if not rbac.project_allowed(user, pid):
        raise ApiError("not_found", "Project unavailable.", 404)
    items, _ = datasource.working_set(user, pid)
    alarm = None
    if rtype == "investigation":
        alarm = datasource.find_alarm(user, str(b.get("alarmId") or ""), pid)
        if not alarm:
            raise ApiError("not_found", "Alarm not available.", 404)
    p = project_label(pid)
    return reports.build_internal(rtype, user, items, filters, f"{p['code']} — {p['name']}", alarm), None


@bp.route("/api/reports")
@rbac.internal("report.view")
def reports_list():
    user = _user()
    rows = db.rows("SELECT r.id, r.title, r.type, r.audience, r.client_id, c.name AS client_name, r.generated_by_name, "
                   "r.generated_at, r.shared_with_client FROM reports r LEFT JOIN clients c ON c.id=r.client_id "
                   "ORDER BY r.id DESC LIMIT 200")
    import exams as exams_mod
    return jsonify({"items": rows, "types": {k: {"title": v[0], "audience": v[1]} for k, v in reports.REPORT_TYPES.items()},
                    "clients": db.rows("SELECT id, name FROM clients WHERE status='active' ORDER BY name"),
                    "exams": [{"id": e["id"], "code": e["code"], "name": e["name"], "clientId": e["clientId"],
                               "clientName": e["clientName"], "projectIds": e["projectIds"]} for e in exams_mod.list_exams()],
                    "canGenerate": "report.generate" in user["permissions"],
                    "canShare": "report.share" in user["permissions"]})


@bp.route("/api/reports/preview", methods=["POST"])
@rbac.internal("report.generate")
def reports_preview():
    report, _ = _build_report(_user(), body())
    return jsonify(report)


@bp.route("/api/reports", methods=["POST"])
@rbac.internal("report.generate")
def reports_generate():
    user = _user()
    report, client_id = _build_report(user, body())
    rid = reports.save(report, user, client_id)
    return jsonify({"id": rid, "report": report})


@bp.route("/api/reports/<int:rid>")
@rbac.internal("report.view")
def reports_get(rid):
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        raise ApiError("not_found", "Report not found.", 404)
    return jsonify({"id": rid, "sharedWithClient": bool(r["shared_with_client"]), **(db.jload(r["data"], {}) or {})})


@bp.route("/api/reports/<int:rid>/csv")
@rbac.internal("report.export")
def reports_csv(rid):
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        raise ApiError("not_found", "Report not found.", 404)
    db.audit("report.export", _user(), "report", rid)
    return Response(reports.to_csv(db.jload(r["data"], {})), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="camview-report-{rid}.csv"'})


@bp.route("/api/reports/<int:rid>/share", methods=["POST"])
@rbac.internal("report.share")
def reports_share(rid):
    user = _user()
    r = db.one("SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        raise ApiError("not_found", "Report not found.", 404)
    if r["audience"] != "client" or not r["client_id"]:
        raise ApiError("bad_request", "Only client reports (built from the client dataset) can be shared.", 400)
    share = bool(body().get("share", True))
    db.execute("UPDATE reports SET shared_with_client=? WHERE id=?", (1 if share else 0, rid))
    db.audit("report.share" if share else "report.unshare", user, "report", rid, client_id=r["client_id"])
    if share:
        notify.to_client(r["client_id"], "client", f"Report available: {r['title']}",
                         "A new report has been shared with you.", f"#/client/reports/{rid}", dedupe=f"report:{rid}")
    return jsonify({"ok": True, "shared": share})
