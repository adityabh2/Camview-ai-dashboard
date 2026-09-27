"""
reports.py — Report Center.

    Internal dataset -> permission/scope filter -> (client firewall) -> report

Internal reports use the generating user's in-scope working set.
Client reports are built ONLY from the client dataset produced by
workflow.build_client_visible_alarm for the chosen client, never from raw
internal objects. Every report carries its audience, scope, data range,
generator and visibility.
"""

import csv
import io
from datetime import datetime, timedelta, timezone

import analytics
import db
import workflow

REPORT_TYPES = {
    "alarm_summary": ("Alarm Summary", "internal"),
    "critical": ("Critical Alarm Report", "internal"),
    "project": ("Project Report", "internal"),
    "tc": ("TC Report", "internal"),
    "centre": ("Centre Report", "internal"),
    "shift": ("Shift Report", "internal"),
    "camera_activity": ("Camera Activity", "internal"),
    "investigation": ("Investigation Report", "internal"),
    "client_shared_internal": ("Client Shared Report (internal view)", "internal"),
    "management": ("Management Summary", "internal"),
    "client_shared": ("Shared Alert Report", "client"),
    "client_summary": ("Client Summary", "client"),
}

FILTER_KEYS = ("exam", "tc", "centre", "camera", "alarmType", "priority", "lastActionType", "visibility",
               "workflowState", "shiftLabel")


