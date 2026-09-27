"""
workflow.py — review workflow, client visibility and the client data firewall.

Four concepts are kept strictly apart (spec: "never merge them"):

  1. ALARM STATE       Camview's own fields — alarmState / lastActionType
                       (0 Pending, 1 Valid, 2 Invalid, 3 Exception). Read-only.
  2. OPS VALIDATION    what an operator decided in Command Center (ops_review).
  3. WORKFLOW STATE    derived application state: NEW, UNDER_REVIEW, INVESTIGATING,
                       READY_FOR_CLIENT, READY_FOR_APPROVAL, APPROVED, SHARED,
                       CLIENT_ACKNOWLEDGED, WITHDRAWN, CLOSED.
  4. CLIENT VISIBILITY per client (publications): INTERNAL (no record),
                       READY_FOR_REVIEW, APPROVED, SHARED, WITHDRAWN, ARCHIVED.

A VALID alarm is NOT client-visible until someone with `alarm.publish`
explicitly shares it (after approval, when two-step approval is on).
"""

import os
import re

import db
from camview_client import ApiError

VISIBILITY_ORDER = ["internal", "ready_for_review", "approved", "shared", "withdrawn", "archived"]
VISIBILITY_LABELS = {"internal": "Internal", "ready_for_review": "Ready for review", "approved": "Approved for client",
                     "shared": "Shared with client", "withdrawn": "Withdrawn", "archived": "Archived"}
WORKFLOW_LABELS = {
    "NEW": "New", "UNDER_REVIEW": "Under review", "INVESTIGATING": "Investigating",
    "READY_FOR_CLIENT": "Ready for client review", "READY_FOR_APPROVAL": "Awaiting approval",
    "APPROVED": "Approved — ready to publish", "SHARED": "Shared with client",
    "CLIENT_ACKNOWLEDGED": "Client acknowledged", "WITHDRAWN": "Withdrawn", "CLOSED": "Closed (invalid)",
}

POLICY_DEFAULTS = {
    # automated delivery (the normal path: VALID → ticket → client)
    # arrival = every detection alert is delivered the moment Camview sends it (no VALID needed; INVALID/EXCEPTION
    # withdraws it) · valid = only after Camview or an operator marks it VALID
    "deliveryTrigger": (os.environ.get("CAMVIEW_DELIVERY_TRIGGER", "valid").strip().lower() or "valid"),
    "autoExams": True,               # client + exam created from the project code (MPESB/G2SG4-CRT-2026/…)
    "deliveryMode": "automatic",     # automatic = visible to the client on VALID; controlled = one-click Send
    "autoShareValid": True,          # alerts the Camview API reports as VALID are sent to the client automatically
    # Optional stricter rule: clients see ONLY alerts an operator marked VALID in this dashboard. Camview's own
    # status, arrival and automatic delivery never make an alert client-visible while it is on (client firewall).
    # Off by default: every VALID alert (marked in Camview or here) goes to the client on its own.
    "clientsSeeOperatorValidOnly": False,
    "autoShareHours": None,          # optional age limit in hours; None = every valid alert in the live data
    "manualReview": True,            # VALID / INVALID / EXCEPTION buttons (False = Camview's status decides alone)
    "autoNomenclature": True,        # build Project › Centre › Location › Camera from Camview camera data
    "autoEvidence": "all",           # evidence included in automatic delivery: all | first | none
    "requireRemarks": False,         # remarks are optional unless an administrator turns this on
    "sendFourEyes": False,           # optional: validator may not also send
    # sharing workflow (advanced / optional path)
    "requireApproval": True,         # two-step: approve, then publish
    "fourEyes": True,                # approver must not be the person who validated
    "validSource": "either",         # camview | ops | either — what counts as "valid" for sharing
    "requireEvidence": False,        # sharing requires at least one evidence item
    "requireContext": False,         # sharing requires a mapped camera (nomenclature)
    "clientContextLevels": ["project", "centre", "room", "camera"],   # default context shown to clients
    "clientShowTicket": False,
    # intelligence thresholds (all shown in every alert's explanation)
    "repeatThreshold": 3, "repeatWindowMinutes": 15,
    "cameraActivityThreshold": 5, "cameraActivityWindowMinutes": 60,
    "relatedMinCount": 3, "relatedWindowMinutes": 15,
    "spikeRatio": 2.0, "spikeMinCount": 5,
    "stormCount": 20, "stormWindowSeconds": 60,
    "suppressionThreshold": 3,
    "longPendingMinutes": None,      # no SLA assumed — disabled until configured
    "slaTargetMinutes": None, "slaWarnMinutes": None,
    "escalation": [],                # [{afterMinutes, roleId}] — none assumed
}


