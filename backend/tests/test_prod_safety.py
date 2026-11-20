"""
Q. production safety · R. observability

* liveness / readiness endpoints; no secrets in any answer
* stable production session secret; HttpOnly / SameSite / Secure cookies
* credentials never logged; correlation ids on every answer and every important operation
"""

import logging
import os

import config
import db
import obs
from fakecamview import FakeCamview


def test_liveness_and_readiness(client):
    r = client.c.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}
    r = client.c.get("/readyz")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "ready"
    assert set(body["checks"]) == {"database", "jobWorker", "sessionSecret", "configuration", "alertStore", "integrity"}
    text = r.get_data(as_text=True)
    assert config.SECRET_KEY not in text and db.DB_PATH not in text


def test_readiness_reports_a_broken_database(client, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", os.path.join(os.path.dirname(db.DB_PATH), "missing-dir", "x", "nope.db"))
    r = client.c.get("/readyz")
    assert r.status_code == 503 and r.get_json()["checks"]["database"] == "error"


def test_every_answer_carries_a_request_id(client, supervisor):
    r = supervisor.c.get("/api/queue/summary")
    assert len(r.headers["X-Request-ID"]) >= 8
    r = supervisor.c.get("/api/queue/summary", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"
    r = supervisor.c.get("/api/queue/summary", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert r.headers["X-Request-ID"] != "<script>alert(1)</script>" and "<" not in r.headers["X-Request-ID"]
    r = client.c.get("/api/queue/nope")
    assert r.get_json()["requestId"] == r.headers["X-Request-ID"]


def test_alert_operations_are_traceable(supervisor, caplog):
    s, q = supervisor.get("/api/queue?projectId=7&status=pending&size=1")
    a = q["items"][0]
    with caplog.at_level(logging.INFO, logger="camview.ops"):
        r = supervisor.c.post(f"/api/queue/{a['alarmId']}/decide", json={"result": "invalid", "projectId": 7},
                              headers={"X-Request-ID": "trace-decide-1"})
    assert r.status_code == 200
    line = next(m for m in caplog.messages if "op=alert.decide" in m)
    for part in ("outcome=ok", "requestId=trace-decide-1", f"alarmId={a['alarmId']}", "projectId=7", "userId="):
        assert part in line, (part, line)


def test_metrics_count_every_tracked_failure_kind(admin):
    s, m = admin.get("/api/admin/metrics")
    assert s == 200
    for k in obs.TRACKED:
        assert k in m["counters"]
    assert set(m["jobs"]["counts"]) == {"PENDING", "RUNNING", "DONE", "RETRY", "FAILED"}
    assert "state" in m["circuit"] and "alerts" in m["alertStore"]


def test_session_cookie_flags(app):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": "operator@demo.camview", "password": "demo"})
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" not in cookie   # plain HTTP: still works
    c2 = app.test_client()
    r = c2.post("/api/auth/login", json={"email": "operator@demo.camview", "password": "demo"},
                base_url="https://camview.example")
    assert "Secure" in r.headers["Set-Cookie"], "over HTTPS the session cookie must be Secure"


def test_live_session_secret_is_stable_across_restarts(tmp_path):
    k1 = config._persisted_secret(str(tmp_path))
    k2 = config._persisted_secret(str(tmp_path))
    assert k1 == k2 and len(k1) >= 32
    assert os.path.exists(tmp_path / config.SECRET_FILE_NAME)


def test_live_mode_refuses_an_unstorable_secret(tmp_path, monkeypatch):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    try:
        config._persisted_secret(str(blocker / "sub"))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "CAMVIEW_SECRET_KEY" in str(e)


def test_generated_admin_password_is_never_logged(tmp_path, monkeypatch, caplog):
    import bootstrap
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "live.db"))
    db.init_db()
    import rbac
    rbac.seed_roles()
    monkeypatch.setenv("CAMVIEW_ADMIN_EMAIL", "root@example.test")
    monkeypatch.delenv("CAMVIEW_ADMIN_PASSWORD", raising=False)
    with caplog.at_level(logging.DEBUG):
        bootstrap.ensure_admin(force=True)
    pw_file = tmp_path / "initial-admin-password.txt"
    password = pw_file.read_text().splitlines()[1]
    assert password and password not in caplog.text
    assert "initial-admin-password.txt" in caplog.text


def test_signed_links_and_keys_are_masked_in_operation_logs():
    s = obs._clean("https://x/a.jpg?X-Amz-Signature=deadbeef&token=abc&password=p&key=k")
    assert "deadbeef" not in s and "abc" not in s and "=p" not in s and "key=k" not in s


def test_camview_key_is_never_in_errors_or_logs(supervisor, monkeypatch, caplog):
    cv = FakeCamview(monkeypatch)
    cv.mode = 500
    with caplog.at_level(logging.DEBUG):
        s, q = supervisor.get("/api/queue?projectId=77&status=all")
        s2, st = supervisor.get("/api/status?projectId=77")
    assert "test-key" not in caplog.text and "test-key" not in str(q) and "test-key" not in str(st)
