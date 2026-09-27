"""
mock_data.py — DEMO MODE data (never used in live mode).

* A complete nomenclature hierarchy for two demo projects
  (Project › TC › Centre › Building › Floor › Room › Camera).
* Alarms in exactly the shape of Camview's listAlarms response
  ({camera, alarm, imageUrls, serialNumber}) so demo and live data go
  through the same normalizer and the UI behaves identically.
* Alarm IDs are deterministic (seeded) so demo workflow records (reviews,
  publications) survive a restart; timestamps are relative to "now".
* Two cameras per project are deliberately NOT in the master data, so the
  Data Quality screen has something real to report.
"""

import random
import uuid
from datetime import datetime, timedelta, timezone

ALARM_TYPES = {
    1: ("Mobile Phone Detected", "A mobile phone was detected in the exam hall.", "high"),
    2: ("Impersonation Suspected", "Face does not match the registered candidate.", "critical"),
    3: ("Unauthorized Person in Hall", "A person without a valid role entered the hall.", "high"),
    4: ("Strongroom Zone Breach", "Movement detected in the strongroom outside allowed hours.", "critical"),
    5: ("Candidate Communication", "Candidates appear to be communicating.", "medium"),
    6: ("Unauthorized Entry/Exit", "Entry or exit through a restricted door.", "medium"),
    7: ("Prohibited Item Left Behind", "An item was left behind in a restricted area.", "low"),
    8: ("Camera View Obstructed", "The camera view is blocked or covered.", "medium"),
}
ALARM_TYPE_NAMES = {k: v[0] for k, v in ALARM_TYPES.items()}
UNKNOWN_ALARM_TYPE = 12      # deliberately NOT in the dictionary (shows up in Data Quality)
HIERARCHY_VERSION = 2

# lastActionType semantics (documented): 0=pending, 1=valid, 2=invalid, 3=exception
ALARM_STATES = {0: "Pending", 1: "Valid", 2: "Invalid", 3: "Exception"}

SHIFTS = ["Shift 1 Morning", "Shift 2 Afternoon"]
DEMO_VIDEO = "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4"

PROJECTS = [
    {"projectId": 7, "code": "PROJECT-07", "name": "State Recruitment Examination 2026",
     "tecs": [("TEC-03", "Lucknow Region"), ("TEC-04", "Kanpur Region")], "cam_base": 101},
    {"projectId": 12, "code": "PROJECT-12", "name": "University Entrance Test 2026",
     "tecs": [("TEC-11", "Delhi North"), ("TEC-12", "Noida")], "cam_base": 501},
    {"projectId": 15, "code": "PROJECT-15", "name": "State Recruitment Skill Test 2026",
     "tecs": [("TEC-07", "Prayagraj Region"), ("TEC-08", "Varanasi Region")], "cam_base": 301},
    {"projectId": 21, "code": "PROJECT-21", "name": "Nursing Entrance Examination 2026",
     "tecs": [("TEC-21", "Jaipur Region"), ("TEC-22", "Jodhpur Region")], "cam_base": 701},
]

# Demo clients and exams (Administration › Exams in live mode)
DEMO_EXAMS = [
    ("exam-sre-pre", "SRE26-PRE", "SRE 2026 — Prelims", "client-a", ["7"]),
    ("exam-sre-skill", "SRE26-SKL", "SRE 2026 — Skill Test", "client-a", ["15"]),
    ("exam-uet", "UET26", "UET 2026 — Entrance", "client-b", ["12"]),
    ("exam-nnc", "NNC26", "NNC 2026 — Nursing Entrance", "client-c", ["21"]),
]
CENTRE_NAMES = ["Govt. Senior Secondary School", "City Public School", "Model Inter College", "Kendriya Vidyalaya",
                "St. Mary's Convent", "DAV Public School", "Govt. Girls Inter College", "Army Public School"]


