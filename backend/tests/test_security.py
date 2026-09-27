"""
Security tests (spec §102, §103, §127): the server — not the UI — enforces
authentication, RBAC, scope, audience and the client data firewall.
"""

import sqlite3

import pytest

import db
from conftest import as_user


def shared_alarm(client_id="client-a"):
    return db.one("SELECT alarm_id FROM publications WHERE client_id=? AND status='shared' LIMIT 1", (client_id,))["alarm_id"]


def unpublished_alarm(project="7"):
    """A project alarm with no publication at all."""
    r = db.one("SELECT alarm_id FROM ops_review WHERE status='marked_valid' AND alarm_id NOT IN "
               "(SELECT alarm_id FROM publications) LIMIT 1")
    return r["alarm_id"]


# --------------------------------------------------------------------------- authentication

@pytest.mark.parametrize("path", ["/api/overview?projectId=7", "/api/alarms?projectId=7", "/api/users",
                                  "/api/client/overview", "/api/client/alerts", "/api/notifications"])
def test_unauthenticated_requests_are_rejected(client, path):
    status, body = client.get(path)
    assert status == 401
    assert body["error"] == "unauthenticated"


def test_wrong_password_and_rate_limit(client):
    for _ in range(8):
        s, b = client.post("/api/auth/login", {"email": "supervisor@demo.camview", "password": "nope"})
        assert s == 401 and "incorrect" in b["message"]
    s, _ = client.post("/api/auth/login", {"email": "supervisor@demo.camview", "password": "demo"})
    assert s == 429


def test_session_endpoint_never_exposes_the_api_key(client):
    import config
    config.API_KEY = "super-secret-key"
    s, body = client.get("/api/auth/session")
    assert "super-secret-key" not in str(body)


