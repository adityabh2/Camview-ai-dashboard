"""
backup.py — database backup, verification and restore.

A Docker volume is storage, not a backup. In live mode the application writes one consistent copy of its
database a day (SQLite online-backup API: safe while the app runs), verifies every copy by opening it and
running PRAGMA integrity_check (the restore test), and keeps the newest CAMVIEW_BACKUP_KEEP copies.

    CAMVIEW_BACKUP_DIR    where copies go (default: backups/ next to the database — put it on another disk
                          or sync it off the server; see README "Backup, restore, disaster recovery")
    CAMVIEW_BACKUP_KEEP   copies to keep (default 14)
    CAMVIEW_BACKUP        0 = no automatic backup

Command line (from backend/):
    python backup.py create                 one backup now (verified)
    python backup.py list                   backups, newest first
    python backup.py verify <file>          integrity check of a backup (a restore test that changes nothing)
    python backup.py restore <file>         STOP THE APP FIRST. Verifies <file>, keeps the current database as
                                            pre-restore-<time>.db, then restores <file> over it
"""

import glob
import logging
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone

log = logging.getLogger("camview.backup")

REQUIRED_TABLES = ("users", "roles", "ops_review", "tickets", "publications", "audit_events", "alerts", "jobs")
DAY = 24 * 3600
_state = {"lastAttempt": 0.0, "last": None, "error": None}


def _db_path():
    import db
    return db.DB_PATH


def backup_dir():
    return os.environ.get("CAMVIEW_BACKUP_DIR") or os.path.join(os.path.dirname(os.path.abspath(_db_path())), "backups")


def keep():
    try:
        return max(1, int(os.environ.get("CAMVIEW_BACKUP_KEEP", "14") or 14))
    except ValueError:
        return 14


def enabled():
    import config
    return config.MODE == "live" and os.environ.get("CAMVIEW_BACKUP", "1") != "0" \
        and os.environ.get("CAMVIEW_TESTING") != "1"


def verify(path):
    """(ok, detail): the copy opens, passes integrity_check and has every required table."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            res = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error as e:
        return False, f"cannot open: {e}"
    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if res != ["ok"]:
        return False, "integrity_check: " + "; ".join(res[:3])
    if missing:
        return False, "missing tables: " + ", ".join(missing)
    return True, "ok"


def create(src=None, dest_dir=None):
    """One consistent, verified copy of the database. Returns info; raises on failure (the bad copy is removed)."""
    src = src or _db_path()
    dest_dir = dest_dir or backup_dir()
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(dest_dir, f"camview-{stamp}.db")
    s = sqlite3.connect(src, timeout=30)
    d = sqlite3.connect(dest)
    try:
        s.backup(d, pages=512, sleep=0.01)        # online backup: consistent while the app keeps writing
    finally:
        d.close()
        s.close()
    ok, detail = verify(dest)
    if not ok:
        os.remove(dest)
        raise RuntimeError(f"backup verification failed: {detail}")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    info = {"file": dest, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bytes": os.path.getsize(dest), "verified": True}
    prune(dest_dir)
    return info


def list_backups(dest_dir=None):
    files = sorted(glob.glob(os.path.join(dest_dir or backup_dir(), "camview-*.db")), reverse=True)
    return [{"file": f, "bytes": os.path.getsize(f),
             "at": datetime.fromtimestamp(os.path.getmtime(f), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} for f in files]


def prune(dest_dir=None):
    for old in list_backups(dest_dir)[keep():]:
        try:
            os.remove(old["file"])
        except OSError:
            log.warning("could not remove old backup %s", old["file"])


def restore(path, target=None):
    """Restores a verified backup over the database (the app must be stopped). The current database is kept."""
    target = target or _db_path()
    ok, detail = verify(path)
    if not ok:
        raise RuntimeError(f"refusing to restore an unverified backup: {detail}")
    if os.path.exists(target):
        keep_as = os.path.join(os.path.dirname(os.path.abspath(target)),
                               f"pre-restore-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db")
        shutil.copy2(target, keep_as)
    for ext in ("-wal", "-shm"):
        if os.path.exists(target + ext):
            os.remove(target + ext)
    s = sqlite3.connect(path)
    d = sqlite3.connect(target)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    ok, detail = verify(target)
    if not ok:
        raise RuntimeError(f"restored database failed verification: {detail}")
    return target


def status():
    b = list_backups() if os.path.isdir(backup_dir()) else []
    newest = b[0] if b else None
    age = None
    if newest:
        age = time.time() - datetime.strptime(newest["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    return {"enabled": enabled(), "count": len(b), "newest": newest, "ageHours": None if age is None else round(age / 3600, 1),
            "overdue": bool(enabled() and (age is None or age > DAY * 1.5)), "lastError": _state["error"],
            "keep": keep()}


def maybe_daily():
    """Called by the job worker: at most one attempt an hour, a backup when the newest is a day old."""
    if not enabled() or time.time() - _state["lastAttempt"] < 3600:
        return None
    _state["lastAttempt"] = time.time()
    st = status()
    if st["newest"] and st["ageHours"] is not None and st["ageHours"] < 24:
        return None
    try:
        info = create()
        _state.update(last=info, error=None)
        log.info("Database backup written and verified: %s (%s bytes)", info["file"], info["bytes"])
        return info
    except Exception as e:
        _state["error"] = f"{type(e).__name__}: {e}"[:200]
        import obs
        obs.count("backup.failure")
        log.exception("Database backup failed")
        return None


if __name__ == "__main__":
    import config  # noqa: F401  (loads backend/.env → which database)
    import db
    db.DB_PATH = config.db_path()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "create":
        print(create())
    elif cmd == "list":
        for b in list_backups():
            print(b["at"], b["bytes"], b["file"])
    elif cmd == "verify" and len(sys.argv) > 2:
        print(verify(sys.argv[2]))
    elif cmd == "restore" and len(sys.argv) > 2:
        print("restored:", restore(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(2)
