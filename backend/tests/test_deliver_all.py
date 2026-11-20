"""CAMVIEW_CLIENT_DELIVERY=all: every detection alert reaches its client on arrival (nobody marks VALID /
INVALID); an administrator revokes single alerts, and a revoked alert is never sent again automatically."""

from datetime import datetime, timedelta, timezone

import pytest

import config
import db
import tickets
import workflow


def _recent(minutes=5):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _alarm(aid, state, project=7, camera=101, kind=None):
    a = {"alarmId": aid, "projectId": project, "cameraId": str(camera), "alarmType": 1,
         "alarmTypeName": "Mobile Phone Detected", "priority": "high", "lastActionType": state,
         "firstInstance": _recent(), "lastInstance": _recent(), "totalTimesReported": 1, "imageUrls": []}
    if kind:
        a["eventKind"] = kind
    return a


@pytest.fixture()
def deliver_all(monkeypatch):
    monkeypatch.setattr(config, "DELIVER_ALL", True, raising=False)


def _client_ids(client_a):
    return {a["alarmId"] for a in client_a.get("/api/client/alerts")[1]["items"]}


def test_default_keeps_the_valid_only_rule():
    p = workflow.policy()
    assert p["clientsSeeOperatorValidOnly"] is True and p["deliveryTrigger"] == "valid"


def test_policy_in_deliver_all_mode(deliver_all):
    p = workflow.policy()
    assert p["clientsSeeOperatorValidOnly"] is False
    assert p["deliveryTrigger"] == "all" and p["autoShareValid"] is True and p["manualReview"] is False


def test_every_alert_whatever_its_camview_status_reaches_the_client(deliver_all, client_a):
    items = [_alarm("ALL-P", 0), _alarm("ALL-V", 1), _alarm("ALL-I", 2), _alarm("ALL-E", 3)]
    delivered, withdrawn = tickets.auto_sync(items)
    assert delivered == 4 and withdrawn == 0
    assert {"ALL-P", "ALL-V", "ALL-I", "ALL-E"} <= _client_ids(client_a)


def test_camera_status_events_are_never_sent(deliver_all, client_a):
    tickets.auto_sync([_alarm("ALL-CAM", 0, kind="camera_status")])
    assert "ALL-CAM" not in _client_ids(client_a)


def test_revoked_alert_is_not_sent_again_automatically(deliver_all, client_a):
    tickets.auto_sync([_alarm("ALL-R", 0)])
    assert "ALL-R" in _client_ids(client_a)
    t = db.one("SELECT id FROM tickets WHERE alarm_id='ALL-R' AND status='open'")
    tickets.withdraw(t["id"], tickets.SYSTEM_USER, "Revoked by administrator")
    assert "ALL-R" not in _client_ids(client_a)
    tickets.auto_sync([_alarm("ALL-R", 1)])                        # next refresh: still revoked
    assert "ALL-R" not in _client_ids(client_a)


def test_camview_marking_invalid_does_not_withdraw(deliver_all, client_a):
    tickets.auto_sync([_alarm("ALL-X", 0)])
    _, withdrawn = tickets.auto_sync([_alarm("ALL-X", 2)])
    assert withdrawn == 0 and "ALL-X" in _client_ids(client_a)


def test_existing_deliveries_are_not_withdrawn_by_the_old_rule(deliver_all, client_a):
    tickets.auto_sync([_alarm("ALL-K", 0)])
    assert tickets.enforce_operator_valid_only() == (0, 0)
    assert "ALL-K" in _client_ids(client_a)
