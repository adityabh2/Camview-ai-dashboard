"""
project_codes.py — the project-code registry (code first).

Operators work with the exam's own project code (Camview's clientProjectId, e.g.
RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL), not with Camview's numeric project ids.

* register(code)   — the Project Lookup API resolves the code to every Camview project carrying it;
                     those projects are monitored and labelled with the code. Nothing is mapped by hand.
* refresh()        — codes are looked up again periodically, so a project Camview adds later under the
                     same code is picked up by itself.
* unregister(code) — its projects stop being monitored (unless another registered code still uses them).
* project_ids(code)— the Camview project ids behind a code (the dashboard filters by code).

Codes from CAMVIEW_PROJECT_CODES (id:code pairs) are carried into the registry once as "configured"
entries, for projects whose code Camview's lookup does not know.
"""

import logging
import threading
import time

import config
import db

log = logging.getLogger("camview.codes")

SETTING = "code_registry"
REFRESH_SECONDS = 15 * 60
_lock = threading.Lock()
_refresh = {"running": False, "at": 0.0}


def _load():
    return db.get_setting(SETTING, {}) or {}


def _save(reg):
    db.set_setting(SETTING, reg)


def _now():
    return db.now_iso()


def entries():
    """Registered codes, oldest first, as a list of dicts."""
    reg = _load()
    out = [{"code": code, **e} for code, e in reg.items()]
    return sorted(out, key=lambda e: e.get("addedAt") or "")


def project_ids(code):
    e = _load().get(str(code or "").strip())
    return [str(p) for p in (e or {}).get("projectIds", [])]


def _monitor(ids, code, names):
    """Monitors `ids` and labels them with `code` (still editable per project in Settings)."""
    import datasource
    import nomenclature
    extras = [str(x) for x in (db.get_setting("extra_projects", []) or [])]
    for pid in ids:
        if pid not in extras and pid != config.DEFAULT_PROJECT_ID:
            extras.append(pid)
        nomenclature.set_project_code(pid, code, names.get(pid))
    db.set_setting("extra_projects", extras)
    datasource.ignore_auto(ids, ignored=False)


def _unmonitor(ids):
    import datasource
    extras = [str(x) for x in (db.get_setting("extra_projects", []) or [])]
    db.set_setting("extra_projects", [x for x in extras if x not in ids])
    datasource.ignore_auto(ids)          # auto-discovery never brings them back by itself


def _dates(projects):
    """{projectId: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}} from the Project Lookup answer (when set)."""
    out = {}
    for p in projects:
        s, e = (str(p.get("startDate") or "")[:10] or None), (str(p.get("endDate") or "")[:10] or None)
        if s or e:
            out[str(p["projectId"])] = {"start": s, "end": e}
    return out


def end_date(code):
    """The day the code's exam ends: the latest end date of its projects (None when Camview has none)."""
    e = _load().get(str(code or "").strip()) or {}
    ends = [d.get("end") for d in (e.get("dates") or {}).values() if d.get("end")]
    return max(ends) if ends else None


def register(code, user=None):
    """Looks the code up in Camview and registers it. Raises ApiError when nothing carries the code."""
    from camview_client import ApiError, lookup_projects
    code = str(code or "").strip()
    projects = lookup_projects(code)                  # validates the code and raises on key / network errors
    if not projects:
        raise ApiError("not_found", f"No Camview project has the code {code}. The code must match exactly "
                                    "(it is the client project id set on the project in Camview).", 404)
    ids = [str(p["projectId"]) for p in projects]
    names = {str(p["projectId"]): p.get("name") for p in projects}
    dates = _dates(projects)
    with _lock:
        reg = _load()
        before = reg.get(code, {})
        reg[code] = {"projectIds": sorted(set(ids) | set(before.get("projectIds", [])), key=int),
                     "names": {**before.get("names", {}), **{k: v for k, v in names.items() if v}},
                     "dates": {**before.get("dates", {}), **dates},
                     "source": "lookup", "addedAt": before.get("addedAt") or _now(), "resolvedAt": _now(), "error": None}
        _save(reg)
    _monitor(ids, code, names)
    import datasource
    datasource.reset()
    db.audit("codes.register", user, "project_code", code, None, {"projectIds": ids})
    return {"code": code, **_load()[code]}


