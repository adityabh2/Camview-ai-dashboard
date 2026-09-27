"""
analytics.py — metrics and distributions derived from a list of alarms.

Everything here is a pure function of the alarms it is given. Internal
callers pass the user's in-scope working set; client callers pass ONLY the
client dataset (workflow.client_visible_alarms), so client analytics can
never include hidden alarms.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

PRIORITY_ORDER = ["critical", "high", "medium", "low"]


def _ts(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def metrics(alarms):
    c = Counter()
    for a in alarms:
        c["total"] += 1
        lat = a.get("lastActionType")
        c[{0: "pending", 1: "valid", 2: "invalid", 3: "exception"}.get(lat, "unknown")] += 1
        if a.get("priority") == "critical":
            c["critical"] += 1
        if a.get("suppressed"):
            c["suppressed"] += 1
        flags = a.get("flags") or {}
        if flags.get("criticalPending"):
            c["criticalPending"] += 1
        if flags.get("evidence"):
            c["withEvidence"] += 1
        if flags.get("repeated"):
            c["repeated"] += 1
        wf = a.get("workflowState")
        if flags.get("pending"):
            c["readyForReview"] += 1
        if wf == "READY_FOR_CLIENT":
            c["readyForClient"] += 1
        if wf == "READY_FOR_APPROVAL":
            c["awaitingApproval"] += 1
        if wf == "APPROVED":
            c["approvedForClient"] += 1
        if wf in ("SHARED", "CLIENT_ACKNOWLEDGED"):
            c["sharedWithClient"] += 1
        if (a.get("review") or {}).get("status") == "marked_valid":
            c["opsValid"] += 1
        if (a.get("review") or {}).get("status") == "marked_invalid":
            c["opsInvalid"] += 1
    keys = ["total", "critical", "criticalPending", "pending", "valid", "invalid", "exception", "suppressed",
            "readyForReview", "readyForClient", "awaitingApproval", "approvedForClient", "sharedWithClient",
            "withEvidence", "repeated", "opsValid", "opsInvalid"]
    out = {k: c.get(k, 0) for k in keys}
    resolved = out["valid"] + out["invalid"]
    out["falseAlarmRate"] = round(out["invalid"] / resolved, 4) if resolved else None
    return out


def distribution(alarms, key_fn, order=None, label_fn=None):
    c = Counter(key_fn(a) for a in alarms)
    keys = [k for k in (order or []) if k in c] + sorted((k for k in c if k not in (order or [])), key=lambda k: str(k))
    return [{"key": k, "label": label_fn(k) if label_fn else ("Unknown" if k is None else str(k)), "count": c[k]}
            for k in keys]


def status_distribution(alarms):
    labels = {0: "Pending", 1: "Valid", 2: "Invalid", 3: "Exception"}
    return distribution(alarms, lambda a: a.get("lastActionType"), [0, 1, 2, 3], lambda k: labels.get(k, "Unknown"))


def priority_distribution(alarms):
    return distribution(alarms, lambda a: a.get("priority"), PRIORITY_ORDER, lambda k: str(k).title())


def level_breakdown(alarms, level):
    groups = defaultdict(Counter)
    names = {}
    for a in alarms:
        node = (a.get("context") or {}).get(level)
        key = node["code"] if node else "Unmapped"
        names[key] = (node or {}).get("name") or key
        groups[key]["total"] += 1
        groups[key][{0: "pending", 1: "valid", 2: "invalid", 3: "exception"}.get(a.get("lastActionType"), "unknown")] += 1
        if a.get("priority") == "critical":
            groups[key]["critical"] += 1
    rows = []
    for k, c in groups.items():
        resolved = c["valid"] + c["invalid"]
        rows.append({"code": k, "name": names[k], "total": c["total"], "critical": c["critical"],
                     "pending": c["pending"], "valid": c["valid"], "invalid": c["invalid"], "exception": c["exception"],
                     "falseAlarmRate": round(c["invalid"] / resolved, 4) if resolved else None})
    rows.sort(key=lambda r: -r["total"])
    return rows


def top_cameras(alarms, n=10):
    groups = defaultdict(list)
    for a in alarms:
        groups[a.get("cameraId")].append(a)
    rows = []
    for cam, items in groups.items():
        latest = max(items, key=lambda a: a.get("lastInstance") or "")
        ctx = latest.get("context") or {}
        rows.append({
            "cameraId": cam, "code": latest.get("cameraCode"), "name": latest.get("cameraName"),
            "location": " / ".join(x["code"] for x in ctx.get("path", [])[:-1]), "mapped": ctx.get("mapped", False),
            "count": len(items), "critical": sum(1 for a in items if a.get("priority") == "critical"),
            "pending": sum(1 for a in items if (a.get("flags") or {}).get("pending")),
            "occurrences": sum(a.get("totalTimesReported") or 1 for a in items),
            "latest": latest.get("lastInstance"), "latestAlarmId": latest.get("alarmId"),
        })
    rows.sort(key=lambda r: (-r["count"], -r["occurrences"]))
    return rows[:n]


def hourly(alarms, hours=24, tz_offset=0, now=None):
    tz = timezone(timedelta(minutes=tz_offset))
    now = (now or datetime.now(timezone.utc)).astimezone(tz).replace(minute=0, second=0, microsecond=0)
    slots = [now - timedelta(hours=h) for h in range(hours - 1, -1, -1)]
    counts = {s: Counter() for s in slots}
    for a in alarms:
        t = _ts(a.get("firstInstance") or a.get("lastInstance"))
        if not t:
            continue
        key = t.astimezone(tz).replace(minute=0, second=0, microsecond=0)
        if key in counts:
            counts[key]["total"] += 1
            counts[key][a.get("priority") or "unknown"] += 1
    return [{"start": s.isoformat(), "total": counts[s]["total"],
             **{p: counts[s][p] for p in PRIORITY_ORDER}} for s in slots]


def daily(alarms, days=14, tz_offset=0, now=None):
    tz = timezone(timedelta(minutes=tz_offset))
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    slots = [today - timedelta(days=d) for d in range(days - 1, -1, -1)]
    counts = {s: Counter() for s in slots}
    for a in alarms:
        t = _ts(a.get("firstInstance") or a.get("lastInstance"))
        if not t:
            continue
        d = t.astimezone(tz).date()
        if d in counts:
            counts[d]["total"] += 1
            counts[d][{0: "pending", 1: "valid", 2: "invalid", 3: "exception"}.get(a.get("lastActionType"), "unknown")] += 1
    return [{"date": s.isoformat(), **{k: counts[s][k] for k in ("total", "pending", "valid", "invalid", "exception")}}
            for s in slots]


def heatmap(alarms, days=14, tz_offset=0, now=None):
    """Date x hour-of-day grid of alarm counts."""
    tz = timezone(timedelta(minutes=tz_offset))
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    dates = [today - timedelta(days=d) for d in range(days - 1, -1, -1)]
    grid = {d: [0] * 24 for d in dates}
    for a in alarms:
        t = _ts(a.get("firstInstance") or a.get("lastInstance"))
        if not t:
            continue
        lt = t.astimezone(tz)
        if lt.date() in grid:
            grid[lt.date()][lt.hour] += 1
    return {"dates": [d.isoformat() for d in dates], "rows": [grid[d] for d in dates],
            "max": max((max(r) for r in grid.values()), default=0)}


def recurrence(alarms):
    buckets = [("1", 1, 1), ("2", 2, 2), ("3–4", 3, 4), ("5–9", 5, 9), ("10+", 10, 10 ** 9)]
    out = []
    for label, lo, hi in buckets:
        out.append({"label": label, "count": sum(1 for a in alarms if lo <= (a.get("totalTimesReported") or 1) <= hi)})
    return out


def shift_distribution(alarms):
    return distribution(alarms, lambda a: a.get("shiftLabel") or "No shift", None, lambda k: str(k))


def type_distribution(alarms):
    return distribution(alarms, lambda a: a.get("alarmTypeName") or "Unknown", None, lambda k: str(k))


def group_activity(alarms, by="camera", gap_minutes=10):
    """ACTIVITY GROUPS (noise reduction). Alarms on the same camera (or room /
    centre when mapped) whose times are no more than `gap_minutes` apart form a
    group. Groups never hide anything: every member alarm id is returned, and
    the rule that formed the group is stated. Returns (groups, rule_text)."""
    gap = timedelta(minutes=float(gap_minutes))

    def key(a):
        if by == "camera":
            return f"camera:{a.get('cameraId')}", a.get("cameraCode") or f"CAM-{a.get('cameraId')}"
        node = (a.get("context") or {}).get(by)
        if node and not node.get("unmapped"):
            label = node["code"]
            if by == "room" and (a.get("context") or {}).get("centre"):
                label = f"Room {node['code']} · {a['context']['centre']['code']}"
            return f"{by}:{node.get('id') or node['code']}", label
        return f"camera:{a.get('cameraId')}", a.get("cameraCode") or f"CAM-{a.get('cameraId')}"

    buckets = defaultdict(list)
    for a in alarms:
        t = _ts(a.get("firstInstance") or a.get("lastInstance"))
        if t:
            buckets[key(a)].append((t, a))
    groups = []
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for (gid, label), items in buckets.items():
        items.sort(key=lambda x: x[0])
        current = [items[0]]
        for t, a in items[1:]:
            if t - current[-1][0] <= gap:
                current.append((t, a))
            else:
                groups.append(_group(gid, label, current, rank))
                current = [(t, a)]
        groups.append(_group(gid, label, current, rank))
    groups.sort(key=lambda g: g["end"], reverse=True)
    rule = (f"Alarms on the same {by} with no more than {gap_minutes:g} minutes between consecutive alarms "
            f"are shown as one group. Expand a group to see every underlying alarm.")
    return groups, rule


def _group(gid, label, members, rank):
    alarms = [a for _, a in members]
    ctx_sets = {lvl: sorted({(a.get("context") or {}).get(lvl, {}).get("code") for a in alarms
                             if (a.get("context") or {}).get(lvl)} - {None})
                for lvl in ("project", "tc", "centre", "room")}
    top = min(alarms, key=lambda a: rank.get(a.get("priority"), 9))
    return {
        "id": f"{gid}:{members[0][0].isoformat()}",
        "label": label, "count": len(alarms),
        "reports": sum(a.get("totalTimesReported") or 1 for a in alarms),
        "start": members[0][0].isoformat(), "end": members[-1][0].isoformat(),
        "durationMinutes": round((members[-1][0] - members[0][0]).total_seconds() / 60, 1),
        "topPriority": top.get("priority"),
        "pending": sum(1 for a in alarms if (a.get("flags") or {}).get("pending")),
        "types": dict(Counter(a.get("alarmTypeName") for a in alarms).most_common(4)),
        "impact": {**ctx_sets, "cameras": sorted({a.get("cameraCode") or str(a.get("cameraId")) for a in alarms})},
        "alarmIds": [a["alarmId"] for a in alarms],
    }


def in_range(alarms, start, end):
    out = []
    for a in alarms:
        t = _ts(a.get("firstInstance") or a.get("lastInstance"))
        if t and start <= t <= end:
            out.append(a)
    return out
