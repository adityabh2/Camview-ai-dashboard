"""Bearer-token credentials and the Project Lookup API (project code -> Camview projects)."""

import os

import pytest
import requests

import camview_client
import config
import db
import nomenclature
from tests.fakecamview import Resp

CODE = "RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"


# --------------------------------------------------------------------------- Authorization header

@pytest.mark.parametrize("stored, sent", [
    ("raw-scoped-key", "raw-scoped-key"),
    ("Bearer abc.def", "Bearer abc.def"),
    ("bearer   abc", "Bearer abc"),
    (JWT, "Bearer " + JWT),
    ("  raw-key  ", "raw-key"),
])
def test_auth_header(stored, sent):
    assert camview_client.auth_header(stored) == sent


def test_credential_kind():
    assert camview_client.credential_kind("") is None
    assert camview_client.credential_kind("raw") == "api_key"
    assert camview_client.credential_kind("Bearer x") == "bearer"
    assert camview_client.credential_kind(JWT) == "bearer"


def test_list_alarms_sends_bearer_token(monkeypatch):
    seen = {}

    def fake(url, json=None, headers=None, timeout=None):
        seen["auth"] = headers["Authorization"]
        return Resp(200, {"cameraAlarmsDetails": [], "totalElements": 0, "totalPages": 0, "hasNext": False})

    monkeypatch.setattr(config, "API_KEY", "Bearer user-token")
    monkeypatch.setattr(requests, "post", fake)
    camview_client.list_page({"projectId": 5})
    assert seen["auth"] == "Bearer user-token"


def test_expired_bearer_token_message(monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "Bearer old")
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(403, {"errorCode": 403}))
    with pytest.raises(camview_client.ApiError) as e:
        camview_client.list_page({"projectId": 5})
    assert "expired" in e.value.message


# --------------------------------------------------------------------------- Project Lookup API

class FakeLookup:
    def __init__(self, monkeypatch, projects=None, status=200):
        self.projects = projects if projects is not None else [
            {"projectId": 4101, "clientProjectId": CODE, "name": "JKSSB Exam Centres", "status": "ACTIVE"},
            {"projectId": 4102, "clientProjectId": CODE, "name": "JKSSB Strong Rooms", "status": "ACTIVE"}]
        self.status, self.calls = status, []
        monkeypatch.setattr(requests, "post", self)

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "auth": headers.get("Authorization")})
        if url.endswith("/api/project/listProjectsByClientProjectId"):
            if self.status != 200:
                return Resp(self.status, {"errorCode": self.status, "description": "API key is not permitted to access this endpoint"})
            hits = [p for p in self.projects if p["clientProjectId"] == json["clientProjectId"]]
            return Resp(200, {"clientProjectId": json["clientProjectId"], "count": len(hits), "projects": hits})
        return Resp(200, {"cameraAlarmsDetails": [], "totalElements": 0, "totalPages": 0, "hasNext": False})


def test_lookup_sends_code_in_body_with_project_key(monkeypatch):
    fake = FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "alarm-key")
    monkeypatch.setattr(config, "PROJECT_API_KEY", JWT)
    out = camview_client.lookup_projects(f"  {CODE} ")
    assert [p["projectId"] for p in out] == [4101, 4102]
    call = fake.calls[0]
    assert call["json"] == {"clientProjectId": CODE}            # slashes and & untouched, spaces trimmed
    assert call["auth"] == "Bearer " + JWT
    assert call["url"] == config.PROJECT_LOOKUP_URL


def test_lookup_falls_back_to_alarm_key(monkeypatch):
    fake = FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "alarm-key")
    monkeypatch.setattr(config, "PROJECT_API_KEY", "")
    camview_client.lookup_projects(CODE)
    assert fake.calls[0]["auth"] == "alarm-key"


def test_lookup_403_explains_scoped_key(monkeypatch):
    FakeLookup(monkeypatch, status=403)
    monkeypatch.setattr(config, "API_KEY", "alarm-key")
    monkeypatch.setattr(config, "PROJECT_API_KEY", "")
    with pytest.raises(camview_client.ApiError) as e:
        camview_client.lookup_projects(CODE)
    assert "Project lookup key" in e.value.message


