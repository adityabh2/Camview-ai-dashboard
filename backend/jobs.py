"""
jobs.py — durable work queue (outbox) for work that must not be lost.

A job is written in the SAME transaction as the decision that asks for it (tickets.decide: review + ticket +
delivery intent commit together), so there is never a "decided but nobody will deliver it" state. It then
runs at once in the request (the common case) and, if that fails, again from the background worker with
exponential backoff — also after a restart: a job left RUNNING by a process that died is taken up again.

    PENDING ──run──► RUNNING ──ok──► DONE
                        │
                        ├─ error ──► RETRY (next_run_at = now + backoff) ──► RUNNING …
                        └─ error after max_attempts, or Permanent ──► FAILED

Handlers must be idempotent (a job can run twice if the process dies right after the work and before DONE
is written): client delivery is an upsert of one publication and one ticket state, so it is.
Each job carries attempts, next_run_at, last_error, created_at and updated_at.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import db
import obs

log = logging.getLogger("camview.jobs")

PENDING, RUNNING, DONE, RETRY, FAILED = "PENDING", "RUNNING", "DONE", "RETRY", "FAILED"
STATES = (PENDING, RUNNING, DONE, RETRY, FAILED)
BACKOFF_BASE = 5            # seconds before the 2nd attempt; doubles every attempt
BACKOFF_MAX = 600
STUCK_SECONDS = 600         # a RUNNING job older than this belonged to a dead worker
HANDLERS = {}
FAILED_HOOKS = {}           # kind -> fn(payload, error): called once when a job ends FAILED


class Permanent(Exception):
    """Retrying cannot help (e.g. the decision was changed since): the job ends FAILED at once."""


def handler(kind, on_failed=None):
    def deco(fn):
        HANDLERS[kind] = fn
        if on_failed:
            FAILED_HOOKS[kind] = on_failed
        return fn
    return deco


def _failed_hook(job, error):
    hook = FAILED_HOOKS.get(job["kind"])
    if hook:
        try:
            hook(job["payload"], error)
        except Exception:
            log.exception("failure hook of job %s", job["id"])


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _now():
    return datetime.now(timezone.utc)


def backoff(attempts):
    return min(BACKOFF_MAX, BACKOFF_BASE * 2 ** max(0, attempts - 1))


def _row(r):
    if not r:
        return None
    return {"id": r["id"], "kind": r["kind"], "dedupeKey": r["dedupe_key"], "payload": db.jload(r["payload"], {}) or {},
            "status": r["status"], "attempts": r["attempts"], "maxAttempts": r["max_attempts"],
            "nextRunAt": r["next_run_at"], "lastError": r["last_error"], "result": db.jload(r["result"], None),
            "createdAt": r["created_at"], "updatedAt": r["updated_at"], "startedAt": r["started_at"],
            "finishedAt": r["finished_at"]}


def enqueue(kind, payload, dedupe_key=None, conn=None, max_attempts=8, rearm=False):
    """Adds a job (PENDING, due now) and returns its id. With a dedupe key, a second enqueue of the same work
    returns the existing job instead of creating another (double click, retry, reload). `rearm`: a FAILED job with
    that key is set back to PENDING with fresh attempts (someone explicitly asked for the work again)."""
    if conn is None:
        with db.connect() as own:
            return enqueue(kind, payload, dedupe_key, own, max_attempts, rearm)
    now = db.now_iso()
    if dedupe_key:
        r = conn.execute("SELECT id, status FROM jobs WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        if r:
            if rearm and r["status"] == FAILED:
                conn.execute("UPDATE jobs SET status='PENDING', attempts=0, next_run_at=?, updated_at=?, payload=? "
                             "WHERE id=?", (now, now, db.jdump(payload), r["id"]))
            return r["id"]
    cur = conn.execute("INSERT INTO jobs (kind, dedupe_key, payload, status, attempts, max_attempts, next_run_at, created_at, "
                       "updated_at) VALUES (?,?,?,?,0,?,?,?,?)",
                       (kind, dedupe_key, db.jdump(payload), PENDING, max_attempts, now, now, now))
    return cur.lastrowid


def get(job_id):
    return _row(db.one("SELECT * FROM jobs WHERE id=?", (job_id,)))


def by_key(dedupe_key):
    return _row(db.one("SELECT * FROM jobs WHERE dedupe_key=?", (dedupe_key,)))


def _claim(job_id, ignore_schedule=False):
    now = db.now_iso()
    with db.quiet(), db.connect(immediate=True) as conn:
        cond = "" if ignore_schedule else " AND (next_run_at IS NULL OR next_run_at <= ?)"
        params = [now, now, job_id] + ([] if ignore_schedule else [now])
        cur = conn.execute(f"UPDATE jobs SET status='RUNNING', attempts=attempts+1, started_at=?, updated_at=? "
                           f"WHERE id=? AND status IN ('PENDING','RETRY'){cond}", params)
        if cur.rowcount != 1:
            return None
        return _row(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def _finish(job, status, error=None, result=None):
    now = _now()
    nxt = _iso(now + timedelta(seconds=backoff(job["attempts"]))) if status == RETRY else None
    with db.quiet():
        db.execute("UPDATE jobs SET status=?, last_error=?, result=COALESCE(?, result), next_run_at=?, updated_at=?, "
                   "finished_at=? WHERE id=?",
                   (status, (error or None) and str(error)[:500], db.jdump(result) if result is not None else None, nxt,
                    _iso(now), _iso(now) if status in (DONE, FAILED) else None, job["id"]))
    return status


def run(job_id, ignore_schedule=True):
    """Runs one job now if it is waiting (PENDING / RETRY). Returns the job afterwards (None if unknown).
    A job that is RUNNING elsewhere or already finished is returned unchanged — never run twice at once."""
    job = _claim(job_id, ignore_schedule)
    if job is None:
        return get(job_id)
    import tickets  # noqa: F401  (registers the delivery handler even if nothing imported it yet)
    fn = HANDLERS.get(job["kind"])
    prev_rid = obs.request_id()
    if not prev_rid:
        obs.set_thread_request_id(f"job-{job['id']}")
    try:
        if fn is None:
            raise Permanent(f"no handler for job kind '{job['kind']}'")
        result = fn(job["payload"], job)
        _finish(job, DONE, result=result if result is not None else {"ok": True})
        obs.event(f"job.{job['kind']}", "ok", job=job["id"], attempt=job["attempts"],
                  alarm_id=job["payload"].get("alarmId"), project_id=job["payload"].get("projectId"))
    except Permanent as e:
        _finish(job, FAILED, error=e)
        obs.event(f"job.{job['kind']}", "failed", logging.WARNING, job=job["id"], error=e,
                  alarm_id=job["payload"].get("alarmId"), project_id=job["payload"].get("projectId"))
        _failed_hook(job, e)
    except Exception as e:                           # any other error: try again later (bounded)
        final = job["attempts"] >= job["maxAttempts"]
        _finish(job, FAILED if final else RETRY, error=f"{type(e).__name__}: {e}")
        if final:
            _failed_hook(job, e)
        obs.event(f"job.{job['kind']}", "failed" if final else "retry", logging.WARNING, job=job["id"],
                  attempt=job["attempts"], error=f"{type(e).__name__}: {e}",
                  alarm_id=job["payload"].get("alarmId"), project_id=job["payload"].get("projectId"))
        if not final:
            log.info("Job %s (%s) will be retried in %ss", job["id"], job["kind"], backoff(job["attempts"]))
    finally:
        if not prev_rid:
            obs.set_thread_request_id(None)
    return get(job_id)


def run_due(limit=25):
    """Runs every job that is due. Returns how many were run."""
    now = db.now_iso()
    due = db.rows("SELECT id FROM jobs WHERE status IN ('PENDING','RETRY') AND (next_run_at IS NULL OR next_run_at <= ?) "
                  "ORDER BY id LIMIT ?", (now, limit))
    n = 0
    for r in due:
        if run(r["id"], ignore_schedule=False) is not None:
            n += 1
    return n


def recover():
    """Start-up (and periodically): jobs left RUNNING by a process that stopped are taken up again."""
    cutoff = _iso(_now() - timedelta(seconds=STUCK_SECONDS))
    with db.quiet(), db.connect() as conn:
        n = conn.execute("UPDATE jobs SET status='RETRY', next_run_at=?, updated_at=?, "
                         "last_error=COALESCE(last_error, 'interrupted (process restart)') "
                         "WHERE status='RUNNING' AND (started_at IS NULL OR started_at <= ? OR ?)",
                         (db.now_iso(), db.now_iso(), cutoff, 1 if _recover_all["pending"] else 0)).rowcount
    _recover_all["pending"] = False
    if n:
        log.warning("Resumed %s interrupted job(s)", n)
    return n


_recover_all = {"pending": True}      # the first recover() of a process takes up EVERY running job (it cannot be ours)
_worker = {"thread": None, "stop": False, "lastRunAt": None}


def start_worker(interval=2.0):
    if _worker["thread"]:
        return

    def loop():
        recover()
        while not _worker["stop"]:
            try:
                run_due()
                _worker["lastRunAt"] = time.time()
                import backup
                backup.maybe_daily()                    # live mode: one verified database backup a day
                if int(time.time()) % 60 < interval:
                    recover()
            except db.DatabaseBusy:
                log.info("job worker: database busy — next round")
            except Exception:
                log.exception("job worker round failed")
            time.sleep(interval)

    _worker["stop"] = False
    _worker["thread"] = threading.Thread(target=loop, name="camview-jobs", daemon=True)
    _worker["thread"].start()


def worker_alive(max_idle=60):
    """The worker thread exists AND completed a round recently (a hung loop is not "healthy")."""
    t = _worker["thread"]
    last = _worker.get("lastRunAt")
    return bool(t and t.is_alive() and last and time.time() - last < max_idle)


def summary():
    counts = {s: 0 for s in STATES}
    for r in db.rows("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
        counts[r["status"]] = r["n"]
    failed = [_row(r) for r in db.rows("SELECT * FROM jobs WHERE status IN ('FAILED','RETRY') ORDER BY updated_at DESC LIMIT 20")]
    return {"counts": counts, "attention": failed, "workerAlive": worker_alive(),
            "inline": os.environ.get("CAMVIEW_TESTING") == "1"}


def retry(job_id):
    """An administrator re-queues a FAILED job (attempts start again)."""
    with db.connect() as conn:
        n = conn.execute("UPDATE jobs SET status='PENDING', attempts=0, next_run_at=?, updated_at=? WHERE id=? AND status='FAILED'",
                         (db.now_iso(), db.now_iso(), job_id)).rowcount
    return bool(n)
