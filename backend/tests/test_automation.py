"""
The automated review flow (final refactor spec):
ALERT → auto context (exam, client, location) → human VALID/INVALID/EXCEPTION
→ auto ticket (idempotent) → auto client routing (automatic or one-click send).
"""

import db
import exams
import workflow
from conftest import as_user


def pending(c, project=None, evidence=False, single_client=True):
    q = "/api/queue?status=pending&size=200" + (f"&projectId={project}" if project else "")
    s, body = c.get(q)
    assert s == 200
    for a in body["items"]:
        if a["alarmType"] != 8 and (a["client"] or not single_client) and (not evidence or a["evidence"]["count"]):
            return a
    raise AssertionError("no pending alert")


def test_queue_spans_all_exams_and_clients(supervisor):
    s, q = supervisor.get("/api/queue?status=all&size=5")
    assert s == 200 and q["counts"]["all"] > 1000                     # four projects merged into one queue
    assert {c[1] for c in q["facets"]["clients"]} == {"State Recruitment Board", "University Examinations Cell",
                                                      "National Nursing Council"}
    assert len(q["facets"]["exams"]) == 4
    a = q["items"][0]
    assert a["exam"] and a["client"] and a["context"]["path"]         # resolved automatically, never typed
    assert "priority" in q["sortRule"]


def test_queue_auto_sorting_priority_then_recency(supervisor):
    items = supervisor.get("/api/queue?status=pending&size=200")[1]["items"]
    ranks = [a["priorityRank"] for a in items]
    assert ranks == sorted(ranks)
    same = [a for a in items if a["priorityRank"] == ranks[0]]
    assert [a["lastInstance"] for a in same] == sorted([a["lastInstance"] for a in same], reverse=True)


def test_queue_filters(supervisor):
    s, q = supervisor.get("/api/queue?status=all&exam=exam-nnc&size=200")
    assert q["items"] and all(a["exam"]["id"] == "exam-nnc" and a["client"]["id"] == "client-c" for a in q["items"])
    s, q = supervisor.get("/api/queue?status=all&client=client-b&priority=critical&size=200")
    assert all(a["client"]["id"] == "client-b" and a["priority"] == "critical" for a in q["items"])


def test_summary_answers_what_needs_a_decision(supervisor):
    s, sm = supervisor.get("/api/queue/summary?tzOffset=330")
    k = sm["kpis"]
    assert {"newAlerts", "pending", "validToday", "invalidToday", "exceptions", "clientAlerts"} <= set(k)
    assert sm["priorityAlerts"] and all(a["decision"] == "pending" for a in sm["priorityAlerts"])
    assert sm["deliveryMode"] == "controlled"


def test_valid_creates_one_ticket_and_routes_to_client_controlled(supervisor, client_a):
    a = pending(supervisor, 7)
    aid = a["alarmId"]
    s, r = supervisor.post(f"/api/queue/{aid}/decide", {"result": "valid"})       # no note, no client, no form
    assert s == 200 and r["ticket"]["ref"].startswith("TKT-") and r["ticketCreated"] is True
    assert r["delivery"]["status"] == "ready" and r["delivery"]["clientId"] == "client-a"
    assert client_a.get(f"/api/client/alerts/{aid}")[0] == 404                   # controlled: not visible yet
    # double click / retry / reload → same ticket
    for _ in range(3):
        s, again = supervisor.post(f"/api/queue/{aid}/decide", {"result": "valid"})
        assert again["ticket"]["id"] == r["ticket"]["id"] and again["ticketCreated"] is False
    assert db.one("SELECT COUNT(*) AS n FROM tickets WHERE alarm_id=?", (aid,))["n"] == 1
    # one-click send
    s, sent = supervisor.post(f"/api/tickets/{r['ticket']['id']}/send", {})
    assert s == 200 and sent["ticket"]["deliveryStatus"] == "delivered"
    s, v = client_a.get(f"/api/client/alerts/{aid}")
    assert s == 200 and v["ticketRef"] == r["ticket"]["ref"] and v["exam"]["name"] == "SRE 2026 — Prelims"
    assert supervisor.post(f"/api/tickets/{r['ticket']['id']}/send", {})[0] == 200  # idempotent


def test_automatic_mode_delivers_on_valid(admin, operator, client_a, client_b):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    a = pending(operator, 7, evidence=True)
    s, r = operator.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    assert r["delivery"]["status"] == "delivered" and r["delivery"]["clientName"] == "State Recruitment Board"
    s, v = client_a.get(f"/api/client/alerts/{a['alarmId']}")
    assert s == 200 and v["summary"].startswith("SRE 2026 — Prelims")
    assert v["evidence"] and all(e["url"].startswith("/api/client/evidence/") for e in v["evidence"])
    assert client_b.get(f"/api/client/alerts/{a['alarmId']}")[0] == 404         # only the mapped client
    n = [x for x in client_a.get("/api/notifications")[1]["items"] if a["alarmId"] in x["link"]]
    assert n                                                                       # client notified automatically


