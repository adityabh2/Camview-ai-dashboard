"""
Tests for the second wave of V1 features: activity groups, context
completeness, since-last-visit, daily brief, comparison, watchlist, snooze,
notification preferences, client conversation, rule versioning & recipient
groups, room scope, error texts, DATA_MODE alias, and large/empty datasets.
"""

import time

import pytest

import camview_client
import config
import datasource
import db
import notify
from conftest import as_user
from test_api import FakeResp


# --------------------------------------------------------------------------- grouping (noise reduction)

def test_groups_never_hide_raw_alarms(supervisor):
    s, raw = supervisor.get("/api/alarms?projectId=7&size=1")
    s, g = supervisor.get("/api/groups?projectId=7&by=camera&gap=10&size=500")
    assert s == 200 and g["groupCount"] < g["rawAlarms"]                      # noise reduced …
    assert g["alarmsInGroups"] == g["rawAlarms"] == raw["totalElements"]    # … nothing lost
    big = max(g["items"], key=lambda x: x["count"])
    assert len(big["alarms"]) == big["count"] == len(big["alarmIds"])        # expandable to every raw event
    assert big["impact"]["cameras"] and "minutes" in g["rule"]


def test_groups_by_room_and_validation(supervisor):
    s, g = supervisor.get("/api/groups?projectId=7&by=room&gap=30")
    assert s == 200 and g["by"] == "room"
    assert supervisor.get("/api/groups?projectId=7&by=planet")[0] == 400


# --------------------------------------------------------------------------- context completeness / data quality

def test_context_completeness_is_data_quality():
    full = datasource.completeness({lvl: {"code": lvl} for lvl in datasource.COMPLETENESS_LEVELS})
    assert full["percent"] == 100 and not full["missing"]
    import nomenclature
    annex = datasource.completeness(nomenclature.resolve(7, 141))            # annex camera: no floor/room
    assert "room" in annex["missing"] and "floor" in annex["missing"] and annex["percent"] == 71   # 5 of 7 levels
    unmapped = datasource.completeness(nomenclature.resolve(7, 199))
    assert unmapped["percent"] < 30


def test_quality_reports_unknown_type_and_missing_room(supervisor):
    s, q = supervisor.get("/api/context/quality?projectId=7")
    assert 12 in q["unknownAlarmTypes"] and q["missingRoom"] >= 1


# --------------------------------------------------------------------------- since last visit / brief / compare

def test_since_last_visit(app):
    first = as_user(app, "manager@demo.camview")
    s, body = first.get("/api/since-last-visit?projectId=7")
    assert body["available"] is False                                         # first sign-in: nothing to compare
    second = as_user(app, "manager@demo.camview")
    s, body = second.get("/api/since-last-visit?projectId=7")
    assert body["available"] is True and "newAlarms" in body and body["since"]


def test_daily_brief(supervisor):
    s, b = supervisor.get("/api/brief?projectId=7&tzOffset=330")
    assert s == 200 and b["totals"]["alarms"] > 0 and b["provenance"] == "derived"
    assert b["peakPeriod"]["count"] >= 1 and b["highestActivity"]
    assert supervisor.get("/api/brief?projectId=7&date=nope")[0] == 400


def test_compare_respects_scope(app, supervisor, operator):
    s, all_ = supervisor.get("/api/compare?level=project&range=7d")
    assert {r["code"] for r in all_["rows"]} == {"PROJECT-07", "PROJECT-12", "PROJECT-15", "PROJECT-21"}
    inv = as_user(app, "investigator@demo.camview")                       # analytics.view, project 7 only
    s, mine = inv.get("/api/compare?level=project&range=7d")
    assert [r["code"] for r in mine["rows"]] == ["PROJECT-07"]
    assert operator.get("/api/compare?level=project")[0] == 403           # no analytics.view
    s, tcs = supervisor.get("/api/compare?level=tc&projectId=7&range=7d")
    assert {r["code"] for r in tcs["rows"]} >= {"TC-0701", "TC-0711"}
    assert supervisor.get("/api/compare?level=tec&projectId=7&range=7d")[0] == 400     # no TEC level exists


