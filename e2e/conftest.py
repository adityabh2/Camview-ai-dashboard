"""
Browser end-to-end harness (Playwright).

Runs the REAL server (backend/app.py, live mode, its own temporary databases) against a local fake Camview:

  fake Camview  POST /alarms/listAlarms   alarms of projects 4100 and 4200 with signed media links (re-signed on
                                          every read); per alert: video / image present, missing, or broken (404)
                GET  /media/<file>        a real H.264 clip and JPEG frame (made with ffmpeg), Range supported
  the app       CAMVIEW_MEDIA_MODE=proxy  evidence streamed through the app (the fake store is plain HTTP on another
                                          port, which the page's CSP would block as a direct link)

Seeded through the public API only:
  project 4100 → exam "SSC CGL 2026"        → client "Staff Selection Commission"
  project 4200 → exam "OB Recruitment 2026" → client "Other Board"
  a supervisor, a second operator, client logins, and camera / recording health through the health ingest endpoint.

    python -m pytest e2e -q                  (Edge via Playwright's msedge channel; E2E_BROWSER=chromium to change)
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
PROJECT, PROJECT_B = "4100", "4200"
ADMIN = ("admin@e2e.test", "e2e-admin-pass")
SUPERVISOR = ("supervisor@e2e.test", "e2e-super-pass")
OPERATOR2 = ("operator2@e2e.test", "e2e-oper-pass")
CLIENT = ("client@ssc.test", "e2e-client-pass")
OTHER_CLIENT = ("client@other.test", "e2e-other-pass")
HEALTH_TOKEN = "e2e-health-token"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def now_iso(offset_s=0):
    return datetime.fromtimestamp(time.time() - offset_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeCamview:
    """State shared with the tests: alarms can be added while the app runs (new arrivals)."""

    def __init__(self, media_dir):
        self.media_dir = media_dir
        self.alarms = []
        self.sig = 0
        self.port = free_port()
        self.lock = threading.Lock()

    def add(self, aid, cam, typ=14, minutes_ago=5, action=0, video="ok", image="ok", project=PROJECT):
        """video / image: ok | none (not attached) | broken (attached, but the file is gone: 404)"""
        with self.lock:
            self.alarms.insert(0, {"aid": aid, "cam": cam, "typ": typ, "at": now_iso(minutes_ago * 60), "action": action,
                                   "video": video, "image": image, "project": str(project)})

    def url(self, name):
        return (f"http://127.0.0.1:{self.port}/media/{name}?X-Amz-Date={datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
                f"&X-Amz-Expires=604800&X-Amz-Signature=s{self.sig}")

    def page(self, body):
        pid = str(body.get("projectId"))
        with self.lock:
            self.sig += 1
            rows = [r for r in self.alarms if r["project"] == pid]
        size, page = int(body.get("size", 100)), int(body.get("page", 0))
        chunk = rows[page * size:(page + 1) * size]
        out = []
        for r in chunk:
            alarm = {"alarmId": r["aid"], "projectId": int(pid), "cameraId": r["cam"], "alarmType": r["typ"],
                     "priority": 1, "lastActionType": r["action"], "firstInstance": r["at"], "lastInstance": r["at"],
                     "totalTimesReported": 1}
            if r["video"] != "none":
                alarm["videoUrl"] = self.url("clip.mp4" if r["video"] == "ok" else f"gone-{r['aid']}.mp4")
            item = {"camera": {"cameraId": r["cam"], "centerCode": "C018" if pid == PROJECT else "C042",
                               "center": "GOVT SCHOOL_Kanpur_Uttar Pradesh", "tcCode": "TC-18" if pid == PROJECT else "TC-42",
                               "subLocation": "Room204", "cameraNumber": f"{r['cam']}_1", "frameSyncStatus": "SYNCED",
                               "lastFrameSync": now_iso()},
                    "alarm": alarm}
            if r["image"] != "none":
                item["imageUrls"] = [self.url("frame.jpg" if r["image"] == "ok" else f"gone-{r['aid']}.jpg")]
            out.append(item)
        pages = max(1, -(-len(rows) // size)) if rows else 0
        return {"cameraAlarmsDetails": out, "totalElements": len(rows), "totalPages": pages, "page": page, "size": size,
                "hasNext": page + 1 < pages}

    def serve(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path.startswith("/projects/getProject"):
                    return self._send(403, b'{"message":"forbidden"}', "application/json")
                if self.headers.get("Authorization") != "e2e-key":
                    return self._send(403, b'{"message":"Unable to authenticate"}', "application/json")
                self._send(200, json.dumps(fake.page(body)).encode(), "application/json")

            def do_GET(self):
                name = self.path.split("?")[0].rsplit("/", 1)[-1]
                path = os.path.join(fake.media_dir, name)
                if not os.path.exists(path):
                    return self._send(404, b"not found", "text/plain")
                data = open(path, "rb").read()
                ctype = "video/mp4" if name.endswith(".mp4") else "image/jpeg"
                rng = self.headers.get("Range")
                if rng and rng.startswith("bytes="):
                    start, _, end = rng[6:].partition("-")
                    start = int(start or 0)
                    end = min(int(end) if end else len(data) - 1, len(data) - 1)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    part = data[start:end + 1]
                else:
                    self.send_response(200)
                    part = data
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(part)))
                self.end_headers()
                try:
                    self.wfile.write(part)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def _send(self, status, body, ctype):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


@pytest.fixture(scope="session")
def workdir():
    d = tempfile.mkdtemp(prefix="camview-e2e-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session")
def media_dir(workdir):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is needed to make the test clip (E2E video checks cannot run without it)")
    d = os.path.join(workdir, "media")
    os.makedirs(d)
    subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=30:size=320x240:rate=15",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", os.path.join(d, "clip.mp4")],
                   check=True)
    subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", "1",
                    os.path.join(d, "frame.jpg")], check=True)
    return d


@pytest.fixture(scope="session")
def camview(media_dir):
    cv = FakeCamview(media_dir)
    for i, cam in enumerate((1001, 1002, 1001, 1003, 1002, 1001), start=1):
        cv.add(f"E2E-{i}", cam, typ=14 if i % 2 else 11, minutes_ago=30 - i)
    cv.add("E2E-CVVALID", 1001, action=1, minutes_ago=20)                 # VALID in Camview only (test D)
    cv.add("E2E-NOVIDEO", 1004, video="broken", minutes_ago=19)           # J: video broken, image fine
    cv.add("E2E-NOIMAGE", 1004, image="broken", minutes_ago=18)           # K: image broken, video fine
    cv.add("E2E-NOMEDIA", 1005, video="broken", image="broken", minutes_ago=17)   # L: both broken
    cv.add("E2E-B1", 2001, project=PROJECT_B, minutes_ago=16)             # E / F: another exam and client
    cv.add("E2E-DBL", 1001, minutes_ago=15)                               # O: double VALID
    cv.add("E2E-TWO", 1002, minutes_ago=14)                               # P: two operators at once
    cv.add("E2E-RESTART", 1001, minutes_ago=13)                           # N: restart during delivery
    srv = cv.serve()
    yield cv
    srv.shutdown()


class Server:
    """One app process. restart() keeps the databases (a process restart, not a new installation)."""

    def __init__(self, workdir, camview, name, extra_env=None):
        self.dir = os.path.join(workdir, name)
        os.makedirs(self.dir, exist_ok=True)
        self.camview, self.port, self.proc = camview, free_port(), None
        self.base = f"http://127.0.0.1:{self.port}"
        self.extra_env = dict(extra_env or {})

    def env(self):
        return {**os.environ, "CAMVIEW_MODE": "live", "CAMVIEW_API_KEY": "e2e-key",
                "CAMVIEW_API_URL": f"http://127.0.0.1:{self.camview.port}/alarms/listAlarms", "CAMVIEW_PROJECT_ID": PROJECT,
                "CAMVIEW_PROJECT_CODES": f"{PROJECT}:SSC-2026-034,{PROJECT_B}:OBR-2026-042",
                "CAMVIEW_ADMIN_EMAIL": ADMIN[0], "CAMVIEW_ADMIN_PASSWORD": ADMIN[1],
                "CAMVIEW_SECRET_KEY": "e2e-secret-" + "x" * 40,
                "CAMVIEW_DB_PATH": os.path.join(self.dir, "live.db"), "CAMVIEW_DEMO_DB_PATH": os.path.join(self.dir, "demo.db"),
                "PORT": str(self.port), "CAMVIEW_CACHE_SECONDS": "3", "CAMVIEW_MEDIA_MODE": "proxy",
                "CAMVIEW_AUTO_DISCOVER": "0", "CAMVIEW_GEOCODE": "0", "CAMVIEW_DEMO_USERS": "0",
                "CAMVIEW_HEALTH_INGEST_TOKEN": HEALTH_TOKEN, "CAMVIEW_TESTING": "", "CAMVIEW_BACKUP": "0",
                "LOG_LEVEL": "WARNING", "PYTHONUNBUFFERED": "1", "CAMVIEW_FAULTS": "", **self.extra_env}

    def start(self):
        log = open(os.path.join(self.dir, "server.log"), "a", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "app.py"], cwd=BACKEND, env=self.env(), stdout=log,
                                     stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                if requests.get(f"{self.base}/readyz", timeout=1).status_code == 200:
                    return self
            except requests.RequestException:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        self.stop()
        pytest.fail("server did not become ready:\n" + open(os.path.join(self.dir, "server.log"), encoding="utf-8").read()[-3000:])

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def restart(self, **env):
        self.stop()
        self.extra_env.update(env)
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        return self.start()


def seed(base):
    s = api_session(base, ADMIN)

    def ok(r):
        assert r.status_code == 200, r.text
        return r.json()
    ok(s.put(f"{base}/api/settings/policy", json={"deliveryMode": "automatic"}))
    ok(s.put(f"{base}/api/settings/projects", json={"projects": [PROJECT_B]}))
    ssc = ok(s.post(f"{base}/api/clients", json={"name": "Staff Selection Commission", "projects": [PROJECT]}))["id"]
    other = ok(s.post(f"{base}/api/clients", json={"name": "Other Board", "projects": [PROJECT_B]}))["id"]
    ok(s.post(f"{base}/api/exams", json={"name": "SSC CGL 2026", "code": "SSC-CGL-2026", "clientId": ssc,
                                        "projectIds": [PROJECT]}))
    ok(s.post(f"{base}/api/exams", json={"name": "OB Recruitment 2026", "code": "OBR-2026", "clientId": other,
                                        "projectIds": [PROJECT_B]}))
    for who, role, extra in ((SUPERVISOR, "supervisor", {}), (OPERATOR2, "operator", {})):
        ok(s.post(f"{base}/api/users", json={"name": f"E2E {role.title()}", "email": who[0], "password": who[1],
                                            "roleId": role, "scopes": [{"type": "global", "value": "*"}], **extra}))
    ok(s.post(f"{base}/api/users", json={"name": "SSC Client", "email": CLIENT[0], "password": CLIENT[1],
                                        "roleId": "client_admin", "clientId": ssc}))
    ok(s.post(f"{base}/api/users", json={"name": "Other Client", "email": OTHER_CLIENT[0], "password": OTHER_CLIENT[1],
                                        "roleId": "client_admin", "clientId": other}))
    push_health(base)


def push_health(base):
    """camera + recording health from a monitoring system — separate facts per camera:
    1001 online+recording · 1002 online+NOT recording · 1004 online+recording UNKNOWN · 1005 OFFLINE"""
    r = requests.post(f"{base}/api/camera-health/ingest", headers={"Authorization": HEALTH_TOKEN}, json={"cameras": [
        {"projectId": int(PROJECT), "cameraId": 1001, "cameraState": "online", "recordingState": "recording",
         "streamState": "available", "lastHeartbeatAt": now_iso(), "lastRecordingAt": now_iso(30)},
        {"projectId": int(PROJECT), "cameraId": 1002, "cameraState": "online", "recordingState": "not_recording",
         "streamState": "available", "lastHeartbeatAt": now_iso()},
        {"projectId": int(PROJECT), "cameraId": 1004, "cameraState": "online", "recordingState": "unknown",
         "streamState": "available", "lastHeartbeatAt": now_iso()},
        {"projectId": int(PROJECT), "cameraId": 1005, "cameraState": "offline", "recordingState": "unknown",
         "streamState": "unavailable", "lastHeartbeatAt": now_iso()}]})
    assert r.status_code == 200, r.text


@pytest.fixture(scope="session")
def server(workdir, camview):
    srv = Server(workdir, camview, "main").start()
    seed(srv.base)
    yield srv.base
    srv.stop()


@pytest.fixture()
def fresh_server(workdir, camview, request):
    """A separate installation (own databases) for tests that restart the process."""
    srv = Server(workdir, camview, f"iso-{request.node.name[:40]}")
    yield srv
    srv.stop()


def api_session(base, who):
    s = requests.Session()
    r = s.post(f"{base}/api/auth/login", json={"email": who[0], "password": who[1]})
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    which = os.environ.get("E2E_BROWSER", "msedge")
    try:
        b = p.chromium.launch(channel=which, headless=os.environ.get("E2E_HEADED") != "1") if which != "chromium" \
            else p.chromium.launch(headless=os.environ.get("E2E_HEADED") != "1")
    except Exception as e:
        p.stop()
        pytest.skip(f"no browser for Playwright ({which}): {e}")
    yield b
    b.close()
    p.stop()


@pytest.fixture()
def login(browser, server):
    contexts = []

    def _login(who, base=None):
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto((base or server) + "/")
        page.fill("#email", who[0])
        page.fill("#password", who[1])
        page.click("button[type=submit]")
        page.wait_for_function("() => !document.querySelector('#email')")
        return page
    yield _login
    for c in contexts:
        c.close()
