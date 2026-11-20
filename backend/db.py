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


BUSY_TIMEOUT = 10          # seconds a statement waits for another writer before "database is locked"


class DatabaseBusy(Exception):
    """The database stayed locked by another writer for longer than BUSY_TIMEOUT. Nothing was committed;
    the caller answers 503 (try again) — never a false success."""


def is_locked(e):
    return isinstance(e, sqlite3.OperationalError) and ("locked" in str(e) or "busy" in str(e))


@contextmanager
def connect(immediate=False):
    """One connection = one transaction: everything written inside is committed together, or (on any
    exception) nothing is. `immediate=True` takes the write lock at the start (BEGIN IMMEDIATE), so a
    read-check-write sequence (revision check, idempotency check) cannot interleave with another writer."""
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            changed = conn.total_changes
            conn.commit()
        except sqlite3.OperationalError as e:
            conn.rollback()
            if is_locked(e):
                raise DatabaseBusy(str(e)) from e
            raise
        except BaseException:
            conn.rollback()
            raise
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
-- canonical alert store: every record received from Camview, as normalized, so an alert that was ever listed
-- stays openable after a feed refresh, a page shift, a restart or a Camview outage (alert_store.py)
CREATE TABLE IF NOT EXISTS alerts (
    alarm_id      TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    event_kind    TEXT,
    last_instance TEXT,
    record        TEXT NOT NULL,
    fingerprint   TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_project ON alerts(project_id, last_instance);
-- durable work queue / outbox (jobs.py): PENDING -> RUNNING -> DONE | RETRY -> ... -> FAILED
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    dedupe_key   TEXT UNIQUE,
    payload      TEXT,
    status       TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    next_run_at  TEXT,
    last_error   TEXT,
    result       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, next_run_at);
-- data-quality events: malformed / unusable Camview records are counted and kept, never silently dropped
CREATE TABLE IF NOT EXISTS dq_events (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    project_id TEXT,
    alarm_id   TEXT,
    detail     TEXT,
    sample     TEXT,
    count      INTEGER NOT NULL DEFAULT 1,
    first_at   TEXT NOT NULL,
    last_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dq_kind ON dq_events(kind, last_at);
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
    "camera_health": {"raw_status": "TEXT", "last_frame_at": "TEXT"},
    # optimistic concurrency: every decision bumps the revision; one made against an older revision is a conflict
    "ops_review": {"revision": "INTEGER NOT NULL DEFAULT 0",
                   # the ACKNOWLEDGE step of the review flow: who picked the alert up and when (kept once decided)
                   "acknowledged_by": "TEXT", "acknowledged_by_id": "TEXT", "acknowledged_at": "TEXT"},
}


