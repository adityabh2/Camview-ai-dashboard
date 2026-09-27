"""
alarms.py — turns Camview `listAlarms` items into the flat shape the UI uses.

The real API returns each row nested:

    {"camera": {...}, "alarm": {...}, "imageUrls": [...], "serialNumber": 1}

with numeric codes (alarmType, priority, lastActionType). The frontend wants
one flat dict per alarm with human-readable labels, so both live data and
mock data are run through `normalize()` here. That is what lets the UI stay
identical between Sample Data and Live Mode.
"""

import re
from collections import defaultdict
from datetime import datetime, timezone

# lastActionType semantics, as documented: 0=pending, 1=valid, 2=invalid, 3=exception
ACTION_LABELS = {0: "Pending", 1: "Valid", 2: "Invalid", 3: "Exception"}

# Alert type names. Camview's API sends only the number; these names were read from the alerts themselves
# (the AI labels in each alert's metadata file and the frames) and are seeded into the Dictionary on the first
# live start, where an administrator can confirm or rename them.
DEFAULT_TYPE_NAMES = {
    1: ("Person Movement", "People detected at an entry / exit or outdoor area (AI label: person)", "medium"),
    2: ("Vehicle & Person Detected", "Vehicles (car, motorcycle, truck) with people near the entrance", "medium"),
    3: ("Trunk Open / Closed", "Question-paper trunk seen open or closed (AI labels: open_trunk, closed_trunk)", "high"),
    4: ("Trunk Changed", "Trunk state changed (AI label: trunk_changed)", "critical"),
    5: ("Vehicle Movement", "Vehicles (car, truck, bus) in the monitored area", "medium"),
    6: ("Server Room Activity", "Activity in the server / strong room", "high"),
    10: ("Camera Status", "Camera online / offline event sent by Camview (no image or video)", "low"),
    11: ("Camera Tampering", "Scene change or tampering on the camera (AI label: tampering_scene_change)", "high"),
    12: ("Lab Activity", "Activity among candidates in the exam lab", "medium"),
    14: ("Mobile Phone Detected", "A mobile phone in the frame (AI label: cell_phone)", "critical"),
    15: ("Candidate Standing", "A person standing at the frisking / biometric point (AI label: person_standing)", "medium"),
}
INFERRED_NOTE = "Name inferred from the alerts' AI labels and frames — confirm or rename in Nomenclature › Dictionary."

# Priority numbers are not documented. This is a best guess and can be
# overridden with CAMVIEW_PRIORITY_LABELS in backend/.env.
DEFAULT_PRIORITY_LABELS = {1: "critical", 2: "high", 3: "medium", 4: "low"}

# Camera objects are not documented either. The first of these keys that is
# present on the camera is used as the "hall" (location) for grouping.
HALL_KEYS = ("hall", "hallName", "location", "locationName", "zone",
             "centerName", "cameraName", "name")
CAMERA_NAME_KEYS = ("cameraName", "name", "displayName", "cameraLabel", "label", "title")
# Alarm-type names Camview may send alongside the numeric alarmType (first match wins).
TYPE_NAME_KEYS = ("alarmTypeName", "alarmName", "alarmTypeLabel", "typeName", "eventName", "eventType", "alarmTitle",
                  "title", "category")
# Camera connection states Camview reports with each camera (camera.frameSyncStatus).
FRAME_SYNC_STATES = ("SYNCED", "OFFLINE", "FAILED")
_CAMERA_EVENT = re.compile(r"camera[\s_-]*(online|offline|disconnected|reconnected|connected)", re.I)


def pretty_place(v):
    """'MAHILA MAHAVIDYALAYA_KANPUR NAGAR_Uttar Pradesh' → 'MAHILA MAHAVIDYALAYA, KANPUR NAGAR, Uttar Pradesh'."""
    return ", ".join(p.strip() for p in str(v).split("_") if p.strip()) if v not in (None, "") else None


def place_name(center, city=None, state=None):
    """Centre name from Camview's fields: 'Ghughuwa_Durg_Durg_CHHATTISGARH' → 'Ghughuwa, Durg, Durg, CHHATTISGARH';
    a separate city / state is appended when it is not already part of the name."""
    parts = [p.strip() for p in str(center or "").split("_") if p.strip()]
    for extra in (city, state):
        if extra not in (None, "") and str(extra).strip().lower() not in {p.lower() for p in parts}:
            parts.append(str(extra).strip())
    return ", ".join(parts) or None


