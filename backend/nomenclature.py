"""
nomenclature.py — Context / Nomenclature engine.

Resolves a raw alarm (projectId, cameraId) into operational context:

    PROJECT › [TC] › CENTRE › ROOM (sub-location) › CAMERA      ← built from what Camview sends
    PROJECT › TC › CENTRE › BUILDING › FLOOR › ROOM › CAMERA     ← optional levels of imported master data

Camview sends project, tcCode (rarely), centerCode / center / city / state,
subLocation and cameraNumber. The tree built from it has exactly those levels;
nothing is invented (no building or floor). Imported master data may add
those levels, and always wins over the automatic tree.
"""

import csv
import io
import threading
import time

import db

LEVELS = ["project", "tc", "centre", "building", "floor", "room", "camera"]
LEVEL_LABELS = {"project": "Project", "tc": "TC", "centre": "Centre", "building": "Building",
                "floor": "Floor", "room": "Room", "camera": "Camera"}
CSV_COLUMNS = ["project_id", "project_code", "project_name", "tc_code", "tc_name",
               "centre_code", "centre_name", "building", "floor", "room", "camera_id", "camera_code", "camera_name"]

_lock = threading.Lock()
_cache = {"version": None, "nodes": None, "by_camera": None, "by_project": None, "children": None}


def _load():
    """Loads all nodes once; reloaded after imports (version bump). The
    version is re-checked at most every 2 s (and immediately after an import
    in this process), not on every resolve."""
    now = time.time()
    if _cache["nodes"] is not None and now - _cache.get("checked", 0) < 2 and _cache.get("db") == db.DB_PATH:
        return _cache
    version = db.get_setting("nomenclature_version", 0)
    _cache["checked"] = now
    with _lock:
        if _cache.get("db") != db.DB_PATH:
            _cache["nodes"] = None
            _cache["db"] = db.DB_PATH
        if _cache["version"] == version and _cache["nodes"] is not None:
            return _cache
        nodes = {r["id"]: r for r in db.rows("SELECT id, level, code, name, parent_id, external_id, meta FROM nomenclature")}
        children = {}
        for n in nodes.values():
            children.setdefault(n["parent_id"], []).append(n["id"])
        by_camera = {n["external_id"]: n["id"] for n in nodes.values() if n["level"] == "camera" and n["external_id"]}
        by_project = {n["external_id"]: n["id"] for n in nodes.values() if n["level"] == "project" and n["external_id"]}
        _cache.update(version=version, nodes=nodes, by_camera=by_camera, by_project=by_project, children=children)
        return _cache


def _node_public(n):
    out = {"id": n["id"], "level": n["level"], "code": n["code"], "name": n["name"] or n["code"],
           "externalId": n["external_id"]}
    if n.get("meta") and "camview" in n["meta"]:
        out["source"] = "camview"          # built automatically from Camview camera data
    return out


def _chain(node_id, nodes):
    chain = []
    while node_id:
        n = nodes.get(node_id)
        if not n:
            break
        chain.append(n)
        node_id = n["parent_id"]
    return list(reversed(chain))


def resolve(project_id, camera_id):
    """Context for an alarm. Never guesses: unknown pieces stay unknown."""
    c = _load()
    nodes = c["nodes"]
    ctx = {"mapped": False, "path": []}
    cam_node = c["by_camera"].get(str(camera_id)) if camera_id not in (None, "") else None
    if cam_node:
        chain = _chain(cam_node, nodes)
        ctx["mapped"] = True
    else:
        proj_node = c["by_project"].get(str(project_id)) if project_id not in (None, "") else None
        chain = _chain(proj_node, nodes) if proj_node else []
    for n in chain:
        pub = _node_public(n)
        ctx[n["level"]] = pub
        ctx["path"].append(pub)
    if not cam_node and camera_id not in (None, ""):
        ctx["camera"] = {"id": None, "level": "camera", "code": f"CAM-{camera_id}", "name": f"Camera {camera_id}",
                         "externalId": str(camera_id), "unmapped": True}
        ctx["path"].append(ctx["camera"])
    if "project" not in ctx and project_id not in (None, ""):
        ctx["project"] = {"id": None, "level": "project", "code": str(project_id),
                          "name": str(project_id), "externalId": str(project_id), "unmapped": True}
        ctx["path"].insert(0, ctx["project"])
    return ctx


AUTO_META = '{"source": "camview"}'


