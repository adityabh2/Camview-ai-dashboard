"""
Final production hardening: truthfulness of health and persistence, complete ingestion, identity, backups,
readiness, and the remaining failure modes (429, DNS / reset, busy database after commit, duplicate job runs).
"""

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
import requests

import alert_store
import backup
import camview_client
import config
import datasource
import db
import dq
import health
import jobs
import tickets
import workflow
from fakecamview import FakeCamview


@pytest.fixture()
def cv(monkeypatch):
    f = FakeCamview(monkeypatch)
    f.items = [f.raw("P1", cam=701), f.raw("P2", cam=702)]
    return f


def _refresh(pid="77"):
    return datasource.refresh(pid, force=True)


# ---------------------------------------------------------------- 10-12 camera / recording truthfulness

def test_camview_frame_sync_is_never_a_recording_time(cv, supervisor):
    _refresh()
    h = health.get("77", "701")
    assert h["camera"]["state"] == "online" and h["camera"]["lastFrameAt"]
    assert h["recording"]["state"] == "unknown" and h["recording"]["lastRecordingAt"] is None
    s, d = supervisor.get("/api/queue/P1?projectId=77")
    assert d["alert"]["recordingStatus"] == "unknown" and d["alert"]["lastRecording"] is None


def test_old_rows_with_frame_sync_as_recording_time_are_migrated():
    db.execute("INSERT INTO camera_health (project_id, camera_id, camera_state, recording_state, stream_state, "
               "last_heartbeat_at, last_recording_at, source, updated_at) VALUES ('9','1','online','unknown','available',"
               "?, '2026-09-01T10:00:00Z', 'camview', ?)", (db.now_iso(), db.now_iso()))
    db.init_db()
    r = db.one("SELECT last_recording_at, last_frame_at FROM camera_health WHERE project_id='9'")
    assert r["last_recording_at"] is None and r["last_frame_at"] == "2026-09-01T10:00:00Z"


def test_a_configured_but_silent_push_source_is_reported_not_trusted(cv, monkeypatch):
    monkeypatch.setenv("CAMVIEW_HEALTH_INGEST_TOKEN", "tok")
    assert health.source_mode() == "push_not_reporting"
    _refresh()                                                   # Camview camera state still arrives…
    h = health.get("77", "701")
    assert h["camera"]["state"] == "online" and h["recording"]["state"] == "unknown"
    assert "HEALTH_SOURCE_NOT_REPORTING" in h["conditions"]      # …and the silence is said explicitly
    assert "HEALTH_SOURCE_NOT_REPORTING" in health.get("77", "999")["conditions"]
    health.ingest([{"projectId": 77, "cameraId": 701, "cameraState": "online", "recordingState": "not_recording"}])
    assert health.source_mode() == "push"
    h = health.get("77", "701")
    assert h["recording"]["state"] == "not_recording" and "HEALTH_SOURCE_NOT_REPORTING" not in h["conditions"]
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    db.execute("UPDATE camera_health SET updated_at=?, last_heartbeat_at=?", (old, old))   # the source falls silent
    assert health.source_mode() == "push_not_reporting"
    h = health.get("77", "701")
    assert h["camera"]["state"] == "unknown" and "HEALTH_SOURCE_NOT_REPORTING" in h["conditions"]


def test_camera_offline_and_recording_states_stay_separate():
    health.ingest([{"projectId": 5, "cameraId": 1, "cameraState": "offline", "recordingState": "unknown"},
                   {"projectId": 5, "cameraId": 2, "cameraState": "online", "recordingState": "not_recording"},
                   {"projectId": 5, "cameraId": 3, "cameraState": "online", "recordingState": "unknown"}])
    a, b, c = (health.get(5, i) for i in (1, 2, 3))
    assert a["camera"]["state"] == "offline" and a["recording"]["state"] == "unknown" and "CAMERA_OFFLINE" in a["conditions"]
    assert b["recording"]["state"] == "not_recording" and "CAMERA_ONLINE_NO_RECORDING" in b["conditions"]
    assert c["camera"]["state"] == "online" and c["recording"]["state"] == "unknown"      # never inferred
    with pytest.raises(Exception):
        health.ingest([{"projectId": 5, "cameraId": 4, "cameraState": "weird"}])


