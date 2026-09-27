"""
exams.py — Exams and automatic client resolution.

    Client ─< Exam ─< Project(s)          (configured once by an administrator)

For every alert the system resolves, automatically:
    projectId (+ alarm time)  →  Exam  →  Client
An operator never selects the exam or the client. A client choice is only
asked for when an alert genuinely maps to more than one client.

Saving an exam also grants its client access to the exam's projects
(client_projects), so the client firewall follows the same mapping.
"""

import re
import time
import uuid

import db
from camview_client import ApiError


def _row(r):
    return {"id": r["id"], "code": r["code"], "name": r["name"], "clientId": r["client_id"],
            "clientName": r.get("client_name"), "projectIds": db.jload(r["project_ids"], []) or [],
            "startDate": r["start_date"], "endDate": r["end_date"], "status": r["status"], "createdAt": r["created_at"]}


def list_exams(include_inactive=True):
    q = "SELECT e.*, c.name AS client_name FROM exams e LEFT JOIN clients c ON c.id = e.client_id"
    if not include_inactive:
        q += " WHERE e.status='active'"
    return [_row(r) for r in db.rows(q + " ORDER BY c.name, e.name")]


def get(exam_id):
    r = db.one("SELECT e.*, c.name AS client_name FROM exams e LEFT JOIN clients c ON c.id=e.client_id WHERE e.id=?",
               (exam_id,))
    return _row(r) if r else None


_cache = {"ver": None, "by_project": {}}


def _index():
    """Exams by project. The version setting is re-checked at most every 2 s (and right
    after any change in this process) — not once per alert, which cost a DB round-trip each."""
    now = time.time()
    if _cache["ver"] is not None and _cache["ver"][0] == db.DB_PATH and now - _cache.get("checked", 0) < 2:
        return _cache["by_project"]
    _cache["checked"] = now
    ver = db.get_setting("exams_version", 0)
    if _cache["ver"] != (db.DB_PATH, ver):
        by_project = {}
        for e in list_exams(include_inactive=False):
            for pid in e["projectIds"]:
                by_project.setdefault(str(pid), []).append(e)
        _cache.update(ver=(db.DB_PATH, ver), by_project=by_project)
    return _cache["by_project"]


def _bump():
    db.set_setting("exams_version", (db.get_setting("exams_version", 0) or 0) + 1)
    _cache["checked"] = 0


def resolve(project_id, when_iso=None):
    """The exam an alert belongs to. With several exams on one project the
    one whose date window contains the alarm time wins; otherwise None is
    returned only if nothing is configured (never guessed)."""
    candidates = _index().get(str(project_id), [])
    if not candidates:
        return None
    if len(candidates) == 1 or not when_iso:
        return candidates[0]
    day = (when_iso or "")[:10]
    dated = [e for e in candidates if (not e["startDate"] or e["startDate"] <= day) and (not e["endDate"] or day <= e["endDate"])]
    return (dated or candidates)[0]


def clients_for(project_id, exam=None):
    """Clients an alert can be delivered to: the exam's client if configured,
    otherwise every client mapped to the project."""
    if exam and exam.get("clientId"):
        c = db.one("SELECT id, name, status FROM clients WHERE id=?", (exam["clientId"],))
        return [c] if c else []
    return db.rows("SELECT c.id, c.name, c.status FROM clients c JOIN client_projects cp ON cp.client_id=c.id "
                   "WHERE cp.project_id=? ORDER BY c.name", (str(project_id),))


# ---------------------------------------------------------------------------
# Client + exam derived from the project code
#   MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL
#   └ client  └ exam (year dropped from the name) └ date (DDMMYY) — the rest is ignored
# ---------------------------------------------------------------------------

_YEAR = re.compile(r"-(?:19|20)\d{2}$")


def parse_project_code(code):
    """→ {"client": "MPESB", "exam": "G2SG4-CRT-2026", "examBase": "G2SG4-CRT", "date": "2026-09-22"} or None."""
    parts = [p.strip() for p in str(code or "").split("/") if p.strip()]
    if len(parts) < 2:
        return None
    client, exam = parts[0].upper(), parts[1].upper()
    date = None
    if len(parts) > 2 and re.fullmatch(r"\d{6}", parts[2]):
        d, m, y = parts[2][:2], parts[2][2:4], parts[2][4:]
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            date = f"20{y}-{m}-{d}"
    return {"client": client, "exam": exam, "examBase": _YEAR.sub("", exam), "date": date}


def _project_code(pid):
    import nomenclature
    return next((p["code"] for p in nomenclature.projects() if p["externalId"] == str(pid)), None)