def sync_from_alarms(project_id, items):
    """Adds Project › [TC] › Centre › Location › Camera nodes for cameras that are NOT in the
    imported master data, using only what Camview sends with each camera
    (camera.tcCode / centerCode / center / subLocation / cameraNumber). Imported master data
    always wins; nothing is guessed (no building/floor is invented). The camera node's
    code is Camview's own cameraNumber (e.g. 1001902_0) and its name Camview's subLocation
    (e.g. Camera1); older auto-built nodes still coded CAM-<id> are upgraded in place.
    Returns the number of cameras added."""
    c = _load()
    pid = str(project_id)
    placeholder = c["by_project"].get(pid)
    if placeholder and "camview" in (c["nodes"][placeholder].get("meta") or ""):
        n = c["nodes"][placeholder]
        if n["code"] == f"PROJECT-{pid}" or n["name"] == f"Project {pid}":   # older auto-built node: invented code / name
            db.execute("UPDATE nomenclature SET code=?, name=CASE WHEN name=? THEN NULL ELSE name END WHERE id=?",
                       (pid, f"Project {pid}", placeholder))
            db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
            _cache["checked"] = 0
            c = _load()
    new, upgrade = {}, {}
    for a in items:
        cam = str(a.get("cameraId") or "").strip()
        if not cam or cam in new or cam in upgrade:
            continue
        nid = c["by_camera"].get(cam)
        if nid:
            n = c["nodes"][nid]
            if a.get("cameraNumber") and n["code"] == f"CAM-{cam}" and "camview" in (n.get("meta") or ""):
                upgrade[cam] = (a["cameraNumber"], a.get("cameraSubLocation") or n["name"], nid)
            continue
        if not a.get("centreCode"):
            continue                          # no location from Camview → stays unmapped (Data Quality)
        new[cam] = a
    if upgrade:
        with db.connect() as conn:
            for code, name, nid in upgrade.values():
                conn.execute("UPDATE nomenclature SET code=?, name=? WHERE id=?", (code, name, nid))
        db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
        _cache["checked"] = 0
    if not new:
        return 0
    rows = []
    proj_id = c["by_project"].get(pid) or f"project:PROJECT-{pid}"
    if proj_id not in c["nodes"]:                          # the project's code IS Camview's project id
        rows.append((proj_id, "project", pid, None, None, pid, AUTO_META))
    for cam, a in new.items():
        parent = proj_id
        if a.get("tcCode"):                                   # Camview's own TC code, when it sends one
            parent = f"{proj_id}/tc:{a['tcCode']}"
            rows.append((parent, "tc", a["tcCode"], None, proj_id, None, AUTO_META))
        centre = f"{parent}/centre:{a['centreCode']}"
        rows.append((centre, "centre", a["centreCode"], a.get("centreName"), parent, None, AUTO_META))
        parent = centre
        if a.get("cameraSubLocation"):
            room = f"{parent}/room:{a['cameraSubLocation']}"
            rows.append((room, "room", a["cameraSubLocation"], None, parent, None, AUTO_META))
            parent = room
        code = a.get("cameraNumber") or f"CAM-{cam}"
        name = a.get("cameraSubLocation") or (a.get("cameraName") if a.get("cameraName")
                                              and not str(a.get("cameraName")).startswith("Camera ") else None)
        # node id keyed by Camview's camera id (unique and stable); the code is Camview's camera number
        rows.append((f"{parent}/camera:CAM-{cam}", "camera", code, name, parent, cam, AUTO_META))
    with db.connect() as conn:
        for r in sorted(set(rows), key=lambda r: LEVELS.index(r[1])):
            conn.execute("INSERT OR IGNORE INTO nomenclature (id, level, code, name, parent_id, external_id, meta) "
                         "VALUES (?,?,?,?,?,?,?)", r)
    db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
    _cache["checked"] = 0
    return len(new)


def rename(node_id, name):
    """Give a node (e.g. a project Camview only knows by number) its real name."""
    n = _load()["nodes"].get(node_id)
    if not n:
        return None
    db.execute("UPDATE nomenclature SET name=? WHERE id=?", ((name or "").strip() or None, node_id))
    db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
    _cache["checked"] = 0
    return node(node_id)


def project_levels(project_id):
    """Levels that exist in a project's tree (project and camera excluded) — what "complete" means there."""
    c = _load()
    nid = c["by_project"].get(str(project_id))
    if not nid:
        return set()
    if "levels" not in c or c["levels"].get("version") != c["version"]:
        per = {}
        for n in c["nodes"].values():
            if n["level"] in ("project", "camera"):
                continue
            root = n["id"]
            while c["nodes"].get(root, {}).get("parent_id"):
                root = c["nodes"][root]["parent_id"]
            per.setdefault(root, set()).add(n["level"])
        c["levels"] = {"version": c["version"], "per": per}
    return set(c["levels"]["per"].get(nid, set()))


