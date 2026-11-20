"""Core rule: clients see ONLY alerts the operations team marked VALID.

Camview's own VALID status, delivery on arrival and automatic sharing never make an alert client-visible —
whatever the stored settings say (older versions allowed it). Operator decisions always win."""

from datetime import datetime, timedelta, timezone

import datasource
import db
import tickets


def _on(admin, hours=24):
    assert admin.put("/api/settings/policy", {"deliveryMode": "automatic", "autoShareValid": True,
                                              "autoShareHours": hours})[0] == 200


def _recent(minutes=5):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _alarm(aid, state, project=7, camera=101):
    return {"alarmId": aid, "projectId": project, "cameraId": str(camera), "alarmType": 1,
            "alarmTypeName": "Mobile Phone Detected", "priority": "high", "lastActionType": state,
            "firstInstance": _recent(), "lastInstance": _recent(), "totalTimesReported": 1, "imageUrls": []}


def test_camview_valid_is_never_delivered_to_clients(admin, client_a):
    _on(admin)
    items = [_alarm("AUTO-V", 1), _alarm("AUTO-P", 0), _alarm("AUTO-I", 2), _alarm("AUTO-E", 3)]
    assert tickets.auto_sync(items) == (0, 0)
    assert db.one("SELECT COUNT(*) n FROM tickets WHERE alarm_id LIKE 'AUTO-%'")["n"] == 0
    ids = {a["alarmId"] for a in client_a.get("/api/client/alerts")[1]["items"]}
    assert not ids & {"AUTO-V", "AUTO-P", "AUTO-I", "AUTO-E"}


def test_earlier_automatic_deliveries_are_withdrawn(client_a):
    """An older version delivered Camview-VALID alerts on its own: at start-up those are withdrawn (quietly,
    once, audited) and the client firewall hides them even before that."""
    now = db.now_iso()
    db.execute("INSERT INTO publications (alarm_id, client_id, status, project_id, shared_by, shared_at, evidence, "
               "share_context, snapshot, updated_at) VALUES ('LEGACY-1','client-a','shared','7','Auto-share (automatic delivery)',"
               "?, '[]', '[]', ?, ?)", (now, db.jdump({"alarmId": "LEGACY-1", "projectId": 7}), now))
    db.execute("INSERT INTO tickets (ref, alarm_id, client_id, project_id, status, result, validated_by, delivery_status, "
               "created_at, updated_at) VALUES ('TKT-LEG','LEGACY-1','client-a','7','open','valid',"
               "'Auto-share (automatic delivery)','delivered',?,?)", (now, now))
    assert "LEGACY-1" not in {a["alarmId"] for a in client_a.get("/api/client/alerts")[1]["items"]}
    assert client_a.get("/api/client/alerts/LEGACY-1")[0] == 404
    assert tickets.enforce_operator_valid_only() == (1, 1)
    assert db.one("SELECT status FROM publications WHERE alarm_id='LEGACY-1'")["status"] == "withdrawn"
    assert tickets.for_alarm("LEGACY-1")["status"] == "cancelled"
    assert tickets.enforce_operator_valid_only() == (0, 0)                     # idempotent


def test_operator_decision_wins_over_camview(admin, supervisor):
    _on(admin)
    a = _alarm("AUTO-O", 1)
    user = supervisor.get("/api/auth/session")[1]["user"]
    db.apply_action("AUTO-O", "mark_invalid", user["name"], None, user={**user, "permissions": []}, project_id=7)
    assert tickets.auto_sync([a]) == (0, 0)
    assert tickets.for_alarm("AUTO-O") is None


def test_off_or_old_alerts_are_not_pushed(admin):
    assert tickets.auto_sync([_alarm("AUTO-F", 1)]) == (0, 0)       # tests default: off
    _on(admin, hours=1)
    old = _alarm("AUTO-OLD", 1)
    old["firstInstance"] = old["lastInstance"] = _recent(minutes=180)
    assert tickets.auto_sync([old]) == (0, 0)


def test_refresh_never_delivers_by_itself(admin, client_a):
    _on(admin)
    datasource.reset()
    datasource.refresh("7", force=True)
    n = db.one("SELECT COUNT(*) n FROM tickets WHERE validated_by LIKE 'Auto-share%'")["n"]
    assert n == 0


def test_manual_review_cannot_be_switched_off(admin, supervisor):
    admin.put("/api/settings/policy", {"manualReview": False})          # an older setting: no longer honoured
    a = supervisor.get("/api/queue?status=pending&size=1")[1]["items"][0]
    assert supervisor.get(f"/api/queue/{a['alarmId']}")[1]["manualReview"] is True
    s, body = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid", "projectId": a["projectId"]})
    assert s == 200, body


def test_the_rule_itself_cannot_be_switched_off(admin):
    s, body = admin.put("/api/settings/policy", {"clientsSeeOperatorValidOnly": False})
    assert s == 400 and body["error"] == "policy_locked"
    db.set_setting("policy", {"clientsSeeOperatorValidOnly": False})    # even written straight into the database
    import workflow
    assert workflow.policy()["clientsSeeOperatorValidOnly"] is True


