"""Operations map (V2): aggregation, scope, administrator positions, no network in tests."""

import pytest

import geo
from conftest import as_user


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    calls = []

    def boom(city, state):
        calls.append((city, state))
        raise AssertionError("the geocoder must never be called in tests")

    monkeypatch.setattr(geo, "_nominatim", boom)
    geo._pending.clear()
    geo._failed.clear()
    yield calls
    assert calls == []


def _point(body, key):
    return next(p for p in body["points"] if p["key"] == key)


def test_geocoding_is_disabled_in_tests():
    assert geo.enabled() is False
    assert geo.request([("INDORE", "Madhya Pradesh")]) == 0
    assert not geo._pending


def test_points_aggregate_demo_data(admin):
    s, body = admin.get("/api/map")
    assert s == 200 and body["range"] == "window"
    pts = [p for p in body["points"] if not p["noCentre"]]
    assert pts and {p["projectId"] for p in pts} >= {"7", "12"}
    # demo cameras carry no city / state: nothing is placed, nothing is invented
    assert body["located"] == 0 and body["bounds"] is None
    assert all(p["lat"] is None and p["reason"] in ("no_city", "no_centre") for p in body["points"])
    # counts match the alert queue for one centre
    p = _point(body, "centre|7|CTR-0711")
    assert p["projectCode"] == "PROJECT-07"
    s, q = admin.get("/api/queue?status=pending&centre=CTR-0711&projectId=7&size=1")
    assert p["counts"]["pending"] == q["totalElements"]
    s, q = admin.get("/api/queue?status=valid&centre=CTR-0711&projectId=7&size=1")
    assert p["counts"]["valid"] == q["totalElements"]
    assert p["status"] == ("alarm" if p["counts"]["urgent"] else "warning" if p["counts"]["pending"] else "ok")
    assert sum(body["counts"].values()) == len(body["points"])
    s, q = admin.get("/api/queue?status=pending&size=1")
    assert body["totals"]["pending"] == q["totalElements"]
    assert body["canEdit"] is True


def test_range_filter(admin):
    _, win = admin.get("/api/map?range=window")
    _, today = admin.get("/api/map?range=today")
    assert today["totals"]["alerts"] <= win["totals"]["alerts"]
    assert today["totals"]["alerts"] == today["totals"]["today"]
    assert admin.get("/api/map?range=year")[0] == 400


def test_scoped_user_sees_only_their_centres(app):
    sana = as_user(app, "operator.tec04@demo.camview")           # scoped to TC-0711
    s, body = sana.get("/api/map")
    assert s == 200
    assert {p["code"] for p in body["points"] if not p["noCentre"]} == {"CTR-0711"}
    op = as_user(app, "operator@demo.camview")                    # scoped to project 7
    _, body = op.get("/api/map")
    assert {p["projectId"] for p in body["points"]} == {"7"}
    assert body["canEdit"] is False


def test_admin_position_wins_and_requires_settings_manage(admin, supervisor):
    put = {"key": "centre|7|CTR-0711", "lat": 22.7196, "lng": 75.8577, "label": "Test"}
    assert supervisor.put("/api/map/places", put)[0] == 403
    assert supervisor.get("/api/map/places")[0] == 403
    s, r = admin.put("/api/map/places", put)
    assert s == 200 and r["place"]["source"] == "admin"
    _, body = admin.get("/api/map")
    p = _point(body, "centre|7|CTR-0711")
    assert (p["lat"], p["lng"], p["precision"]) == (22.7196, 75.8577, "admin")
    assert body["located"] == 1 and body["bounds"] == [[22.7196, 75.8577], [22.7196, 75.8577]]
    s, lst = admin.get("/api/map/places")
    assert s == 200 and [x["key"] for x in lst["items"]] == ["centre|7|CTR-0711"]
    # invalid input
    assert admin.put("/api/map/places", {"key": "centre|7|CTR-0711", "lat": 200, "lng": 1})[0] == 400
    assert admin.put("/api/map/places", {"key": "nope", "lat": 1, "lng": 1})[0] == 400
    # clear
    assert admin.put("/api/map/places", {"key": "centre|7|CTR-0711", "clear": True})[0] == 200
    _, body = admin.get("/api/map")
    assert _point(body, "centre|7|CTR-0711")["lat"] is None


