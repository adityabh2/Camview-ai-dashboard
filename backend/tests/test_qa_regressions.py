"""QA regression suite: every alert in the list opens to the same, complete record;
malformed upstream data never takes the feed down; decisions are idempotent under
concurrency; nothing leaks across clients or roles; no false success."""

import threading

import datasource
import db
import pytest
import tickets
from conftest import as_user


def _q(c, qs):
    s, body = c.get(f"/api/queue?{qs}")
    assert s == 200, body
    return body


# ---------------------------------------------------------------- 001-003 list ↔ detail
DETAIL_KEYS = ("alarmId", "projectId", "cameraId", "alarmType", "alarmTypeName", "lastActionType", "priority",
               "firstInstance", "lastInstance", "totalTimesReported", "decision", "decisionSource", "locationLabel",
               "exam", "client", "clients")


def test_001_003_every_listed_alert_opens_to_the_same_record(supervisor):
    checked = 0
    for page in (1, 2, 3):
        items = _q(supervisor, f"status=all&size=200&page={page}")["items"]
        for a in items[::7]:                                          # a spread across projects, states and pages
            s, d = supervisor.get(f"/api/queue/{a['alarmId']}?projectId={a['projectId']}")
            assert s == 200, (a["alarmId"], d)
            det = d["alarm"]
            for k in DETAIL_KEYS:
                assert det.get(k) == a.get(k), (a["alarmId"], k, a.get(k), det.get(k))
            kinds = [e["kind"] for e in d["evidence"]]
            assert kinds.count("image") == a["evidence"]["images"] and ("video" in kinds) == a["evidence"]["video"]
            assert (d["ticket"] or {}).get("id") == (a["ticket"] or {}).get("id")
            assert det["context"]["path"] and det["health"]["conditions"]
            checked += 1
    assert checked > 50


def test_002_detail_works_without_or_with_wrong_project(supervisor):
    a = _q(supervisor, "status=all&size=5&projectId=12")["items"][0]
    s, d = supervisor.get(f"/api/queue/{a['alarmId']}")                       # e.g. from a notification link
    assert s == 200 and d["alarm"]["alarmId"] == a["alarmId"]
    s, d = supervisor.get(f"/api/queue/{a['alarmId']}?projectId=7")          # wrong project → never a different alert
    assert s == 404 or d["alarm"]["alarmId"] == a["alarmId"]


@pytest.mark.parametrize("bad", ["does-not-exist", "a%2Fb", "%00", "x" * 400, "ALM-%27%3B--"])
def test_024_odd_alert_ids_are_404_not_500(supervisor, bad):
    s, body = supervisor.get(f"/api/queue/{bad}")
    assert s == 404 and body["error"] == "not_found"


def test_022_pagination_has_no_duplicates_or_gaps(supervisor):
    full = _q(supervisor, "status=all&size=200&page=1")
    total = full["totalElements"]
    ids = []
    for page in range(1, 6):
        ids += [a["alarmId"] for a in _q(supervisor, f"status=all&size=50&page={page}")["items"]]
    assert len(ids) == len(set(ids)) == min(total, 250)
    assert ids[:200] == [a["alarmId"] for a in full["items"]]                # same order at any page size


# ---------------------------------------------------------------- 021 malformed upstream data
def test_021_malformed_records_are_skipped_not_fatal():
    good = {"alarm": {"alarmId": "OK1", "alarmType": 1, "cameraId": 5, "projectId": 34, "priority": 1,
                      "lastActionType": 0}, "camera": {"id": 5}}
    junk = [None, "oops", [1, 2], {"alarm": None}, {"alarm": {"priority": 1}}, {"alarm": {"alarmId": "M1", "alarmMetadata": "x"}},
            {"alarm": {"alarmId": "I1"}, "imageUrls": "http://img"}, {"alarm": {"alarmId": "V1", "videoUrl": 12}}, good, good]
    out = datasource._normalize(junk)
    ids = [a["alarmId"] for a in out]
    assert ids == ["M1", "I1", "V1", "OK1"]                                    # junk skipped, duplicate OK1 once
    by = {a["alarmId"]: a for a in datasource.enrich(out)}
    assert by["I1"]["imageUrls"] == ["http://img"] and by["I1"]["evidence"]["images"] == 1   # not 10 "characters"
    assert by["V1"]["videoUrl"] is None and by["V1"]["evidence"]["video"] is False
    assert by["M1"]["alarmEvent"] is None