# --------------------------------------------------------------------------- watchlist

def test_watchlist_and_watcher_notification(supervisor):
    assert supervisor.post("/api/watchlist", {"entityType": "camera", "entityId": "106", "label": "CAM-106",
                                              "projectId": "7"})[0] == 200
    s, w = supervisor.get("/api/watchlist")
    item = w["items"][0]
    assert item["entityType"] == "camera" and item["alarms"] > 0 and item["latest"]["cameraCode"] == "CAM-106"
    fake = {"alarmId": "WATCH-1", "cameraId": "106", "projectId": "7", "priority": "critical", "alarmTypeName": "X",
            "context": {"mapped": True}}
    notify.notify_watchers([fake])
    s, n = supervisor.get("/api/notifications")
    assert any("Watched camera" in x["title"] for x in n["items"])
    supervisor.post("/api/watchlist/remove", {"entityType": "camera", "entityId": "106"})
    assert supervisor.get("/api/watchlist")[1]["items"] == []


def test_watchlist_is_scope_checked(operator):
    assert operator.post("/api/watchlist", {"entityType": "project", "entityId": "12", "projectId": "12"})[0] == 404


# --------------------------------------------------------------------------- inbox: severity, snooze, preferences

def test_snooze_hides_non_critical_but_never_critical(supervisor):
    uid = "u-super"
    notify.to_users([uid], "operational", "info-one", severity="info", dedupe="t-info")
    notify.to_users([uid], "operational", "crit-one", severity="critical", dedupe="t-crit")
    items = {n["title"]: n for n in supervisor.get("/api/notifications")[1]["items"]}
    ids = [items["info-one"]["id"], items["crit-one"]["id"]]
    s, r = supervisor.post("/api/notifications/mark", {"action": "snooze", "ids": ids, "minutes": 30})
    assert s == 200 and r["criticalNotSnoozed"] == 1
    titles = {n["title"] for n in supervisor.get("/api/notifications")[1]["items"]}
    assert "crit-one" in titles and "info-one" not in titles
    assert "info-one" in {n["title"] for n in supervisor.get("/api/notifications?snoozed=1")[1]["items"]}
    assert supervisor.get("/api/notifications?severity=critical")[1]["items"][0]["title"] == "crit-one"


def test_notification_preferences(supervisor):
    s, p = supervisor.put("/api/me/preferences", {"notifications": {"criticalOnly": True, "sound": True}})
    assert p["notifications"]["criticalOnly"] and p["notifications"]["sound"]
    notify.to_users(["u-super"], "operational", "pref-info", severity="info", dedupe="p1")
    notify.to_users(["u-super"], "operational", "pref-crit", severity="critical", dedupe="p2")
    titles = {n["title"] for n in supervisor.get("/api/notifications")[1]["items"]}
    assert "pref-crit" in titles and "pref-info" not in titles


# --------------------------------------------------------------------------- client conversation

def shared(client_id="client-a"):
    return db.one("SELECT alarm_id, project_id FROM publications WHERE client_id=? AND status='shared'", (client_id,))