def policy():
    p = dict(POLICY_DEFAULTS)
    p.update(db.get_setting("policy", {}) or {})
    if p.get("clientsSeeOperatorValidOnly", True):
        # nothing reaches a client without an operator's VALID: no delivery on arrival, no Camview-VALID auto-share
        p.update(deliveryTrigger="valid", autoShareValid=False, manualReview=True)
    return p


def set_policy(updates, user):
    old = policy()
    new = dict(old)
    for k, v in (updates or {}).items():
        if k in POLICY_DEFAULTS:
            new[k] = v
    db.set_setting("policy", {k: new[k] for k in POLICY_DEFAULTS})
    changed = {k: new[k] for k in POLICY_DEFAULTS if old.get(k) != new.get(k)}
    if changed:
        db.audit("settings.policy", user, "settings", "policy", {k: old.get(k) for k in changed}, changed)
    return new


# ---------------------------------------------------------------------------
# Derived state
# ---------------------------------------------------------------------------

def publications_for(alarm_ids):
    out = {}
    for r in db._in_chunks("SELECT p.*, c.name AS client_name FROM publications p JOIN clients c ON c.id = p.client_id",
                           alarm_ids, "p.alarm_id"):
        out.setdefault(r["alarm_id"], []).append(pub_public(dict(r)))
    return out


def pub_public(r):
    return {
        "alarmId": r["alarm_id"], "clientId": r["client_id"], "clientName": r.get("client_name"),
        "status": r["status"], "statusLabel": VISIBILITY_LABELS.get(r["status"], r["status"]),
        "projectId": r.get("project_id"),
        "requestedBy": r.get("requested_by"), "requestedById": r.get("requested_by_id"),
        "requestedAt": r.get("requested_at"),
        "approvedBy": r.get("approved_by"), "approvedAt": r.get("approved_at"),
        "sharedBy": r.get("shared_by"), "sharedAt": r.get("shared_at"),
        "withdrawnBy": r.get("withdrawn_by"), "withdrawnAt": r.get("withdrawn_at"),
        "withdrawReason": r.get("withdraw_reason"),
        "clientSummary": r.get("client_summary"),
        "evidence": db.jload(r.get("evidence"), []) or [],
        "shareContext": db.jload(r.get("share_context"), []) or [],
        "viewedAt": r.get("viewed_at"), "acknowledgedBy": r.get("acknowledged_by"),
        "acknowledgedAt": r.get("acknowledged_at"), "ackComment": r.get("ack_comment"),
        "updatedAt": r.get("updated_at"),
    }


def visibility_summary(pubs):
    """Overall client visibility of an alarm across clients (most advanced wins)."""
    if not pubs:
        return {"state": "internal", "label": VISIBILITY_LABELS["internal"], "clients": []}
    rank = {"shared": 5, "approved": 4, "ready_for_review": 3, "withdrawn": 2, "archived": 1}
    top = max(pubs, key=lambda p: rank.get(p["status"], 0))
    return {"state": top["status"], "label": VISIBILITY_LABELS.get(top["status"], top["status"]),
            "acknowledged": any(p["acknowledgedAt"] for p in pubs if p["status"] == "shared"),
            "clients": [{"clientId": p["clientId"], "clientName": p["clientName"], "status": p["status"],
                         "sharedAt": p["sharedAt"], "acknowledgedAt": p["acknowledgedAt"]} for p in pubs]}


def derive_workflow(review_status, pubs, assigned):
    statuses = {p["status"] for p in pubs or []}
    if "shared" in statuses:
        return "CLIENT_ACKNOWLEDGED" if any(p["acknowledgedAt"] for p in pubs if p["status"] == "shared") else "SHARED"
    if "approved" in statuses:
        return "APPROVED"
    if "ready_for_review" in statuses:
        return "READY_FOR_APPROVAL"
    if statuses and statuses <= {"withdrawn", "archived"}:
        return "WITHDRAWN"
    if review_status == "marked_valid":
        return "READY_FOR_CLIENT"
    if review_status == "marked_invalid":
        return "CLOSED"
    if review_status == "marked_exception" or assigned:
        return "INVESTIGATING"
    if review_status == "acknowledged":
        return "UNDER_REVIEW"
    return "NEW"


