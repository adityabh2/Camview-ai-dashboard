"""
camview_client.py — the only code that talks to Camview's listAlarms API.

* Builds requests that contain only documented ListAlarmsRequest fields.
* Attaches the credential server-side: a scoped API key is sent raw (`Authorization: <key>`); a user login
  token is sent as `Authorization: Bearer <token>` (see auth_header).
* Also resolves an exam's project code (client project id) to Camview projectIds via the Project Lookup API.
* Maps every failure to a human-readable ApiError; never leaks the key.
"""

import logging
import threading
import time
from urllib.parse import urlparse

import requests

import config

log = logging.getLogger("camview.api")

# One pooled HTTPS session: the pages of a project are read in parallel and every 30 s, so re-using
# connections (no TLS handshake per page) is the single biggest speed-up for a refresh.
_session_lock = threading.Lock()
_session = None
RETRY_STATUSES = (502, 503, 504)
RETRIES = 2                      # extra attempts on a transient failure (timeout, connection reset, 5xx)
RETRY_BACKOFF = 0.6              # seconds before the first retry (doubles each time)

# Every call to Camview (refresh pages, project discovery, project records) shares ONE concurrency limit, so a
# discovery scan and several project refreshes together can never flood Camview with parallel requests.
MAX_CONCURRENCY = max(1, int(__import__("os").environ.get("CAMVIEW_MAX_CONCURRENCY", "6") or 6))
_slots = threading.BoundedSemaphore(MAX_CONCURRENCY)


class CircuitBreaker:
    """CLOSED → (FAIL_THRESHOLD consecutive failed calls) → OPEN: calls fail fast for `cooldown` seconds
    (doubling on every re-open, up to MAX_COOLDOWN) → HALF_OPEN: one trial call; success closes it.
    Protects Camview from being hammered while it is down, and keeps every screen fast while it is."""
    FAIL_THRESHOLD = 5
    BASE_COOLDOWN = 15.0
    MAX_COOLDOWN = 300.0

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.state, self.failures, self.opened_at, self.cooldown, self.trial = "closed", 0, 0.0, self.BASE_COOLDOWN, False
        self.last_error = None

    def before(self):
        with self.lock:
            if self.state == "open":
                if time.time() - self.opened_at < self.cooldown:
                    raise ApiError("circuit_open", "Camview is not answering; calls are paused for "
                                   f"{round(self.cooldown - (time.time() - self.opened_at))} s to let it recover.", 503)
                self.state, self.trial = "half_open", False
            if self.state == "half_open":
                if self.trial:
                    raise ApiError("circuit_open", "Camview is not answering; checking whether it is back.", 503)
                self.trial = True

    def success(self):
        with self.lock:
            if self.state != "closed":
                log.info("Camview answered again — circuit closed")
            self.state, self.failures, self.trial, self.cooldown = "closed", 0, False, self.BASE_COOLDOWN

    def failure(self, err):
        with self.lock:
            self.failures += 1
            self.last_error = getattr(err, "code", str(err))
            if self.state == "half_open":
                self.cooldown = min(self.MAX_COOLDOWN, self.cooldown * 2)
            if self.state == "half_open" or self.failures >= self.FAIL_THRESHOLD:
                if self.state != "open":
                    log.warning("Camview failing (%s consecutive) — circuit OPEN for %ss", self.failures, self.cooldown)
                self.state, self.opened_at, self.trial = "open", time.time(), False

    def snapshot(self):
        with self.lock:
            left = max(0, round(self.cooldown - (time.time() - self.opened_at))) if self.state == "open" else 0
            return {"state": self.state, "consecutiveFailures": self.failures, "retryInSeconds": left,
                    "lastError": self.last_error}


breaker = CircuitBreaker()


def breaker_state():
    return breaker.snapshot()


def session():
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=0)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
        return _session


_REAL_POST = requests.post


def _post(url, **kw):
    """POST over the pooled session. Tests replace `requests.post` with a fake Camview; that replacement
    is honoured so no test ever reaches the network."""
    if requests.post is not _REAL_POST:
        return requests.post(url, **kw)
    return session().post(url, **kw)


def auth_header(credential):
    """The Authorization header value for a stored credential.
    * "Bearer <token>" is sent as it is (a user login token).
    * A bare JWT (three dot-separated parts starting "eyJ") is a login token pasted without the word Bearer:
      the prefix is added.
    * Anything else is a raw scoped API key and is sent without a prefix."""
    c = (credential or "").strip()
    if c[:7].lower() == "bearer ":
        return "Bearer " + c[7:].strip()
    if c.startswith("eyJ") and c.count(".") == 2:
        return "Bearer " + c
    return c


def credential_kind(credential):
    c = (credential or "").strip()
    if not c:
        return None
    return "bearer" if auth_header(c).startswith("Bearer ") else "api_key"


