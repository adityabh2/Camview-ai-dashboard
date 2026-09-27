"""
Client logins per exam, and exam / client names from the project code.

* A client login can be limited to some of its client's exams: it sees only VALID alerts of those exams.
* It can never be limited to another client's exam.
* Sign-in names may be plain usernames (like the built-in "admin"), not only emails.
* "Use these names": client and exam named from the project code, nothing typed by hand.
"""

import pytest

import db
import nomenclature
import workflow
from conftest import Client


@pytest.fixture(autouse=True)
def rule_on(isolated_db):
    workflow.set_policy({"clientsSeeOperatorValidOnly": True, "deliveryMode": "automatic"}, None)
    yield


def _client_with_two_exams(admin):
    exams = admin.get("/api/exams")[1]["items"]
    by_client = {}
    for e in exams:
        if e.get("clientId"):
            by_client.setdefault(e["clientId"], []).append(e)
    cid, two = next((c, xs) for c, xs in by_client.items() if len(xs) >= 2)
    return cid, two


def _new_login(app, admin, cid, exam_ids, email="board.control"):
    s, r = admin.post("/api/users", {"name": "Board control", "email": email, "roleId": "client_user", "clientId": cid,
                                     "password": "control-pass-123",
                                     "scopes": [{"type": "exam", "value": x} for x in exam_ids]})
    assert s == 200, r
    return Client(app).login(email, "control-pass-123")


def _valid_in_exam(supervisor, exam_id):
    items = supervisor.get(f"/api/queue?status=pending&exam={exam_id}&size=50&range=all")[1]["items"]
    a = next(x for x in items if x["client"] and x["alarmType"] != 8)
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid", "clientId": a["client"]["id"]})
    assert s == 200 and r["delivery"]["status"] == "delivered", r
    return a["alarmId"]


def test_exam_limited_login_sees_only_its_exam(app, admin, supervisor):
    cid, (e1, e2) = _client_with_two_exams(admin)
    a1, a2 = _valid_in_exam(supervisor, e1["id"]), _valid_in_exam(supervisor, e2["id"])
    only1 = _new_login(app, admin, cid, [e1["id"]])
    ids = {x["alarmId"] for x in only1.get("/api/client/alerts")[1]["items"]}
    assert a1 in ids and a2 not in ids
    assert only1.get(f"/api/client/alerts/{a2}")[0] == 404
    assert [x["id"] for x in only1.get("/api/client/overview")[1]["exams"]] == [e1["id"]]
    both = _new_login(app, admin, cid, [], email="board.all")
    ids = {x["alarmId"] for x in both.get("/api/client/alerts")[1]["items"]}
    assert {a1, a2} <= ids


def test_login_cannot_be_limited_to_another_clients_exam(admin):
    cid, _ = _client_with_two_exams(admin)
    other = next(e for e in admin.get("/api/exams")[1]["items"] if e.get("clientId") and e["clientId"] != cid)
    s, r = admin.post("/api/users", {"name": "X", "email": "x.user", "roleId": "client_user", "clientId": cid,
                                     "scopes": [{"type": "exam", "value": other["id"]}]})
    assert s == 400
    s, r = admin.post("/api/users", {"name": "Y", "email": "y.user", "roleId": "client_user", "clientId": cid,
                                     "scopes": [{"type": "centre", "value": "CTR-0701"}]})
    assert s == 400


def test_exams_list_shows_client_logins(app, admin):
    cid, (e1, e2) = _client_with_two_exams(admin)
    _new_login(app, admin, cid, [e1["id"]], email="only.first")
    items = {e["id"]: e for e in admin.get("/api/exams")[1]["items"]}
    assert "only.first" in [u["email"] for u in items[e1["id"]]["logins"]]
    assert "only.first" not in [u["email"] for u in items[e2["id"]]["logins"]]


def test_bad_usernames_are_refused(admin):
    cid, _ = _client_with_two_exams(admin)
    for bad in ("a", "has space", "x@y"):
        assert admin.post("/api/users", {"name": "Z", "email": bad, "roleId": "client_user", "clientId": cid})[0] == 400


def test_names_from_the_project_code(admin):
    e = admin.get("/api/exams")[1]["items"][0]
    pid = e["projectIds"][0]
    nomenclature.set_project_code(pid, "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL")
    e = next(x for x in admin.get("/api/exams")[1]["items"] if x["id"] == e["id"])
    assert e["fromCode"]["client"] == "MPESB" and e["fromCode"]["exam"] == "MPESB/G2SG4-CRT" and e["fromCode"]["date"] == "2026-09-22"
    s, r = admin.post(f"/api/exams/{e['id']}/names-from-code", {"renameClient": True})
    assert s == 200, r
    assert r["exam"]["name"] == "MPESB/G2SG4-CRT" and r["exam"]["startDate"] == "2026-09-22"
    assert db.one("SELECT name FROM clients WHERE id=?", (e["clientId"],))["name"] == "MPESB"
    assert db.one("SELECT 1 FROM audit_events WHERE action='client.rename'")


def test_manage_client_login_password_status_and_exams(app, admin):
    cid, (e1, e2) = _client_with_two_exams(admin)
    _new_login(app, admin, cid, [e1["id"]], email="desk.one")
    c = next(x for x in admin.get("/api/clients")[1]["items"] if x["id"] == cid)
    u = next(x for x in c["users"] if x["email"] == "desk.one")
    assert u["exams"] == [e1["id"]] and u["roleId"] == "client_user" and {e["id"] for e in c["exams"]} >= {e1["id"], e2["id"]}
    # reset password: old one stops working, new one works
    assert admin.put(f"/api/users/{u['id']}", {"password": "brand-new-pass-1"})[0] == 200
    assert Client(app).c.post("/api/auth/login", json={"email": "desk.one", "password": "control-pass-123"}).status_code != 200
    Client(app).login("desk.one", "brand-new-pass-1")
    # widen to every exam (no exam scope), then disable
    assert admin.put(f"/api/users/{u['id']}", {"scopes": []})[0] == 200
    users = next(x for x in admin.get("/api/clients")[1]["items"] if x["id"] == cid)["users"]
    assert next(x for x in users if x["email"] == "desk.one")["exams"] == []
    assert admin.put(f"/api/users/{u['id']}", {"status": "disabled"})[0] == 200
    assert Client(app).c.post("/api/auth/login", json={"email": "desk.one", "password": "brand-new-pass-1"}).status_code != 200
    # too-short password refused
    assert admin.put(f"/api/users/{u['id']}", {"password": "abcd"})[0] == 400      # below the 5-character minimum
