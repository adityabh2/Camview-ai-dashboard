"""
API behaviour tests (demo mode) + live mode against a fake Camview.
"""

import os
import shutil

import pytest

import bootstrap
import camview_client
import config
import datasource
import db
import nomenclature


# --------------------------------------------------------------------------- core endpoints

@pytest.mark.parametrize("path", [
    "/api/status?projectId=7", "/api/overview?projectId=7", "/api/alarms?projectId=7", "/api/alerts?projectId=7",
    "/api/work?projectId=7", "/api/cameras?projectId=7", "/api/cameras/106?projectId=7", "/api/context/tree?projectId=7",
    "/api/context/quality?projectId=7", "/api/analytics?projectId=7&range=7d", "/api/history?projectId=7",
    "/api/evidence?projectId=7", "/api/shift?projectId=7", "/api/presentation?projectId=7",
    "/api/sharing?projectId=7&tab=candidates", "/api/sharing?projectId=7&tab=shared", "/api/search?q=CAM-10",
    "/api/notifications", "/api/reports", "/api/alert-rules", "/api/schedules", "/api/dictionary", "/api/bookmarks",
    "/api/clients"])
def test_supervisor_endpoints_ok(supervisor, path):
    s, body = supervisor.get(path)
    assert s == 200, (path, body)


@pytest.mark.parametrize("path", ["/api/users", "/api/roles", "/api/settings", "/api/audit"])
def test_admin_endpoints_ok(admin, path):
    assert admin.get(path)[0] == 200


def test_alarm_list_filters_sort_and_pagination(supervisor):
    s, body = supervisor.get("/api/alarms?projectId=7&quick=critical&sort=priority&size=5&page=2")
    assert s == 200 and body["page"] == 2 and len(body["items"]) <= 5
    assert all(a["priority"] == "critical" for a in body["items"])
    s, body = supervisor.get("/api/alarms?projectId=7&tc=TC-0701&size=200")
    assert body["items"] and all(a["context"]["path"][1]["code"] == "TC-0701" for a in body["items"])
    s, body = supervisor.get("/api/alarms?projectId=7&search=CAM-106&size=200")
    assert body["items"] and all(a["cameraCode"] == "CAM-106" or "CAM-106" in str(a) for a in body["items"])


def test_alarm_shape_keeps_concepts_separate(supervisor):
    a = supervisor.get("/api/alarms?projectId=7&size=1")[1]["items"][0]
    for key in ("lastActionType", "review", "workflowState", "visibility"):
        assert key in a
    assert a["visibility"]["state"] in ("internal", "ready_for_review", "approved", "shared", "withdrawn")


def test_unknown_alarm_is_not_available(supervisor):
    s, body = supervisor.get("/api/alarms/ALM-NOPE?projectId=7")
    assert s == 404 and body["message"] == "Alarm not available."


def test_export_is_audited(manager):
    r = manager.c.get("/api/export/alarms.csv?projectId=7&quick=critical")
    assert r.status_code == 200 and r.data.startswith(b"Alarm ID")
    assert db.one("SELECT 1 FROM audit_events WHERE action='alarm.export'")


def test_search_is_grouped_and_scoped(app):
    from conftest import as_user
    op = as_user(app, "operator@demo.camview")                  # project 7 only
    s, body = op.get("/api/search?q=CAM-5")                     # project 12 cameras are CAM-5xx
    assert "cameras" not in body["groups"]
    sup = as_user(app, "supervisor@demo.camview")
    s, body = sup.get("/api/search?q=CAM-5")
    assert body["groups"]["cameras"]


def test_saved_views_are_per_user(supervisor, operator):
    s, v = supervisor.post("/api/views", {"name": "Critical", "route": "/live", "query": "quick=critical"})
    assert s == 200
    assert operator.get("/api/views")[1]["items"] == []
    assert operator.delete(f"/api/views/{v['id']}")[0] == 200          # no-op for someone else's view
    assert len(supervisor.get("/api/views")[1]["items"]) == 1


