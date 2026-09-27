"""
notify.py — in-app notifications, scheduled/event alerts and escalation.

Only real application events create notifications (a share, an approval
request, an assignment, a new critical alarm the backend actually received,
a failed Camview refresh, a due schedule, a configured escalation step).

Delivery channel: IN-APP only. Email / SMS / push are not integrated, so
they are shown as unavailable in the UI instead of pretending to send.
"""

from datetime import datetime, timezone

import db

CATEGORIES = ("operational", "investigation", "approval", "client", "system")
CHANNELS = {"in_app": True, "email": False, "sms": False, "push": False}


SEVERITIES = ("critical", "high", "warning", "info")
PREF_DEFAULTS = {"enabled": True, "criticalOnly": False, "sound": False, "browser": False}


def get_prefs(user_id):
    r = db.one("SELECT prefs FROM user_prefs WHERE user_id=?", (user_id,))
    return {**PREF_DEFAULTS, **(db.jload(r["prefs"], {}) if r else {})}


def set_prefs(user_id, updates):
    prefs = get_prefs(user_id)
    for k in PREF_DEFAULTS:
        if k in (updates or {}):
            prefs[k] = bool(updates[k])
    db.execute("INSERT INTO user_prefs (user_id, prefs) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET prefs=excluded.prefs",
               (user_id, db.jdump(prefs)))
    return prefs


def _wants(user_id, severity):
    """Critical notifications are always delivered (they can't be switched off or snoozed)."""
    if severity == "critical":
        return True
    p = get_prefs(user_id)
    return p["enabled"] and not p["criticalOnly"]


def to_users(user_ids, category, title, body=None, link=None, dedupe=None, severity="info"):
    severity = severity if severity in SEVERITIES else "info"
    now = db.now_iso()
    with db.connect() as conn:
        for uid in set(user_ids):
            if not _wants(uid, severity):
                continue
            conn.execute("INSERT OR IGNORE INTO notifications (user_id, category, title, body, link, created_at, dedupe_key, "
                         "severity) VALUES (?,?,?,?,?,?,?,?)",
                         (uid, category, title[:200], (body or "")[:1000], link, now, dedupe, severity))


def _active_users():
    import rbac
    out = []
    for r in db.rows("SELECT id FROM users WHERE status='active'"):
        u = rbac.load_user(r["id"])
        if u:
            out.append(u)
    return out


def expand_recipients(recipients):
    """Rule/schedule recipients may be role ids or 'group:<id>' (recipient groups).
    Returns (role_ids, user_ids)."""
    roles, users = set(), set()
    for r in recipients or []:
        if isinstance(r, str) and r.startswith("group:"):
            g = db.one("SELECT roles, user_ids FROM recipient_groups WHERE id=?", (r[6:],))
            if g:
                roles |= set(db.jload(g["roles"], []) or [])
                users |= set(db.jload(g["user_ids"], []) or [])
        elif r:
            roles.add(r)
    return roles, users


def to_permission(perm, category, title, body=None, link=None, project_id=None, dedupe=None, exclude=None,
                  alarm=None, role_ids=None, user_ids=None, severity="info"):
    """Internal users holding `perm` (and notification.view) whose scope covers the project/alarm."""
    import rbac
    ids = []
    for u in _active_users():
        if u["audience"] != "internal" or u["id"] == exclude:
            continue
        if perm and perm not in u["permissions"]:
            continue
        if (role_ids or user_ids) and u["roleId"] not in (role_ids or ()) and u["id"] not in (user_ids or ()):
            continue
        if "notification.view" not in u["permissions"]:
            continue
        if alarm is not None and not rbac.alarm_in_scope(u, alarm):
            continue
        if alarm is None and project_id not in (None, "") and not rbac.project_allowed(u, project_id):
            continue
        ids.append(u["id"])
    to_users(ids, category, title, body, link, dedupe, severity)
    return ids


def to_client(client_id, category, title, body=None, link=None, dedupe=None, severity="info"):
    prefs = db.jload((db.one("SELECT notification_prefs FROM clients WHERE id=?", (client_id,)) or {})
                     .get("notification_prefs"), {}) or {}
    if prefs.get("inApp") is False:
        return []
    ids = [u["id"] for u in _active_users()
           if u["audience"] == "client" and u["clientId"] == client_id and "notification.view" in u["permissions"]]
    to_users(ids, category, title, body, link, dedupe, severity)
    return ids


