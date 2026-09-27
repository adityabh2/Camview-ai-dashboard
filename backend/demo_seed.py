"""
demo_seed.py — populates the DEMO database (camview-demo.db) only.

Creates demo users for every role (password: demo), two clients, the demo
nomenclature, the alarm type dictionary and a realistic workflow history:

  Project 07: 40 validated by operators -> 15 approved -> 8 shared with Client A
              (2 acknowledged by the client), 3 awaiting approval, 1 withdrawn,
              12 marked invalid.
  Project 12: 5 shared with Client B.

Everything seeded here is demo data and is tagged {"seed": true} in the audit
trail. It never touches the live database.
"""

import random
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

import alarms as normalizer
import db
import mock_data
import nomenclature
import rbac
import workflow

DEMO_PASSWORD = "demo"

DEMO_USERS = [
    # id, name, email, role, client, scopes
    ("u-admin", "Aditi Rao", "admin@demo.camview", "super_admin", None, [("global", "*")]),
    ("u-itadmin", "Imran Sheikh", "it.admin@demo.camview", "admin", None, [("global", "*")]),
    ("u-manager", "Meera Iyer", "manager@demo.camview", "manager", None, [("global", "*")]),
    ("u-super", "Rohan Mehta", "supervisor@demo.camview", "supervisor", None, [("global", "*")]),
    ("u-super12", "Kavya Nair", "supervisor.p12@demo.camview", "supervisor", None, [("project", "12")]),
    ("u-op", "Arjun Verma", "operator@demo.camview", "operator", None, [("project", "7")]),
    ("u-op-tec", "Sana Khan", "operator.tec04@demo.camview", "operator", None, [("tc", "TC-0711")]),
    ("u-inv", "Vikram Das", "investigator@demo.camview", "investigator", None, [("project", "7")]),
    ("u-cadmin", "Anil Kapoor", "client.admin@client-a.demo", "client_admin", "client-a", []),
    ("u-cview", "Neha Gupta", "viewer@client-a.demo", "client_viewer", "client-a", []),
    ("u-cuser-b", "Rahul Bose", "user@client-b.demo", "client_user", "client-b", []),
]

CLIENTS = [
    ("client-a", "State Recruitment Board", ["7", "15"], "exams@srb.demo", {"showTicket": False}),
    ("client-b", "University Examinations Cell", ["12"], "cell@univ.demo", {"showTicket": True}),
    ("client-c", "National Nursing Council", ["21"], "exams@nnc.demo", {"showTicket": True}),
]


def _demo_exam(project_id):
    e = next((e for e in mock_data.DEMO_EXAMS if str(project_id) in e[4]), None)
    return {"id": e[0], "code": e[1], "name": e[2]} if e else None


def demo_users_enabled():
    import os
    return os.environ.get("CAMVIEW_DEMO_USERS", "0") == "1"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# Bump when the demo story changes (projects, clients, exams, tickets). An older demo
# database is refreshed on start: generated demo records are rebuilt; users, roles,
# settings and the append-only audit trail are kept. Never touches the live database.
DEMO_DATA_VERSION = 3
_DEMO_TABLES = ("ops_review", "publications", "assignments", "notes", "bookmarks", "notifications", "handovers",
                "escalations_fired", "client_messages", "tickets", "exams", "client_projects", "rule_versions",
                "alert_rules", "schedules", "reports")


def _reset_demo_records():
    with db.connect() as conn:
        for t in _DEMO_TABLES:
            conn.execute(f"DELETE FROM {t}")
    db.set_setting("exams_version", (db.get_setting("exams_version", 0) or 0) + 1)
    import exams as exams_mod
    exams_mod._cache["checked"] = 0
    db.set_setting("demo_seeded", None)


