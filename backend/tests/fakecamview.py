"""
A scriptable fake Camview for live-mode tests (listAlarms + signed media links).

    cv = FakeCamview(monkeypatch, project="77")
    cv.items = [cv.raw("A1"), cv.raw("A2", action=1)]
    cv.mode = 500          # every call now answers HTTP 500 ("ok", 403, 500, 503, "timeout", "network",
                           #  "malformed_json", "empty", "not_a_list")

No test ever reaches the network: requests.post is replaced (camview_client honours the replacement).
"""

import math
from datetime import datetime, timedelta, timezone

import requests

import camview_client
import config

MEDIA = "https://media.camviewai.com"


def signed(path, issued=None, seconds=604800, sig="abc"):
    issued = issued or datetime.now(timezone.utc)
    return (f"{MEDIA}/{path}?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date={issued.strftime('%Y%m%dT%H%M%SZ')}"
            f"&X-Amz-Expires={seconds}&X-Amz-Signature={sig}")


class Resp:
    def __init__(self, status, data=None, text=None):
        self.status_code, self._data = status, data
        self.text = text if text is not None else ("" if data is None else str(data))

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeCamview:
    def __init__(self, monkeypatch, project="77"):
        self.project = str(project)
        self.items = []
        self.mode = "ok"
        self.calls = 0
        self.sig = 1                      # bumped on every read: Camview re-signs every link on every read
        self.skip = set()                 # alarm ids left out of the next reads (Camview's shifting pages)
        monkeypatch.setattr(config, "MODE", "live")
        monkeypatch.setattr(config, "API_KEY", "test-key")
        monkeypatch.setattr(config, "DEFAULT_PROJECT_ID", self.project)
        monkeypatch.setattr(config, "CACHE_SECONDS", 2)
        monkeypatch.setattr(camview_client, "RETRY_BACKOFF", 0)
        monkeypatch.setattr(requests, "post", self)

    def raw(self, aid, cam=501, at="2026-09-28T10:00:00Z", typ=14, action=0, image=True, video=True, **alarm):
        a = {"alarmId": aid, "projectId": int(self.project), "cameraId": cam, "alarmType": typ, "priority": 1,
             "lastActionType": action, "firstInstance": at, "lastInstance": at, "totalTimesReported": 1, **alarm}
        return {"camera": {"cameraId": cam, "centerCode": "C018", "center": "GOVT SCHOOL_Kanpur_Uttar Pradesh",
                           "subLocation": "Room204", "cameraNumber": f"{cam}_1", "frameSyncStatus": "SYNCED",
                           "lastFrameSync": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                "alarm": a, "_image": image, "_video": video}

    def _signed_item(self, it):
        it = {k: v for k, v in it.items() if not k.startswith("_")}
        a = dict(it.get("alarm") or {})
        aid = a.get("alarmId")
        raw = self._index().get(aid, {})
        if raw.get("_image", True) and aid:
            it["imageUrls"] = [signed(f"{aid}.jpg", sig=f"s{self.sig}")]
        if raw.get("_video", True) and aid:
            a["videoUrl"] = signed(f"{aid}.mp4", sig=f"s{self.sig}")
        it["alarm"] = a
        return it

    def _index(self):
        """alarm id → raw item; rebuilt when the item list changed (kept O(1) per record for the load tests)."""
        key = (id(self.items), len(self.items))
        if getattr(self, "_idx_key", None) != key:
            self._idx = {x["alarm"].get("alarmId"): x for x in self.items
                         if isinstance(x, dict) and isinstance(x.get("alarm"), dict)}
            self._idx_key = key
        return self._idx

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        m = self.mode
        if m == "timeout":
            raise requests.exceptions.Timeout("slow")
        if m == "network":
            raise requests.exceptions.ConnectionError("down")
        if isinstance(m, int):
            return Resp(m, {"message": "error"})
        if m == "malformed_json":
            return Resp(200, None, "<html>gateway</html>")
        if m == "empty":
            return Resp(200, {})
        if m == "not_a_list":
            return Resp(200, {"cameraAlarmsDetails": {"oops": 1}, "totalElements": 1})
        if url.endswith("/projects/getProject"):
            return Resp(403, {"message": "forbidden"})
        if str(json.get("projectId")) != self.project:     # any other project: exists, but has no alarms
            return Resp(200, {"cameraAlarmsDetails": [], "totalElements": 0, "totalPages": 0, "hasNext": False})
        self.sig += 1

        def aid_of(x):
            return x["alarm"].get("alarmId") if isinstance(x, dict) and isinstance(x.get("alarm"), dict) else None
        visible = [x for x in self.items if aid_of(x) is None or aid_of(x) not in self.skip]
        page, size = json["page"], json["size"]
        chunk = visible[page * size:(page + 1) * size]
        chunk = [self._signed_item(x) if isinstance(x, dict) and isinstance(x.get("alarm"), dict) else x for x in chunk]
        pages = max(1, math.ceil(len(visible) / size))
        return Resp(200, {"cameraAlarmsDetails": chunk, "totalElements": len(visible), "totalPages": pages,
                          "page": page, "size": size, "hasNext": page + 1 < pages})


def old(days):
    return datetime.now(timezone.utc) - timedelta(days=days)