def list_for(user_id, include_archived=False, include_snoozed=False, limit=200):
    now = db.now_iso()
    q = "SELECT * FROM notifications WHERE user_id=?"
    params = [user_id]
    if not include_archived:
        q += " AND archived_at IS NULL"
    if not include_snoozed:
        q += " AND (snoozed_until IS NULL OR snoozed_until <= ?)"
        params.append(now)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [{"id": r["id"], "category": r["category"], "title": r["title"], "body": r["body"], "link": r["link"],
             "severity": r["severity"] or "info", "createdAt": r["created_at"], "read": bool(r["read_at"]),
             "archived": bool(r["archived_at"]),
             "snoozedUntil": r["snoozed_until"] if r["snoozed_until"] and r["snoozed_until"] > now else None}
            for r in db.rows(q, params)]


def unread_count(user_id):
    return db.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read_at IS NULL AND archived_at IS NULL "
                  "AND (snoozed_until IS NULL OR snoozed_until <= ?)", (user_id, db.now_iso()))["n"]


def mark(user_id, ids, field):
    now = db.now_iso()
    with db.connect() as conn:
        if ids == "all":
            conn.execute(f"UPDATE notifications SET {field}=? WHERE user_id=? AND {field} IS NULL", (now, user_id))
        else:
            for i in ids:
                conn.execute(f"UPDATE notifications SET {field}=? WHERE user_id=? AND id=?", (now, user_id, int(i)))


def snooze(user_id, ids, until_iso):
    """Snooze non-critical notifications. Critical ones are never snoozed."""
    skipped = 0
    with db.connect() as conn:
        for i in ids:
            r = conn.execute("SELECT severity FROM notifications WHERE user_id=? AND id=?", (user_id, int(i))).fetchone()
            if not r:
                continue
            if (r["severity"] or "info") == "critical":
                skipped += 1
                continue
            conn.execute("UPDATE notifications SET snoozed_until=? WHERE user_id=? AND id=?", (until_iso, user_id, int(i)))
    return skipped


def notify_watchers(fresh_alarms):
    """New alarms on a watched camera / location / project notify the watcher (scope-checked)."""
    import rbac
    if not fresh_alarms:
        return
    watches = db.rows("SELECT * FROM watchlist WHERE entity_type != 'alarm'")
    for w in watches:
        u = rbac.load_user(w["user_id"])
        if not u or u["audience"] != "internal" or "notification.view" not in u["permissions"]:
            continue
        for a in fresh_alarms:
            if watch_matches(w, a) and rbac.alarm_in_scope(u, a):
                to_users([u["id"]], "operational", f"Watched {w['entity_type']} {w['label'] or w['entity_id']}: new alarm",
                         f"{a['alarmId']} · {a.get('alarmTypeName')} · {a.get('priority')}",
                         f"#/alerts/{a['alarmId']}?projectId={a.get('projectId')}", dedupe=f"watch:{w['entity_type']}:{w['entity_id']}:{a['alarmId']}",
                         severity="high" if a.get("priority") == "critical" else "info")


def watch_matches(w, a):
    t, v = w["entity_type"], str(w["entity_id"])
    if t == "alarm":
        return a.get("alarmId") == v
    if t == "camera":
        return str(a.get("cameraId")) == v
    if t == "project":
        return str(a.get("projectId")) == v
    node = (a.get("context") or {}).get(t)
    return bool(node) and (node.get("id") == v or (t != "room" and node.get("code") == v))


# ---------------------------------------------------------------------------
# Scheduled / event-phase alerts
# ---------------------------------------------------------------------------

PHASES = ["PRE_EVENT", "EVENT_START", "DURING_EVENT", "BEFORE_EVENT_END", "EVENT_END", "POST_EVENT", "SHIFT_HANDOVER"]
TEMPLATES = {
    "critical_alarm": "Critical alarm requires immediate review.",
    "repeated_alarm": "Repeated activity detected — please review the related camera.",
    "pending_review": "Alarms are waiting for review.",
    "approval_required": "Alarms are waiting for supervisor approval.",
    "client_sharing": "Approved alarms are ready to be shared with the client.",
    "event_start": "The event has started. Monitoring is active.",
    "event_end": "The event has ended. Complete pending reviews and handover.",
    "shift_handover": "Shift handover is due. Record open items.",
    "evidence_available": "New evidence is available for review.",
    "system_issue": "Data refresh problems detected — check System Status.",
}


