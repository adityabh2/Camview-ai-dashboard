"""
M. failure injection · I. live-data resilience · H. data quality / quarantine · J. health truthfulness

Every deliberate upstream failure must leave the system working (last good data kept, clear state shown) or
fail safely with a clear message — never a crash, never a claim of LIVE without a recent successful read.
"""

from datetime import datetime, timedelta, timezone

import pytest

import camview_client
import datasource
import db
import dq
import obs
from fakecamview import FakeCamview


@pytest.fixture()
def cv(monkeypatch):
    f = FakeCamview(monkeypatch)
    f.items = [f.raw("L1", cam=501), f.raw("L2", cam=502, typ=11), f.raw("L3", cam=503, action=1)]
    return f


def _refresh():
    return datasource.refresh("77", force=True)


def _state():
    return datasource.freshness(datasource.refresh("77"))


@pytest.mark.parametrize("mode,code", [(403, "forbidden"), (500, "upstream_error"), (503, "upstream_error"),
                                       ("timeout", "timeout"), ("network", "network_error"),
                                       ("malformed_json", "malformed_response"), ("empty", "malformed_response"),
                                       ("not_a_list", "malformed_response")])
def test_upstream_failure_keeps_the_last_good_data(cv, supervisor, mode, code):
    assert len(_refresh().items) == 3 and _state()["state"] == "live"
    cv.mode = mode
    feed = _refresh()
    assert len(feed.items) == 3, "a failed read must never empty the screens"
    f = _state()
    assert f["state"] == "delayed" and f["lastError"]["code"] == code
    s, q = supervisor.get("/api/queue?projectId=77&status=all")
    assert s == 200 and {a["alarmId"] for a in q["items"]} == {"L1", "L2", "L3"}
    assert q["freshness"]["77"]["state"] == "delayed"
    s, d = supervisor.get("/api/queue/L1?projectId=77")
    assert s == 200 and d["alert"]["alarmId"] == "L1"
    assert obs.counters()["refresh.failure"] >= 1
    cv.mode = "ok"
    camview_client.breaker.reset()
    _refresh()
    assert _state()["state"] == "live"                   # recovers by itself


@pytest.mark.parametrize("mode", [403, 500, "timeout", "malformed_json"])
def test_failure_before_any_data_is_disconnected_not_a_crash(cv, supervisor, mode):
    cv.mode = mode
    s, q = supervisor.get("/api/queue?projectId=77&status=all")
    assert s == 200 and q["items"] == []
    assert q["freshness"]["77"]["state"] == "disconnected"
    s, st = supervisor.get("/api/status?projectId=77")
    assert s == 200 and st["overall"]["state"] == "disconnected"


def test_an_empty_answer_after_data_is_a_failed_read(cv):
    _refresh()
    cv.items = []                                        # Camview never deletes alarms
    feed = _refresh()
    assert len(feed.items) == 3 and feed.last_error["code"] == "empty_response"


def test_never_live_without_a_recent_successful_refresh(cv):
    _refresh()
    feed = datasource.refresh("77")
    assert datasource.freshness(feed)["state"] == "live"
    feed.last_success_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert datasource.freshness(feed)["state"] == "delayed"
    fresh = datasource.Feed("88")
    assert datasource.freshness(fresh)["state"] == "connecting"


def test_circuit_breaker_stops_hammering_and_recovers(cv, monkeypatch):
    _refresh()
    cv.mode = 500
    for _ in range(camview_client.CircuitBreaker.FAIL_THRESHOLD):
        _refresh()
    assert camview_client.breaker_state()["state"] == "open"
    calls = cv.calls
    feed = _refresh()
    assert cv.calls == calls, "an open circuit fails fast without calling Camview"
    assert feed.last_error["code"] == "circuit_open" and len(feed.items) == 3
    assert _state()["circuit"]["state"] == "open"
    cv.mode = "ok"
    monkeypatch.setattr(camview_client.breaker, "opened_at", 0)            # cool-down over: one trial call
    _refresh()
    assert camview_client.breaker_state()["state"] == "closed" and _state()["state"] == "live"


def test_client_errors_never_open_the_circuit(cv):
    cv.mode = 403                                        # e.g. discovery probing projects the key may not read
    for _ in range(camview_client.CircuitBreaker.FAIL_THRESHOLD + 2):
        _refresh()
    assert camview_client.breaker_state()["state"] == "closed"


def test_failing_feed_backs_off(cv):
    _refresh()
    cv.mode = 500
    _refresh()
    feed = datasource.refresh("77")
    assert feed.failures == 1 and feed.retry_at > datetime.now(timezone.utc).timestamp()
    calls = cv.calls
    feed.fetched_at = 0                                  # cache expired, but still inside the backoff window
    datasource.refresh("77")
    assert cv.calls == calls, "a failing project must not be re-read on every request"
    assert _state()["nextAttemptAt"]