def _ts(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def apply_filters(alarms, f):
    out = []
    start = _ts(f.get("from")) if f.get("from") else None
    end = _ts(f.get("to")) if f.get("to") else None
    for a in alarms:
        if a.get("eventKind") == "camera_status" and str(f.get("alarmType") or "") != str(a.get("alarmType")):
            continue                                  # camera online/offline events are not alerts to report
        ctx = a.get("context") or {}
        ok = True
        if f.get("exam") and (a.get("exam") or {}).get("id") != f["exam"]:
            ok = False
        for lvl in ("tc", "centre"):
            if f.get(lvl) and (ctx.get(lvl) or {}).get("code") != f[lvl]:
                ok = False
        if f.get("camera") and str(a.get("cameraId")) != str(f["camera"]) and a.get("cameraCode") != f["camera"]:
            ok = False
        if f.get("alarmType") not in (None, "") and str(a.get("alarmType")) != str(f["alarmType"]):
            ok = False
        if f.get("priority") and a.get("priority") != f["priority"]:
            ok = False
        if f.get("lastActionType") not in (None, "") and str(a.get("lastActionType")) != str(f["lastActionType"]):
            ok = False
        if f.get("visibility") and (a.get("visibility") or {}).get("state") != f["visibility"]:
            ok = False
        if f.get("workflowState") and a.get("workflowState") != f["workflowState"]:
            ok = False
        if f.get("shiftLabel") and a.get("shiftLabel") != f["shiftLabel"]:
            ok = False
        t = _ts(a.get("firstInstance") or a.get("lastInstance"))
        if start and (not t or t < start):
            ok = False
        if end and (not t or t > end):
            ok = False
        if ok:
            out.append(a)
    return out


def _alarm_rows(alarms, limit=2000):
    cols = ["Alarm ID", "Raised", "Last seen", "Type", "Priority", "Camview state", "Ops review", "Workflow",
            "Client visibility", "Project", "TC", "Centre", "Room", "Camera", "Occurrences", "Shift",
            "Evidence", "Ticket"]
    rows = []
    for a in alarms[:limit]:
        ctx = a.get("context") or {}
        rows.append([a["alarmId"], a.get("firstInstance"), a.get("lastInstance"), a.get("alarmTypeName"),
                     a.get("priority"), a.get("lastActionLabel"), (a.get("review") or {}).get("status"),
                     a.get("workflowLabel"), (a.get("visibility") or {}).get("label"),
                     (ctx.get("project") or {}).get("code"),
                     (ctx.get("tc") or {}).get("code"), (ctx.get("centre") or {}).get("code"),
                     (ctx.get("room") or {}).get("code"), a.get("cameraCode"), a.get("totalTimesReported"),
                     a.get("shiftLabel"), (a.get("evidence") or {}).get("count"), a.get("ticketId")])
    return cols, rows


def _kv(title, pairs):
    return {"title": title, "kind": "kv", "rows": [[k, v] for k, v in pairs]}


def _table(title, columns, rows, note=None):
    return {"title": title, "kind": "table", "columns": columns, "rows": rows, "note": note}


def _breakdown_section(title, rows):
    return _table(title, ["Code", "Name", "Total", "Critical", "Pending", "Valid", "Invalid", "Exception", "False-alarm rate"],
                  [[r["code"], r["name"], r["total"], r["critical"], r["pending"], r["valid"], r["invalid"],
                    r["exception"], None if r["falseAlarmRate"] is None else f"{r['falseAlarmRate'] * 100:.0f}%"]
                   for r in rows])


def build_internal(rtype, user, alarms, filters, project_label, alarm=None):
    title, audience = REPORT_TYPES[rtype]
    data = apply_filters(alarms, filters)
    m = analytics.metrics(data)
    sections = []
    summary = [("Alarms", m["total"]), ("Critical", m["critical"]), ("Pending (Camview)", m["pending"]),
               ("Valid (Camview)", m["valid"]), ("Invalid (Camview)", m["invalid"]),
               ("Exception (Camview)", m["exception"]), ("Suppressed", m["suppressed"]),
               ("Marked valid by Ops", m["opsValid"]), ("Marked invalid by Ops", m["opsInvalid"]),
               ("Ready for client", m["readyForClient"]), ("Awaiting approval", m["awaitingApproval"]),
               ("Approved (not yet shared)", m["approvedForClient"]), ("Shared with client", m["sharedWithClient"]),
               ("False-alarm rate", None if m["falseAlarmRate"] is None else f"{m['falseAlarmRate'] * 100:.1f}%")]
    if rtype == "investigation":
        if not alarm:
            raise ValueError("An alarm is required for an investigation report.")
        ctx = alarm.get("context") or {}
        sections.append(_kv("Alarm", [("Alarm ID", alarm["alarmId"]), ("Type", alarm.get("alarmTypeName")),
                                      ("Priority", alarm.get("priority")), ("Camview state", alarm.get("lastActionLabel")),
                                      ("Ops review", (alarm.get("review") or {}).get("status")),
                                      ("Workflow", alarm.get("workflowLabel")),
                                      ("Client visibility", (alarm.get("visibility") or {}).get("label")),
                                      ("First instance", alarm.get("firstInstance")),
                                      ("Last instance", alarm.get("lastInstance")),
                                      ("Occurrences", alarm.get("totalTimesReported")), ("Ticket", alarm.get("ticketId")),
                                      ("Shift", alarm.get("shiftLabel")), ("Suppressed", alarm.get("suppressed")),
                                      ("Evidence items", (alarm.get("evidence") or {}).get("count"))]))
        sections.append(_kv("Context", [(n["level"].title(), f"{n['code']} — {n.get('name') or ''}")
                                        for n in ctx.get("path", [])] or [("Context", "Not mapped")]))
        sections.append(_table("Internal notes", ["Type", "Author", "At", "Note"],
                               [[n["kindLabel"], n["author"], n["createdAt"], n["body"]]
                                for n in workflow.notes_for(alarm["alarmId"])]))
        sections.append(_table("Client sharing", ["Client", "Status", "Requested", "Approved", "Shared", "Acknowledged"],
                               [[p["clientName"], p["statusLabel"], f"{p['requestedBy'] or ''} {p['requestedAt'] or ''}",
                                 f"{p['approvedBy'] or ''} {p['approvedAt'] or ''}", f"{p['sharedBy'] or ''} {p['sharedAt'] or ''}",
                                 p["acknowledgedAt"]] for p in alarm.get("publications", [])]))
        sections.append(_table("Audit trail", ["When", "Who", "Action", "Note"],
                               [[e["created_at"], e["operator"], e["action"], e["note"]]
                                for e in db.get_audit_trail(alarm["alarmId"])]))
        record_count = 1
    else:
        sections.append(_kv("Summary", summary))
        if rtype in ("alarm_summary", "management", "project"):
            sections.append(_table("By priority", ["Priority", "Count"],
                                   [[d["label"], d["count"]] for d in analytics.priority_distribution(data)]))
            sections.append(_table("By alarm type", ["Type", "Count"],
                                   [[d["label"], d["count"]] for d in analytics.type_distribution(data)]))
        if rtype in ("project", "tc", "management"):
            sections.append(_breakdown_section("By TC", analytics.level_breakdown(data, "tc")))
        if rtype in ("project", "centre", "tc"):
            sections.append(_breakdown_section("By centre", analytics.level_breakdown(data, "centre")))
        if rtype in ("shift", "management", "alarm_summary"):
            sections.append(_table("By shift", ["Shift", "Count"],
                                   [[d["label"], d["count"]] for d in analytics.shift_distribution(data)]))
        if rtype in ("camera_activity", "management", "centre"):
            sections.append(_table("Most active cameras", ["Camera", "Location", "Alarms", "Critical", "Pending",
                                                           "Occurrences", "Latest"],
                                   [[c["code"], c["location"], c["count"], c["critical"], c["pending"],
                                     c["occurrences"], c["latest"]] for c in analytics.top_cameras(data, 25)]))
        subset = data
        if rtype == "critical":
            subset = [a for a in data if a.get("priority") == "critical"]
        if rtype == "client_shared_internal":
            subset = [a for a in data if (a.get("visibility") or {}).get("state") in ("shared", "approved", "withdrawn")]
        if rtype != "management":
            cols, rows_ = _alarm_rows(subset)
            sections.append(_table("Alarms", cols, rows_,
                                   note=f"Showing {len(rows_)} of {len(subset)}" if len(subset) > len(rows_) else None))
        record_count = len(subset)
    return {"meta": _meta(title, rtype, "internal", user, filters, project_label, record_count,
                          "INTERNAL — may contain internal operational data. Do not send to clients."),
            "sections": sections}


def client_dataset_for(client_id):
    """The client dataset for a client, built through the same firewall a
    client user goes through."""
    c = db.one("SELECT id, name, status FROM clients WHERE id=?", (client_id,))
    if not c:
        raise ValueError("Unknown client.")
    pseudo = {"audience": "client", "permissions": {"client.portal", "client.evidence"}, "clientId": c["id"],
              "clientProjects": {r["project_id"] for r in db.rows("SELECT project_id FROM client_projects WHERE client_id=?",
                                                                  (c["id"],))}}
    return c, workflow.client_visible_alarms(pseudo)


def build_client(rtype, user, client_id, filters):
    title, _ = REPORT_TYPES[rtype]
    client, data = client_dataset_for(client_id)
    start = _ts(filters.get("from")) if filters.get("from") else None
    end = _ts(filters.get("to")) if filters.get("to") else None
    if start or end:
        data = [a for a in data if (not start or (_ts(a.get("firstInstance")) or start) >= start)
                and (not end or (_ts(a.get("firstInstance")) or end) <= end)]
    if filters.get("priority"):
        data = [a for a in data if a.get("priority") == filters["priority"]]
    if filters.get("exam"):
        data = [a for a in data if (a.get("exam") or {}).get("id") == filters["exam"]]
    pri = analytics.priority_distribution(data)
    sections = [
        _kv("Summary", [("Client", client["name"]), ("Shared alerts", len(data)),
                        ("Critical", sum(1 for a in data if a.get("priority") == "critical")),
                        ("Acknowledged", sum(1 for a in data if a.get("acknowledgedAt")))]),
        _table("By priority", ["Priority", "Count"], [[d["label"], d["count"]] for d in pri]),
    ]
    if rtype == "client_shared":
        sections.append(_table("Shared alerts", ["Alarm ID", "Type", "Priority", "Raised", "Location", "Summary",
                                                 "Shared at", "Acknowledged"],
                               [[a["alarmId"], a.get("alarmTypeName"), a.get("priority"), a.get("firstInstance"),
                                 " / ".join(x["code"] for x in a.get("context", [])), a.get("summary"),
                                 a.get("sharedAt"), a.get("acknowledgedAt") or "—"] for a in data]))
    return {"meta": _meta(title, rtype, "client", user, filters, client["name"], len(data),
                          f"CLIENT — contains only alerts shared with {client['name']}.", client=client),
            "sections": sections, "clientId": client["id"]}


def _meta(title, rtype, audience, user, filters, scope_label, count, visibility, client=None):
    rng = "All available data"
    if filters.get("from") or filters.get("to"):
        rng = f"{filters.get('from') or '…'} → {filters.get('to') or 'now'}"
    return {"title": title, "type": rtype, "audience": audience,
            "scope": scope_label, "filters": {k: v for k, v in filters.items() if v not in (None, "")},
            "dataRange": rng, "generatedBy": user["name"], "generatedAt": db.now_iso(),
            "visibility": visibility, "recordCount": count, "client": client["name"] if client else None}


def save(report, user, client_id=None):
    meta = report["meta"]
    rid = db.execute("INSERT INTO reports (title, type, audience, client_id, params, data, generated_by, "
                     "generated_by_name, generated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (meta["title"], meta["type"], meta["audience"], client_id, db.jdump(meta["filters"]),
                      db.jdump(report), user["id"], user["name"], meta["generatedAt"]))
    db.audit("report.generate", user, "report", rid, None, meta["title"], client_id=client_id,
             details={"audience": meta["audience"], "records": meta["recordCount"]})
    return rid


def to_csv(report):
    out = io.StringIO()
    w = csv.writer(out)
    m = report["meta"]
    for k in ("title", "audience", "scope", "dataRange", "generatedBy", "generatedAt", "visibility", "recordCount"):
        w.writerow([k, m.get(k)])
    for s in report["sections"]:
        w.writerow([])
        w.writerow([s["title"].upper()])
        if s["kind"] == "table":
            w.writerow(s["columns"])
        for r in s["rows"]:
            w.writerow(["" if v is None else v for v in r])
    return out.getvalue()


def default_range(days):
    end = datetime.now(timezone.utc)
    return (end - timedelta(days=days)).isoformat(), end.isoformat()
