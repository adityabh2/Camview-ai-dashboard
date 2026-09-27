"""
routes_map.py — Operations map (V2).

  GET /api/map?range=today|24h|window   per-centre points + per-city aggregates (scope-filtered)
  GET /api/map/places                   stored positions (administrators)
  PUT /api/map/places                   set / clear an administrator's position of a centre or city

Positions are never invented: see geo.py.
"""

from flask import Blueprint, jsonify, request

import datasource
import geo
import rbac
from camview_client import ApiError
from routes_common import body, project_label

bp = Blueprint("map", __name__)

RANGES = ("today", "24h", "window")


def _user():
    return rbac.current_user()


@bp.route("/api/map")
@rbac.internal("alarm.view", "camera.view", any_of=True)
def map_points():
    from routes_queue import _all_alarms
    user = _user()
    rng = request.args.get("range") or "window"
    if rng not in RANGES:
        raise ApiError("bad_request", "range must be today, 24h or window.", 400)
    items, freshness = _all_alarms(user)
    known = geo.places()
    labels = {}

    def label_for(pid):
        if pid not in labels:
            p = project_label(pid)
            labels[pid] = {"code": p.get("code"), "name": p.get("name")}
        return labels[pid]

    out = geo.build(items, rng, known=known, label_for=label_for)
    # cities that were never looked up are queued for the background geocoder (never waited for)
    geo.request({(p["city"], p["state"]) for p in out["points"] if p["city"]}, known)
    out["geocoding"] = geo.progress([c["key"] for c in out["cities"]], known)
    out["freshness"] = freshness
    out["canEdit"] = rbac.has(user, "settings.manage")
    out["projects"] = [{"projectId": pid, **label_for(pid)} for pid in datasource.allowed_projects(user)
                       if rbac.project_allowed(user, pid)]
    return jsonify(out)


@bp.route("/api/map/places")
@rbac.internal("settings.manage")
def map_places():
    return jsonify({"items": sorted(geo.places().values(), key=lambda r: r["key"]), "geocodingEnabled": geo.enabled()})


@bp.route("/api/map/places", methods=["PUT"])
@rbac.internal("settings.manage")
def map_places_put():
    b = body()
    key = b.get("key")
    if not geo.valid_key(key):
        raise ApiError("bad_request", "key must be 'centre|<projectId>|<centreCode>' or 'city|<city>|<state>'.", 400)
    if key.startswith("city|"):
        key = geo.city_key(key.split("|")[1], key.split("|")[2])
    user = _user()
    if b.get("clear"):
        if not geo.clear_admin(key, user):
            raise ApiError("not_found", "No administrator position is set for this place.", 404)
        return jsonify({"ok": True, "key": key, "cleared": True})
    try:
        lat, lng = float(b.get("lat")), float(b.get("lng"))
    except (TypeError, ValueError):
        raise ApiError("bad_request", "lat and lng must be numbers.", 400)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or lat != lat or lng != lng:
        raise ApiError("bad_request", "lat must be between -90 and 90, lng between -180 and 180.", 400)
    label = str(b.get("label") or "").strip()[:200] or None
    row = geo.set_admin(key, round(lat, 6), round(lng, 6), label, user)
    return jsonify({"ok": True, "place": row})
