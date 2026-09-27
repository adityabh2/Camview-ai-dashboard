"""
db.py — Command Center's own persistence (SQLite).

Camview's API is read-only, so everything the Command Center *adds* on top
of the alarm feed lives here:

  users / roles / role_permissions / user_scopes      RBAC
  clients / client_projects                           client management
  nomenclature / alarm_types / priority_levels        master data
  ops_review                                          operator validation (app-level)
  publications                                        client visibility / sharing workflow
  assignments / notes / bookmarks                     investigation workflow
  notifications / alert_rules / schedules             notification architecture
  reports / handovers / saved_views / settings        misc
  audit_events                                        append-only audit trail

None of this changes Camview's own record (alarmState / lastActionType).
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import changes

DB_PATH = os.environ.get("CAMVIEW_DB_PATH", os.path.join(os.path.dirname(__file__), "camview.db"))

# Writes made inside `quiet()` do not announce a data change (audit rows written when someone merely
# views something, heartbeat timestamps…): they change nothing a screen would need to re-read.
_local = threading.local()


@contextmanager
def quiet():
    _local.quiet = getattr(_local, "quiet", 0) + 1
    try:
        yield
    finally:
        _local.quiet -= 1


@contextmanager
def batch(reason="batch"):
    """Many writes, one announcement: background work (auto-delivery of hundreds of tickets, rule checks)
    would otherwise move the data version on every row and make every screen and cache re-read hundreds
    of times. Inside a batch nothing is announced; at the end, one change if anything was written."""
    outer = getattr(_local, "batch", None)
    if outer is not None:                           # nested: the outer batch announces
        yield
        return
    _local.batch = False
    _local.quiet = getattr(_local, "quiet", 0) + 1
    try:
        yield
    finally:
        _local.quiet -= 1
        wrote, _local.batch = _local.batch, None
        if wrote:
            changes.bump(reason)

# Operator review actions -> resulting ops_review.status (None = note only)
ALLOWED_ACTIONS = {
    "acknowledge": "acknowledged",
    "mark_valid": "marked_valid",
    "mark_invalid": "marked_invalid",
    "mark_exception": "marked_exception",
    "reopen": "unreviewed",
    "note": None,
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        changed = conn.total_changes
        conn.commit()
    finally:
        conn.close()
    # Any committed INSERT / UPDATE / DELETE is a data change the screens must learn about
    if changed:
        if getattr(_local, "batch", None) is not None:
            _local.batch = True                     # announced once when the batch ends
        elif not getattr(_local, "quiet", 0):
            changes.bump("db")


_connect = connect  # backwards-compatible name


def rows(sql, params=()):
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(sql, params=()):
    with connect() as conn:
        r = conn.execute(sql, params).fetchone()
        return dict(r) if r else None


def execute(sql, params=()):
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def jload(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def jdump(value):
    return None if value is None else json.dumps(value, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_review (
    alarm_id          TEXT PRIMARY KEY,
    status            TEXT NOT NULL DEFAULT 'unreviewed',
    updated_at        TEXT,
    updated_by        TEXT,
    snapshot          TEXT,
    first_reviewed_at TEXT,
    validated_by_id   TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alarm_id TEXT NOT NULL, action TEXT NOT NULL,
    note TEXT, operator TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT NOT NULL,
    user_id       TEXT,
    user_name     TEXT,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    old_value     TEXT,
    new_value     TEXT,
    project_id    TEXT,
    client_id     TEXT,
    note          TEXT,
    details       TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ev_at ON audit_events(at);
CREATE INDEX IF NOT EXISTS idx_audit_ev_res ON audit_events(resource_type, resource_id);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
    audience TEXT NOT NULL DEFAULT 'internal', is_system INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE, permission TEXT NOT NULL,
    PRIMARY KEY (role_id, permission)
);
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    contact TEXT, notification_prefs TEXT, visibility_policy TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS client_projects (
    client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE, project_id TEXT NOT NULL,
    PRIMARY KEY (client_id, project_id)
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT, role_id TEXT NOT NULL REFERENCES roles(id),
    client_id TEXT REFERENCES clients(id), status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT, last_login_at TEXT, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_scopes (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL, scope_value TEXT NOT NULL,
    PRIMARY KEY (user_id, scope_type, scope_value)
);

CREATE TABLE IF NOT EXISTS nomenclature (
    id TEXT PRIMARY KEY, level TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
    parent_id TEXT REFERENCES nomenclature(id) ON DELETE CASCADE,
    external_id TEXT, meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_nom_parent ON nomenclature(parent_id);
CREATE INDEX IF NOT EXISTS idx_nom_ext ON nomenclature(level, external_id);
CREATE TABLE IF NOT EXISTS alarm_types (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, icon TEXT,
    severity TEXT, workflow TEXT, client_share_policy TEXT NOT NULL DEFAULT 'allowed'
);
CREATE TABLE IF NOT EXISTS priority_levels (
    value INTEGER PRIMARY KEY, label TEXT NOT NULL, rank INTEGER NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS publications (
    alarm_id TEXT NOT NULL, client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    project_id TEXT,
    requested_by TEXT, requested_by_id TEXT, requested_at TEXT, approved_by_id TEXT, shared_by_id TEXT,
    approved_by TEXT, approved_at TEXT,
    shared_by TEXT, shared_at TEXT,
    withdrawn_by TEXT, withdrawn_at TEXT, withdraw_reason TEXT,
    client_summary TEXT, evidence TEXT, share_context TEXT, snapshot TEXT,
    viewed_at TEXT, acknowledged_by TEXT, acknowledged_at TEXT, ack_comment TEXT,
    updated_at TEXT,
    PRIMARY KEY (alarm_id, client_id)
);
CREATE INDEX IF NOT EXISTS idx_pub_status ON publications(status);

CREATE TABLE IF NOT EXISTS assignments (
    alarm_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by TEXT, assigned_at TEXT, snapshot TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alarm_id TEXT NOT NULL,
    kind TEXT NOT NULL, body TEXT NOT NULL, author_id TEXT, author_name TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_alarm ON notes(alarm_id);
CREATE TABLE IF NOT EXISTS bookmarks (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, alarm_id TEXT NOT NULL,
    created_at TEXT, snapshot TEXT, PRIMARY KEY (user_id, alarm_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL, title TEXT NOT NULL, body TEXT, link TEXT,
    created_at TEXT NOT NULL, read_at TEXT, archived_at TEXT, dedupe_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, archived_at, read_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_dedupe ON notifications(user_id, dedupe_key);

CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    event TEXT NOT NULL DEFAULT 'alarm', conditions TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'camera',
    severity TEXT NOT NULL DEFAULT 'warning', recipients TEXT, channel TEXT NOT NULL DEFAULT 'in_app',
    escalation TEXT, action TEXT NOT NULL DEFAULT 'notify', template TEXT,
    created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phase TEXT NOT NULL,
    at TEXT NOT NULL, template TEXT, message TEXT, recipients TEXT,
    enabled INTEGER NOT NULL DEFAULT 1, fired_at TEXT, created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, type TEXT NOT NULL,
    audience TEXT NOT NULL, client_id TEXT REFERENCES clients(id) ON DELETE CASCADE,
    params TEXT, data TEXT, generated_by TEXT, generated_by_name TEXT, generated_at TEXT,
    shared_with_client INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS handovers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, from_shift TEXT, to_shift TEXT, project_id TEXT,
    notes TEXT, snapshot TEXT, created_by TEXT, created_by_name TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL, route TEXT NOT NULL, query TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS escalations_fired (
    alarm_id TEXT NOT NULL, step INTEGER NOT NULL, fired_at TEXT, PRIMARY KEY (alarm_id, step)
);
CREATE TABLE IF NOT EXISTS watchlist (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    label TEXT, project_id TEXT, created_at TEXT, PRIMARY KEY (user_id, entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, prefs TEXT
);
CREATE TABLE IF NOT EXISTS client_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alarm_id TEXT NOT NULL, client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    author_id TEXT, author_name TEXT, audience TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cmsg ON client_messages(alarm_id, client_id);
CREATE TABLE IF NOT EXISTS rule_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id INTEGER NOT NULL, version INTEGER NOT NULL, status TEXT,
    snapshot TEXT, summary TEXT, changed_by TEXT, changed_at TEXT
);
CREATE TABLE IF NOT EXISTS exams (
    id TEXT PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
    client_id TEXT REFERENCES clients(id) ON DELETE SET NULL,
    project_ids TEXT NOT NULL, start_date TEXT, end_date TEXT,
    status TEXT NOT NULL DEFAULT 'active', created_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE,
    alarm_id TEXT NOT NULL UNIQUE,              -- one alert = one ticket (idempotent)
    camview_ticket_id TEXT,
    exam_id TEXT, client_id TEXT, project_id TEXT,
    status TEXT NOT NULL,                       -- open | cancelled
    result TEXT,                                -- valid (tickets are only created for VALID)
    validated_by TEXT, validated_by_id TEXT, validated_at TEXT,
    delivery_status TEXT,                       -- delivered | ready | not_deliverable | withdrawn
    delivery_note TEXT, delivered_at TEXT, delivered_by TEXT,
    snapshot TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_client ON tickets(client_id, delivery_status);
CREATE TABLE IF NOT EXISTS recipient_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT, roles TEXT, user_ids TEXT, created_at TEXT
);
-- camera / recording health from a REAL source (push adapter); never derived from alarm names
CREATE TABLE IF NOT EXISTS camera_health (
    project_id TEXT NOT NULL, camera_id TEXT NOT NULL,
    camera_state TEXT, recording_state TEXT, stream_state TEXT,
    last_heartbeat_at TEXT, last_recording_at TEXT,
    last_event TEXT, last_event_at TEXT, source TEXT, updated_at TEXT,
    PRIMARY KEY (project_id, camera_id)
);
"""

