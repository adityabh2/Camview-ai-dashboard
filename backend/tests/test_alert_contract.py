"""
A. Canonical alert data · B. one AlertService · C. contract stability

* every alert received is persisted; an alert that was listed stays openable after a feed refresh, a page
  shift, a restart and while Camview is down (never "outside the current data window")
* list and detail are built by the same service from the same record: the list item resolves to a detail
  with the same identity, timestamps, context, decision and ticket
* every AlertDTO field is always present; unknown values say why (not_mapped / not_available / unknown)
"""

import alert_service
import alert_store
import datasource
import db
from fakecamview import FakeCamview

MATCH = ("alarmId", "projectId", "projectCode", "cameraId", "cameraCode", "alarmType", "alarmTypeName", "firstInstance",
         "lastInstance", "occurrences", "examId", "examName", "clientId", "clientName", "centreCode", "room",
         "operatorDecision", "decision", "camviewStatus", "clientVisibility", "ticketRef", "ticketStatus", "revision",
         "cameraStatus", "recordingStatus", "streamStatus")


def _queue(c, **q):
    s, r = c.get("/api/queue", query_string={"status": "all", "size": 30, **q})
    assert s == 200, r
    return r


def test_every_list_item_resolves_to_the_same_detail(supervisor):
    items = _queue(supervisor, projectId=7)["items"]
    assert items
    for it in items[:12]:
        assert set(alert_service.CONTRACT_KEYS) <= set(it["alert"]), "list item is missing contract fields"
        s, d = supervisor.get(f"/api/queue/{it['alarmId']}?projectId={it['projectId']}")
        assert s == 200, d
        assert set(alert_service.CONTRACT_KEYS) <= set(d["alert"])
        for k in MATCH:
            assert it["alert"][k] == d["alert"][k], f"{it['alarmId']}: list says {k}={it['alert'][k]!r}, detail {d['alert'][k]!r}"


def test_contract_holds_after_a_decision(supervisor):
    it = _queue(supervisor, projectId=7, status="pending")["items"][0]
    s, r = supervisor.post(f"/api/queue/{it['alarmId']}/decide",
                           {"result": "valid", "projectId": it["projectId"], "expectedRevision": it["alert"]["revision"]})
    assert s == 200, r
    listed = next(x for x in _queue(supervisor, projectId=7)["items"] if x["alarmId"] == it["alarmId"])
    s, d = supervisor.get(f"/api/queue/{it['alarmId']}?projectId=7")
    for k in MATCH:
        assert listed["alert"][k] == d["alert"][k] == r["alert"][k], k
    assert d["alert"]["operatorDecision"] == "valid" and d["alert"]["revision"] == 1 and d["alert"]["ticketRef"]


def test_unknown_values_are_explicit_never_omitted():
    raw = {"alarm": {"alarmId": "U1", "projectId": 9191, "cameraId": 42, "alarmType": 999, "lastActionType": 0,
                     "firstInstance": "2026-09-28T10:00:00Z", "lastInstance": "2026-09-28T10:00:00Z"}}
    [a] = datasource.enrich(datasource._normalize([raw], "9191"))
    d = alert_service.dto(a)
    assert set(d) == set(alert_service.CONTRACT_KEYS)
    assert d["examMapping"] == "not_mapped" and d["examId"] is None
    assert d["clientMapping"] == "not_mapped" and d["clientId"] is None
    assert d["projectMapping"] == "not_mapped" and d["projectCode"] is None
    assert d["tecMapping"] == "not_available" and d["tecCode"] is None
    assert d["contextMapping"] == "not_mapped"
    assert d["cameraStatus"] == "unknown" and d["recordingStatus"] == "unknown" and d["streamStatus"] == "unknown"
    assert d["healthAvailable"] is False
    assert d["evidence"]["status"] == "not_available" and d["evidence"]["video"] is None and d["evidence"]["images"] == []
    assert d["camviewStatus"] == "pending" and d["operatorDecision"] == "pending" and d["clientVisibility"] == "internal"
    assert d["ticketId"] is None and d["ticketStatus"] is None