def test_lookup_needs_a_code(monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "k")
    with pytest.raises(camview_client.ApiError):
        camview_client.lookup_projects("   ")


def test_lookup_url_follows_api_url():
    assert config.PROJECT_LOOKUP_URL.endswith("/api/project/listProjectsByClientProjectId")
    assert "/alarms/" not in config.PROJECT_LOOKUP_URL


def test_lookup_route_monitors_and_sets_code(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "alarm-key")
    monkeypatch.setattr(config, "PROJECT_API_KEY", "Bearer t")
    s, r = admin.post("/api/settings/projects/lookup", {"code": CODE})
    assert s == 200 and r["count"] == 2 and r["applied"] == []
    s, r = admin.post("/api/settings/projects/lookup", {"code": CODE, "monitor": ["4101"]})
    assert s == 200 and r["applied"] == ["4101"]
    assert "4101" in [str(x) for x in db.get_setting("extra_projects", [])]
    node = next(p for p in nomenclature.projects() if p["externalId"] == "4101")
    assert node["code"] == CODE
    # the code stays editable afterwards
    s, r = admin.put("/api/nomenclature/projects/4101", {"code": "EDITED/CODE"})
    assert s == 200 and r["code"] == "EDITED/CODE"


def test_lookup_route_rejects_foreign_ids(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    s, _ = admin.post("/api/settings/projects/lookup", {"code": CODE, "monitor": ["9999"]})
    assert s == 400


def test_lookup_route_needs_settings_manage(supervisor, monkeypatch):
    FakeLookup(monkeypatch)
    s, _ = supervisor.post("/api/settings/projects/lookup", {"code": CODE})
    assert s == 403


# --------------------------------------------------------------------------- saving keys

@pytest.fixture()
def temp_env(monkeypatch, tmp_path):
    path = tmp_path / ".env"
    path.write_text("CAMVIEW_API_KEY=old\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", str(path))
    monkeypatch.setattr(os, "environ", os.environ.copy())   # config.apply() reads os.environ; keep it per-test
    yield path
    config.apply()


def test_setup_keeps_bearer_prefix(admin, temp_env):
    s, r = admin.post("/api/config/setup", {"apiKey": "bearer  tok-123", "projectApiKey": JWT})
    assert s == 200 and r["apiKeyKind"] == "bearer" and r["projectKeyConfigured"]
    text = temp_env.read_text(encoding="utf-8")
    assert "CAMVIEW_API_KEY=Bearer tok-123" in text
    assert f"CAMVIEW_PROJECT_API_KEY={JWT}" in text
    s, cfg = admin.get("/api/settings")
    assert cfg["connection"]["apiKeyKind"] == "bearer"
    assert "tok-123" not in str(cfg)                             # never sent back


def test_setup_blank_keeps_key_and_clear_removes_project_key(admin, temp_env):
    admin.post("/api/config/setup", {"apiKey": "raw-key", "projectApiKey": "Bearer p"})
    admin.post("/api/config/setup", {"apiKey": "", "projectApiKey": "", "projectApiKeyClear": True})
    text = temp_env.read_text(encoding="utf-8")
    assert "CAMVIEW_API_KEY=raw-key" in text
    assert "CAMVIEW_PROJECT_API_KEY=\n" in text


def test_setup_rejects_multiline_key(admin, temp_env):
    s, _ = admin.post("/api/config/setup", {"apiKey": "a\nCAMVIEW_MODE=demo"})
    assert s == 400


# --------------------------------------------------------------------------- no-login mode (CAMVIEW_NO_LOGIN=1)

def test_without_no_login_requests_need_sign_in(client):
    s, r = client.get("/api/auth/session")
    assert r["authenticated"] is False and r["noLogin"] is False
    s, _ = client.get("/api/settings")
    assert s == 401


def test_no_login_runs_as_super_admin(client, monkeypatch):
    monkeypatch.setattr(config, "NO_LOGIN", True)
    s, r = client.get("/api/auth/session")
    assert r["authenticated"] is True and r["noLogin"] is True
    assert r["user"]["roleName"] == "Super Admin"
    s, _ = client.get("/api/settings")
    assert s == 200


def test_no_login_creates_admin_when_none_exists(client, monkeypatch):
    monkeypatch.setattr(config, "NO_LOGIN", True)
    db.execute("DELETE FROM users WHERE role_id='super_admin'")
    s, r = client.get("/api/auth/session")
    assert r["authenticated"] is True and r["user"]["roleName"] == "Super Admin"


# --------------------------------------------------------------------------- project-code registry (code first)

def test_register_code_monitors_all_its_projects(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    s, r = admin.post("/api/project-codes", {"code": CODE})
    assert s == 200 and r["code"] == CODE
    assert sorted(p["externalId"] for p in r["projects"]) == ["4101", "4102"]
    extras = [str(x) for x in db.get_setting("extra_projects", [])]
    assert "4101" in extras and "4102" in extras
    assert all(p["code"] == CODE for p in nomenclature.projects() if p["externalId"] in ("4101", "4102"))
    s, lst = admin.get("/api/project-codes")
    assert s == 200 and [e["code"] for e in lst["items"]] == [CODE] and lst["canManage"]


def test_register_unknown_code_is_404(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    s, r = admin.post("/api/project-codes", {"code": "NOPE/1"})
    assert s == 404
    s, lst = admin.get("/api/project-codes")
    assert lst["items"] == []


def test_unregister_stops_monitoring(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    admin.post("/api/project-codes", {"code": CODE})
    s, r = admin.post("/api/project-codes/remove", {"code": CODE})
    assert s == 200 and r["stoppedMonitoring"] == ["4101", "4102"]
    extras = [str(x) for x in db.get_setting("extra_projects", [])]
    assert "4101" not in extras and "4102" not in extras


def test_refresh_picks_up_new_project_under_known_code(admin, monkeypatch):
    import project_codes
    fake = FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    admin.post("/api/project-codes", {"code": CODE})
    fake.projects.append({"projectId": 4103, "clientProjectId": CODE, "name": "New centre", "status": "ACTIVE"})
    monkeypatch.setattr(config, "MODE", "live")
    assert project_codes.refresh(force=True)
    assert project_codes.project_ids(CODE) == ["4101", "4102", "4103"]
    assert "4103" in [str(x) for x in db.get_setting("extra_projects", [])]


def test_register_needs_settings_manage(supervisor, monkeypatch):
    FakeLookup(monkeypatch)
    s, _ = supervisor.post("/api/project-codes", {"code": CODE})
    assert s == 403


def test_seed_from_config_keeps_manual_codes(monkeypatch):
    import project_codes
    monkeypatch.setattr(config, "PROJECT_CODES", {"2773": "MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL"})
    assert project_codes.seed_from_config() == 1
    assert project_codes.project_ids("MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL") == ["2773"]
    assert project_codes.seed_from_config() == 0                      # once only


def test_summary_filters_by_code(admin, monkeypatch):
    import project_codes
    s, all_ = admin.get("/api/queue/summary")
    pids = [p["externalId"] for p in all_["projects"]]
    assert len(pids) >= 2
    db.set_setting(project_codes.SETTING, {"ONE/CODE": {"projectIds": [pids[0]], "names": {}, "source": "configured",
                                                         "addedAt": "2026-01-01T00:00:00Z"}})
    s, one = admin.get("/api/queue/summary?code=ONE/CODE")
    assert s == 200 and [p["externalId"] for p in one["projects"]] == [pids[0]]
    s, _ = admin.get("/api/queue/summary?code=NOT/REGISTERED")
    assert s == 404


# --------------------------------------------------------------------------- clients are linked by project CODE

def _client_projects(cid):
    return sorted(r["project_id"] for r in db.rows("SELECT project_id FROM client_projects WHERE client_id=?", (cid,)))


def test_client_created_with_code_gets_its_projects(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    s, r = admin.post("/api/clients", {"name": "RailTel J&K", "projectCodes": [CODE]})
    assert s == 200
    assert _client_projects(r["id"]) == ["4101", "4102"]                 # code registered and resolved on the fly
    s, lst = admin.get("/api/clients")
    c = next(x for x in lst["items"] if x["id"] == r["id"])
    assert c["projectCodes"] == [CODE]
    assert [x["code"] for x in lst["codes"]] == [CODE]


def test_client_unlinking_code_removes_its_projects_only(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    s, r = admin.post("/api/clients", {"name": "C", "projectCodes": [CODE], "projects": ["999"]})
    assert _client_projects(r["id"]) == ["4101", "4102", "999"]
    s, out = admin.put(f"/api/clients/{r['id']}", {"projectCodes": [], "projects": ["999"]})
    assert s == 200 and out["projectCodes"] == []
    assert _client_projects(r["id"]) == ["999"]


def test_new_project_under_code_reaches_linked_client(admin, monkeypatch):
    import project_codes
    fake = FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    s, r = admin.post("/api/clients", {"name": "C", "projectCodes": [CODE]})
    fake.projects.append({"projectId": 4103, "clientProjectId": CODE, "name": "New centre", "status": "ACTIVE"})
    monkeypatch.setattr(config, "MODE", "live")
    project_codes.refresh(force=True)
    assert _client_projects(r["id"]) == ["4101", "4102", "4103"]


def test_client_with_unknown_code_is_rejected(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    s, _ = admin.post("/api/clients", {"name": "C", "projectCodes": ["DOES/NOT/EXIST"]})
    assert s == 404


def test_unknown_code_creates_no_client(admin, monkeypatch):
    FakeLookup(monkeypatch)
    monkeypatch.setattr(config, "API_KEY", "k")
    before = db.one("SELECT COUNT(*) AS n FROM clients")["n"]
    s, _ = admin.post("/api/clients", {"name": "Ghost", "projectCodes": ["DOES/NOT/EXIST"]})
    assert s == 404 and db.one("SELECT COUNT(*) AS n FROM clients")["n"] == before


# --------------------------------------------------------------------------- two doors: admin (no login) / client portal

class _Sock:
    def __init__(self, port):
        self.port = port

    def getsockname(self):
        return ("0.0.0.0", self.port)


def _door(app, port):
    c = app.test_client()
    c.environ_base["gunicorn.socket"] = _Sock(port)
    return c


@pytest.fixture()
def doors(monkeypatch):
    monkeypatch.setattr(config, "NO_LOGIN", True)
    monkeypatch.setattr(config, "NO_LOGIN_PORTS", {"5000"})
    monkeypatch.setattr(config, "CLIENT_ONLY_PORTS", {"5001"})


def test_admin_door_needs_no_login(app, doors):
    r = _door(app, 5000).get("/api/auth/session").get_json()
    assert r["authenticated"] and r["noLogin"] and r["user"]["roleName"] == "Super Admin"


def test_portal_door_asks_for_sign_in(app, doors):
    c = _door(app, 5001)
    r = c.get("/api/auth/session").get_json()
    assert r["authenticated"] is False and r["noLogin"] is False and r["clientPortal"] is True
    assert c.get("/api/settings").status_code == 401


def test_portal_door_accepts_client_accounts(app, doors):
    c = _door(app, 5001)
    r = c.post("/api/auth/login", json={"email": "client.admin@client-a.demo", "password": "demo"})
    assert r.status_code == 200 and r.get_json()["user"]["audience"] == "client"
    assert c.get("/api/client/overview").status_code == 200


def test_portal_door_refuses_internal_accounts(app, doors):
    c = _door(app, 5001)
    r = c.post("/api/auth/login", json={"email": "admin@demo.camview", "password": "demo"})
    assert r.status_code == 401                              # same answer as a wrong password
    assert c.get("/api/auth/session").get_json()["authenticated"] is False


def test_internal_session_is_ignored_on_portal_door(app, doors):
    """An internal user's session cookie carried to the portal door does not open it."""
    c = app.test_client()
    c.environ_base["gunicorn.socket"] = _Sock(7000)              # some other door with normal sign-in
    assert c.post("/api/auth/login", json={"email": "admin@demo.camview", "password": "demo"}).status_code == 200
    c.environ_base["gunicorn.socket"] = _Sock(5001)
    assert c.get("/api/auth/session").get_json()["authenticated"] is False
    assert c.get("/api/settings").status_code == 401


def test_unknown_door_never_gets_no_login(app, doors):
    """A request whose listener port cannot be read is never treated as the admin door."""
    r = app.test_client().get("/api/auth/session").get_json()
    assert r["authenticated"] is False