_COLUMN_MIGRATIONS = {
    "notifications": {"severity": "TEXT", "snoozed_until": "TEXT"},
    "users": {"prev_login_at": "TEXT", "pw_changed_at": "TEXT"},
    "alert_rules": {"status": "TEXT", "version": "INTEGER", "updated_at": "TEXT", "updated_by": "TEXT"},
    "camera_health": {"raw_status": "TEXT"},
}


def init_db():
    # WAL: readers never wait for a writer (background auto-share must not stall page loads).
    # Persistent per database file; harmless if the filesystem refuses it.
    try:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        c.close()
    except sqlite3.DatabaseError:
        pass
    with connect() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ops_review)")}
        for col in ("snapshot", "first_reviewed_at", "validated_by_id"):
            if col not in cols:
                conn.execute(f"ALTER TABLE ops_review ADD COLUMN {col} TEXT")
        for table, new_cols in _COLUMN_MIGRATIONS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in new_cols.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        conn.execute("UPDATE alert_rules SET status = CASE WHEN enabled=1 THEN 'active' ELSE 'disabled' END "
                     "WHERE status IS NULL")
        conn.execute("UPDATE alert_rules SET version = 1 WHERE version IS NULL")
        # One-time migration of the old review trail into the append-only audit table.
        migrated = conn.execute("SELECT value FROM settings WHERE key='migrated_audit_log'").fetchone()
        if not migrated:
            for r in conn.execute("SELECT alarm_id, action, note, operator, created_at FROM audit_log ORDER BY id"):
                conn.execute(
                    "INSERT INTO audit_events (at, user_name, action, resource_type, resource_id, note, details) "
                    "VALUES (?, ?, ?, 'alarm', ?, ?, ?)",
                    (r["created_at"], r["operator"], "review." + r["action"], r["alarm_id"], r["note"],
                     jdump({"migrated": True})))
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('migrated_audit_log', '1')")


