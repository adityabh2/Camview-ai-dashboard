"""
rbac.py — Role-Based Access Control, enforced on the server.

* PERMISSIONS: the catalog (every permission the product knows about).
* DEFAULT_ROLES: seeded once; roles and their permissions are editable in
  Roles & Permissions (stored in the DB), so nothing is hard-coded in the UI.
* Audience: every role is either `internal` or `client`. Internal endpoints
  refuse client users *regardless of permissions* and vice versa — a second,
  independent wall around internal data.
* Scope: internal users can be limited to projects / TECs / TCs / centres /
  cameras; client users are limited to their client's assigned projects and
  only ever see published records (see workflow.build_client_visible_alarm).
"""

import functools

from flask import g, request, session

import db
import nomenclature
from camview_client import ApiError

PERMISSIONS = {
    # operations
    "dashboard.view": "View the Command Center",
    "live.view": "View Live Operations",
    "work.view": "Use My Work queues",
    "alarm.view": "View alarms",
    "alarm.investigate": "Open investigation workspaces",
    "alarm.validate": "Mark alarms valid",
    "alarm.invalidate": "Mark alarms invalid",
    "alarm.exception": "Mark alarms as exception",
    "alarm.assign": "Assign alarms to users",
    "alarm.comment": "Add internal notes",
    "alarm.export": "Export alarm data",
    "alarm.approve": "Approve alarms for client sharing",
    "alarm.publish": "Publish (share) alarms with clients",
    "alarm.withdraw": "Withdraw shared alarms",
    "evidence.view": "View evidence",
    "evidence.download": "Download evidence",
    "camera.view": "View cameras",
    "analytics.view": "View analytics",
    "history.view": "View alarm history",
    "report.view": "View reports",
    "report.generate": "Generate reports",
    "report.export": "Export reports",
    "report.share": "Share reports with clients",
    "nomenclature.view": "View nomenclature / context",
    "nomenclature.manage": "Import and edit nomenclature, alarm types, priorities",
    "project.view": "View projects",
    "alert.view": "View intelligent alerts",
    "alert.manage": "Manage alert rules, schedules and escalation",
    "shift.view": "View shift control",
    "shift.handover": "Create shift handovers",
    "presentation.view": "Use presentation mode",
    "notification.view": "Receive notifications",
    "audit.view": "View the audit trail",
    "user.view": "View users",
    "user.manage": "Create and edit users",
    "role.view": "View roles",
    "role.manage": "Edit roles and permissions",
    "client.view": "View clients",
    "client.manage": "Create and edit clients",
    "settings.view": "View settings",
    "settings.manage": "Change connection, workflow and data settings",
    # client portal
    "client.portal": "Use the client portal",
    "client.acknowledge": "Acknowledge shared alerts",
    "client.evidence": "View shared evidence",
    "client.report.view": "View reports shared with the client",
    "client.analytics": "View client analytics",
}

INTERNAL_ONLY = {p for p in PERMISSIONS if not p.startswith("client.") or p in ("client.view", "client.manage")}
CLIENT_PERMS = {"client.portal", "client.acknowledge", "client.evidence", "client.report.view",
                "client.analytics", "notification.view", "presentation.view"}

_VIEW = ["dashboard.view", "live.view", "work.view", "alarm.view", "alarm.investigate", "evidence.view",
         "camera.view", "history.view", "nomenclature.view", "project.view", "alert.view", "notification.view",
         "shift.view", "presentation.view"]

DEFAULT_ROLES = {
    "super_admin": ("Super Admin", "Full access, including role management.", "internal",
                    sorted(INTERNAL_ONLY)),
    "admin": ("Administrator", "Manages users, clients, settings and master data.", "internal",
              sorted(INTERNAL_ONLY)),
    "manager": ("Manager", "Executive overview, analytics, reports and client sharing decisions.", "internal",
                _VIEW + ["analytics.view", "report.view", "report.generate", "report.export", "report.share",
                         "alarm.approve", "alarm.publish", "alarm.withdraw", "alarm.export", "audit.view",
                         "client.view", "user.view", "evidence.download"]),
    "supervisor": ("Supervisor", "Reviews, approves and shares alarms; manages the shift.", "internal",
                   _VIEW + ["alarm.validate", "alarm.invalidate", "alarm.exception", "alarm.assign",
                            "alarm.comment", "alarm.approve", "alarm.publish", "alarm.withdraw", "alarm.export",
                            "evidence.download", "analytics.view", "report.view", "report.generate",
                            "report.export", "shift.handover", "client.view", "audit.view"]),
    "operator": ("Operator", "Monitors, investigates and validates alarms.", "internal",
                 _VIEW + ["alarm.validate", "alarm.invalidate", "alarm.exception", "alarm.comment",
                          "shift.handover"]),
    "investigator": ("Investigator", "Deep-dives into alarms and evidence.", "internal",
                     _VIEW + ["alarm.comment", "evidence.download", "analytics.view", "report.view"]),
    "client_admin": ("Client Admin", "Client-side lead: shared alerts, evidence, reports, acknowledgement.",
                     "client", sorted(CLIENT_PERMS)),
    "client_user": ("Client User", "Views and acknowledges shared alerts.", "client",
                    ["client.portal", "client.acknowledge", "client.evidence", "client.report.view",
                     "notification.view"]),
    "client_viewer": ("Client Viewer", "Read-only view of shared alerts.", "client",
                      ["client.portal", "client.evidence", "notification.view"]),
}

