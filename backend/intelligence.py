"""
intelligence.py — Explainable intelligent alerts.

Every alert is DERIVED from alarm data the user may see, and carries:
  title, category, scope {level, code}, severity, timestamp,
  reasons  (the "Why am I seeing this?" bullet list),
  inputs   (the numbers and thresholds used),
  related  (alarm ids), camera, action (where to go).

No opaque scores. A rule that lacks data (e.g. a spike check with less than
6 hours of history, or a location alert without nomenclature) is skipped and
reported in `skipped` with the reason.
"""

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import db

SEVERITY_ORDER = {"critical": 0, "high": 1, "warning": 2, "info": 3}


def _ts(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, ValueError, TypeError):
        return None


def _aid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _human(minutes):
    m = float(minutes or 0)
    if m < 60:
        return f"{int(m)} min"
    if m < 1440:
        return f"{int(m // 60)} h {int(m % 60)} min"
    return f"{m / 1440:.1f} days"


def _cam(a):
    return a.get("cameraCode") or f"CAM-{a.get('cameraId')}"


def _where(a):
    ctx = a.get("context") or {}
    return " / ".join(n["code"] for n in ctx.get("path", []))


def where_label(a):
    """Human context path for an alarm: PROJECT-07 / TC-023 / CTR-018 / Room 204 / CAM-109."""
    ctx = a.get("context") or {}
    parts = []
    for lvl in ("project", "tc", "centre", "room", "camera"):
        n = ctx.get(lvl)
        if n:
            parts.append(("Room " + n["code"]) if lvl == "room" else n["code"])
    return " / ".join(parts) or f"camera {a.get('cameraId')}"


def _alert(kind, category, severity, title, scope_level, scope_code, reasons, inputs, related, *, camera=None,
           at=None, action=None, action_label="Investigate", alarm=None, what=None, where=None, evidence=None):
    """Every alert answers WHAT happened, WHERE, WHEN, WHY (reasons), what EVIDENCE exists, and the ACTION."""
    if alarm is not None:
        where = where or where_label(alarm)
        ev = alarm.get("evidence") or {}
        evidence = evidence or (f"{ev.get('images', 0)} image(s)" + (" + video" if ev.get("video") else "")
                                if ev.get("count") else "No evidence attached")
    return {
        "id": _aid(kind, scope_level, scope_code, ",".join(sorted(related))[:400]),
        "type": kind, "category": category, "severity": severity, "title": title,
        "what": what or title, "where": where or f"{scope_level.upper()} {scope_code}",
        "when": at or db.now_iso(), "evidence": evidence,
        "scope": {"level": scope_level, "code": scope_code},
        "reasons": reasons, "inputs": inputs, "related": related[:50], "relatedCount": len(related),
        "camera": camera, "timestamp": at or db.now_iso(),
        "action": action, "actionLabel": action_label, "provenance": "derived",
    }


