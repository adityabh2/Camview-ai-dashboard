"""
archive.py — archiving alerts and project codes; only an administrator deletes.

Alerts
  archive(ids)   hidden from every list, count, map, HMS board and dashboard; kept with their decision, tickets
                 and audit trail; restorable.
  restore(ids)   back into the lists.
  delete(ids)    administrator only, archived alerts only: the dashboard's own records of the alert (review,
                 tickets, client deliveries, assignment) are removed and the alert never comes back, even if
                 Camview sends it again. The audit trail is append-only and always kept.

Project codes (project_codes.py keeps the registry)
  archive_code   monitoring stops; the code, its projects' data, clients and reports are kept; restorable.
  restore_code   monitored again.
  delete_code    administrator only, archived codes only: unregistered and the centres / cameras built from its
                 Camview data are removed.
"""

import threading

import db

_cache = {"version": None, "ids": frozenset(), "lock": threading.Lock()}


def is_admin(user):
    return bool(user) and user.get("roleId") == "super_admin"


def hidden_ids():
    """Every archived (or deleted) alarm id — cached until the archive changes."""
    v = db.get_setting("archive_version", 0)
    with _cache["lock"]:
        if _cache["version"] != v:
            _cache["ids"] = frozenset(r["alarm_id"] for r in db.rows("SELECT alarm_id FROM archived_alerts"))
            _cache["version"] = v
        return _cache["ids"]


def archived_ids():
    return {r["alarm_id"] for r in db.rows("SELECT alarm_id FROM archived_alerts WHERE deleted=0")}


def _bump():
    db.set_setting("archive_version", (db.get_setting("archive_version", 0) or 0) + 1)


def archive(items, user, reason=None):
    """items: [(alarm_id, project_id)]. Returns how many were newly archived."""
    now, n = db.now_iso(), 0
    with db.connect() as conn:
        for aid, pid in items:
            cur = conn.execute("INSERT OR IGNORE INTO archived_alerts (alarm_id, project_id, archived_by, archived_at, reason) "
                               "VALUES (?,?,?,?,?)", (str(aid), None if pid is None else str(pid), user["name"], now, reason))
            n += cur.rowcount
    if n:
        _bump()
        db.audit("archive.alerts", user, "alarm", f"{n} alert(s)", None, "archived", details={"count": n, "reason": reason})
    return n


def restore(ids, user):
    ph = ",".join("?" for _ in ids) or "''"
    n = db.execute(f"DELETE FROM archived_alerts WHERE deleted=0 AND alarm_id IN ({ph})", [str(i) for i in ids])
    _bump()
    db.audit("archive.restore", user, "alarm", f"{len(ids)} alert(s)", "archived", "restored", details={"ids": ids[:50]})
    return n


def delete(ids, user):
    """Administrator only, archived alerts only. Returns the ids deleted."""
    from camview_client import ApiError
    if not is_admin(user):
        raise ApiError("forbidden", "Only an administrator can delete.", 403)
    ids = [str(i) for i in ids]
    ph = ",".join("?" for _ in ids) or "''"
    allowed = [r["alarm_id"] for r in db.rows(f"SELECT alarm_id FROM archived_alerts WHERE deleted=0 AND alarm_id IN ({ph})", ids)]
    if not allowed:
        raise ApiError("not_eligible", "Only archived alerts can be deleted. Archive them first.", 409)
    now = db.now_iso()
    ph2 = ",".join("?" for _ in allowed)
    with db.connect() as conn:
        for table in ("publications", "tickets", "ops_review", "assignments"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE alarm_id IN ({ph2})", allowed)
            except Exception:                         # a table this version does not have
                pass
        conn.execute(f"UPDATE archived_alerts SET deleted=1, deleted_by=?, deleted_at=? WHERE alarm_id IN ({ph2})",
                     [user["name"], now, *allowed])
    _bump()
    db.audit("archive.delete", user, "alarm", f"{len(allowed)} alert(s)", "archived", "deleted", details={"ids": allowed[:100]})
    return allowed


def archived_list(limit=500):
    return db.rows("SELECT alarm_id, project_id, archived_by, archived_at, reason FROM archived_alerts WHERE deleted=0 "
                   "ORDER BY archived_at DESC LIMIT ?", (limit,))