# --------------------------------------------------------------------------
# Audit (append-only)
# --------------------------------------------------------------------------

def audit(action, user=None, resource_type=None, resource_id=None, old=None, new=None,
          project_id=None, client_id=None, note=None, details=None):
    with quiet():                                   # an audit row on its own changes nothing on screen
        execute(
            "INSERT INTO audit_events (at, user_id, user_name, action, resource_type, resource_id, old_value, "
            "new_value, project_id, client_id, note, details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), (user or {}).get("id"), (user or {}).get("name"), action, resource_type,
             None if resource_id is None else str(resource_id),
             None if old is None else (old if isinstance(old, str) else jdump(old)),
             None if new is None else (new if isinstance(new, str) else jdump(new)),
             None if project_id is None else str(project_id), client_id, note, jdump(details)))


def audit_query(filters=None, limit=200, offset=0):
    filters = filters or {}
    where, params = ["1=1"], []
    for key, col in (("action", "action"), ("user", "user_name"), ("resourceType", "resource_type"),
                     ("resourceId", "resource_id"), ("projectId", "project_id"), ("clientId", "client_id")):
        if filters.get(key):
            if key == "action":
                where.append("action LIKE ?")
                params.append(filters[key] + "%")
            elif key == "user":
                where.append("user_name LIKE ?")
                params.append(f"%{filters[key]}%")
            else:
                where.append(f"{col} = ?")
                params.append(str(filters[key]))
    if filters.get("since"):
        where.append("at >= ?")
        params.append(filters["since"])
    if filters.get("until"):
        where.append("at < ?")
        params.append(filters["until"])
    total = one(f"SELECT COUNT(*) AS n FROM audit_events WHERE {' AND '.join(where)}", params)["n"]
    items = rows(f"SELECT * FROM audit_events WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ? OFFSET ?",
                 params + [limit, offset])
    for it in items:
        it["details"] = jload(it["details"], {})
    return total, items


