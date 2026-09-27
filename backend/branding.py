"""
branding.py — organisation branding: product name, subtitle and logo.

Stored as ONE settings row (key "branding"):
    {"name": str, "subtitle": str, "logo": {"mime", "data" (base64), "updatedAt"} | None}

Nothing stored = the stock CAMVIEW / Command Center look. Logos are validated
here (type allow-list, magic bytes, size, and for SVG a strict "no active
content" check) before they are ever stored or served.
"""

import base64
import binascii
import hashlib
import re
import xml.etree.ElementTree as ET

import db
from camview_client import ApiError

KEY = "branding"
DEFAULT_NAME = "CAMVIEW"
DEFAULT_SUBTITLE = "Command Center"
NAME_MAX = 40
SUBTITLE_MAX = 60
LOGO_MAX_BYTES = 512 * 1024
MIMES = ("image/png", "image/jpeg", "image/webp", "image/svg+xml")

_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp|svg\+xml));base64,([A-Za-z0-9+/=\s]*)$", re.S)
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

def _stored():
    raw = db.get_setting(KEY) or {}
    return raw if isinstance(raw, dict) else {}


def logo():
    """The stored logo dict or None."""
    lg = _stored().get("logo")
    return lg if isinstance(lg, dict) and lg.get("mime") in MIMES and lg.get("data") else None


def logo_version(lg):
    return hashlib.sha256(f"{lg.get('updatedAt')}|{lg['data']}".encode()).hexdigest()[:12] if lg else None


def public():
    """What the browser may see (no image bytes)."""
    s = _stored()
    lg = logo()
    return {"name": s.get("name") or DEFAULT_NAME, "subtitle": s.get("subtitle") or DEFAULT_SUBTITLE,
            "hasLogo": bool(lg), "logoVersion": logo_version(lg)}


def logo_bytes():
    """(bytes, mime) or None."""
    lg = logo()
    if not lg:
        return None
    try:
        return base64.b64decode(lg["data"]), lg["mime"]
    except (binascii.Error, ValueError):
        return None


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _text(value, field, limit, default):
    if value is None:
        return default
    if not isinstance(value, str):
        raise ApiError("bad_request", f"{field} must be text.", 400)
    value = " ".join(_CTRL.sub(" ", value).split())
    if len(value) > limit:
        raise ApiError("bad_request", f"{field} can be at most {limit} characters.", 400)
    return value or default


def sniff(data):
    """The real image type from magic bytes (None = not an allowed image)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _local(name):
    return name.rsplit("}", 1)[-1].lower() if isinstance(name, str) else ""


def check_svg(data):
    """Raises ApiError unless `data` is a plain, script-free SVG document."""
    bad = ApiError("bad_logo", "This SVG contains scripts, event handlers or external links and can't be used.", 400)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError("bad_logo", "The SVG must be UTF-8 text.", 400)
    low = text.lower()
    # no DTD / entities at all (entity expansion, external entities) and no stylesheet PIs
    if "<!doctype" in low or "<!entity" in low or "<?xml-stylesheet" in low:
        raise bad
    if "<script" in low or "javascript:" in low:
        raise bad
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ApiError("bad_logo", "The SVG file is not valid XML.", 400)
    if _local(root.tag) != "svg":
        raise ApiError("bad_logo", "The file is not an SVG image.", 400)
    for el in root.iter():
        tag = _local(el.tag)
        if tag in ("script", "foreignobject", "iframe", "embed", "object"):
            raise bad
        for attr, val in el.attrib.items():
            name = _local(attr)
            if name.startswith("on"):
                raise bad
            if name == "href" and val.strip() and not val.strip().startswith("#"):
                raise bad
            if "javascript:" in val.lower().replace(" ", ""):
                raise bad
            if name == "style" and re.search(r"url\(\s*['\"]?(?!#)", val, re.I):
                raise bad
        if tag == "style" and el.text and (re.search(r"url\(\s*['\"]?(?!#)", el.text, re.I) or "@import" in el.text.lower()):
            raise bad


def parse_logo(value):
    """data URL -> {"mime", "data"} after full validation."""
    if not isinstance(value, str):
        raise ApiError("bad_logo", "Logo must be a data URL (data:image/png;base64,…).", 400)
    m = _DATA_URL.match(value.strip())
    if not m:
        raise ApiError("bad_logo", "Logo must be a PNG, JPEG, WEBP or SVG image.", 400)
    mime, b64 = m.group(1), re.sub(r"\s+", "", m.group(2))
    if len(b64) > (LOGO_MAX_BYTES // 3 + 1) * 4:
        raise ApiError("logo_too_large", "The logo can be at most 512 KB.", 413)
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError("bad_logo", "The logo data is not valid base64.", 400)
    if not data:
        raise ApiError("bad_logo", "The logo file is empty.", 400)
    if len(data) > LOGO_MAX_BYTES:
        raise ApiError("logo_too_large", "The logo can be at most 512 KB.", 413)
    if mime == "image/svg+xml":
        check_svg(data)
    elif sniff(data) != mime:
        raise ApiError("bad_logo", "The file content doesn't match its image type.", 400)
    return {"mime": mime, "data": base64.b64encode(data).decode("ascii")}


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def update(payload, user):
    """Applies {name?, subtitle?, logo?, removeLogo?}; returns the public view."""
    if not isinstance(payload, dict):
        raise ApiError("bad_request", "Expected a JSON object.", 400)
    cur = _stored()
    old_logo = logo()
    new = {"name": cur.get("name") or DEFAULT_NAME, "subtitle": cur.get("subtitle") or DEFAULT_SUBTITLE,
           "logo": old_logo}
    if "name" in payload:
        new["name"] = _text(payload["name"], "Name", NAME_MAX, DEFAULT_NAME)
    if "subtitle" in payload:
        new["subtitle"] = _text(payload["subtitle"], "Subtitle", SUBTITLE_MAX, DEFAULT_SUBTITLE)
    if payload.get("removeLogo"):
        new["logo"] = None
    elif payload.get("logo"):
        lg = parse_logo(payload["logo"])
        lg["updatedAt"] = db.now_iso()
        new["logo"] = lg

    def summary(name, subtitle, lg):
        return {"name": name, "subtitle": subtitle,
                "logo": {"mime": lg["mime"], "bytes": len(base64.b64decode(lg["data"]))} if lg else None}

    db.set_setting(KEY, new)
    db.audit("settings.branding", user, "settings", "branding",
             old=summary(cur.get("name") or DEFAULT_NAME, cur.get("subtitle") or DEFAULT_SUBTITLE, old_logo),
             new=summary(new["name"], new["subtitle"], new["logo"]))
    return public()
