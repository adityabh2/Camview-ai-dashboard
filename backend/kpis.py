"""
kpis.py — calculations behind the "KPIs & Reports" page.

Two independent verdicts are reported side by side, never mixed:

  * Camview verdict  — `lastActionType` on each alarm from Camview
                       (0 pending, 1 valid, 2 invalid, 3 exception).
  * Ops verdict      — what this dashboard's operators recorded
                       (marked_valid / marked_invalid / marked_exception /
                       acknowledged), stored in SQLite by db.py.

"False-alarm rate" = invalid / (valid + invalid), i.e. of the alarms that
were resolved one way or the other, how many turned out not to be real.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

RANGES = {
    "today": "Today",
    "24h": "Last 24 hours",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
}

VERDICTS = {0: "pending", 1: "valid", 2: "invalid", 3: "exception"}
OPS_STATUSES = ("marked_valid", "marked_invalid", "marked_exception", "acknowledged")
OPS_LABELS = {
    "marked_valid": "Valid", "marked_invalid": "Invalid", "marked_exception": "Exception",
    "acknowledged": "Acknowledged", "unreviewed": "Unreviewed",
}

# The few alarm fields kept as a snapshot with each ops review.
SNAPSHOT_FIELDS = ("alarmTypeName", "hall", "cameraName", "cameraId", "priority",
                   "firstInstance", "lastInstance", "lastActionType", "projectId")


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_range(name, tz_offset_minutes=0, now=None):
    """Returns (start, end, bucket, label) in the viewer's time zone.
    Hourly buckets up to 24h, daily beyond that."""
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    if name not in RANGES:
        name = "24h"
    if name == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}[name]
    bucket = "hour" if name in ("today", "24h") else "day"
    return start, now, bucket, RANGES[name]


def raised_at(alarm):
    return parse_ts(alarm.get("firstInstance")) or parse_ts(alarm.get("lastInstance"))


def _rate(num, den):
    return round(num / den, 4) if den else None


def _floor(dt, bucket):
    if bucket == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _trend(alarms, start, end, bucket):
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    tz = start.tzinfo
    slots = {}
    t = _floor(start, bucket)
    while t <= end:
        slots[t] = Counter()
        t += step
    for a in alarms:
        ts = raised_at(a)
        key = _floor(ts.astimezone(tz), bucket)
        if key in slots:
            slots[key][VERDICTS.get(a.get("lastActionType"), "pending")] += 1
    return [
        {"start": iso(k), **{v: c.get(v, 0) for v in VERDICTS.values()}}
        for k, c in sorted(slots.items())
    ]


def _breakdown(alarms, key_fn):
    groups = defaultdict(Counter)
    for a in alarms:
        groups[key_fn(a)][VERDICTS.get(a.get("lastActionType"), "pending")] += 1
    rows = []
    for name, c in groups.items():
        total = sum(c.values())
        rows.append({
            "name": name, "total": total,
            **{v: c.get(v, 0) for v in VERDICTS.values()},
            "falseAlarmRate": _rate(c["invalid"], c["valid"] + c["invalid"]),
        })
    rows.sort(key=lambda r: (-r["total"], str(r["name"])))
    return rows


def list_row(alarm, review=None):
    review = review or {}
    return {
        "alarmId": alarm.get("alarmId") or review.get("alarmId"),
        "alarmTypeName": alarm.get("alarmTypeName"),
        "hall": alarm.get("hall"),
        "cameraName": alarm.get("cameraName"),
        "priority": alarm.get("priority"),
        "firstInstance": alarm.get("firstInstance"),
        "lastInstance": alarm.get("lastInstance"),
        "camviewStatus": VERDICTS.get(alarm.get("lastActionType"), "unknown"),
        "opsStatus": review.get("status", "unreviewed"),
        "reviewedBy": review.get("updatedBy"),
        "reviewedAt": review.get("updatedAt"),
    }


def compute(alarms, reviews_by_id, ops_reviews, audit_rows, start, end, bucket, label,
            list_limit=500, scanned=None, truncated=False):
    """
    alarms        normalized alarms (any time); filtered here to those raised in range
    reviews_by_id {alarmId: review} for those alarms (db.reviews_for)
    ops_reviews   reviews whose latest change is in range (db.list_reviews)
    audit_rows    per-operator action counts in range (db.audit_stats)
    """
    in_range = [a for a in alarms if (ts := raised_at(a)) and start <= ts <= end]
    in_range.sort(key=lambda a: raised_at(a), reverse=True)
    counts = Counter(VERDICTS.get(a.get("lastActionType"), "pending") for a in in_range)
    total = len(in_range)
    resolved = counts["valid"] + counts["invalid"]

    # --- ops verdicts on the alarms raised in this period
    reviewed = [(a, reviews_by_id[a["alarmId"]]) for a in in_range
                if reviews_by_id.get(a["alarmId"], {}).get("status", "unreviewed") != "unreviewed"]
    compared = agreed = 0
    minutes = []
    for a, r in reviewed:
        camview = VERDICTS.get(a.get("lastActionType"))
        ops = {"marked_valid": "valid", "marked_invalid": "invalid"}.get(r["status"])
        if camview in ("valid", "invalid") and ops:
            compared += 1
            agreed += camview == ops
        first_review, raised = parse_ts(r.get("firstReviewedAt")), raised_at(a)
        if first_review and raised and first_review >= raised:
            minutes.append((first_review - raised).total_seconds() / 60)

    # --- what the ops team did in this period (independent of Camview fetch)
    ops_counts = Counter(r["status"] for r in ops_reviews)
    ops_resolved = ops_counts["marked_valid"] + ops_counts["marked_invalid"]

    operators = defaultdict(lambda: {"operator": None, "actions": 0, "lastAt": None,
                                     **{k: 0 for k in ("acknowledge", "mark_valid", "mark_invalid",
                                                       "mark_exception", "reopen", "note")}})
    for row in audit_rows:
        op = operators[row["operator"]]
        op["operator"] = row["operator"]
        op["actions"] += row["count"]
        op[row["action"]] = op.get(row["action"], 0) + row["count"]
        op["lastAt"] = max(filter(None, [op["lastAt"], row["lastAt"]]), default=None)
    operator_rows = sorted(operators.values(), key=lambda o: -o["actions"])

    # --- lists
    by_id = {a["alarmId"]: a for a in alarms}

    def ops_list(status):
        rows = []
        for r in ops_reviews:
            if r["status"] == status:
                rows.append(list_row(by_id.get(r["alarmId"]) or {**r.get("snapshot", {}), "alarmId": r["alarmId"]}, r))
        return rows

    camview_valid = [list_row(a, reviews_by_id.get(a["alarmId"])) for a in in_range if a.get("lastActionType") == 1]
    camview_invalid = [list_row(a, reviews_by_id.get(a["alarmId"])) for a in in_range if a.get("lastActionType") == 2]
    ops_invalid, ops_valid = ops_list("marked_invalid"), ops_list("marked_valid")

    cameras = _breakdown(in_range, lambda a: f'{a.get("cameraName")} · {a.get("hall")}')
    noisy = sorted((c for c in cameras if c["invalid"]), key=lambda c: (-c["invalid"], -c["total"]))[:10]

    return {
        "range": {"start": iso(start), "end": iso(end), "bucket": bucket, "label": label},
        "source": {"alarmsScanned": scanned if scanned is not None else len(alarms), "truncated": truncated},
        "camview": {
            "total": total,
            **{v: counts.get(v, 0) for v in VERDICTS.values()},
            "resolved": resolved,
            "falseAlarmRate": _rate(counts["invalid"], resolved),
            "validRate": _rate(counts["valid"], resolved),
        },
        "ops": {
            "reviewedOfRaised": len(reviewed),
            "coverage": _rate(len(reviewed), total),
            "reviewsInPeriod": len(ops_reviews),
            **{s: ops_counts.get(s, 0) for s in OPS_STATUSES},
            "falseAlarmRate": _rate(ops_counts["marked_invalid"], ops_resolved),
            "actionsInPeriod": sum(r["count"] for r in audit_rows),
            "medianMinutesToReview": round(median(minutes), 1) if minutes else None,
            "agreement": {"compared": compared, "agreed": agreed, "rate": _rate(agreed, compared)},
        },
        "trend": _trend(in_range, start, end, bucket),
        "byType": _breakdown(in_range, lambda a: a.get("alarmTypeName") or "Unknown"),
        "byHall": _breakdown(in_range, lambda a: a.get("hall") or "Unknown"),
        "noisyCameras": noisy,
        "operators": operator_rows,
        "lists": {
            "camviewValid": camview_valid[:list_limit],
            "camviewInvalid": camview_invalid[:list_limit],
            "opsInvalid": ops_invalid[:list_limit],
            "opsValid": ops_valid[:list_limit],
        },
        "listTotals": {
            "camviewValid": len(camview_valid), "camviewInvalid": len(camview_invalid),
            "opsInvalid": len(ops_invalid), "opsValid": len(ops_valid),
        },
    }