# --------------------------------------------------------------------------
# Operator review (validation) — app-level, separate from Camview's state
# --------------------------------------------------------------------------

def bulk_get_review_status(alarm_ids):
    return {k: v["status"] for k, v in reviews_for(alarm_ids).items()}


def apply_action(alarm_id, action, operator, note=None, snapshot=None, user=None, project_id=None):
    """Records an operator review action. `operator` is the display name;
    `user` (dict) is the authenticated user when available."""
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unknown action '{action}'. Allowed: {sorted(ALLOWED_ACTIONS)}")
    if not operator or not str(operator).strip():
        raise ValueError("An operator name is required for any action (for the audit trail).")
    operator = str(operator).strip()
    now = now_iso()
    new_status = ALLOWED_ACTIONS[action]
    snapshot_json = jdump(snapshot) if isinstance(snapshot, dict) and snapshot else None
    first_review = now if action not in ("reopen", "note") else None
    validator = (user or {}).get("id") if action in ("mark_valid", "mark_invalid", "mark_exception") else None

    with connect() as conn:
        before = conn.execute("SELECT status FROM ops_review WHERE alarm_id = ?", (alarm_id,)).fetchone()
        if new_status is not None:
            conn.execute("""
                INSERT INTO ops_review (alarm_id, status, updated_at, updated_by, snapshot, first_reviewed_at, validated_by_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alarm_id) DO UPDATE SET
                    status=excluded.status, updated_at=excluded.updated_at, updated_by=excluded.updated_by,
                    snapshot=COALESCE(excluded.snapshot, ops_review.snapshot),
                    first_reviewed_at=COALESCE(ops_review.first_reviewed_at, excluded.first_reviewed_at),
                    validated_by_id=CASE WHEN excluded.validated_by_id IS NOT NULL THEN excluded.validated_by_id
                                         WHEN excluded.status IN ('unreviewed','acknowledged') THEN NULL
                                         ELSE ops_review.validated_by_id END
            """, (alarm_id, new_status, now, operator, snapshot_json, first_review, validator))
        elif snapshot_json:
            conn.execute("UPDATE ops_review SET snapshot = ? WHERE alarm_id = ?", (snapshot_json, alarm_id))
        conn.execute(
            "INSERT INTO audit_events (at, user_id, user_name, action, resource_type, resource_id, old_value, "
            "new_value, project_id, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (now, (user or {}).get("id"), operator, "review." + action, "alarm", alarm_id,
             before["status"] if before else "unreviewed", new_status,
             None if project_id is None else str(project_id), (note or "").strip() or None))
        row = conn.execute("SELECT status, updated_at, updated_by FROM ops_review WHERE alarm_id = ?",
                           (alarm_id,)).fetchone()
    return {
        "alarmId": alarm_id,
        "status": row["status"] if row else "unreviewed",
        "updatedAt": row["updated_at"] if row else None,
        "updatedBy": row["updated_by"] if row else None,
    }


