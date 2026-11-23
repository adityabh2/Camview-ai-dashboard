"""
CAMVIEW COMMAND CENTER — backend
================================

Alarm Intelligence • Live Operations • Investigation • Collaboration

This Flask app is the ONLY component that holds the Camview listAlarms key.
The browser never sees it. On top of the read-only Camview feed it owns:

  * authentication, RBAC (roles, permissions, scopes) — enforced here
  * the nomenclature/context engine (master data)
  * operator validation, investigation workflow, assignments, notes
  * client visibility / sharing workflow and the client data firewall
  * explainable intelligent alerts, notifications, schedules, escalation
  * analytics, reports, audit trail (append-only)

Architecture:  RAW API -> normalize -> context -> workflow/visibility ->
               intelligence -> authorization/scope -> client firewall -> UI

Run:  pip install -r requirements.txt  &&  python app.py  ->  http://localhost:5000
      (demo mode by default; set the API key in Settings > Connection for live)
"""

import gzip
import logging
import os
from datetime import timedelta

from flask import Flask, g, jsonify, request, send_from_directory
from flask.sessions import SecureCookieSessionInterface
from werkzeug.exceptions import HTTPException

import obs

obs.install_logging(os.environ.get("LOG_LEVEL", "INFO"))

import bootstrap  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
from camview_client import ApiError  # noqa: E402

log = logging.getLogger("camview")


class SessionInterface(SecureCookieSessionInterface):
    """Session cookie: HttpOnly, SameSite=Lax, and Secure whenever the request itself arrived over HTTPS
    (directly, or through a proxy when CAMVIEW_TRUST_PROXY=1) — or always with CAMVIEW_SECURE_COOKIES=1.
    Plain-HTTP installs (a LAN, a test box) keep working; an HTTPS install never sends the cookie in clear."""

    def get_cookie_secure(self, app_):
        return bool(app_.config["SESSION_COOKIE_SECURE"]) or request.is_secure


app = Flask(__name__, static_folder=config.FRONTEND_DIR, static_url_path="")
app.session_interface = SessionInterface()
if config.TRUST_PROXY:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("CAMVIEW_SECURE_COOKIES", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    JSON_SORT_KEYS=False,
)

bootstrap.run()
if os.environ.get("CAMVIEW_TESTING") != "1":
    import datasource as _ds
    import jobs as _jobs
    _ds.start_warmer()                      # live mode only: background refresh of every project
    _jobs.start_worker()                    # durable jobs (client delivery): resumes unfinished work after a restart
    import auto_reports as _auto_reports
    _auto_reports.start()                   # daily and end-of-project reports shared with clients by themselves

from routes_admin import bp as admin_bp      # noqa: E402
from routes_client import bp as client_bp    # noqa: E402
from routes_common import bp as common_bp    # noqa: E402
from routes_ops import bp as ops_bp          # noqa: E402
from routes_queue import bp as queue_bp      # noqa: E402
from routes_sharing import bp as sharing_bp  # noqa: E402
from routes_incidents import bp as incidents_bp  # noqa: E402  (V2: incidents)
from routes_map import bp as map_bp          # noqa: E402  (V2: operations map)
from routes_ai import bp as ai_bp            # noqa: E402  (V2: AI assistant + smart search)
from routes_branding import bp as branding_bp  # noqa: E402  (organisation name + logo)
from routes_hms import bp as hms_bp          # noqa: E402  (HMS board: alerts per classification, live)

for blueprint in (common_bp, ops_bp, queue_bp, sharing_bp, admin_bp, client_bp, incidents_bp, map_bp, ai_bp, branding_bp, hms_bp):
    app.register_blueprint(blueprint)