def projects(include_auto=True):
    """Known projects: imported master data and, unless include_auto is False, projects built
    automatically from Camview camera data (source = "camview"). Empty list if none."""
    c = _load()
    out = [_node_public(c["nodes"][nid]) for nid in c["by_project"].values()]
    if not include_auto:
        out = [p for p in out if p.get("source") != "camview"]
    return sorted(out, key=lambda p: (p["code"] or ""))


def set_project_code(external_id, code, name=None):
    """Gives a project the exam's own code (Camview sends only the number). Creates the project node
    when the project is not in the tree yet. Empty code → back to the Camview id."""
    pid = str(external_id).strip()
    code = (code or "").strip()[:160] or pid
    c = _load()
    nid = c["by_project"].get(pid)
    with db.connect() as conn:
        if nid:
            if name is None:
                conn.execute("UPDATE nomenclature SET code=? WHERE id=?", (code, nid))
            else:
                conn.execute("UPDATE nomenclature SET code=?, name=? WHERE id=?", (code, (name or "").strip() or None, nid))
        else:
            nid = f"project:PROJECT-{pid}"
            conn.execute("INSERT OR IGNORE INTO nomenclature (id, level, code, name, parent_id, external_id, meta) "
                         "VALUES (?,?,?,?,?,?,?)", (nid, "project", code, (name or "").strip() or None, None, pid, AUTO_META))
    db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
    _cache["checked"] = 0
    if code != pid:
        try:                                   # the client and exam follow from the code (policy autoExams)
            import exams
            import workflow
            if workflow.policy().get("autoExams", True):
                exams.ensure_from_project_code(pid, code)
        except Exception:                      # never blocks naming
            import logging
            logging.getLogger("camview.nomenclature").exception("auto exam from project code failed for %s", pid)
    return node(nid)


def parse_code_mapping(text):
    """Lines of "<project id>,<code>" (also ':' or a tab as separator, optional third field = name)."""
    out = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("\t", ",", ":"):
            if sep in line:
                pid, rest = line.split(sep, 1)
                break
        else:
            continue
        pid, rest = pid.strip(), rest.strip()
        name = None
        if "," in rest:
            rest, name = (x.strip() for x in rest.split(",", 1))
        if pid.isdigit() and rest:
            out[pid] = (rest, name or None)
    return out


def apply_project_codes(codes):
    """Applies CAMVIEW_PROJECT_CODES at start-up (only where the code differs)."""
    n = 0
    for pid, code in (codes or {}).items():
        cur = next((p for p in projects() if p["externalId"] == str(pid)), None)
        if not cur or cur["code"] != code:
            set_project_code(pid, code)
            n += 1
    return n


def delete_auto_project(external_id):
    """Removes a project that was built automatically from Camview data (and every node under it).
    Imported master data is never deleted here. Returns the number of nodes removed, or None."""
    c = _load()
    nid = c["by_project"].get(str(external_id))
    if not nid or "camview" not in (c["nodes"][nid].get("meta") or ""):
        return None
    ids, stack = [], [nid]
    while stack:
        cur = stack.pop()
        ids.append(cur)
        stack.extend(c["children"].get(cur, []))
    with db.connect() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            conn.execute(f"DELETE FROM nomenclature WHERE id IN ({','.join('?' for _ in chunk)})", chunk)
    db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
    _cache["checked"] = 0
    return len(ids)


def node(node_id):
    c = _load()
    n = c["nodes"].get(node_id)
    if not n:
        return None
    out = _node_public(n)
    out["path"] = [_node_public(x) for x in _chain(node_id, c["nodes"])]
    out["children"] = [_node_public(c["nodes"][k]) for k in sorted(c["children"].get(node_id, []),
                                                                   key=lambda k: c["nodes"][k]["code"])]
    return out


def descendant_cameras(node_id):
    """External camera ids under a node (inclusive)."""
    c = _load()
    out, stack = set(), [node_id]
    while stack:
        nid = stack.pop()
        n = c["nodes"].get(nid)
        if not n:
            continue
        if n["level"] == "camera" and n["external_id"]:
            out.add(n["external_id"])
        stack.extend(c["children"].get(nid, []))
    return out


def tree():
    c = _load()

    def build(nid):
        n = c["nodes"][nid]
        pub = _node_public(n)
        kids = sorted(c["children"].get(nid, []), key=lambda k: c["nodes"][k]["code"])
        pub["children"] = [build(k) for k in kids]
        return pub

    return [build(k) for k in sorted(c["children"].get(None, []), key=lambda k: c["nodes"][k]["code"])]


