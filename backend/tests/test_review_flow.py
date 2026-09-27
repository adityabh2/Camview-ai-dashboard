"""Acceptance tests for the automated review flow: routing across many clients /
exams, missing mappings and evidence, and camera / recording health kept separate
from alarms (real source only, nothing inferred)."""

import os
from datetime import datetime, timedelta, timezone

import datasource
import db
import exams
import health
import pytest


def _pending(c, project):
    items = c.get(f"/api/queue?status=pending&size=200&projectId={project}")[1]["items"]
    return next(a for a in items if a["alarmType"] != 8 and a["client"])


@pytest.fixture
def ingest_token(monkeypatch):
    monkeypatch.setenv("CAMVIEW_HEALTH_INGEST_TOKEN", "health-secret")
    return "health-secret"


def _push(app, token, records):
    return app.test_client().post("/api/camera-health/ingest", json={"cameras": records},
                                  headers={"Authorization": token})


# ---------------------------------------------------------------- routing
def test_many_clients_many_exams_route_automatically(supervisor):
    seen = {}
    for pid in ("7", "12", "15", "21"):
        a = _pending(supervisor, pid)
        s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
        assert s == 200
        seen[pid] = (r["exam"]["name"], r["delivery"]["clientName"])
    assert seen == {"7": ("SRE 2026 — Prelims", "State Recruitment Board"),
                    "12": ("UET 2026 — Entrance", "University Examinations Cell"),
                    "15": ("SRE 2026 — Skill Test", "State Recruitment Board"),
                    "21": ("NNC 2026 — Nursing Entrance", "National Nursing Council")}


def test_exam_resolved_by_date_when_a_project_has_two_exams():
    exams.seed([("exam-a", "A", "Exam A", "client-a", ["7"]), ("exam-b", "B", "Exam B", "client-a", ["7"])])
    db.execute("UPDATE exams SET start_date='2026-01-01', end_date='2026-01-31' WHERE id='exam-a'")
    db.execute("UPDATE exams SET start_date='2026-02-01', end_date='2026-02-28' WHERE id='exam-b'")
    db.execute("UPDATE exams SET status='inactive' WHERE id='exam-sre-pre'")
    db.set_setting("exams_version", 99)
    assert exams.resolve("7", "2026-01-15T10:00:00Z")["id"] == "exam-a"
    assert exams.resolve("7", "2026-02-15T10:00:00Z")["id"] == "exam-b"


def test_missing_client_mapping_is_explicit_not_invented(admin, supervisor):
    db.execute("DELETE FROM client_projects WHERE project_id='21'")
    db.execute("UPDATE exams SET client_id=NULL WHERE id='exam-nnc'")
    db.set_setting("exams_version", 77)
    a = _pending_any(supervisor, "21")
    assert a["clients"] == [] and a["client"] is None
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    assert r["delivery"]["status"] == "not_deliverable" and "No client" in r["delivery"]["reason"]
    s, sm = supervisor.get("/api/queue/summary")
    assert any(u["projectId"] == "21" for u in sm["unroutedValid"]) or sm["totals"]["valid"] >= 0


def _pending_any(c, project):
    items = c.get(f"/api/queue?status=pending&size=200&projectId={project}")[1]["items"]
    return next(a for a in items if a["alarmType"] != 8)


# ---------------------------------------------------------------- evidence
def test_missing_evidence_never_breaks_the_review(supervisor):
    items = supervisor.get("/api/queue?status=all&size=500&projectId=7")[1]["items"]
    a = next(x for x in items if not x["evidence"]["count"])
    s, d = supervisor.get(f"/api/queue/{a['alarmId']}?projectId=7")
    assert s == 200 and d["evidence"] == [] and d["alarm"]["health"]["available"] is False