def test_client_conversation_both_ways(client_a, supervisor, client_b):
    pub = shared()
    aid = pub["alarm_id"]
    s, r = client_a.post(f"/api/client/alerts/{aid}/messages", {"kind": "clarification", "body": "Which room exactly?"})
    assert s == 200 and r["messages"][-1]["kind"] == "clarification"
    assert client_a.post(f"/api/client/alerts/{aid}/messages", {"kind": "response", "body": "x"})[1]["messages"][-1]["kind"] == "comment"
    s, d = supervisor.get(f"/api/alarms/{aid}?projectId={pub['project_id']}")
    thread = next(x for x in d["sharing"] if x["client"]["id"] == "client-a")["messages"]
    assert thread[0]["body"] == "Which room exactly?"
    assert any("Clarification" in n["title"] for n in supervisor.get("/api/notifications")[1]["items"])
    s, r = supervisor.post("/api/sharing/respond", {"alarmId": aid, "clientId": "client-a", "body": "Room 201, rear camera."})
    assert s == 200
    s, v = client_a.get(f"/api/client/alerts/{aid}")
    assert v["messages"][-1]["body"] == "Room 201, rear camera." and v["messages"][-1]["audience"] == "internal"
    # other clients can't post into it; can't respond on unshared alerts
    assert client_b.post(f"/api/client/alerts/{aid}/messages", {"body": "hi"})[0] == 404
    approved = db.one("SELECT alarm_id FROM publications WHERE status='approved' AND client_id='client-a'")["alarm_id"]
    assert supervisor.post("/api/sharing/respond", {"alarmId": approved, "clientId": "client-a", "body": "x"})[0] == 409


def test_viewer_cannot_comment(viewer_a):
    assert viewer_a.post(f"/api/client/alerts/{shared()['alarm_id']}/messages", {"body": "hi"})[0] == 403


# --------------------------------------------------------------------------- rule versioning, groups, tester

RULE = {"name": "Crit pending v", "conditions": [{"field": "priority", "op": "eq", "value": "critical"},
                                                 {"field": "lastActionType", "op": "eq", "value": "0"}],
        "recipients": ["operator"], "channel": "in_app", "status": "active"}


def test_rule_versioning(admin):
    s, r = admin.post("/api/alert-rules", RULE)
    rid = r["id"]
    s, u = admin.put(f"/api/alert-rules/{rid}", {**RULE, "severity": "critical"})
    assert u["version"] == 2 and "severity" in u["changed"]
    s, u = admin.put(f"/api/alert-rules/{rid}", {"status": "draft"})
    assert u["version"] == 3
    s, v = admin.get(f"/api/alert-rules/{rid}/versions")
    assert [x["version"] for x in v["items"]] == [3, 2, 1] and v["items"][0]["status"] == "draft"
    assert v["items"][0]["changedBy"] == "Aditi Rao"
    s, same = admin.put(f"/api/alert-rules/{rid}", {"status": "draft"})
    assert same["changed"] == []                                          # no-op doesn't create a version


def test_draft_rules_do_not_fire_and_groups_expand(admin, operator):
    s, g = admin.post("/api/recipient-groups", {"name": "Control Room", "roles": [], "userIds": ["u-op"]})
    assert admin.post("/api/recipient-groups", {"name": "Bad", "roles": ["client_admin"]})[0] == 400
    s, r = admin.post("/api/alert-rules", {**RULE, "name": "Draft rule", "status": "draft", "recipients": [f"group:{g['id']}"]})
    operator.get("/api/status?projectId=7")
    assert not [n for n in operator.get("/api/notifications")[1]["items"] if n["title"].startswith("Draft rule")]
    admin.put(f"/api/alert-rules/{r['id']}", {"status": "active"})
    operator.get("/api/status?projectId=7")
    assert [n for n in operator.get("/api/notifications")[1]["items"] if n["title"].startswith("Draft rule")]


def test_rule_tester_over_period(admin):
    s, t = admin.post("/api/alert-rules/test", {"projectId": "7", "hours": 24 * 14, "conditions": RULE["conditions"]})
    assert s == 200 and t["matches"] == len(t["items"]) or t["matches"] > 50
    assert all(a["priority"] == "critical" and a["lastActionType"] == 0 for a in t["items"])


# --------------------------------------------------------------------------- room scope

def test_room_scope(app, admin):
    import nomenclature
    room = nomenclature.resolve(7, 106)["room"]
    s, r = admin.post("/api/users", {"name": "Room Op", "email": "room.op@demo.camview", "roleId": "operator",
                                     "password": "room-op-pass-1", "scopes": [{"type": "room", "value": room["id"]}]})
    from conftest import Client
    op = Client(app).login("room.op@demo.camview", "room-op-pass-1")
    s, body = op.get("/api/alarms?projectId=7&size=200")
    assert body["items"] and all(a["context"]["path"][5]["code"] == room["code"] and
                                 a["context"]["path"][2]["code"] == nomenclature.resolve(7, 106)["centre"]["code"]
                                 for a in body["items"])


