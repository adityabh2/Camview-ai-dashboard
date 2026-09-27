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
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

log = logging.getLogger("camview.media")

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
