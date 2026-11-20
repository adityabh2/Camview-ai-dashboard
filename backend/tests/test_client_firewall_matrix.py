"""
K. RBAC security (direct API access) · L. client delivery

The backend is authoritative: every role is checked against the API directly, not through the UI.
A client can never reach another client's alerts, tickets, evidence, projects or exams; routing follows the
real exam → client mapping; an alert is visible to a client ONLY after an operator VALID and only to the
client its exam belongs to.
"""

import pytest

import db
import exams
import workflow
from conftest import as_user

INTERNAL_READS = ["/api/queue?status=all", "/api/queue/summary", "/api/tickets", "/api/exams", "/api/alarms?projectId=7",
                  "/api/status?projectId=7", "/api/context/quality?projectId=7", "/api/admin/metrics",
                  "/api/clients", "/api/users", "/api/settings", "/api/audit"]
CLIENT_READS = ["/api/client/overview", "/api/client/alerts", "/api/client/analytics", "/api/client/profile"]


def _valid_delivered(supervisor, project=7):
    workflow.set_policy({"deliveryMode": "automatic"}, None)
    s, q = supervisor.get(f"/api/queue?projectId={project}&status=pending&size=50")
    a = next(x for x in q["items"] if x["evidence"]["count"])
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid", "projectId": project,
                                                                 "expectedRevision": 0})
    assert s == 200 and r["delivery"]["status"] == "delivered", r
    return a, r


@pytest.mark.parametrize("email", ["client.admin@client-a.demo", "viewer@client-a.demo", "user@client-b.demo"])
def test_clients_never_reach_internal_endpoints(app, email):
    c = as_user(app, email)
    for path in INTERNAL_READS:
        s, body = c.get(path)
        assert s == 404, f"{email} reached {path} ({s})"
    for path, payload in (("/api/queue/X/decide", {"result": "valid"}), ("/api/tickets/1/send", {}),
                          ("/api/settings/policy", {}), ("/api/admin/jobs/1/retry", {})):
        s, _ = c.post(path, payload) if not path.endswith("policy") else c.put(path, payload)
        assert s == 404, f"{email} could write {path}"


@pytest.mark.parametrize("email", ["operator@demo.camview", "supervisor@demo.camview", "manager@demo.camview",
                                   "admin@demo.camview"])
def test_internal_users_never_reach_the_client_portal(app, email):
    c = as_user(app, email)
    for path in CLIENT_READS:
        assert c.get(path)[0] == 404, f"{email} reached {path}"


def test_role_permissions_are_enforced_by_the_api(app, supervisor):
    s, q = supervisor.get("/api/queue?projectId=7&status=pending&size=5")
    aid = q["items"][0]["alarmId"]
    manager = as_user(app, "manager@demo.camview")                     # may publish, may not decide
    assert manager.post(f"/api/queue/{aid}/decide", {"result": "valid", "projectId": 7})[0] == 403
    investigator = as_user(app, "investigator@demo.camview")
    assert investigator.post(f"/api/queue/{aid}/decide", {"result": "invalid", "projectId": 7})[0] == 403
    operator = as_user(app, "operator@demo.camview")
    assert operator.get("/api/admin/metrics")[0] == 403
    assert operator.put("/api/settings/policy", {"deliveryMode": "automatic"})[0] == 403
    assert operator.post("/api/tickets/1/send", {})[0] in (403, 404)
    assert as_user(app, "admin@demo.camview").get("/api/admin/metrics")[0] == 200


def test_operator_scope_is_enforced_on_every_alert_endpoint(app):
    operator = as_user(app, "operator@demo.camview")                   # Project 07 only
    import datasource
    other = datasource.refresh("12").items[0]["alarmId"]
    for path in (f"/api/queue/{other}", f"/api/queue/{other}?projectId=12", f"/api/alarms/{other}?projectId=12",
                 f"/api/media/{other}/image/0?projectId=12"):
        assert operator.get(path)[0] == 404, path
    assert operator.post(f"/api/queue/{other}/decide", {"result": "valid", "projectId": 12})[0] == 404
    s, q12 = operator.get("/api/queue?projectId=12&status=all")      # an out-of-scope project lists nothing
    assert s in (200, 404) and not (q12 or {}).get("items")
    s, q = operator.get("/api/queue?status=all&size=200")
    assert {str(a["projectId"]) for a in q["items"]} <= {"7"}


