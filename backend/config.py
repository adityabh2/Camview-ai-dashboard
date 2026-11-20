"""
config.py — runtime configuration for CAMVIEW Command Center.

Everything comes from environment variables / backend/.env. Values can be
re-applied at runtime (Settings > Connect saves new ones) via `apply()`.

Data mode
---------
CAMVIEW_MODE = demo | live
  demo : generated demo data (projects, cameras, alarms, users, clients),
         stored in its own database (camview-demo.db). Always labelled DEMO.
  live : real Camview listAlarms data through the scoped API key, workflow
         data in camview.db.
  Default: live if CAMVIEW_API_KEY is set, otherwise demo.
"""

import os
import secrets

import alarms

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BACKEND_DIR, ".env")
ENV_EXAMPLE_PATH = os.path.join(BACKEND_DIR, ".env.example")
FRONTEND_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "frontend")

try:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)
except ImportError:  # python-dotenv is optional; plain env vars still work
    pass

DEFAULT_API_URL = "https://default.prod.api.camviewai.com/alarms/listAlarms"

# Feature flags (spec §129). V2 features stay off until their backend exists.
FEATURE_DEFAULTS = {
    "ENABLE_CLIENT_PORTAL": True,
    "ENABLE_CLIENT_SHARING": True,
    "ENABLE_AUDIT": True,
    "ENABLE_SCHEDULED_ALERTS": True,
    "ENABLE_ADVANCED_REPORTS": True,
    "ENABLE_AI": True,             # V2: AI assistant (Claude); answers only once CAMVIEW_AI_API_KEY is set
    "ENABLE_SMART_SEARCH": True,   # V2: plain-language search, interpreted filters shown before searching
    "ENABLE_INCIDENTS": True,      # V2: incidents built from correlated alerts
    "ENABLE_REALTIME": True,       # V2: push updates to browsers (/api/events); Camview itself is still polled
    "ENABLE_MAP": True,            # V2: operations map by city / centre (positions geocoded, labelled approximate)
    "ENABLE_ADVANCED_CORRELATION": True,  # V2: explained correlation (same centre / camera / type, time window)
}


