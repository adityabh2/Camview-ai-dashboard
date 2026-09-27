"""
Data pipeline tests: normalization, nomenclature, intelligence (explainable),
analytics, KPIs, reports (internal vs client dataset), mock determinism.
"""

from datetime import datetime, timedelta, timezone

import alarms
import analytics
import db
import intelligence
import kpis
import mock_data
import nomenclature
import reports
import workflow

REAL_ITEM = {
    "camera": {"cameraId": 123, "cameraName": "Gate Cam", "location": "Hall 7"},
    "alarm": {"alarmId": "ALM-1", "cameraId": 123, "alarmType": 5, "projectId": 1, "alarmState": 1,
              "lastActionType": 0, "priority": 1, "firstInstance": "2026-09-24T10:00:00Z",
              "lastInstance": "2026-09-24T10:05:00Z", "totalTimesReported": 3, "ticketId": 456,
              "imageUrl": "https://img/1", "videoUrl": None, "shiftLabel": "Morning", "suppressed": False},
    "imageUrls": ["https://img/1", "https://img/2"], "serialNumber": 1,
}


# --------------------------------------------------------------------------- normalization

def test_normalize_flattens_documented_shape():
    a = alarms.normalize(REAL_ITEM, {5: "Mobile Phone"})
    assert (a["alarmId"], a["cameraId"], a["alarmTypeName"], a["priority"], a["lastActionLabel"]) == \
        ("ALM-1", "123", "Mobile Phone", "critical", "Pending")
    assert a["imageUrls"] == ["https://img/1", "https://img/2"]


def test_normalize_handles_missing_fields_and_epoch_times():
    item = {"alarm": {"alarmId": "X", "alarmType": 9, "priority": 7, "firstInstance": 1727172000000}}
    a = alarms.normalize(item)
    assert a["alarmTypeName"] == "Alert type 9" and a["priority"] == "P7" and a["firstInstance"].endswith("Z")
    assert a["cameraId"] == "" and a["totalTimesReported"] == 1


def test_type_and_camera_names_come_from_camview_when_not_in_dictionary():
    a = alarms.normalize({"alarm": {"alarmId": "A", "alarmType": 14, "alarmTypeName": "TRUCK_TAMPERING", "cameraId": 7},
                          "camera": {"cameraId": 7, "cameraName": "Gate 2 · Truck bay"}})
    assert a["alarmTypeName"] == "Truck Tampering" and a["cameraName"] == "Gate 2 · Truck bay"
    b = alarms.normalize({"alarm": {"alarmId": "B", "alarmType": 1, "alarmName": "Mobile Detection"}}, {1: "Mobile Phone"})
    assert b["alarmTypeName"] == "Mobile Phone"                     # the admin dictionary wins


def test_mock_ids_are_deterministic():
    p = mock_data.PROJECTS[0]
    ids1 = [i["alarm"]["alarmId"] for i in mock_data.generate_project_alarms(p)]
    ids2 = [i["alarm"]["alarmId"] for i in mock_data.generate_project_alarms(p)]
    assert ids1 == ids2 and len(set(ids1)) == len(ids1)


# --------------------------------------------------------------------------- nomenclature

def test_context_resolution_mapped_and_unmapped():
    ctx = nomenclature.resolve(7, 106)
    assert ctx["mapped"] and [n["level"] for n in ctx["path"]] == \
        ["project", "tc", "centre", "building", "floor", "room", "camera"]
    ctx = nomenclature.resolve(7, 199)       # deliberately unmapped demo camera
    assert not ctx["mapped"] and ctx["camera"]["unmapped"] and ctx["project"]["code"] == "PROJECT-07"
    ctx = nomenclature.resolve(999, 5)       # unknown project: nothing invented
    assert ctx["project"]["unmapped"]


def test_csv_import_and_export_roundtrip():
    csv_text = ("project_id,project_code,tc_code,centre_code,building,floor,room,camera_id,camera_code\n"
                "42,PROJECT-42,TC-91,CTR-910,A,1,101,9001,CAM-9001\n"
                "42,PROJECT-42,TC-91,CTR-910,A,1,102,9002,CAM-9002\n")
    r = nomenclature.import_data(csv_text, "csv", replace=True)
    assert r["counts"]["camera"] == 2 and r["counts"]["room"] == 2 and not r["errors"]
    assert nomenclature.resolve(42, 9002)["room"]["code"] == "102"
    assert "CAM-9001" in nomenclature.export_csv()


