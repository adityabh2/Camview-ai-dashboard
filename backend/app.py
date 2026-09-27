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

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import bootstrap
import config
from camview_client import ApiError

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("camview")

app = Flask(__name__, static_folder=config.FRONTEND_DIR, static_url_path="")
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
    _ds.start_warmer()                      # live mode only: background refresh of every project

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

for blueprint in (common_bp, ops_bp, queue_bp, sharing_bp, admin_bp, client_bp, incidents_bp, map_bp, ai_bp, branding_bp):
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


@app.after_request
def security_headers(resp):
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


def _error(code, message, status):
    if status >= 500:
        log.warning("Error %s: %s (HTTP %s) on %s", code, message, status, request.path)
    return jsonify({"error": code, "message": message}), status


@app.errorhandler(ApiError)
def handle_api_error(e):
    return _error(e.code, e.message, e.status)


@app.route("/")
def index():
    return send_from_directory(config.FRONTEND_DIR, "index.html")


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