def is_valid(alarm, pol=None):
    """Whether an alarm counts as VALID for sharing, per policy. An explicit
    ops decision of invalid/exception always blocks sharing."""
    pol = pol or policy()
    ops = (alarm.get("review") or {}).get("status", "unreviewed")
    if ops in ("marked_invalid", "marked_exception"):
        return False
    if pol["validSource"] == "ops":
        return ops == "marked_valid"
    if pol["validSource"] == "camview":
        return alarm.get("lastActionType") == 1
    return ops == "marked_valid" or alarm.get("lastActionType") == 1


# ---------------------------------------------------------------------------
# Eligibility engine (spec: clientShareEligible)
# ---------------------------------------------------------------------------

def client_share_eligible(alarm, user, client, action="request", pol=None):
    """Explains, check by check, whether `user` may take `action`
    (request | approve | publish) for `alarm` towards `client`."""
    pol = pol or policy()
    checks = []

    def check(cid, ok, text):
        checks.append({"id": cid, "ok": bool(ok), "text": text})

    perm = {"request": ("alarm.validate", "alarm.publish", "alarm.approve"),
            "approve": ("alarm.approve",), "publish": ("alarm.publish",)}[action]
    check("permission", any(p in user["permissions"] for p in perm),
          f"You have permission to {action} ({' or '.join(perm)})")
    check("valid", is_valid(alarm, pol),
          {"camview": "Camview reports the alarm as Valid",
           "ops": "An operator marked the alarm Valid in Command Center",
           "either": "Alarm is Valid (Camview or operator) and not marked invalid/exception by an operator"}
          [pol["validSource"]])
    if client is None:
        check("client", False, "A client is selected")
    else:
        check("client_active", client.get("status") == "active", f"Client {client['name']} is active")
        projects = {r["project_id"] for r in db.rows("SELECT project_id FROM client_projects WHERE client_id=?",
                                                     (client["id"],))}
        check("client_project", str(alarm.get("projectId")) in projects,
              f"Project {alarm.get('projectId')} is assigned to {client['name']}")
    from rbac import alarm_in_scope  # local import avoids a cycle
    check("scope", alarm_in_scope(user, alarm), "Alarm is inside your access scope")
    types = {t["id"]: t for t in db.rows("SELECT id, client_share_policy FROM alarm_types")}
    tpol = (types.get(alarm.get("alarmType")) or {}).get("client_share_policy", "allowed")
    check("type_policy", tpol != "never", "Alarm type may be shared with clients (Alarm Type Dictionary)")
    if pol["requireContext"]:
        check("context", (alarm.get("context") or {}).get("mapped"), "Camera is mapped in the nomenclature")
    if pol["requireEvidence"]:
        check("evidence", (alarm.get("evidence") or {}).get("count", 0) > 0, "At least one evidence item exists")

    pub = None
    if client is not None:
        pub = next((p for p in (alarm.get("publications") or []) if p["clientId"] == client["id"]), None)
    status = pub["status"] if pub else "internal"
    if action == "request":
        check("state", status in ("internal", "withdrawn"),
              "Not already requested/approved/shared for this client" if status not in ("internal", "withdrawn")
              else "Not yet shared with this client")
    elif action == "approve":
        check("state", status == "ready_for_review", "Sharing has been requested and awaits approval")
        if pol["fourEyes"]:
            validator = (alarm.get("review") or {}).get("validatedById")
            requester = pub.get("requestedById") if pub else None
            check("four_eyes", user["id"] not in {validator, requester} - {None},
                  "Four-eyes: approver is not the person who validated or requested it")
    elif action == "publish":
        need = ("approved",) if pol["requireApproval"] else ("approved", "ready_for_review", "internal", "withdrawn")
        check("state", status in need,
              "Supervisor approval is complete" if pol["requireApproval"] else "Alarm can be published directly")
        if pol["fourEyes"] and not pol["requireApproval"]:
            validator = (alarm.get("review") or {}).get("validatedById")
            check("four_eyes", user["id"] != validator, "Four-eyes: publisher is not the person who validated it")
    return {"eligible": all(c["ok"] for c in checks), "checks": checks, "currentStatus": status}


# ---------------------------------------------------------------------------
# Transitions (each one audited + notified)
# ---------------------------------------------------------------------------

def _client(client_id):
    c = db.one("SELECT id, name, status FROM clients WHERE id = ?", (client_id,))
    if not c:
        raise ApiError("bad_request", "Unknown client.", 400)
    return c


def _require_eligible(alarm, user, client, action):
    result = client_share_eligible(alarm, user, client, action)
    if not result["eligible"]:
        failed = "; ".join(c["text"] for c in result["checks"] if not c["ok"])
        raise ApiError("not_eligible", f"Not allowed: {failed}.", 409)
    return result