def test_import_reports_row_errors():
    r = nomenclature.import_data("project_id,camera_id\n,5\n", "csv")
    assert r["errors"] and "project_id is required" in r["errors"][0]


def test_quality_reports_unmapped_cameras():
    items = [{"cameraId": "106", "projectId": "7"}, {"cameraId": "199", "projectId": "7"}, {"cameraId": "", "projectId": "7"}]
    q = nomenclature.quality(items, known_types=set())
    assert q["unmappedCameras"] == ["199"] and q["alarmsMissingCamera"] == 1 and q["mappedCameras"] == 1


# --------------------------------------------------------------------------- intelligence

def _enriched(overrides):
    base = {"alarmId": "A", "projectId": "7", "cameraId": "1", "cameraCode": "CAM-1", "alarmType": 1,
            "alarmTypeName": "Phone", "priority": "critical", "priorityLevel": 1, "lastActionType": 0,
            "lastActionLabel": "Pending", "totalTimesReported": 5, "firstInstance": None, "lastInstance": None,
            "context": {"mapped": False, "path": []}, "evidence": {"count": 1}, "ageMinutes": 12, "spanMinutes": 7,
            "workflowState": "NEW", "review": {"status": "unreviewed"},
            "flags": {"pending": True, "criticalPending": True, "repeated": True, "evidence": True, "suppressed": False,
                      "new": False, "longPending": False}}
    base.update(overrides)
    return base


def test_every_alert_explains_itself():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=10)).isoformat()
    items = [_enriched({"alarmId": f"A{i}", "firstInstance": ts, "lastInstance": ts}) for i in range(6)]
    out, skipped = intelligence.evaluate(items, workflow.policy())
    kinds = {a["type"] for a in out}
    assert {"critical_pending", "repeated_activity", "high_camera_activity"} <= kinds
    for a in out:
        assert a["reasons"] and a["title"] and a["scope"]["level"] and a["provenance"] == "derived"
    rep = next(a for a in out if a["type"] == "repeated_activity")
    assert any("5 occurrences" in r for r in rep["reasons"]) and any("7" in r for r in rep["reasons"])


def test_unsupported_intelligence_is_skipped_with_reason():
    ts = datetime.now(timezone.utc).isoformat()
    out, skipped = intelligence.evaluate([_enriched({"firstInstance": ts, "lastInstance": ts})], workflow.POLICY_DEFAULTS)
    reasons = {s["type"]: s["reason"] for s in skipped}
    assert "activity_spike" in reasons and "6 h" in reasons["activity_spike"]
    assert "long_pending" in reasons                    # no SLA assumed by default
    assert "multiple_related" in reasons                # no nomenclature -> no location relationships


def test_rule_matching_and_description():
    rule = {"conditions": [{"field": "priority", "op": "eq", "value": "critical"},
                           {"field": "occurrences", "op": "gte", "value": "3"}]}
    assert intelligence.rule_matches(rule, _enriched({}))
    assert not intelligence.rule_matches(rule, _enriched({"totalTimesReported": 1}))
    assert intelligence.describe_condition(rule["conditions"][1]) == "Occurrences ≥ 3"


# --------------------------------------------------------------------------- analytics / KPIs

def test_metrics_keep_camview_and_workflow_separate():
    items = [_enriched({"lastActionType": 1, "workflowState": "SHARED", "flags": {"pending": False}}),
             _enriched({"lastActionType": 2, "flags": {"pending": False}}),
             _enriched({"lastActionType": 0})]
    m = analytics.metrics(items)
    assert (m["valid"], m["invalid"], m["pending"], m["sharedWithClient"]) == (1, 1, 1, 1)
    assert m["falseAlarmRate"] == 0.5


