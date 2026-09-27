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
    global API_URL, API_KEY, DEFAULT_PROJECT_ID, TIMEOUT, MODE, ALARM_TYPE_NAMES, PRIORITY_LABELS
    global HALL_FIELD, FEATURES, ALLOWED_API_HOST_SUFFIX, ALLOW_REMOTE_SETUP, SECRET_KEY
    global LIVE_DB_PATH, DEMO_DB_PATH, WINDOW_PAGES, CACHE_SECONDS, KPI_MAX_PAGES, KPI_USE_HISTORY, PROJECT_CODES
    global EXCLUDED_PROJECTS

    API_URL = os.environ.get("CAMVIEW_API_URL", "").strip() or DEFAULT_API_URL
    API_KEY = os.environ.get("CAMVIEW_API_KEY", "").strip()
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

    SECRET_KEY = os.environ.get("CAMVIEW_SECRET_KEY", "").strip()
    if not SECRET_KEY:
        # Sessions need a stable signing key; create one once and keep it in .env.
        SECRET_KEY = secrets.token_urlsafe(48)
        os.environ["CAMVIEW_SECRET_KEY"] = SECRET_KEY
        if not os.environ.get("CAMVIEW_TESTING"):
            try:
                write_env({"CAMVIEW_SECRET_KEY": SECRET_KEY})
            except OSError:
                pass


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