def _snapshot(alarm):
    keys = ("alarmId", "projectId", "cameraId", "cameraName", "alarmType", "alarmTypeName", "priority",
            "priorityLevel", "lastActionType", "lastActionLabel", "firstInstance", "lastInstance",
            "totalTimesReported", "ticketId", "shiftLabel", "suppressed", "imageUrls", "videoUrl", "context",
            "centreCode", "locationLabel")
    return {k: alarm.get(k) for k in keys}


def request_share(alarm, user, client_id):
    client = _client(client_id)
    _require_eligible(alarm, user, client, "request")
    now = db.now_iso()
    with db.connect() as conn:
        conn.execute("""
            INSERT INTO publications (alarm_id, client_id, status, project_id, requested_by, requested_by_id,
                                      requested_at, snapshot, updated_at)
            VALUES (?, ?, 'ready_for_review', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alarm_id, client_id) DO UPDATE SET status='ready_for_review', requested_by=excluded.requested_by,
              requested_by_id=excluded.requested_by_id,
              requested_at=excluded.requested_at, snapshot=excluded.snapshot, updated_at=excluded.updated_at,
              approved_by=NULL, approved_by_id=NULL, approved_at=NULL, shared_by=NULL, shared_by_id=NULL, shared_at=NULL,
              withdrawn_by=NULL, withdrawn_at=NULL, withdraw_reason=NULL
        """, (alarm["alarmId"], client["id"], str(alarm.get("projectId")), user["name"], user["id"], now,
              db.jdump(_snapshot(alarm)), now))
    db.audit("share.request", user, "alarm", alarm["alarmId"], "internal", "ready_for_review",
             alarm.get("projectId"), client["id"])
    import notify
    notify.to_permission("alarm.approve", "approval", f"Approval requested: {alarm['alarmId']}",
                         f"{user['name']} asked to share {alarm.get('alarmTypeName')} with {client['name']}.",
                         f"#/sharing?tab=ready_for_review&alarm={alarm['alarmId']}", project_id=alarm.get("projectId"),
                         dedupe=f"approval:{alarm['alarmId']}:{client['id']}", exclude=user["id"])
    return get_publication(alarm["alarmId"], client["id"])


def approve(alarm, user, client_id):
    client = _client(client_id)
    _require_eligible(alarm, user, client, "approve")
    now = db.now_iso()
    db.execute("UPDATE publications SET status='approved', approved_by=?, approved_by_id=?, approved_at=?, updated_at=? "
               "WHERE alarm_id=? AND client_id=?", (user["name"], user["id"], now, now, alarm["alarmId"], client["id"]))
    db.audit("share.approve", user, "alarm", alarm["alarmId"], "ready_for_review", "approved",
             alarm.get("projectId"), client["id"])
    import notify
    notify.to_permission("alarm.publish", "client", f"Approved for client: {alarm['alarmId']}",
                         f"{user['name']} approved sharing with {client['name']}. Ready to publish.",
                         f"#/sharing?tab=approved&alarm={alarm['alarmId']}", project_id=alarm.get("projectId"),
                         dedupe=f"approved:{alarm['alarmId']}:{client['id']}", exclude=user["id"])
    return get_publication(alarm["alarmId"], client["id"])