class ApiError(Exception):
    def __init__(self, code, message, status, details=None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def to_int(value, field):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError("bad_request", f"{field} must be a number (got {value!r}).", 400)


def build_body(body):
    """ListAlarmsRequest with documented fields only. UI pages are 1-based;
    Camview pages are 0-based."""
    project_id = body.get("projectId") or config.DEFAULT_PROJECT_ID
    if project_id in (None, ""):
        raise ApiError("bad_request", "projectId is required. Select a project or set CAMVIEW_PROJECT_ID.", 400)
    page = max(1, to_int(body.get("page", 1), "page"))
    size = max(1, min(100, to_int(body.get("size", 20), "size")))
    out = {"projectId": to_int(project_id, "projectId"), "page": page - 1, "size": size}
    alarm_type = body.get("alarmType")
    if alarm_type not in (None, "", []):
        ids = alarm_type if isinstance(alarm_type, list) else [alarm_type]
        out["alarmType"] = [to_int(t, "alarmType") for t in ids]
    for key in ("alarmState", "lastActionType", "startTime", "endTime", "limit"):
        if body.get(key) not in (None, ""):
            out[key] = to_int(body[key], key)
    if body.get("useHistory") is not None:
        out["useHistory"] = bool(body["useHistory"])
    for key in ("shiftLabel", "lastKey"):
        if body.get(key):
            out[key] = str(body[key])
    return out


def call(payload):
    """POSTs to listAlarms; returns parsed JSON dict or raises ApiError. Retries transient failures with
    exponential backoff, shares the global concurrency limit and goes through the circuit breaker."""
    if not config.API_KEY:
        raise ApiError("live_mode_not_configured",
                       "No Camview API key is configured. An administrator can add it in Settings › Connection.", 503)
    breaker.before()
    try:
        data = _call(payload)
    except ApiError as e:
        if getattr(e, "transient", False):
            breaker.failure(e)
        else:
            breaker.success()                         # Camview answered (with a refusal): it is up
        raise
    breaker.success()
    return data


def _transient(err):
    """Marks an error the circuit breaker counts: Camview itself is struggling (timeout, no connection, 5xx).
    A 4xx is about one request (a project the key may not read, a bad field) and never opens the circuit."""
    err.transient = True
    return err


def _call(payload):
    resp, last = None, None
    for attempt in range(RETRIES + 1):
        if attempt:
            time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
        resp = None                                   # only the LAST attempt's outcome counts
        try:
            with _slots:
                resp = _post(
                    config.API_URL, json=payload,
                    headers={"Authorization": auth_header(config.API_KEY), "Content-Type": "application/json"},
                    timeout=config.TIMEOUT,
                )
        except requests.exceptions.Timeout:
            last = _transient(ApiError("timeout", "Alarm service did not respond in time.", 504))
            continue                                  # a slow answer once is not an outage: try again
        except requests.exceptions.RequestException:
            last = _transient(ApiError("network_error", "Unable to connect to the alarm service.", 502))
            continue
        if resp.status_code in RETRY_STATUSES and attempt < RETRIES:
            log.info("Camview HTTP %s (attempt %s) — retrying", resp.status_code, attempt + 1)
            continue
        break
    if resp is None:
        raise last

    if resp.status_code == 429:
        # Camview asks us to slow down: never retried at once; the refresh backs off at least Retry-After seconds
        err = _transient(ApiError("rate_limited", "Camview is limiting requests (HTTP 429); reading again later.", 503))
        try:
            err.retry_after = float(resp.headers.get("Retry-After") or 0) if hasattr(resp, "headers") else 0
        except (TypeError, ValueError):
            err.retry_after = 0
        raise err
    if resp.status_code in (401, 403):
        hint = ("The Bearer token may have expired (login tokens last about an hour) or lack alarm:read — paste a fresh one."
                if credential_kind(config.API_KEY) == "bearer" else
                "Camview rejected the API key — check the key and that the URL ends in /alarms/listAlarms.")
        raise ApiError("forbidden", f"Access to alarm data was denied (HTTP {resp.status_code}). {hint}", 502)
    if resp.status_code >= 400:
        log.warning("Camview HTTP %s: %s", resp.status_code, resp.text[:300])
        err = ApiError("upstream_error", "Alarm service returned an internal error." if resp.status_code >= 500
                       else f"Alarm service returned HTTP {resp.status_code}.", 502)
        raise _transient(err) if resp.status_code >= 500 else err
    try:
        data = resp.json()
    except ValueError:
        raise ApiError("malformed_response", "Unable to process alarm data (the service returned a non-JSON response).", 502)
    if not isinstance(data, dict):
        raise ApiError("malformed_response", "Unable to process alarm data (unexpected response shape).", 502)
    return data


_project_cache = {}


def project_info(project_id):
    """Camview's own project record (POST /projects/getProject), when the key is allowed to read it.
    Keys scoped to listAlarms get HTTP 403; the answer (or its absence) is remembered for an hour."""
    import time
    pid = str(project_id)
    hit = _project_cache.get(pid)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    out = None
    if config.API_KEY:
        base = config.API_URL.rsplit("/alarms/", 1)[0]
        try:
            with _slots:
                resp = _post(f"{base}/projects/getProject", json={"projectId": int(pid)},
                             headers={"Authorization": auth_header(config.API_KEY), "Content-Type": "application/json"},
                             timeout=config.TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                data = data.get("project", data) if isinstance(data, dict) else None
                if isinstance(data, dict):
                    code = next((str(data[k]).strip() for k in ("projectCode", "code", "examCode", "projectName", "name", "title")
                                 if data.get(k)), None)
                    name = next((str(data[k]).strip() for k in ("projectName", "name", "title") if data.get(k)), None)
                    out = {"code": code, "name": name} if code else None
            elif resp.status_code in (401, 403):
                log.info("Camview project record not readable with this key (HTTP %s) — project codes come from "
                         "CAMVIEW_PROJECT_CODES or Settings", resp.status_code)
        except (requests.RequestException, ValueError, TypeError):
            out = None
    _project_cache[pid] = (time.time(), out)
    return out


def list_page(body):
    """One page: returns (raw_items, response). An answer without the alarm list is a failed read (never
    "no alarms"). Non-object entries are passed on so the refresh records them as data-quality events."""
    data = call(build_body(body))
    items = data.get("cameraAlarmsDetails")
    if items is None:
        if not data or not any(k in data for k in ("totalElements", "totalPages", "hasNext")):
            raise ApiError("malformed_response", "Unable to process alarm data (the response carried no alarm list).", 502)
        items = []
    if not isinstance(items, list):
        raise ApiError("malformed_response", "Unable to process alarm data (the alarm list is not a list).", 502)
    return items, data


def validate_api_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(config.ALLOWED_API_HOST_SUFFIX):
        raise ApiError("bad_request", f"API URL must be https and on a *{config.ALLOWED_API_HOST_SUFFIX} host.", 400)
    if not parsed.path.rstrip("/").endswith("/alarms/listAlarms"):
        raise ApiError("bad_request", "API URL must end with /alarms/listAlarms (no /api prefix).", 400)


def lookup_projects(client_project_id):
    """Project Lookup API: the exam's own project code (Camview's clientProjectId, e.g.
    RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL) → every Camview project carrying it.
    Returns a list of {projectId, clientProjectId, name, status, ...}; raises ApiError on failure.
    The code goes in the JSON body, so slashes, & and spaces need no escaping."""
    code = str(client_project_id or "").strip()
    if not code:
        raise ApiError("bad_request", "Enter a project code.", 400)
    if len(code) > 200:
        raise ApiError("bad_request", "Project code is too long (max 200 characters).", 400)
    key = config.PROJECT_API_KEY or config.API_KEY
    if not key:
        raise ApiError("live_mode_not_configured",
                       "No key for the Project Lookup API. Add it in Settings › Connection (Project lookup key).", 503)
    try:
        with _slots:
            resp = _post(config.PROJECT_LOOKUP_URL, json={"clientProjectId": code},
                         headers={"Authorization": auth_header(key), "Content-Type": "application/json"},
                         timeout=config.TIMEOUT)
    except requests.exceptions.Timeout:
        raise ApiError("timeout", "Project Lookup API did not respond in time.", 504)
    except requests.exceptions.RequestException:
        raise ApiError("network_error", "Unable to connect to the Project Lookup API.", 502)
    try:
        data = resp.json()
    except ValueError:
        data = None
    desc = data.get("description") if isinstance(data, dict) else None
    if resp.status_code == 401:
        raise ApiError("forbidden", "Project Lookup refused the token (HTTP 401): "
                       f"{desc or 'token could not be resolved'}. Sign in again and paste a fresh Bearer token.", 502)
    if resp.status_code == 403:
        raise ApiError("forbidden", "Project Lookup refused the key (HTTP 403): "
                       f"{desc or 'not permitted'}. This key is not authorised for project lookup — set a Project "
                       "lookup key (an authorised API key or a Bearer token) in Settings › Connection.", 502)
    if resp.status_code == 400:
        raise ApiError("bad_request", desc or "Project Lookup rejected the code.", 400)
    if resp.status_code >= 400:
        log.warning("Project Lookup HTTP %s: %s", resp.status_code, (resp.text or "")[:300])
        raise ApiError("upstream_error", f"Project Lookup API returned HTTP {resp.status_code}.", 502)
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise ApiError("malformed_response", "Project Lookup API returned an unexpected response.", 502)
    out = []
    for p in data["projects"]:
        if not isinstance(p, dict):
            continue
        try:
            pid = int(p.get("projectId"))
        except (TypeError, ValueError):
            continue
        out.append({"projectId": pid, "clientProjectId": str(p.get("clientProjectId") or code),
                    **{k: p.get(k) for k in ("name", "status", "description", "startDate", "endDate",
                                             "projectType", "streamType")}})
    return out
