"""
Automatic states the alert workspace shows and the operational views the queue offers - all derived from
facts, none of them a task for the operator:

* exam phase (pre / live / post exam) from the exam's own date window - never guessed without dates
* per-alert data quality, evidence completeness and the client readiness gate (truthful, informational)
* the event story: related alerts on the same camera, an idempotent event group, "why this alert" facts;
  grouping is a view - raw alerts are never merged or deleted
* queue views: needs review / client eligible / client shared / camera issues / recording issues
"""

import pytest

import alert_service
import datasource
import exams
import health
from fakecamview import FakeCamview


# ---------------------------------------------------------------- exam phase

def test_exam_phase_comes_only_from_the_date_window():
    e = {"startDate": "2026-09-20", "endDate": "2026-09-25"}
    assert exams.phase(e, "2026-09-10T08:00:00Z") == "pre_exam"
    assert exams.phase(e, "2026-09-22T08:00:00Z") == "live_exam"
    assert exams.phase(e, "2026-09-30T08:00:00Z") == "post_exam"
    assert exams.phase({"startDate": "2026-09-20", "endDate": None}, "2026-09-30T00:00:00Z") == "live_exam"
    assert exams.phase({"startDate": None, "endDate": None}, "2026-09-22T08:00:00Z") == "unknown"
    assert exams.phase(None) == "unknown"


def test_phase_travels_with_the_alert_and_the_exam_list(admin, supervisor):
    s, r = supervisor.get("/api/queue", query_string={"status": "all", "size": 50})
    it = next(x for x in r["items"] if x["alert"]["examId"])
    assert it["alert"]["examPhase"] == "unknown"                     # demo exams carry no dates
    day = it["lastInstance"][:10]
    s, _ = admin.put(f"/api/exams/{it['alert']['examId']}", {"startDate": day, "endDate": day})
    assert s == 200
    s, d = supervisor.get(f"/api/queue/{it['alarmId']}?projectId={it['projectId']}")
    assert d["alert"]["examPhase"] == "live_exam" and d["alert"]["examStartDate"] == day
    s, ex = supervisor.get("/api/exams")
    e = next(x for x in ex["items"] if x["id"] == it["alert"]["examId"])
    assert e["phase"] in ("pre_exam", "live_exam", "post_exam")      # the CURRENT phase of the session
    s, summary = supervisor.get("/api/queue/summary")
    tile = next(x for x in summary["exams"] if x["id"] == it["alert"]["examId"])
    assert tile["phase"] == e["phase"]


# ---------------------------------------------------------------- quality / evidence / readiness

def test_quality_evidence_and_readiness_are_explicit_for_an_unknown_record():
    raw = {"alarm": {"alarmId": "Q1", "projectId": 9191, "cameraId": 42, "alarmType": 999, "lastActionType": 0,
                     "firstInstance": "2026-09-28T10:00:00Z", "lastInstance": "2026-09-28T10:00:00Z"}}
    [a] = datasource.enrich(datasource._normalize([raw], "9191"))
    d = alert_service.dto(a)
    q = d["quality"]
    assert q["state"] == "incomplete"
    assert {"Project code", "Exam mapping", "Client mapping", "Evidence attached"} <= set(q["missing"])
    assert "Recording status available" in q["unavailable"], "a missing health source is unavailable, not a data defect"
    ev = d["evidenceCompleteness"]
    assert ev["state"] == "incomplete" and [c["ok"] for c in ev["checks"]] == [False, False, True, True]
    r = d["clientReadiness"]
    assert r["state"] == "not_ready"
    assert "Operator decision is PENDING" in r["reasons"] and "No client mapped to this exam / project" in r["reasons"]
    assert set(alert_service.CONTRACT_KEYS) >= {"quality", "evidenceCompleteness", "clientReadiness", "examPhase"}