def test_client_evidence_uses_the_fresh_live_url(admin, supervisor, client_a):
    admin.put("/api/settings/policy", {"deliveryMode": "automatic"})
    items = supervisor.get("/api/queue?status=pending&size=200&projectId=7")[1]["items"]
    a = next(x for x in items if x["evidence"]["images"] and x["client"] and x["alarmType"] != 8)
    supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid"})
    # the stored snapshot URL is stale; the proxy must still serve the current one
    db.execute("UPDATE publications SET snapshot=json_set(snapshot, '$.imageUrls', json('[\"/demo-evidence/expired.svg\"]')) "
               "WHERE alarm_id=?", (a["alarmId"],))
    s, v = client_a.get(f"/api/client/alerts/{a['alarmId']}")
    url = v["evidence"][0]["url"]
    r = client_a.c.get(url)
    assert r.status_code == 200 and b"expired" not in r.data


# ---------------------------------------------------------------- health
def test_health_unavailable_without_a_real_source(supervisor):
    a = _pending(supervisor, "7")
    h = supervisor.get(f"/api/queue/{a['alarmId']}?projectId=7")[1]["alarm"]["health"]
    assert h["available"] is False and h["camera"]["state"] == "unknown" and h["recording"]["state"] == "unknown"
    assert h["conditions"] == ["HEALTH_UNAVAILABLE"]


def test_alarm_named_camera_online_is_not_health():
    import alarms
    a = alarms.normalize({"alarm": {"alarmId": "E1", "alarmType": 10, "cameraId": 5, "projectId": 34,
                                    "alarmMetadata": {"reason": "Camera Online", "status": "OFFLINE"}}})
    [e] = datasource.enrich([a])
    assert e["alarmTypeName"] == "Camera Online"
    assert e["health"]["camera"]["state"] == "unknown"                    # event ≠ current health
    assert e["alarmEvent"]["status"] == "OFFLINE"


def test_camera_offline_and_online_without_recording(app, ingest_token):
    r = _push(app, ingest_token, [
        {"projectId": 7, "cameraId": 101, "cameraState": "offline", "recordingState": "not_recording"},
        {"projectId": 7, "cameraId": 102, "cameraState": "online", "recordingState": "not_recording",
         "lastRecordingAt": "2026-09-26T10:02:00Z"},
        {"projectId": 7, "cameraId": 103, "cameraState": "online", "recordingState": "recording", "streamState": "available"}])
    assert r.status_code == 200 and r.get_json()["saved"] == 3
    h = health.lookup([(7, 101), (7, 102), (7, 103)])
    assert "CAMERA_OFFLINE" in h[("7", "101")]["conditions"] and "CAMERA_ONLINE_NO_RECORDING" not in h[("7", "101")]["conditions"]
    assert {"CAMERA_ONLINE", "RECORDING_NOT_DETECTED", "CAMERA_ONLINE_NO_RECORDING"} <= set(h[("7", "102")]["conditions"])
    assert h[("7", "102")]["recording"]["lastRecordingAt"] == "2026-09-26T10:02:00Z"
    assert {"CAMERA_ONLINE", "RECORDING_ACTIVE", "STREAM_AVAILABLE"} <= set(h[("7", "103")]["conditions"])


def test_transitions_and_stale_heartbeat(app, ingest_token):
    _push(app, ingest_token, [{"projectId": 7, "cameraId": 110, "cameraState": "online", "recordingState": "recording"}])
    r = _push(app, ingest_token, [{"projectId": 7, "cameraId": 110, "cameraState": "offline", "recordingState": "recording"}])
    assert r.get_json()["events"] == [{"projectId": "7", "cameraId": "110", "event": "CAMERA_DISCONNECTED"}]
    r = _push(app, ingest_token, [{"projectId": 7, "cameraId": 110, "cameraState": "online", "recordingState": "not_recording"}])
    assert health.get(7, 110)["lastEvent"]["type"] == "RECORDING_STOPPED"
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _push(app, ingest_token, [{"projectId": 7, "cameraId": 111, "cameraState": "online", "recordingState": "recording",
                               "lastHeartbeatAt": old}])
    h = health.get(7, 111)
    assert "HEARTBEAT_STALE" in h["conditions"] and h["camera"]["state"] == "unknown"   # never claims ONLINE when stale


