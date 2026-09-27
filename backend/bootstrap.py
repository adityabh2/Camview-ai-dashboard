"""
bootstrap.py — prepares the database for the current mode.

Demo mode : camview-demo.db, seeded with demo users/clients/master data.
Live mode : camview.db. On first start with no users, a Super Admin is created
            (CAMVIEW_ADMIN_EMAIL / CAMVIEW_ADMIN_PASSWORD, or a generated
            password printed once to the console).
"""

import logging
import os
import secrets

from werkzeug.security import generate_password_hash

import config
import datasource
import db
import rbac

log = logging.getLogger("camview.bootstrap")


def run():
    db.DB_PATH = config.db_path()
    db.init_db()
    import ai
    import geo
    import incidents
    for feature in (incidents, geo, ai):         # V2 feature tables (each module owns its schema)
        feature.init_schema()
    rbac.seed_roles()
    datasource.reset()
    if config.MODE == "demo":
        import demo_seed
        demo_seed.seed()
        if not demo_seed.demo_users_enabled():
            removed = db.one("SELECT COUNT(*) AS n FROM users WHERE is_demo=1")["n"]
            if removed:
                db.execute("DELETE FROM users WHERE is_demo=1")      # scopes/notifications/views cascade
                db.audit("user.demo_removed", None, "user", "demo", None, {"removed": removed})
                log.info("Removed %s demo login accounts", removed)
        ensure_admin(force=not db.rows("SELECT 1 FROM users LIMIT 1"))
        return
    # live defaults — never invented, only what's configured
    if not db.rows("SELECT 1 FROM priority_levels LIMIT 1"):
        order = sorted(config.PRIORITY_LABELS.items())
        with db.connect() as conn:
            for rank, (value, label) in enumerate(order):
                conn.execute("INSERT INTO priority_levels (value, label, rank, confirmed) VALUES (?,?,?,0)",
                             (value, label, rank))
    import alarms
    with db.connect() as conn:                     # names read from the data, for every type not named yet
        for tid, (name, desc, sev) in alarms.DEFAULT_TYPE_NAMES.items():
            conn.execute("INSERT OR IGNORE INTO alarm_types (id, name, description, severity) VALUES (?,?,?,?)",
                         (tid, name, f"{desc}. {alarms.INFERRED_NOTE}", sev))
        for tid, name in config.ALARM_TYPE_NAMES.items():                         # CAMVIEW_ALARM_TYPE_NAMES wins
            conn.execute("INSERT INTO alarm_types (id, name, description) VALUES (?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description",
                         (tid, name, "From CAMVIEW_ALARM_TYPE_NAMES"))
    ensure_admin(force=not db.rows("SELECT 1 FROM users LIMIT 1"))
    datasource.prune_stale_projects()          # an old exam's auto-built tree never lingers next to the running one
    import tickets
    tickets.enforce_operator_valid_only()      # clients see only alerts the backend team marked VALID
    try:
        tickets.deliver_pending_valid()        # operator-VALID alerts that were waiting for a client mapping
    except Exception:
        log.exception("delivering pending VALID alerts failed")
    if os.environ.get("CAMVIEW_TESTING") != "1":
        applied = datasource.sync_project_codes(force=True)   # CAMVIEW_PROJECT_CODES, then Camview's project record
        if applied:
            log.info("Project codes applied: %s", applied)


def ensure_admin(force=False):
    """Creates the configured administrator (CAMVIEW_ADMIN_EMAIL, may be a plain
    username such as 'admin') if it doesn't exist yet. Only the *initial*
    password comes from CAMVIEW_ADMIN_PASSWORD: once created, the password is
    changed in the app (profile menu or Users) and never overwritten here."""
    email = os.environ.get("CAMVIEW_ADMIN_EMAIL", "").strip()
    if not email and not force:
        return
    email = email or "admin@camview.local"
    if db.one("SELECT 1 FROM users WHERE email = ?", (email,)):
        return
    password = os.environ.get("CAMVIEW_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    uid = "u-admin-" + secrets.token_hex(4)
    db.execute("INSERT INTO users (id, name, email, password_hash, role_id, status, created_at) "
               "VALUES (?, 'Administrator', ?, ?, 'super_admin', 'active', ?)",
               (uid, email, generate_password_hash(password), db.now_iso()))
    db.execute("INSERT OR IGNORE INTO user_scopes (user_id, scope_type, scope_value) VALUES (?, 'global', '*')", (uid,))
    db.audit("user.bootstrap", None, "user", uid, None, email)
    if not os.environ.get("CAMVIEW_ADMIN_PASSWORD"):
        log.warning("=" * 70)
        log.warning("Super Admin created  %s  /  %s", email, password)
        log.warning("Sign in and change this password (profile menu > Change password).")
        log.warning("=" * 70)
