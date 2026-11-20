"""
obs.py — observability: request ids, operation events and failure counters.

Every request gets a correlation id (X-Request-ID: taken from the caller when it is a safe token, otherwise
generated). It is returned in the response header and in every error body, and every log line written while
the request runs carries it (`[req=…]`). Important alert operations are logged as one structured line:

    op=alert.decide outcome=ok requestId=… alarmId=… projectId=… userId=…

Never logged: API keys, passwords, session secrets, signed media links (only ids and outcomes).

Counters (since the process started) back GET /api/admin/metrics: refresh failures, alert detail failures,
media failures, validation conflicts, ticket creation failures, client delivery failures, mapping failures.
"""

import logging
import re
import threading
import time
import uuid

log = logging.getLogger("camview.ops")

_counters = {}
_lock = threading.Lock()
_started = time.time()
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{6,64}$")
_local = threading.local()

# the failure counters every deployment reports, even at zero
TRACKED = ("refresh.failure", "alert_detail.failure", "media.failure", "validation.conflict", "ticket.create_failure",
           "delivery.failure", "mapping.failure")


def new_request_id(incoming=None):
    rid = incoming if incoming and _SAFE_ID.match(incoming) else uuid.uuid4().hex[:16]
    _local.request_id = rid
    return rid


def request_id():
    """The current request's id (Flask g when inside a request, else the thread's — background jobs set one)."""
    try:
        from flask import g, has_request_context
        if has_request_context() and getattr(g, "request_id", None):
            return g.request_id
    except Exception:
        pass
    return getattr(_local, "request_id", None)


def set_thread_request_id(rid):
    _local.request_id = rid


def count(name, n=1):
    with _lock:
        _counters[name] = _counters.get(name, 0) + n


def counters():
    with _lock:
        out = {k: 0 for k in TRACKED}
        out.update(_counters)
        return out


def reset():
    with _lock:
        _counters.clear()


def uptime_seconds():
    return round(time.time() - _started)


def event(operation, outcome="ok", level=logging.INFO, alarm_id=None, project_id=None, user=None, **extra):
    """One structured line per important alert operation. `outcome` != "ok" also counts `<operation>.<outcome>`."""
    if outcome != "ok":
        count(f"{operation}.{outcome}")
    uid = user.get("id") if isinstance(user, dict) else user
    parts = [f"op={operation}", f"outcome={outcome}", f"requestId={request_id() or '-'}"]
    for k, v in (("alarmId", alarm_id), ("projectId", project_id), ("userId", uid)):
        if v not in (None, ""):
            parts.append(f"{k}={v}")
    for k, v in extra.items():
        if v not in (None, ""):
            parts.append(f"{k}={_clean(v)}")
    log.log(level, " ".join(parts))


def _clean(v):
    s = str(v).replace("\n", " ")[:200]
    return re.sub(r"(?i)(X-Amz-Signature|Signature|token|key|password)=[^&\s]+", r"\1=***", s)


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every log record so the format string can print it."""

    def filter(self, record):
        record.request_id = request_id() or "-"
        return True


def install_logging(level):
    fmt = "%(asctime)s %(levelname)s %(name)s [req=%(request_id)s]: %(message)s"
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=fmt)
    for h in root.handlers:
        h.addFilter(RequestIdFilter())
        h.setFormatter(logging.Formatter(fmt))
    root.setLevel(level)