CSP = ("default-src 'self'; "
       "script-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
       "font-src 'self' https://fonts.gstatic.com; "
       "img-src 'self' data: https:; media-src 'self' https:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


GZIP_MIN_BYTES = 1400          # smaller answers fit one packet anyway
GZIP_TYPES = ("application/json", "text/csv", "text/html", "text/css", "application/javascript", "text/javascript",
              "image/svg+xml")


@app.before_request
def correlation_id():
    g.request_id = obs.new_request_id(request.headers.get("X-Request-ID"))


@app.after_request
def security_headers(resp):
    rid = getattr(g, "request_id", None)
    if rid:
        resp.headers["X-Request-ID"] = rid
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return compress(resp)


def compress(resp):
    """gzip for JSON / static answers above GZIP_MIN_BYTES when the browser accepts it: a queue page or the
    dashboard summary shrinks 5–10×, which is what makes refreshes feel instant on a slow link."""
    if (resp.direct_passthrough or resp.is_streamed or resp.status_code < 200 or resp.status_code >= 300
            or "gzip" not in request.headers.get("Accept-Encoding", "").lower()
            or resp.headers.get("Content-Encoding")
            or not resp.mimetype or not resp.mimetype.startswith(GZIP_TYPES)):
        return resp
    data = resp.get_data()
    if len(data) < GZIP_MIN_BYTES:
        return resp
    resp.set_data(gzip.compress(data, compresslevel=5))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = ", ".join(v for v in (resp.headers.get("Vary"), "Accept-Encoding") if v)
    resp.headers["Content-Length"] = str(len(resp.get_data()))
    return resp


def _error(code, message, status, details=None):
    if status >= 500:
        log.warning("Error %s: %s (HTTP %s) on %s", code, message, status, request.path)
    body = {"error": code, "message": message, "requestId": getattr(g, "request_id", None)}
    if details:
        body["details"] = details
    return jsonify(body), status


@app.errorhandler(ApiError)
def handle_api_error(e):
    return _error(e.code, e.message, e.status, getattr(e, "details", None))


@app.errorhandler(db.DatabaseBusy)
def handle_db_busy(e):
    # nothing was committed: the operation can simply be repeated — never reported as a success
    obs.count("db.busy")
    return _error("database_busy", "The database is busy. Nothing was saved — please try again.", 503)


@app.route("/")
def index():
    return send_from_directory(config.FRONTEND_DIR, "index.html")


@app.route("/healthz")
def liveness():
    """Liveness: the process answers. No authentication, no details."""
    return jsonify({"status": "ok"})


@app.route("/readyz")
def readiness():
    """Readiness: may this instance receive traffic? The database answers and accepts writes, the durable job
    worker runs (outside tests), and the session secret is stable. Names what failed — never a secret or a path."""
    import jobs
    checks = {}
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1 FROM ops_review LIMIT 1").fetchall()
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _ready (x INTEGER)")
        checks["database"] = "ok"
    except Exception as ex:
        checks["database"] = "locked" if isinstance(ex, db.DatabaseBusy) else "error"
    checks["jobWorker"] = "ok" if jobs.worker_alive() or os.environ.get("CAMVIEW_TESTING") == "1" else "stopped"
    checks["sessionSecret"] = "ok" if config.SECRET_SOURCE in ("env", "file", "env-file") \
        or (config.MODE == "demo" and config.SECRET_KEY) else "unstable"
    # live mode cannot do its core work without a Camview key and URL
    checks["configuration"] = "ok" if config.MODE == "demo" or (config.API_KEY and config.API_URL) else "incomplete"
    import alert_store
    checks["alertStore"] = "ok" if alert_store.HEALTH["ok"] else "failing"      # alerts received but not persisted
    checks["integrity"] = "ok" if db.INTEGRITY.get("ok") is not False else "failed"
    ok = all(v == "ok" for v in checks.values())
    return jsonify({"status": "ready" if ok else "not_ready", "mode": config.MODE, "checks": checks}), 200 if ok else 503


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return _error("not_found", "No such endpoint.", 404)
    return send_from_directory(config.FRONTEND_DIR, "index.html")


@app.errorhandler(Exception)
def unhandled(e):
    if isinstance(e, HTTPException):
        return _error(e.name.lower().replace(" ", "_"), e.description, e.code)
    log.exception("Unhandled error on %s", request.path)
    return _error("internal_error", "Unexpected server error.", 500)   # never a stack trace to the client


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    log.info("CAMVIEW Command Center on http://localhost:%s  (mode=%s, api_configured=%s)",
             port, config.MODE, bool(config.API_KEY))
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=debug, threaded=True)