def get_audit_trail(alarm_id):
    """Every audit event about one alarm, newest first."""
    out = []
    for r in rows("SELECT * FROM audit_events WHERE resource_type='alarm' AND resource_id=? ORDER BY id DESC",
                  (alarm_id,)):
        action = r["action"]
        out.append({
            "action": action[7:] if action.startswith("review.") else action,
            "note": r["note"], "operator": r["user_name"], "created_at": r["at"],
            "oldValue": r["old_value"], "newValue": r["new_value"], "clientId": r["client_id"],
        })
    return out


def get_review(alarm_id):
    r = one("SELECT status, updated_at, updated_by FROM ops_review WHERE alarm_id = ?", (alarm_id,))
    if not r:
        return {"alarmId": alarm_id, "status": "unreviewed", "updatedAt": None, "updatedBy": None}
    return {"alarmId": alarm_id, "status": r["status"], "updatedAt": r["updated_at"], "updatedBy": r["updated_by"]}


def _review_row(r):
    return {
        "alarmId": r["alarm_id"], "status": r["status"], "updatedAt": r["updated_at"],
        "updatedBy": r["updated_by"], "firstReviewedAt": r["first_reviewed_at"],
        "validatedById": r["validated_by_id"], "snapshot": jload(r["snapshot"], {}) or {},
    }


_REVIEW_COLS = "alarm_id, status, updated_at, updated_by, snapshot, first_reviewed_at, validated_by_id"


def list_reviews(since=None, until=None):
    q = f"SELECT {_REVIEW_COLS} FROM ops_review WHERE status != 'unreviewed'"
    params = []
    if since:
        q += " AND updated_at >= ?"
        params.append(since)
    if until:
        q += " AND updated_at < ?"
        params.append(until)
    q += " ORDER BY updated_at DESC"
    with connect() as conn:
        return [_review_row(r) for r in conn.execute(q, params).fetchall()]


def _in_chunks(table_sql, ids, key_col):
    ids = list(dict.fromkeys(ids))
    out = []
    with connect() as conn:
        for i in range(0, len(ids), 900):  # stay under SQLite's variable limit
            chunk = ids[i:i + 900]
            ph = ",".join("?" for _ in chunk)
            out.extend(conn.execute(f"{table_sql} WHERE {key_col} IN ({ph})", chunk).fetchall())
    return out


def reviews_for(alarm_ids):
    return {r["alarm_id"]: _review_row(r)
            for r in _in_chunks(f"SELECT {_REVIEW_COLS} FROM ops_review", alarm_ids, "alarm_id")}


def audit_stats(since=None, until=None):
    """[{operator, action, count, lastAt}] for review actions in [since, until)."""
    q = ("SELECT user_name AS operator, action, COUNT(*) AS n, MAX(at) AS last_at FROM audit_events "
         "WHERE action LIKE 'review.%'")
    params = []
    if since:
        q += " AND at >= ?"
        params.append(since)
    if until:
        q += " AND at < ?"
        params.append(until)
    q += " GROUP BY user_name, action"
    return [{"operator": r["operator"], "action": r["action"][7:], "count": r["n"], "lastAt": r["last_at"]}
            for r in rows(q, params)]


# --------------------------------------------------------------------------
# Settings (key/value JSON)
# --------------------------------------------------------------------------

def get_setting(key, default=None):
    r = one("SELECT value FROM settings WHERE key = ?", (key,))
    return jload(r["value"], default) if r else default


def set_setting(key, value):
    execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, jdump(value)))
