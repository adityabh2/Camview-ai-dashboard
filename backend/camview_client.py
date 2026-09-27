"""
camview_client.py — the only code that talks to Camview's listAlarms API.

* Builds requests that contain only documented ListAlarmsRequest fields.
* Attaches the scoped key server-side (`Authorization: <key>`, no Bearer).
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


class ApiError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


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
    """POSTs to listAlarms; returns parsed JSON dict or raises ApiError."""
    if not config.API_KEY:
        raise ApiError("live_mode_not_configured",
                       "No Camview API key is configured. An administrator can add it in Settings › Connection.", 503)
    resp, last = None, None
    for attempt in range(RETRIES + 1):
        if attempt:
            time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
        try:
            resp = _post(
                config.API_URL, json=payload,
                headers={"Authorization": config.API_KEY, "Content-Type": "application/json"},
                timeout=config.TIMEOUT,
            )
        except requests.exceptions.Timeout:
            last = ApiError("timeout", "Alarm service did not respond in time.", 504)
            continue                                  # a slow answer once is not an outage: try again
        except requests.exceptions.RequestException:
            last = ApiError("network_error", "Unable to connect to the alarm service.", 502)
            continue
        if resp.status_code in RETRY_STATUSES and attempt < RETRIES:
            log.info("Camview HTTP %s (attempt %s) — retrying", resp.status_code, attempt + 1)
            continue
        break
    if resp is None:
        raise last

    if resp.status_code in (401, 403):
        raise ApiError("forbidden", f"Access to alarm data was denied (HTTP {resp.status_code}). "
                       "Camview rejected the API key — check the key and that the URL ends in /alarms/listAlarms.", 502)
    if resp.status_code >= 400:
        log.warning("Camview HTTP %s: %s", resp.status_code, resp.text[:300])
        raise ApiError("upstream_error", "Alarm service returned an internal error." if resp.status_code >= 500
                       else f"Alarm service returned HTTP {resp.status_code}.", 502)
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
            resp = _post(f"{base}/projects/getProject", json={"projectId": int(pid)},
                         headers={"Authorization": config.API_KEY, "Content-Type": "application/json"},
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
    """One normalized-shape page: returns (raw_items, response)."""
    data = call(build_body(body))
    items = [i for i in (data.get("cameraAlarmsDetails") or []) if isinstance(i, dict)]
    return items, data


def validate_api_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(config.ALLOWED_API_HOST_SUFFIX):
        raise ApiError("bad_request", f"API URL must be https and on a *{config.ALLOWED_API_HOST_SUFFIX} host.", 400)
    if not parsed.path.rstrip("/").endswith("/alarms/listAlarms"):
        raise ApiError("bad_request", "API URL must end with /alarms/listAlarms (no /api prefix).", 400)
