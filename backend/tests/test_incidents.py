"""Incidents (V2): explained correlation suggestions, one open incident per alert, lifecycle, audit, access."""

import db
import routes_queue


def _suggestions(c, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    code, data = c.get(f"/api/incidents/suggestions{'?' + qs if qs else ''}")
    assert code == 200, data
    return data


def _create_first(c):
    s = _suggestions(c)["items"][0]
    code, data = c.post("/api/incidents", {"suggestionId": s["id"]})
    assert code == 201, data
    return s, data["incident"]


def test_suggestions_are_explained(supervisor):
    data = _suggestions(supervisor)
    assert data["settings"]["enabled"] and data["settings"]["gapMinutes"] == 30
    assert data["items"], "demo data has correlated alerts"
    for s in data["items"]:
        assert s["reason"] and s["ruleText"]
        assert len(s["alarmIds"]) == s["count"]
        assert s["firstAt"] <= s["lastAt"]
        assert s["severity"] in ("critical", "high", "medium", "low")
        if s["rule"] == "centre":
            assert s["count"] >= 3
            assert s["reason"].startswith(f"{s['count']} alerts at centre {s['centre']} within ")
            assert "×" in s["reason"]
        else:
            assert s["count"] >= 2
    # a larger minimum can only give fewer (or equal) centre suggestions
    strict = _suggestions(supervisor, minAlerts=6)
    assert all(s["count"] >= 6 for s in strict["items"] if s["rule"] == "centre")


def test_suggestions_exclude_camera_status_events(supervisor, monkeypatch):
    first = _suggestions(supervisor)["items"][0]
    ids = set(first["alarmIds"])
    real = routes_queue._all_alarms

    def as_status_events(user, project=None):
        items, fresh = real(user, project)
        return [{**a, "eventKind": "camera_status"} if a["alarmId"] in ids else a for a in items], fresh

    monkeypatch.setattr(routes_queue, "_all_alarms", as_status_events)
    after = _suggestions(supervisor)
    assert not any(ids & set(s["alarmIds"]) for s in after["items"])


def test_create_from_suggestion_and_linked_alarms_leave_suggestions(supervisor):
    s, inc = _create_first(supervisor)
    assert inc["ref"] == f"INC-{inc['id']:06d}"
    assert inc["status"] == "open" and inc["alarmCount"] == s["count"] and inc["reason"] == s["reason"]
    code, detail = supervisor.get(f"/api/incidents/{inc['id']}")
    assert code == 200
    assert {a["alarmId"] for a in detail["alarms"]} == set(s["alarmIds"])
    assert detail["events"][0]["kind"] == "created"
    after = _suggestions(supervisor)
    assert not any(set(s["alarmIds"]) & set(x["alarmIds"]) for x in after["items"])
    code, lst = supervisor.get("/api/incidents")
    assert code == 200 and lst["counts"]["open"] == 1 and lst["items"][0]["id"] == inc["id"]
    code, by_alarm = supervisor.get(f"/api/incidents?alarm={s['alarmIds'][0]}&status=all")
    assert [x["id"] for x in by_alarm["items"]] == [inc["id"]]


def test_one_alarm_in_one_open_incident(supervisor):
    s, inc = _create_first(supervisor)
    code, data = supervisor.post("/api/incidents", {"alarms": s["alarms"][:1], "title": "Duplicate"})
    assert code == 409 and inc["ref"] in str(data)
    # once resolved, the alert is free for a new incident; the old one cannot re-open while it is taken
    code, _ = supervisor.put(f"/api/incidents/{inc['id']}", {"status": "resolved", "resolution": "Invigilator removed phones"})
    assert code == 200
    code, data = supervisor.post("/api/incidents", {"alarms": s["alarms"][:1], "title": "Follow-up"})
    assert code == 201, data
    code, _ = supervisor.put(f"/api/incidents/{inc['id']}", {"status": "open"})
    assert code == 409


def test_status_owner_comment_in_timeline_and_audit(supervisor):
    _s, inc = _create_first(supervisor)
    iid = inc["id"]
    code, data = supervisor.put(f"/api/incidents/{iid}", {"status": "resolved"})
    assert code == 400                                         # resolving needs a resolution
    code, data = supervisor.put(f"/api/incidents/{iid}", {"status": "investigating", "ownerId": "u-op"})
    assert code == 200 and data["incident"]["status"] == "investigating"
    assert data["incident"]["owner"]["name"] == "Arjun Verma"
    code, _ = supervisor.put(f"/api/incidents/{iid}", {"ownerId": "u-client-not-a-user"})
    assert code == 400
    code, _ = supervisor.post(f"/api/incidents/{iid}/comments", {"body": "Centre superintendent called"})
    assert code == 201
    code, data = supervisor.put(f"/api/incidents/{iid}", {"status": "resolved", "resolution": "Phones confiscated"})
    assert code == 200 and data["incident"]["resolvedAt"] and data["incident"]["resolution"] == "Phones confiscated"
    _, detail = supervisor.get(f"/api/incidents/{iid}")
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds[0] == "created" and "owner" in kinds and "comment" in kinds and kinds.count("status") == 2
    assert any(e["kind"] == "comment" and e["body"] == "Centre superintendent called" for e in detail["events"])
    actions = {r["action"] for r in db.rows("SELECT action FROM audit_events WHERE resource_type='incident' AND resource_id=?",
                                            (inc["ref"],))}
    assert {"incident.create", "incident.status", "incident.comment"} <= actions
    _, lst = supervisor.get("/api/incidents?status=resolved")
    assert lst["counts"]["resolved"] == 1 and lst["resolvedToday"] == 1


def test_add_and_remove_alarms(supervisor):
    s, inc = _create_first(supervisor)
    other = next(x for x in _suggestions(supervisor)["items"] if str(x["projectId"]) == str(inc["projectId"]))
    code, data = supervisor.post(f"/api/incidents/{inc['id']}/alarms", {"add": other["alarms"][:1], "remove": s["alarmIds"][:1]})
    assert code == 200, data
    assert data["added"] == [other["alarms"][0]["alarmId"]]
    assert data["removed"] == s["alarmIds"][:1]
    _, detail = supervisor.get(f"/api/incidents/{inc['id']}")
    kinds = [e["kind"] for e in detail["events"]]
    assert "alarm_added" in kinds and "alarm_removed" in kinds


def test_client_user_gets_404(client_a, supervisor):
    _s, inc = _create_first(supervisor)
    for path in ("/api/incidents", "/api/incidents/suggestions", f"/api/incidents/{inc['id']}"):
        code, _ = client_a.get(path)
        assert code == 404
    code, _ = client_a.post("/api/incidents", {"alarmIds": ["x"]})
    assert code == 404


def test_without_investigate_cannot_create_or_change(app, supervisor):
    from tests.conftest import as_user
    _s, inc = _create_first(supervisor)
    # the investigator role keeps alarm.view + alarm.comment but loses alarm.investigate
    db.execute("DELETE FROM role_permissions WHERE role_id='investigator' AND permission='alarm.investigate'")
    inv = as_user(app, "investigator@demo.camview")
    s = _suggestions(inv)
    if s["items"]:
        code, _ = inv.post("/api/incidents", {"suggestionId": s["items"][0]["id"]})
        assert code == 403
    code, _ = inv.post("/api/incidents", {"alarmIds": ["whatever"]})
    assert code == 403
    code, _ = inv.put(f"/api/incidents/{inc['id']}", {"status": "closed"})
    assert code == 403
    code, data = inv.get("/api/incidents")
    assert code == 200 and data["can"]["manage"] is False and data["can"]["comment"] is True


def test_scope_hides_other_projects(app, supervisor):
    from tests.conftest import as_user
    _s, inc = _create_first(supervisor)
    scoped = as_user(app, "supervisor.p12@demo.camview")     # sees project 12 only
    code, lst = scoped.get("/api/incidents?status=all")
    assert code == 200
    visible = str(inc["projectId"]) == "12"
    assert (inc["id"] in [x["id"] for x in lst["items"]]) == visible
    code, _ = scoped.get(f"/api/incidents/{inc['id']}")
    assert code == (200 if visible else 404)
    assert all(str(x["projectId"]) == "12" for x in _suggestions(scoped)["items"])