def publish(alarm, user, client_id, client_summary, evidence_selection, context_levels):
    client = _client(client_id)
    _require_eligible(alarm, user, client, "publish")
    summary = (client_summary or "").strip()
    if not summary:
        raise ApiError("bad_request", "A client-safe summary is required before publishing.", 400)
    images = alarm.get("imageUrls") or []
    selected = set(evidence_selection or [])
    evidence = [{"kind": "image", "index": i, "shared": f"image:{i}" in selected} for i in range(len(images))]
    if alarm.get("videoUrl"):
        evidence.append({"kind": "video", "index": 0, "shared": "video:0" in selected})
    allowed_levels = [lvl for lvl in (context_levels or []) if lvl in
                      ("project", "tc", "centre", "building", "floor", "room", "camera")]
    now = db.now_iso()
    with db.connect() as conn:
        exists = conn.execute("SELECT status FROM publications WHERE alarm_id=? AND client_id=?",
                              (alarm["alarmId"], client["id"])).fetchone()
        if not exists:
            conn.execute("INSERT INTO publications (alarm_id, client_id, status, project_id, requested_by, requested_at) "
                         "VALUES (?, ?, 'internal', ?, ?, ?)",
                         (alarm["alarmId"], client["id"], str(alarm.get("projectId")), user["name"], now))
        conn.execute("""UPDATE publications SET status='shared', shared_by=?, shared_by_id=?, shared_at=?, client_summary=?, evidence=?,
                        share_context=?, snapshot=?, updated_at=?, withdrawn_by=NULL, withdrawn_at=NULL,
                        withdraw_reason=NULL, acknowledged_by=NULL, acknowledged_at=NULL, ack_comment=NULL, viewed_at=NULL
                        WHERE alarm_id=? AND client_id=?""",
                     (user["name"], user["id"], now, summary, db.jdump(evidence), db.jdump(allowed_levels),
                      db.jdump(_snapshot(alarm)), now, alarm["alarmId"], client["id"]))
    shared_ev = [e for e in evidence if e["shared"]]
    db.audit("share.publish", user, "alarm", alarm["alarmId"], exists["status"] if exists else "internal", "shared",
             alarm.get("projectId"), client["id"],
             details={"evidenceShared": len(shared_ev), "evidenceInternal": len(evidence) - len(shared_ev),
                      "contextLevels": allowed_levels, "summaryShared": True})
    import notify
    critical = alarm.get("priority") == "critical"
    notify.to_client(client["id"], "client",
                     ("Critical alert shared" if critical else "New alert shared") + f": {alarm.get('alarmTypeName')}",
                     summary[:200], f"#/client/alerts/{alarm['alarmId']}",
                     dedupe=f"shared:{alarm['alarmId']}:{now}")
    return get_publication(alarm["alarmId"], client["id"])


def withdraw(alarm_id, user, client_id, reason):
    client = _client(client_id)
    if "alarm.withdraw" not in user["permissions"]:
        raise ApiError("forbidden", "You don't have permission to withdraw shared alerts.", 403)
    if not (reason or "").strip():
        raise ApiError("bad_request", "A reason is required to withdraw an alert.", 400)
    pub = db.one("SELECT status, project_id FROM publications WHERE alarm_id=? AND client_id=?", (alarm_id, client["id"]))
    if not pub or pub["status"] not in ("shared", "approved", "ready_for_review"):
        raise ApiError("not_eligible", "Only requested, approved or shared alerts can be withdrawn.", 409)
    from rbac import project_allowed
    if not project_allowed(user, pub["project_id"]):
        raise ApiError("not_found", "Resource unavailable.", 404)
    now = db.now_iso()
    db.execute("UPDATE publications SET status='withdrawn', withdrawn_by=?, withdrawn_at=?, withdraw_reason=?, "
               "updated_at=? WHERE alarm_id=? AND client_id=?",
               (user["name"], now, reason.strip(), now, alarm_id, client["id"]))
    db.audit("share.withdraw", user, "alarm", alarm_id, pub["status"], "withdrawn", pub["project_id"], client["id"],
             note=reason.strip())
    if pub["status"] == "shared":
        import notify
        notify.to_client(client["id"], "client", f"Alert withdrawn: {alarm_id}",
                         "An alert previously shared with you is no longer available.", "#/client/alerts",
                         dedupe=f"withdrawn:{alarm_id}:{now}")
    return get_publication(alarm_id, client["id"])


def get_publication(alarm_id, client_id):
    r = db.one("SELECT p.*, c.name AS client_name FROM publications p JOIN clients c ON c.id=p.client_id "
               "WHERE p.alarm_id=? AND p.client_id=?", (alarm_id, client_id))
    if not r:
        return None
    return pub_public(r)


# ---------------------------------------------------------------------------
# CLIENT DATA FIREWALL
# ---------------------------------------------------------------------------

CLIENT_FIELDS = ("alarmId", "alarmTypeName", "priority", "firstInstance", "lastInstance", "totalTimesReported",
                 "shiftLabel")


def client_safe_summary(alarm, context_levels=None):
    """Deterministic template (DERIVED, not AI) — only uses fields a client may see."""
    ctx = alarm.get("context") or {}
    levels = context_levels or []
    where = next((ctx[lvl]["code"] for lvl in ("camera", "room", "centre", "tc", "project")
                  if lvl in levels and ctx.get(lvl)), None)
    n = alarm.get("totalTimesReported") or 1
    ev = (alarm.get("evidence") or {}).get("count", 0)
    parts = [f"{alarm.get('alarmTypeName') or 'Alert'} detected" + (f" at {where}." if where else ".")]
    parts.append(f"{n} recorded occurrence{'s' if n != 1 else ''}.")
    parts.append("Evidence available." if ev else "No evidence attached.")
    return " ".join(parts)