def test_every_received_alert_is_persisted(supervisor):
    _queue(supervisor, projectId=7)
    feed = datasource.refresh("7")
    stored = alert_store.get_many([a["alarmId"] for a in feed.items])
    assert len(stored) == len(feed.items)
    a = feed.items[0]
    assert stored[a["alarmId"]]["lastInstance"] == a["lastInstance"]


def test_listed_alert_stays_openable_after_restart(supervisor):
    """A demo arrival is random: after a restart (the working set is rebuilt) it is no longer in the feed.
    It was listed, so it must still open — from the canonical store."""
    feed = datasource.refresh("7")
    before = {a["alarmId"] for a in feed.items}
    feed.demo_tick -= 100                                  # two simulated arrivals
    datasource.refresh("7", force=True)
    listed = {a["alarmId"] for a in _queue(supervisor, projectId=7, status="pending", size=100)["items"]}
    new = sorted(listed - before)
    assert new, "expected a simulated arrival in the list"
    aid = new[0]
    datasource.reset()                                     # process restart: memory is gone, the database is not
    assert aid not in {a["alarmId"] for a in datasource.refresh("7").items}
    s, d = supervisor.get(f"/api/queue/{aid}?projectId=7")
    assert s == 200, d
    assert d["alert"]["source"] == "stored" and d["alert"]["alarmId"] == aid
    s, d2 = supervisor.get(f"/api/queue/{aid}")            # also without the project hint
    assert s == 200 and d2["alarm"]["alarmId"] == aid
    s, r = supervisor.post(f"/api/queue/{aid}/decide", {"result": "invalid", "projectId": 7})
    assert s == 200, r                                     # and it can still be decided


def test_page_shift_never_makes_an_alert_unavailable(supervisor, monkeypatch):
    cv = FakeCamview(monkeypatch)
    cv.items = [cv.raw(f"S{i}", cam=500 + i) for i in range(5)]
    assert len(datasource.refresh("77", force=True).items) == 5
    cv.skip = {"S3"}                                       # Camview's shifting pages keep skipping one record
    for _ in range(datasource.MISSING_GRACE + 2):
        datasource.refresh("77", force=True)
    assert "S3" not in {a["alarmId"] for a in datasource.refresh("77").items}
    s, d = supervisor.get("/api/queue/S3?projectId=77")
    assert s == 200, d
    assert d["alert"]["source"] == "stored" and d["alert"]["cameraId"] == "503"


def test_restart_while_camview_is_down_shows_stored_alerts_not_live(supervisor, monkeypatch):
    cv = FakeCamview(monkeypatch)
    cv.items = [cv.raw("R1"), cv.raw("R2", cam=502)]
    datasource.refresh("77", force=True)
    datasource.reset()                                     # restart…
    cv.mode = 500                                          # …while Camview is failing
    r = _queue(supervisor, projectId=77)
    assert {a["alarmId"] for a in r["items"]} == {"R1", "R2"}
    f = r["freshness"]["77"]
    assert f["state"] == "disconnected" and f["source"] == "stored", "stored data must never be presented as LIVE"
    s, d = supervisor.get("/api/queue/R2?projectId=77")
    assert s == 200 and d["alert"]["source"] == "stored"


def test_out_of_scope_and_missing_alerts_answer_the_same(operator):
    s1, _ = operator.get("/api/queue/NO-SUCH-ALERT")
    feed = datasource.refresh("12")                        # project 12: outside the operator's scope
    s2, _ = operator.get(f"/api/queue/{feed.items[0]['alarmId']}?projectId=12")
    s3, _ = operator.get(f"/api/queue/{feed.items[0]['alarmId']}")
    assert s1 == s2 == s3 == 404


def test_store_writes_only_changed_records():
    items = datasource.refresh("7").items
    n = alert_store.save("7", items)
    assert n == 0, "unchanged records must not be rewritten on every refresh"
    changed = [dict(items[0], totalTimesReported=(items[0].get("totalTimesReported") or 1) + 1)]
    assert alert_store.save("7", changed) == 1
    assert db.one("SELECT record FROM alerts WHERE alarm_id=?", (items[0]["alarmId"],))