# ---------------------------------------------------------------- 3 canonical store failure is visible

def test_received_but_not_persisted_is_never_reported_as_healthy(cv, supervisor, client, monkeypatch):
    real = alert_store._collisions

    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    _refresh()
    cv.items.append(cv.raw("P3", cam=703))
    monkeypatch.setattr(alert_store, "_collisions", broken)        # the store cannot write…
    feed = _refresh()
    monkeypatch.setattr(alert_store, "_collisions", real)
    assert "P3" in {a["alarmId"] for a in feed.items}                # …the data was received and is shown…
    f = datasource.freshness(feed)
    assert f["persisted"] is False and f["persistError"]              # …but says it was NOT persisted
    s, st = supervisor.get("/api/status?projectId=77")
    assert st["overall"]["notPersisted"] == ["77"]
    r = client.c.get("/readyz")
    assert r.status_code == 503 and r.get_json()["checks"]["alertStore"] == "failing"
    _refresh()                                                       # storage works again: recovers by itself
    assert datasource.freshness(datasource.refresh("77"))["persisted"] is True
    assert alert_store.get("P3") and client.c.get("/readyz").status_code == 200


# ---------------------------------------------------------------- 2 identity: alarmId + projectId

def test_an_alarm_id_from_another_project_never_overwrites_the_stored_alert(cv, supervisor):
    _refresh()
    other = dict(datasource.refresh("77").items[0], projectId=88, cameraId="999")
    alert_store.save("88", [other])
    assert alert_store.get("P1")["projectId"] == 77 and alert_store.get("P1")["cameraId"] == "701"
    assert any(e["kind"] == "alarm_id_collision" and e["alarm_id"] == "P1" for e in dq.events())
    assert supervisor.get("/api/queue/P1?projectId=88")[0] == 404      # identity includes the project
    assert supervisor.get("/api/queue/P1?projectId=77")[0] == 200


# ---------------------------------------------------------------- 4 complete ingestion beyond the window

def test_projects_larger_than_the_window_are_swept_completely(cv, monkeypatch):
    monkeypatch.setattr(config, "WINDOW_PAGES", 5)
    cv.items = [cv.raw(f"W{i:04d}", cam=800 + i % 50) for i in range(1200)]          # 12 pages of 100
    seen = set()
    for _ in range(12):
        feed = _refresh()
        f = datasource.freshness(feed)
        assert f["windowLimited"] is True and f["coverage"]["pagesTotal"] == 12
        seen |= {a["alarmId"] for a in feed.items if not a.get("_stored")}
        if f["coverage"]["complete"]:
            break
    assert f["coverage"]["complete"] and f["coverage"]["lastFullCycleAt"]
    assert len(seen) == 1200, f"every page must be read within one sweep cycle ({len(seen)} of 1200)"
    assert db.one("SELECT COUNT(*) n FROM alerts WHERE project_id='77'")["n"] == 1200
    assert len(feed.items) == 1200                                  # the working set is complete (store fills in)
    cp = db.get_setting("ingest:77")
    assert cp["cursor"] and cp["pages"] == 12                        # durable checkpoint
    datasource.reset()                                               # restart: the sweep continues where it was
    _refresh()
    assert db.get_setting("ingest:77")["cursor"] != cp["cursor"] or cp["cursor"] == 5


def test_a_complete_read_is_not_window_limited(cv):
    f = datasource.freshness(_refresh())
    assert f["windowLimited"] is False and f["coverage"]["complete"]


# ---------------------------------------------------------------- 28 remaining Camview failure modes