def ensure_from_project_code(project_id, code, user=None):
    """Creates (once) the client and the exam a project belongs to, from its project code, and attaches the
    project. The exam is named CLIENT/EXAM without the year (MPESB/G2SG4-CRT); a second project of the same
    exam (another date or shift) joins it. Names never collide: if the same exam of ANOTHER year already
    exists, the new one keeps its year (MPESB/G2SG4-CRT-2025). A project already mapped to an exam is left
    alone. Returns the exam or None."""
    pid = str(project_id)
    parsed = parse_project_code(code)
    if not parsed:
        return None
    exams_all = list_exams(include_inactive=True)
    if any(pid in e["projectIds"] for e in exams_all):
        return None
    # client: by name (case-insensitive), created when missing
    client = db.one("SELECT id, name FROM clients WHERE UPPER(name)=?", (parsed["client"],))
    if not client:
        cid = "client-" + uuid.uuid4().hex[:8]
        db.execute("INSERT INTO clients (id, name, status, contact, notification_prefs, visibility_policy, created_at) "
                   "VALUES (?,?,?,?,?,?,?)", (cid, parsed["client"], "active", "", db.jdump({"inApp": True}),
                                              db.jdump({"showTicket": False}), db.now_iso()))
        db.audit("client.auto_create", user, "client", cid, None, {"name": parsed["client"], "fromProjectCode": code},
                 client_id=cid)
        client = {"id": cid, "name": parsed["client"]}
    short, full = f"{parsed['client']}/{parsed['examBase']}", f"{parsed['client']}/{parsed['exam']}"
    by_code = {e["code"].upper(): e for e in exams_all}

    def same_exam(e):
        """The existing exam is the same exam-year when any of its projects' codes says so."""
        for other in e["projectIds"]:
            p = parse_project_code(_project_code(other) or "")
            if p and p["client"] == parsed["client"]:
                return p["exam"] == parsed["exam"]
        return e["code"].upper() == full
    target = None
    if full in by_code:
        target = by_code[full]
    elif short in by_code and same_exam(by_code[short]):
        target = by_code[short]
    if target:
        projects = [*target["projectIds"], pid]
        start = min(filter(None, [target["startDate"], parsed["date"]]), default=None)
        end = max(filter(None, [target["endDate"], parsed["date"]]), default=None)
        db.execute("UPDATE exams SET project_ids=?, start_date=?, end_date=? WHERE id=?",
                   (db.jdump(projects), start, end, target["id"]))
        _grant(target["clientId"] or client["id"], projects)
        _bump()
        db.audit("exam.auto_attach", user, "exam", target["id"], None, {"project": pid, "fromProjectCode": code},
                 client_id=target["clientId"])
        return get(target["id"])
    name = full if short in by_code else short          # another year of the same exam exists → keep the year
    eid = "exam-" + uuid.uuid4().hex[:8]
    db.execute("INSERT INTO exams (id, code, name, client_id, project_ids, start_date, end_date, status, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (eid, name, name, client["id"], db.jdump([pid]), parsed["date"], parsed["date"],
                                             "active", db.now_iso()))
    _grant(client["id"], [pid])
    _bump()
    db.audit("exam.auto_create", user, "exam", eid, None, {"name": name, "client": client["id"], "project": pid,
                                                           "fromProjectCode": code}, client_id=client["id"])
    return get(eid)


def _validate(b):
    name = str(b.get("name") or "").strip()
    code = str(b.get("code") or "").strip() or re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:24]
    if not name:
        raise ApiError("bad_request", "Exam name is required.", 400)
    projects = [str(p).strip() for p in (b.get("projectIds") or []) if str(p).strip()]
    if not projects:
        raise ApiError("bad_request", "Map the exam to at least one Camview project ID.", 400)
    if any(not p.isdigit() for p in projects):
        raise ApiError("bad_request", "Project IDs are the numeric Camview projectId values.", 400)
    client_id = b.get("clientId") or None
    if client_id and not db.one("SELECT 1 FROM clients WHERE id=?", (client_id,)):
        raise ApiError("bad_request", "Unknown client.", 400)
    for k in ("startDate", "endDate"):
        v = b.get(k)
        if v and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(v)):
            raise ApiError("bad_request", f"{k} must be YYYY-MM-DD.", 400)
    status = b.get("status") or "active"
    if status not in ("active", "inactive"):
        raise ApiError("bad_request", "Status must be active or inactive.", 400)
    return code, name, client_id, projects, b.get("startDate") or None, b.get("endDate") or None, status


def _grant(client_id, projects):
    if client_id:
        with db.connect() as conn:
            for p in projects:
                conn.execute("INSERT OR IGNORE INTO client_projects (client_id, project_id) VALUES (?,?)", (client_id, p))


def create(b, user):
    code, name, client_id, projects, start, end, status = _validate(b)
    eid = "exam-" + uuid.uuid4().hex[:8]
    db.execute("INSERT INTO exams (id, code, name, client_id, project_ids, start_date, end_date, status, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (eid, code, name, client_id, db.jdump(projects), start, end, status, db.now_iso()))
    _grant(client_id, projects)
    _bump()
    db.audit("exam.create", user, "exam", eid, None, {"name": name, "client": client_id, "projects": projects},
             client_id=client_id)
    return get(eid)


def update(exam_id, b, user):
    old = get(exam_id)
    if not old:
        raise ApiError("not_found", "Exam not found.", 404)
    merged = {"code": old["code"], "name": old["name"], "clientId": old["clientId"], "projectIds": old["projectIds"],
              "startDate": old["startDate"], "endDate": old["endDate"], "status": old["status"], **b}
    code, name, client_id, projects, start, end, status = _validate(merged)
    db.execute("UPDATE exams SET code=?, name=?, client_id=?, project_ids=?, start_date=?, end_date=?, status=? WHERE id=?",
               (code, name, client_id, db.jdump(projects), start, end, status, exam_id))
    _grant(client_id, projects)
    _bump()
    db.audit("exam.update", user, "exam", exam_id, old, {"name": name, "client": client_id, "projects": projects,
                                                           "status": status}, client_id=client_id)
    return get(exam_id)


def seed(rows):
    """Demo seeding helper: rows = [(id, code, name, client_id, [projects])]."""
    with db.connect() as conn:
        for eid, code, name, client_id, projects in rows:
            conn.execute("INSERT OR REPLACE INTO exams (id, code, name, client_id, project_ids, status, created_at) "
                         "VALUES (?,?,?,?,?, 'active', ?)", (eid, code, name, client_id, db.jdump(projects), db.now_iso()))
    for _, _, _, client_id, projects in rows:
        _grant(client_id, projects)
    _bump()
