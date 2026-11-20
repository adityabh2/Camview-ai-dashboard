"""
The ACKNOWLEDGE step of the review flow.

* an operator picks an alert up: who / when is recorded, the alert leaves NEW, the decision stays pending
* acknowledging is not a decision: the review revision does not move and it never makes anyone's decision a conflict
* deciding an alert nobody acknowledged acknowledges it implicitly (by the decider)
* idempotent: the first acknowledgement stays; camera status events and client users cannot acknowledge
"""

import alert_service
import db


def _pending(c, project=7):
    s, q = c.get(f"/api/queue?projectId={project}&status=pending&size=50")
    assert s == 200 and q["items"], q
    return q["items"][0]


def _ack(c, a):
    return c.post(f"/api/queue/{a['alarmId']}/acknowledge", {"projectId": a["projectId"]})


def _audit_acks(alarm_id):
    return db.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='review.acknowledge' AND resource_id=?", (alarm_id,))["n"]


def test_contract_carries_the_acknowledgement():
    assert {"reviewState", "acknowledgedBy", "acknowledgedAt"} <= set(alert_service.CONTRACT_KEYS)
    assert alert_service.CONTRACT_VERSION == 1, "additive fields only: the contract version stays"


def test_acknowledge_records_who_and_when_without_deciding(supervisor):
    a = _pending(supervisor)
    assert a["alert"]["reviewState"] == "new" and a["alert"]["acknowledgedAt"] is None
    assert a["flags"]["unacknowledged"] is True
    s, r = _ack(supervisor, a)
    assert s == 200, r
    assert r["acknowledged"]["by"] and r["acknowledged"]["at"] and not r["idempotent"]
    d = r["alert"]
    assert d["reviewState"] == "acknowledged" and d["operatorDecision"] == "pending" and d["decision"] == "pending"
    assert d["revision"] == a["alert"]["revision"], "acknowledging is not a decision: the revision does not move"
    assert d["acknowledgedBy"] == r["acknowledged"]["by"] and d["acknowledgedAt"] == r["acknowledged"]["at"]
    rv = db.get_review(a["alarmId"])
    assert rv["acknowledgedAt"] == r["acknowledged"]["at"] and rv["status"] == "acknowledged"
    assert _audit_acks(a["alarmId"]) == 1
    # the list says the same as the detail
    s, q = supervisor.get("/api/queue?projectId=7&status=pending&size=200")
    listed = next(x for x in q["items"] if x["alarmId"] == a["alarmId"])
    assert listed["alert"]["reviewState"] == "acknowledged"
    assert listed["review"]["acknowledgedBy"] == r["acknowledged"]["by"]
    assert listed["flags"]["unacknowledged"] is False
    s, det = supervisor.get(f"/api/queue/{a['alarmId']}?projectId=7")
    assert det["alert"]["acknowledgedAt"] == r["acknowledged"]["at"]
    assert det["alarm"]["review"]["acknowledgedBy"] == r["acknowledged"]["by"]


def test_second_acknowledgement_is_idempotent_and_keeps_the_first(supervisor, operator):
    a = _pending(operator)
    s1, r1 = _ack(operator, a)
    s2, r2 = _ack(supervisor, a)
    assert s1 == s2 == 200, (r1, r2)
    assert not r1["idempotent"] and r2["idempotent"]
    assert r2["acknowledged"] == r1["acknowledged"], "the first acknowledgement stays"
    assert _audit_acks(a["alarmId"]) == 1


def test_acknowledgement_never_conflicts_with_a_decision(supervisor, operator):
    a = _pending(operator)
    rev = a["alert"]["revision"]
    assert _ack(operator, a)[0] == 200
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide",
                           {"result": "valid", "projectId": a["projectId"], "expectedRevision": rev})
    assert s == 200, r                       # a view taken before the acknowledgement is still current
    assert r["alert"]["reviewState"] == "decided" and r["alert"]["revision"] == rev + 1
    assert r["alert"]["acknowledgedBy"] == r["review"]["acknowledgedBy"], "who picked it up is kept once decided"
    assert r["ticket"]["ref"]


def test_deciding_acknowledges_implicitly(supervisor):
    a = _pending(supervisor)
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "invalid", "projectId": a["projectId"]})
    assert s == 200, r
    assert r["alert"]["reviewState"] == "decided"
    assert r["alert"]["acknowledgedAt"] and r["alert"]["acknowledgedBy"]
    assert _audit_acks(a["alarmId"]) == 0, "the implicit acknowledgement is part of the decision, not a second event"
    s, r2 = _ack(supervisor, a)               # acknowledging afterwards changes nothing
    assert s == 200 and r2["idempotent"] and r2["alert"]["reviewState"] == "decided"


def test_unacknowledged_view_and_kpi_count_pending_alerts_nobody_picked_up(supervisor):
    s, q = supervisor.get("/api/queue?projectId=7&status=pending&view=unacknowledged&size=200")
    assert s == 200 and q["items"], q
    before = q["views"]["unacknowledged"]
    assert before == q["counts"]["unacknowledged"] == len(q["items"])
    assert all(x["flags"]["unacknowledged"] and x["decision"] == "pending" and x["eventKind"] == "alert" for x in q["items"])
    a = q["items"][0]
    assert _ack(supervisor, a)[0] == 200
    s, q2 = supervisor.get("/api/queue?projectId=7&status=pending&view=unacknowledged&size=200")
    assert q2["views"]["unacknowledged"] == q2["counts"]["unacknowledged"] == before - 1
    assert a["alarmId"] not in {x["alarmId"] for x in q2["items"]}
    s, summ = supervisor.get("/api/queue/summary?projectId=7")
    assert s == 200 and summ["kpis"]["unacknowledged"] == before - 1


def test_camera_status_events_and_client_users_cannot_acknowledge(supervisor, client_a):
    s, q = supervisor.get("/api/queue?projectId=7&kind=camera_status&status=all&size=5")
    assert s == 200
    for ev in q["items"][:1]:
        s, r = _ack(supervisor, ev)
        assert s == 409 and r["error"] == "camera_event", r
        assert db.get_review(ev["alarmId"])["acknowledgedAt"] is None
    a = _pending(supervisor)
    assert _ack(client_a, a)[0] == 404, "internal endpoints are unavailable to client users"
