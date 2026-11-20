"""
Load: 100 / 1,000 / 5,000 alerts through the real path - fake Camview -> normalize -> canonical store -> enrich
-> API. Measures the first read (pages fetched in parallel, every record persisted), the list page, the detail
with its event story, and checks that repeated requests inside the cache window never call Camview again and
that no duplicate records appear. Bounds are generous (CI machines vary) but catch quadratic regressions.

    python -m pytest tests/test_performance.py -s      (prints the timings)
"""

import math
import time
from datetime import datetime, timedelta, timezone

import pytest

import alert_store
import config
import datasource
from fakecamview import FakeCamview

# seconds allowed per 1,000 alerts (never below the floor): first read + store, list page, detail, summary.
# Measured on a laptop at 5,000 alerts: refresh 2.2 s, list 1.0 s, detail 0.3 s, summary 0.3 s (~3x headroom).
BUDGET = {"refresh": (2.0, 3.0), "list": (0.6, 1.0), "detail": (0.3, 1.5), "summary": (0.3, 1.5)}


def _limit(kind, n):
    per_k, floor = BUDGET[kind]
    return max(floor, per_k * n / 1000)


@pytest.mark.parametrize("n", [100, 1000, 5000])
def test_load(n, monkeypatch, supervisor):
    cv = FakeCamview(monkeypatch)
    monkeypatch.setattr(config, "WINDOW_PAGES", 100)
    base = datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)
    cv.items = [cv.raw(f"P{i}", cam=500 + i % 40, at=(base + timedelta(seconds=i * 5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       typ=(14, 11, 5)[i % 3], action=(0, 0, 0, 1)[i % 4]) for i in range(n)]
    timings = {}

    t = time.perf_counter()
    feed = datasource.refresh("77", force=True)
    timings["refresh"] = time.perf_counter() - t
    assert feed.last_error is None and len(feed.items) == n, "every record read once, no duplicates"
    assert cv.calls == math.ceil(n / 100), "one Camview call per page"
    assert len({a["alarmId"] for a in feed.items}) == n
    assert alert_store.stats()["alerts"] >= n, "every received alert is persisted"
    calls = cv.calls

    t = time.perf_counter()
    s, q = supervisor.get("/api/queue", query_string={"status": "all", "projectId": 77, "size": 50})
    timings["list"] = time.perf_counter() - t
    assert s == 200 and q["totalElements"] == n and len(q["items"]) == 50
    assert q["views"]["needs_review"] == sum(1 for i in range(n) if i % 4 != 3)   # views counted on the same records

    t = time.perf_counter()
    s, d = supervisor.get(f"/api/queue/P{n // 2}?projectId=77")
    timings["detail"] = time.perf_counter() - t
    assert s == 200 and d["alert"]["alarmId"] == f"P{n // 2}" and d["story"]["groupCount"] >= 1

    t = time.perf_counter()
    s, summary = supervisor.get("/api/queue/summary", query_string={"projectId": 77})
    timings["summary"] = time.perf_counter() - t
    assert s == 200 and summary["totals"]["all"] == n

    s, q2 = supervisor.get("/api/queue", query_string={"status": "pending", "projectId": 77, "size": 50, "page": 2})
    assert s == 200 and cv.calls == calls, "requests inside the cache window must not call Camview again"

    print(f"\n{n} alerts: " + " · ".join(f"{k} {v * 1000:.0f} ms" for k, v in timings.items()))
    for k, v in timings.items():
        assert v < _limit(k, n), f"{k} took {v:.2f}s for {n} alerts (limit {_limit(k, n):.1f}s)"


def test_a_restart_keeps_every_alert_openable_from_the_store(monkeypatch, supervisor):
    cv = FakeCamview(monkeypatch)
    monkeypatch.setattr(config, "WINDOW_PAGES", 100)
    cv.items = [cv.raw(f"R{i}", cam=500 + i % 7) for i in range(1000)]
    datasource.refresh("77", force=True)
    datasource.reset()                                                # the process restarts: the feed is empty
    cv.mode = 500                                                     # and Camview is down
    t = time.perf_counter()
    s, d = supervisor.get("/api/queue/R777?projectId=77")
    assert s == 200 and d["alert"]["source"] == "stored" and d["alert"]["alarmId"] == "R777"
    assert time.perf_counter() - t < 3.0