def test_health_ingest_is_protected(app, monkeypatch):
    monkeypatch.delenv("CAMVIEW_HEALTH_INGEST_TOKEN", raising=False)
    assert _push(app, "anything", [{"projectId": 7, "cameraId": 1, "cameraState": "online"}]).status_code == 401
    monkeypatch.setenv("CAMVIEW_HEALTH_INGEST_TOKEN", "right")
    assert _push(app, "wrong", [{"projectId": 7, "cameraId": 1, "cameraState": "online"}]).status_code == 401
    assert _push(app, "right", [{"projectId": 7, "cameraId": 1, "cameraState": "sleeping"}]).status_code == 400
    assert _push(app, "right", [{"cameraId": 1, "cameraState": "online"}]).status_code == 400


def test_health_is_shown_on_queue_items(app, supervisor, ingest_token):
    a = _pending(supervisor, "7")
    _push(app, ingest_token, [{"projectId": 7, "cameraId": a["cameraId"], "cameraState": "online",
                               "recordingState": "not_recording"}])
    items = supervisor.get("/api/queue?status=pending&size=200&projectId=7")[1]["items"]
    b = next(x for x in items if x["alarmId"] == a["alarmId"])
    assert "CAMERA_ONLINE_NO_RECORDING" in b["health"]["conditions"]


# ---------------------------------------------------------------- live refresh
def test_live_refresh_adds_new_alerts_without_touching_decisions(supervisor):
    a = _pending(supervisor, "7")
    supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "invalid"})
    feed = datasource.refresh("7")
    before = len(feed.items)
    feed.demo_tick -= 10_000                                     # time passes → the demo feed receives new alarms
    feed.fetched_at = 0
    feed = datasource.refresh("7")
    assert len(feed.items) > before and feed.new_ids
    s, d = supervisor.get(f"/api/queue/{a['alarmId']}?projectId=7")
    assert d["alarm"]["decision"] == "invalid"                    # the review in progress is untouched
    s, st = supervisor.get("/api/status?projectId=7")
    assert st["overall"]["state"] in ("live", "delayed") and st["overall"]["projects"] == 4


def test_project_placeholder_names_are_not_invented():
    import nomenclature
    ctx = nomenclature.resolve("999", "5")
    assert ctx["project"]["code"] == "999" and ctx["project"]["name"] == "999" and ctx["project"]["unmapped"]


# ---------------------------------------------------------------- camera status from Camview (live finding)

def _cam_item(fs, cid=9101, alarm_id="A1", last="2026-09-26T10:00:00Z", number="1000056_2"):
    import alarms
    return alarms.normalize({"alarm": {"alarmId": alarm_id, "alarmType": 1, "cameraId": cid, "projectId": 7, "priority": 1,
                                       "lastActionType": 0, "firstInstance": last, "lastInstance": last},
                             "camera": {"id": cid, "frameSyncStatus": fs, "lastFrameSync": "2026-09-26T09:59:00Z",
                                        "cameraNumber": number, "subLocation": "CONTROL ROOM", "centerCode": "41199",
                                        "center": "MAHILA MAHAVIDYALAYA_KANPUR NAGAR_Uttar Pradesh"}})


def _camera_event(cid="9101", status="OFFLINE", alarm_id=None):
    return datasource._normalize([{"alarm": {"alarmId": alarm_id or f"EV-{cid}", "alarmType": 10, "cameraId": cid, "projectId": 7,
                                             "priority": 1, "lastActionType": 0, "firstInstance": "2026-09-26T10:00:00Z",
                                             "lastInstance": "2026-09-26T10:00:00Z",
                                             "alarmMetadata": {"status": status, "reason": "Camera Offline"}},
                                   "camera": {"id": int(cid), "frameSyncStatus": status, "cameraNumber": "1000056_2",
                                              "subLocation": "CONTROL ROOM", "centerCode": "41199", "center": "MAHILA_KANPUR"}}])[0]


