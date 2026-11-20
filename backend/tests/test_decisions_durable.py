"""
E. durable work queue · F. transactional validation · G. optimistic concurrency

* VALID commits the decision, the ticket and the delivery intent together — or nothing
* a decision made against an older revision is a 409 conflict, never a silent overwrite
* double click / retry / simultaneous identical decisions: one decision, one ticket, one delivery
* delivery is a durable job: a failure is retried, a restart resumes it, a changed decision cancels it
"""

import sqlite3
import threading

import alert_service
import db
import jobs
import notify
import rbac
import tickets
import workflow


def _pending(c, project=7):
    s, q = c.get(f"/api/queue?projectId={project}&status=pending&size=50")
    assert s == 200 and q["items"], q
    return q["items"][0]


def _automatic():
    workflow.set_policy({"deliveryMode": "automatic"}, None)


def _decide(c, a, result, **kw):
    return c.post(f"/api/queue/{a['alarmId']}/decide", {"result": result, "projectId": a["projectId"], **kw})


def _count(sql, *p):
    return db.one(sql, p)["n"]


def test_double_valid_is_one_decision_one_ticket_one_delivery(supervisor, client_a):
    _automatic()
    a = _pending(supervisor)
    s1, r1 = _decide(supervisor, a, "valid", expectedRevision=0)
    s2, r2 = _decide(supervisor, a, "valid", expectedRevision=0)       # the second click of a double click
    assert s1 == s2 == 200, (r1, r2)
    assert r1["delivery"]["status"] == "delivered" and not r1["idempotent"]
    assert r2["idempotent"] and r2["delivery"]["status"] == "delivered"
    assert r1["ticket"]["ref"] == r2["ticket"]["ref"]
    aid = a["alarmId"]
    assert _count("SELECT COUNT(*) n FROM tickets WHERE alarm_id=?", aid) == 1
    assert _count("SELECT COUNT(*) n FROM publications WHERE alarm_id=?", aid) == 1
    assert _count("SELECT COUNT(*) n FROM jobs WHERE payload LIKE ?", f'%"{aid}"%') == 1
    assert _count("SELECT COUNT(*) n FROM audit_events WHERE action='review.mark_valid' AND resource_id=?", aid) == 1
    assert _count("SELECT COUNT(*) n FROM audit_events WHERE action LIKE 'delivery.%' AND resource_id=?", aid) == 1
    assert db.get_review(aid)["revision"] == 1
    s, cl = client_a.get("/api/client/alerts")
    assert sum(1 for x in cl["items"] if x["alarmId"] == aid) == 1


def test_conflicting_decision_is_rejected_not_overwritten(supervisor, operator):
    a = _pending(operator)
    s, r = _decide(operator, a, "valid", expectedRevision=a["alert"]["revision"])
    assert s == 200, r
    s, r = _decide(supervisor, a, "invalid", expectedRevision=a["alert"]["revision"])   # based on the old state
    assert s == 409 and r["error"] == "conflict", r
    assert r["details"]["current"]["decision"] == "valid" and r["details"]["current"]["revision"] == 1
    assert r["requestId"]
    assert db.get_review(a["alarmId"])["status"] == "marked_valid", "the conflicting decision must not be saved"
    s, d = supervisor.get(f"/api/queue/{a['alarmId']}?projectId=7")
    s, r = _decide(supervisor, a, "invalid", expectedRevision=d["alert"]["revision"])     # informed: allowed
    assert s == 200 and db.get_review(a["alarmId"])["revision"] == 2


def test_same_decision_on_a_stale_revision_is_idempotent(supervisor, operator):
    a = _pending(operator)
    assert _decide(operator, a, "exception", expectedRevision=0)[0] == 200
    s, r = _decide(supervisor, a, "exception", expectedRevision=0)
    assert s == 200 and r["idempotent"]
    assert db.get_review(a["alarmId"])["revision"] == 1


