"""
tickets.py — the automated decision engine.

The human decides ONE thing per alert: VALID, INVALID or EXCEPTION.
Everything else is automatic:

  VALID      → decision recorded (who / when)
             → ticket created or re-associated (idempotent: one alert = one ticket)
             → exam + client resolved from the mapping (no manual selection)
             → delivered to the client automatically   (deliveryMode = automatic)
               or marked READY with a one-click Send   (deliveryMode = controlled)
  INVALID    → decision recorded, never delivered; an earlier ticket is cancelled
               and any client delivery withdrawn
  EXCEPTION  → decision recorded, kept in exception monitoring, not delivered

Delivery reuses the existing publication model and client firewall, so the
client sees only the client-safe view built by workflow.build_client_visible_alarm.
"""

import db
import exams
import workflow
from camview_client import ApiError

RESULTS = {"valid": "mark_valid", "invalid": "mark_invalid", "exception": "mark_exception"}
PERMS = {"valid": "alarm.validate", "invalid": "alarm.invalidate", "exception": "alarm.exception"}


def _ticket_row(r):
    if not r:
        return None
    return {"id": r["id"], "ref": r["ref"], "alarmId": r["alarm_id"], "camviewTicketId": r["camview_ticket_id"],
            "examId": r["exam_id"], "clientId": r["client_id"], "projectId": r["project_id"], "status": r["status"],
            "result": r["result"], "validatedBy": r["validated_by"], "validatedAt": r["validated_at"],
            "validatedById": r["validated_by_id"],
            "deliveryStatus": r["delivery_status"], "deliveryNote": r["delivery_note"],
            "deliveredAt": r["delivered_at"], "deliveredBy": r["delivered_by"], "createdAt": r["created_at"],
            "updatedAt": r["updated_at"], "snapshot": db.jload(r["snapshot"], {}) or {},
            "examName": r.get("exam_name"), "clientName": r.get("client_name")}


_SELECT = ("SELECT t.*, e.name AS exam_name, c.name AS client_name FROM tickets t "
           "LEFT JOIN exams e ON e.id=t.exam_id LEFT JOIN clients c ON c.id=t.client_id")


def for_alarm(alarm_id):
    return _ticket_row(db.one(_SELECT + " WHERE t.alarm_id=?", (alarm_id,)))


def for_alarms(alarm_ids):
    return {r["alarm_id"]: _ticket_row(dict(r)) for r in db._in_chunks(_SELECT, alarm_ids, "t.alarm_id")}


def get(ticket_id):
    return _ticket_row(db.one(_SELECT + " WHERE t.id=?", (ticket_id,)))


def _snapshot(alarm, exam, clients):
    snap = workflow._snapshot(alarm)
    snap["exam"] = {"id": exam["id"], "code": exam["code"], "name": exam["name"]} if exam else None
    snap["clients"] = [{"id": c["id"], "name": c["name"]} for c in clients]
    return snap


def ensure_ticket(alarm, user, exam, client_id, clients):
    """Creates the alert's ticket once. Repeated calls (double click, retry,
    reload, background sync) return the same ticket."""
    now = db.now_iso()
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tickets (alarm_id, camview_ticket_id, exam_id, client_id, project_id, status, result, "
            "validated_by, validated_by_id, validated_at, delivery_status, snapshot, created_at, updated_at) "
            "VALUES (?,?,?,?,?,'open','valid',?,?,?,NULL,?,?,?)",
            (alarm["alarmId"], None if alarm.get("ticketId") is None else str(alarm.get("ticketId")),
             exam["id"] if exam else None, client_id, str(alarm.get("projectId")), user["name"], user["id"], now,
             db.jdump(_snapshot(alarm, exam, clients)), now, now))
        row = conn.execute("SELECT id, ref, status FROM tickets WHERE alarm_id=?", (alarm["alarmId"],)).fetchone()
        created = row["ref"] is None
        if created:
            conn.execute("UPDATE tickets SET ref=? WHERE id=?", (f"TKT-{row['id']:06d}", row["id"]))
        elif row["status"] == "cancelled":       # re-validated after an earlier INVALID/EXCEPTION
            conn.execute("UPDATE tickets SET status='open', result='valid', validated_by=?, validated_by_id=?, "
                         "validated_at=?, client_id=COALESCE(?, client_id), delivery_status=NULL, delivery_note=NULL, "
                         "updated_at=? WHERE id=?", (user["name"], user["id"], now, client_id, now, row["id"]))
    t = for_alarm(alarm["alarmId"])
    if created:
        db.audit("ticket.create", user, "ticket", t["ref"], None, {"alarm": alarm["alarmId"], "client": client_id,
                                                                    "exam": exam["id"] if exam else None},
                 project_id=alarm.get("projectId"), client_id=client_id)
    return t, created