def hierarchy():
    """Nested master data (the same JSON format an admin would import)."""
    out = []
    centre_i = 0
    for p in PROJECTS:
        cam = p["cam_base"]
        tcs = []
        for t_i, (tec_code, tec_name) in enumerate(p["tecs"]):        # "tecs" are regions: they name the TCs
            for tc_n in range(2):
                tc_code = f"TC-{p['projectId']:02d}{t_i}{tc_n + 1}"
                centre_code = f"CTR-{p['projectId']:02d}{t_i}{tc_n + 1}"
                centre_name = f"{CENTRE_NAMES[centre_i % len(CENTRE_NAMES)]}, {tec_name}"
                centre_i += 1
                floors = []
                for floor in (1, 2):
                    rooms = []
                    for r in (1, 2):
                        room = f"{floor}0{r}"
                        cams = []
                        for c in range(2):
                            cams.append({"cameraId": cam, "code": f"CAM-{cam}",
                                         "name": f"Room {room} · {'Front' if c == 0 else 'Rear'}"})
                            cam += 1
                        rooms.append({"code": room, "cameras": cams})
                    floors.append({"code": str(floor), "rooms": rooms})
                buildings = [{"code": "A" if tc_n == 0 else "B", "floors": floors}]
                if t_i == 0 and tc_n == 0:
                    # data-quality case: annex cameras mapped to a building but with no floor/room
                    buildings.append({"code": "ANNEX", "cameras": [
                        {"cameraId": p["cam_base"] + 40 + k, "code": f"CAM-{p['cam_base'] + 40 + k}",
                         "name": f"Annex corridor {k + 1}"} for k in range(2)]})
                tcs.append({"code": tc_code, "name": f"Test Centre {tc_code[-3:]} · {tec_name}",
                            "centres": [{"code": centre_code, "name": centre_name, "buildings": buildings}]})
        out.append({"projectId": p["projectId"], "code": p["code"], "name": p["name"], "tcs": tcs})
    return {"projects": out}


def _camera_rooms():
    """cameraId -> 'CTR-xxxx Room 201' label for the evidence overlay."""
    out = {}
    for p in hierarchy()["projects"]:
        for tc in p["tcs"]:
            if True:
                for centre in tc["centres"]:
                    for b in centre["buildings"]:
                        for cam in b.get("cameras", []):
                            out[cam["cameraId"]] = f"{centre['code']} Bldg {b['code']}"
                        for fl in b.get("floors", []):
                            for room in fl["rooms"]:
                                for cam in room["cameras"]:
                                    out[cam["cameraId"]] = f"{centre['code']} Room {room['code']}"
    return out


def _cameras(project):
    base = project["cam_base"]
    mapped = list(range(base, base + 2 * 2 * 2 * 2 * 2))       # 4 TC x 2 floors x 2 rooms x 2 cams
    return mapped + [base + 98, base + 99]                     # two unmapped cameras


def _iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _shift_for(ts):
    return SHIFTS[0] if ts.astimezone().hour < 13 else SHIFTS[1]


def _alarm(rng, project, camera_id, ts, *, state=None, priority=None, times=None, alarm_type=None, alarm_id=None):
    times = times or rng.choices([1, 1, 1, 2, 3, 4, 6], weights=[40, 20, 10, 12, 10, 5, 3])[0]
    first = ts
    last = ts + timedelta(minutes=rng.uniform(0, min(20, times * 3)) if times > 1 else 0)
    priority = priority or rng.choices([1, 2, 3, 4], weights=[8, 22, 40, 30])[0]
    if state is None:
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        # realistic operations: recent alarms are still pending, older ones have been worked
        w = [60, 25, 10, 5] if age_h < 2 else [12, 52, 26, 10] if age_h < 12 else [0, 58, 32, 10]
        state = rng.choices([0, 1, 2, 3], weights=w)[0]
    aid = alarm_id or f"ALM-{rng.getrandbits(32):08X}"
    n_img = rng.choices([0, 1, 2, 3], weights=[10, 40, 35, 15])[0]
    suppressed = rng.random() < 0.06
    atype = alarm_type or rng.choices(list(ALARM_TYPES), weights=[22, 8, 12, 5, 18, 14, 11, 10])[0]
    ticket = rng.randint(4000, 9999) if rng.random() < 0.18 else None
    if n_img:
        rng.random()                          # former video draw — keeps the demo sequence deterministic
    # synthetic CCTV-style frames (demo_evidence.py) — watermarked DEMO, never real recordings
    room = CAMERA_ROOMS.get(camera_id, "")
    images = [f"/demo-evidence/{aid}-{i}.svg?cam=CAM-{camera_id}&room={room.replace(' ', '+')}"
              f"&ts={_iso(first)}&type={atype}&f={i}" for i in range(n_img)]
    return {
        "camera": {"cameraId": camera_id},
        "alarm": {
            "alarmId": aid, "cameraId": camera_id, "alarmType": atype,
            "projectId": project["projectId"], "alarmState": 1, "lastActionType": state, "priority": priority,
            "firstInstance": _iso(first), "lastInstance": _iso(last), "totalTimesReported": times,
            "ticketId": ticket,
            "imageUrl": images[0] if images else None,
            "videoUrl": None,                 # no fake video in demo mode
            "shiftLabel": _shift_for(first), "suppressed": suppressed, "suppressionTrigger": suppressed,
        },
        "imageUrls": images,
        "serialNumber": None,
    }


