"""Reports that reach clients by themselves: daily, and once when a project ends."""

from datetime import datetime, timedelta, timezone

import pytest

import auto_reports
import config
import db
import project_codes
import tickets

CFG = {"daily": True, "onEnd": True, "hour": 20, "offset": 330}


def _alarm(aid, minutes_ago=30, project=7):
    t = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"alarmId": aid, "projectId": project, "cameraId": "101", "alarmType": 1, "alarmTypeName": "Mobile Phone Detected",
            "priority": "high", "lastActionType": 0, "firstInstance": t, "lastInstance": t, "totalTimesReported": 1,
            "imageUrls": []}


@pytest.fixture()
def delivered(monkeypatch):
    monkeypatch.setattr(config, "DELIVER_ALL", True, raising=False)
    tickets.auto_sync([_alarm("REP-1"), _alarm("REP-2")])


def _client_a_id():
    return db.one("SELECT client_id FROM users WHERE email='client.admin@client-a.demo'")["client_id"]


def _now_local(hour):
    n = datetime.now(timezone.utc) + timedelta(minutes=330)
    return n.replace(hour=hour, minute=5)


def test_daily_report_waits_for_the_hour(delivered):
    assert auto_reports.daily(_now_local(9), CFG) == 0


def test_daily_report_is_shared_once(delivered, client_a):
    assert auto_reports.daily(_now_local(21), CFG) >= 1
    s, r = client_a.get("/api/client/reports")
    titles = [x["title"] for x in (r.get("items") or r)]
    assert any(t.startswith("Daily report") for t in titles)
    assert auto_reports.daily(_now_local(22), CFG) == 0                  # never twice the same day


def test_daily_report_switched_off(delivered):
    assert auto_reports.daily(_now_local(21), {**CFG, "daily": False}) == 0


def test_end_of_project_report_after_the_end_date(delivered, client_a):
    yesterday = (datetime.now(timezone.utc) + timedelta(minutes=330) - timedelta(days=1)).date().isoformat()
    db.set_setting(project_codes.SETTING, {"DONE/CODE": {"projectIds": ["7"], "names": {}, "source": "lookup",
                                                          "dates": {"7": {"start": yesterday, "end": yesterday}},
                                                          "addedAt": "2026-01-01T00:00:00Z"}})
    assert auto_reports.on_end(cfg=CFG) >= 1
    s, r = client_a.get("/api/client/reports")
    titles = [x["title"] for x in (r.get("items") or r)]
    assert any(t.startswith("Final report — DONE/CODE") for t in titles)
    assert auto_reports.on_end(cfg=CFG) == 0                             # once only


def test_no_end_report_while_project_runs(delivered):
    today = (datetime.now(timezone.utc) + timedelta(minutes=330)).date().isoformat()
    db.set_setting(project_codes.SETTING, {"RUN/CODE": {"projectIds": ["7"], "names": {}, "source": "lookup",
                                                         "dates": {"7": {"start": today, "end": today}},
                                                         "addedAt": "2026-01-01T00:00:00Z"}})
    assert auto_reports.on_end(cfg=CFG) == 0


def test_project_dates_come_from_lookup():
    assert project_codes._dates([{"projectId": 5, "startDate": "2026-11-15T00:00:00", "endDate": "2026-11-16"}]) == \
        {"5": {"start": "2026-11-15", "end": "2026-11-16"}}


def test_code_and_exam_for_the_same_projects_give_one_report(delivered, monkeypatch):
    yesterday = (datetime.now(timezone.utc) + timedelta(minutes=330) - timedelta(days=1)).date().isoformat()
    db.set_setting(project_codes.SETTING, {"SAME/CODE": {"projectIds": ["7"], "names": {}, "source": "lookup",
                                                          "dates": {"7": {"start": yesterday, "end": yesterday}},
                                                          "addedAt": "2026-01-01T00:00:00Z"}})
    import exams
    monkeypatch.setattr(exams, "list_exams", lambda *a, **k: [{"id": "exam-same", "name": "Same exam", "code": "SAME",
                                                               "projectIds": ["7"], "endDate": yesterday}])
    first = auto_reports.on_end(cfg=CFG)
    assert first >= 1
    clients = db.one("SELECT COUNT(DISTINCT client_id) AS n FROM client_projects WHERE project_id='7'")["n"]
    assert first == clients                                              # one per client, not two