# the lookups every screen and every delivery makes (idempotent; primary keys already cover alerts.alarm_id,
# ops_review.alarm_id, tickets.alarm_id (UNIQUE) and publications(alarm_id, client_id))
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_alerts_project ON alerts(project_id, last_instance);
CREATE INDEX IF NOT EXISTS idx_tickets_client ON tickets(client_id, delivery_status);
CREATE INDEX IF NOT EXISTS idx_tickets_project ON tickets(project_id, status);
CREATE INDEX IF NOT EXISTS idx_pub_client_status ON publications(client_id, status);
CREATE INDEX IF NOT EXISTS idx_pub_project ON publications(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_review_status ON ops_review(status, updated_at);
"""

INTEGRITY = {"checkedAt": None, "ok": None, "detail": None}


def check_integrity(full=False):
    """PRAGMA quick_check (or integrity_check) + foreign_key_check. Run after migrations at start-up; the result
    is part of /readyz. Never raises."""
    try:
        with connect() as conn:
            res = [r[0] for r in conn.execute("PRAGMA integrity_check" if full else "PRAGMA quick_check").fetchall()]
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        ok = res == ["ok"] and not fk
        detail = None if ok else "; ".join(res[:3]) + (f"; {len(fk)} foreign-key violation(s)" if fk else "")
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    INTEGRITY.update(checkedAt=now_iso(), ok=ok, detail=detail, path=DB_PATH)
    return ok


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
        # Camview's lastFrameSync used to be stored as the RECORDING time: it is the camera's last frame
        conn.execute("UPDATE camera_health SET last_frame_at=COALESCE(last_frame_at, last_recording_at), "
                     "last_recording_at=NULL WHERE source='camview' AND last_recording_at IS NOT NULL")
        conn.executescript(INDEXES)
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
          project_id=None, client_id=None, note=None, details=None, conn=None):
    """Appends one audit event. With `conn` it is part of the caller's transaction (written only if the
    operation it describes is committed) — required inside a `connect(immediate=True)` block, where a
    second connection would wait for the write lock that block holds."""
    sql = ("INSERT INTO audit_events (at, user_id, user_name, action, resource_type, resource_id, old_value, "
           "new_value, project_id, client_id, note, details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
    params = (now_iso(), (user or {}).get("id"), (user or {}).get("name"), action, resource_type,
              None if resource_id is None else str(resource_id),
              None if old is None else (old if isinstance(old, str) else jdump(old)),
              None if new is None else (new if isinstance(new, str) else jdump(new)),
              None if project_id is None else str(project_id), client_id, note, jdump(details))
    if conn is not None:
        conn.execute(sql, params)
        return
    with quiet():                                   # an audit row on its own changes nothing on screen
        execute(sql, params)


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


def apply_action(alarm_id, action, operator, note=None, snapshot=None, user=None, project_id=None, conn=None):
    """Records an operator review action. `operator` is the display name;
    `user` (dict) is the authenticated user when available. With `conn` the write joins the caller's
    transaction (a decision, its ticket and its delivery intent commit together or not at all).
    Every status change bumps the review's revision (optimistic concurrency, see tickets.decide)."""
    if conn is None:
        with connect() as own:
            return apply_action(alarm_id, action, operator, note, snapshot, user, project_id, own)
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

    before = conn.execute("SELECT status FROM ops_review WHERE alarm_id = ?", (alarm_id,)).fetchone()
    if new_status is not None:
        conn.execute("""
            INSERT INTO ops_review (alarm_id, status, updated_at, updated_by, snapshot, first_reviewed_at, validated_by_id,
                                    revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(alarm_id) DO UPDATE SET
                status=excluded.status, updated_at=excluded.updated_at, updated_by=excluded.updated_by,
                revision=ops_review.revision + 1,
                snapshot=COALESCE(excluded.snapshot, ops_review.snapshot),
                first_reviewed_at=COALESCE(ops_review.first_reviewed_at, excluded.first_reviewed_at),
                validated_by_id=CASE WHEN excluded.validated_by_id IS NOT NULL THEN excluded.validated_by_id
                                     WHEN excluded.status IN ('unreviewed','acknowledged') THEN NULL
                                     ELSE ops_review.validated_by_id END
        """, (alarm_id, new_status, now, operator, snapshot_json, first_review, validator))
        if action != "reopen":      # any review action implies the alert was picked up: the first acknowledgement stays
            conn.execute("UPDATE ops_review SET acknowledged_by=COALESCE(acknowledged_by, ?), "
                         "acknowledged_by_id=COALESCE(acknowledged_by_id, ?), acknowledged_at=COALESCE(acknowledged_at, ?) "
                         "WHERE alarm_id=?", (operator, (user or {}).get("id"), now, alarm_id))
    elif snapshot_json:
        conn.execute("UPDATE ops_review SET snapshot = ? WHERE alarm_id = ?", (snapshot_json, alarm_id))
    conn.execute(
        "INSERT INTO audit_events (at, user_id, user_name, action, resource_type, resource_id, old_value, "
        "new_value, project_id, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (now, (user or {}).get("id"), operator, "review." + action, "alarm", alarm_id,
         before["status"] if before else "unreviewed", new_status,
         None if project_id is None else str(project_id), (note or "").strip() or None))
    return _state(alarm_id, conn.execute(f"SELECT {_STATE_COLS} FROM ops_review WHERE alarm_id = ?", (alarm_id,)).fetchone())


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