def _set_delivery(ticket_id, status, note=None, user=None, delivered=False):
    now = db.now_iso()
    db.execute("UPDATE tickets SET delivery_status=?, delivery_note=?, updated_at=?"
               + (", delivered_at=?, delivered_by=?" if delivered else "") + " WHERE id=?",
               (status, note, now, *((now, user["name"]) if delivered else ()), ticket_id))


def _deliverable(alarm, client):
    if not client:
        return False, "No client is mapped to this exam/project (Administration › Exams)."
    if client.get("status") != "active":
        return False, f"Client {client['name']} is inactive."
    t = db.one("SELECT client_share_policy FROM alarm_types WHERE id=?", (alarm.get("alarmType"),))
    if t and t["client_share_policy"] == "never":
        return False, "This alarm type is configured never to be sent to clients."
    return True, None


def deliver(alarm, client, user, exam, how, quiet=False):
    """Makes the alert visible to the client (a 'shared' publication built
    from the policy defaults). No wizard, no manual composition."""
    pol = workflow.policy()
    levels = pol["clientContextLevels"]
    images = alarm.get("imageUrls") or []
    mode = pol.get("autoEvidence", "all")
    evidence = [{"kind": "image", "index": i, "shared": mode == "all" or (mode == "first" and i == 0)}
                for i in range(len(images))]
    if alarm.get("videoUrl"):
        evidence.append({"kind": "video", "index": 0, "shared": mode == "all"})
    summary = workflow.client_safe_summary(alarm, levels)
    if exam:
        summary = f"{exam['name']}: {summary}"
    snap = _snapshot(alarm, exam, [client])
    now = db.now_iso()
    with db.connect() as conn:
        conn.execute("""
            INSERT INTO publications (alarm_id, client_id, status, project_id, requested_by, requested_by_id, requested_at,
                approved_by, approved_by_id, approved_at, shared_by, shared_by_id, shared_at, client_summary, evidence,
                share_context, snapshot, updated_at)
            VALUES (?,?, 'shared', ?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?,?)
            ON CONFLICT(alarm_id, client_id) DO UPDATE SET status='shared', shared_by=excluded.shared_by,
                shared_by_id=excluded.shared_by_id, shared_at=excluded.shared_at, client_summary=excluded.client_summary,
                evidence=excluded.evidence, share_context=excluded.share_context, snapshot=excluded.snapshot,
                updated_at=excluded.updated_at, withdrawn_by=NULL, withdrawn_at=NULL, withdraw_reason=NULL
        """, (alarm["alarmId"], client["id"], str(alarm.get("projectId")), user["name"], user["id"], now,
              user["name"], user["id"], now, user["name"], user["id"], now, summary, db.jdump(evidence),
              db.jdump(levels), db.jdump(snap), now))
    db.audit("delivery." + how, user, "alarm", alarm["alarmId"], None, "delivered", alarm.get("projectId"), client["id"],
             details={"evidenceShared": sum(1 for e in evidence if e["shared"]), "contextLevels": levels,
                      "exam": exam["id"] if exam else None})
    if quiet:
        return
    import notify
    notify.to_client(client["id"], "client",
                     ("Critical alert" if alarm.get("priority") == "critical" else "New alert")
                     + f": {alarm.get('alarmTypeName')}" + (f" — {exam['name']}" if exam else ""),
                     summary[:200], f"#/client/alerts/{alarm['alarmId']}", dedupe=f"delivered:{alarm['alarmId']}:{now}",
                     severity="critical" if alarm.get("priority") == "critical" else "info")


