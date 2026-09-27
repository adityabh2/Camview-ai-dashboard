"""
changes.py — one number that says "something the screens show has changed".

Every write to the Command Center database (a decision, a ticket, a notification,
master data…) and every Camview refresh that brought different alarm data bumps the
version. Browsers learn the current version from /api/status and are pushed a new one
over /api/events (Server-Sent Events) the moment it changes, so a screen re-reads its
data only when there is something new — not on a timer, and not for nothing.

No dependencies: db.py and datasource.py both import this module.
"""

import threading
import time

_cond = threading.Condition()
_version = int(time.time())          # a restart is itself a change: browsers re-read once
_bumps = 0


def version():
    return _version


def bump(reason=None):
    """Increments the version and wakes every waiting /api/events stream."""
    global _version, _bumps
    with _cond:
        _version += 1
        _bumps += 1
        _cond.notify_all()
    return _version


def wait(since, timeout):
    """Blocks up to `timeout` seconds until the version differs from `since`; returns the current one."""
    with _cond:
        if _version == since:
            _cond.wait(timeout)
        return _version


def stats():
    return {"version": _version, "bumps": _bumps}
