"""
alert_service.py — THE way the backend retrieves one alert and describes it.

Every route that lists or opens an alert goes through here, so a list item and the detail it opens are built
by the same code from the same record:

    retrieval   live feed  →  canonical alert store (alert_store)  →  review/ticket snapshot (legacy rows)
    enrichment  datasource.enrich: context (nomenclature), exam, client, camera + recording health, review,
                ticket, client visibility — one function for lists and details
    contract    dto(a): the stable AlertDTO. Every field is always present; a value that is not known says why
                ("not_mapped", "not_available", "unknown") instead of silently disappearing.

"Outside the current data window" no longer exists: an alert that was ever received is in the store, so it
opens after a refresh, a page shift, a restart or while Camview is down (source = "stored").
"""

import logging
import time
from datetime import datetime

import alert_store
import config
import datasource
import db
import health as health_mod
import obs
import rbac
import workflow

log = logging.getLogger("camview.alerts")

CONTRACT_VERSION = 1
NOT_MAPPED, NOT_AVAILABLE, UNKNOWN = "not_mapped", "not_available", "unknown"
DECISIONS = {"marked_valid": "valid", "marked_invalid": "invalid", "marked_exception": "exception"}
CAMVIEW_STATUS = {0: "pending", 1: "valid", 2: "invalid", 3: "exception"}


def _base_record(alarm_id, project_id=None, candidates=None):
    """(record, source): the freshest normalized record of an alert, not yet enriched or scope-checked."""
    pids = [str(project_id)] if project_id else list(candidates or [])
    for pid in pids:
        feed = datasource.refresh(pid)
        for base in feed.items:
            if base["alarmId"] == alarm_id:
                return dict(base), ("stored" if base.get("_stored") else "live" if config.MODE == "live" else "demo")
    rec = alert_store.get(alarm_id)
    if rec and (not project_id or str(rec.get("projectId")) == str(project_id)):
        return rec, "stored"
    snap = None
    for sql in ("SELECT snapshot FROM publications WHERE alarm_id=? AND snapshot IS NOT NULL",
                "SELECT snapshot FROM assignments WHERE alarm_id=? AND snapshot IS NOT NULL",
                "SELECT snapshot FROM ops_review WHERE alarm_id=? AND snapshot IS NOT NULL",
                "SELECT snapshot FROM tickets WHERE alarm_id=? AND snapshot IS NOT NULL"):
        r = db.one(sql, (alarm_id,))
        if r:
            snap = db.jload(r["snapshot"], None)
            if snap and snap.get("projectId") is not None:
                break
    if not snap or snap.get("projectId") is None:
        return None, None
    if project_id and str(snap.get("projectId")) != str(project_id):
        return None, None
    base = {k: snap.get(k) for k in snap if k not in ("context", "exam", "clients")}
    base.setdefault("alarmId", alarm_id)
    base.setdefault("imageUrls", [])
    return base, "snapshot"


def get(user, alarm_id, project_id=None):
    """One alert as `user` may see it (enriched + AlertDTO), or None when it does not exist OR is out of the
    user's scope — callers answer both the same way, so hidden records are never revealed."""
    try:
        cands = [p for p in datasource.allowed_projects(user) if rbac.project_allowed(user, p)]
        if project_id and not rbac.project_allowed(user, project_id):
            return None
        base, source = _base_record(alarm_id, project_id, cands)
        if base is None or not rbac.project_allowed(user, base.get("projectId")):
            return None
        feed = datasource._feeds.get(str(base.get("projectId")))
        a = datasource.enrich([base], feed.new_ids if feed else None)[0]
        if not rbac.alarm_in_scope(user, a):
            return None
        a["source"] = source
        a.pop("_stored", None)
        a["alert"] = dto(a, show_evidence="evidence.view" in user["permissions"])
        return a
    except Exception:
        obs.count("alert_detail.failure")
        obs.event("alert.detail", "error", logging.ERROR, alarm_id=alarm_id, project_id=project_id, user=user)
        raise