def _live_for(pub_rows):
    """The current Camview records for delivered alerts (type name, priority, repeats as they are NOW)."""
    import datasource
    by_project, out = {}, {}
    for r in pub_rows:
        snap = db.jload(r.get("snapshot"), {}) or {}
        if snap.get("alarmId") and r.get("project_id"):
            by_project.setdefault(str(r["project_id"]), []).append(snap["alarmId"])
    for pid, ids in by_project.items():
        try:
            found, _ = datasource.live_alarms(pid, ids)
            out.update(found)
        except Exception:                             # the snapshot is always enough
            pass
    return out


def build_client_visible_alarm(user, pub_row, live=None):
    """Returns the client-safe view of ONE publication, or None.
    1 authorization  2 client/project scope  3 publication state
    4 strip internal fields  5 drop unshared evidence  6 no internal notes.
    Type name, priority and repeat count come from the live record when the alert is still in
    Camview's list (names in the Dictionary may have changed since delivery); the client never sees
    a verdict — only the alert."""
    if not user or user["audience"] != "client" or "client.portal" not in user["permissions"]:
        return None
    if pub_row["client_id"] != user["clientId"]:
        return None
    if str(pub_row.get("project_id")) not in user["clientProjects"]:
        return None                                   # project assignment removed -> access follows policy
    if policy().get("clientsSeeOperatorValidOnly", True) and not operator_valid([pub_row["alarm_id"]]):
        return None                                   # only alerts the backend team marked VALID reach a client
    if pub_row["status"] != "shared":
        return None
    snap = db.jload(pub_row.get("snapshot"), {}) or {}
    exams_allowed = (user.get("scopes") or {}).get("exam")
    if exams_allowed and ((snap.get("exam") or {}).get("id") not in exams_allowed):
        return None                                   # this login is limited to some of the client's exams
    out = {k: snap.get(k) for k in CLIENT_FIELDS}
    if live is None:
        live = _live_for([pub_row])
    now = live.get(snap.get("alarmId")) if live else None
    if now:
        for k in ("alarmTypeName", "priority", "lastInstance", "totalTimesReported"):
            if now.get(k) is not None:
                out[k] = now[k]
    ctx = snap.get("context") or {}
    levels = db.jload(pub_row.get("share_context"), []) or []
    out["context"] = [{"level": lvl, "code": ctx[lvl]["code"], "name": ctx[lvl].get("name")}
                      for lvl in ("project", "tc", "centre", "building", "floor", "room", "camera")
                      if lvl in levels and ctx.get(lvl)]
    # the same naming operators see, limited to the levels shared with this client:
    # master data  PROJECT - TC - CAMERA · Camview data  CENTRE CODE - SUB-LOCATION - CAMERA NUMBER
    shown = {c["level"]: c for c in out["context"]}
    cam = shown.get("camera", {}).get("code")
    if "tc" in shown:
        parts = [shown.get("project", {}).get("code"), shown["tc"]["code"], cam]
    elif "centre" in shown or (snap.get("centreCode") and "centre" in levels):
        parts = [shown["centre"]["code"] if "centre" in shown else snap.get("centreCode"),
                 shown.get("room", {}).get("code"), cam]
    else:
        parts = [shown.get("project", {}).get("code"), cam]
    out["locationLabel"] = " - ".join(p for p in parts if p) or None
    out["cameraName"] = shown["camera"].get("name") if "camera" in shown else None
    if "client.evidence" in user["permissions"]:
        out["evidence"] = [{"kind": e["kind"], "index": e["index"],
                            "url": f"/api/client/evidence/{snap.get('alarmId')}/{e['kind']}/{e['index']}"}
                           for e in (db.jload(pub_row.get("evidence"), []) or []) if e.get("shared")]
    else:
        out["evidence"] = []
    out["imageUrl"] = next((e["url"] for e in out["evidence"] if e["kind"] == "image"), None)   # frame for lists
    out["hasVideo"] = any(e["kind"] == "video" for e in out["evidence"])
    policy_ = db.jload((db.one("SELECT visibility_policy FROM clients WHERE id=?", (user["clientId"],)) or {})
                       .get("visibility_policy"), {}) or {}
    if policy_.get("showTicket") and snap.get("ticketId"):
        out["ticketId"] = snap.get("ticketId")
    out["status"] = "Delivered"
    exam = snap.get("exam") or None
    out["exam"] = {"id": exam.get("id"), "name": exam.get("name")} if exam else None
    t = db.one("SELECT ref FROM tickets WHERE alarm_id=? AND client_id=? AND status='open'",
               (snap.get("alarmId"), pub_row["client_id"]))
    out["ticketRef"] = t["ref"] if t else None
    # an automatic delivery's summary is rebuilt from the current record (current type name); a summary a
    # person wrote when publishing by hand is kept as written — never with a stored verdict
    if now and str(pub_row.get("shared_by") or "").startswith("Auto-share"):
        summary = client_safe_summary({**now, "context": ctx, "evidence": {"count": len(out["evidence"])}}, levels)
        out["summary"] = f"{exam['name']}: {summary}" if exam and exam.get("name") else summary
    else:
        out["summary"] = re.sub(r"\s*Status: Validated\.?", "", pub_row.get("client_summary") or "") or None
    out["sharedAt"] = pub_row.get("shared_at")
    out["acknowledgedAt"] = pub_row.get("acknowledged_at")
    out["acknowledgedBy"] = pub_row.get("acknowledged_by")
    out["ackComment"] = pub_row.get("ack_comment")
    out["viewedAt"] = pub_row.get("viewed_at")
    return out


