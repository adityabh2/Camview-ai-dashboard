"""
Live updates: the data version, the push stream and the shared enrichment cache.

* /api/status carries dataVersion; it changes when an operator decides, when a ticket is sent,
  when a notification is created — and not when someone merely looks at something.
* /api/events (Server-Sent Events) sends the current version first and a new one after a change.
* The enriched working set is computed once and shared; a decision is visible on the very next read.
"""

import threading
import time

import changes
import datasource
import db


def _version(client):
    s, r = client.get("/api/status")
    assert s == 200, r
    return r["dataVersion"]


def test_status_carries_data_version(operator):
    v = _version(operator)
    assert isinstance(v, int) and v == changes.version()


def test_reading_does_not_bump_the_version(operator, monkeypatch):
    monkeypatch.setattr(datasource, "DEMO_NEW_ALARM_EVERY", 10 ** 9)   # no simulated arrival during a slow run
    v1 = _version(operator)
    operator.get("/api/queue?status=pending")
    operator.get("/api/queue/summary")
    s, q = operator.get("/api/queue?status=pending&size=1")
    assert s == 200
    if q["items"]:
        operator.get(f"/api/queue/{q['items'][0]['alarmId']}?projectId={q['items'][0]['projectId']}")
    assert _version(operator) == v1


def test_audit_alone_is_quiet_but_decisions_are_not(operator):
    v1 = changes.version()
    db.audit("test.view", None, "alarm", "X")
    assert changes.version() == v1, "an audit row on its own must not make every screen re-read"
    s, q = operator.get("/api/queue?status=pending&size=1")
    assert s == 200 and q["items"]
    a = q["items"][0]
    s, r = operator.post(f"/api/queue/{a['alarmId']}/decide", {"result": "invalid", "projectId": a["projectId"]})
    assert s == 200, r
    assert changes.version() > v1


def test_decision_is_visible_on_the_next_read(operator):
    """The enrichment cache must be invalidated by the write, not by time."""
    s, q = operator.get("/api/queue?status=pending&size=1")
    a = q["items"][0]
    s, r = operator.post(f"/api/queue/{a['alarmId']}/decide", {"result": "invalid", "projectId": a["projectId"]})
    assert s == 200, r
    s, q2 = operator.get("/api/queue?status=invalid&size=200")
    assert a["alarmId"] in {x["alarmId"] for x in q2["items"]}
    s, q3 = operator.get("/api/queue?status=pending&size=200")
    assert a["alarmId"] not in {x["alarmId"] for x in q3["items"]}


def test_enriched_working_set_is_shared_between_reads(operator):
    s, q = operator.get("/api/queue?status=all&size=1")
    pid = q["items"][0]["projectId"]
    feed = datasource.refresh(pid)
    first = datasource.enriched(feed)
    second = datasource.enriched(feed)
    assert [a["alarmId"] for a in first] == [a["alarmId"] for a in second]
    # copies: annotating one read never leaks into the next
    first[0]["_marker"] = True
    assert "_marker" not in datasource.enriched(feed)[0]
    # a data change invalidates the cache
    changes.bump("test")
    third = datasource.enriched(feed)
    assert [a["alarmId"] for a in third] == [a["alarmId"] for a in first]


def test_feed_refresh_bumps_only_when_alarm_data_changed():
    pid = datasource.all_project_ids()[0]
    datasource.refresh(pid, force=True)
    v1 = changes.version()
    time.sleep(0.05)
    datasource.refresh(pid, force=True)          # demo: same records again
    assert changes.version() == v1
    feed = datasource._feed(pid)
    feed.items = list(feed.items[1:])            # something different next time
    feed.signature = None
    datasource.refresh(pid, force=True)
    assert changes.version() > v1


def test_events_stream_sends_version_then_changes(operator):
    r = operator.c.get("/api/events", headers={"Accept": "text/event-stream"})
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    it = r.response
    first = next(it)
    first = first.decode() if isinstance(first, bytes) else first
    assert "event: version" in first and f"data: {changes.version()}" in first
    # a change arrives as the next event (the stream waits for it)
    got = {}

    def reader():
        chunk = next(it)
        got["chunk"] = chunk.decode() if isinstance(chunk, bytes) else chunk

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.2)
    v = changes.bump("test")
    t.join(5)
    assert not t.is_alive(), "the stream did not wake up on a change"
    assert f"data: {v}" in got["chunk"]
    r.close()


def test_events_require_sign_in(client):
    r = client.c.get("/api/events")
    assert r.status_code == 401


def test_json_is_gzipped_when_accepted(operator):
    r = operator.c.get("/api/queue/summary", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("Content-Encoding") == "gzip"
    import gzip
    import json
    data = json.loads(gzip.decompress(r.get_data()))
    assert "totals" in data
    r2 = operator.c.get("/api/queue/summary", headers={"Accept-Encoding": "identity"})
    assert r2.headers.get("Content-Encoding") is None and r2.get_json()["totals"] == data["totals"]


def test_status_tick_runs_on_change_not_per_poll(operator, monkeypatch):
    import routes_ops
    calls = []
    monkeypatch.setattr(routes_ops.notify, "check_schedules", lambda: calls.append(1))
    routes_ops._tick_state["at"] = 0.0
    operator.get("/api/status")
    assert len(calls) == 1
    # nothing changed since the last tick: further polls (from any tab) do not evaluate again
    routes_ops._tick_state.update(at=time.time(), version=changes.version())
    operator.get("/api/status")
    operator.get("/api/status")
    assert len(calls) == 1
    # a change (rule saved, new alarm data) is evaluated on the very next poll
    changes.bump("test")
    operator.get("/api/status")
    assert len(calls) == 2