def test_client_isolation_across_alerts_tickets_evidence_projects_exams(app, supervisor, client_a, client_b):
    a, r = _valid_delivered(supervisor)                                # project 7 → client A
    aid = a["alarmId"]
    s, d = client_a.get(f"/api/client/alerts/{aid}")
    assert s == 200 and d["ticketRef"] == r["ticket"]["ref"]
    ev = d["evidence"][0]["url"]
    # client B: the alert, its evidence, its thread and acknowledgement do not exist
    assert client_b.get(f"/api/client/alerts/{aid}")[0] == 404
    assert client_b.c.get(ev).status_code == 404
    assert client_b.post(f"/api/client/alerts/{aid}/acknowledge", {})[0] == 404
    assert client_b.post(f"/api/client/alerts/{aid}/messages", {"body": "x"})[0] == 404
    assert aid not in {x["alarmId"] for x in client_b.get("/api/client/alerts")[1]["items"]}
    # projects / exams / tickets of client A never appear for client B
    ov = client_b.get("/api/client/overview")[1]
    assert all("7" not in str(p) for p in ov["projects"]) and all(e["id"] != (r["exam"] or {}).get("id") for e in ov["exams"])
    assert all(x.get("ticketRef") != r["ticket"]["ref"] for x in client_b.get("/api/client/alerts")[1]["items"])
    prof = client_b.get("/api/client/profile")[1]
    assert "client-a" not in str(prof) and "State Recruitment Board" not in str(prof)


def test_only_operator_valid_reaches_a_client(supervisor, client_a):
    workflow.set_policy({"deliveryMode": "automatic"}, None)
    s, q = supervisor.get("/api/queue?projectId=7&status=pending&size=50")
    inv, exc = q["items"][0], q["items"][1]
    supervisor.post(f"/api/queue/{inv['alarmId']}/decide", {"result": "invalid", "projectId": 7})
    supervisor.post(f"/api/queue/{exc['alarmId']}/decide", {"result": "exception", "projectId": 7})
    ids = {x["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"]}
    assert inv["alarmId"] not in ids and exc["alarmId"] not in ids
    # even a stray 'shared' publication (a bug, a manual DB edit, an older version) is not visible without VALID
    now = db.now_iso()
    db.execute("INSERT INTO publications (alarm_id, client_id, status, project_id, shared_at, evidence, share_context, "
               "snapshot, updated_at) VALUES (?, 'client-a', 'shared', '7', ?, '[]', '[]', ?, ?)",
               (inv["alarmId"], now, db.jdump({"alarmId": inv["alarmId"], "projectId": 7}), now))
    assert client_a.get(f"/api/client/alerts/{inv['alarmId']}")[0] == 404
    assert inv["alarmId"] not in {x["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"]}


def test_routing_is_derived_from_the_exam_mapping_never_typed(supervisor):
    a, r = _valid_delivered(supervisor)
    exam = exams.resolve(7, a.get("firstInstance"))
    assert r["exam"]["id"] == exam["id"] and r["ticket"]["clientId"] == exam["clientId"] == "client-a"
    assert r["alert"]["examId"] == exam["id"] and r["alert"]["clientId"] == "client-a"
    assert r["alert"]["clientVisibility"] == "visible" and r["alert"]["visibleToClientIds"] == ["client-a"]
    s, body = supervisor.post(f"/api/queue/{a['alarmId']}/decide",
                              {"result": "valid", "projectId": 7, "clientId": "client-b"})   # not this exam's client
    assert s == 400


def test_a_remapped_exam_never_shows_the_alert_to_the_old_client(admin, supervisor, client_a):
    a, r = _valid_delivered(supervisor)
    assert client_a.get(f"/api/client/alerts/{a['alarmId']}")[0] == 200
    exam = exams.resolve(7, a.get("firstInstance"))
    s, _ = admin.put(f"/api/exams/{exam['id']}", {"clientId": "client-b"})
    assert s == 200
    assert client_a.get(f"/api/client/alerts/{a['alarmId']}")[0] == 404, "the old client must lose the alert"


def test_invalid_after_valid_withdraws_from_the_client(supervisor, client_a):
    a, r = _valid_delivered(supervisor)
    s, d = supervisor.get(f"/api/queue/{a['alarmId']}?projectId=7")
    s, r2 = supervisor.post(f"/api/queue/{a['alarmId']}/decide",
                            {"result": "invalid", "projectId": 7, "expectedRevision": d["alert"]["revision"]})
    assert s == 200 and r2["delivery"]["status"] == "withdrawn"
    assert client_a.get(f"/api/client/alerts/{a['alarmId']}")[0] == 404
    assert r2["alert"]["clientVisibility"] == "internal"