def operator_valid(alarm_ids):
    """The alarm ids an operator of the backend team marked VALID (ops_review), whatever Camview says."""
    return {i for i, r in db.reviews_for(alarm_ids).items() if r["status"] == "marked_valid"}


def client_visible_alarms(user):
    """The CLIENT DATASET: every alarm this client user may see, already
    filtered and transformed. Client analytics/reports use only this."""
    if not user or user["audience"] != "client" or not user["clientId"]:
        return []
    out = []
    rows = db.rows("SELECT * FROM publications WHERE client_id=? AND status='shared' ORDER BY shared_at DESC",
                   (user["clientId"],))
    live = _live_for(rows)
    for r in rows:
        v = build_client_visible_alarm(user, r, live)
        if v:
            out.append(v)
    return out


def internal_not_shared(alarm, context_levels, evidence_selection):
    """What the preview lists as staying INTERNAL."""
    items = ["Internal notes and operator comments", "Internal audit trail", "Ops review decisions and who made them",
             "Camview alarm state codes and raw metadata", "Assignment and workflow information"]
    ctx = alarm.get("context") or {}
    hidden_ctx = [ctx[lvl]["code"] for lvl in ("project", "tc", "centre", "building", "floor", "room", "camera")
                  if ctx.get(lvl) and lvl not in (context_levels or [])]
    if hidden_ctx:
        items.append("Location context: " + ", ".join(hidden_ctx))
    total_ev = len(alarm.get("imageUrls") or []) + (1 if alarm.get("videoUrl") else 0)
    hidden_ev = total_ev - len(evidence_selection or [])
    if hidden_ev > 0:
        items.append(f"{hidden_ev} evidence item{'s' if hidden_ev != 1 else ''} not selected")
    if alarm.get("ticketId"):
        items.append(f"Ticket #{alarm['ticketId']} (unless the client's policy shows tickets)")
    return items


# ---------------------------------------------------------------------------
# Client conversation (comment / clarification request / internal response)
# ---------------------------------------------------------------------------

MESSAGE_KINDS = {"comment": "Comment", "clarification": "Clarification requested", "response": "Response"}


def messages_for(alarm_id, client_id):
    """The shared thread between the client and the operations team for ONE
    alarm and client. Internal notes are never part of it."""
    return [{"id": r["id"], "kind": r["kind"], "kindLabel": MESSAGE_KINDS.get(r["kind"], r["kind"]),
             "audience": r["audience"], "author": r["author_name"], "body": r["body"], "createdAt": r["created_at"]}
            for r in db.rows("SELECT * FROM client_messages WHERE alarm_id=? AND client_id=? ORDER BY id",
                             (alarm_id, client_id))]