def withdraw_delivery(alarm_id, user, reason):
    """Removes client visibility (e.g. an alert re-classified as INVALID)."""
    now = db.now_iso()
    pubs = db.rows("SELECT client_id, status, project_id FROM publications WHERE alarm_id=? "
                   "AND status IN ('shared','approved','ready_for_review')", (alarm_id,))
    for p in pubs:
        db.execute("UPDATE publications SET status='withdrawn', withdrawn_by=?, withdrawn_at=?, withdraw_reason=?, "
                   "updated_at=? WHERE alarm_id=? AND client_id=?", (user["name"], now, reason, now, alarm_id, p["client_id"]))
        db.audit("delivery.withdraw", user, "alarm", alarm_id, p["status"], "withdrawn", p["project_id"], p["client_id"],
                 note=reason)
        if p["status"] == "shared":
            import notify
            notify.to_client(p["client_id"], "client", f"Alert withdrawn: {alarm_id}",
                             "An alert previously sent to you is no longer valid and has been withdrawn.",
                             "#/client/alerts", dedupe=f"withdrawn:{alarm_id}:{now}")
    return len(pubs)


def decide(alarm, user, result, note=None, client_id=None):
    """The one human action. Returns what the system did automatically."""
    if result not in RESULTS:
        raise ApiError("bad_request", "Result must be valid, invalid or exception.", 400)
    if PERMS[result] not in user["permissions"]:
        raise ApiError("forbidden", "You don't have permission to make that decision.", 403)
    if alarm.get("eventKind") == "camera_status":
        raise ApiError("camera_event", "Camera online / offline events are counted in the Cameras KPI and camera health. "
                                       "They are not alerts to decide and never become tickets.", 409)
    pol = workflow.policy()
    if not pol.get("manualReview"):
        raise ApiError("manual_review_off", "Alerts are decided by Camview's status and sent automatically. "
                                            "Manual review is turned off (Settings › Workflow).", 409)
    if pol.get("requireRemarks") and not (note or "").strip():
        raise ApiError("remarks_required", "Your administrator requires a remark for every decision.", 400)
    exam = exams.resolve(alarm.get("projectId"), alarm.get("firstInstance"))
    clients = [c for c in exams.clients_for(alarm.get("projectId"), exam)]
    review = db.apply_action(alarm["alarmId"], RESULTS[result], user["name"], note, snapshot=_snapshot(alarm, exam, clients),
                             user=user, project_id=alarm.get("projectId"))
    out = {"result": result, "review": review, "exam": exam, "clients": clients, "ticket": None,
           "delivery": {"status": "none"}}

    if result != "valid":
        t = for_alarm(alarm["alarmId"])
        if t and t["status"] != "cancelled":
            db.execute("UPDATE tickets SET status='cancelled', result=?, delivery_status='withdrawn', updated_at=? WHERE id=?",
                       (result, db.now_iso(), t["id"]))
            withdrawn = withdraw_delivery(alarm["alarmId"], user, f"Re-classified as {result.upper()}")
            db.audit("ticket.cancel", user, "ticket", t["ref"], "open", "cancelled", alarm.get("projectId"),
                     note=f"Alert re-classified as {result}")
            out["delivery"] = {"status": "withdrawn", "withdrawn": withdrawn}
        out["ticket"] = for_alarm(alarm["alarmId"])
        return out

    # ---- VALID -------------------------------------------------------------
    chosen = None
    if client_id:
        chosen = next((c for c in clients if c["id"] == client_id), None)
        if not chosen:
            raise ApiError("bad_request", "That client is not mapped to this alert's exam/project.", 400)
    elif len(clients) == 1:
        chosen = clients[0]
    ticket, created = ensure_ticket(alarm, user, exam, chosen["id"] if chosen else None, clients)
    out["ticketCreated"] = created

    if ticket["deliveryStatus"] == "delivered":          # idempotent: already delivered
        out["delivery"] = {"status": "delivered", "clientId": ticket["clientId"]}
    elif len(clients) > 1 and not chosen:
        _set_delivery(ticket["id"], "needs_client", "This exam/project maps to more than one client — choose one.")
        out["delivery"] = {"status": "needs_client", "options": clients}
    else:
        ok, why = _deliverable(alarm, chosen)
        if not ok:
            _set_delivery(ticket["id"], "not_deliverable", why)
            out["delivery"] = {"status": "not_deliverable", "reason": why}
        elif pol.get("deliveryMode", "controlled") == "automatic":
            try:
                deliver(alarm, chosen, user, exam, "auto")
            except Exception:
                import logging
                logging.getLogger("camview.tickets").exception("Client delivery failed for %s", alarm["alarmId"])
                _set_delivery(ticket["id"], "failed", "Delivery to the client failed — retry.")
                raise ApiError("delivery_failed", f"VALID was saved and ticket {ticket['ref']} exists, but delivery to "
                                                  f"{chosen['name']} failed. Press VALID again (or Send) to retry.", 502)
            _set_delivery(ticket["id"], "delivered", None, user, delivered=True)
            out["delivery"] = {"status": "delivered", "clientId": chosen["id"], "clientName": chosen["name"]}
        else:
            _set_delivery(ticket["id"], "ready", f"Ready to send to {chosen['name']}")
            import notify
            notify.to_permission("alarm.publish", "client", f"Ready for {chosen['name']}: {alarm['alarmId']}",
                                 f"{alarm.get('alarmTypeName')} validated by {user['name']} — one click to send.",
                                 "#/tickets?delivery=ready", project_id=alarm.get("projectId"),
                                 dedupe=f"ready:{alarm['alarmId']}", exclude=user["id"])
            out["delivery"] = {"status": "ready", "clientId": chosen["id"], "clientName": chosen["name"]}
    out["ticket"] = for_alarm(alarm["alarmId"])
    return out


