"""A registered project code always shows its alerts: its projects are monitored, the project dropdown lists only
its projects, and a project from another code never empties the list."""

import config
import db
import project_codes
from tests.test_project_lookup import CODE, FakeLookup


def _register_without_monitoring(code, pids):
    reg = project_codes._load()
    reg[code] = {"projectIds": list(pids), "names": {}, "source": "configured", "addedAt": "2026-01-01T00:00:00Z"}
    db.set_setting(project_codes.SETTING, reg)


def test_registered_code_projects_get_monitored(monkeypatch):
    _register_without_monitoring("CFG/CODE", ["4242"])
    monkeypatch.setattr(config, "EXCLUDED_PROJECTS", set())
    added = project_codes.ensure_monitored()
    assert "4242" in added
    assert "4242" in [str(x) for x in db.get_setting("extra_projects", [])]
    assert project_codes.ensure_monitored() == []                        # nothing left to add


def test_archived_and_excluded_codes_are_not_monitored(monkeypatch):
    _register_without_monitoring("OLD/CODE", ["5151"])
    reg = project_codes._load()
    reg["OLD/CODE"]["archived"] = True
    db.set_setting(project_codes.SETTING, reg)
    _register_without_monitoring("EXC/CODE", ["6161"])
    monkeypatch.setattr(config, "EXCLUDED_PROJECTS", {"6161"})
    added = project_codes.ensure_monitored()
    assert "5151" not in added and "6161" not in added
    assert project_codes.excluded_ids("EXC/CODE") == ["6161"]


def test_dropdown_lists_only_the_codes_projects(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    admin.post("/api/project-codes", {"code": CODE})
    s, q = admin.get(f"/api/queue?status=all&range=all&size=1&code={CODE.replace('&', '%26')}")
    assert s == 200 and {p[0] for p in q["facets"]["projects"]} <= {"4101", "4102"}
    s, q = admin.get(f"/api/queue?status=all&range=all&size=1&code={CODE.replace('&', '%26')}&projectId=7")
    assert s == 200 and q["totalElements"] == 0                          # a project outside the code: nothing mixed in


def test_hms_rows_per_project_code(supervisor):
    s, d = supervisor.get("/api/hms?tzOffset=330")
    assert s == 200 and isinstance(d["byCode"], list) and d["byCode"]
    assert sum(r["total"] for r in d["byCode"]) == d["totals"]["total"]
    for r in d["byCode"]:
        assert sum(r["types"].values()) == r["total"]