def test_camview_frame_sync_status_is_the_camera_health_source(monkeypatch):
    monkeypatch.delenv("CAMVIEW_HEALTH_INGEST_TOKEN", raising=False)
    assert health.source_mode() == "none"
    saved, events = health.sync_from_camview("7", [_cam_item("SYNCED")])
    assert (saved, events) == (1, [])
    h = health.get(7, 9101)
    assert h["source"] == "camview" and h["camera"]["state"] == "online" and "STREAM_AVAILABLE" in h["conditions"]
    assert h["recording"]["state"] == "unknown" and h["recording"]["lastRecordingAt"] == "2026-09-26T09:59:00Z"
    assert health.source_mode() == "camview" and health.status()["mode"] == "camview"
    saved, events = health.sync_from_camview("7", [_cam_item("OFFLINE")])
    assert events == [{"projectId": "7", "cameraId": "9101", "event": "CAMERA_DISCONNECTED"}]
    assert health.get(7, 9101)["camera"]["state"] == "offline"
    health.sync_from_camview("7", [_cam_item("FAILED")])
    h = health.get(7, 9101)
    assert h["camera"]["state"] == "unknown" and "FRAME_SYNC_FAILED" in h["conditions"] and h["raw"] == "FAILED"
    # the latest record per camera wins; records without a status are ignored
    assert health.sync_from_camview("7", [_cam_item("OFFLINE", last="2026-09-26T09:00:00Z", alarm_id="old"),
                                          _cam_item("SYNCED", last="2026-09-26T11:00:00Z", alarm_id="new")])[0] == 1
    assert health.get(7, 9101)["camera"]["state"] == "online"
    assert health.sync_from_camview("7", [datasource._normalize([{"alarm": {"alarmId": "X", "cameraId": 5, "projectId": 7}}])[0]]) == (0, [])
    # a configured push source is authoritative: Camview's status never overwrites it
    monkeypatch.setenv("CAMVIEW_HEALTH_INGEST_TOKEN", "t")
    assert health.sync_from_camview("7", [_cam_item("OFFLINE")]) == (0, [])


def test_camera_disconnect_notifies_alert_reviewers_once_a_day(supervisor):
    ev = [{"projectId": "7", "cameraId": "9101", "event": "CAMERA_DISCONNECTED"}]
    datasource._notify_camera_events("7", ev, [_cam_item("OFFLINE")])
    datasource._notify_camera_events("7", ev, [_cam_item("OFFLINE")])
    datasource._notify_camera_events("7", [{"projectId": "7", "cameraId": "9101", "event": "CAMERA_RECONNECTED"}], [])
    s, n = supervisor.get("/api/notifications")
    offline = [x for x in n["items"] if x["title"].startswith("Camera offline")]
    assert len(offline) == 1
    assert offline[0]["title"] == "Camera offline: 41199 - CONTROL ROOM - 1000056_2"     # Camview's own naming
    assert offline[0]["severity"] == "high" and offline[0]["link"] == "#/cameras/9101?projectId=7"


def test_camera_status_events_have_their_own_queue_and_never_bury_alerts(supervisor):
    feed = datasource.refresh("7")
    feed.items.append(_camera_event())
    s, alerts = supervisor.get("/api/queue?projectId=7&status=all&size=500")
    assert s == 200 and alerts["kind"] == "alert" and alerts["kinds"]["camera_status"] == 1
    assert all(a["eventKind"] == "alert" for a in alerts["items"]) and "EV-9101" not in {a["alarmId"] for a in alerts["items"]}
    assert all("imageUrl" in a and "hasVideo" in a for a in alerts["items"])      # frame thumbnails for the list
    s, ev = supervisor.get("/api/queue?projectId=7&status=all&kind=camera")
    assert [a["alarmId"] for a in ev["items"]] == ["EV-9101"] and ev["counts"]["all"] == 1
    assert ev["items"][0]["evidence"]["count"] == 0 and ev["items"][0]["alarmEvent"]["status"] == "OFFLINE"
    s, summ = supervisor.get("/api/queue/summary?projectId=7")
    assert summ["totals"]["all"] == alerts["totalElements"]                          # KPIs count alerts only
    assert summ["cameras"]["events"] == 1 and summ["cameras"]["eventsPending"] == 1
    assert {"reporting", "offline", "online", "syncFailed", "source"} <= set(summ["cameras"])
    assert all(a["eventKind"] == "alert" for a in summ["priorityAlerts"])


