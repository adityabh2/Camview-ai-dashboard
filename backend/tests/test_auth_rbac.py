"""
Sign-in, passwords, deleting logins and client delivery of operator-VALID alerts.

* Minimum password length is CAMVIEW_PASSWORD_MIN (default 5) everywhere.
* A password reset signs the account out of every browser; changing your own keeps this browser signed in.
* Logins can be deleted (never yourself, never the last Super Admin; Super Admins only by role managers).
* An operator-VALID alert that could not be delivered (no client mapped yet) goes out once a client is mapped.
* Administrators can preview exactly what a client sees.
"""

import pytest

import config
import db
import tickets
import workflow
from conftest import Client


@pytest.fixture(autouse=True)
def rule_on(isolated_db):
    workflow.set_policy({"clientsSeeOperatorValidOnly": True, "deliveryMode": "automatic"}, None)
    yield


def _client_id(admin):
    return next(c["id"] for c in admin.get("/api/clients")[1]["items"] if c["projects"])


def _login(admin, cid, email="desk.a", password="pass5"):
    s, r = admin.post("/api/users", {"name": "Desk", "email": email, "roleId": "client_user", "clientId": cid, "password": password})
    assert s == 200, r
    return r["id"]


def test_minimum_password_length_is_five(app, admin):
    assert config.PASSWORD_MIN == 5
    cid = _client_id(admin)
    assert admin.post("/api/users", {"name": "X", "email": "four.chars", "roleId": "client_user", "clientId": cid, "password": "abcd"})[0] == 400
    _login(admin, cid, "five.chars", "abcde")
    c = Client(app).login("five.chars", "abcde")
    assert c.post("/api/auth/password", {"current": "abcde", "new": "wxyz"})[0] == 400
    assert c.post("/api/auth/password", {"current": "abcde", "new": "vwxyz"})[0] == 200
    assert admin.get("/api/auth/session")[1]["passwordMin"] == 5


def test_reset_signs_the_login_out_everywhere(app, admin):
    uid = _login(admin, _client_id(admin))
    one, two = Client(app).login("desk.a", "pass5"), Client(app).login("desk.a", "pass5")
    assert one.get("/api/client/alerts")[0] == 200
    assert admin.put(f"/api/users/{uid}", {"password": "newpass"})[0] == 200
    assert one.get("/api/client/alerts")[0] == 401 and two.get("/api/client/alerts")[0] == 401
    Client(app).login("desk.a", "newpass")


def test_own_change_keeps_this_browser_and_signs_out_the_others(app, admin):
    _login(admin, _client_id(admin))
    here, there = Client(app).login("desk.a", "pass5"), Client(app).login("desk.a", "pass5")
    assert here.post("/api/auth/password", {"current": "pass5", "new": "mine5"})[0] == 200
    assert here.get("/api/client/alerts")[0] == 200
    assert there.get("/api/client/alerts")[0] == 401


def test_delete_login(app, admin, supervisor):
    uid = _login(admin, _client_id(admin))
    signed_in = Client(app).login("desk.a", "pass5")
    assert supervisor.delete(f"/api/users/{uid}")[0] in (403, 404)
    s, r = admin.delete(f"/api/users/{uid}")
    assert s == 200, r
    assert signed_in.get("/api/client/alerts")[0] == 401
    assert Client(app).c.post("/api/auth/login", json={"email": "desk.a", "password": "pass5"}).status_code == 401
    assert db.one("SELECT 1 FROM audit_events WHERE action='user.delete' AND resource_id=?", (uid,))
    assert admin.delete(f"/api/users/{uid}")[0] == 404


def test_cannot_delete_yourself_or_the_last_super_admin(admin):
    me = admin.get("/api/auth/session")[1]["user"]["id"]
    assert admin.delete(f"/api/users/{me}")[0] == 400
    others = [u for u in admin.get("/api/users")[1]["items"] if u["roleId"] == "super_admin" and u["id"] != me]
    for u in others:                                   # leave the signed-in admin as the only Super Admin …
        assert admin.delete(f"/api/users/{u['id']}")[0] == 200
    db.execute("INSERT INTO users (id, name, email, password_hash, role_id, status, created_at) VALUES "
               "('u-sa2','SA2','sa2','x','super_admin','disabled',?)", (db.now_iso(),))
    # … a disabled Super Admin can go; deleting the signed-in one (the last active) is refused as "yourself"
    assert admin.delete("/api/users/u-sa2")[0] == 200


def test_operator_valid_waiting_for_a_client_is_delivered_once_possible(app, admin, supervisor):
    items = supervisor.get("/api/queue?status=pending&size=200&projectId=7")[1]["items"]
    a = next(x for x in items if x["client"] and x["alarmType"] != 8)
    cid = a["client"]["id"]
    db.execute("UPDATE clients SET status='inactive' WHERE id=?", (cid,))       # the client cannot receive now
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    assert s == 200 and r["delivery"]["status"] == "not_deliverable", r
    db.execute("UPDATE clients SET status='active' WHERE id=?", (cid,))         # fixed: the waiting VALID goes out
    assert tickets.deliver_pending_valid() >= 1
    assert tickets.for_alarm(a["alarmId"])["deliveryStatus"] == "delivered"
    pv = admin.get(f"/api/clients/{cid}/preview")[1]
    assert a["alarmId"] in {x["alarmId"] for x in pv["items"]} and pv["rule"] is True
    assert tickets.deliver_pending_valid() == 0                                 # idempotent


def test_preview_is_internal_only(admin, client_a):
    cid = _client_id(admin)
    assert client_a.get(f"/api/clients/{cid}/preview")[0] == 404
    assert admin.get("/api/clients/nope/preview")[0] == 404


def test_valid_review_on_an_auto_share_ticket_is_delivered(supervisor):
    """Live case: auto-share opened the ticket (no validator); an operator later marked the alert VALID."""
    items = supervisor.get("/api/queue?status=pending&size=200&projectId=7")[1]["items"]
    a = next(x for x in items if x["client"] and x["alarmType"] != 8)
    cid = a["client"]["id"]
    db.execute("UPDATE clients SET status='inactive' WHERE id=?", (cid,))
    assert supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})[0] == 200
    db.execute("UPDATE tickets SET validated_by_id=NULL, validated_by='Auto-share (automatic delivery)' WHERE alarm_id=?",
               (a["alarmId"],))
    db.execute("UPDATE clients SET status='active' WHERE id=?", (cid,))
    assert tickets.deliver_pending_valid() >= 1
    t = tickets.for_alarm(a["alarmId"])
    assert t["deliveryStatus"] == "delivered" and t["validatedById"]