def evaluate(alarms, pol, feed_status=None, rules=None, now=None):
    now = now or datetime.now(timezone.utc)
    out, skipped = [], []
    records = alarms                                     # everything, for camera health
    alarms = [a for a in alarms if a.get("eventKind") != "camera_status"]   # alert checks: detections only
    by_cam = defaultdict(list)
    for a in alarms:
        by_cam[a.get("cameraId")].append(a)

    # 1. CRITICAL PENDING -----------------------------------------------------
    for a in alarms:
        if a["flags"]["criticalPending"]:
            out.append(_alert(
                "critical_pending", "operational", "critical",
                f"Critical {a.get('alarmTypeName')} pending review at {where_label(a)}", "camera", _cam(a),
                [f"Priority = {a.get('priority')} (priority value {a.get('priorityLevel')})",
                 "Camview lastActionType = 0 (Pending)",
                 "No operator decision recorded in Command Center",
                 f"Raised {int(a['ageMinutes'])} min ago" if a.get("ageMinutes") is not None else "Raised time unknown",
                 f"Evidence: {a['evidence']['count']} item(s)" if a["evidence"]["count"] else "No evidence attached"],
                {"priority": a.get("priority"), "lastActionType": 0, "ageMinutes": a.get("ageMinutes")},
                [a["alarmId"]], camera=_cam(a), at=a.get("lastInstance"),
                action=f"#/investigations/{a['alarmId']}", alarm=a,
                what=f"Critical {a.get('alarmTypeName')} still pending"))

    # 1b. CAMERAS OFFLINE (from the real health source only — never from an alarm's name) ---
    with_health = [a for a in records if (a.get("health") or {}).get("available")]
    if records and not with_health:
        skipped.append({"type": "cameras_offline", "reason": "No camera-health source yet: in live mode Camview's "
                                                              "frameSyncStatus is imported with the first refresh; a "
                                                              "monitoring source can also push status."})
    else:
        offline = {}
        for a in with_health:
            if a["health"]["camera"]["state"] == "offline":
                key = (str(a.get("projectId")), str(a.get("cameraId")))
                if key not in offline or (a.get("lastInstance") or "") > (offline[key].get("lastInstance") or ""):
                    offline[key] = a
        if offline:
            cams = sorted(offline.values(), key=lambda a: a.get("lastInstance") or "", reverse=True)
            src = cams[0]["health"].get("source") or "health source"
            centres = {a.get("centreCode") or ((a.get("context") or {}).get("centre") or {}).get("code") for a in cams}
            out.append(_alert(
                "cameras_offline", "operational", "high", f"{len(cams)} camera(s) offline", "project",
                str(cams[0].get("projectId")),
                [f"{len(cams)} cameras report camera.state = offline",
                 "Source: Camview frameSyncStatus, sent with every alarm and refreshed with the live feed"
                 if src == "camview" else f"Source: {src} (pushed health reports)",
                 "Cameras: " + ", ".join(a.get("locationLabel") or _cam(a) for a in cams[:8])
                 + (" …" if len(cams) > 8 else ""),
                 "No image or video can arrive from a camera that is not sending frames"],
                {"count": len(cams), "source": src, "centres": len(centres - {None})},
                [a["alarmId"] for a in cams], camera=_cam(cams[0]),
                at=cams[0]["health"].get("updatedAt") or db.now_iso(),
                action="#/monitoring?tab=health", action_label="Camera health",
                what=f"{len(cams)} cameras offline",
                where=where_label(cams[0]) if len(cams) == 1 else f"{len(centres - {None})} centre(s)",
                evidence="No image or video — the cameras are not sending frames"))

    # 2. REPEATED ACTIVITY (per alarm, from totalTimesReported + first/last) ---
    for a in alarms:
        n = a.get("totalTimesReported") or 1
        if a["flags"]["repeated"] and n >= pol["repeatThreshold"]:
            span = a.get("spanMinutes")
            out.append(_alert(
                "repeated_activity", "operational", "high" if a.get("priority") in ("critical", "high") else "warning",
                (f"{'Critical r' if a.get('priority') == 'critical' else 'R'}epeated activity detected at {where_label(a)} — "
                 f"{n} reports" + (f" within {span:g} minutes" if span is not None else "")), "camera", _cam(a),
                [f"{n} occurrences (totalTimesReported)",
                 f"Same camera ({_cam(a)})",
                 f"Within {span:g} minutes (firstInstance → lastInstance)" if span is not None else "Time span unknown",
                 f"Threshold: ≥{pol['repeatThreshold']} reports within {pol['repeatWindowMinutes']} min",
                 f"Current state = {a.get('lastActionLabel')}",
                 "Evidence available" if a["flags"]["evidence"] else "No evidence"],
                {"occurrences": n, "spanMinutes": span, "threshold": pol["repeatThreshold"],
                 "windowMinutes": pol["repeatWindowMinutes"]},
                [a["alarmId"]], camera=_cam(a), at=a.get("lastInstance"), action=f"#/investigations/{a['alarmId']}",
                alarm=a, what=f"{a.get('alarmTypeName')} reported {n} times"))

    # 3. HIGH CAMERA ACTIVITY ----------------------------------------------------
    window = timedelta(minutes=pol["cameraActivityWindowMinutes"])
    for cam_id, items in by_cam.items():
        recent = [a for a in items if (_ts(a.get("lastInstance")) or datetime.min.replace(tzinfo=timezone.utc)) >= now - window]
        if len(recent) >= pol["cameraActivityThreshold"]:
            a0 = recent[0]
            types = Counter(a.get("alarmTypeName") for a in recent).most_common(3)
            out.append(_alert(
                "high_camera_activity", "operational", "warning",
                f"High activity at {where_label(a0)} — {len(recent)} alarms in {pol['cameraActivityWindowMinutes']} min",
                "camera", _cam(a0),
                [f"{len(recent)} alarms from the same camera in the last {pol['cameraActivityWindowMinutes']} min",
                 f"Threshold: ≥{pol['cameraActivityThreshold']} alarms",
                 "Types: " + ", ".join(f"{t} ×{c}" for t, c in types),
                 f"Location: {_where(a0)}" if (a0.get('context') or {}).get('mapped') else "Camera not mapped in nomenclature"],
                {"count": len(recent), "threshold": pol["cameraActivityThreshold"],
                 "windowMinutes": pol["cameraActivityWindowMinutes"]},
                [a["alarmId"] for a in recent], camera=_cam(a0), at=recent[0].get("lastInstance"),
                action=f"#/cameras/{cam_id}", action_label="Open camera", alarm=a0,
                what=f"{len(recent)} alarms from one camera",
                evidence=f"{sum(1 for a in recent if a['flags']['evidence'])} of {len(recent)} alarms have evidence"))

    # 4. MULTIPLE RELATED ALARMS (same room/centre, only when mapped) ------------
    rel_window = timedelta(minutes=pol["relatedWindowMinutes"])
    mapped = [a for a in alarms if (a.get("context") or {}).get("mapped")]
    if not mapped and alarms:
        skipped.append({"type": "multiple_related", "reason": "No cameras are mapped in the nomenclature, so "
                                                              "location relationships can't be established."})
    for level in ("room", "centre"):
        groups = defaultdict(list)
        for a in mapped:
            node = a["context"].get(level)
            ts = _ts(a.get("lastInstance"))
            if node and ts and ts >= now - rel_window:
                groups[node["code"] if level == "room" else node["code"]].append(a)
        for code, items in groups.items():
            cams = {a.get("cameraId") for a in items}
            if len(items) >= pol["relatedMinCount"] and len(cams) >= 2:
                a0 = items[0]
                label = f"{a0['context'][level]['code']}" + (f" ({a0['context']['centre']['code']})"
                                                            if level == "room" and a0['context'].get('centre') else "")
                out.append(_alert(
                    "multiple_related", "operational", "high",
                    f"{len(items)} related alarms at {level} {label}", level, code,
                    [f"{len(items)} alarms within {pol['relatedWindowMinutes']} min",
                     f"{len(cams)} different cameras in the same {level}",
                     f"Relationship: same {level} (from nomenclature master data)",
                     f"Threshold: ≥{pol['relatedMinCount']} alarms from ≥2 cameras"],
                    {"count": len(items), "cameras": len(cams), "windowMinutes": pol["relatedWindowMinutes"]},
                    [a["alarmId"] for a in items], camera=_cam(a0), at=items[0].get("lastInstance"),
                    action=f"#/context?node={a0['context'][level]['id']}", action_label=f"Open {level}"))
        if level == "room" and any(len(v) >= pol["relatedMinCount"] for v in groups.values()):
            break  # don't repeat the same cluster at centre level

    # 5. ACTIVITY SPIKE vs baseline --------------------------------------------
    stamps = [t for t in (_ts(a.get("firstInstance") or a.get("lastInstance")) for a in alarms) if t]
    if stamps:
        oldest = min(stamps)
        history_h = (now - oldest).total_seconds() / 3600
        if history_h < 6:
            skipped.append({"type": "activity_spike", "reason": f"Only {history_h:.1f} h of history in the working "
                                                                f"set; at least 6 h is needed for a baseline."})
        else:
            last_hour = sum(1 for t in stamps if t >= now - timedelta(hours=1))
            base_hours = min(24, history_h - 1)
            base = sum(1 for t in stamps if now - timedelta(hours=1 + base_hours) <= t < now - timedelta(hours=1))
            baseline = base / base_hours if base_hours else 0
            if last_hour >= pol["spikeMinCount"] and baseline > 0 and last_hour >= baseline * pol["spikeRatio"]:
                out.append(_alert(
                    "activity_spike", "operational", "warning",
                    f"Activity above baseline — {last_hour} alarms in the last hour", "project",
                    str((alarms[0].get("context") or {}).get("project", {}).get("code", alarms[0].get("projectId"))),
                    [f"Current: {last_hour} alarms in the last hour",
                     f"Baseline: {baseline:.1f} per hour (average of the previous {base_hours:.0f} h)",
                     f"Ratio {last_hour / baseline:.1f}× ≥ configured {pol['spikeRatio']}×",
                     "Cause is not inferred — review the related alarms"],
                    {"current": last_hour, "baselinePerHour": round(baseline, 2), "ratio": round(last_hour / baseline, 2)},
                    [a["alarmId"] for a in alarms if (_ts(a.get("firstInstance")) or now) >= now - timedelta(hours=1)],
                    action="#/live?sort=lastInstance", action_label="Open live"))

    # 6. ACTIVITY SURGE (storm) --------------------------------------------------
    timed = sorted(((t, a) for a in alarms if (t := _ts(a.get("firstInstance") or a.get("lastInstance")))),
                   key=lambda x: x[0])
    win = timedelta(seconds=pol["stormWindowSeconds"])
    j, best = 0, (0, 0, 0)
    for i, (t, _) in enumerate(timed):
        while timed[j][0] < t - win:
            j += 1
        if i - j + 1 > best[0]:
            best = (i - j + 1, j, i)
    if best[0] >= pol["stormCount"] and timed[best[2]][0] >= now - timedelta(hours=1):
        members = [a for _, a in timed[best[1]:best[2] + 1]]
        cams = sorted({_cam(a) for a in members})
        start, end = timed[best[1]][0], timed[best[2]][0]
        proj = where_label(members[0]).split(" / ")[0]
        out.append(_alert(
            "activity_surge", "operational", "critical",
            f"Activity surge — {best[0]} alarms in {pol['stormWindowSeconds']} s at {proj}", "project",
            str(members[0].get("projectId")),
            [f"{best[0]} alarms within {pol['stormWindowSeconds']} seconds (threshold ≥{pol['stormCount']})",
             f"Time range: {start.isoformat(timespec='seconds')} → {end.isoformat(timespec='seconds')}",
             f"Cameras affected ({len(cams)}): " + ", ".join(cams[:12]) + (" …" if len(cams) > 12 else ""),
             "All underlying alarms are listed below — nothing is hidden"],
            {"count": best[0], "windowSeconds": pol["stormWindowSeconds"], "cameras": len(cams)},
            [a["alarmId"] for a in members], at=end.isoformat(),
            action=f"#/live?from={start.isoformat()}&to={end.isoformat()}", action_label="View events",
            what=f"{best[0]} alarms in {pol['stormWindowSeconds']} seconds", where=proj))

    # 7. SUPPRESSION ACTIVITY ----------------------------------------------------
    supp = [a for a in alarms if a["flags"]["suppressed"] and (_ts(a.get("lastInstance")) or now) >= now - timedelta(hours=1)]
    if len(supp) >= pol["suppressionThreshold"]:
        out.append(_alert(
            "suppression_activity", "operational", "info", f"{len(supp)} suppressed alarms in the last hour", "project",
            str(supp[0].get("projectId")),
            [f"{len(supp)} alarms flagged suppressed by Camview in the last hour",
             f"Threshold: ≥{pol['suppressionThreshold']}", "Suppressed alarms are still listed — review if unexpected"],
            {"count": len(supp)}, [a["alarmId"] for a in supp], action="#/live?quick=suppressed", action_label="View"))

    # 8. LONG PENDING (only when an SLA has been configured) ---------------------
    if pol.get("longPendingMinutes"):
        # one grouped alert (oldest first) instead of one per alarm, so the list stays readable
        overdue = sorted((a for a in alarms if a["flags"]["longPending"]), key=lambda a: -(a.get("ageMinutes") or 0))
        if overdue:
            oldest = overdue[0]
            out.append(_alert(
                "long_pending", "investigation", "high",
                f"{len(overdue)} alarm(s) pending longer than {_human(pol['longPendingMinutes'])}", "project",
                str(oldest.get("projectId")),
                [f"{len(overdue)} alarms have had no operator decision for more than {pol['longPendingMinutes']} min "
                 "(configured limit)",
                 f"Oldest: {oldest['alarmId']} on {_cam(oldest)} — pending {_human(oldest['ageMinutes'])}",
                 f"Critical among them: {sum(1 for a in overdue if a.get('priority') == 'critical')}"],
                {"count": len(overdue), "limitMinutes": pol["longPendingMinutes"],
                 "oldestMinutes": int(oldest["ageMinutes"])},
                [a["alarmId"] for a in overdue], camera=_cam(oldest),
                action="#/live?quick=pending&sort=firstInstance&dir=asc", action_label="Review oldest"))
    else:
        skipped.append({"type": "long_pending", "reason": "No pending-time limit is configured (Settings › Workflow)."})

    # 9a. REVIEW REQUIRED: pending alarms without an operator decision ------------
    to_review = [a for a in alarms if a["flags"]["pending"] and (a.get("review") or {}).get("status") == "unreviewed"]
    if to_review:
        crit = sum(1 for a in to_review if a.get("priority") == "critical")
        out.append(_alert("review_required", "investigation", "warning" if not crit else "high",
                          f"{len(to_review)} alarm(s) waiting for review", "project", str(to_review[0].get("projectId")),
                          [f"{len(to_review)} alarms are Pending in Camview with no operator decision in Command Center",
                           f"{crit} of them are critical",
                           f"{sum(1 for a in to_review if a['flags']['evidence'])} have evidence ready"],
                          {"count": len(to_review), "critical": crit}, [a["alarmId"] for a in to_review],
                          action="#/review?tab=pending", action_label="Open review queue"))

    # 9. WORKFLOW: approval / client sharing required ---------------------------
    awaiting = [a for a in alarms if a["workflowState"] == "READY_FOR_APPROVAL"]
    if awaiting:
        out.append(_alert("approval_required", "approval", "warning", f"{len(awaiting)} alarm(s) awaiting approval",
                          "project", str(awaiting[0].get("projectId")),
                          [f"{len(awaiting)} client-sharing requests have not been approved yet",
                           "Four-eyes approval is " + ("on" if pol["fourEyes"] else "off")],
                          {"count": len(awaiting)}, [a["alarmId"] for a in awaiting],
                          action="#/sharing?tab=ready_for_review", action_label="Review"))
    approved = [a for a in alarms if a["workflowState"] == "APPROVED"]
    if approved:
        out.append(_alert("client_sharing_required", "client", "info", f"{len(approved)} approved alarm(s) not yet shared",
                          "project", str(approved[0].get("projectId")),
                          [f"{len(approved)} alarms are approved for a client but not published"],
                          {"count": len(approved)}, [a["alarmId"] for a in approved],
                          action="#/sharing?tab=approved", action_label="Publish"))
    ready = [a for a in alarms if a["workflowState"] == "READY_FOR_CLIENT"]
    if ready:
        out.append(_alert("ready_for_client", "client", "info", f"{len(ready)} validated alarm(s) ready for client review",
                          "project", str(ready[0].get("projectId")),
                          [f"{len(ready)} alarms were marked valid by an operator",
                           "Valid does not mean shared — they stay INTERNAL until someone requests, approves and publishes"],
                          {"count": len(ready)}, [a["alarmId"] for a in ready],
                          action="#/sharing?tab=candidates", action_label="Review"))

    # 10. EVIDENCE AVAILABLE on pending critical/high ---------------------------
    ev = [a for a in alarms if a["flags"]["pending"] and a["flags"]["evidence"] and a.get("priority") in ("critical", "high")]
    if ev:
        out.append(_alert("evidence_available", "investigation", "info",
                          f"Evidence ready on {len(ev)} pending high-priority alarm(s)", "project",
                          str(ev[0].get("projectId")),
                          [f"{len(ev)} pending alarms with priority critical/high have evidence attached",
                           "Evidence can be reviewed now in the investigation workspace"],
                          {"count": len(ev)}, [a["alarmId"] for a in ev], action="#/evidence?pending=1",
                          action_label="Review evidence"))

    # 11. SYSTEM ------------------------------------------------------------------
    if feed_status and feed_status.get("lastError"):
        err = feed_status["lastError"]
        out.append(_alert("connection_problem", "system", "high", "Data refresh failed", "global", "camview",
                          [err.get("message", "Unknown error"), f"At {err.get('at')}",
                           f"Showing last successful data from {feed_status.get('lastSuccessAt') or 'never'}"],
                          {"code": err.get("code")}, [], action="#/settings?tab=system", action_label="System status"))
    unmapped = {a.get("cameraId") for a in alarms if not (a.get("context") or {}).get("mapped")}
    if alarms and unmapped:
        out.append(_alert("data_quality", "system", "info", f"{len(unmapped)} camera(s) not in the nomenclature",
                          "global", "nomenclature",
                          [f"{len(unmapped)} cameras sending alarms have no Project/TC/Centre mapping",
                           "Their alarms show without location context"],
                          {"cameras": sorted(str(c) for c in unmapped)[:20]}, [],
                          action="#/context?tab=quality", action_label="Data quality"))

    # 12. USER-DEFINED RULES -----------------------------------------------------------
    for rule in rules or []:
        hits = [a for a in alarms if rule_matches(rule, a)]
        if hits:
            conds = rule["conditions"]
            out.append(_alert(
                f"rule:{rule['id']}", "operational", rule.get("severity", "warning"),
                f"{rule['name']} — {len(hits)} alarm(s)", rule.get("scope", "camera"),
                _cam(hits[0]) if rule.get("scope") == "camera" else str(hits[0].get("projectId")),
                [f"Rule '{rule['name']}' matched"] + [describe_condition(c) for c in conds],
                {"matches": len(hits)}, [a["alarmId"] for a in hits], camera=_cam(hits[0]),
                action=f"#/investigations/{hits[0]['alarmId']}" if len(hits) == 1 else "#/live",
                action_label="Investigate" if len(hits) == 1 else "View"))

    out.sort(key=lambda x: x["timestamp"] or "", reverse=True)          # newest first...
    out.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 9))         # ...within each severity (stable sort)
    return out, skipped