def search(q, limit=8):
    q = (q or "").strip().lower()
    if not q:
        return []
    c = _load()
    hits = [n for n in c["nodes"].values()
            if q in (n["code"] or "").lower() or q in (n["name"] or "").lower() or q == (n["external_id"] or "").lower()]
    hits.sort(key=lambda n: (LEVELS.index(n["level"]), n["code"]))
    grouped = {}
    for n in hits:
        grouped.setdefault(n["level"], [])
        if len(grouped[n["level"]]) < limit:
            grouped[n["level"]].append(node(n["id"]))
    return grouped


def projects_for_scope(scopes):
    """Project external ids reachable from TC/centre/camera scope codes."""
    c = _load()
    out = set()
    wanted = {lvl: scopes.get(lvl, set()) for lvl in ("tc", "centre", "room", "camera")}
    for n in c["nodes"].values():
        vals = wanted.get(n["level"])
        if not vals:
            continue
        hit = n["id"] in vals if n["level"] == "room" else \
            (n["code"] in vals or n["id"] in vals or (n["external_id"] and n["external_id"] in vals))
        if hit:
            chain = _chain(n["id"], c["nodes"])
            if chain and chain[0]["level"] == "project" and chain[0]["external_id"]:
                out.add(chain[0]["external_id"])
    return out


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _rows_from_json(data):
    """Nested JSON -> flat rows. Accepts {"projects":[{"projectId","code","name","tcs":[...]}]}."""
    out = []

    def walk(level_idx, items, base):
        level = LEVELS[level_idx]
        plural = {"project": "projects", "tc": "tcs", "centre": "centres", "building": "buildings",
                  "floor": "floors", "room": "rooms", "camera": "cameras"}
        for it in items or []:
            row = dict(base)
            if level == "project":
                row.update(project_id=it.get("projectId", it.get("id")), project_code=it.get("code"),
                           project_name=it.get("name"))
            elif level == "camera":
                row.update(camera_id=it.get("cameraId", it.get("id")), camera_code=it.get("code"),
                           camera_name=it.get("name"))
            elif level in ("building", "floor", "room"):
                row[level] = it.get("code") or it.get("name")
            else:
                row[f"{level}_code"] = it.get("code")
                row[f"{level}_name"] = it.get("name")
            nxt = level_idx + 1
            kids = it.get(plural[LEVELS[nxt]]) if nxt < len(LEVELS) else None
            if kids:
                walk(nxt, kids, row)
            else:
                # allow skipping levels, e.g. centre -> cameras directly
                skipped = False
                for j in range(nxt + 1, len(LEVELS)):
                    if it.get(plural[LEVELS[j]]):
                        walk(j, it[plural[LEVELS[j]]], row)
                        skipped = True
                        break
                if not skipped:
                    out.append(row)

    walk(0, data.get("projects") if isinstance(data, dict) else data, {})
    return out


def _rows_from_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    missing = {"project_id"} - set(reader.fieldnames or [])
    if missing:
        raise ValueError("CSV must have a header row with at least: project_id (see README for all columns).")
    return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in reader]


def import_data(payload, fmt, replace=True):
    """Imports master data. Returns counts per level and any row errors."""
    rows_ = _rows_from_csv(payload) if fmt == "csv" else _rows_from_json(payload)
    errors, nodes = [], {}

    def add(level, code, name, parent, external=None):
        code = str(code).strip()
        nid = f"{parent}/{level}:{code}" if parent else f"{level}:{code}"
        if nid not in nodes:
            nodes[nid] = (nid, level, code, (name or "").strip() or None, parent,
                          None if external in (None, "") else str(external).strip())
        return nid

    for i, r in enumerate(rows_, start=2):
        pid = str(r.get("project_id") or "").strip()
        if not pid:
            errors.append(f"Row {i}: project_id is required.")
            continue
        parent = add("project", r.get("project_code") or pid, r.get("project_name"), None, pid)
        for level, code_key, name_key in (("tc", "tc_code", "tc_name"),
                                          ("centre", "centre_code", "centre_name"), ("building", "building", None),
                                          ("floor", "floor", None), ("room", "room", None)):
            code = r.get(code_key)
            if code not in (None, ""):
                parent = add(level, code, r.get(name_key) if name_key else None, parent)
        cam = str(r.get("camera_id") or "").strip()
        if cam:
            add("camera", r.get("camera_code") or f"CAM-{cam}", r.get("camera_name"), parent, cam)

    cams = [n for n in nodes.values() if n[1] == "camera"]
    seen = {}
    for n in cams:
        if n[5] in seen and seen[n[5]] != n[0]:
            errors.append(f"Camera id {n[5]} appears under two different locations; the last one was kept.")
        seen[n[5]] = n[0]

    with db.connect() as conn:
        if replace:
            conn.execute("DELETE FROM nomenclature")
        # parents first so the foreign key is satisfied
        for n in sorted(nodes.values(), key=lambda n: LEVELS.index(n[1])):
            conn.execute("INSERT OR REPLACE INTO nomenclature (id, level, code, name, parent_id, external_id) "
                         "VALUES (?,?,?,?,?,?)", n)
    db.set_setting("nomenclature_version", (db.get_setting("nomenclature_version", 0) or 0) + 1)
    _cache["checked"] = 0   # reload on next use
    counts = {lvl: sum(1 for n in nodes.values() if n[1] == lvl) for lvl in LEVELS}
    return {"counts": counts, "rows": len(rows_), "errors": errors}


