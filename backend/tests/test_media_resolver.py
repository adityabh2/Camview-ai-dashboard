"""
D. media reliability

Stored signed links are never trusted forever: the browser asks /api/media/<alarm>/<kind>/<i>, the server resolves
the freshest link, re-reads the project when the link expired, and maps every upstream failure (404 / 403 / 410 /
timeout) to a clean answer. Range requests pass through. One broken item never breaks the page.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

import datasource
import db
import media
import obs
import workflow
from fakecamview import MEDIA, FakeCamview, signed


class Stream:
    def __init__(self, status, headers=None, body=b"x" * 10):
        self.status_code, self.headers, self.body, self.closed = status, headers or {}, body, False

    def iter_content(self, n):
        yield self.body

    def close(self):
        self.closed = True


@pytest.fixture()
def cv(monkeypatch):
    f = FakeCamview(monkeypatch)
    f.items = [f.raw("M1"), f.raw("M2", cam=502, video=False)]
    datasource.refresh("77", force=True)
    return f


def _raw_get(c, path, **kw):
    return c.c.get(path, **kw)


def test_expiry_is_read_from_the_signature():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assert media.expires_at(signed("a.jpg", issued=now, seconds=3600)) == now + timedelta(seconds=3600)
    assert media.is_expired(signed("a.jpg", issued=now - timedelta(days=8)))
    assert not media.is_expired(signed("a.jpg", issued=now))
    epoch = int((now + timedelta(hours=1)).timestamp())
    assert media.expires_at(f"{MEDIA}/b.mp4?Expires={epoch}&Signature=x").timestamp() == epoch
    assert media.expires_at(f"{MEDIA}/c.jpg") is None and not media.is_expired(f"{MEDIA}/c.jpg")


def test_media_link_redirects_to_the_current_signed_link(cv, supervisor):
    r = _raw_get(supervisor, "/api/media/M1/image/0?projectId=77")
    assert r.status_code == 302
    assert r.headers["Location"].startswith(f"{MEDIA}/M1.jpg?") and f"s{cv.sig}" in r.headers["Location"]
    r = _raw_get(supervisor, "/api/media/M1/video/0?projectId=77")
    assert r.status_code == 302 and "/M1.mp4?" in r.headers["Location"]
    s, body = supervisor.get("/api/media/M2/video/0?projectId=77")      # this alert has no video
    assert s == 404 and body["error"] == "media_not_found"
    s, _ = supervisor.get("/api/media/M1/image/5?projectId=77")
    assert s == 404
    s, _ = supervisor.get("/api/media/M1/audio/0?projectId=77")
    assert s == 404


def test_detail_never_hands_out_a_stored_signed_link(cv, supervisor):
    s, d = supervisor.get("/api/queue/M1?projectId=77")
    urls = [e["url"] for e in d["evidence"]] + [d["alarm"].get("imageUrl"), d["alarm"].get("videoUrl")] + \
        d["alarm"].get("imageUrls", [])
    assert urls and all(u.startswith("/api/media/M1/") for u in urls if u), urls
    assert "X-Amz-Signature" not in json.dumps(d)
    s, q = supervisor.get("/api/queue?projectId=77&status=all")
    assert all((a["imageUrl"] or "").startswith("/api/media/") for a in q["items"] if a["imageUrl"])


def test_an_expired_stored_link_is_renewed_or_reported(cv, supervisor):
    stale = signed("M1.jpg", issued=datetime.now(timezone.utc) - timedelta(days=9), sig="old")
    rec = json.loads(db.one("SELECT record FROM alerts WHERE alarm_id='M1'")["record"])
    rec["imageUrls"], rec["imageUrl"] = [stale], stale
    db.execute("UPDATE alerts SET record=? WHERE alarm_id='M1'", (json.dumps(rec),))
    datasource.reset()                                   # restart; Camview is down: only the stored (expired) link exists
    cv.mode = 500
    s, body = supervisor.get("/api/media/M1/image/0?projectId=77")
    assert s == 410 and body["error"] == "media_expired"
    assert obs.counters()["media.failure"] >= 1
    s, d = supervisor.get("/api/queue/M1?projectId=77")  # …and the alert page itself still opens
    assert s == 200 and d["alert"]["source"] == "stored"
    cv.mode = "ok"                                       # Camview is back: the link is renewed on demand
    media._refreshed.clear()
    r = _raw_get(supervisor, "/api/media/M1/image/0?projectId=77")
    assert r.status_code == 302 and "sig=old" not in r.headers["Location"] and "Signature=old" not in r.headers["Location"]


@pytest.mark.parametrize("upstream,status,code", [(404, 404, "media_not_found"), (403, 410, "media_expired"),
                                                  (410, 410, "media_expired"), (500, 502, "media_unavailable"),
                                                  ("timeout", 504, "media_timeout"), ("down", 502, "media_unavailable")])
def test_proxied_media_failures_are_clean(cv, supervisor, monkeypatch, upstream, status, code):
    def fake_get(url, stream=False, timeout=None, headers=None):
        if upstream == "timeout":
            raise requests.Timeout("slow")
        if upstream == "down":
            raise requests.ConnectionError("down")
        return Stream(upstream)
    monkeypatch.setattr(requests, "get", fake_get)
    s, body = supervisor.get("/api/media/M1/video/0?projectId=77&proxy=1")
    assert s == status and body["error"] == code and body["requestId"]


def test_range_requests_pass_through(cv, supervisor, monkeypatch):
    seen = {}

    def fake_get(url, stream=False, timeout=None, headers=None):
        seen["range"] = (headers or {}).get("Range")
        return Stream(206, {"Content-Type": "video/mp4", "Content-Range": "bytes 0-9/1000", "Accept-Ranges": "bytes",
                            "Content-Length": "10"})
    monkeypatch.setattr(requests, "get", fake_get)
    r = supervisor.c.get("/api/media/M1/video/0?projectId=77&proxy=1", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.headers["Content-Range"] == "bytes 0-9/1000" and r.data == b"x" * 10
    assert seen["range"] == "bytes=0-9"


def test_expired_upstream_link_is_renewed_once_while_streaming(cv, supervisor, monkeypatch):
    urls = []

    def fake_get(url, stream=False, timeout=None, headers=None):
        urls.append(url)
        return Stream(403) if len(urls) == 1 else Stream(200, {"Content-Type": "image/jpeg"})
    monkeypatch.setattr(requests, "get", fake_get)
    r = supervisor.c.get("/api/media/M1/image/0?projectId=77&proxy=1")
    assert r.status_code == 200 and len(urls) == 2 and urls[0] != urls[1]


def test_media_follows_the_viewers_scope(operator, supervisor):
    feed = datasource.refresh("12")                       # outside the operator's scope (project 07 only)
    other = next(a for a in feed.items if a.get("imageUrls"))
    s, _ = operator.get(f"/api/media/{other['alarmId']}/image/0?projectId=12")
    assert s == 404
    s, _ = operator.get(f"/api/media/{other['alarmId']}/image/0")
    assert s == 404
    mine = next(a for a in datasource.refresh("7").items if a.get("imageUrls"))
    r = operator.c.get(f"/api/media/{mine['alarmId']}/image/0?projectId=7")
    assert r.status_code == 302 and r.headers["Location"].startswith("/demo-evidence/")
    r = supervisor.c.get(r.headers["Location"])
    assert r.status_code == 200 and b"<svg" in r.data


def test_client_evidence_only_for_the_clients_own_valid_alerts(supervisor, client_a, client_b):
    workflow.set_policy({"deliveryMode": "automatic"}, None)
    s, q = supervisor.get("/api/queue?projectId=7&status=pending&size=50")
    a = next(x for x in q["items"] if x["evidence"]["count"])
    s, r = supervisor.post(f"/api/queue/{a['alarmId']}/decide", {"result": "valid", "projectId": 7, "expectedRevision": 0})
    assert r["delivery"]["status"] == "delivered", r
    s, d = client_a.get(f"/api/client/alerts/{a['alarmId']}")
    assert s == 200 and d["evidence"]
    url = d["evidence"][0]["url"]
    assert url.startswith("/api/client/evidence/")
    assert client_a.c.get(url).status_code == 200
    assert client_b.c.get(url).status_code == 404         # another client: identical "not available"
    assert client_b.get(f"/api/client/alerts/{a['alarmId']}")[0] == 404
    assert client_a.get(f"/api/media/{a['alarmId']}/image/0?projectId=7")[0] == 404   # internal endpoint: wall