def test_rule_dry_run_and_crud(admin):
    s, r = admin.post("/api/alert-rules/test", {"projectId": "7", "conditions": [{"field": "priority", "op": "eq", "value": "critical"}]})
    assert s == 200 and r["matches"] > 0 and r["explanation"] == ["Priority = critical"]
    bad = {"name": "x", "conditions": [{"field": "priority", "op": "eq", "value": "critical"}], "channel": "email"}
    assert admin.post("/api/alert-rules", bad)[0] == 400                       # email is not integrated
    s, r = admin.post("/api/alert-rules", {**bad, "channel": "in_app"})
    assert s == 200


def test_rules_notify_recipients_and_feed_work_queue(admin, operator):
    rule = {"name": "Critical pending (test)", "conditions": [{"field": "priority", "op": "eq", "value": "critical"},
                                                              {"field": "lastActionType", "op": "eq", "value": "0"}],
            "recipients": ["operator"], "channel": "in_app", "action": "queue"}
    assert admin.post("/api/alert-rules", rule)[0] == 200
    operator.get("/api/status?projectId=7")
    operator.get("/api/status?projectId=7")
    hits = [n for n in operator.get("/api/notifications")[1]["items"] if n["title"].startswith("Critical pending (test)")]
    assert hits and len(hits) == len({n["title"] for n in hits})         # once per alarm
    s, work = operator.get("/api/work?projectId=7")
    assert any(sec["key"] == "rules" and sec["count"] for sec in work["sections"])


def test_audit_user_filter_is_partial(admin):
    s, body = admin.get("/api/audit?user=Arjun")
    assert s == 200 and body["items"] and all("Arjun" in (i["user_name"] or "") for i in body["items"])


def test_schedule_fires_once_into_notifications(admin, operator):
    admin.post("/api/schedules", {"name": "Exam start", "phase": "EVENT_START", "at": "2020-01-01T00:00:00Z",
                                  "template": "event_start", "recipients": ["operator"]})
    operator.get("/api/status?projectId=7")
    operator.get("/api/status?projectId=7")
    items = [n for n in operator.get("/api/notifications")[1]["items"] if "Exam start" in n["title"]]
    assert len(items) == 1


def test_notifications_mark_read(supervisor):
    s, n = supervisor.get("/api/notifications")
    s, r = supervisor.post("/api/notifications/mark", {"action": "read", "ids": "all"})
    assert r["unread"] == 0


def test_handover_records_snapshot(supervisor):
    s, r = supervisor.post("/api/handovers", {"projectId": "7", "fromShift": "Shift 1 Morning", "toShift": "Shift 2 Afternoon",
                                              "notes": "All clear"})
    assert s == 200
    s, sh = supervisor.get("/api/shift?projectId=7")
    assert sh["handovers"][0]["notes"] == "All clear" and "openItems" in sh["handovers"][0]["snapshot"]


def test_policy_validation(admin):
    assert admin.put("/api/settings/policy", {"validSource": "nope"})[0] == 400
    assert admin.put("/api/settings/policy", {"repeatThreshold": -1})[0] == 400
    s, r = admin.put("/api/settings/policy", {"longPendingMinutes": "", "repeatThreshold": 4})
    assert s == 200 and r["policy"]["longPendingMinutes"] is None and r["policy"]["repeatThreshold"] == 4


def test_user_create_requires_client_for_client_roles(admin):
    assert admin.post("/api/users", {"name": "A", "email": "a@x.io", "roleId": "client_user"})[0] == 400
    s, r = admin.post("/api/users", {"name": "A", "email": "a@x.io", "roleId": "client_user", "clientId": "client-b"})
    assert s == 200 and r["temporaryPassword"]


# --------------------------------------------------------------------------- live mode

class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p, self.text = status, payload, ""

    def json(self):
        if self._p is None:
            raise ValueError
        return self._p


LIVE_PAGE = {"cameraAlarmsDetails": [
    {"camera": {"cameraId": 106}, "alarm": {"alarmId": "LIVE-1", "cameraId": 106, "alarmType": 5, "projectId": 7,
                                            "alarmState": 1, "lastActionType": 0, "priority": 1,
                                            "firstInstance": "2030-01-01T10:00:00Z", "lastInstance": "2030-01-01T10:01:00Z",
                                            "totalTimesReported": 2}, "imageUrls": [], "serialNumber": 1}],
    "page": 0, "size": 100, "totalElements": 1, "totalPages": 1, "hasNext": False, "lastKey": None}