def export_csv():
    c = _load()
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for cam_id in sorted(c["by_camera"].values()):
        chain = _chain(cam_id, c["nodes"])
        row = {}
        for n in chain:
            lvl = n["level"]
            if lvl == "project":
                row.update(project_id=n["external_id"], project_code=n["code"], project_name=n["name"] or "")
            elif lvl == "camera":
                row.update(camera_id=n["external_id"], camera_code=n["code"], camera_name=n["name"] or "")
            elif lvl in ("building", "floor", "room"):
                row[lvl] = n["code"]
            else:
                row[f"{lvl}_code"] = n["code"]
                row[f"{lvl}_name"] = n["name"] or ""
        w.writerow(row)
    return out.getvalue()


def quality(alarms_seen, known_types):
    """Data-quality metrics computed only from master data + alarms actually seen."""
    c = _load()
    nodes = c["nodes"].values()
    cams_seen = {str(a["cameraId"]) for a in alarms_seen if a.get("cameraId") not in (None, "")}
    projects_seen = {str(a["projectId"]) for a in alarms_seen if a.get("projectId") not in (None, "")}
    mapped = cams_seen & set(c["by_camera"])
    codes = {}
    for n in nodes:
        if n["level"] in ("project", "tc", "centre", "camera"):  # codes that should be unique
            codes.setdefault((n["level"], n["code"]), []).append(n["id"])
    duplicates = [{"level": k[0], "code": k[1], "count": len(v)} for k, v in codes.items() if len(v) > 1]
    incomplete = []
    for cam_id in c["by_camera"].values():
        chain = _chain(cam_id, c["nodes"])
        levels = {n["level"] for n in chain}
        expected = project_levels(chain[0]["external_id"]) if chain and chain[0]["level"] == "project" else set()
        # a level is missing only when the project's own tree has it (a Camview-built tree has no building or floor)
        missing = [lvl for lvl in ("tc", "centre", "floor", "room") if lvl in expected and lvl not in levels]
        if missing:
            incomplete.append({"camera": c["nodes"][cam_id]["code"], "missing": missing})
    missing_ts = sum(1 for a in alarms_seen if not a.get("firstInstance") and not a.get("lastInstance"))
    unknown_types = sorted({a["alarmType"] for a in alarms_seen
                            if a.get("alarmType") is not None and a["alarmType"] not in known_types})
    return {
        "masterData": {lvl: sum(1 for n in nodes if n["level"] == lvl) for lvl in LEVELS},
        "camerasSeen": len(cams_seen),
        "mappedCameras": len(mapped),
        "unmappedCameras": sorted(cams_seen - set(c["by_camera"]), key=lambda x: (len(x), x)),
        "unknownProjects": sorted(projects_seen - set(c["by_project"])),
        "missingTc": sum(1 for i in incomplete if "tc" in i["missing"]),
        "missingCentre": sum(1 for i in incomplete if "centre" in i["missing"]),
        "missingFloor": sum(1 for i in incomplete if "floor" in i["missing"]),
        "missingRoom": sum(1 for i in incomplete if "room" in i["missing"]),
        "incompleteContext": incomplete[:200],
        "duplicateCodes": duplicates,
        "unknownAlarmTypes": unknown_types,
        "alarmsMissingTimestamps": missing_ts,
        "alarmsMissingCamera": sum(1 for a in alarms_seen if a.get("cameraId") in (None, "")),
        "alarmsMissingEvidence": sum(1 for a in alarms_seen if not a.get("imageUrls") and not a.get("videoUrl")),
    }