def test_camview_calls_share_one_concurrency_limit(cv, monkeypatch):
    import threading
    import time
    active, peak, lock = [0], [0], threading.Lock()
    real = cv.__call__

    def slow(url, json=None, headers=None, timeout=None):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.02)
        try:
            return real(url, json=json, headers=headers, timeout=timeout)
        finally:
            with lock:
                active[0] -= 1
    import requests
    monkeypatch.setattr(requests, "post", slow)
    monkeypatch.setattr(camview_client, "_slots", threading.BoundedSemaphore(2))
    cv.items = [cv.raw(f"C{i}", cam=600 + i) for i in range(1500)]          # 15 pages read in parallel
    feed = _refresh()
    assert len(feed.items) == 1500
    assert 1 <= peak[0] <= 2, peak


def test_malformed_records_are_quarantined_not_dropped_silently(cv, supervisor):
    cv.items = [cv.raw("G1"), "not-an-object", {"alarm": "broken"}, {"alarm": {"alarmType": 3}},
                cv.raw("G2", cam=502), {"alarm": {"alarmId": "G3", "cameraId": 7, "totalTimesReported": "x",
                                                  "alarmType": "fourteen", "firstInstance": "2026-09-28T10:00:00Z"}}]
    feed = _refresh()
    ids = {a["alarmId"] for a in feed.items}
    assert {"G1", "G2"} <= ids, "good records are processed"
    g3 = next((a for a in feed.items if a["alarmId"] == "G3"), None)
    assert g3 and g3["projectId"] == 77, "a record without projectId belongs to the project it was read from"
    kinds = {r["kind"] for r in dq.events()}
    assert {"malformed_record", "missing_alarm_id"} <= kinds
    s, q = supervisor.get("/api/context/quality?projectId=77")
    assert s == 200
    c = q["counters"]
    assert c["malformedRecords"] >= 3
    assert c["unknownAlarmTypes"] >= 1 and c["missingExamMapping"] >= 2 and c["missingClientMapping"] >= 2
    assert "missingEvidence" in c and c["recentEvents"]
    assert all("X-Amz-Signature" not in (e["sample"] or "") for e in c["recentEvents"])


def test_missing_evidence_is_counted(cv, supervisor):
    cv.items = [cv.raw("N1", image=False, video=False), cv.raw("N2")]
    _refresh()
    s, q = supervisor.get("/api/context/quality?projectId=77")
    assert q["counters"]["missingEvidence"] == 1
    s, d = supervisor.get("/api/queue/N1?projectId=77")
    assert s == 200 and d["alert"]["evidence"]["status"] == "not_available" and d["evidence"] == []


def test_missing_client_and_exam_mapping_is_a_safe_failure(cv, supervisor, client_a):
    _refresh()                                           # project 77 has no exam and no client
    s, r = supervisor.post("/api/queue/L1/decide", {"result": "valid", "projectId": 77, "expectedRevision": 0})
    assert s == 200, r
    assert r["delivery"]["status"] == "not_deliverable" and r["ticket"]["ref"]
    assert r["alert"]["examMapping"] == "not_mapped" and r["alert"]["clientMapping"] == "not_mapped"
    assert r["alert"]["clientVisibility"] == "internal"
    assert obs.counters()["mapping.failure"] >= 1
    assert not client_a.get("/api/client/alerts")[1]["items"] or \
        all(a["alarmId"] != "L1" for a in client_a.get("/api/client/alerts")[1]["items"])


def test_health_is_reported_never_inferred(cv, supervisor):
    _refresh()                                           # Camview reports frameSyncStatus SYNCED, nothing about recording
    s, d = supervisor.get("/api/queue/L1?projectId=77")
    h = d["alert"]
    assert h["cameraStatus"] == "online" and h["healthSource"] == "camview"
    assert h["recordingStatus"] == "unknown", "recording is never inferred from the camera being online"
    db.execute("DELETE FROM camera_health")               # health source unavailable
    s, d = supervisor.get("/api/queue/L1?projectId=77")
    h = d["alert"]
    assert h["healthAvailable"] is False and h["cameraStatus"] == "unknown" and h["recordingStatus"] == "unknown"
    assert "HEALTH_UNAVAILABLE" in h["healthConditions"]


def test_stale_heartbeat_makes_health_unknown(cv, supervisor):
    _refresh()
    db.execute("UPDATE camera_health SET last_heartbeat_at='2026-01-01T00:00:00Z'")
    s, d = supervisor.get("/api/queue/L1?projectId=77")
    assert d["alert"]["cameraStatus"] == "unknown" and "HEARTBEAT_STALE" in d["alert"]["healthConditions"]


def test_camview_status_never_makes_an_alert_client_visible(cv, supervisor):
    _refresh()                                           # L3 is VALID in Camview
    s, d = supervisor.get("/api/queue/L3?projectId=77")
    assert d["alert"]["camviewStatus"] == "valid" and d["alert"]["operatorDecision"] == "pending"
    assert d["alert"]["clientVisibility"] == "internal"