@pytest.fixture()
def live(app, monkeypatch, tmp_path):
    calls = []
    resp = {"v": FakeResp(200, LIVE_PAGE)}
    monkeypatch.setattr(camview_client.requests, "post",
                        lambda url, json=None, headers=None, timeout=None: calls.append((url, json, headers)) or resp["v"])
    config.MODE, config.API_KEY, config.DEFAULT_PROJECT_ID = "live", "raw-key", "7"
    config.LIVE_DB_PATH = str(tmp_path / "live.db")
    bootstrap.run()
    nomenclature._cache.update(nodes=None, checked=0, db=None)
    yield {"calls": calls, "resp": resp}
    config.MODE, config.API_KEY, config.DEFAULT_PROJECT_ID = "demo", "", ""


def test_live_bootstrap_admin_and_documented_request(app, live):
    from conftest import Client
    c = Client(app).login("admin@camview.local", "live-admin-pass-123")
    s, body = c.get("/api/alarms?projectId=7")
    assert s == 200 and body["items"][0]["alarmId"] == "LIVE-1"
    url, payload, headers = live["calls"][0]
    assert url == config.API_URL and headers["Authorization"] == "raw-key"        # raw key, no Bearer
    assert payload == {"projectId": 7, "page": 0, "size": 100}                    # documented fields, 0-based page
    a = body["items"][0]
    assert a["context"]["mapped"] is False                                        # no master data -> not invented
    # Camview's priority value is kept, but until the mapping is confirmed the shown priority follows the alert type
    assert a["priorityLevel"] is not None and a["priority"] in ("critical", "high", "medium", "low") and a["prioritySource"] in ("type", "default")


def test_live_failure_keeps_last_data_and_reports_state(app, live):
    from conftest import Client
    c = Client(app).login("admin@camview.local", "live-admin-pass-123")
    assert c.get("/api/alarms?projectId=7")[1]["items"]
    live["resp"]["v"] = FakeResp(403, {})
    datasource._feeds["7"].fetched_at = 0
    s, st = c.get("/api/status?projectId=7")
    assert st["freshness"]["state"] == "delayed" and "rejected" in st["freshness"]["lastError"]["message"]
    s, body = c.get("/api/alarms?projectId=7")
    assert body["items"][0]["alarmId"] == "LIVE-1"                                # last good data preserved
    s, n = c.get("/api/notifications")
    assert any(x["category"] == "system" for x in n["items"])


def test_camview_error_mapping():
    with pytest.raises(camview_client.ApiError) as e:
        camview_client.build_body({"projectId": "PRJ-1"})
    assert e.value.status == 400
    body = camview_client.build_body({"projectId": "7", "page": 3, "size": 500, "alarmType": ["5"], "useHistory": 1,
                                      "priority": "critical"})
    assert body == {"projectId": 7, "page": 2, "size": 100, "alarmType": [5], "useHistory": True}


def test_live_reads_every_page_newest_alarm_on_last_page(app, live, monkeypatch):
    """Regression: Camview's listAlarms is not sorted newest-first. Only reading the first pages
    missed the latest alarms (project 5: 300 of 2,523 were loaded)."""
    from conftest import Client

    def page(n):
        ts = "2026-06-10T07:12:00Z" if n == 2 else f"2026-04-0{n + 1}T10:00:00Z"
        items = [{"alarm": {"alarmId": f"P{n}-{i}", "projectId": 7, "cameraId": 100 + i, "alarmType": 1, "priority": 2,
                            "lastActionType": 0, "firstInstance": ts, "lastInstance": ts}} for i in range(100 if n < 2 else 37)]
        return FakeResp(200, {"cameraAlarmsDetails": items, "totalElements": 237, "totalPages": 3, "hasNext": n < 2})

    monkeypatch.setattr(camview_client.requests, "post",
                        lambda url, json=None, headers=None, timeout=None: live["calls"].append(json) or page(json["page"]))
    datasource.reset()
    c = Client(app).login("admin@camview.local", "live-admin-pass-123")
    s, q = c.get("/api/queue?status=all&projectId=7&size=5")
    assert q["counts"]["all"] == 237                                    # every page, no duplicates
    assert q["items"][0]["lastInstance"].startswith("2026-06-10")        # the newest alarm (last page) is shown first
    assert sorted(p["page"] for p in live["calls"] if isinstance(p, dict) and "page" in p)[-1] == 2   # 0-based last page