def send(ticket_id, user, alarm, client_id=None):
    """Controlled mode: one click sends a READY ticket to its client."""
    t = get(ticket_id)
    if not t or t["status"] != "open":
        raise ApiError("not_eligible", "Only open (valid) tickets can be sent.", 409)
    if t["deliveryStatus"] == "delivered":
        return t                                             # idempotent
    exam = exams.get(t["examId"]) if t["examId"] else None
    clients = exams.clients_for(t["projectId"], exam)
    cid = client_id or t["clientId"]
    client = next((c for c in clients if c["id"] == cid), None)
    if not client:
        raise ApiError("bad_request", "Choose the client for this ticket.", 400)
    ok, why = _deliverable(alarm, client)
    if not ok:
        raise ApiError("not_eligible", why, 409)
    if workflow.policy().get("sendFourEyes") and t["validatedAt"] and \
            db.one("SELECT validated_by_id FROM tickets WHERE id=?", (t["id"],))["validated_by_id"] == user["id"]:
        raise ApiError("not_eligible", "Four-eyes is on: the person who validated can't also send it.", 409)
    deliver(alarm, client, user, exam, "send")
    db.execute("UPDATE tickets SET client_id=? WHERE id=?", (client["id"], t["id"]))
    _set_delivery(t["id"], "delivered", None, user, delivered=True)
    return get(ticket_id)


def withdraw(ticket_id, user, reason=None):
    t = get(ticket_id)
    if not t:
        raise ApiError("not_found", "Ticket not found.", 404)
    n = withdraw_delivery(t["alarmId"], user, (reason or "").strip() or "Withdrawn by operations")
    _set_delivery(t["id"], "withdrawn", (reason or "").strip() or None)
    return get(ticket_id), n