# --------------------------------------------------------------------------- misc

def test_error_texts_match_spec(monkeypatch):
    config.API_KEY = "k"
    for resp, text in ((FakeResp(403, {}), "Access to alarm data was denied"),
                       (FakeResp(500, {}), "Alarm service returned an internal error."),
                       (FakeResp(200, None), "Unable to process alarm data")):
        monkeypatch.setattr(camview_client.requests, "post", lambda *a, _r=resp, **k: _r)
        with pytest.raises(camview_client.ApiError) as e:
            camview_client.call({"projectId": 1})
        assert text in e.value.message

    def boom(*a, **k):
        raise camview_client.requests.exceptions.Timeout()
    monkeypatch.setattr(camview_client.requests, "post", boom)
    with pytest.raises(camview_client.ApiError) as e:
        camview_client.call({"projectId": 1})
    assert e.value.message == "Alarm service did not respond in time."


def test_data_mode_alias(monkeypatch):
    monkeypatch.setenv("CAMVIEW_MODE", "")
    monkeypatch.setenv("DATA_MODE", "mock")
    config.apply()
    assert config.MODE == "demo"
    monkeypatch.setenv("DATA_MODE", "live")
    config.apply()
    assert config.MODE == "live"
    monkeypatch.setenv("CAMVIEW_MODE", "demo")
    config.apply()


# --------------------------------------------------------------------------- large / empty datasets (live, fake Camview)

def _page(n_start, count, has_next, total):
    return {"cameraAlarmsDetails": [
        {"camera": {"cameraId": 106}, "alarm": {"alarmId": f"BIG-{i}", "cameraId": 106, "alarmType": 1, "projectId": 7,
                                                "lastActionType": i % 4, "priority": 1 + i % 4,
                                                "firstInstance": f"2030-01-01T{(i // 60) % 24:02d}:{i % 60:02d}:00Z",
                                                "totalTimesReported": 1}, "imageUrls": []}
        for i in range(n_start, n_start + count)],
        "page": 0, "size": 100, "totalElements": total, "totalPages": (total + 99) // 100, "hasNext": has_next}


@pytest.mark.parametrize("total", [0, 1, 20, 1250])
def test_live_datasets_of_any_size(app, monkeypatch, tmp_path, total):
    import bootstrap
    import nomenclature
    calls = []

    def fake(url, json=None, headers=None, timeout=None):
        calls.append(json)
        start = json["page"] * 100
        return FakeResp(200, _page(start, max(0, min(100, total - start)), start + 100 < total, total))

    monkeypatch.setattr(camview_client.requests, "post", fake)
    config.MODE, config.API_KEY, config.DEFAULT_PROJECT_ID = "live", "k", "7"
    config.LIVE_DB_PATH = str(tmp_path / "big.db")
    try:
        bootstrap.run()
        nomenclature._cache.update(nodes=None, checked=0, db=None)
        from conftest import Client
        c = Client(app).login("admin@camview.local", "live-admin-pass-123")
        t = time.time()
        s, body = c.get("/api/alarms?projectId=7&size=20&page=2")
        assert s == 200
        window = min(total, config.WINDOW_PAGES * 100)
        assert body["totalElements"] == window                                   # working window, never the full history
        assert len(body["items"]) == max(0, min(20, window - 20))
        assert len(calls) == max(1, min(config.WINDOW_PAGES, (total + 99) // 100))  # paged fetch, no over-fetching
        s, ov = c.get("/api/overview?projectId=7")
        assert s == 200 and ov["metrics"]["total"] == window
        assert time.time() - t < 10
    finally:
        config.MODE, config.API_KEY, config.DEFAULT_PROJECT_ID = "demo", "", ""
