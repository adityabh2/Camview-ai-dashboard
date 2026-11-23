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
    if not db.check_integrity():               # after migrations: a damaged database makes /readyz fail, loudly
        log.error("DATABASE INTEGRITY CHECK FAILED: %s — restore from a backup (README › Backup)",
                  db.INTEGRITY["detail"])
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
    with db.connect() as conn:                     # the official feature list names every alert code
        for tid, (name, desc, sev) in alarms.DEFAULT_TYPE_NAMES.items():
            conn.execute("INSERT OR IGNORE INTO alarm_types (id, name, description, severity) VALUES (?,?,?,?)",
                         (tid, name, f"{desc}. {alarms.FEATURE_NOTE}", sev))
            # a name an older version GUESSED (or the placeholder "Alert type N") is corrected; a name an
            # administrator typed in the Dictionary is kept
            conn.execute("UPDATE alarm_types SET name=?, description=? WHERE id=? AND (description LIKE ? OR name LIKE ?)",
                         (name, f"{desc}. {alarms.FEATURE_NOTE}", tid, f"%{alarms.INFERRED_NOTE[:40]}%", "Alert type %"))
        for tid, name in config.ALARM_TYPE_NAMES.items():   # CAMVIEW_ALARM_TYPE_NAMES names codes OUTSIDE the list only
            if tid in alarms.FEATURES:
                continue
            conn.execute("INSERT INTO alarm_types (id, name, description) VALUES (?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description",
                         (tid, name, "From CAMVIEW_ALARM_TYPE_NAMES"))
        for tid, (name, desc, _sev) in alarms.DEFAULT_TYPE_NAMES.items():   # the feature list always wins
            conn.execute("UPDATE alarm_types SET name=?, description=? WHERE id=? AND name<>?",
                         (name, f"{desc}. {alarms.FEATURE_NOTE}", tid, name))
    ensure_admin(force=not db.rows("SELECT 1 FROM users LIMIT 1"))
    import project_codes
    project_codes.seed_from_config()           # CAMVIEW_PROJECT_CODES joins the project-code registry
    project_codes.ensure_monitored()           # every registered code's projects are monitored (no empty tabs)
    datasource.prune_stale_projects()          # an old exam's auto-built tree never lingers next to the running one
    import tickets
    # core rule: clients see only operator-VALID alerts. Anything an older version delivered on Camview's VALID
    # or on arrival is withdrawn (quietly, once, audited as delivery.policy_enforced).
    tickets.enforce_operator_valid_only()
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
    if config.NO_LOGIN:
        log.info("No-login mode (CAMVIEW_NO_LOGIN=1): the app runs as %s without a sign-in screen", email)
        return
    if not os.environ.get("CAMVIEW_ADMIN_PASSWORD"):
        # never written to the log (logs are shipped and kept): a file only the server's owner can read
        path = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "initial-admin-password.txt")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{email}\n{password}\n")
            log.warning("Super Admin %s created. Its one-time password is in %s — sign in, change it, delete the file.",
                        email, path)
        except OSError:
            log.error("Super Admin %s created but its password could not be saved. Set CAMVIEW_ADMIN_PASSWORD and "
                      "reset the password from another administrator account.", email)