def seed():
    rbac.seed_roles()
    if db.get_setting("demo_seeded") and db.get_setting("demo_data_version") != DEMO_DATA_VERSION:
        _reset_demo_records()
    if db.get_setting("demo_seeded"):
        if db.get_setting("demo_hierarchy_version") != mock_data.HIERARCHY_VERSION:
            nomenclature.import_data(mock_data.hierarchy(), "json")      # refresh demo master data only
            db.set_setting("demo_hierarchy_version", mock_data.HIERARCHY_VERSION)
        return
    db.set_setting("demo_hierarchy_version", mock_data.HIERARCHY_VERSION)
    now = datetime.now(timezone.utc)

    # master data ---------------------------------------------------------------
    nomenclature.import_data(mock_data.hierarchy(), "json")
    with db.connect() as conn:
        for tid, (name, desc, sev) in mock_data.ALARM_TYPES.items():
            conn.execute("INSERT OR REPLACE INTO alarm_types (id, name, description, severity, icon, client_share_policy) "
                         "VALUES (?,?,?,?,?,?)", (tid, name, desc, sev, None, "never" if tid == 8 else "allowed"))
        for value, label, rank in ((1, "critical", 0), (2, "high", 1), (3, "medium", 2), (4, "low", 3)):
            conn.execute("INSERT OR REPLACE INTO priority_levels (value, label, rank, confirmed) VALUES (?,?,?,1)",
                         (value, label, rank))
        for cid, name, projects, contact, vis in CLIENTS:
            conn.execute("INSERT OR REPLACE INTO clients (id, name, status, contact, notification_prefs, visibility_policy, "
                         "created_at) VALUES (?,?,?,?,?,?,?)",
                         (cid, name, "active", contact, db.jdump({"inApp": True, "critical": True, "reports": True}),
                          db.jdump(vis), _iso(now - timedelta(days=30))))
            for p in projects:
                conn.execute("INSERT OR IGNORE INTO client_projects (client_id, project_id) VALUES (?,?)", (cid, p))
        pw = generate_password_hash(DEMO_PASSWORD)
        # Demo login accounts are off by default (only the configured admin exists);
        # CAMVIEW_DEMO_USERS=1 turns them on (used by the automated tests).
        for uid, name, email, role, client, scopes in (DEMO_USERS if demo_users_enabled() else []):
            conn.execute("INSERT OR REPLACE INTO users (id, name, email, password_hash, role_id, client_id, status, "
                         "created_at, is_demo) VALUES (?,?,?,?,?,?,?,?,1)",
                         (uid, name, email, pw, role, client, "active", _iso(now - timedelta(days=30))))
            for st, sv in scopes:
                conn.execute("INSERT OR IGNORE INTO user_scopes (user_id, scope_type, scope_value) VALUES (?,?,?)",
                             (uid, st, sv))

    workflow.set_policy({"longPendingMinutes": 60, "slaTargetMinutes": 30, "slaWarnMinutes": 15,
                         "escalation": [{"afterMinutes": 15, "roleId": "supervisor"},
                                        {"afterMinutes": 45, "roleId": "manager"}]}, None)

    # workflow history -------------------------------------------------------------
    rng = random.Random(7)
    names = {k: v[0] for k, v in mock_data.ALARM_TYPES.items()}
    import exams as exams_mod
    exams_mod.seed(mock_data.DEMO_EXAMS)
    plans = {7: "a", 12: "b", 15: "a2", 21: "c"}
    for project in mock_data.PROJECTS:
        plan = plans[project["projectId"]]
        items = [normalizer.normalize(i, names) for i in mock_data.generate_project_alarms(project, now=now)]
        for a in items:
            a["context"] = nomenclature.resolve(a["projectId"], a["cameraId"])
            a["cameraCode"] = (a["context"].get("camera") or {}).get("code")
            if a["context"].get("mapped"):
                a["cameraName"] = a["context"]["camera"]["name"]
            a["evidence"] = {"count": len(a.get("imageUrls") or []) + (1 if a.get("videoUrl") else 0)}
        # older, shareable alarms (type 8 is "never share"), mapped, with evidence
        pool = [a for a in items if a["lastActionType"] in (0, 1) and a["alarmType"] != 8
                and a["context"].get("mapped") and a["imageUrls"]
                and (now - datetime.fromisoformat(a["firstInstance"].replace("Z", "+00:00"))) > timedelta(hours=3)]
        rng.shuffle(pool)
        if plan == "a":
            _seed_project(items, pool, now, rng, "client-a", validated=40, ready=3, approved_only=7, shared=8,
                          acknowledged=2, withdrawn=1, invalid=12)
        elif plan == "a2":      # second exam of client A: validated, nothing sent yet (spec §164: client A sees 8)
            _seed_project(items, pool, now, rng, "client-a", validated=10, ready=2, approved_only=1, shared=0,
                          acknowledged=0, withdrawn=0, invalid=4)
        elif plan == "b":
            _seed_project(items, pool, now, rng, "client-b", validated=12, ready=1, approved_only=1, shared=5,
                          acknowledged=1, withdrawn=0, invalid=4)
        else:
            _seed_project(items, pool, now, rng, "client-c", validated=12, ready=1, approved_only=1, shared=4,
                          acknowledged=1, withdrawn=0, invalid=4)

    # rules, schedules, handover ------------------------------------------------------
    with db.connect() as conn:
        conn.execute("INSERT INTO alert_rules (name, enabled, event, conditions, scope, severity, recipients, channel, "
                     "escalation, action, template, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("Impersonation pending", 1, "alarm",
                      db.jdump([{"field": "alarmType", "op": "eq", "value": "2"},
                                {"field": "lastActionType", "op": "eq", "value": "0"}]),
                      "camera", "critical", db.jdump(["supervisor", "operator"]), "in_app",
                      db.jdump({"afterMinutes": 20, "roleId": "manager"}), "notify", "critical_alarm", "Aditi Rao",
                      _iso(now - timedelta(days=5))))
        conn.execute("INSERT INTO alert_rules (name, enabled, event, conditions, scope, severity, recipients, channel, "
                     "action, template, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("Strongroom activity", 1, "alarm",
                      db.jdump([{"field": "alarmType", "op": "eq", "value": "4"},
                                {"field": "lastActionType", "op": "eq", "value": "0"}]), "centre", "high",
                      db.jdump(["supervisor"]), "in_app", "notify", "critical_alarm", "Aditi Rao",
                      _iso(now - timedelta(days=5))))
        for name, phase, delta, template in (("Paper 1 — last 30 minutes", "BEFORE_EVENT_END", timedelta(hours=1), "event_end"),
                                             ("Paper 1 ends", "EVENT_END", timedelta(hours=1, minutes=30), "event_end"),
                                             ("Afternoon shift handover", "SHIFT_HANDOVER", timedelta(hours=2), "shift_handover")):
            conn.execute("INSERT INTO schedules (name, phase, at, template, recipients, enabled, created_by, created_at) "
                         "VALUES (?,?,?,?,?,1,?,?)", (name, phase, _iso(now + delta), template,
                                                       db.jdump(["operator", "supervisor"]), "Aditi Rao", _iso(now)))
        conn.execute("INSERT INTO handovers (from_shift, to_shift, project_id, notes, snapshot, created_by, created_by_name, "
                     "created_at) VALUES (?,?,?,?,?,?,?,?)",
                     ("Shift 2 Afternoon", "Shift 1 Morning", "7",
                      "Hall cameras at CTR-0412 were re-angled after repeated obstruction alerts. "
                      "Two impersonation alarms still under investigation.",
                      db.jdump({"openInvestigations": 2, "criticalPending": 1, "clientApprovals": 3, "openItems": 6}),
                      "u-super", "Rohan Mehta", _iso(now - timedelta(hours=14))))
    db.set_setting("demo_data_version", DEMO_DATA_VERSION)
    db.set_setting("demo_seeded", _iso(now))