def list_tickets(user, filters, allowed_projects):
    where, params = ["1=1"], []
    if filters.get("status"):
        where.append("t.status=?"); params.append(filters["status"])
    if filters.get("delivery"):
        where.append("t.delivery_status=?"); params.append(filters["delivery"])
    if filters.get("client"):
        where.append("t.client_id=?"); params.append(filters["client"])
    if filters.get("exam"):
        where.append("t.exam_id=?"); params.append(filters["exam"])
    if filters.get("search"):
        s = f"%{filters['search']}%"
        where.append("(t.ref LIKE ? OR t.alarm_id LIKE ?)"); params += [s, s]
    ph = ",".join("?" for _ in allowed_projects) or "''"
    where.append(f"t.project_id IN ({ph})"); params += list(allowed_projects)
    rows = db.rows(_SELECT + f" WHERE {' AND '.join(where)} ORDER BY t.id DESC LIMIT 1000", params)
    counts = {r["delivery_status"] or "none": r["n"] for r in db.rows(
        f"SELECT delivery_status, COUNT(*) AS n FROM tickets t WHERE t.status='open' AND t.project_id IN ({ph}) "
        "GROUP BY delivery_status", list(allowed_projects))}
    return [_ticket_row(dict(r)) for r in rows], counts


# ---------------------------------------------------------------------------
# Auto-share: alerts the Camview API reports as VALID are sent to the client
# ---------------------------------------------------------------------------

SYSTEM_USER = {"id": None, "name": "Auto-share (automatic delivery)", "permissions": [], "audience": "internal",
               "email": "system", "role": "system"}
QUIET_BATCH = 10          # more deliveries than this in one refresh → one summary notification per client


def _auto_retry(t):
    """No ticket yet, a cancelled one, or a system ticket that could not be delivered (no client mapped yet)."""
    if not t or t["status"] != "open":
        return True
    return t["validatedBy"] == SYSTEM_USER["name"] and t["deliveryStatus"] in ("not_deliverable", "needs_client", "failed", None)