def _flag(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def apply():
    """(Re)reads settings from the environment into module globals."""
    # `docker run --env-file` passes values verbatim: KEY="value" arrives WITH the quotes, and a .env saved on
    # Windows can carry a BOM or a trailing CR. Clean every CAMVIEW_ value so the API key and ids stay valid.
    for k in [k for k in os.environ if k.lstrip("﻿").startswith("CAMVIEW_")]:
        v = os.environ[k].strip().lstrip("﻿")
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1].strip()
        name = k.lstrip("﻿")
        if name != k:
            del os.environ[k]
        os.environ[name] = v
    global API_URL, API_KEY, DEFAULT_PROJECT_ID, TIMEOUT, MODE, ALARM_TYPE_NAMES, PRIORITY_LABELS
    global HALL_FIELD, FEATURES, ALLOWED_API_HOST_SUFFIX, ALLOW_REMOTE_SETUP, SECRET_KEY
    global LIVE_DB_PATH, DEMO_DB_PATH, WINDOW_PAGES, CACHE_SECONDS, KPI_MAX_PAGES, KPI_USE_HISTORY, PROJECT_CODES
    global EXCLUDED_PROJECTS

    API_URL = os.environ.get("CAMVIEW_API_URL", "").strip() or DEFAULT_API_URL
    API_KEY = os.environ.get("CAMVIEW_API_KEY", "").strip()
    global LIVE_MINUTES
    # An alert counts as LIVE while its latest occurrence is at most this many minutes old (its own time, not ours).
    LIVE_MINUTES = max(1, int(os.environ.get("CAMVIEW_LIVE_MINUTES", "30") or 30))
    global DELIVER_ALL
    # all (default) = every detection alert goes to its client the moment Camview sends it; nobody has to mark it
    # VALID. An administrator marks an alert INVALID (or EXCEPTION) to take it back from the client - it stays in
    # the system with its decision and audit trail. valid = clients see only alerts an operator marked VALID.
    DELIVER_ALL = os.environ.get("CAMVIEW_CLIENT_DELIVERY", "all").strip().lower() != "valid"
    global NO_LOGIN
    # 1 = no sign-in screen: every request runs as the built-in Super Admin. ONLY for a single-user install that
    # is reachable from this computer alone (docker-compose.local.yml publishes the port on 127.0.0.1 only).
    NO_LOGIN = os.environ.get("CAMVIEW_NO_LOGIN", "0").strip().lower() in ("1", "true", "yes", "on")
    global NO_LOGIN_PORTS, CLIENT_ONLY_PORTS
    # Two doors in one app (docker-compose.local.yml): the no-login administrator door only on these listener
    # ports (published on 127.0.0.1), and a client-portal door where sign-in is required and ONLY client accounts
    # are accepted. The port is the server's own socket, never a header, so a request cannot pretend to be local.
    NO_LOGIN_PORTS = {p.strip() for p in os.environ.get("CAMVIEW_NO_LOGIN_PORTS", "").split(",") if p.strip()}
    CLIENT_ONLY_PORTS = {p.strip() for p in os.environ.get("CAMVIEW_CLIENT_ONLY_PORTS", "").split(",") if p.strip()}
    global PROJECT_API_KEY, PROJECT_LOOKUP_URL
    # Key for the Project Lookup API (POST /api/project/listProjectsByClientProjectId). The scoped alarm key
    # is limited to listAlarms, so this is usually a separate key or a user "Bearer <token>". Blank = API_KEY.
    PROJECT_API_KEY = os.environ.get("CAMVIEW_PROJECT_API_KEY", "").strip()
    PROJECT_LOOKUP_URL = os.environ.get("CAMVIEW_PROJECT_LOOKUP_URL", "").strip() or \
        ((os.environ.get("CAMVIEW_API_URL", "").strip() or DEFAULT_API_URL).rsplit("/alarms/", 1)[0]
         + "/api/project/listProjectsByClientProjectId")
    DEFAULT_PROJECT_ID = os.environ.get("CAMVIEW_PROJECT_ID", "").strip()
    TIMEOUT = float(os.environ.get("CAMVIEW_TIMEOUT", "15") or 15)
    mode = os.environ.get("CAMVIEW_MODE", "").strip().lower()
    if not mode:  # DATA_MODE=mock|live is accepted as an alias (spec §161)
        mode = {"mock": "demo", "demo": "demo", "live": "live"}.get(os.environ.get("DATA_MODE", "").strip().lower(), "")
    MODE = mode if mode in ("demo", "live") else ("live" if API_KEY else "demo")
    ALARM_TYPE_NAMES = alarms.parse_id_map(os.environ.get("CAMVIEW_ALARM_TYPE_NAMES", ""))
    PRIORITY_LABELS = alarms.parse_id_map(os.environ.get("CAMVIEW_PRIORITY_LABELS", ""),
                                          alarms.DEFAULT_PRIORITY_LABELS)
    HALL_FIELD = os.environ.get("CAMVIEW_HALL_FIELD", "").strip() or None
    # Camview's listAlarms carries no project code; the exam's own code (e.g. MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL)
    # is configured per project id and shown everywhere instead of the number. Also editable in Settings.
    PROJECT_CODES = parse_project_codes(os.environ.get("CAMVIEW_PROJECT_CODES", ""))
    # Old / foreign project ids that must never be monitored or listed (their auto-built data is removed)
    EXCLUDED_PROJECTS = {p.strip() for p in os.environ.get("CAMVIEW_EXCLUDED_PROJECTS", "").split(",") if p.strip().isdigit()}
    FEATURES = {k: _flag(k, v) for k, v in FEATURE_DEFAULTS.items()}
    ALLOWED_API_HOST_SUFFIX = os.environ.get("CAMVIEW_ALLOWED_HOST_SUFFIX", ".camviewai.com")
    ALLOW_REMOTE_SETUP = os.environ.get("CAMVIEW_ALLOW_REMOTE_SETUP", "0") == "1"
    LIVE_DB_PATH = os.environ.get("CAMVIEW_DB_PATH") or os.path.join(BACKEND_DIR, "camview.db")
    DEMO_DB_PATH = os.environ.get("CAMVIEW_DEMO_DB_PATH") or os.path.join(BACKEND_DIR, "camview-demo.db")
    # How many pages of 100 alarms form the "working set" for live operations.
    # Pages of 100 alarms read per project. Camview's listAlarms is NOT sorted newest-first, so the
    # whole project must be read to be sure the latest alarms are included (cap: 100 pages = 10,000).
    WINDOW_PAGES = max(1, int(os.environ.get("CAMVIEW_WINDOW_PAGES", "100") or 100))
    # Live data is fetched from Camview at most once per this many seconds per project (default 30 s).
    CACHE_SECONDS = max(2, int(os.environ.get("CAMVIEW_CACHE_SECONDS", "30") or 30))
    KPI_MAX_PAGES = max(1, int(os.environ.get("CAMVIEW_KPI_MAX_PAGES", "10") or 10))
    KPI_USE_HISTORY = os.environ.get("CAMVIEW_KPI_USE_HISTORY", "0") == "1"
    global PASSWORD_MIN
    # Minimum password length for every account (sign-in lockout after repeated failures still applies).
    PASSWORD_MIN = max(4, int(os.environ.get("CAMVIEW_PASSWORD_MIN", "5") or 5))
    global AUTO_DISCOVER, AUTO_DISCOVER_MINUTES, AUTO_ACTIVE_HOURS, AUTO_RETIRE_HOURS
    # Live mode: running Camview projects are found and monitored automatically (nobody types an id).
    # "Running" = any event (alert or camera status) within AUTO_ACTIVE_HOURS. A project found this way
    # stops being monitored after AUTO_RETIRE_HOURS without any event (0 = never).
    AUTO_DISCOVER = os.environ.get("CAMVIEW_AUTO_DISCOVER", "1") != "0"
    AUTO_DISCOVER_MINUTES = max(5, int(os.environ.get("CAMVIEW_AUTO_DISCOVER_MINUTES", "30") or 30))
    AUTO_ACTIVE_HOURS = max(1, int(os.environ.get("CAMVIEW_AUTO_ACTIVE_HOURS", "24") or 24))
    AUTO_RETIRE_HOURS = max(0, int(os.environ.get("CAMVIEW_AUTO_RETIRE_HOURS", "72") or 0))

    global MEDIA_MODE
    # redirect (default): the browser loads evidence straight from Camview's media store via a freshly signed link;
    # proxy: evidence is streamed through this server (browsers without access to the media store, strict proxies)
    MEDIA_MODE = "proxy" if os.environ.get("CAMVIEW_MEDIA_MODE", "").strip().lower() == "proxy" else "redirect"
    global SECRET_SOURCE, TRUST_PROXY
    TRUST_PROXY = os.environ.get("CAMVIEW_TRUST_PROXY", "0") == "1"   # behind a TLS proxy: honour X-Forwarded-Proto
    SECRET_KEY = os.environ.get("CAMVIEW_SECRET_KEY", "").strip()
    SECRET_SOURCE = "env" if SECRET_KEY else None
    if not SECRET_KEY and MODE == "live":
        # Production sessions need a STABLE signing key: a new one after every restart signs everybody out and
        # hides a misconfiguration. Without CAMVIEW_SECRET_KEY the key is kept next to the live database (the
        # persistent volume); if it cannot be kept there, the server refuses to start rather than improvise.
        SECRET_KEY, SECRET_SOURCE = _persisted_secret(os.path.dirname(os.path.abspath(LIVE_DB_PATH))), "file"
    elif not SECRET_KEY:
        # demo: create one once and keep it in .env
        SECRET_KEY, SECRET_SOURCE = secrets.token_urlsafe(48), "generated"
        os.environ["CAMVIEW_SECRET_KEY"] = SECRET_KEY
        if not os.environ.get("CAMVIEW_TESTING"):
            try:
                write_env({"CAMVIEW_SECRET_KEY": SECRET_KEY})
                SECRET_SOURCE = "env-file"
            except OSError:
                pass