def resolve_system(alarm_id, project_id=None):
    """The enriched alert for background work (delivery jobs) — no user scope applies."""
    base, source = _base_record(alarm_id, project_id, [str(project_id)] if project_id else datasource.all_project_ids())
    if base is None:
        return None
    a = datasource.enrich([base])[0]
    a["source"] = source
    a.pop("_stored", None)
    return a


def media_record(alarm_id, project_id=None):
    """The normalized record whose media links are the freshest we have (live feed, then store, then snapshot)."""
    base, source = _base_record(alarm_id, project_id, [str(project_id)] if project_id else [])
    return base, source


def evidence_items(a, show=True):
    """Evidence as stable, same-origin links (/api/media/…): the signed Camview link behind them is resolved
    when the browser asks, so an expired link in a stored record never reaches the screen."""
    if not show:
        return []
    aid = a["alarmId"]
    pid = a.get("projectId")
    q = f"?projectId={pid}" if pid not in (None, "") else ""
    ev = [{"kind": "video", "index": 0, "url": f"/api/media/{aid}/video/0{q}"}] if a.get("videoUrl") else []
    return ev + [{"kind": "image", "index": i, "url": f"/api/media/{aid}/image/{i}{q}"}
                 for i, _ in enumerate(a.get("imageUrls") or [])]


def media_links(a, show=True):
    """{imageUrls, videoUrl, imageUrl} as resolvable same-origin links — for RESPONSES only (never stored)."""
    ev = evidence_items(a, show)
    images = [e["url"] for e in ev if e["kind"] == "image"]
    video = next((e["url"] for e in ev if e["kind"] == "video"), None)
    return {"imageUrls": images, "videoUrl": video, "imageUrl": images[0] if images else None}


def public(a, user):
    """A copy of an enriched alert for a response: signed Camview links replaced by /api/media links."""
    out = dict(a)
    out.update(media_links(a, "evidence.view" in user["permissions"]))
    out.pop("metadataUrl", None)
    out["hasMetadata"] = bool(a.get("metadataUrl"))
    return out


def _node(ctx, level):
    n = ctx.get(level)
    return None if not n or n.get("unmapped") else n


# ---------------------------------------------------------------------------
# Automatic states derived from facts: data quality, evidence completeness, the client readiness gate and the
# event story. Informational for the operator (never a decision), enforced facts for delivery.
# ---------------------------------------------------------------------------

STORY_WINDOW_MINUTES = 60      # related = same camera within +/- this window of the alert
GROUP_GAP_MINUTES = 10         # same camera + same type, chained by gaps <= this, is ONE event group
_never_share = {"key": None, "at": 0.0, "ids": set()}
_pol = {"key": None, "at": 0.0, "value": None}


def _never_share_types():
    """Alarm types an administrator configured never to reach a client (re-read at most every 5 s)."""
    now = time.time()
    if _never_share["key"] != db.DB_PATH or now - _never_share["at"] > 5:
        try:
            ids = {str(r["id"]) for r in db.rows("SELECT id FROM alarm_types WHERE client_share_policy='never'")}
        except Exception:                                   # no table yet (import time): nothing is blocked
            ids = set()
        _never_share.update(key=db.DB_PATH, at=now, ids=ids)
    return _never_share["ids"]


def _policy():
    now = time.time()
    if _pol["key"] != db.DB_PATH or now - _pol["at"] > 2 or _pol["value"] is None:
        try:
            _pol.update(key=db.DB_PATH, at=now, value=workflow.policy())
        except Exception:
            return dict(workflow.POLICY_DEFAULTS)
    return _pol["value"]