def test_security_headers(client):
    r = client.c.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_non_json_writes_are_rejected(supervisor):
    r = supervisor.c.post("/api/sharing/request", data="alarmId=x", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 415


# --------------------------------------------------------------------------- audience wall

def test_client_cannot_call_internal_endpoints(client_a):
    aid = shared_alarm()
    for path in (f"/api/alarms/{aid}?projectId=7", "/api/alarms?projectId=7", "/api/overview?projectId=7",
                 "/api/export/alarms.csv?projectId=7", "/api/audit", "/api/users"):
        s, body = client_a.get(path)
        assert s == 404, path            # indistinguishable from "doesn't exist"


def test_internal_user_cannot_use_client_portal(supervisor):
    s, _ = supervisor.get("/api/client/alerts")
    assert s == 404


# --------------------------------------------------------------------------- client firewall

def test_client_cannot_open_unpublished_alarm(client_a):
    s, body = client_a.get(f"/api/client/alerts/{unpublished_alarm()}")
    assert s == 404 and body["message"] == "Alert not available."


def test_client_cannot_open_another_clients_alarm(client_a):
    s, body = client_a.get(f"/api/client/alerts/{shared_alarm('client-b')}")
    assert s == 404 and body["message"] == "Alert not available."


def test_hidden_and_missing_records_look_identical(client_a):
    withdrawn = db.one("SELECT alarm_id FROM publications WHERE client_id='client-a' AND status='withdrawn'")["alarm_id"]
    s1, b1 = client_a.get(f"/api/client/alerts/{withdrawn}")
    s2, b2 = client_a.get("/api/client/alerts/ALM-DOES-NOT-EXIST")
    assert (s1, b1) == (s2, b2) == (404, {"error": "not_found", "message": "Alert not available."})


def test_client_payload_contains_no_internal_fields(client_a):
    aid = shared_alarm()
    db.execute("INSERT INTO notes (alarm_id, kind, body, author_name, created_at) VALUES (?,?,?,?,?)",
               (aid, "internal", "TOP-SECRET-INTERNAL-NOTE", "x", db.now_iso()))
    s, body = client_a.get(f"/api/client/alerts/{aid}")
    assert s == 200
    text = str(body)
    for forbidden in ("TOP-SECRET-INTERNAL-NOTE", "lastActionType", "alarmState", "review", "workflow", "assignment",
                      "picsum.photos", "cameraId", "requestedBy", "approvedBy", "validatedBy"):
        assert forbidden not in text, forbidden
    assert all(e["url"].startswith("/api/client/evidence/") for e in body["evidence"])


def test_client_list_only_contains_shared_alerts(client_a):
    s, body = client_a.get("/api/client/alerts")
    shared = {r["alarm_id"] for r in db.rows("SELECT alarm_id FROM publications WHERE client_id='client-a' AND status='shared'")}
    assert {a["alarmId"] for a in body["items"]} == shared
    assert len(shared) == 8


def test_client_analytics_only_use_visible_alarms(client_a):
    s, body = client_a.get("/api/client/analytics")
    assert body["total"] == 8
    assert sum(d["count"] for d in body["priorityDistribution"]) == 8


def test_unshared_evidence_is_not_served(client_a, monkeypatch):
    aid = shared_alarm()
    pub = db.one("SELECT evidence FROM publications WHERE alarm_id=? AND client_id='client-a'", (aid,))
    items = db.jload(pub["evidence"])
    hidden = [e for e in items if not e["shared"]]
    served = []

    class FakeResp:
        status_code = 200
        headers = {"Content-Type": "image/jpeg"}

        def iter_content(self, n):
            yield b"img"

    import routes_client
    monkeypatch.setattr(routes_client.requests, "get", lambda url, **kw: served.append(url) or FakeResp())
    ok = next(e for e in items if e["shared"])
    r = client_a.c.get(f"/api/client/evidence/{aid}/{ok['kind']}/{ok['index']}")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml" and b"DEMO EVIDENCE" in r.data
    if hidden:
        r = client_a.c.get(f"/api/client/evidence/{aid}/{hidden[0]['kind']}/{hidden[0]['index']}")
        assert r.status_code == 404
    assert served == []          # demo frames are rendered locally; nothing is fetched from outside


def test_demo_evidence_frames_are_internal_and_demo_only(supervisor, client_a, client):
    a = supervisor.get("/api/alarms?projectId=7&quick=evidence&size=1")[1]["items"][0]
    url = supervisor.get(f"/api/alarms/{a['alarmId']}?projectId=7")[1]["evidenceItems"][0]["url"]
    assert url.startswith("/demo-evidence/")
    r = supervisor.c.get(url)
    assert r.status_code == 200 and b"SYNTHETIC FRAME" in r.data and b"<script" not in r.data
    assert client_a.c.get(url).status_code == 404          # clients only get evidence through the firewall proxy
    assert client.c.get(url).status_code == 401            # not signed in


def test_viewer_without_evidence_permission_gets_no_evidence(app, admin):
    admin.put("/api/roles/client_viewer", {"permissions": ["client.portal", "notification.view"]})
    viewer = as_user(app, "viewer@client-a.demo")
    s, body = viewer.get(f"/api/client/alerts/{shared_alarm()}")
    assert body["evidence"] == []
    s, _ = viewer.get(f"/api/client/evidence/{shared_alarm()}/image/0")
    assert s == 403


def test_removing_project_assignment_revokes_access(app, admin, client_a):
    assert len(client_a.get("/api/client/alerts")[1]["items"]) == 8
    s, _ = admin.put("/api/clients/client-a", {"projects": []})
    assert s == 200
    fresh = as_user(app, "client.admin@client-a.demo")
    assert fresh.get("/api/client/alerts")[1]["items"] == []


def test_inactive_client_loses_all_access(app, admin):
    admin.put("/api/clients/client-a", {"status": "inactive"})
    s, body = app.test_client().post("/api/auth/login", json={"email": "client.admin@client-a.demo", "password": "demo"}), None
    assert s.status_code == 403


def test_client_role_can_never_hold_internal_permissions(app, admin):
    s, body = admin.put("/api/roles/client_admin", {"permissions": ["client.portal", "alarm.view", "user.manage"]})
    assert "alarm.view" not in body["permissions"] and "user.manage" not in body["permissions"]


# --------------------------------------------------------------------------- RBAC + scope

def test_operator_cannot_export(operator):
    s, _ = operator.get("/api/export/alarms.csv?projectId=7")
    assert s == 403


def test_operator_cannot_publish_or_approve(operator):
    aid = db.one("SELECT alarm_id FROM publications WHERE status='approved' AND client_id='client-a'")["alarm_id"]
    s, _ = operator.post("/api/sharing/publish", {"alarmId": aid, "clientId": "client-a", "clientSummary": "x",
                                                 "evidence": [], "context": [], "confirm": True})
    assert s == 403
    s, _ = operator.post("/api/sharing/approve", {"alarmId": aid, "clientId": "client-a"})
    assert s == 403


def test_operator_cannot_download_evidence(operator):
    aid = operator.get("/api/alarms?projectId=7&quick=evidence&size=1")[1]["items"][0]["alarmId"]
    s, _ = operator.post("/api/evidence/log", {"alarmId": aid, "kind": "image", "index": 0, "download": True})
    assert s == 403


def test_role_change_requires_permission(supervisor, admin):
    s, _ = supervisor.put("/api/users/u-op", {"roleId": "admin"})
    assert s == 403
    s, _ = admin.put("/api/users/u-op", {"roleId": "supervisor"})
    assert s == 200


def test_cannot_disable_last_super_admin_or_self(admin):
    s, body = admin.put("/api/users/u-admin", {"status": "disabled"})
    assert s == 400


def test_project_scope_is_enforced(operator):
    # operator is scoped to project 7
    s, _ = operator.get("/api/alarms?projectId=12")
    assert s == 404
    p12 = db.one("SELECT alarm_id FROM publications WHERE client_id='client-b' LIMIT 1")["alarm_id"]
    s, body = operator.get(f"/api/alarms/{p12}")
    assert s == 404 and body["message"] == "Alarm not available."


def test_tc_scope_limits_alarms(app):
    sana = as_user(app, "operator.tec04@demo.camview")
    s, body = sana.get("/api/alarms?projectId=7&size=200")
    assert s == 200 and body["items"]
    assert all(a["context"]["path"][1]["code"] == "TC-0711" for a in body["items"])


def test_setup_requires_settings_manage(supervisor):
    s, _ = supervisor.post("/api/config/setup", {"apiKey": "x"})
    assert s == 403


def test_setup_rejects_foreign_api_host(admin):
    s, body = admin.post("/api/config/setup", {"apiUrl": "https://evil.example.com/alarms/listAlarms", "apiKey": "k"})
    assert s == 400


# --------------------------------------------------------------------------- audit

def test_audit_trail_is_append_only():
    db.audit("test.event", None, "x", "1")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE audit_events SET action='tampered'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM audit_events")


def test_audit_view_requires_permission(operator, admin):
    assert operator.get("/api/audit")[0] == 403
    s, body = admin.get("/api/audit?action=auth.")
    assert s == 200 and body["items"][0]["action"].startswith("auth.")