# ---------------------------------------------------------------- 016 duplicate / concurrent VALID
def test_016_concurrent_valid_is_one_ticket_one_delivery(app, admin, supervisor):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    a = next(x for x in _q(supervisor, "status=pending&size=200&projectId=7")["items"] if x["client"] and x["alarmType"] != 8)
    results = []

    def go():
        c = as_user(app, "supervisor@demo.camview")
        results.append(c.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"}))

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(s == 200 for s, _ in results), results
    assert len({r["ticket"]["id"] for _, r in results}) == 1
    assert db.one("SELECT COUNT(*) n FROM tickets WHERE alarm_id=?", (a["alarmId"],))["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM publications WHERE alarm_id=?", (a["alarmId"],))["n"] == 1


# ---------------------------------------------------------------- 018 no false success
def test_ticket_delivery_failure_is_reported_and_retry_works(admin, supervisor, client_a, monkeypatch):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    a = next(x for x in _q(supervisor, "status=pending&size=200&projectId=7")["items"] if x["client"] and x["alarmType"] != 8)
    real = tickets.deliver
    monkeypatch.setattr(tickets, "deliver", lambda *a_, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    s, body = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    assert s == 502 and body["error"] == "delivery_failed" and "retry" in body["message"]   # explicit, never "delivered"
    t = tickets.for_alarm(a["alarmId"])
    assert t is None or t["deliveryStatus"] != "delivered"
    assert client_a.get(f"/api/client/alerts/{a['alarmId']}")[0] == 404
    monkeypatch.setattr(tickets, "deliver", real)
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})       # retry
    assert s == 200 and r["delivery"]["status"] == "delivered"
    assert client_a.get(f"/api/client/alerts/{a['alarmId']}")[0] == 200


# ---------------------------------------------------------------- 028 database integrity
def test_028_integrity_after_mixed_decisions(admin, supervisor):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    items = [x for x in _q(supervisor, "status=pending&size=200")["items"] if x["client"] and x["alarmType"] != 8][:9]
    for i, a in enumerate(items):
        res = ("valid", "invalid", "exception")[i % 3]
        supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": res})
    supervisor.post(f"/api/queue/{items[0]['alarmId']}/decide", {"result": "invalid"})     # re-classify a delivered one
    assert not db.rows("SELECT alarm_id FROM tickets GROUP BY alarm_id HAVING COUNT(*) > 1")
    assert not db.rows("SELECT alarm_id FROM publications GROUP BY alarm_id, client_id HAVING COUNT(*) > 1")
    # an open, delivered ticket always has a shared publication for the same alarm + client
    assert not db.rows("""SELECT t.id FROM tickets t LEFT JOIN publications p ON p.alarm_id=t.alarm_id AND p.client_id=t.client_id
                          AND p.status='shared' WHERE t.status='open' AND t.delivery_status='delivered' AND p.alarm_id IS NULL""")
    # nothing decided INVALID / EXCEPTION is visible to a client
    assert not db.rows("""SELECT p.alarm_id FROM publications p JOIN ops_review r ON r.alarm_id=p.alarm_id
                          WHERE p.status='shared' AND r.status IN ('marked_invalid','marked_exception')""")
    assert not db.rows("SELECT id FROM tickets WHERE status='open' AND result != 'valid'")