def pretty_name(v):
    """'MOBILE_DETECTION' / 'truck-tampering' → 'Mobile Detection' / 'Truck Tampering'. Keeps mixed-case names."""
    s = str(v).strip()
    if not s or s.isdigit():
        return None
    if s.isupper() or s.islower() or "_" in s or "-" in s:
        s = " ".join(w.capitalize() for w in s.replace("_", " ").replace("-", " ").split())
    return s


def parse_id_map(raw, default=None):
    """Parses "1:critical,2:high" into {1: "critical", 2: "high"}."""
    result = dict(default or {})
    for part in (raw or "").split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        try:
            result[int(key.strip())] = value.strip()
        except ValueError:
            continue
    return result


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_iso(value):
    """Accepts ISO strings or epoch milliseconds; returns an ISO string."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _first(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def event_kind(alarm):
    """'camera_status' for Camview's camera online/offline events (they carry a status in alarmMetadata
    and never an image or video), 'alert' for everything else (detections with evidence to review)."""
    md = alarm.get("alarmMetadata") if isinstance(alarm.get("alarmMetadata"), dict) else {}
    status = str(md.get("status") or "").strip().upper()
    text = " ".join(str(md.get(k) or "") for k in ("reason", "message", "type"))
    if status in ("ONLINE", "OFFLINE") or _CAMERA_EVENT.search(text):
        return "camera_status"
    if _CAMERA_EVENT.search(str(_first(alarm, TYPE_NAME_KEYS) or "")):
        return "camera_status"
    return "alert"


def normalize(item, type_names=None, priority_labels=None, hall_field=None):
    """Flattens one `cameraAlarmsDetails` entry. Also accepts an already-flat
    alarm dict (no "alarm" key) so older callers keep working."""
    type_names = type_names or {}
    priority_labels = priority_labels or DEFAULT_PRIORITY_LABELS

    alarm = item.get("alarm") if isinstance(item.get("alarm"), dict) else item
    camera = item.get("camera") if isinstance(item.get("camera"), dict) else {}

    camera_id = alarm.get("cameraId", camera.get("cameraId", camera.get("id")))
    # Camview's own camera naming: subLocation ("Camera1", "CONTROL ROOM") at a centre, cameraNumber ("1000056_2")
    # as the camera's identifier and deviceId as the physical device. No name is invented on top of them.
    sub_loc, cam_no = camera.get("subLocation"), camera.get("cameraNumber")
    sub_loc = None if sub_loc in (None, "") else str(sub_loc).strip()
    cam_no = None if cam_no in (None, "") else str(cam_no).strip()
    camera_name = _first(camera, CAMERA_NAME_KEYS) or _first(alarm, ("cameraName", "cameraLabel")) or sub_loc or cam_no         or (f"Camera {camera_id}" if camera_id is not None else "Unknown camera")
    frame_sync = str(camera.get("frameSyncStatus") or "").strip().upper() or None
    hall = camera.get(hall_field) if hall_field else None
    hall = hall or _first(camera, HALL_KEYS) or camera_name

    type_id = _to_int(alarm.get("alarmType"))
    priority_level = _to_int(alarm.get("priority"))
    action = _to_int(alarm.get("lastActionType"))
    kind = event_kind(alarm)
    md = alarm["alarmMetadata"] if isinstance(alarm.get("alarmMetadata"), dict) else {}
    # A camera status event is named by what Camview said ("Camera Offline"). For detections the metadata
    # reason is a detail of that one alert (e.g. "no_flip"), never the name of the whole type.
    md_name = pretty_name(_first(md, ("reason", "message", "type")) or "") if kind == "camera_status" else None

    image_urls = item.get("imageUrls") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    image_urls = [u for u in image_urls if isinstance(u, str) and u.strip()] if isinstance(image_urls, list) else []
    if not image_urls and isinstance(alarm.get("imageUrl"), str) and alarm["imageUrl"].strip():
        image_urls = [alarm["imageUrl"]]

    return {
        "alarmId": str(alarm.get("alarmId") or f"row-{item.get('serialNumber', '')}"),
        "serialNumber": item.get("serialNumber"),
        "cameraId": "" if camera_id is None else str(camera_id),
        "cameraName": str(camera_name),
        "centreCode": None if camera.get("centerCode") in (None, "") else str(camera["centerCode"]),
        "centreName": place_name(camera.get("center"), camera.get("city"), camera.get("state")),
        "cameraNumber": cam_no,
        "deviceId": None if camera.get("deviceId") in (None, "") else str(camera["deviceId"]),
        "tcCode": None if camera.get("tcCode") in (None, "") else str(camera["tcCode"]).strip(),
        "cameraCity": None if camera.get("city") in (None, "") else str(camera["city"]).strip(),
        "cameraState": None if camera.get("state") in (None, "") else str(camera["state"]).strip(),
        # camera connection as Camview reports it with the alarm (SYNCED / OFFLINE / FAILED) → health.py
        "frameSyncStatus": frame_sync,
        "lastFrameSync": _to_iso(camera.get("lastFrameSync")),
        "alarmMetadata": alarm.get("alarmMetadata") if isinstance(alarm.get("alarmMetadata"), dict) else None,
        "eventKind": kind,
        "cameraSubLocation": sub_loc,
        "hall": str(hall),
        "projectId": alarm.get("projectId"),
        "alarmType": type_id,
        # dictionary (Nomenclature › Dictionary / CAMVIEW_ALARM_TYPE_NAMES) → name sent by Camview → "Alert type N"
        "alarmTypeName": type_names.get(type_id) or pretty_name(_first(alarm, TYPE_NAME_KEYS) or "") or md_name
                         or (f"Alert type {type_id}" if type_id is not None else "Unknown alert type"),
        "alarmState": alarm.get("alarmState"),
        "lastActionType": action,
        "lastActionLabel": ACTION_LABELS.get(action, "Unknown"),
        "priorityLevel": priority_level,
        "priority": priority_labels.get(priority_level) or (f"P{priority_level}" if priority_level is not None else "unknown"),
        "firstInstance": _to_iso(alarm.get("firstInstance")),
        "lastInstance": _to_iso(alarm.get("lastInstance")),
        "totalTimesReported": _to_int(alarm.get("totalTimesReported")) or 1,
        "ticketId": alarm.get("ticketId"),
        "imageUrl": alarm.get("imageUrl") or (image_urls[0] if image_urls else None),
        "imageUrls": image_urls,
        "videoUrl": alarm["videoUrl"] if isinstance(alarm.get("videoUrl"), str) and alarm["videoUrl"].strip() else None,
        # AI detection metadata (labels, confidences, boxes) Camview stores next to the frame
        "metadataUrl": alarm["metadataUrl"] if isinstance(alarm.get("metadataUrl"), str) and alarm["metadataUrl"].strip() else None,
        "shiftLabel": alarm.get("shiftLabel") or "",
        "suppressed": bool(alarm.get("suppressed")),
        "suppressionTrigger": alarm.get("suppressionTrigger"),
    }


def apply_filters(alarms, args, keys=None):
    """Filters already-normalized alarms. `keys` limits which filters run
    (live mode only applies the ones Camview itself can't do)."""
    keys = set(keys or ("alarmType", "lastActionType", "alarmState", "shiftLabel",
                        "priority", "hall", "search"))
    result = alarms

    if "alarmType" in keys and args.get("alarmType") not in (None, "", []):
        wanted = args["alarmType"] if isinstance(args["alarmType"], list) else [args["alarmType"]]
        wanted = {str(w) for w in wanted}
        result = [a for a in result if str(a["alarmType"]) in wanted]
    for key in ("lastActionType", "alarmState"):
        if key in keys and args.get(key) not in (None, ""):
            result = [a for a in result if str(a[key]) == str(args[key])]
    for key in ("shiftLabel", "priority", "hall"):
        if key in keys and args.get(key):
            result = [a for a in result if a[key] == args[key]]
    if "search" in keys and args.get("search"):
        s = str(args["search"]).lower()
        result = [
            a for a in result
            if any(s in str(a[f]).lower() for f in ("alarmId", "cameraId", "cameraName", "alarmTypeName", "hall"))
        ]
    return result


def hall_rollup(alarms):
    by_hall = defaultdict(list)
    for a in alarms:
        by_hall[a["hall"]].append(a)

    rollup = []
    for hall, hall_alarms in by_hall.items():
        pending = sum(1 for a in hall_alarms if a["lastActionType"] == 0)
        critical = sum(1 for a in hall_alarms if a["priority"] == "critical")
        cameras = sorted({a["cameraName"] for a in hall_alarms})

        if critical > 0 or pending >= 6:
            status = "at_risk"
        elif pending >= 2:
            status = "needs_review"
        else:
            status = "healthy"

        rollup.append({
            "hall": hall,
            "cameraCount": len(cameras),
            "cameras": cameras,
            "totalAlarms": len(hall_alarms),
            "pendingAlarms": pending,
            "criticalAlarms": critical,
            "status": status,
        })

    rollup.sort(key=lambda h: h["hall"])
    return rollup