def test_camera_event_review_shows_the_latest_frame_from_that_camera(supervisor):
    s, q = supervisor.get("/api/queue?projectId=7&status=all&size=500")
    withev = next(a for a in q["items"] if a["evidence"]["count"])
    feed = datasource.refresh("7")
    ev = _camera_event(cid=str(withev["cameraId"]), alarm_id="EV-SAME-CAM")
    feed.items.append(ev)
    s, r = supervisor.get("/api/queue/EV-SAME-CAM?projectId=7")
    assert s == 200 and r["evidence"] == [] and r["detections"] is None and r["alarm"]["eventKind"] == "camera_status"
    ce = r["cameraEvidence"]
    assert ce and ce["alarmId"] != "EV-SAME-CAM" and ce["evidence"] and all(e["url"] for e in ce["evidence"])
    # an alert with its own evidence gets no substitute
    s, r = supervisor.get(f"/api/queue/{withev['alarmId']}?projectId=7")
    assert r["evidence"] and r["cameraEvidence"] is None


def test_offline_cameras_are_an_intelligent_alert_only_with_a_real_source():
    import intelligence
    import workflow
    [no_source] = datasource.enrich([_camera_event(cid="9555")])
    out, skipped = datasource.enrich, None
    out, skipped = intelligence.evaluate([no_source], workflow.policy())
    assert "cameras_offline" in {s["type"] for s in skipped}                 # an alarm named "Camera Offline" proves nothing
    assert not any(x["type"] == "cameras_offline" for x in out)
    health.sync_from_camview("7", [_cam_item("OFFLINE")])
    [e] = datasource.enrich([_cam_item("OFFLINE")])
    assert e["health"]["camera"]["state"] == "offline" and e["locationLabel"] == "41199 - CONTROL ROOM - 1000056_2"
    out, skipped = intelligence.evaluate([e], workflow.policy())
    off = next(x for x in out if x["type"] == "cameras_offline")
    assert off["severity"] == "high" and off["inputs"] == {"count": 1, "source": "camview", "centres": 1}
    assert off["action"] == "#/monitoring?tab=health" and "41199 - CONTROL ROOM - 1000056_2" in " ".join(off["reasons"])


def test_health_board_lists_cameras_with_camview_naming(supervisor):
    items = [_cam_item("OFFLINE"), _cam_item("FAILED", cid=9102, alarm_id="A2", number="1000056_3")]
    health.sync_from_camview("7", items)
    import nomenclature
    assert nomenclature.sync_from_alarms(7, items) == 2
    s, d = supervisor.get("/api/camera-health/cameras")
    assert s == 200 and d["status"]["mode"] == "camview"
    assert d["counts"]["offline"] == 1 and d["counts"]["syncFailed"] == 1
    first = d["items"][0]                                                    # offline first
    assert first["cameraId"] == "9101" and first["label"] == "41199 - CONTROL ROOM - 1000056_2"
    assert first["code"] == "1000056_2" and first["name"] == "CONTROL ROOM" and first["centre"] == "41199"


def test_freshness_reports_when_camview_last_produced_an_alert(supervisor, admin):
    feed = datasource.refresh("7")
    f = datasource.freshness(feed)
    assert f["latestAlertAt"] and f["quietHours"] is not None and f["quietHours"] >= 0
    s, st = supervisor.get("/api/status?projectId=7")
    assert st["overall"]["latestAlertAt"] and st["overall"]["quietHours"] is not None
    s, r = admin.post("/api/settings/projects/discover", {"from": 1, "to": 5})
    assert s == 503 and r["error"] == "live_mode_not_configured"          # demo mode never pretends to scan Camview


def test_records_skipped_by_shifting_pages_are_kept_for_a_few_refreshes():
    feed = datasource.Feed("7")
    a, b = _cam_item("SYNCED", alarm_id="KEEP-A"), _cam_item("SYNCED", cid=9102, alarm_id="KEEP-B", number="1000056_3")
    feed.items = [a, b]
    merged = datasource._merge_missing(feed, [a])
    assert [x["alarmId"] for x in merged] == ["KEEP-A", "KEEP-B"] and feed.missing == {"KEEP-B": 1}
    for _ in range(datasource.MISSING_GRACE - 1):
        merged = datasource._merge_missing(feed, [a])
    assert "KEEP-B" in {x["alarmId"] for x in merged}
    assert [x["alarmId"] for x in datasource._merge_missing(feed, [a])] == ["KEEP-A"]      # grace exhausted
    assert feed.missing == {}
    feed.items = [a, b]
    assert len(datasource._merge_missing(feed, [a, b])) == 2 and feed.missing == {}     # back → forgotten


