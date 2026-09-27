"""
Workflow tests: validation, the client-sharing lifecycle, four-eyes control,
eligibility rules, notifications and the demo story (spec §128/§129).
"""

import db
import workflow
from conftest import as_user


def pending_alarm(c, project="7", **extra):
    """A pending, unreviewed, mapped alarm with evidence and a shareable type."""
    s, body = c.get(f"/api/alarms?projectId={project}&quick=pending,evidence&size=200")
    for a in body["items"]:
        if a["alarmType"] != 8 and a["context"]["mapped"] and a["visibility"]["state"] == "internal":
            return a
    raise AssertionError("no suitable alarm")


def test_demo_story_end_to_end(app, operator, supervisor, client_a):
    a = pending_alarm(operator)
    aid = a["alarmId"]

    # 1-6: operator investigates and validates
    s, d = operator.get(f"/api/alarms/{aid}?projectId=7")
    assert s == 200 and d["alarm"]["context"]["mapped"] and d["alarm"]["context"]["tc"]
    s, r = operator.post(f"/api/alarms/{aid}/review", {"action": "mark_valid", "note": "Confirmed on camera"})
    assert s == 200 and r["status"] == "marked_valid"

    # VALID ≠ SHARED: still internal, client cannot see it
    s, d = operator.get(f"/api/alarms/{aid}?projectId=7")
    assert d["alarm"]["workflowState"] == "READY_FOR_CLIENT"
    assert d["alarm"]["visibility"]["state"] == "internal"
    assert client_a.get(f"/api/client/alerts/{aid}")[0] == 404

    # 7: operator requests client review
    s, r = operator.post("/api/sharing/request", {"alarmId": aid, "clientId": "client-a", "projectId": "7"})
    assert s == 200 and r["publication"]["status"] == "ready_for_review"
    # supervisor was notified (real event)
    s, n = supervisor.get("/api/notifications")
    assert any(aid in x["title"] and x["category"] == "approval" for x in n["items"])

    # 8-9: supervisor approves (different person -> four-eyes OK)
    s, r = supervisor.post("/api/sharing/approve", {"alarmId": aid, "clientId": "client-a", "projectId": "7"})
    assert s == 200 and r["publication"]["status"] == "approved"
    assert client_a.get(f"/api/client/alerts/{aid}")[0] == 404        # approved is still not visible

    # 10-11: client-safe preview
    s, pv = supervisor.get(f"/api/sharing/preview?alarmId={aid}&clientId=client-a&projectId=7")
    assert s == 200 and pv["eligibility"]["publish"]["eligible"]
    assert any("Internal notes" in t for t in pv["internalNotShared"])

    # publishing requires explicit confirmation and a summary
    body = {"alarmId": aid, "clientId": "client-a", "projectId": "7", "clientSummary": "Phone detected in Room 101.",
            "evidence": ["image:0"], "context": ["project", "centre"]}
    assert supervisor.post("/api/sharing/publish", body)[0] == 400
    assert supervisor.post("/api/sharing/publish", {**body, "confirm": True, "clientSummary": " "})[0] == 400
    s, r = supervisor.post("/api/sharing/publish", {**body, "confirm": True})
    assert s == 200 and r["publication"]["status"] == "shared"

    # 14-16: client sees ONLY approved information
    s, v = client_a.get(f"/api/client/alerts/{aid}")
    assert s == 200
    assert v["summary"] == "Phone detected in Room 101."
    assert [c["level"] for c in v["context"]] == ["project", "centre"]
    assert len(v["evidence"]) == 1 and v["evidence"][0]["index"] == 0
    assert "Confirmed on camera" not in str(v)                        # operator note stays internal
    assert v["viewedAt"]

    # client acknowledges; internal side sees it
    s, v = client_a.post(f"/api/client/alerts/{aid}/acknowledge", {"comment": "Superintendent informed"})
    assert s == 200 and v["acknowledgedAt"]
    s, d = supervisor.get(f"/api/alarms/{aid}?projectId=7")
    assert d["alarm"]["workflowState"] == "CLIENT_ACKNOWLEDGED"
    actions = [e["action"] for e in d["audit"]]
    for a_ in ("mark_valid", "share.request", "share.approve", "share.publish", "client.view", "client.acknowledge"):
        assert a_ in actions, a_

    # withdraw -> client loses access immediately
    assert supervisor.post("/api/sharing/withdraw", {"alarmId": aid, "clientId": "client-a", "reason": "x"})[0] == 400
    s, r = supervisor.post("/api/sharing/withdraw", {"alarmId": aid, "clientId": "client-a", "reason": "Duplicate",
                                                     "confirm": True})
    assert s == 200 and r["publication"]["status"] == "withdrawn"
    assert client_a.get(f"/api/client/alerts/{aid}")[0] == 404


