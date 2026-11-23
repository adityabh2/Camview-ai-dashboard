"""Archiving alerts and project codes; only an administrator (Super Admin) deletes, and only what is archived."""

import pytest

import config
import db
import project_codes
from tests.test_project_lookup import CODE, FakeLookup


def _ids(c, q="status=all&range=all&size=500"):
    return {a["alarmId"] for a in c.get(f"/api/queue?{q}")[1]["items"]}


def test_archived_alerts_leave_the_lists_and_can_be_restored(admin):
    ids = sorted(_ids(admin))[:3]
    assert len(ids) == 3
    s, r = admin.post("/api/archive/alerts", {"alarmIds": ids, "reason": "exam over"})
    assert s == 200 and r["archived"] == 3
    assert not set(ids) & _ids(admin)                                   # gone from the list
    assert set(ids) <= _ids(admin, "status=all&range=all&size=500&archived=1")   # in the Archived view
    s, ov = admin.get("/api/archive")
    assert {a["alarm_id"] for a in ov["alerts"]} >= set(ids)
    s, r = admin.post("/api/archive/alerts/restore", {"alarmIds": ids[:1]})
    assert s == 200 and ids[0] in _ids(admin)


def test_only_an_administrator_deletes(admin, supervisor):
    ids = sorted(_ids(admin))[:2]
    admin.post("/api/archive/alerts", {"alarmIds": ids})
    s, _ = supervisor.post("/api/archive/alerts/delete", {"alarmIds": ids})
    assert s == 403                                                     # supervisors cannot delete
    ov = admin.get("/api/archive")[1]
    if not ov["canDelete"]:
        pytest.skip("demo admin is not a Super Admin")
    s, r = admin.post("/api/archive/alerts/delete", {"alarmIds": ids})
    assert s == 200 and r["deleted"] == 2
    assert not set(ids) & _ids(admin) and not set(ids) & _ids(admin, "status=all&range=all&size=500&archived=1")


def test_delete_needs_archive_first(admin):
    if not admin.get("/api/archive")[1]["canDelete"]:
        pytest.skip("demo admin is not a Super Admin")
    one = sorted(_ids(admin))[:1]
    s, _ = admin.post("/api/archive/alerts/delete", {"alarmIds": one})
    assert s == 409


def test_archived_code_stops_monitoring_and_restores(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    admin.post("/api/project-codes", {"code": CODE})
    s, r = admin.post("/api/project-codes/archive", {"code": CODE})
    assert s == 200 and sorted(r["stoppedMonitoring"]) == ["4101", "4102"]
    extras = [str(x) for x in db.get_setting("extra_projects", [])]
    assert "4101" not in extras
    assert project_codes._load()[CODE]["archived"] is True              # kept, not removed
    s, r = admin.post("/api/project-codes/restore", {"code": CODE})
    assert s == 200 and "4101" in [str(x) for x in db.get_setting("extra_projects", [])]


def test_code_delete_is_admin_only_and_archived_only(admin, supervisor, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    admin.post("/api/project-codes", {"code": CODE})
    s, _ = supervisor.post("/api/project-codes/delete", {"code": CODE})
    assert s == 403
    if not admin.get("/api/archive")[1]["canDelete"]:
        pytest.skip("demo admin is not a Super Admin")
    s, _ = admin.post("/api/project-codes/delete", {"code": CODE})
    assert s == 409                                                     # archive first
    admin.post("/api/project-codes/archive", {"code": CODE})
    s, r = admin.post("/api/project-codes/delete", {"code": CODE})
    assert s == 200 and CODE not in project_codes._load()