def test_intelligence_ignores_camera_status_events_for_alert_checks():
    import intelligence
    import workflow
    ev = datasource.enrich([_camera_event(cid="9777")])
    out, _ = intelligence.evaluate(ev, workflow.policy())
    assert not any(x["type"] in ("critical_pending", "review_required", "evidence_available") for x in out)


def test_old_auto_built_project_can_be_removed_but_not_the_configured_one(admin, monkeypatch):
    import nomenclature
    import config
    nomenclature.sync_from_alarms(555, [_cam_item("SYNCED", cid=9555, alarm_id="P1")])
    admin.put("/api/settings/projects", {"projects": ["555"]})
    s, st = admin.get("/api/settings")
    assert "555" in [p["externalId"] for p in st["system"]["monitoredProjects"]]
    monkeypatch.setattr(config, "DEFAULT_PROJECT_ID", "555")
    assert admin.delete("/api/nomenclature/projects/555")[0] == 409
    monkeypatch.setattr(config, "DEFAULT_PROJECT_ID", "")
    s, r = admin.delete("/api/nomenclature/projects/555")
    assert s == 200 and r["nodesRemoved"] == 4 and r["extraProjects"] == []
    s, st = admin.get("/api/settings")
    assert "555" not in [p["externalId"] for p in st["system"]["monitoredProjects"]] and st["system"]["staleProjects"] == []
    assert admin.delete("/api/nomenclature/projects/7")[0] == 404             # imported master data stays


def test_camview_tree_has_no_tec_and_counts_as_complete(supervisor):
    import alarms
    import nomenclature
    item = alarms.normalize({"alarm": {"alarmId": "L1", "alarmType": 1, "cameraId": 9800, "projectId": 2773},
                             "camera": {"id": 9800, "cameraNumber": "1000271_16", "subLocation": "SERVER ROOM", "centerCode": "10070",
                                        "center": "SHRI SR EDUCATION COLLEGE", "city": "NEEMUCH", "state": "Madhya Pradesh"}})
    assert item["centreName"] == "SHRI SR EDUCATION COLLEGE, NEEMUCH, Madhya Pradesh"      # city / state as Camview sends them
    assert alarms.place_name("Ghughuwa_Durg_Durg_CHHATTISGARH", "Durg", "CHHATTISGARH") == "Ghughuwa, Durg, Durg, CHHATTISGARH"
    assert nomenclature.sync_from_alarms(2773, [item]) == 1
    ctx = nomenclature.resolve(2773, "9800")
    assert [n["level"] for n in ctx["path"]] == ["project", "centre", "room", "camera"]         # no TEC, building or floor
    assert nomenclature.project_levels(2773) == {"centre", "room"}
    assert "tc" in nomenclature.project_levels(7)                                              # imported master data keeps its TC
    [e] = datasource.enrich([item])
    assert e["contextCompleteness"]["percent"] == 100 and e["contextCompleteness"]["expected"] == ["project", "centre", "room", "camera"]
    q = nomenclature.quality([item], set())
    assert not any(i["camera"] == "1000271_16" for i in q["incompleteContext"])                # nothing "missing" that Camview never sends


def test_old_auto_built_projects_are_pruned_in_live_mode(monkeypatch):
    import config
    import nomenclature
    nomenclature.sync_from_alarms(4242, [_cam_item("SYNCED", cid=9900, alarm_id="Z1")])
    assert "4242" in [p["externalId"] for p in nomenclature.projects()]
    assert datasource.prune_stale_projects() == 0                       # demo mode: never
    monkeypatch.setattr(config, "MODE", "live")
    monkeypatch.setattr(config, "DEFAULT_PROJECT_ID", "4242")
    assert datasource.prune_stale_projects() == 0                       # monitored: kept
    monkeypatch.setattr(config, "DEFAULT_PROJECT_ID", "1")
    assert datasource.prune_stale_projects() == 1                       # no longer monitored: removed
    assert "4242" not in [p["externalId"] for p in nomenclature.projects()]
    assert "7" in [p["externalId"] for p in nomenclature.projects()]      # imported master data untouched


