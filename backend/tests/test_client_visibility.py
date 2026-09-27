"""
Production rule: a client sees ONLY alerts the backend operations team marked VALID.

* Camview's own VALID status, delivery on arrival and automatic sharing never reach a client.
* Anything delivered before the rule is withdrawn (publication) and its automatic ticket cancelled.
* Administrators still see every decision (valid / invalid / exception / pending) internally.
* Camera online / offline events are counted in the Cameras KPI only: never decided, never a ticket.
"""

import pytest

import datasource
import db
import tickets
import workflow
from camview_client import ApiError


@pytest.fixture(autouse=True)
def rule_on(isolated_db, monkeypatch):
    monkeypatch.setitem(workflow.POLICY_DEFAULTS, "clientsSeeOperatorValidOnly", True)
    workflow.set_policy({"clientsSeeOperatorValidOnly": True}, None)     # the seeded database stores every key
    yield


def _deliverable(supervisor):
    items = supervisor.get("/api/queue?status=pending&size=200&projectId=7")[1]["items"]
    return [x for x in items if x["client"] and x["alarmType"] != 8]


def test_policy_forces_delivery_on_operator_valid_only():
    workflow.set_policy({"deliveryTrigger": "arrival", "autoShareValid": True, "manualReview": False}, None)
    p = workflow.policy()
    assert (p["deliveryTrigger"], p["autoShareValid"], p["manualReview"]) == ("valid", False, True)


def test_operator_valid_reaches_the_client(admin, supervisor, client_a):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    a = _deliverable(supervisor)[0]
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    assert s == 200 and r["delivery"]["status"] == "delivered"
    ids = {x["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"]}
    assert a["alarmId"] in ids
    assert client_a.get(f"/api/client/alerts/{a['alarmId']}")[0] == 200


def test_delivery_without_operator_valid_is_invisible_and_withdrawn(admin, supervisor, client_a):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    a, b = _deliverable(supervisor)[:2]
    supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    # an old automatic delivery of `b` (e.g. "on arrival"), no operator decision
    pub = db.one("SELECT * FROM publications WHERE alarm_id=?", (a["alarmId"],))
    db.execute("INSERT OR REPLACE INTO publications (alarm_id, client_id, status, project_id, shared_by, shared_at, snapshot, "
               "evidence, share_context) VALUES (?,?,?,?,?,?,?,?,?)",
               (b["alarmId"], pub["client_id"], "shared", pub["project_id"], "Auto-share (automatic delivery)", db.now_iso(),
                pub["snapshot"].replace(a["alarmId"], b["alarmId"]), pub["evidence"], pub["share_context"]))
    db.execute("UPDATE ops_review SET status='unreviewed' WHERE alarm_id=?", (b["alarmId"],))
    # the firewall hides it at once …
    ids = {x["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"]}
    assert a["alarmId"] in ids and b["alarmId"] not in ids
    assert client_a.get(f"/api/client/alerts/{b['alarmId']}")[0] == 404
    # … and the clean-up withdraws it for good, leaving the operator-VALID one
    withdrawn, _ = tickets.enforce_operator_valid_only()
    assert withdrawn >= 1
    assert db.one("SELECT status FROM publications WHERE alarm_id=?", (b["alarmId"],))["status"] == "withdrawn"
    assert db.one("SELECT status FROM publications WHERE alarm_id=?", (a["alarmId"],))["status"] == "shared"
    assert db.one("SELECT 1 FROM audit_events WHERE action='delivery.policy_enforced'")


def test_camview_valid_is_never_auto_shared(supervisor):
    items, _ = datasource.working_set({"audience": "internal", "scopes": {}, "permissions": [], "id": "x"}, "7") \
        if False else (datasource.refresh("7").items, None)
    valid = [dict(x, lastActionType=1) for x in items[:5]]
    before = db.one("SELECT COUNT(*) AS n FROM publications WHERE status='shared'")["n"]
    assert tickets.auto_sync(valid) == (0, 0)
    assert db.one("SELECT COUNT(*) AS n FROM publications WHERE status='shared'")["n"] == before


def test_admin_still_sees_every_decision(admin, supervisor):
    ok = _deliverable(supervisor)
    supervisor.post(f"/api/queue/{ok[0]['alarmId']}/decide", {"result": "invalid"})
    supervisor.post(f"/api/queue/{ok[1]['alarmId']}/decide", {"result": "exception"})
    for status, aid in (("invalid", ok[0]["alarmId"]), ("exception", ok[1]["alarmId"])):
        ids = {x["alarmId"] for x in admin.get(f"/api/queue?status={status}&size=200&range=all")[1]["items"]}
        assert aid in ids


def test_camera_status_events_never_become_tickets(supervisor):
    ev = datasource._normalize([{"alarm": {"alarmId": "EV-9101", "alarmType": 10, "cameraId": "9101", "projectId": 7,
                                           "priority": 1, "lastActionType": 0, "firstInstance": "2026-09-26T10:00:00Z",
                                           "lastInstance": "2026-09-26T10:00:00Z",
                                           "alarmMetadata": {"status": "OFFLINE", "reason": "Camera Offline"}},
                                 "camera": {"id": 9101, "frameSyncStatus": "OFFLINE"}}])[0]
    user = {"id": "u", "name": "Op", "permissions": ["alarm.validate", "alarm.invalidate", "alarm.exception"]}
    with pytest.raises(ApiError) as e:
        tickets.decide(ev, user, "valid")
    assert e.value.status == 409
    assert not db.one("SELECT 1 FROM tickets WHERE alarm_id='EV-9101'")


def test_decided_by_filter_separates_team_and_camview(supervisor):
    a = _deliverable(supervisor)[0]
    supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "invalid"})
    team = supervisor.get("/api/queue?status=all&by=team&size=200&range=all")[1]
    assert a["alarmId"] in {x["alarmId"] for x in team["items"]}
    assert all(x["decisionSource"] == "operator" for x in team["items"])
    cam = supervisor.get("/api/queue?status=all&by=camview&size=200&range=all")[1]
    assert all(x["decisionSource"] != "operator" and x["decision"] != "pending" for x in cam["items"])
    assert team["counts"]["all"] == len(team["items"]) or team["counts"]["all"] >= len(team["items"])
