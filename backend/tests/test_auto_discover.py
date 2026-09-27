"""
Running Camview projects are monitored automatically (live mode), without anyone typing a project id.

* A project with any event within AUTO_ACTIVE_HOURS is added; a quiet or excluded one is not.
* A project an administrator removed is never added back.
* A project found this way is dropped again after AUTO_RETIRE_HOURS without events.
"""

from datetime import datetime, timedelta, timezone

import pytest

import config
import datasource
import db


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def live(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "MODE", "live")
    monkeypatch.setattr(config, "AUTO_DISCOVER", True)
    monkeypatch.setattr(config, "API_KEY", "k")
    monkeypatch.setattr(config, "EXCLUDED_PROJECTS", {"9003"})
    monkeypatch.setattr(datasource, "sync_project_codes", lambda force=False: 0)
    monkeypatch.setattr(datasource, "refresh", lambda pid, force=False: None)
    monkeypatch.setattr(datasource, "_auto", {**datasource._auto, "at": 0.0, "full_at": 0.0, "running": False})
    scans = []

    def scan(found):
        def discover(start, end, workers=12):
            scans.append((start, end))
            return {"from": start, "to": end, "projects": found}
        monkeypatch.setattr(datasource, "discover_projects", discover)
        assert datasource.auto_discover(force=True, background=False)
    return scan, scans


def test_running_projects_are_monitored_automatically(live):
    scan, scans = live
    scan([{"projectId": "9001", "latestAt": _ago(1)},            # running
          {"projectId": "9002", "latestAt": _ago(24 * 10)},      # old exam: quiet for days
          {"projectId": "9003", "latestAt": _ago(1)},            # excluded
          {"projectId": "9004", "error": "forbidden"}])
    ids = datasource.all_project_ids()
    assert "9001" in ids and not {"9002", "9003", "9004"} & set(ids)
    assert "9001" in datasource.auto_status()["projects"] and datasource.auto_status()["lastAdded"] == ["9001"]
    assert db.one("SELECT 1 FROM audit_events WHERE action='settings.projects_auto'")
    scan([{"projectId": "9001", "latestAt": _ago(0.5)}])          # idempotent
    assert datasource.all_project_ids().count("9001") == 1 and datasource.auto_status()["lastAdded"] == []
    assert scans[-1][1] >= 9001                                   # scans around the highest known id


def test_removed_project_is_not_added_back(live, admin):
    scan, _ = live
    scan([{"projectId": "9001", "latestAt": _ago(1)}])
    assert admin.delete("/api/nomenclature/projects/9001")[0] == 200
    scan([{"projectId": "9001", "latestAt": _ago(0.1)}])
    assert "9001" not in datasource.all_project_ids()
    extras = [str(x) for x in (db.get_setting("extra_projects", []) or [])]
    assert admin.put("/api/settings/projects", {"projects": extras + ["9001"]})[0] == 200   # re-added by hand
    assert "9001" not in (db.get_setting("auto_ignored", []) or [])


def test_quiet_auto_project_is_retired(live, monkeypatch):
    scan, _ = live
    scan([{"projectId": "9001", "latestAt": _ago(1)}])
    auto = db.get_setting("auto_projects", {})
    auto["9001"]["lastSeenAt"] = _ago(config.AUTO_RETIRE_HOURS + 1)
    db.set_setting("auto_projects", auto)
    scan([])
    assert "9001" not in datasource.all_project_ids() and datasource.auto_status()["lastRetired"] == ["9001"]


def test_settings_show_auto_discovery(admin):
    s, r = admin.get("/api/settings")
    assert s == 200 and r["system"]["autoDiscovery"]["enabled"] is False    # demo mode
    assert admin.post("/api/settings/projects/auto-discover", {})[0] == 503