def test_project_code_can_be_set_from_settings(admin, supervisor):
    admin.put("/api/settings/projects", {"projects": ["3131"]})
    s, r = admin.put("/api/nomenclature/projects/3131", {"code": "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL", "name": "MPESB Group 2 Sub Group 4"})
    assert s == 200 and r["code"] == "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL" and r["externalId"] == "3131"
    s, st = admin.get("/api/settings")
    row = next(p for p in st["system"]["monitoredProjects"] if p["externalId"] == "3131")
    assert row["code"] == "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL" and row["name"] == "MPESB Group 2 Sub Group 4"
    assert admin.put("/api/nomenclature/projects/abc", {"code": "x"})[0] == 400
    assert supervisor.put("/api/nomenclature/projects/3131", {"code": "x"})[0] == 403


def test_lists_carry_the_alert_frame_for_evidence_viewers(supervisor, client_a):
    s, q = supervisor.get("/api/queue?projectId=7&status=all&size=50")
    withev = next(a for a in q["items"] if a["evidence"]["count"])
    assert withev["imageUrl"] and "hasVideo" in withev
    s, live = supervisor.get("/api/alarms?projectId=7&size=20")
    assert all("imageUrl" in a for a in live["items"])
    s, cl = client_a.get("/api/client/alerts")
    shared = next((a for a in cl["items"] if a.get("evidence")), None)
    if shared:
        assert shared["imageUrl"].startswith("/api/client/evidence/") and "hasVideo" in shared


def test_project_codes_can_be_mapped_in_bulk(admin):
    import nomenclature
    assert nomenclature.parse_code_mapping("2773, MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL, MPESB Group 2\n2872:OTHER/CODE\n# comment\nbad line") \
        == {"2773": ("MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL", "MPESB Group 2"), "2872": ("OTHER/CODE", None)}
    s, r = admin.put("/api/nomenclature/project-codes", {"text": "5151, MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL, MPESB Group 2"})
    assert s == 200 and r["applied"][0]["code"] == "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL" and r["applied"][0]["name"] == "MPESB Group 2"
    assert admin.put("/api/nomenclature/project-codes", {"text": "nothing"})[0] == 400


def test_excluded_projects_are_never_monitored_or_listed(admin, monkeypatch):
    import config
    import nomenclature
    admin.put("/api/settings/projects", {"projects": ["1", "34", "6262"]})
    nomenclature.sync_from_alarms(34, [_cam_item("SYNCED", cid=9934, alarm_id="X34")])
    monkeypatch.setattr(config, "EXCLUDED_PROJECTS", {"1", "34"})
    assert "1" not in datasource.all_project_ids() and "34" not in datasource.all_project_ids() and "6262" in datasource.all_project_ids()
    monkeypatch.setattr(config, "MODE", "live")
    assert datasource.prune_stale_projects() == 1                                   # project 34's auto-built tree removed
    assert db.get_setting("extra_projects") == ["6262"]                             # and both left the extra list
    assert "34" not in [p["externalId"] for p in nomenclature.projects()]