def test_429_backs_off_for_at_least_retry_after(cv, monkeypatch):
    _refresh()

    class R429:
        status_code = 429
        headers = {"Retry-After": "120"}
        text = "slow down"

        def json(self):
            return {}
    monkeypatch.setattr(requests, "post", lambda *a, **k: R429())
    feed = _refresh()
    assert feed.last_error["code"] == "rate_limited" and len(feed.items) == 2
    assert feed.retry_at - datetime.now(timezone.utc).timestamp() >= 110
    assert camview_client.breaker_state()["consecutiveFailures"] >= 1


@pytest.mark.parametrize("exc", [requests.exceptions.ConnectionError("Name or service not known"),
                                 requests.exceptions.ConnectionError("Connection reset by peer"),
                                 requests.exceptions.ChunkedEncodingError("reset")])
def test_dns_failure_and_connection_reset(cv, monkeypatch, exc):
    _refresh()

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(requests, "post", boom)
    feed = _refresh()
    assert feed.last_error["code"] == "network_error" and len(feed.items) == 2
    assert datasource.freshness(feed)["state"] == "delayed" and datasource.freshness(feed)["lastFailureAt"]


# ---------------------------------------------------------------- 24 job recovery

def test_busy_database_after_commit_is_not_a_false_failure(supervisor, monkeypatch):
    workflow.set_policy({"deliveryMode": "automatic"}, None)
    s, q = supervisor.get("/api/queue?projectId=7&status=pending&size=5")
    a = q["items"][0]

    def busy(*x, **k):
        raise db.DatabaseBusy("database is locked")
    monkeypatch.setattr(jobs, "run", busy)
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid", "projectId": 7, "expectedRevision": 0})
    assert s == 200 and r["delivery"]["status"] == "queued", r            # committed; the worker delivers it
    monkeypatch.undo()
    assert jobs.run_due() == 1 and tickets.for_alarm(a["alarmId"])["deliveryStatus"] == "delivered"