def auto_sync(items):
    """Runs after every data refresh when policy autoShareValid is on.

    deliveryTrigger = "valid"  : Camview VALID (lastActionType 1), no operator decision, no open ticket
                                 → ticket + delivered to the exam's client. An auto-delivered alert that
                                 Camview later reports as not valid → withdrawn, ticket cancelled.
    deliveryTrigger = "arrival": every detection alert (pending or valid in Camview) is delivered the moment
                                 it arrives — the client sees a 12:00 alert at 12:00, not when someone marks
                                 it hours later. Camera status events are never delivered. When Camview or an
                                 operator marks it INVALID / EXCEPTION it is withdrawn.
    Idempotent. With no client mapped yet the ticket is still created (NOT DELIVERABLE) and delivered once
    one is. Operator decisions always win. Returns (delivered, withdrawn)."""
    pol = workflow.policy()
    if not pol.get("autoShareValid") or not items or pol.get("clientsSeeOperatorValidOnly", True):
        return 0, 0                          # only an operator's VALID delivers (decide()); nothing automatic
    arrival = pol.get("deliveryTrigger") == "arrival"

    def wanted(a):
        if a.get("eventKind") == "camera_status":
            return False
        return a.get("lastActionType") in (0, 1) if arrival else a.get("lastActionType") == 1

    def dropped(a):
        return a.get("lastActionType") in (2, 3) if arrival else a.get("lastActionType") != 1
    from datetime import datetime, timedelta, timezone
    hours = pol.get("autoShareHours")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=float(hours))).strftime("%Y-%m-%dT%H:%M:%S") if hours else ""
    ids = [a["alarmId"] for a in items]
    reviews = db.reviews_for(ids)
    decided = {i for i, r in reviews.items() if r["status"] in ("marked_valid", "marked_invalid", "marked_exception")}
    existing = for_alarms(ids)
    to_deliver = [a for a in items if wanted(a) and a["alarmId"] not in decided
                  and (a.get("lastInstance") or a.get("firstInstance") or "") >= cutoff
                  and _auto_retry(existing.get(a["alarmId"]))]
    stale = [a for a in items if dropped(a) and a["alarmId"] not in decided
             and (existing.get(a["alarmId"]) or {}).get("status") == "open"
             and (existing.get(a["alarmId"]) or {}).get("validatedBy") == SYSTEM_USER["name"]]
    delivered, per_client = 0, {}
    if to_deliver:
        import datasource
        quiet = len(to_deliver) > QUIET_BATCH
        for a in datasource.enrich(to_deliver):
            exam = exams.get(a["exam"]["id"]) if a.get("exam") else None
            clients = exams.clients_for(a.get("projectId"), exam)
            prior = existing.get(a["alarmId"])
            if not clients and prior and prior["status"] == "open" and prior["deliveryStatus"] == "not_deliverable":
                continue                              # ticket exists; delivered once a client is mapped
            chosen = clients[0] if len(clients) == 1 else None
            ticket, _ = ensure_ticket(a, SYSTEM_USER, exam, chosen["id"] if chosen else None, clients)
            if chosen and ticket["clientId"] != chosen["id"]:
                db.execute("UPDATE tickets SET client_id=? WHERE id=?", (chosen["id"], ticket["id"]))
            if ticket["deliveryStatus"] == "delivered":
                continue
            if len(clients) > 1:
                _set_delivery(ticket["id"], "needs_client", "This exam/project maps to more than one client — choose one.")
                continue
            ok, why = _deliverable(a, chosen)
            if not ok:
                _set_delivery(ticket["id"], "not_deliverable", why)
                continue
            try:
                deliver(a, chosen, SYSTEM_USER, exam, "auto_arrival" if arrival else "auto_camview", quiet=quiet)
            except Exception:                     # one failure never blocks the rest; retried next refresh
                import logging
                logging.getLogger("camview.tickets").exception("Auto-share delivery failed for %s", a["alarmId"])
                _set_delivery(ticket["id"], "failed", "Delivery to the client failed — retried automatically.")
                continue
            _set_delivery(ticket["id"], "delivered", None, SYSTEM_USER, delivered=True)
            delivered += 1
            per_client[chosen["id"]] = per_client.get(chosen["id"], 0) + 1
        if quiet and per_client:
            import notify
            for cid, n in per_client.items():
                notify.to_client(cid, "client", f"{n} new alerts", "New alerts are available in your portal.",
                                 "#/client/alerts", dedupe=f"autobatch:{cid}:{db.now_iso()}")
    withdrawn = 0
    for a in stale:
        t = existing[a["alarmId"]]
        db.execute("UPDATE tickets SET status='cancelled', result='camview_changed', delivery_status='withdrawn', "
                   "updated_at=? WHERE id=?", (db.now_iso(), t["id"]))
        withdraw_delivery(a["alarmId"], SYSTEM_USER, "Camview reports this alert as INVALID / EXCEPTION" if arrival
                          else "Camview no longer reports this alert as VALID")
        withdrawn += 1
    return delivered, withdrawn


def enforce_operator_valid_only():
    """Applies "clients see only alerts the backend team marked VALID" to what was delivered before the rule:
    every client-visible publication and open automatic ticket whose alert has no operator VALID is withdrawn
    (publication) and cancelled (ticket). Quiet: clients are not notified per alert — they never should have
    seen these. Idempotent; runs at start-up and when the policy is saved. Returns (withdrawn, cancelled)."""
    if not workflow.policy().get("clientsSeeOperatorValidOnly", True):
        return 0, 0
    now = db.now_iso()
    pubs = db.rows("SELECT alarm_id, client_id, project_id FROM publications WHERE status IN ('shared','approved','ready_for_review')")
    ok = workflow.operator_valid([p["alarm_id"] for p in pubs])
    bad = [p for p in pubs if p["alarm_id"] not in ok]
    auto = db.rows("SELECT id, alarm_id FROM tickets WHERE status='open' AND validated_by_id IS NULL")
    ok_t = workflow.operator_valid([t["alarm_id"] for t in auto])
    cancel = [t for t in auto if t["alarm_id"] not in ok_t]
    if not bad and not cancel:
        return 0, 0
    reason = "Not marked VALID by the operations team (clients see only operator-validated alerts)"
    with db.batch("operator-valid-only"), db.connect() as conn:
        for p in bad:
            conn.execute("UPDATE publications SET status='withdrawn', withdrawn_by=?, withdrawn_at=?, withdraw_reason=?, "
                         "updated_at=? WHERE alarm_id=? AND client_id=?",
                         (SYSTEM_USER["name"], now, reason, now, p["alarm_id"], p["client_id"]))
        for t in cancel:
            conn.execute("UPDATE tickets SET status='cancelled', delivery_status='withdrawn', delivery_note=?, updated_at=? "
                         "WHERE id=?", (reason, now, t["id"]))
    db.audit("delivery.policy_enforced", None, "policy", "clientsSeeOperatorValidOnly", None,
             {"withdrawn": len(bad), "ticketsCancelled": len(cancel)}, note=reason)
    import logging
    logging.getLogger("camview.tickets").info("Operator-VALID-only: %s client deliveries withdrawn, %s automatic tickets "
                                              "cancelled", len(bad), len(cancel))
    return len(bad), len(cancel)