def _alarm(pid, code, city, state, **kw):
    a = {"projectId": pid, "centreCode": code, "centreName": code, "cameraCity": city, "cameraState": state,
         "cameraId": kw.pop("cam", "1"), "eventKind": "alert", "decision": "pending", "priority": "medium",
         "lastInstance": "2026-09-27T10:00:00Z", "alarmTypeName": "Person Movement", "health": {}}
    a.update(kw)
    return a


def test_city_positions_are_approximate_and_admin_wins():
    items = [_alarm(34, "A1", "INDORE", "Madhya Pradesh", priority="high"),
             _alarm(34, "A2", "Indore", "Madhya Pradesh", decision="valid", cam="2",
                    health={"available": True, "camera": {"state": "offline"}, "conditions": ["CAMERA_OFFLINE"]}),
             _alarm(34, "A3", "INDORE", "Madhya Pradesh", decision="invalid", cam="3",
                    health={"available": True, "camera": {"state": "unknown"}, "conditions": ["FRAME_SYNC_FAILED"]}),
             _alarm(34, "B1", "BHOPAL", "Madhya Pradesh"),
             _alarm(34, None, None, None, cam="9")]
    known = {"city|indore|madhya pradesh": {"key": "city|indore|madhya pradesh", "lat": 22.72, "lng": 75.86,
                                            "source": "osm", "status": "found"},
             "city|bhopal|madhya pradesh": {"key": "city|bhopal|madhya pradesh", "lat": None, "lng": None,
                                            "source": "osm", "status": "not_found"},
             "centre|34|A3": {"key": "centre|34|A3", "lat": 22.8, "lng": 75.9, "source": "admin", "status": "admin"}}
    out = geo.build(items, "window", known=known)
    pts = {p["key"]: p for p in out["points"]}
    a1, a2, a3 = pts["centre|34|A1"], pts["centre|34|A2"], pts["centre|34|A3"]
    assert a1["precision"] == a2["precision"] == "city" and (a1["lat"], a1["lng"]) != (a2["lat"], a2["lng"])
    assert abs(a1["lat"] - 22.72) < 0.05 and abs(a2["lng"] - 75.86) < 0.05
    assert (a3["lat"], a3["lng"], a3["precision"]) == (22.8, 75.9, "admin")
    assert a1["status"] == "alarm" and a2["status"] == "ok" and pts["centre|34|B1"]["status"] == "warning"
    assert a2["counts"]["camerasOffline"] == 1 and a3["counts"]["syncFailed"] == 1
    assert pts["centre|34|B1"]["lat"] is None and pts["centre|34|B1"]["reason"] == "not_found"
    assert pts["nocentre|34"]["reason"] == "no_centre"
    # deterministic
    again = {p["key"]: p for p in geo.build(items, "window", known=known)["points"]}
    assert (again["centre|34|A1"]["lat"], again["centre|34|A1"]["lng"]) == (a1["lat"], a1["lng"])
    indore = next(c for c in out["cities"] if c["key"] == "city|indore|madhya pradesh")
    assert indore["centres"] == 3 and indore["pending"] == 1 and indore["status"] == "alarm"
    assert out["geocoding"]["resolved"] == 1 and out["geocoding"]["failed"] == 1


def test_geocoder_caches_answers_without_network():
    geo._pending["city|indore|madhya pradesh"] = ("INDORE", "Madhya Pradesh")
    geo._pending["city|nowhere|madhya pradesh"] = ("NOWHERE", "Madhya Pradesh")
    fake = {"INDORE": (22.72, 75.86, "Indore"), "NOWHERE": None}
    old = geo.MIN_INTERVAL
    geo.MIN_INTERVAL = 0
    try:
        assert geo.work_once(fetch=lambda c, s: fake[c]) is True
        assert geo.work_once(fetch=lambda c, s: fake[c]) is True
        assert geo.work_once(fetch=lambda c, s: fake[c]) is None
    finally:
        geo.MIN_INTERVAL = old
    known = geo.places()
    assert known["city|indore|madhya pradesh"]["lat"] == 22.72
    assert known["city|nowhere|madhya pradesh"]["status"] == "not_found"


def test_admin_can_set_city_position(admin):
    s, r = admin.put("/api/map/places", {"key": "city|INDORE|Madhya Pradesh", "lat": 22.7, "lng": 75.8})
    assert s == 200 and r["place"]["key"] == "city|indore|madhya pradesh"


def test_client_user_gets_404(client_a):
    assert client_a.get("/api/map")[0] == 404
    assert client_a.put("/api/map/places", {"key": "city|a|b", "lat": 1, "lng": 1})[0] == 404
