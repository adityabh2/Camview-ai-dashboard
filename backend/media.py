"""
media.py — the AI detection metadata Camview stores next to each alarm frame
(alarm.metadataUrl → {"camera_id", "timestamp", "models": [{"model", "results": [{"label",
"confidence", "box": {x1, y1, x2, y2}}]}]}).

It is what the alert itself says it detected ("person ×3", "truck", "tampering_scene_change"),
so the review screen shows it next to the image, with the boxes drawn on the frame. Fetched
lazily (only when an alert is opened), cached per file, never on the list refresh path.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import requests

log = logging.getLogger("camview.media")

# ---------------------------------------------------------------------------
# Evidence links: resolved when asked for, never trusted forever
# ---------------------------------------------------------------------------
# Camview's media links are signed and expire (7 days). A record read today from the store may carry a link
# that no longer works, so the browser never gets a stored link directly: it asks /api/media/<alarm>/<kind>/<i>
# and the server resolves the freshest link (live feed → store), notices an expired signature, re-reads the
# project once to get a newly signed link, and either redirects to it or streams it (Range requests pass through).
# A missing / expired / slow file is a clean 404 / 410 / 504 — the page shows IMAGE / VIDEO UNAVAILABLE.

EXPIRY_MARGIN = 60          # seconds: a link this close to expiry is treated as expired
_refreshed = {}             # project id -> epoch of the last "links expired" re-read (at most one a minute)


def expires_at(url):
    """When a signed link stops working (UTC datetime), or None when it carries no known expiry.
    Understands S3 SigV4 (X-Amz-Date + X-Amz-Expires), S3 SigV2 / CloudFront (Expires=epoch) and GCS."""
    try:
        q = {k.lower(): v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    except (TypeError, ValueError):
        return None
    for date_k, exp_k in (("x-amz-date", "x-amz-expires"), ("x-goog-date", "x-goog-expires")):
        if date_k in q and exp_k in q:
            try:
                start = datetime.strptime(q[date_k], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                return start + timedelta(seconds=int(q[exp_k]))
            except ValueError:
                return None
    if "expires" in q and q["expires"].isdigit():
        return datetime.fromtimestamp(int(q["expires"]), tz=timezone.utc)
    return None


def is_expired(url, margin=EXPIRY_MARGIN):
    exp = expires_at(url) if isinstance(url, str) else None
    return bool(exp and exp <= datetime.now(timezone.utc) + timedelta(seconds=margin))


def pick(record, kind, index):
    if not record:
        return None
    if kind == "video":
        return record.get("videoUrl") if index == 0 else None
    if kind == "image":
        urls = record.get("imageUrls") or []
        return urls[index] if 0 <= index < len(urls) else None
    return None


def resolve(alarm_id, kind, index, project_id=None, force_fresh=False):
    """(url, state): the freshest link for one evidence item. state = ok | expired | missing.
    With `force_fresh` (the upstream refused the link) the project is re-read once to get a newly signed link."""
    import alert_service
    import datasource
    rec, _ = alert_service.media_record(alarm_id, project_id)
    url = pick(rec, kind, index)
    if not url:
        return None, "missing"
    if url.startswith("/demo-evidence/"):
        return url, "ok"                                  # same-origin demo evidence
    if not url.lower().startswith(("https://", "http://")):
        return None, "missing"                            # never follow anything else (no loops, no local paths)
    pid = str((rec or {}).get("projectId") or project_id or "")
    if (force_fresh or is_expired(url)) and pid:
        if time.time() - _refreshed.get(pid, 0) > 60:
            _refreshed[pid] = time.time()
            datasource.refresh(pid, force=True)           # Camview signs every link anew on each read
            rec, _ = alert_service.media_record(alarm_id, pid)
            fresh = pick(rec, kind, index)
            if fresh and fresh != url:
                return fresh, "ok"
        return url, "expired"
    return url, "ok"


def open_stream(url, range_header=None, timeout=15):
    """GETs a media file for streaming. Returns (response, None) or (None, (code, message, status))."""
    try:
        upstream = requests.get(url, stream=True, timeout=timeout,
                                headers={"Range": range_header} if range_header else {})
    except requests.Timeout:
        return None, ("media_timeout", "The evidence file did not arrive in time.", 504)
    except requests.RequestException:
        return None, ("media_unavailable", "The evidence service could not be reached.", 502)
    if upstream.status_code in (403, 410):
        upstream.close()
        return None, ("media_expired", "The link to this evidence file has expired.", 410)
    if upstream.status_code == 404:
        upstream.close()
        return None, ("media_not_found", "This evidence file does not exist (anymore).", 404)
    if upstream.status_code == 416:
        upstream.close()
        return None, ("media_range", "Requested range not satisfiable.", 416)
    if upstream.status_code >= 400:
        upstream.close()
        return None, ("media_unavailable", "The evidence file could not be loaded right now.", 502)
    return upstream, None


STREAM_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")

_cache = {}
_lock = threading.Lock()
CACHE_MAX = 2000
CACHE_TTL = 6 * 3600


def detections(url, timeout=4):
    """Parsed detections for a metadataUrl, or None when there is no file / it cannot be read."""
    if not url or not isinstance(url, str):
        return None
    key = urlsplit(url)._replace(query="", fragment="").geturl()      # signed links change, the file does not
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    data = None
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
    except (requests.RequestException, ValueError):
        data = None
    out = parse(data) if isinstance(data, dict) else None
    with _lock:
        if len(_cache) >= CACHE_MAX:
            _cache.clear()
        _cache[key] = (now, out)
    return out


def parse(data):
    boxes, counts, best, models = [], {}, {}, []
    for model in data.get("models") or []:
        if not isinstance(model, dict):
            continue
        if model.get("model"):
            models.append(str(model["model"]))
        for r in model.get("results") or []:
            if not isinstance(r, dict):
                continue
            label = str(r.get("label") or "").strip()
            if not label:
                continue
            conf = r.get("confidence")
            conf = round(float(conf), 3) if isinstance(conf, (int, float)) else None
            counts[label] = counts.get(label, 0) + 1
            if conf is not None and conf > best.get(label, -1):
                best[label] = conf
            box = r.get("box") if isinstance(r.get("box"), dict) else None
            if box and all(isinstance(box.get(k), (int, float)) for k in ("x1", "y1", "x2", "y2")):
                boxes.append({"label": label, "confidence": conf, "x1": box["x1"], "y1": box["y1"],
                              "x2": box["x2"], "y2": box["y2"]})
    ts = data.get("timestamp")
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    labels = sorted(({"label": lb, "count": c, "confidence": best.get(lb)} for lb, c in counts.items()),
                    key=lambda x: (-x["count"], x["label"]))
    return {"labels": labels, "boxes": boxes[:60], "models": sorted(set(models)),
            "timestamp": ts if isinstance(ts, str) else None,
            "summary": ", ".join(f"{x['label']}" + (f" ×{x['count']}" if x["count"] > 1 else "") for x in labels) or None}