def unregister(code, user=None):
    from camview_client import ApiError
    code = str(code or "").strip()
    with _lock:
        reg = _load()
        if code not in reg:
            raise ApiError("not_found", "This project code is not registered.", 404)
        ids = set(reg.pop(code).get("projectIds", []))
        still = {p for e in reg.values() for p in e.get("projectIds", [])}
        _save(reg)
    gone = sorted((ids - still) - {config.DEFAULT_PROJECT_ID}, key=int)
    _unmonitor(set(gone))
    import datasource
    datasource.reset()
    db.audit("codes.unregister", user, "project_code", code, None, {"stoppedMonitoring": gone})
    return {"code": code, "stoppedMonitoring": gone}


def refresh(force=False):
    """Looks every registered code up again (new projects under a known code are monitored)."""
    if config.MODE != "live" or _refresh["running"] or (not force and time.time() - _refresh["at"] < REFRESH_SECONDS):
        return False
    _refresh.update(running=True, at=time.time())
    try:
        from camview_client import ApiError, lookup_projects
        for e in entries():
            if e.get("source") != "lookup" or e.get("archived"):
                continue
            code = e["code"]
            try:
                projects = lookup_projects(code)
            except ApiError as err:
                with _lock:
                    reg = _load()
                    if code in reg:
                        reg[code]["error"] = err.message
                        _save(reg)
                continue
            ids = [str(p["projectId"]) for p in projects]
            names = {str(p["projectId"]): p.get("name") for p in projects}
            with _lock:
                reg = _load()
                if code not in reg:
                    continue
                new = [p for p in ids if p not in reg[code].get("projectIds", [])]
                reg[code].update(projectIds=sorted(set(reg[code].get("projectIds", [])) | set(ids), key=int),
                                 resolvedAt=_now(), error=None)
                reg[code]["names"] = {**reg[code].get("names", {}), **{k: v for k, v in names.items() if v}}
                reg[code]["dates"] = {**reg[code].get("dates", {}), **_dates(projects)}
                _save(reg)
            if new:
                _monitor(new, code, names)
                _grant_clients(code, new)
                log.info("Project code %s: new Camview project(s) %s now monitored", code, ", ".join(new))
        return True
    finally:
        _refresh["running"] = False


def refresh_soon():
    """Non-blocking: refreshes in the background when the last refresh is older than REFRESH_SECONDS."""
    if config.MODE != "live" or _refresh["running"] or time.time() - _refresh["at"] < REFRESH_SECONDS:
        return
    threading.Thread(target=_safe_refresh, name="codes-refresh", daemon=True).start()


def _safe_refresh():
    try:
        refresh()
    except Exception:                                  # never breaks a screen
        log.exception("Project code refresh failed")


# ---- clients are linked to project CODES (the client's id for its exam), never to Camview numbers -------------

CLIENT_SETTING = "client_codes"


def client_codes(client_id):
    return list((db.get_setting(CLIENT_SETTING, {}) or {}).get(str(client_id), []))


def resolve(codes, user=None):
    """Project codes → Camview project ids. A code that is not registered yet is looked up and registered."""
    ids = []
    for code in [str(c).strip() for c in codes or [] if str(c).strip()]:
        if code not in _load():
            register(code, user)
        ids += [p for p in project_ids(code) if p not in ids]
    return ids


def set_client_codes(client_id, codes):
    allc = db.get_setting(CLIENT_SETTING, {}) or {}
    clean = []
    for c in codes or []:
        c = str(c).strip()
        if c and c not in clean:
            clean.append(c)
    if clean:
        allc[str(client_id)] = clean
    else:
        allc.pop(str(client_id), None)
    db.set_setting(CLIENT_SETTING, allc)
    return clean