def test_the_same_job_run_twice_at_once_delivers_once(supervisor, monkeypatch):
    workflow.set_policy({"deliveryMode": "automatic"}, None)
    s, q = supervisor.get("/api/queue?projectId=7&status=pending&size=5")
    a = q["items"][0]
    monkeypatch.setattr(jobs, "run", lambda job_id, ignore_schedule=True: jobs.get(job_id))
    supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid", "projectId": 7})
    monkeypatch.undo()
    job = jobs.by_key(f"deliver:{a['alarmId']}:r1:client-a")
    calls = []
    real = tickets.deliver
    monkeypatch.setattr(tickets, "deliver", lambda *x, **k: (calls.append(1), real(*x, **k)))
    threads = [threading.Thread(target=jobs.run, args=(job["id"],)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and jobs.get(job["id"])["status"] == jobs.DONE
    assert db.one("SELECT COUNT(*) n FROM publications WHERE alarm_id=?", (a["alarmId"],))["n"] == 1


# ---------------------------------------------------------------- 33 database robustness

def test_required_indexes_exist_and_integrity_is_checked():
    idx = {r["name"] for r in db.rows("SELECT name FROM sqlite_master WHERE type='index'")}
    for name in ("idx_alerts_project", "idx_tickets_client", "idx_pub_client_status", "idx_jobs_due", "idx_review_status"):
        assert name in idx, name
    assert db.check_integrity(full=True) and db.INTEGRITY["ok"]


def test_readiness_fails_on_integrity_or_incomplete_live_configuration(client, monkeypatch):
    monkeypatch.setitem(db.INTEGRITY, "ok", False)
    r = client.c.get("/readyz")
    assert r.status_code == 503 and r.get_json()["checks"]["integrity"] == "failed"
    monkeypatch.setitem(db.INTEGRITY, "ok", True)
    monkeypatch.setattr(config, "MODE", "live")
    monkeypatch.setattr(config, "API_KEY", "")
    r = client.c.get("/readyz")
    assert r.status_code == 503 and r.get_json()["checks"]["configuration"] == "incomplete"


# ---------------------------------------------------------------- 34 backup / restore

def test_backup_is_verified_pruned_and_restorable(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMVIEW_BACKUP_KEEP", "2")
    db.execute("INSERT INTO settings (key, value) VALUES ('marker', '\"before\"')")
    infos = []
    for _ in range(3):
        infos.append(backup.create(dest_dir=str(tmp_path)))
        import time
        time.sleep(1.1)                                              # distinct timestamps
    listed = backup.list_backups(str(tmp_path))
    assert len(listed) == 2 and all(backup.verify(b["file"])[0] for b in listed)     # retention + restore test
    db.set_setting("marker", "after")                                # data changes after the backup…
    target = str(tmp_path / "restored.db")
    import shutil
    shutil.copy(db.DB_PATH, target)
    backup.restore(listed[0]["file"], target=target)                  # …restore brings the backup's state back
    r = sqlite3.connect(target).execute("SELECT value FROM settings WHERE key='marker'").fetchone()
    assert r[0] == '"before"'
    assert any(n.startswith("pre-restore-") for n in os.listdir(tmp_path))        # the replaced database is kept


def test_a_corrupt_backup_is_refused(tmp_path):
    bad = tmp_path / "camview-bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    ok, detail = backup.verify(str(bad))
    assert not ok
    with pytest.raises(RuntimeError):
        backup.restore(str(bad), target=str(tmp_path / "t.db"))


def test_daily_backup_runs_only_in_live_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMVIEW_BACKUP_DIR", str(tmp_path))
    monkeypatch.setitem(backup._state, "lastAttempt", 0.0)
    assert backup.maybe_daily() is None                               # demo / tests: never
    monkeypatch.setattr(config, "MODE", "live")
    monkeypatch.setenv("CAMVIEW_TESTING", "0")
    info = backup.maybe_daily()
    assert info and info["verified"] and backup.status()["count"] == 1 and not backup.status()["overdue"]
    monkeypatch.setitem(backup._state, "lastAttempt", 0.0)
    assert backup.maybe_daily() is None                               # already one today


# ---------------------------------------------------------------- 32 performance: batched client firewall

def test_batched_client_list_matches_the_single_alert_path(client_a):
    import rbac
    user = rbac.load_user(db.one("SELECT id FROM users WHERE email='client.admin@client-a.demo'")["id"])
    listed = workflow.client_visible_alarms(user)
    assert listed
    for v in listed:
        row = db.one("SELECT * FROM publications WHERE alarm_id=? AND client_id=?", (v["alarmId"], user["clientId"]))
        single = workflow.build_client_visible_alarm(user, row)
        assert single["alarmId"] == v["alarmId"] and single["ticketRef"] == v["ticketRef"]


def test_a_failing_database_never_crashes_a_refresh(cv, monkeypatch):
    """Regression: recording a refresh failure writes a notification; with the database failing too, refresh()
    used to raise out of the request (a crash instead of DELAYED / DISCONNECTED)."""
    _refresh()
    cv.mode = 500
    import notify

    def down(*a, **k):
        raise sqlite3.OperationalError("database disk image is malformed")
    monkeypatch.setattr(notify, "to_permission", down)
    feed = _refresh()                                                 # must not raise
    assert feed.last_error["code"] == "upstream_error" and len(feed.items) == 2


def test_old_records_seen_for_the_first_time_are_not_new_alerts(cv):
    """Regression: records of pages read for the first time (window sweep, restart, shifting pages) were announced
    as NEW alerts — one notification round per record, which made a single refresh take ~26 s and would flood
    everyone. New = first seen AND raised within NEW_ALERT_MINUTES."""
    _refresh()                                                        # baseline
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cv.items += [cv.raw(f"OLD{i}", cam=900 + i, at=old) for i in range(50)]
    feed = _refresh()
    assert not any(k.startswith("OLD") for k in feed.new_ids)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cv.items.append(cv.raw("FRESH1", cam=950, at=now))
    feed = _refresh()
    assert "FRESH1" in feed.new_ids