def test_four_eyes_blocks_self_approval(supervisor):
    a = pending_alarm(supervisor)
    aid = a["alarmId"]
    supervisor.post(f"/api/alarms/{aid}/review", {"action": "mark_valid"})
    supervisor.post("/api/sharing/request", {"alarmId": aid, "clientId": "client-a"})
    s, body = supervisor.post("/api/sharing/approve", {"alarmId": aid, "clientId": "client-a"})
    assert s == 409 and "Four-eyes" in body["message"]


def test_four_eyes_can_be_turned_off(supervisor, admin):
    admin.put("/api/settings/policy", {"fourEyes": False})
    aid = pending_alarm(supervisor)["alarmId"]
    supervisor.post(f"/api/alarms/{aid}/review", {"action": "mark_valid"})
    supervisor.post("/api/sharing/request", {"alarmId": aid, "clientId": "client-a"})
    assert supervisor.post("/api/sharing/approve", {"alarmId": aid, "clientId": "client-a"})[0] == 200


def test_publish_requires_approval_when_two_step(operator, supervisor):
    aid = pending_alarm(operator)["alarmId"]
    operator.post(f"/api/alarms/{aid}/review", {"action": "mark_valid"})
    operator.post("/api/sharing/request", {"alarmId": aid, "clientId": "client-a"})
    s, body = supervisor.post("/api/sharing/publish", {"alarmId": aid, "clientId": "client-a", "clientSummary": "x",
                                                       "evidence": [], "context": [], "confirm": True})
    assert s == 409 and "approval" in body["message"].lower()


def test_ops_invalid_blocks_sharing_even_if_camview_valid(operator):
    s, body = operator.get("/api/alarms?projectId=7&lastActionType=1&size=200")
    a = next(x for x in body["items"] if x["visibility"]["state"] == "internal" and x["alarmType"] != 8
             and x["review"]["status"] == "unreviewed")
    operator.post(f"/api/alarms/{a['alarmId']}/review", {"action": "mark_invalid"})
    s, body = operator.post("/api/sharing/eligibility", {"alarmIds": [a["alarmId"]], "clientId": "client-a"})
    checks = {c["id"]: c["ok"] for c in body["results"][0]["checks"]}
    assert checks["valid"] is False and body["eligible"] == 0


def test_alarm_type_policy_never_blocks_sharing(operator):
    s, body = operator.get("/api/alarms?projectId=7&alarmType=8&size=50")
    aid = body["items"][0]["alarmId"]
    operator.post(f"/api/alarms/{aid}/review", {"action": "mark_valid"})
    s, body = operator.post("/api/sharing/eligibility", {"alarmIds": [aid], "clientId": "client-a"})
    assert {c["id"]: c["ok"] for c in body["results"][0]["checks"]}["type_policy"] is False


def test_bulk_eligibility_and_controlled_bulk_request(operator):
    s, body = operator.get("/api/alarms?projectId=7&quick=pending&size=6")
    pending = [a["alarmId"] for a in body["items"]][:2]
    valid = []
    for a in operator.get("/api/alarms?projectId=7&quick=pending,evidence&size=200")[1]["items"]:
        if a["alarmType"] != 8 and a["alarmId"] not in pending and len(valid) < 3:
            operator.post(f"/api/alarms/{a['alarmId']}/review", {"action": "mark_valid"})
            valid.append(a["alarmId"])
    s, e = operator.post("/api/sharing/eligibility", {"alarmIds": pending + valid, "clientId": "client-a"})
    assert e["selected"] == 5 and e["eligible"] == 3 and e["notEligible"] == 2
    assert all(not r["eligible"] and any(not c["ok"] for c in r["checks"]) for r in e["results"] if r["alarmId"] in pending)
    s, r = operator.post("/api/sharing/bulk-request", {"alarmIds": pending + valid, "clientId": "client-a"})
    assert sorted(r["requested"]) == sorted(valid) and len(r["skipped"]) == 2