def test_simultaneous_decisions_serialize(app, supervisor):
    _automatic()
    a = _pending(supervisor)
    alarm = alert_service.resolve_system(a["alarmId"], "7")
    users = [rbac.load_user(r["id"]) for r in db.rows(
        "SELECT id FROM users WHERE email IN ('supervisor@demo.camview','admin@demo.camview')")]
    results, errors = [], []

    def worker(i):
        u = users[i % len(users)]
        try:
            results.append(tickets.decide(dict(alarm), u, "valid" if i % 2 == 0 else "invalid", expected_revision=0))
        except Exception as e:                    # conflicts are the expected loser outcome
            errors.append(getattr(e, "code", repr(e)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(errors) <= {"conflict"}, errors
    winners = [r for r in results if not r["idempotent"]]
    assert len(winners) == 1, f"exactly one decision may win, got {len(winners)}"
    assert db.get_review(a["alarmId"])["revision"] == 1
    assert _count("SELECT COUNT(*) n FROM tickets WHERE alarm_id=?", a["alarmId"]) <= 1
    assert _count("SELECT COUNT(*) n FROM publications WHERE alarm_id=? AND status='shared'", a["alarmId"]) \
        == (1 if winners[0]["result"] == "valid" else 0)


def test_valid_is_all_or_nothing(supervisor, monkeypatch):
    a = _pending(supervisor)

    def boom(*args, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(tickets, "ensure_ticket", boom)
    s, r = _decide(supervisor, a, "valid", expectedRevision=0)
    assert s == 500 and r["error"] == "internal_error"
    assert db.get_review(a["alarmId"])["status"] == "unreviewed", "the decision must not survive a failed ticket write"
    assert _count("SELECT COUNT(*) n FROM audit_events WHERE action='review.mark_valid' AND resource_id=?", a["alarmId"]) == 0
    assert _count("SELECT COUNT(*) n FROM jobs") == 0
    s, d = supervisor.get(f"/api/queue/{a['alarmId']}?projectId=7")
    assert d["alert"]["operatorDecision"] == "pending"


def test_failed_delivery_is_retried_by_the_durable_queue(supervisor, client_a, monkeypatch):
    _automatic()
    a = _pending(supervisor)
    real = tickets.deliver
    calls = {"n": 0}

    def flaky(*args, **kw):
        calls["n"] += 1
        raise ConnectionError("client portal store unavailable")
    monkeypatch.setattr(tickets, "deliver", flaky)
    s, r = _decide(supervisor, a, "valid", expectedRevision=0)
    assert s == 200, r                                     # the decision IS saved — and the answer says delivery is pending
    assert r["delivery"]["status"] == "queued" and r["delivery"]["retrying"]
    assert r["ticket"]["deliveryStatus"] == "queued"
    job = jobs.by_key(f"deliver:{a['alarmId']}:r1:client-a")
    assert job["status"] == jobs.RETRY and job["attempts"] == 1 and job["lastError"] and job["nextRunAt"]
    assert not any(x["alarmId"] == a["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"])
    monkeypatch.setattr(tickets, "deliver", real)
    assert jobs.run_due() == 0, "not due yet: exponential backoff"
    db.execute("UPDATE jobs SET next_run_at=? WHERE id=?", (db.now_iso(), job["id"]))
    assert jobs.run_due() == 1
    assert jobs.get(job["id"])["status"] == jobs.DONE and jobs.get(job["id"])["attempts"] == 2
    assert tickets.for_alarm(a["alarmId"])["deliveryStatus"] == "delivered"
    assert any(x["alarmId"] == a["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"])


def test_restart_during_delivery_resumes_exactly_once(supervisor, client_a, monkeypatch):
    _automatic()
    a = _pending(supervisor)
    monkeypatch.setattr(jobs, "run", lambda job_id, ignore_schedule=True: jobs.get(job_id))   # process dies before running
    s, r = _decide(supervisor, a, "valid", expectedRevision=0)
    assert s == 200 and r["delivery"]["status"] == "queued"
    job = jobs.by_key(f"deliver:{a['alarmId']}:r1:client-a")
    db.execute("UPDATE jobs SET status='RUNNING', attempts=1, started_at=? WHERE id=?", (db.now_iso(), job["id"]))
    monkeypatch.undo()
    jobs._recover_all["pending"] = True                    # a new process starts
    assert jobs.recover() == 1 and jobs.get(job["id"])["status"] == jobs.RETRY
    jobs.run_due()
    jobs.run(job["id"])                                    # a second run changes nothing
    assert jobs.get(job["id"])["status"] == jobs.DONE
    assert _count("SELECT COUNT(*) n FROM publications WHERE alarm_id=? AND status='shared'", a["alarmId"]) == 1
    assert _count("SELECT COUNT(*) n FROM notifications WHERE dedupe_key LIKE ?", f"delivered:{a['alarmId']}:%") \
        == _count("SELECT COUNT(*) n FROM users WHERE client_id='client-a' AND status='active'")
    assert any(x["alarmId"] == a["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"])


def test_a_changed_decision_cancels_the_pending_delivery(supervisor, client_a, monkeypatch):
    _automatic()
    a = _pending(supervisor)
    monkeypatch.setattr(tickets, "deliver", lambda *x, **k: (_ for _ in ()).throw(ConnectionError("down")))
    s, r = _decide(supervisor, a, "valid", expectedRevision=0)
    assert r["delivery"]["status"] == "queued"
    monkeypatch.undo()
    s, r = _decide(supervisor, a, "invalid", expectedRevision=1)
    assert s == 200
    job = jobs.by_key(f"deliver:{a['alarmId']}:r1:client-a")
    jobs.run(job["id"])
    assert jobs.get(job["id"])["status"] == jobs.FAILED       # Permanent: superseded — never retried, never published
    assert _count("SELECT COUNT(*) n FROM publications WHERE alarm_id=? AND status='shared'", a["alarmId"]) == 0
    assert not any(x["alarmId"] == a["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"])


def test_locked_database_answers_503_and_saves_nothing(supervisor, monkeypatch):
    a = _pending(supervisor)
    monkeypatch.setattr(db, "BUSY_TIMEOUT", 0.2)
    other = sqlite3.connect(db.DB_PATH, timeout=1)
    other.execute("BEGIN IMMEDIATE")                        # another writer holds the lock
    try:
        s, r = _decide(supervisor, a, "valid", expectedRevision=0)
    finally:
        other.rollback()
        other.close()
    assert s == 503 and r["error"] == "database_busy", r
    assert db.get_review(a["alarmId"])["status"] == "unreviewed"
    s, r = _decide(supervisor, a, "valid", expectedRevision=0)            # the retry the message asks for works
    assert s == 200


def test_a_committed_decision_is_never_reported_as_failed(supervisor, monkeypatch):
    """Regression: a notification sent after the commit (ready-to-send) used to raise out of decide(), so the
    screen showed an error for a decision that WAS saved. Post-commit side effects are best effort."""
    a = _pending(supervisor)
    monkeypatch.setattr(notify, "to_permission", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("smtp down")))
    s, r = _decide(supervisor, a, "valid", expectedRevision=0)            # controlled mode: "ready" notification
    assert s == 200 and r["delivery"]["status"] == "ready", r
    assert db.get_review(a["alarmId"])["status"] == "marked_valid"


def test_valid_again_rearms_a_delivery_that_gave_up(supervisor, client_a, monkeypatch):
    """Regression: after the delivery job ended FAILED, pressing VALID again returned the dead job (dedupe) and the
    ticket stayed 'queued' for ever. Asking again re-arms the same job; still one ticket, one delivery."""
    _automatic()
    a = _pending(supervisor)
    monkeypatch.setattr(tickets, "deliver", lambda *x, **k: (_ for _ in ()).throw(ConnectionError("down")))
    _decide(supervisor, a, "valid", expectedRevision=0)
    job = jobs.by_key(f"deliver:{a['alarmId']}:r1:client-a")
    db.execute("UPDATE jobs SET attempts=max_attempts, next_run_at=? WHERE id=?", (db.now_iso(), job["id"]))
    jobs.run(job["id"])                                                    # the last attempt fails → FAILED
    assert jobs.get(job["id"])["status"] == jobs.FAILED
    assert tickets.for_alarm(a["alarmId"])["deliveryStatus"] == "failed"  # the ticket says so (not "queued")
    monkeypatch.undo()
    s, r = _decide(supervisor, a, "valid", expectedRevision=1)
    assert s == 200 and r["idempotent"] and r["delivery"]["status"] == "delivered", r
    assert jobs.get(job["id"])["status"] == jobs.DONE
    assert _count("SELECT COUNT(*) n FROM jobs WHERE payload LIKE ?", f'%"{a["alarmId"]}"%') == 1
    assert any(x["alarmId"] == a["alarmId"] for x in client_a.get("/api/client/alerts")[1]["items"])