def quality(a):
    """Data-quality state of one alert - complete | incomplete | unknown - and the checks behind it. Required
    checks are the mapping and evidence the automation needs; health checks are informational (a source that
    does not report recording makes the fact UNKNOWN, not the alert incomplete)."""
    ctx = a.get("context") or {}
    levels = (a.get("contextCompleteness") or {}).get("expected") or []
    project = _node(ctx, "project")
    pid = None if a.get("projectId") in (None, "") else str(a.get("projectId"))
    clients = a.get("clients") or []
    t = a.get("ticket") or {}
    h = a.get("health") or {}
    checks = [
        {"key": "project", "label": "Project code", "ok": bool(project and project.get("code") not in (None, "", pid))},
        {"key": "exam", "label": "Exam mapping", "ok": bool(a.get("exam"))},
        {"key": "client", "label": "Client mapping", "ok": len(clients) == 1 or (len(clients) > 1 and bool(t.get("clientId")))},
    ]
    if "tc" in levels:
        checks.append({"key": "tc", "label": "TC", "ok": bool(_node(ctx, "tc"))})
    checks += [
        {"key": "centre", "label": "Centre", "ok": bool(_node(ctx, "centre") or a.get("centreCode"))},
        {"key": "camera", "label": "Camera", "ok": a.get("cameraId") not in (None, "")},
        {"key": "evidence", "label": "Evidence attached", "ok": bool(a.get("imageUrls") or a.get("videoUrl"))},
        {"key": "cameraHealth", "label": "Camera status available", "ok": bool(h.get("available")), "required": False},
        {"key": "recordingHealth", "label": "Recording status available",
         "ok": (h.get("recording") or {}).get("state", "unknown") != "unknown", "required": False},
    ]
    if pid is None or a.get("cameraId") in (None, ""):
        state = "unknown"
    else:
        state = "complete" if all(c["ok"] for c in checks if c.get("required", True)) else "incomplete"
    return {"state": state, "checks": checks,
            "missing": [c["label"] for c in checks if not c["ok"] and c.get("required", True)],
            "unavailable": [c["label"] for c in checks if not c["ok"] and not c.get("required", True)]}


def evidence_completeness(a):
    """Evidence complete / incomplete: snapshot, video, timestamp, camera. Informational only - it never
    decides VALID / INVALID."""
    checks = [
        {"key": "snapshot", "label": "Snapshot", "ok": bool(a.get("imageUrls"))},
        {"key": "video", "label": "Video", "ok": bool(a.get("videoUrl"))},
        {"key": "timestamp", "label": "Timestamp", "ok": bool(a.get("firstInstance") or a.get("lastInstance"))},
        {"key": "camera", "label": "Camera", "ok": a.get("cameraId") not in (None, "")},
    ]
    return {"state": "complete" if all(c["ok"] for c in checks) else "incomplete", "checks": checks}


def readiness(a):
    """The client readiness gate, evaluated the way delivery is: every required check must pass before a
    validated alert can reach a client. States: visible (delivered and still operator-VALID) - ready -
    not_ready (with the reasons). Nothing here asks the operator to fix mapping - that is administration."""
    review = a.get("review") or {}
    decision = DECISIONS.get(review.get("status"), "pending")
    clients = a.get("clients") or []
    t = a.get("ticket") or {}
    chosen = a.get("client") or (next((c for c in clients if c["id"] == t.get("clientId")), None) if t.get("clientId") else None)
    shared = any(p.get("status") == "shared" for p in a.get("publications") or [])
    pol = _policy()
    has_media = bool(a.get("imageUrls") or a.get("videoUrl"))
    client_ok = bool(chosen)
    client_label = ("Client mapped" if client_ok else
                    "This exam maps to several clients - one is chosen on VALID" if len(clients) > 1 else
                    "No client mapped to this exam / project")
    never = str(a.get("alarmType")) in _never_share_types()
    active = bool(chosen) and chosen.get("status", "active") == "active"
    checks = [
        {"key": "decision", "label": "Operator decision VALID" if decision == "valid" else
         f"Operator decision is {decision.upper()}", "ok": decision == "valid"},
        {"key": "project", "label": "Project mapped", "ok": bool(_node(a.get("context") or {}, "project")), "required": False},
        {"key": "exam", "label": "Exam mapped", "ok": bool(a.get("exam")), "required": False},
        {"key": "client", "label": client_label, "ok": client_ok},
        {"key": "authorized", "label": "Client authorized" if not chosen or active
         else f"Client {chosen.get('name')} is inactive", "ok": active},
        {"key": "policy", "label": "This alert type is configured never to be sent to clients" if never
         else "Alert type may be sent to clients", "ok": not never},
        {"key": "kind", "label": "Camera status events are never delivered" if a.get("eventKind") == "camera_status"
         else "Alert (not a camera status event)", "ok": a.get("eventKind") != "camera_status"},
        {"key": "withdrawn", "label": "Ticket cancelled / withdrawn" if t.get("status") == "cancelled" else "Not withdrawn",
         "ok": t.get("status") != "cancelled"},
        {"key": "evidence", "label": "Evidence policy satisfied" if has_media or pol.get("autoEvidence") == "none"
         else "No evidence attached (sent without evidence)", "ok": has_media or pol.get("autoEvidence") == "none",
         "required": False},
        {"key": "clientSafe", "label": "Client-safe data available", "ok": True},
    ]
    ok = all(c["ok"] for c in checks if c.get("required", True))
    state = "visible" if shared and decision == "valid" else "ready" if ok else "not_ready"
    return {"state": state, "checks": checks,
            "reasons": [c["label"] for c in checks if not c["ok"] and c.get("required", True)]}