def test_readiness_follows_the_decision_and_the_delivery(supervisor):
    s, r = supervisor.get("/api/queue", query_string={"status": "pending", "size": 50})
    it = next(x for x in r["items"] if x["alert"]["clientMapping"] == "mapped" and x["alert"]["evidence"]["status"] == "available")
    assert it["alert"]["clientReadiness"]["state"] == "not_ready"
    assert it["alert"]["quality"]["state"] == "complete", it["alert"]["quality"]
    body = {"result": "valid", "projectId": it["projectId"], "expectedRevision": it["alert"]["revision"]}
    s, d = supervisor.post(f"/api/queue/{it['alarmId']}/decide", body)
    assert s == 200, d
    assert d["alert"]["clientReadiness"]["state"] == "ready" and d["alert"]["clientReadiness"]["reasons"] == []
    assert d["alert"]["clientVisibility"] == "internal"               # controlled delivery: ready, not yet sent
    # the views follow the gate: eligible now, shared after Send
    s, q = supervisor.get("/api/queue", query_string={"status": "all", "view": "client_eligible"})
    assert it["alarmId"] in {a["alarmId"] for a in q["items"]} and q["views"]["client_eligible"] >= 1
    s, sent = supervisor.post(f"/api/tickets/{d['ticket']['id']}/send", {})
    assert s == 200, sent
    s, d2 = supervisor.get(f"/api/queue/{it['alarmId']}?projectId={it['projectId']}")
    assert d2["alert"]["clientReadiness"]["state"] == "visible" and d2["alert"]["clientVisibility"] == "visible"
    s, q = supervisor.get("/api/queue", query_string={"status": "all", "view": "client_shared"})
    assert it["alarmId"] in {a["alarmId"] for a in q["items"]}
    # INVALID: withdrawn, never ready, never visible
    s, d3 = supervisor.post(f"/api/queue/{it['alarmId']}/decide", {**body, "result": "invalid", "expectedRevision": d2["alert"]["revision"]})
    assert s == 200, d3
    r3 = d3["alert"]["clientReadiness"]
    assert r3["state"] == "not_ready" and d3["alert"]["clientVisibility"] == "internal"
    assert "Operator decision is INVALID" in r3["reasons"]


def test_a_never_share_alert_type_is_not_ready_even_when_valid(admin, supervisor):
    s, r = supervisor.get("/api/queue", query_string={"status": "pending", "size": 50})
    it = next(x for x in r["items"] if x["alert"]["clientMapping"] == "mapped")
    s, _ = admin.put("/api/dictionary/alarm-types",
                     {"items": [{"id": it["alarmType"], "name": it["alarmTypeName"], "clientSharePolicy": "never"}]})
    assert s == 200
    alert_service._never_share.update(at=0.0)                         # the 5 s cache: re-read now
    s, d = supervisor.post(f"/api/queue/{it['alarmId']}/decide",
                           {"result": "valid", "projectId": it["projectId"], "expectedRevision": it["alert"]["revision"]})
    assert s == 200, d
    r = d["alert"]["clientReadiness"]
    assert r["state"] == "not_ready" and "This alert type is configured never to be sent to clients" in r["reasons"]
    assert d["delivery"]["status"] == "not_deliverable"               # the gate says what delivery did


# ---------------------------------------------------------------- event story

@pytest.fixture()
def cv(monkeypatch):
    f = FakeCamview(monkeypatch)

    def t(m):
        return f"2026-09-28T10:{m:02d}:00Z"
    f.items = [f.raw("S1", cam=501, at=t(42)), f.raw("S2", cam=501, at=t(43)), f.raw("S3", cam=501, at=t(45)),
               f.raw("S4", cam=501, at=t(47)), f.raw("S5", cam=501, at=t(49)),
               f.raw("S6", cam=501, at=t(20), typ=11),                # same camera, other type: related, not grouped
               f.raw("S7", cam=502, at=t(44)),                        # another camera: unrelated
               f.raw("S8", cam=501, at="2026-09-28T13:00:00Z")]       # same camera, hours later: outside the window
    return f