SCOPE_TYPES = ("global", "project", "tc", "centre", "room", "camera")
CLIENT_SCOPE_TYPES = ("exam",)          # a client login can be limited to some of its client's exams


def seed_roles():
    """Creates the default roles once. Existing roles are never overwritten."""
    with db.connect() as conn:
        for rid, (name, desc, audience, perms) in DEFAULT_ROLES.items():
            if conn.execute("SELECT 1 FROM roles WHERE id = ?", (rid,)).fetchone():
                continue
            conn.execute("INSERT INTO roles (id, name, description, audience, is_system) VALUES (?,?,?,?,1)",
                         (rid, name, desc, audience))
            conn.executemany("INSERT INTO role_permissions (role_id, permission) VALUES (?, ?)",
                             [(rid, p) for p in perms])


def load_user(user_id):
    """User with role, permissions, scopes and client assignment — or None."""
    u = db.one("SELECT u.*, r.name AS role_name, r.audience FROM users u JOIN roles r ON r.id = u.role_id "
               "WHERE u.id = ?", (user_id,))
    if not u or u["status"] != "active":
        return None
    perms = {r["permission"] for r in db.rows("SELECT permission FROM role_permissions WHERE role_id = ?",
                                              (u["role_id"],))}
    # audience wall: a role can only ever hold permissions of its audience
    perms = perms & (CLIENT_PERMS if u["audience"] == "client" else INTERNAL_ONLY)
    scopes = {}
    for s in db.rows("SELECT scope_type, scope_value FROM user_scopes WHERE user_id = ?", (user_id,)):
        scopes.setdefault(s["scope_type"], set()).add(str(s["scope_value"]))
    client = None
    client_projects = set()
    if u["client_id"]:
        client = db.one("SELECT id, name, status FROM clients WHERE id = ?", (u["client_id"],))
        if client and client["status"] == "active":
            client_projects = {r["project_id"] for r in
                               db.rows("SELECT project_id FROM client_projects WHERE client_id = ?", (client["id"],))}
        else:
            perms = set()  # inactive client: no access at all
    return {
        "id": u["id"], "name": u["name"], "email": u["email"], "roleId": u["role_id"], "roleName": u["role_name"],
        "audience": u["audience"], "permissions": perms, "scopes": scopes, "clientId": u["client_id"],
        "client": client, "clientProjects": client_projects, "isDemo": bool(u["is_demo"]),
        "pwChangedAt": u.get("pw_changed_at"),
        "_scope_projects": nomenclature.projects_for_scope(scopes),
    }


def public_user(user):
    return {
        "id": user["id"], "name": user["name"], "email": user["email"], "roleId": user["roleId"],
        "roleName": user["roleName"], "audience": user["audience"], "permissions": sorted(user["permissions"]),
        "scopes": {k: sorted(v) for k, v in user["scopes"].items()},
        "client": user["client"], "clientProjects": sorted(user["clientProjects"]),
    }


def current_user():
    if "user" not in g:
        uid = session.get("uid")
        u = load_user(uid) if uid else None
        if u and session.get("pwv") != u.get("pwChangedAt"):
            u = None                     # password changed or reset since this session signed in: sign in again
        g.user = u
    return g.user


def has(user, perm):
    return bool(user) and perm in user["permissions"]


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def is_global(user):
    return user["audience"] == "internal" and ("global" in user["scopes"] or not user["scopes"])


def project_allowed(user, project_id):
    if project_id in (None, ""):
        return False
    pid = str(project_id)
    if user["audience"] == "client":
        return pid in user["clientProjects"]
    if is_global(user):
        return True
    if pid in user["scopes"].get("project", set()):
        return True
    # a narrower scope (TC/centre/room/camera) inside the project also grants the project
    return bool(user["scopes"].keys() & {"tc", "centre", "room", "camera"}) and \
        pid in user.get("_scope_projects", set())


def alarm_in_scope(user, alarm):
    """Internal scope check on an enriched alarm (with resolved context)."""
    if user["audience"] != "internal":
        return False
    if is_global(user):
        return True
    s = user["scopes"]
    if str(alarm.get("projectId")) in s.get("project", set()):
        return True
    ctx = alarm.get("context") or {}
    for level in ("tc", "centre", "room", "camera"):
        node = ctx.get(level) or {}
        if not node or node.get("unmapped"):
            continue
        allowed = s.get(level, set())
        # room numbers repeat across centres, so rooms are scoped by their unique node id only
        if node.get("id") in allowed or (level != "room" and node.get("code") in allowed):
            return True
    if str(alarm.get("cameraId")) in s.get("camera", set()):
        return True
    return False


# ---------------------------------------------------------------------------
# Decorators — every API route goes through one of these
# ---------------------------------------------------------------------------

def _require(audience, perms, any_of=False):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                raise ApiError("unauthenticated", "Please sign in.", 401)
            if audience and user["audience"] != audience:
                # don't reveal what exists on the other side of the wall
                raise ApiError("not_found", "Resource unavailable.", 404)
            if perms:
                ok = any(p in user["permissions"] for p in perms) if any_of else \
                    all(p in user["permissions"] for p in perms)
                if not ok:
                    raise ApiError("forbidden", "You don't have permission to do that.", 403)
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.is_json:
                # JSON-only writes + same-origin cookies = no cross-site form posts (CSRF)
                raise ApiError("bad_request", "Requests must be JSON.", 415)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def internal(*perms, any_of=False):
    return _require("internal", perms, any_of)


def client(*perms, any_of=False):
    return _require("client", perms, any_of)


def authenticated(fn):
    return _require(None, (), False)(fn)