def _ts(x):
    try:
        return datetime.fromisoformat(str(x.get("lastInstance") or x.get("firstInstance")).replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None


def story(a, items):
    """The factual event story around one alert: the other alerts of the same camera within
    +/- STORY_WINDOW_MINUTES, the event group (same camera + same type, chained by gaps <= GROUP_GAP_MINUTES) and
    plain "why this alert" facts. Grouping is idempotent - the group id is the earliest alert of the chain, so the
    same data always yields the same group - and it is a VIEW: raw alerts are never merged or deleted."""
    t0 = _ts(a)
    pid, cam = str(a.get("projectId")), str(a.get("cameraId"))
    same_cam = []
    for b in items:
        if (b.get("eventKind") == "camera_status" or str(b.get("projectId")) != pid or str(b.get("cameraId")) != cam
                or b["alarmId"] == a["alarmId"]):
            continue
        tb = _ts(b)
        if t0 is not None and tb is not None and abs((tb - t0).total_seconds()) <= STORY_WINDOW_MINUTES * 60:
            same_cam.append((tb, b))
    if t0 is not None:
        same_cam.append((t0, a))
    same_cam.sort(key=lambda x: (x[0], x[1]["alarmId"]))
    typed = [(t, b) for t, b in same_cam if b.get("alarmType") == a.get("alarmType")]
    group = [a]
    if typed:
        i = next(k for k, (_, b) in enumerate(typed) if b["alarmId"] == a["alarmId"])
        lo, hi = i, i
        while lo > 0 and (typed[lo][0] - typed[lo - 1][0]).total_seconds() <= GROUP_GAP_MINUTES * 60:
            lo -= 1
        while hi < len(typed) - 1 and (typed[hi + 1][0] - typed[hi][0]).total_seconds() <= GROUP_GAP_MINUTES * 60:
            hi += 1
        group = [b for _, b in typed[lo:hi + 1]]
    in_group = {b["alarmId"] for b in group}
    first, last = _ts(group[0]), _ts(group[-1])
    span = round((last - first).total_seconds() / 60) if first and last else 0
    related = len(same_cam) - 1
    timeline = [{"alarmId": b["alarmId"], "projectId": b.get("projectId"), "at": b.get("lastInstance") or b.get("firstInstance"),
                 "alarmTypeName": b.get("alarmTypeName"), "decision": b.get("decision"),
                 "decisionSource": b.get("decisionSource"), "video": bool(b.get("videoUrl")),
                 "images": len(b.get("imageUrls") or []), "current": b["alarmId"] == a["alarmId"],
                 "inGroup": b["alarmId"] in in_group} for _, b in same_cam[:60]]
    why = []
    occ = a.get("totalTimesReported") or 1
    if occ > 1:
        why.append(f"Reported {occ}x by Camview")
    if len(group) > 1:
        why.append(f"{len(group)} {a.get('alarmTypeName') or 'alerts'} on the same camera within {max(span, 1)} min")
    others = related - (len(group) - 1)
    if others > 0:
        why.append(f"{others} other alert{'s' if others != 1 else ''} on this camera within {STORY_WINDOW_MINUTES} min")
    why.append("Video available" if a.get("videoUrl") else "No video attached")
    n_img = len(a.get("imageUrls") or [])
    why.append(f"{n_img} supporting image{'s' if n_img != 1 else ''}" if n_img else "No image attached")
    if a.get("priorityConfirmed") and a.get("priority"):
        why.append(f"Priority {a['priority']} from the alert-type dictionary")
    cond = set((a.get("health") or {}).get("conditions") or [])
    if "CAMERA_OFFLINE" in cond:
        why.append("Camera reported OFFLINE by the health source")
    if "FRAME_SYNC_FAILED" in cond:
        why.append("Camview could not sync frames from this camera")
    if "RECORDING_NOT_DETECTED" in cond:
        why.append("Recording NOT DETECTED by the health source")
    return {"kind": "repeated" if len(group) > 1 else "single",
            "groupId": f"{pid}:{cam}:{a.get('alarmType')}:{group[0]['alarmId']}", "groupCount": len(group),
            "groupFrom": group[0].get("lastInstance") or group[0].get("firstInstance"),
            "groupTo": group[-1].get("lastInstance") or group[-1].get("firstInstance"), "spanMinutes": span,
            "relatedCount": related, "windowMinutes": STORY_WINDOW_MINUTES, "gapMinutes": GROUP_GAP_MINUTES,
            "timeline": timeline, "why": why}


def dto(a, show_evidence=True):
    """The stable AlertDTO (contract version 1). Every key is always present."""
    ctx = a.get("context") or {}
    project, tc, centre = _node(ctx, "project"), _node(ctx, "tc"), _node(ctx, "centre")
    building, floor, room, camera = _node(ctx, "building"), _node(ctx, "floor"), _node(ctx, "room"), _node(ctx, "camera")
    exam = a.get("exam")
    clients = a.get("clients") or []
    client = a.get("client")
    h = a.get("health") or health_mod.unavailable()
    review = a.get("review") or {}
    t = a.get("ticket")
    pubs = a.get("publications") or []
    decision_op = DECISIONS.get(review.get("status"), "pending")
    shared = [p for p in pubs if p.get("status") == "shared"]
    visible = bool(shared) and decision_op == "valid"
    ev = evidence_items(a, show_evidence)
    code = project.get("code") if project else None
    pid = None if a.get("projectId") in (None, "") else str(a.get("projectId"))
    has_media = bool(a.get("imageUrls") or a.get("videoUrl"))
    return {
        "contractVersion": CONTRACT_VERSION,
        "source": a.get("source") or ("stored" if a.get("_stored") else "live" if config.MODE == "live" else "demo"),
        # identity
        "alarmId": a["alarmId"], "alarmIdDerived": bool(a.get("alarmIdDerived")),
        "projectId": pid,
        "projectCode": code if code and code != pid else None,
        "projectName": (project or {}).get("name") if project and project.get("name") not in (None, "", pid) else None,
        "projectMapping": "mapped" if code and code != pid else NOT_MAPPED,
        # exam / client (derived from the project mapping — never chosen by the operator)
        "examId": exam["id"] if exam else None, "examCode": exam.get("code") if exam else None,
        "examName": exam.get("name") if exam else None, "examMapping": "mapped" if exam else NOT_MAPPED,
        # exam phase at the time of the alert, from the exam's own date window (unknown without dates)
        "examPhase": (exam or {}).get("phase") or UNKNOWN,
        "examStartDate": (exam or {}).get("startDate"), "examEndDate": (exam or {}).get("endDate"),
        "clientId": client["id"] if client else None, "clientName": client["name"] if client else None,
        "clientMapping": "mapped" if client else ("multiple" if len(clients) > 1 else NOT_MAPPED),
        "clientOptions": [{"id": c["id"], "name": c["name"]} for c in clients],
        # nomenclature (Camview has no TEC level: always not_available unless master data adds one later)
        "tecCode": None, "tecName": None, "tecMapping": NOT_AVAILABLE,
        "tcCode": (tc or {}).get("code") or a.get("tcCode"), "tcName": (tc or {}).get("name"),
        "centreCode": (centre or {}).get("code") or a.get("centreCode"),
        "centreName": a.get("centreName") or (centre or {}).get("name"),
        "building": (building or {}).get("code"), "floor": (floor or {}).get("code"),
        "room": (room or {}).get("code") or a.get("cameraSubLocation"),
        "cameraId": None if a.get("cameraId") in (None, "") else str(a.get("cameraId")),
        "cameraCode": a.get("cameraCode") or (camera or {}).get("code") or a.get("cameraNumber"),
        "cameraName": a.get("cameraName"),
        "locationLabel": a.get("locationLabel"),
        "contextMapping": "mapped" if ctx.get("mapped") else ("camview" if a.get("centreCode") else NOT_MAPPED),
        # event
        "eventKind": a.get("eventKind") or "alert",
        "alarmType": a.get("alarmType"), "alarmTypeName": a.get("alarmTypeName"),
        "alarmTypeKnown": a.get("alarmTypeKnown") if a.get("alarmTypeKnown") is not None else None,
        "priority": a.get("priority") or UNKNOWN, "prioritySource": a.get("prioritySource"),
        "firstInstance": a.get("firstInstance"), "lastInstance": a.get("lastInstance"),
        "occurrences": a.get("totalTimesReported") or 1,
        # health: three separate facts, never inferred from one another or from the alarm's name
        "cameraStatus": (h.get("camera") or {}).get("state") or UNKNOWN,
        "recordingStatus": (h.get("recording") or {}).get("state") or UNKNOWN,
        "streamStatus": (h.get("stream") or {}).get("state") or UNKNOWN,
        "lastHeartbeat": (h.get("camera") or {}).get("lastHeartbeatAt"),
        "lastRecording": (h.get("recording") or {}).get("lastRecordingAt"),
        "healthAvailable": bool(h.get("available")), "healthSource": h.get("source") or "none",
        "healthConditions": list(h.get("conditions") or []),
        # evidence
        "evidence": {
            "status": "available" if ev else ("restricted" if has_media else NOT_AVAILABLE),
            "video": next((e for e in ev if e["kind"] == "video"), None),
            "images": [e for e in ev if e["kind"] == "image"],
            "clips": [e for e in ev if e["kind"] == "video"],
            "hasMetadata": bool(a.get("metadataUrl")),
        },
        # Camview's own status (read-only here) and the operations decision are two separate facts
        "camviewAlarmState": a.get("alarmState"),
        "camviewLastAction": a.get("lastActionType"),
        "camviewStatus": CAMVIEW_STATUS.get(a.get("lastActionType"), UNKNOWN),
        "operatorDecision": decision_op,
        "validatedBy": review.get("by") if decision_op != "pending" else None,
        "validatedAt": review.get("at") if decision_op != "pending" else None,
        "revision": review.get("revision") or 0,
        # the ACKNOWLEDGE step: who picked the alert up and when (kept once it is decided); new → acknowledged → decided
        "acknowledgedBy": review.get("acknowledgedBy"), "acknowledgedAt": review.get("acknowledgedAt"),
        "reviewState": "decided" if decision_op != "pending" else ("acknowledged" if review.get("acknowledgedAt") else "new"),
        "decision": a.get("decision"), "decisionSource": a.get("decisionSource"),
        # client visibility: ONLY an operator VALID with an active delivery makes an alert visible to a client
        "clientVisibility": "visible" if visible else "internal",
        "clientSharedAt": max((p.get("sharedAt") or "" for p in shared), default=None) or None,
        "visibleToClientIds": [p["clientId"] for p in shared] if visible else [],
        # ticket
        "ticketId": t["id"] if t else None, "ticketRef": t["ref"] if t else None,
        "ticketStatus": t["status"] if t else None, "deliveryStatus": t["deliveryStatus"] if t else None,
        # automatic states (informational for the operator; the readiness gate is what delivery enforces)
        "quality": quality(a),
        "evidenceCompleteness": evidence_completeness(a),
        "clientReadiness": readiness(a),
    }


CONTRACT_KEYS = tuple(dto({"alarmId": "X"}).keys())