# ---------------------------------------------------------------------------
# Rule conditions (used by the rule builder)
# ---------------------------------------------------------------------------

FIELDS = {
    "priority": ("Priority", lambda a: a.get("priority")),
    "lastActionType": ("Camview state (lastActionType)", lambda a: a.get("lastActionType")),
    "alarmType": ("Alarm type id", lambda a: a.get("alarmType")),
    "workflowState": ("Workflow state", lambda a: a.get("workflowState")),
    "visibility": ("Client visibility", lambda a: (a.get("visibility") or {}).get("state")),
    "occurrences": ("Occurrences", lambda a: a.get("totalTimesReported") or 1),
    "ageMinutes": ("Age (minutes)", lambda a: a.get("ageMinutes")),
    "hasEvidence": ("Has evidence", lambda a: a["flags"]["evidence"]),
    "suppressed": ("Suppressed", lambda a: a["flags"]["suppressed"]),
    "shiftLabel": ("Shift", lambda a: a.get("shiftLabel")),
    "tc": ("TC code", lambda a: ((a.get("context") or {}).get("tc") or {}).get("code")),
    "centre": ("Centre code", lambda a: ((a.get("context") or {}).get("centre") or {}).get("code")),
    "camera": ("Camera code", lambda a: a.get("cameraCode")),
}
OPS = {"eq": "=", "ne": "≠", "gte": "≥", "lte": "≤", "in": "is one of"}


def _coerce(v, sample):
    if isinstance(sample, bool):
        return str(v).lower() in ("1", "true", "yes")
    if isinstance(sample, (int, float)):
        try:
            return float(v)
        except (TypeError, ValueError):
            return v
    return str(v)


def rule_matches(rule, a):
    for c in rule.get("conditions") or []:
        f = FIELDS.get(c.get("field"))
        if not f:
            return False
        actual = f[1](a)
        if actual is None:
            return False
        op, value = c.get("op", "eq"), c.get("value")
        if op == "in":
            vals = [_coerce(x.strip(), actual) for x in str(value).split(",")]
            if _coerce(actual, actual) not in vals:
                return False
            continue
        v = _coerce(value, actual)
        a_ = float(actual) if isinstance(actual, (int, float)) and not isinstance(actual, bool) else actual
        try:
            ok = {"eq": a_ == v, "ne": a_ != v, "gte": a_ >= v, "lte": a_ <= v}[op]
        except TypeError:
            ok = False
        if not ok:
            return False
    return True


def describe_condition(c):
    label = FIELDS.get(c.get("field"), (c.get("field"),))[0]
    return f"{label} {OPS.get(c.get('op'), c.get('op'))} {c.get('value')}"
