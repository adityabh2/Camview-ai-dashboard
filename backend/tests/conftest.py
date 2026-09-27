"""
Test fixtures.

The demo database is seeded ONCE into a template file; every test gets its
own copy, so tests are isolated and fast. Tests never touch backend/.env,
the real databases, or the real Camview API.
"""

import os
import shutil
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="camview-tests-")
os.environ.update(
    CAMVIEW_TESTING="1", CAMVIEW_SHARE_INLINE="1", CAMVIEW_MODE="demo", CAMVIEW_DEMO_USERS="1", CAMVIEW_ADMIN_EMAIL="", CAMVIEW_API_KEY=" ", CAMVIEW_SECRET_KEY="test-secret",
    CAMVIEW_DB_PATH=os.path.join(_TMP, "live-template.db"),
    CAMVIEW_DEMO_DB_PATH=os.path.join(_TMP, "demo-template.db"),
    CAMVIEW_ADMIN_PASSWORD="live-admin-pass-123",
    CAMVIEW_DELIVERY_TRIGGER="valid",           # tests describe the VALID flow; arrival has its own test
    CAMVIEW_PROJECT_CODES="",
)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workflow  # noqa: E402
# Most tests describe the manual / controlled flow; auto-share has its own tests (test_autoshare.py).
workflow.POLICY_DEFAULTS.update(deliveryMode="controlled", autoShareValid=False, manualReview=True, autoShareHours=24,
                                # the older suites describe every delivery mode; test_client_visibility.py covers
                                # the production default (clients see only operator-VALID alerts)
                                clientsSeeOperatorValidOnly=False)

import app as app_module  # noqa: E402  (seeds the demo template on import)
import config  # noqa: E402
import datasource  # noqa: E402
import db  # noqa: E402
import nomenclature  # noqa: E402

TEMPLATE = os.environ["CAMVIEW_DEMO_DB_PATH"]


def _fresh_copy(src):
    path = os.path.join(_TMP, f"t-{os.urandom(6).hex()}.db")
    shutil.copy(src, path)
    return path


@pytest.fixture(autouse=True)
def isolated_db():
    """Each test runs against its own copy of the seeded demo database."""
    config.MODE = "demo"
    config.API_KEY = ""
    db.DB_PATH = _fresh_copy(TEMPLATE)
    nomenclature._cache.update(nodes=None, checked=0, db=None)
    datasource.reset()
    import routes_common
    import routes_ops
    routes_common._failures.clear()          # sign-in rate limiter is per-process state
    routes_ops._tick_state["at"] = 0.0       # schedules / escalations run on the first /api/status of every test
    yield
    datasource.reset()


@pytest.fixture()
def app():
    app_module.app.config["TESTING"] = True
    return app_module.app


class Client:
    """Thin wrapper: JSON in/out and helpers for signing in."""

    def __init__(self, app):
        self.c = app.test_client()

    def login(self, email, password="demo"):
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.get_json()
        return self

    def get(self, path, **kw):
        r = self.c.get(path, **kw)
        return r.status_code, r.get_json(silent=True)

    def post(self, path, body=None, **kw):
        r = self.c.post(path, json=body if body is not None else {}, **kw)
        return r.status_code, r.get_json(silent=True)

    def put(self, path, body=None):
        r = self.c.put(path, json=body or {})
        return r.status_code, r.get_json(silent=True)

    def delete(self, path):
        r = self.c.delete(path, json={})
        return r.status_code, r.get_json(silent=True)


@pytest.fixture()
def client(app):
    return Client(app)


def as_user(app, email):
    return Client(app).login(email)


@pytest.fixture()
def supervisor(app):
    return as_user(app, "supervisor@demo.camview")


@pytest.fixture()
def operator(app):
    return as_user(app, "operator@demo.camview")


@pytest.fixture()
def admin(app):
    return as_user(app, "admin@demo.camview")


@pytest.fixture()
def manager(app):
    return as_user(app, "manager@demo.camview")


@pytest.fixture()
def client_a(app):
    return as_user(app, "client.admin@client-a.demo")


@pytest.fixture()
def viewer_a(app):
    return as_user(app, "viewer@client-a.demo")


@pytest.fixture()
def client_b(app):
    return as_user(app, "user@client-b.demo")