def test_heatmap_and_daily_shapes():
    ts = datetime.now(timezone.utc).isoformat()
    h = analytics.heatmap([{"firstInstance": ts}], days=7)
    assert len(h["dates"]) == 7 and all(len(r) == 24 for r in h["rows"]) and h["max"] == 1
    assert sum(d["total"] for d in analytics.daily([{"firstInstance": ts, "lastActionType": 1}], days=3)) == 1


def test_kpi_verdicts_separate_camview_and_ops():
    start, end, bucket, label = kpis.resolve_range("24h")
    ts = kpis.iso(datetime.now(timezone.utc) - timedelta(minutes=30))
    items = [{"alarmId": "K1", "lastActionType": 1, "firstInstance": ts, "lastInstance": ts, "alarmTypeName": "T"},
             {"alarmId": "K2", "lastActionType": 2, "firstInstance": ts, "lastInstance": ts, "alarmTypeName": "T"}]
    db.apply_action("K1", "mark_invalid", "Priya")
    r = kpis.compute(items, db.reviews_for(["K1", "K2"]), db.list_reviews(kpis.iso(start)), db.audit_stats(kpis.iso(start)),
                     start, end, bucket, label)
    assert r["camview"]["valid"] == 1 and r["camview"]["invalid"] == 1
    assert r["ops"]["marked_invalid"] >= 1 and r["ops"]["agreement"]["compared"] == 1


# --------------------------------------------------------------------------- reports

def test_client_report_is_built_from_client_dataset_only(manager):
    s, rep = manager.post("/api/reports/preview", {"type": "client_shared", "clientId": "client-a"})
    assert s == 200 and rep["meta"]["audience"] == "client"
    table = next(x for x in rep["sections"] if x["title"] == "Shared alerts")
    shared = {r["alarm_id"] for r in db.rows("SELECT alarm_id FROM publications WHERE client_id='client-a' AND status='shared'")}
    assert {row[0] for row in table["rows"]} == shared
    assert "INTERNAL" not in rep["meta"]["visibility"]


def test_internal_report_marks_itself_internal(manager):
    s, rep = manager.post("/api/reports/preview", {"type": "alarm_summary", "projectId": "7"})
    assert s == 200 and rep["meta"]["visibility"].startswith("INTERNAL")
    assert rep["meta"]["generatedBy"] == "Meera Iyer" and rep["meta"]["scope"].startswith("PROJECT-07")


def test_only_client_reports_can_be_shared_and_client_sees_only_shared(manager, client_a):
    s, internal = manager.post("/api/reports", {"type": "alarm_summary", "projectId": "7"})
    assert manager.post(f"/api/reports/{internal['id']}/share", {"share": True})[0] == 400
    s, cr = manager.post("/api/reports", {"type": "client_shared", "clientId": "client-a"})
    assert client_a.get(f"/api/client/reports/{cr['id']}")[0] == 404         # not shared yet
    assert manager.post(f"/api/reports/{cr['id']}/share", {"share": True})[0] == 200
    s, body = client_a.get(f"/api/client/reports/{cr['id']}")
    assert s == 200 and body["meta"]["client"] == "State Recruitment Board"
    assert client_a.get(f"/api/client/reports/{internal['id']}")[0] == 404


def test_report_filters():
    items = [_enriched({"alarmId": "F1", "context": {"tc": {"code": "TC-1"}}}),
             _enriched({"alarmId": "F2", "context": {"tc": {"code": "TC-2"}}})]
    assert [a["alarmId"] for a in reports.apply_filters(items, {"tc": "TC-2"})] == ["F2"]
    ev = _enriched({"alarmId": "EV", "eventKind": "camera_status", "alarmType": 10, "exam": {"id": "e1"}})
    det = _enriched({"alarmId": "D", "exam": {"id": "e1"}})
    assert [a["alarmId"] for a in reports.apply_filters([ev, det], {"exam": "e1"})] == ["D"]      # status events never reported
    assert [a["alarmId"] for a in reports.apply_filters([ev, det], {"alarmType": 10})] == ["EV"]   # unless asked for explicitly