SECRET_FILE_NAME = ".session-secret"


def _persisted_secret(directory):
    path = os.path.join(directory, SECRET_FILE_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(48)
    try:
        os.makedirs(directory, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
    except OSError as e:
        raise RuntimeError("Live mode needs a stable session secret: set CAMVIEW_SECRET_KEY (48+ random characters), "
                           f"or make {directory} writable so one can be kept there.") from e
    import logging
    logging.getLogger("camview.config").warning(
        "CAMVIEW_SECRET_KEY is not set: a session secret was generated and kept in %s (stable across restarts). "
        "Set CAMVIEW_SECRET_KEY to manage it yourself.", path)
    return key


def parse_project_codes(raw):
    """"2773:MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL,2872:OTHER" → {"2773": "MPESB/…", "2872": "OTHER"}.
    The code may contain slashes and dashes; entries are separated by commas."""
    out = {}
    for part in (raw or "").split(","):
        if ":" not in part:
            continue
        pid, code = part.split(":", 1)
        if pid.strip().isdigit() and code.strip():
            out[pid.strip()] = code.strip()
    return out


def db_path():
    return DEMO_DB_PATH if MODE == "demo" else LIVE_DB_PATH


def write_env(updates):
    """Updates KEY=value lines in backend/.env, keeping comments and other
    settings. Starts from .env.example if .env doesn't exist yet."""
    source = ENV_PATH if os.path.exists(ENV_PATH) else ENV_EXAMPLE_PATH
    lines = []
    if os.path.exists(source):
        with open(source, encoding="utf-8") as f:
            lines = f.read().splitlines()
    remaining = dict(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


apply()
