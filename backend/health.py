"""
health.py — camera connection & recording health.

Kept strictly separate from alarms: an alarm titled "Camera Online" is an EVENT,
not proof of the camera's current state. Health comes only from a real source:

  * "none" (default)  — no source connected → every value is UNKNOWN and the UI
                        says HEALTH DATA NOT AVAILABLE.
  * "camview"         — Camview reports the camera's connection with every alarm
                        (camera.frameSyncStatus = SYNCED | OFFLINE | FAILED and
                        camera.lastFrameSync). Every live refresh writes it here
                        (sync_from_camview); the heartbeat is the time Camview
                        reported it, so it goes UNKNOWN when the feed stops.
  * "push"            — a monitoring system (NVR / VMS / heartbeat service) POSTs
                        status to /api/camera-health/ingest with the server-side
                        CAMVIEW_HEALTH_INGEST_TOKEN. Nothing is inferred.

Camview's listAlarms API has no recording endpoint, so recording stays UNKNOWN
unless a push source reports it. Another adapter only has to write rows shaped
like the camera_health table (see ingest()).

Machine-readable states:
  camera.state     online | offline | unknown
  recording.state  recording | not_recording | unknown
  stream.state     available | unavailable | unknown
Conditions: CAMERA_ONLINE, CAMERA_OFFLINE, CAMERA_ONLINE_NO_RECORDING, RECORDING_ACTIVE,
  RECORDING_NOT_DETECTED, STREAM_AVAILABLE, STREAM_UNAVAILABLE, FRAME_SYNC_FAILED, HEARTBEAT_STALE,
  HEALTH_UNAVAILABLE
Transitions (lastEvent): CAMERA_RECONNECTED, CAMERA_DISCONNECTED, RECORDING_STARTED, RECORDING_STOPPED
"""

import os
from datetime import datetime, timezone

import db
from camview_client import ApiError

CAMERA_STATES = ("online", "offline", "unknown")
RECORDING_STATES = ("recording", "not_recording", "unknown")
STREAM_STATES = ("available", "unavailable", "unknown")
_ALIASES = {"up": "online", "down": "offline", "active": "recording", "inactive": "not_recording",
            "notrecording": "not_recording", "no_recording": "not_recording"}


# camera.frameSyncStatus → camera / stream state. FAILED = Camview could not sync frames: the camera is
# not delivering (stream unavailable) but Camview does not say it is disconnected, so it stays UNKNOWN
# and is flagged FRAME_SYNC_FAILED instead of being called offline.
CAMVIEW_STATES = {"SYNCED": ("online", "available"), "OFFLINE": ("offline", "unavailable"),
                  "FAILED": ("unknown", "unavailable")}


def source_mode():
    if os.environ.get("CAMVIEW_HEALTH_INGEST_TOKEN", "").strip():
        return "push"
    return "camview" if db.one("SELECT 1 FROM camera_health WHERE source='camview' LIMIT 1") else "none"


def ingest_token():
    return os.environ.get("CAMVIEW_HEALTH_INGEST_TOKEN", "").strip()


def stale_seconds():
    try:
        return max(30, int(os.environ.get("CAMVIEW_HEALTH_STALE_SECONDS", "300") or 300))
    except ValueError:
        return 300


