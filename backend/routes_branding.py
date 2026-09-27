"""routes_branding.py — organisation name and logo (Settings › Branding).

GET  /api/branding        public  {name, subtitle, hasLogo, logoVersion} (the sign-in page needs it)
GET  /api/branding/logo   public  the logo bytes (SVG served inside a script-less sandbox CSP)
PUT  /api/branding        internal, settings.manage, JSON {name?, subtitle?, logo? (data URL), removeLogo?}
"""

from flask import Blueprint, Response, jsonify, request

import branding
import rbac
from camview_client import ApiError

bp = Blueprint("branding", __name__)


@bp.route("/api/branding")
def branding_get():
    return jsonify(branding.public())


@bp.route("/api/branding/logo")
def branding_logo():
    found = branding.logo_bytes()
    if not found:
        raise ApiError("not_found", "No logo has been set.", 404)
    data, mime = found
    resp = Response(data, mimetype=mime)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = "inline"
    if mime == "image/svg+xml":
        # opened directly, an SVG is a document: never let it run script or load anything
        resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return resp


@bp.route("/api/branding", methods=["PUT"])
@rbac.internal("settings.manage")
def branding_put():
    return jsonify(branding.update(request.get_json(silent=True), rbac.current_user()))