def check_schedules():
    """Fires due schedules once. Called from the status tick."""
    now = datetime.now(timezone.utc)
    for s in db.rows("SELECT * FROM schedules WHERE enabled=1 AND fired_at IS NULL"):
        try:
            at = datetime.fromisoformat(s["at"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at > now:
            continue
        roles, extra_users = expand_recipients(db.jload(s["recipients"], []) or [])
        msg = s["message"] or TEMPLATES.get(s["template"] or "", s["name"])
        title = f"{s['phase'].replace('_', ' ').title()}: {s['name']}"
        ids = [u["id"] for u in _active_users() if (u["roleId"] in roles or u["id"] in extra_users)
               and "notification.view" in u["permissions"]]
        to_users(ids, "operational", title, msg, "#/notifications", dedupe=f"schedule:{s['id']}")
        db.execute("UPDATE schedules SET fired_at=? WHERE id=?", (db.now_iso(), s["id"]))
        db.audit("schedule.fired", None, "schedule", s["id"], None, title, details={"recipients": len(ids)})


RULE_LOOKBACK_MINUTES = 24 * 60   # a newly enabled rule doesn't notify about old history


def check_rules(alarms, rules):
    """User-defined alert rules: notify the rule's recipient roles once per
    matching alarm, and apply the rule's escalation step once when due."""
    import intelligence
    for rule in rules:
        raw = db.jload(rule.get("recipients"), []) if isinstance(rule.get("recipients"), str) else (rule.get("recipients") or [])
        roles, extra_users = expand_recipients(raw)
        esc = db.jload(rule.get("escalation"), None) if isinstance(rule.get("escalation"), str) else rule.get("escalation")
        for a in alarms:
            age = a.get("ageMinutes")
            if age is None or age > RULE_LOOKBACK_MINUTES or not intelligence.rule_matches(rule, a):
                continue
            cam = a.get("cameraCode") or f"camera {a.get('cameraId')}"
            if roles or extra_users:
                to_permission(None, "operational", f"{rule['name']}: {a['alarmId']}",
                              f"{a.get('alarmTypeName')} at {cam} matched rule “{rule['name']}”.",
                              f"#/alerts/{a['alarmId']}?projectId={a.get('projectId')}", alarm=a, role_ids=roles, user_ids=extra_users,
                              dedupe=f"rule:{rule['id']}:{a['alarmId']}",
                              severity=rule.get("severity") if rule.get("severity") in SEVERITIES else "warning")
            if esc and esc.get("roleId") and esc.get("afterMinutes") not in (None, ""):
                try:
                    due = age >= float(esc["afterMinutes"])
                except (TypeError, ValueError):
                    due = False
                if due:
                    to_permission(None, "operational", f"Escalation — {rule['name']}: {a['alarmId']}",
                                  f"Still matching after {int(age)} min (rule escalation after {esc['afterMinutes']} min).",
                                  f"#/alerts/{a['alarmId']}?projectId={a.get('projectId')}", alarm=a, role_ids=[esc["roleId"]],
                                  dedupe=f"rule-esc:{rule['id']}:{a['alarmId']}", severity="high")


def check_escalations(alarms, pol):
    """Configured escalation steps for critical pending alarms (none by default)."""
    steps = pol.get("escalation") or []
    if not steps:
        return
    now = datetime.now(timezone.utc)
    for a in alarms:
        if a.get("priority") != "critical" or a.get("lastActionType") != 0 or \
                (a.get("review") or {}).get("status", "unreviewed") != "unreviewed":
            continue
        try:
            raised = datetime.fromisoformat(a["firstInstance"].replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError):
            continue
        age = (now - raised).total_seconds() / 60
        for i, step in enumerate(steps):
            try:
                after = float(step.get("afterMinutes"))
            except (TypeError, ValueError):
                continue
            if age < after:
                continue
            if db.one("SELECT 1 FROM escalations_fired WHERE alarm_id=? AND step=?", (a["alarmId"], i)):
                continue
            db.execute("INSERT OR IGNORE INTO escalations_fired (alarm_id, step, fired_at) VALUES (?,?,?)",
                       (a["alarmId"], i, db.now_iso()))
            to_permission(None, "operational", f"Escalation (step {i + 1}): {a['alarmId']}",
                          f"Critical alarm pending for {int(age)} min — {a.get('alarmTypeName')} at "
                          f"{(a.get('context') or {}).get('camera', {}).get('code', 'camera ' + str(a.get('cameraId')))}.",
                          f"#/alerts/{a['alarmId']}?projectId={a.get('projectId')}", alarm=a, role_ids=[step.get("roleId")],
                          dedupe=f"esc:{a['alarmId']}:{i}", severity="high")