def test_invalid_never_reaches_client_and_reclassify_withdraws(admin, supervisor, client_a):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    a = pending(supervisor, 7)
    aid = a["alarmId"]
    s, r = supervisor.post(f"/api/queue/{aid}/decide", {"result": "invalid"})
    assert r["ticket"] is None and r["delivery"]["status"] == "none"
    assert client_a.get(f"/api/client/alerts/{aid}")[0] == 404
    # valid → delivered, then re-classified invalid → ticket cancelled, client loses it
    supervisor.post(f"/api/queue/{aid}/decide", {"result": "valid"})
    assert client_a.get(f"/api/client/alerts/{aid}")[0] == 200
    s, r = supervisor.post(f"/api/queue/{aid}/decide", {"result": "invalid"})
    assert r["ticket"]["status"] == "cancelled" and r["delivery"]["status"] == "withdrawn"
    assert client_a.get(f"/api/client/alerts/{aid}")[0] == 404
    # and back to valid re-opens the SAME ticket
    s, r2 = supervisor.post(f"/api/queue/{aid}/decide", {"result": "valid"})
    assert r2["ticket"]["id"] == r["ticket"]["id"] and r2["ticket"]["status"] == "open"


def test_exception_is_kept_for_monitoring_not_delivered(supervisor):
    a = pending(supervisor, 12)
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "exception"})
    assert r["ticket"] is None
    s, q = supervisor.get("/api/queue?status=exception&size=500")
    assert any(x["alarmId"] == a["alarmId"] for x in q["items"])


def test_remarks_optional_unless_configured(admin, supervisor):
    a = pending(supervisor, 7)
    admin.put("/api/settings/policy", {"requireRemarks": True})
    s, body = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "invalid"})
    assert s == 400 and body["error"] == "remarks_required"
    assert supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "invalid", "note": "reflection"})[0] == 200


def test_multi_client_exam_asks_for_client_only_then(admin, supervisor):
    s, cl = admin.post("/api/clients", {"name": "Second Client", "projects": ["12"]})
    exams.seed([("exam-uet", "UET26", "UET 2026 — Entrance", None, ["12"])])     # exam without a single client
    a = pending(supervisor, 12, single_client=False)
    assert len(a["clients"]) == 2 and a["client"] is None
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    assert r["delivery"]["status"] == "needs_client" and len(r["delivery"]["options"]) == 2
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid", "clientId": cl["id"]})
    assert r["delivery"]["status"] == "ready" and r["delivery"]["clientId"] == cl["id"]


def test_operator_permissions_and_scope(app, operator):
    a = pending(operator, 7)
    inv = as_user(app, "investigator@demo.camview")                             # can view, cannot decide
    assert inv.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})[0] == 403
    s, q = operator.get("/api/queue?status=all&size=500")                      # operator: project 7 only
    assert {x["projectId"] for x in q["items"]} == {7}
    other = operator.get("/api/queue?status=all&projectId=12")
    assert other[0] == 200 and other[1]["items"] == []                          # out of scope → nothing
    s, r = operator.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    assert operator.post(f"/api/tickets/{r['ticket']['id']}/send", {})[0] == 403  # operators don't send


def test_tickets_listing_and_exam_admin(admin, supervisor):
    s, t = supervisor.get("/api/tickets?size=500")
    assert s == 200 and t["items"] and {"delivered", "ready"} <= set(t["counts"])
    assert all(x["ref"].startswith("TKT-") for x in t["items"])
    s, e = admin.post("/api/exams", {"name": "Board Exam 2027", "clientId": "client-c", "projectIds": ["21"]})
    assert s == 200 and e["clientName"] == "National Nursing Council"
    assert admin.post("/api/exams", {"name": "x", "projectIds": ["abc"]})[0] == 400
    assert supervisor.post("/api/exams", {"name": "y", "projectIds": ["7"]})[0] == 403


def test_every_ticket_carries_live_alert_and_evidence(supervisor, operator):
    a = pending(supervisor, 7, evidence=True)
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    tid = r["ticket"]["id"]
    s, t = supervisor.get("/api/tickets?size=500")
    row = next(x for x in t["items"] if x["id"] == tid)
    assert row["live"]["source"] == "live" and row["live"]["evidenceCount"] == a["evidence"]["count"]
    assert row["live"]["lastActionLabel"] and "7" in t["freshness"]
    s, d = supervisor.get(f"/api/tickets/{tid}")                                 # ticket panel: playable evidence
    assert s == 200 and d["alarm"]["source"] != "snapshot" and d["ticket"]["ref"] == r["ticket"]["ref"]
    assert len(d["evidence"]) == a["evidence"]["count"] and all(e["url"] for e in d["evidence"])
    assert supervisor.get("/api/tickets/999999")[0] == 404


def test_client_dashboard_shows_my_exams(client_a):
    s, o = client_a.get("/api/client/overview")
    names = {e["name"] for e in o["exams"]}
    assert names == {"SRE 2026 — Prelims", "SRE 2026 — Skill Test"}
    assert sum(e["alerts"] for e in o["exams"]) == o["metrics"]["shared"] == 8