def _seed_project(items, pool, now, rng, client_id, *, validated, ready, approved_only, shared, acknowledged,
                  withdrawn, invalid):
    operators = [("u-op", "Arjun Verma"), ("u-op-tec", "Sana Khan")] if client_id == "client-a" else \
        [("u-super12", "Kavya Nair")]
    approver = ("u-super", "Rohan Mehta") if client_id == "client-a" else ("u-manager", "Meera Iyer")
    chosen = pool[:validated]
    snap_keys = ("alarmId", "projectId", "cameraId", "cameraName", "alarmType", "alarmTypeName", "priority",
                 "priorityLevel", "lastActionType", "lastActionLabel", "firstInstance", "lastInstance",
                 "totalTimesReported", "ticketId", "shiftLabel", "suppressed", "imageUrls", "videoUrl", "context")

    def at(a, minutes):
        return _iso(datetime.fromisoformat(a["firstInstance"].replace("Z", "+00:00")) + timedelta(minutes=minutes))

    with db.connect() as conn:
        for a in chosen:
            op = operators[rng.randrange(len(operators))]
            snap = {k: a.get(k) for k in snap_keys}
            conn.execute("INSERT OR REPLACE INTO ops_review (alarm_id, status, updated_at, updated_by, snapshot, "
                         "first_reviewed_at, validated_by_id) VALUES (?,?,?,?,?,?,?)",
                         (a["alarmId"], "marked_valid", at(a, 12), op[1], db.jdump(snap), at(a, 6), op[0]))
            conn.execute("INSERT INTO audit_events (at, user_id, user_name, action, resource_type, resource_id, "
                         "old_value, new_value, project_id, details) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (at(a, 12), op[0], op[1], "review.mark_valid", "alarm", a["alarmId"], "unreviewed",
                          "marked_valid", str(a["projectId"]), db.jdump({"seed": True})))
        idx = 0
        plan = [("ready_for_review", ready), ("approved", approved_only), ("shared", shared), ("withdrawn", withdrawn)]
        acked = 0
        for status, n in plan:
            for a in chosen[idx: idx + n]:
                op = next(o for o in operators)
                snap = {k: a.get(k) for k in snap_keys}
                snap["exam"] = _demo_exam(a["projectId"])
                images = a.get("imageUrls") or []
                evidence = [{"kind": "image", "index": i, "shared": i == 0} for i in range(len(images))]
                if a.get("videoUrl"):
                    evidence.append({"kind": "video", "index": 0, "shared": True})
                summary = workflow.client_safe_summary(a, ["centre", "camera"])
                row = {
                    "alarm_id": a["alarmId"], "client_id": client_id, "status": status, "project_id": str(a["projectId"]),
                    "requested_by": op[1], "requested_by_id": op[0], "requested_at": at(a, 15),
                    "approved_by": approver[1] if status in ("approved", "shared", "withdrawn") else None,
                    "approved_by_id": approver[0] if status in ("approved", "shared", "withdrawn") else None,
                    "approved_at": at(a, 25) if status in ("approved", "shared", "withdrawn") else None,
                    "shared_by": approver[1] if status in ("shared", "withdrawn") else None,
                    "shared_by_id": approver[0] if status in ("shared", "withdrawn") else None,
                    "shared_at": at(a, 30) if status in ("shared", "withdrawn") else None,
                    "withdrawn_by": approver[1] if status == "withdrawn" else None,
                    "withdrawn_at": at(a, 90) if status == "withdrawn" else None,
                    "withdraw_reason": "Duplicate of another shared alert" if status == "withdrawn" else None,
                    "client_summary": summary if status in ("shared", "withdrawn") else None,
                    "evidence": db.jdump(evidence) if status in ("shared", "withdrawn") else None,
                    "share_context": db.jdump(["project", "tc", "centre", "camera"]) if status in ("shared", "withdrawn") else None,
                    "snapshot": db.jdump(snap), "updated_at": at(a, 30),
                }
                if status == "shared" and acked < acknowledged:
                    acked += 1
                    row.update(viewed_at=at(a, 60), acknowledged_by="Anil Kapoor" if client_id == "client-a" else "Rahul Bose",
                               acknowledged_at=at(a, 75), ack_comment="Noted. Centre superintendent informed.")
                cols = ", ".join(row)
                conn.execute(f"INSERT OR REPLACE INTO publications ({cols}) VALUES ({', '.join('?' for _ in row)})",
                             list(row.values()))
                steps = [("share.request", op, 15, "internal", "ready_for_review")]
                if row["approved_at"]:
                    steps.append(("share.approve", approver, 25, "ready_for_review", "approved"))
                if row["shared_at"]:
                    steps.append(("share.publish", approver, 30, "approved", "shared"))
                if row["withdrawn_at"]:
                    steps.append(("share.withdraw", approver, 90, "shared", "withdrawn"))
                for action, who, m, old, new in steps:
                    conn.execute("INSERT INTO audit_events (at, user_id, user_name, action, resource_type, resource_id, "
                                 "old_value, new_value, project_id, client_id, details) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (at(a, m), who[0], who[1], action, "alarm", a["alarmId"], old, new, str(a["projectId"]),
                                  client_id, db.jdump({"seed": True})))
            idx += n
        # operator-invalidated alarms
        others = [a for a in items if a["alarmId"] not in {c["alarmId"] for c in chosen}
                  and a["lastActionType"] in (0, 2)
                  and (now - datetime.fromisoformat(a["firstInstance"].replace("Z", "+00:00"))) > timedelta(hours=2)]
        for a in others[:invalid]:
            op = operators[rng.randrange(len(operators))]
            snap = {k: a.get(k) for k in snap_keys}
            conn.execute("INSERT OR REPLACE INTO ops_review (alarm_id, status, updated_at, updated_by, snapshot, "
                         "first_reviewed_at, validated_by_id) VALUES (?,?,?,?,?,?,?)",
                         (a["alarmId"], "marked_invalid", at(a, 9), op[1], db.jdump(snap), at(a, 9), op[0]))
            conn.execute("INSERT INTO audit_events (at, user_id, user_name, action, resource_type, resource_id, old_value, "
                         "new_value, project_id, note, details) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (at(a, 9), op[0], op[1], "review.mark_invalid", "alarm", a["alarmId"], "unreviewed",
                          "marked_invalid", str(a["projectId"]), "Shadow / reflection — no person present.",
                          db.jdump({"seed": True})))
        # a couple of internal notes (never visible to clients)
        for a in chosen[:4]:
            conn.execute("INSERT INTO notes (alarm_id, kind, body, author_id, author_name, created_at) VALUES (?,?,?,?,?,?)",
                         (a["alarmId"], "internal", "Invigilator confirmed on radio. Candidate seat number noted in "
                                                    "internal log (not for client).", operators[0][0], operators[0][1],
                          at(a, 10)))
        # every VALID alert has exactly one ticket; delivery status follows the demo history
        exam = next((e for e in mock_data.DEMO_EXAMS if str(chosen[0]["projectId"]) in e[4]), None) if chosen else None
        pub_status = {r["alarm_id"]: r["status"] for r in
                      conn.execute("SELECT alarm_id, status FROM publications WHERE client_id=?", (client_id,))}
        for a in chosen:
            st = pub_status.get(a["alarmId"])
            delivery = {"shared": "delivered", "withdrawn": "withdrawn"}.get(st, "ready")
            snap = {k: a.get(k) for k in snap_keys}
            snap["exam"] = {"id": exam[0], "code": exam[1], "name": exam[2]} if exam else None
            conn.execute("INSERT OR IGNORE INTO tickets (alarm_id, camview_ticket_id, exam_id, client_id, project_id, status, "
                         "result, validated_by, validated_by_id, validated_at, delivery_status, delivery_note, delivered_at, "
                         "delivered_by, snapshot, created_at, updated_at) VALUES (?,?,?,?,?,'open','valid',?,?,?,?,?,?,?,?,?,?)",
                         (a["alarmId"], None if a.get("ticketId") is None else str(a["ticketId"]), exam[0] if exam else None,
                          client_id, str(a["projectId"]), operators[0][1], operators[0][0], at(a, 12), delivery,
                          None if delivery != "ready" else "Ready to send", at(a, 30) if delivery == "delivered" else None,
                          approver[1] if delivery == "delivered" else None, db.jdump(snap), at(a, 12), at(a, 30)))
        conn.execute("UPDATE tickets SET ref = 'TKT-' || substr('000000' || id, -6) WHERE ref IS NULL")