def _age(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds()
    except (TypeError, ValueError):
        return None


def unavailable():
    return {"available": False, "source": "none",
            "camera": {"state": "unknown", "lastHeartbeatAt": None},
            "recording": {"state": "unknown", "lastRecordingAt": None},
            "stream": {"state": "unknown"}, "lastEvent": None, "conditions": ["HEALTH_UNAVAILABLE"]}


def _view(r):
    """Row → public health view with explicit conditions. A stale heartbeat makes the camera UNKNOWN."""
    if not r:
        return unavailable()
    cam, rec, stream = r["camera_state"] or "unknown", r["recording_state"] or "unknown", r["stream_state"] or "unknown"
    cond = []
    age = _age(r["last_heartbeat_at"])
    raw = (r["raw_status"] if "raw_status" in r.keys() else None) or None
    if age is None or age > stale_seconds():
        cond.append("HEARTBEAT_STALE")
        cam, rec, stream = "unknown", "unknown", "unknown"
    elif raw == "FAILED":
        cond.append("FRAME_SYNC_FAILED")
    if cam == "online":
        cond.append("CAMERA_ONLINE")
    elif cam == "offline":
        cond.append("CAMERA_OFFLINE")
    if rec == "recording":
        cond.append("RECORDING_ACTIVE")
    elif rec == "not_recording":
        cond.append("RECORDING_NOT_DETECTED")
        if cam == "online":
            cond.append("CAMERA_ONLINE_NO_RECORDING")
    if stream == "available":
        cond.append("STREAM_AVAILABLE")
    elif stream == "unavailable":
        cond.append("STREAM_UNAVAILABLE")
    return {"available": True, "source": r["source"] or "push", "raw": raw,
            "camera": {"state": cam, "lastHeartbeatAt": r["last_heartbeat_at"]},
            "recording": {"state": rec, "lastRecordingAt": r["last_recording_at"]},
            "stream": {"state": stream}, "updatedAt": r["updated_at"],
            "lastEvent": {"type": r["last_event"], "at": r["last_event_at"]} if r["last_event"] else None,
            "conditions": cond}


def lookup(pairs):
    """{(project_id, camera_id): health view} for the given pairs (chunked queries)."""
    pairs = {(str(p), str(c)) for p, c in pairs if c not in (None, "")}
    if not pairs:
        return {}
    rows = {}
    cams = sorted({c for _, c in pairs})
    for i in range(0, len(cams), 500):
        chunk = cams[i:i + 500]
        for r in db.rows(f"SELECT * FROM camera_health WHERE camera_id IN ({','.join('?' for _ in chunk)})", chunk):
            rows[(r["project_id"], r["camera_id"])] = r
    return {k: _view(rows.get(k)) for k in pairs}


def get(project_id, camera_id):
    return lookup([(project_id, camera_id)]).get((str(project_id), str(camera_id)), unavailable())


def _state(v, allowed):
    v = "unknown" if v in (None, "") else str(v).strip().lower().replace(" ", "_").replace("-", "_")
    v = _ALIASES.get(v, v)
    if v not in allowed:
        raise ApiError("bad_request", f"Unknown state '{v}'. Allowed: {', '.join(allowed)}.", 400)
    return v


def ingest(records, source="push"):
    """Upserts real health reports: [{projectId, cameraId, cameraState, recordingState,
    streamState?, lastHeartbeatAt?, lastRecordingAt?}]. Returns (saved, transition events)."""
    if not isinstance(records, list) or not records:
        raise ApiError("bad_request", "Send a non-empty list of camera health records.", 400)
    if len(records) > 5000:
        raise ApiError("bad_request", "At most 5000 records per request.", 400)
    now = db.now_iso()
    clean = []
    for rec in records:
        if not isinstance(rec, dict):
            raise ApiError("bad_request", "Every record must be an object.", 400)
        pid, cid = str(rec.get("projectId") or "").strip(), str(rec.get("cameraId") or "").strip()
        if not pid or not cid:
            raise ApiError("bad_request", "projectId and cameraId are required on every record.", 400)
        clean.append((pid, cid, _state(rec.get("cameraState"), CAMERA_STATES),
                      _state(rec.get("recordingState"), RECORDING_STATES), _state(rec.get("streamState"), STREAM_STATES),
                      rec.get("lastHeartbeatAt") or now, rec.get("lastRecordingAt"),
                      (str(rec.get("rawStatus") or "").strip().upper() or None)))
    events = []
    with db.connect() as conn:
        for pid, cid, cam, recst, stream, hb, last_rec, raw in clean:
            prev = conn.execute("SELECT camera_state, recording_state, last_event, last_event_at FROM camera_health "
                                "WHERE project_id=? AND camera_id=?", (pid, cid)).fetchone()
            event, event_at = (prev["last_event"], prev["last_event_at"]) if prev else (None, None)
            if prev and prev["camera_state"] != cam and cam in ("online", "offline"):
                event, event_at = ("CAMERA_RECONNECTED" if cam == "online" else "CAMERA_DISCONNECTED"), now
            if prev and prev["recording_state"] != recst and recst in ("recording", "not_recording"):
                event, event_at = ("RECORDING_STARTED" if recst == "recording" else "RECORDING_STOPPED"), now
            if event_at == now:
                events.append({"projectId": pid, "cameraId": cid, "event": event})
            conn.execute("""INSERT INTO camera_health (project_id, camera_id, camera_state, recording_state, stream_state,
                    last_heartbeat_at, last_recording_at, last_event, last_event_at, source, updated_at, raw_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id, camera_id) DO UPDATE SET camera_state=excluded.camera_state,
                    recording_state=excluded.recording_state, stream_state=excluded.stream_state,
                    last_heartbeat_at=excluded.last_heartbeat_at,
                    last_recording_at=COALESCE(excluded.last_recording_at, camera_health.last_recording_at),
                    last_event=excluded.last_event, last_event_at=excluded.last_event_at, source=excluded.source,
                    updated_at=excluded.updated_at, raw_status=excluded.raw_status""",
                         (pid, cid, cam, recst, stream, hb, last_rec, event, event_at, source, now, raw))
    return len(clean), events


def sync_from_camview(project_id, items, reported_at=None):
    """Writes the camera connection Camview sent with the alarms of one project (the latest record
    per camera). Recording is left UNKNOWN: Camview does not report it. A push source, when
    configured, is authoritative and is never overwritten. Returns (saved, transition events)."""
    if source_mode() == "push":
        return 0, []
    latest = {}
    for a in items or []:
        fs, cid = a.get("frameSyncStatus"), str(a.get("cameraId") or "").strip()
        if fs not in CAMVIEW_STATES or not cid:
            continue
        if cid not in latest or (a.get("lastInstance") or "") > (latest[cid].get("lastInstance") or ""):
            latest[cid] = a
    if not latest:
        return 0, []
    now = reported_at or db.now_iso()
    records = []
    for cid, a in latest.items():
        cam, stream = CAMVIEW_STATES[a["frameSyncStatus"]]
        records.append({"projectId": str(project_id), "cameraId": cid, "cameraState": cam, "recordingState": "unknown",
                        "streamState": stream, "lastHeartbeatAt": now, "lastRecordingAt": a.get("lastFrameSync"),
                        "rawStatus": a["frameSyncStatus"]})
    saved, events = 0, []
    for i in range(0, len(records), 5000):
        n, ev = ingest(records[i:i + 5000], source="camview")
        saved += n
        events.extend(ev)
    return saved, events


NOTES = {
    "camview": "Camera connection comes from Camview (camera.frameSyncStatus / lastFrameSync sent with every alarm, "
               "refreshed with the live feed). Recording status is not reported by Camview: connect an NVR / VMS "
               "source (POST /api/camera-health/ingest) to get it.",
    "push": "A monitoring source is pushing camera and recording status to /api/camera-health/ingest.",
    "none": "No camera status received yet. In live mode Camview's frameSyncStatus is imported automatically with "
            "the first refresh; a monitoring source can POST to /api/camera-health/ingest with the token set in "
            "CAMVIEW_HEALTH_INGEST_TOKEN.",
}


def status():
    r = db.one("SELECT COUNT(*) AS n, MAX(updated_at) AS last FROM camera_health")
    mode = source_mode()
    return {"mode": mode, "camerasReporting": r["n"], "lastReportAt": r["last"],
            "staleSeconds": stale_seconds(), "note": NOTES[mode]}