_STATE_COLS = "status, updated_at, updated_by, revision, acknowledged_by, acknowledged_at"


def _state(alarm_id, r):
    """The review state every decision / acknowledgement answers with (and the conflict check reads)."""
    if not r:
        return {"alarmId": alarm_id, "status": "unreviewed", "updatedAt": None, "updatedBy": None, "revision": 0,
                "acknowledgedBy": None, "acknowledgedAt": None}
    return {"alarmId": alarm_id, "status": r["status"], "updatedAt": r["updated_at"], "updatedBy": r["updated_by"],
            "revision": r["revision"] or 0, "acknowledgedBy": r["acknowledged_by"], "acknowledgedAt": r["acknowledged_at"]}


def get_review(alarm_id, conn=None):
    q = f"SELECT {_STATE_COLS} FROM ops_review WHERE alarm_id = ?"
    r = conn.execute(q, (alarm_id,)).fetchone() if conn is not None else one(q, (alarm_id,))
    return _state(alarm_id, r)


def acknowledge(alarm_id, user, note=None, snapshot=None, project_id=None, conn=None):
    """The ACKNOWLEDGE step of the review flow: records who picked the alert up and when. It is NOT a decision:
    the review revision does not move (an operator deciding from a view taken before the acknowledgement is not
    in conflict) and the decision stays pending. Idempotent: an acknowledged alert keeps its first
    acknowledgement and gets no second audit row. Returns (review state, changed)."""
    if conn is None:
        with connect() as own:
            return acknowledge(alarm_id, user, note, snapshot, project_id, own)
    cur = get_review(alarm_id, conn)
    if cur["acknowledgedAt"]:
        return cur, False
    now = now_iso()
    snapshot_json = jdump(snapshot) if isinstance(snapshot, dict) and snapshot else None
    conn.execute("""
        INSERT INTO ops_review (alarm_id, status, updated_at, updated_by, snapshot, first_reviewed_at, revision,
                                acknowledged_by, acknowledged_by_id, acknowledged_at)
        VALUES (?, 'acknowledged', ?, ?, ?, ?, 0, ?, ?, ?)
        ON CONFLICT(alarm_id) DO UPDATE SET
            status=CASE WHEN ops_review.status='unreviewed' THEN 'acknowledged' ELSE ops_review.status END,
            snapshot=COALESCE(ops_review.snapshot, excluded.snapshot),
            first_reviewed_at=COALESCE(ops_review.first_reviewed_at, excluded.first_reviewed_at),
            acknowledged_by=excluded.acknowledged_by, acknowledged_by_id=excluded.acknowledged_by_id,
            acknowledged_at=excluded.acknowledged_at
    """, (alarm_id, now, user["name"], snapshot_json, now, user["name"], user.get("id"), now))
    conn.execute(
        "INSERT INTO audit_events (at, user_id, user_name, action, resource_type, resource_id, old_value, "
        "new_value, project_id, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (now, user.get("id"), user["name"], "review.acknowledge", "alarm", alarm_id, cur["status"],
         "acknowledged" if cur["status"] == "unreviewed" else cur["status"],
         None if project_id is None else str(project_id), (note or "").strip() or None))
    return get_review(alarm_id, conn), True


def _review_row(r):
    return {
        "alarmId": r["alarm_id"], "status": r["status"], "updatedAt": r["updated_at"],
        "updatedBy": r["updated_by"], "firstReviewedAt": r["first_reviewed_at"],
        "validatedById": r["validated_by_id"], "snapshot": jload(r["snapshot"], {}) or {},
        "revision": r["revision"] or 0,
        "acknowledgedBy": r["acknowledged_by"], "acknowledgedById": r["acknowledged_by_id"],
        "acknowledgedAt": r["acknowledged_at"],
    }


_REVIEW_COLS = ("alarm_id, status, updated_at, updated_by, snapshot, first_reviewed_at, validated_by_id, revision, "
                "acknowledged_by, acknowledged_by_id, acknowledged_at")


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