def add_message(alarm_id, client_id, user, kind, body, project_id=None):
    body = (body or "").strip()
    if not body:
        raise ApiError("bad_request", "Message text is required.", 400)
    if kind not in MESSAGE_KINDS:
        raise ApiError("bad_request", "Unknown message type.", 400)
    audience = "client" if user["audience"] == "client" else "internal"
    if (audience == "client") != (kind in ("comment", "clarification")):
        raise ApiError("bad_request", "Clients comment or ask for clarification; the operations team responds.", 400)
    db.execute("INSERT INTO client_messages (alarm_id, client_id, author_id, author_name, audience, kind, body, created_at) "
               "VALUES (?,?,?,?,?,?,?,?)", (alarm_id, client_id, user["id"], user["name"], audience, kind, body[:2000],
                                           db.now_iso()))
    db.audit(f"client.{kind}", user, "alarm", alarm_id, None, kind, project_id, client_id, note=body[:200])
    import notify
    if audience == "client":
        notify.to_permission("alarm.publish", "client",
                             f"{'Clarification requested' if kind == 'clarification' else 'Client comment'}: {alarm_id}",
                             f"{user['name']} ({(user.get('client') or {}).get('name', '')}): {body[:160]}",
                             f"#/investigations/{alarm_id}", project_id=project_id,
                             dedupe=f"cmsg:{alarm_id}:{db.now_iso()}", severity="warning" if kind == "clarification" else "info")
    else:
        notify.to_client(client_id, "client", f"Response from the operations team: {alarm_id}", body[:200],
                         f"#/client/alerts/{alarm_id}", dedupe=f"cresp:{alarm_id}:{db.now_iso()}")
    return messages_for(alarm_id, client_id)


# ---------------------------------------------------------------------------
# Notes, assignment
# ---------------------------------------------------------------------------

NOTE_KINDS = {"internal": "Internal note", "operator": "Operator note", "supervisor": "Supervisor note"}


def add_note(alarm_id, kind, body, user, project_id=None):
    if kind not in NOTE_KINDS:
        raise ApiError("bad_request", "Unknown note type.", 400)
    if kind == "supervisor" and "alarm.approve" not in user["permissions"]:
        raise ApiError("forbidden", "Only supervisors can add supervisor notes.", 403)
    body = (body or "").strip()
    if not body:
        raise ApiError("bad_request", "Note text is required.", 400)
    db.execute("INSERT INTO notes (alarm_id, kind, body, author_id, author_name, created_at) VALUES (?,?,?,?,?,?)",
               (alarm_id, kind, body[:4000], user["id"], user["name"], db.now_iso()))
    db.audit("note.add", user, "alarm", alarm_id, None, kind, project_id, note=body[:200])


def notes_for(alarm_id):
    return [{"id": r["id"], "kind": r["kind"], "kindLabel": NOTE_KINDS.get(r["kind"], r["kind"]), "body": r["body"],
             "author": r["author_name"], "createdAt": r["created_at"]}
            for r in db.rows("SELECT * FROM notes WHERE alarm_id=? ORDER BY id DESC", (alarm_id,))]


def assign(alarm, assignee_id, user):
    if assignee_id in (None, ""):
        db.execute("DELETE FROM assignments WHERE alarm_id=?", (alarm["alarmId"],))
        db.audit("alarm.unassign", user, "alarm", alarm["alarmId"], None, None, alarm.get("projectId"))
        return None
    from rbac import load_user, alarm_in_scope
    target = load_user(assignee_id)
    if not target or target["audience"] != "internal" or "alarm.investigate" not in target["permissions"]:
        raise ApiError("bad_request", "That user can't be assigned investigations.", 400)
    if not alarm_in_scope(target, alarm):
        raise ApiError("bad_request", f"{target['name']} has no access to this alarm's scope.", 400)
    db.execute("INSERT INTO assignments (alarm_id, user_id, assigned_by, assigned_at, snapshot) VALUES (?,?,?,?,?) "
               "ON CONFLICT(alarm_id) DO UPDATE SET user_id=excluded.user_id, assigned_by=excluded.assigned_by, "
               "assigned_at=excluded.assigned_at, snapshot=excluded.snapshot",
               (alarm["alarmId"], target["id"], user["name"], db.now_iso(), db.jdump(_snapshot(alarm))))
    db.audit("alarm.assign", user, "alarm", alarm["alarmId"], None, target["name"], alarm.get("projectId"))
    import notify
    notify.to_users([target["id"]], "investigation", f"Assigned to you: {alarm['alarmId']}",
                    f"{user['name']} assigned {alarm.get('alarmTypeName')} to you.",
                    f"#/investigations/{alarm['alarmId']}", dedupe=f"assign:{alarm['alarmId']}:{target['id']}")
    return {"userId": target["id"], "name": target["name"]}


def assignments_for(alarm_ids):
    return {r["alarm_id"]: {"userId": r["user_id"], "name": r["name"], "assignedBy": r["assigned_by"],
                            "assignedAt": r["assigned_at"]}
            for r in db._in_chunks("SELECT a.alarm_id, a.user_id, a.assigned_by, a.assigned_at, u.name "
                                   "FROM assignments a JOIN users u ON u.id = a.user_id", alarm_ids, "a.alarm_id")}