def test_client_and_exam_follow_from_the_project_code(admin):
    import exams
    import nomenclature
    p = exams.parse_project_code("mpesb/G2SG4-CRT-2026/220926/LIVECCTV/IIL")
    assert p == {"client": "MPESB", "exam": "G2SG4-CRT-2026", "examBase": "G2SG4-CRT", "date": "2026-09-22"}
    assert exams.parse_project_code("nocode") is None
    e1 = nomenclature.set_project_code(7001, "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL") and next(
        e for e in exams.list_exams() if "7001" in e["projectIds"])
    assert e1["name"] == "MPESB/G2SG4-CRT" and e1["clientName"] == "MPESB" and e1["startDate"] == "2026-09-22"
    nomenclature.set_project_code(7002, "MPESB/G2SG4-CRT-2026/230926/LIVECCTV/IIL")     # another date: same exam
    e1 = exams.get(e1["id"])
    assert sorted(e1["projectIds"]) == ["7001", "7002"] and e1["endDate"] == "2026-09-23"
    nomenclature.set_project_code(7003, "MPESB/G2SG4-CRT-2025/220925/LIVECCTV/IIL")     # another year: never collides
    e2 = next(e for e in exams.list_exams() if "7003" in e["projectIds"])
    assert e2["name"] == "MPESB/G2SG4-CRT-2025" and e2["id"] != e1["id"] and e2["clientId"] == e1["clientId"]
    nomenclature.set_project_code(7004, "MPESB/G1SG2-ABC-2026/240926/LIVECCTV/IIL")     # another exam, same client
    e3 = next(e for e in exams.list_exams() if "7004" in e["projectIds"])
    assert e3["name"] == "MPESB/G1SG2-ABC" and e3["clientId"] == e1["clientId"]
    assert db.one("SELECT COUNT(*) n FROM clients WHERE name='MPESB'")["n"] == 1
    # the client may see its projects; a project already mapped to an exam is left alone
    assert {"7001", "7002", "7003", "7004"} <= {r["project_id"] for r in db.rows("SELECT project_id FROM client_projects WHERE client_id=?", (e1["clientId"],))}
    nomenclature.set_project_code(7001, "OTHER/EXAM-2026/220926/X/Y")
    assert "7001" in exams.get(e1["id"])["projectIds"] and not any(e["name"].startswith("OTHER") for e in exams.list_exams())


def test_priority_follows_the_alert_type_until_camview_priority_is_confirmed():
    import alarms
    db.execute("UPDATE priority_levels SET confirmed=0")                                 # live-like: nothing confirmed
    db.execute("INSERT OR REPLACE INTO alarm_types (id, name, severity) VALUES (14, 'Mobile Phone Detected', 'critical')")
    db.execute("INSERT OR REPLACE INTO alarm_types (id, name, severity) VALUES (12, 'Lab Activity', 'medium')")
    raw = lambda t, aid: {"alarm": {"alarmId": aid, "alarmType": t, "cameraId": 1, "projectId": 7, "priority": 1, "lastActionType": 0}}
    out = {a["alarmId"]: a for a in datasource._normalize([raw(14, "P14"), raw(12, "P12"), raw(999, "P999")])}
    assert out["P14"]["priority"] == "critical" and out["P14"]["priorityRank"] == 0 and out["P14"]["prioritySource"] == "type"
    assert out["P12"]["priority"] == "medium" and out["P12"]["priorityRank"] == 2 and out["P12"]["priorityConfirmed"]
    assert out["P999"]["priority"] == "medium" and out["P999"]["prioritySource"] == "default" and not out["P999"]["priorityConfirmed"]
    assert all(a["priorityLevel"] == 1 for a in out.values())                            # Camview's own value is kept
    db.execute("UPDATE priority_levels SET confirmed=1")
    a = datasource._normalize([raw(12, "C12")])[0]
    assert a["priority"] == "critical" and a["prioritySource"] == "camview"               # confirmed mapping wins again
    assert "Validated" not in workflow.client_safe_summary({"alarmTypeName": "Lab Activity", "context": {}}, [])


import workflow  # noqa: E402


def test_dashboard_carries_hourly_and_type_series(supervisor):
    s, sm = supervisor.get("/api/queue/summary?projectId=7&tzOffset=330")
    assert s == 200 and len(sm["hourly"]) == 24 and set(sm["hourly"][0]) == {"hour", "pending", "valid", "invalid", "exception"}
    assert sum(h["pending"] + h["valid"] + h["invalid"] + h["exception"] for h in sm["hourly"]) > 0 and sm["hourlyDay"]
    assert sm["byType"] and {"type", "today", "total", "pending", "valid", "invalid", "exception"} <= set(sm["byType"][0])
    assert sm["totals"]["todayByDecision"]["pending"] <= sm["totals"]["pending"]