def test_real_camview_camera_shape_names_and_label():
    """Shape observed in live listAlarms responses (camera has no name, only subLocation/cameraNumber/centerCode)."""
    import datasource
    item = {"alarm": {"alarmId": "H-1", "alarmType": 10, "cameraId": 9540, "projectId": 34, "priority": 1, "lastActionType": 0,
                      "alarmMetadata": {"message": "Camera Offline", "reason": "Camera Offline", "status": "OFFLINE"}},
            "camera": {"id": 9540, "cameraNumber": "1000056_2", "subLocation": "CONTROL ROOM", "centerCode": "41199",
                       "center": "MAHILA MAHAVIDYALAYA_KANPUR NAGAR_Uttar Pradesh"}, "imageUrls": []}
    a = alarms.normalize(item)
    assert a["alarmTypeName"] == "Camera Offline" and a["eventKind"] == "camera_status"
    assert a["cameraName"] == "CONTROL ROOM" and a["cameraNumber"] == "1000056_2" and a["centreCode"] == "41199"
    assert a["centreName"] == "MAHILA MAHAVIDYALAYA, KANPUR NAGAR, Uttar Pradesh"
    # naming in Camview's own words when there is no master data: CENTRE CODE - SUB-LOCATION - CAMERA NUMBER
    assert datasource.location_label({}, 34, "9540", a["centreCode"], a["cameraNumber"], a["cameraSubLocation"]) \
        == "41199 - CONTROL ROOM - 1000056_2"
    assert datasource.location_label({}, 34, "9540") == "34 - TC not mapped - CAM-9540"
    master = {"project": {"code": "PROJECT-07"}, "tc": {"code": "TC-0701"}, "camera": {"code": "CAM-106"}}
    assert datasource.location_label(master, 7, "106") == "PROJECT-07 - TC-0701 - CAM-106"   # master data wins


def test_event_kind_separates_camera_status_events_from_alerts():
    """Live finding: type-10 records are camera online/offline events (status in alarmMetadata, never an
    image or video); types 1/5/11 are detections with imageUrl + videoUrl + metadataUrl."""
    ev = alarms.normalize({"alarm": {"alarmId": "H", "alarmType": 10, "cameraId": 1,
                                     "alarmMetadata": {"status": "OFFLINE", "reason": "Camera Online"}}})
    assert ev["eventKind"] == "camera_status" and ev["alarmTypeName"] == "Camera Online"
    det = alarms.normalize({"alarm": {"alarmId": "D", "alarmType": 1, "cameraId": 8817, "imageUrl": "https://x/f.jpg",
                                      "videoUrl": "https://x/f.mp4", "metadataUrl": "https://x/f.json"},
                            "camera": {"id": 8817, "deviceId": "1001902", "cameraNumber": "1001902_0", "subLocation": "Camera1",
                                       "frameSyncStatus": "offline", "lastFrameSync": "2026-05-01T16:26:54.759328Z"}})
    assert det["eventKind"] == "alert" and det["imageUrls"] == ["https://x/f.jpg"] and det["metadataUrl"] == "https://x/f.json"
    assert det["frameSyncStatus"] == "OFFLINE" and det["deviceId"] == "1001902" and det["cameraName"] == "Camera1"
    assert alarms.normalize({"alarm": {"alarmId": "N", "alarmType": 3, "alarmTypeName": "CAMERA_OFFLINE"}})["eventKind"] == "camera_status"


def test_detection_metadata_is_summarised_with_boxes():
    import media
    ts = 1777568544038
    d = media.parse({"camera_id": 8817, "timestamp": ts, "models": [{"model": "yolo", "results": [
        {"label": "person", "confidence": 0.85, "box": {"x1": 287, "y1": 277, "x2": 366, "y2": 476}},
        {"label": "person", "confidence": 0.792, "box": {"x1": 391, "y1": 244, "x2": 448, "y2": 474}},
        {"label": "truck", "confidence": 0.4, "box": {"x1": 1, "y1": 2, "x2": 3, "y2": 4}}, "junk"]}]})
    assert d["summary"] == "person ×2, truck" and d["labels"][0] == {"label": "person", "count": 2, "confidence": 0.85}
    assert len(d["boxes"]) == 3 and d["models"] == ["yolo"]
    assert d["timestamp"] == datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert media.parse({"models": "garbage"})["labels"] == [] and media.detections(None) is None