CAMERA_ROOMS = _camera_rooms()


def generate_project_alarms(project, now=None, days=14, count=360):
    """Deterministic history for one demo project."""
    now = now or datetime.now(timezone.utc)
    rng = random.Random(20260926 + project["projectId"])
    cams = _cameras(project)
    items = []
    for i in range(count):
        # denser recent activity: ~40% in the last 24h
        hours_back = rng.uniform(0, 24) if i % 5 < 2 else rng.uniform(24, days * 24)
        ts = now - timedelta(hours=hours_back)
        items.append(_alarm(rng, project, rng.choice(cams), ts))
    # a guaranteed story in the last hour: critical pending + a burst on one camera
    story_cam = cams[5]
    items.append(_alarm(rng, project, story_cam, now - timedelta(minutes=12), state=0, priority=1, times=5,
                        alarm_type=2))
    for m in (9, 7, 4):
        items.append(_alarm(rng, project, story_cam, now - timedelta(minutes=m), state=0, priority=2, times=1))
    items.append(_alarm(rng, project, cams[9], now - timedelta(hours=3), state=0, priority=1, times=3, alarm_type=4))
    # data-quality cases (separate generator so the IDs above never change):
    dq = random.Random(99 + project["projectId"])
    base = project["cam_base"]
    for k in range(4):                                   # cameras with no room in master data
        items.append(_alarm(dq, project, base + 40 + (k % 2), now - timedelta(hours=dq.uniform(1, 30))))
    for k in range(3):                                   # alarm type not in the dictionary
        items.append(_alarm(dq, project, dq.choice(cams[:32]), now - timedelta(hours=dq.uniform(1, 30)),
                            alarm_type=UNKNOWN_ALARM_TYPE))
    items.sort(key=lambda it: it["alarm"]["lastInstance"], reverse=True)
    for n, it in enumerate(items, start=1):
        it["serialNumber"] = n
    return items


def generate_live_alarm(project_id):
    """A brand-new pending alarm happening right now (demo live feed)."""
    project = next((p for p in PROJECTS if p["projectId"] == int(project_id)), PROJECTS[0])
    rng = random.Random()
    item = _alarm(rng, project, rng.choice(_cameras(project)), datetime.now(timezone.utc), state=0, times=1,
                  alarm_id=f"ALM-{uuid.uuid4().hex[:8].upper()}")
    return item


def paginate(items, page=1, size=20):
    """1-based paging (the backend's public contract; Camview itself is 0-based)."""
    page = max(1, int(page))
    size = max(1, min(100, int(size)))
    start = (page - 1) * size
    total = len(items)
    total_pages = max(1, (total + size - 1) // size)
    return {"cameraAlarmsDetails": items[start:start + size], "page": page, "size": size,
            "totalElements": total, "totalPages": total_pages, "hasNext": page < total_pages, "lastKey": None}