def _grant_clients(code, ids):
    """New projects under a code reach every client linked to that code."""
    linked = [cid for cid, cs in (db.get_setting(CLIENT_SETTING, {}) or {}).items() if code in cs]
    if not linked or not ids:
        return
    with db.connect() as conn:
        conn.executemany("INSERT OR IGNORE INTO client_projects (client_id, project_id) VALUES (?,?)",
                         [(cid, p) for cid in linked for p in ids])
    db.audit("codes.client_access", None, "project_code", code, None, {"clients": linked, "projectIds": list(ids)})


def seed_from_config():
    """CAMVIEW_PROJECT_CODES (id:code) → registry entries, once per code, without a lookup."""
    if not config.PROJECT_CODES:
        return 0
    added = 0
    with _lock:
        reg = _load()
        for pid, code in config.PROJECT_CODES.items():
            e = reg.setdefault(code, {"projectIds": [], "names": {}, "source": "configured", "addedAt": _now(),
                                      "resolvedAt": None, "error": None})
            if pid not in e["projectIds"]:
                e["projectIds"] = sorted(set(e["projectIds"]) | {pid}, key=int)
                added += 1
        if added:
            _save(reg)
    return added


# ---- archive (codes): monitoring stops, everything is kept; only an administrator deletes ----------------------

def archive_code(code, user=None):
    from camview_client import ApiError
    code = str(code or "").strip()
    with _lock:
        reg = _load()
        if code not in reg:
            raise ApiError("not_found", "This project code is not registered.", 404)
        reg[code]["archived"] = True
        reg[code]["archivedAt"] = _now()
        reg[code]["archivedBy"] = (user or {}).get("name")
        ids = set(reg[code].get("projectIds", []))
        still = {p for c, e in reg.items() if c != code and not e.get("archived") for p in e.get("projectIds", [])}
        _save(reg)
    stopped = sorted((ids - still) - {config.DEFAULT_PROJECT_ID}, key=int)
    _unmonitor(set(stopped))
    import datasource
    datasource.reset()
    db.audit("codes.archive", user, "project_code", code, "active", "archived", {"stoppedMonitoring": stopped})
    return {"code": code, "archived": True, "stoppedMonitoring": stopped}


def restore_code(code, user=None):
    from camview_client import ApiError
    code = str(code or "").strip()
    with _lock:
        reg = _load()
        if code not in reg or not reg[code].get("archived"):
            raise ApiError("not_found", "This project code is not archived.", 404)
        reg[code].pop("archived", None)
        reg[code].pop("archivedAt", None)
        reg[code].pop("archivedBy", None)
        ids = [str(p) for p in reg[code].get("projectIds", [])]
        names = reg[code].get("names", {})
        _save(reg)
    _monitor(ids, code, names)
    import datasource
    datasource.reset()
    db.audit("codes.restore", user, "project_code", code, "archived", "active", {"projectIds": ids})
    return {"code": code, "archived": False, "projectIds": ids}


def delete_code(code, user=None):
    """Administrator only, archived codes only: unregistered, and the centres / cameras built from its Camview data
    are removed. Clients, reports and the audit trail are kept."""
    from camview_client import ApiError
    import archive
    if not archive.is_admin(user):
        raise ApiError("forbidden", "Only an administrator can delete.", 403)
    code = str(code or "").strip()
    e = _load().get(code)
    if not e:
        raise ApiError("not_found", "This project code is not registered.", 404)
    if not e.get("archived"):
        raise ApiError("not_eligible", "Archive the project code first; only archived codes can be deleted.", 409)
    ids = [str(p) for p in e.get("projectIds", [])]
    with _lock:
        reg = _load()
        reg.pop(code, None)
        _save(reg)
    import nomenclature
    removed = 0
    for pid in ids:
        if pid != config.DEFAULT_PROJECT_ID:
            removed += nomenclature.delete_auto_project(pid) or 0
    import datasource
    datasource.reset()
    db.audit("codes.delete", user, "project_code", code, "archived", "deleted", {"projectIds": ids, "nodesRemoved": removed})
    return {"code": code, "deleted": True, "nodesRemoved": removed}