def test_nomenclature_is_built_from_camview_camera_data():
    import alarms
    import nomenclature
    raw = [{"alarm": {"alarmId": f"N{i}", "alarmType": 1, "cameraId": 9000 + i, "projectId": 77},
            "camera": {"id": 9000 + i, "cameraNumber": f"100_{i}", "subLocation": "CLASS ROOM", "centerCode": "41147",
                       "center": "GOVT COLLEGE_KANPUR"}} for i in range(3)]
    items = [alarms.normalize(r) for r in raw]
    assert nomenclature.sync_from_alarms(77, items) == 3
    assert nomenclature.sync_from_alarms(77, items) == 0                 # idempotent
    ctx = nomenclature.resolve(77, "9001")
    assert ctx["mapped"] and ctx["centre"]["code"] == "41147" and ctx["centre"]["name"] == "GOVT COLLEGE, KANPUR"
    assert ctx["room"]["code"] == "CLASS ROOM" and ctx["camera"]["code"] == "100_1"     # Camview's own cameraNumber
    assert ctx["camera"]["name"] == "CLASS ROOM" and "tec" not in ctx and "tc" not in ctx   # nothing invented
    # auto-built nodes from before (coded CAM-<id>) are upgraded to Camview's camera number in place
    import db
    db.execute("UPDATE nomenclature SET code='CAM-9002', name='old' WHERE external_id='9002' AND level='camera'")
    db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
    nomenclature._cache["checked"] = 0
    assert nomenclature.resolve(77, "9002")["camera"]["code"] == "CAM-9002"
    assert nomenclature.sync_from_alarms(77, items) == 0
    assert nomenclature.resolve(77, "9002")["camera"]["code"] == "100_2"
    # Camview's tcCode, when it sends one, becomes the TC level
    tc_items = [alarms.normalize({"alarm": {"alarmId": "T1", "alarmType": 1, "cameraId": 9100, "projectId": 78},
                                  "camera": {"id": 9100, "cameraNumber": "200_0", "subLocation": "Camera1", "centerCode": "500",
                                             "center": "X_Y", "tcCode": "TC-5"}})]
    assert nomenclature.sync_from_alarms(78, tc_items) == 1
    ctx = nomenclature.resolve(78, "9100")
    assert ctx["tc"]["code"] == "TC-5" and ctx["centre"]["code"] == "500" and ctx["camera"]["code"] == "200_0"
    assert "77" in [p["externalId"] for p in nomenclature.projects()]
    proj = nomenclature.resolve(77, "9001")["project"]
    assert proj["code"] == "77" and proj["source"] == "camview"                  # the code IS Camview's project id
    # an older auto-built project node coded PROJECT-77 is upgraded in place
    db.execute("UPDATE nomenclature SET code='PROJECT-77' WHERE external_id='77' AND level='project'")
    db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
    nomenclature._cache["checked"] = 0
    nomenclature.sync_from_alarms(77, items)
    assert nomenclature.resolve(77, "9001")["project"]["code"] == "77"
    # the exam's own code replaces the number and survives the next sync; the env mapping applies it at start-up
    import config
    nomenclature.set_project_code(77, "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL")
    nomenclature.sync_from_alarms(77, items)
    assert nomenclature.resolve(77, "9001")["project"]["code"] == "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL"
    assert config.parse_project_codes("77:MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL, 5:OLD,x:bad") == {"77": "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL", "5": "OLD"}
    assert nomenclature.apply_project_codes({"77": "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL", "4321": "NEW/CODE"}) == 1
    assert next(p for p in nomenclature.projects() if p["externalId"] == "4321")["code"] == "NEW/CODE"
    nomenclature.set_project_code(77, "")                                    # empty → back to the Camview id
    assert nomenclature.resolve(77, "9001")["project"]["code"] == "77"
    # an auto-built project does not keep itself monitored; master-data projects and the default do
    assert "77" not in datasource.all_project_ids() and "7" in datasource.all_project_ids()
    assert "77" not in [p["externalId"] for p in nomenclature.projects(include_auto=False)]
    # ...and its data can be removed; imported master data cannot be removed this way
    assert nomenclature.delete_auto_project("77") == 6 and nomenclature.resolve(77, "9001")["mapped"] is False
    assert nomenclature.delete_auto_project("7") is None
    assert nomenclature.sync_from_alarms(7, [alarms.normalize({"alarm": {"alarmId": "X", "cameraId": 101, "projectId": 7}})]) == 0


def test_summary_has_live_totals(supervisor):
    s, sm = supervisor.get("/api/queue/summary")
    t = sm["totals"]
    assert t["all"] == t["pending"] + t["valid"] + t["invalid"] + t["exception"] and t["latestAlertAt"]
    assert sm["projects"] and "unroutedValid" in sm


def test_camview_valid_without_client_creates_nothing(admin):
    _on(admin)
    assert tickets.auto_sync([_alarm("AUTO-NC", 1, project=99)]) == (0, 0)
    assert tickets.for_alarm("AUTO-NC") is None


def test_delivery_on_arrival_is_not_possible(admin, client_a):
    """Arrival delivery (every detection alert to the client at once) contradicts the core rule: the setting is
    ignored and nothing reaches a client before an operator marks it VALID."""
    _on(admin)
    admin.put("/api/settings/policy", {"deliveryTrigger": "arrival"})
    import workflow
    assert workflow.policy()["deliveryTrigger"] == "valid"
    items = [_alarm("ARR-P", 0), _alarm("ARR-V", 1)]
    assert tickets.auto_sync(items) == (0, 0)
    ids = {a["alarmId"] for a in client_a.get("/api/client/alerts")[1]["items"]}
    assert not ids & {"ARR-P", "ARR-V"}