def test_client_from_other_project_is_not_eligible(operator):
    aid = pending_alarm(operator)["alarmId"]
    operator.post(f"/api/alarms/{aid}/review", {"action": "mark_valid"})
    s, e = operator.post("/api/sharing/eligibility", {"alarmIds": [aid], "clientId": "client-b"})
    assert {c["id"]: c["ok"] for c in e["results"][0]["checks"]}["client_project"] is False


def test_reopen_is_blocked_while_shared(supervisor):
    aid = db.one("SELECT alarm_id FROM publications WHERE status='shared' AND client_id='client-a'")["alarm_id"]
    s, body = supervisor.post(f"/api/alarms/{aid}/review", {"action": "reopen"})
    assert s == 409


def test_review_permissions_per_action(app, operator):
    inv = as_user(app, "investigator@demo.camview")
    aid = pending_alarm(operator)["alarmId"]
    assert inv.post(f"/api/alarms/{aid}/review", {"action": "mark_valid"})[0] == 403
    assert inv.post(f"/api/alarms/{aid}/review", {"action": "acknowledge"})[0] == 200


def test_internal_notes_and_supervisor_notes(operator, supervisor):
    aid = pending_alarm(operator)["alarmId"]
    assert operator.post(f"/api/alarms/{aid}/notes", {"kind": "supervisor", "body": "x"})[0] == 403
    s, body = supervisor.post(f"/api/alarms/{aid}/notes", {"kind": "supervisor", "body": "Escalate to centre"})
    assert s == 200 and body["notes"][0]["kindLabel"] == "Supervisor note"


def test_assignment_respects_scope(supervisor, app):
    p12 = supervisor.get("/api/alarms?projectId=12&size=1")[1]["items"][0]["alarmId"]
    s, body = supervisor.post(f"/api/alarms/{p12}/assign", {"userId": "u-op"})      # u-op is project 7 only
    assert s == 400
    aid = pending_alarm(supervisor)["alarmId"]
    s, body = supervisor.post(f"/api/alarms/{aid}/assign", {"userId": "u-op"})
    assert s == 200 and body["assignment"]["name"] == "Arjun Verma"
    op = as_user(app, "operator@demo.camview")
    assert any(x["alarmId"] == aid for x in op.get("/api/alarms?projectId=7&quick=mine")[1]["items"])


def test_seeded_demo_numbers_match_spec():
    counts = {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) AS n FROM publications "
                                                   "WHERE client_id='client-a' AND project_id='7' GROUP BY status")}
    assert counts == {"ready_for_review": 3, "approved": 7, "shared": 8, "withdrawn": 1}
    assert db.one("SELECT COUNT(*) AS n FROM ops_review o JOIN publications p ON p.alarm_id=o.alarm_id "
                  "WHERE p.client_id='client-a' AND p.project_id='7'")["n"] == 19
    # client A has a second exam with nothing shared yet, so it still sees exactly 8 alerts (spec §164)
    assert db.one("SELECT COUNT(*) AS n FROM publications WHERE client_id='client-a' AND status='shared'")["n"] == 8
    # every seeded VALID alert has exactly one ticket
    assert db.one("SELECT COUNT(*) AS n FROM tickets")["n"] == db.one(
        "SELECT COUNT(*) AS n FROM ops_review WHERE status='marked_valid'")["n"]


def test_client_safe_summary_template_uses_only_allowed_levels():
    alarm = {"alarmTypeName": "Phone", "totalTimesReported": 3, "evidence": {"count": 2},
             "context": {"centre": {"code": "CTR-1"}, "camera": {"code": "CAM-9"}}}
    assert "CAM-9" not in workflow.client_safe_summary(alarm, ["centre"])
    assert workflow.client_safe_summary(alarm, ["camera"]).startswith("Phone detected at CAM-9.")