# ---------------------------------------------------------------- 017 / 020 isolation + RBAC (backend, not buttons)
def test_017_client_isolation_direct_urls(admin, supervisor, client_a, client_b):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    a = next(x for x in _q(supervisor, "status=pending&size=200&projectId=7")["items"]
             if x["client"] and x["alarmType"] != 8 and x["evidence"]["images"])
    supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    s, v = client_a.get(f"/api/client/alerts/{a['alarmId']}")
    assert s == 200 and v["evidence"]
    url = v["evidence"][0]["url"]
    assert client_b.get(f"/api/client/alerts/{a['alarmId']}")[0] == 404
    assert client_b.c.get(url).status_code == 404                                # evidence proxy too
    assert {x["alarmId"] for x in client_b.get("/api/client/alerts")[1]["items"]}.isdisjoint({a["alarmId"]})
    for path in ("/api/queue", f"/api/queue/{a['alarmId']}", "/api/tickets", "/api/exams", "/api/users"):
        assert client_a.get(path)[0] in (403, 404)                               # internal APIs closed to clients
    assert client_a.post(f"/api/queue/{a['alarmId']}/decide", {"result": "invalid"})[0] in (403, 404)
    body = str(v)
    for internal in ("note", "review", "assignment", "validatedBy", "workflowState", "clients"):
        assert f"'{internal}'" not in body


ROLE_MATRIX = [
    # email,                        queue, detail, decide, send, users, settings
    ("admin@demo.camview",          200,   200,    200,    200,  200,   200),
    ("supervisor@demo.camview",     200,   200,    200,    200,  403,   403),
    ("operator@demo.camview",       200,   200,    200,    403,  403,   403),
    ("investigator@demo.camview",   200,   200,    403,    403,  403,   403),
    ("manager@demo.camview",        200,   200,    None,   200,  None,  None),
]


@pytest.mark.parametrize("email,queue,detail,decide,send,users,settings", ROLE_MATRIX)
def test_020_role_matrix_enforced_by_the_api(app, email, queue, detail, decide, send, users, settings):
    sup = as_user(app, "supervisor@demo.camview")
    a = next(x for x in _q(sup, "status=pending&size=200&projectId=7")["items"] if x["client"] and x["alarmType"] != 8)
    c = as_user(app, email)
    assert c.get("/api/queue?projectId=7&size=1")[0] == queue
    assert c.get(f"/api/queue/{a['alarmId']}?projectId=7")[0] == detail
    if decide is not None:
        assert c.post(f"/api/queue/{a['alarmId']}/decide", {"result": "exception"})[0] == decide
    s, r = sup.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    assert c.post(f"/api/tickets/{r['ticket']['id']}/send", {})[0] == send
    if users is not None:
        assert c.get("/api/users")[0] == users
    if settings is not None:
        assert c.put("/api/settings/policy", {"requireRemarks": False})[0] == settings


def test_unauthenticated_is_rejected(app):
    c = app.test_client()
    for path in ("/api/queue", "/api/queue/summary", "/api/tickets", "/api/client/alerts", "/api/camera-health/status"):
        assert c.get(path).status_code == 401
    assert c.post("/api/queue/x/decide", json={"result": "valid"}).status_code == 401


def test_events_without_alarm_id_get_a_stable_derived_id():
    """Live finding: Camview sends camera online/offline events (type 10) with alarmId = null
    (505 of 2,523 records in project 5). They must stay visible, openable and de-duplicated."""
    ev = {"alarm": {"alarmId": None, "alarmType": 10, "cameraId": "8698", "projectId": 5, "priority": 1, "lastActionType": 0,
                    "firstInstance": "2026-04-03T10:56:03.289525Z", "lastInstance": "2026-05-08T09:29:18Z",
                    "alarmMetadata": {"reason": "Camera Offline", "status": "OFFLINE"}}, "camera": {"id": 8698}}
    out = datasource._normalize([ev, ev, {"alarm": {"alarmId": None}}])
    assert len(out) == 1                                             # repeats merged; nothing to identify → skipped
    a = out[0]
    assert a["alarmId"] == "EVT-5-8698-10-20260403105603289" and a["alarmIdDerived"] is True
    assert datasource._normalize([ev])[0]["alarmId"] == a["alarmId"]  # stable across refreshes
    assert a["alarmTypeName"] == "Camera Offline"