def deliver_pending_valid():
    """Operator-VALID alerts whose ticket could not be delivered yet (no client mapped, several clients, a failed
    send) are delivered as soon as that is resolved: runs at start-up and whenever exams or clients change.
    Only automatic delivery sends by itself; controlled delivery marks them READY for the one-click Send.
    Returns how many were delivered or made ready."""
    import datasource
    pol = workflow.policy()
    # the operator's VALID is the review (ops_review) — a ticket opened earlier by auto-share keeps no validator
    rows = db.rows("SELECT * FROM tickets WHERE status='open' "
                   "AND COALESCE(delivery_status, '') IN ('', 'not_deliverable', 'needs_client', 'failed')")
    if not rows:
        return 0
    reviews = db.reviews_for([r["alarm_id"] for r in rows])
    decided = {i for i, rv in reviews.items() if rv["status"] == "marked_valid"}
    done = 0
    with db.batch("deliver-pending"):
        for r in rows:
            if r["alarm_id"] not in decided:
                continue
            t = _ticket_row(r)
            live = {a["alarmId"]: a for a in datasource.refresh(str(t["projectId"])).items}
            alarm = live.get(t["alarmId"]) or t["snapshot"] or {}
            if not alarm.get("alarmId"):
                continue
            if t["alarmId"] in live:                  # the full record (context, exam, evidence) the client view needs
                alarm = datasource.enrich([alarm])[0]
            exam = exams.resolve(alarm.get("projectId") or t["projectId"], alarm.get("firstInstance"))
            clients = exams.clients_for(alarm.get("projectId") or t["projectId"], exam)
            client = next((c for c in clients if c["id"] == t["clientId"]), None) or (clients[0] if len(clients) == 1 else None)
            ok, why = _deliverable(alarm, client)
            if not ok:
                if why != t["deliveryNote"]:
                    _set_delivery(t["id"], "needs_client" if len(clients) > 1 else "not_deliverable", why)
                continue
            rv = reviews[t["alarmId"]]
            who = {"id": t.get("validatedById") or rv["validatedById"],
                   "name": (t.get("validatedBy") if t.get("validatedById") else rv["updatedBy"]) or "Operations"}
            db.execute("UPDATE tickets SET client_id=?, exam_id=COALESCE(exam_id, ?), result='valid', validated_by=?, "
                       "validated_by_id=?, validated_at=COALESCE(?, validated_at) WHERE id=?",
                       (client["id"], exam["id"] if exam else None, who["name"], who["id"],
                        None if t.get("validatedById") else rv["updatedAt"], t["id"]))
            if pol.get("deliveryMode", "controlled") == "automatic":
                deliver(alarm, client, who, exam, "retry")
                _set_delivery(t["id"], "delivered", None, who, delivered=True)
            else:
                _set_delivery(t["id"], "ready", f"Ready to send to {client['name']}")
            done += 1
    if done:
        import logging
        logging.getLogger("camview.tickets").info("Delivered %s operator-VALID alert(s) that were waiting for a client", done)
    return done