def test_event_story_groups_repeated_activity_without_merging_alerts(cv, supervisor):
    datasource.refresh("77", force=True)
    s, d = supervisor.get("/api/queue/S3?projectId=77")
    assert s == 200, d
    st = d["story"]
    assert st["kind"] == "repeated" and st["groupCount"] == 5 and st["spanMinutes"] == 7
    assert st["groupId"] == "77:501:14:S1"                           # earliest alert of the chain: deterministic
    assert st["relatedCount"] == 5                                    # S1 S2 S4 S5 + S6; not S7, not S8
    assert [x["alarmId"] for x in st["timeline"]] == ["S6", "S1", "S2", "S3", "S4", "S5"]
    assert [x["inGroup"] for x in st["timeline"]] == [False, True, True, True, True, True]
    assert next(x for x in st["timeline"] if x["current"])["alarmId"] == "S3"
    assert any(w.startswith("5 ") and "same camera within 7 min" in w for w in st["why"]), st["why"]
    assert "1 other alert on this camera within 60 min" in st["why"]
    assert "Video available" in st["why"] and "1 supporting image" in st["why"]
    # idempotent: the same group from any member, and again on a second read
    s, d1 = supervisor.get("/api/queue/S5?projectId=77")
    assert d1["story"]["groupId"] == st["groupId"] and d1["story"]["groupCount"] == 5
    s, d2 = supervisor.get("/api/queue/S3?projectId=77")
    assert d2["story"]["groupId"] == st["groupId"]
    # a view, not a merge: every raw alert is still listed and openable
    s, q = supervisor.get("/api/queue", query_string={"status": "all", "projectId": 77, "size": 50})
    assert {a["alarmId"] for a in q["items"]} >= {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"}
    s, d = supervisor.get("/api/queue/S7?projectId=77")
    assert d["story"]["kind"] == "single" and d["story"]["relatedCount"] == 0 and d["story"]["groupCount"] == 1


def test_story_facts_name_camera_and_recording_problems(cv, supervisor):
    datasource.refresh("77", force=True)
    health.ingest([{"projectId": 77, "cameraId": 502, "cameraState": "offline", "recordingState": "unknown", "streamState": "unavailable"},
                   {"projectId": 77, "cameraId": 501, "cameraState": "online", "recordingState": "not_recording", "streamState": "available"}])
    s, d = supervisor.get("/api/queue/S7?projectId=77")
    assert "Camera reported OFFLINE by the health source" in d["story"]["why"]
    s, d = supervisor.get("/api/queue/S1?projectId=77")
    assert "Recording NOT DETECTED by the health source" in d["story"]["why"]
    assert d["alert"]["cameraStatus"] == "online" and d["alert"]["recordingStatus"] == "not_recording"


# ---------------------------------------------------------------- queue views

def test_queue_views_are_filters_over_the_same_records(cv, supervisor):
    datasource.refresh("77", force=True)
    s, q = supervisor.get("/api/queue", query_string={"status": "all", "projectId": 77})
    assert q["views"] == {"needs_review": 8, "unacknowledged": 8, "client_eligible": 0, "client_shared": 0, "camera_issues": 0,
                          "recording_issues": 0}                # fresh alerts: nobody has acknowledged one yet
    health.ingest([{"projectId": 77, "cameraId": 502, "cameraState": "offline", "recordingState": "unknown", "streamState": "unavailable"},
                   {"projectId": 77, "cameraId": 501, "cameraState": "online", "recordingState": "not_recording", "streamState": "available"}])
    s, q = supervisor.get("/api/queue", query_string={"status": "all", "projectId": 77})
    assert q["views"]["camera_issues"] == 1 and q["views"]["recording_issues"] == 7
    s, q = supervisor.get("/api/queue", query_string={"status": "all", "projectId": 77, "view": "camera_issues"})
    assert [a["alarmId"] for a in q["items"]] == ["S7"]
    s, q = supervisor.get("/api/queue", query_string={"status": "all", "projectId": 77, "view": "recording_issues"})
    assert {a["alarmId"] for a in q["items"]} == {"S1", "S2", "S3", "S4", "S5", "S6", "S8"}
    s, q = supervisor.get("/api/queue", query_string={"status": "pending", "projectId": 77, "view": "needs_review", "size": 3})
    assert q["totalElements"] == 8 and len(q["items"]) == 3          # a view paginates like any list
    s, q = supervisor.get("/api/queue", query_string={"view": "nonsense"})
    assert s == 400
    # the dashboard KPI for recording exists only because a real source reports recording
    s, summary = supervisor.get("/api/queue/summary", query_string={"projectId": 77})
    assert summary["cameras"]["recordingReporting"] == 1 and summary["cameras"]["notRecording"] == 1
