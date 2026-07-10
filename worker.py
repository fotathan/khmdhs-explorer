"""
worker.py — background runner for admin-launched ingestion / table-extract jobs.

The admin UI ENQUEUES a job (a row in proc.ingest_job or proc.table_extract_job
with status='queued', the db.py argv in `command`, and per-run env in `job_env`).
This worker claims one queued job at a time, runs it as a `db.py` subprocess (the
exact same CLI the shell uses — no ingestion logic lives here), streams its
stdout/stderr into the job row's `log_text`, bumps `heartbeat_at` so the web UI
can tell it's alive across containers, and honours the `cancel_requested` flag.

Why this exists: previously the web process spawned the subprocess on its own
container. On Render that starves the web dyno and — worse — a deploy/restart
destroys the container and kills the detached job, and the local log file with
it. A separate worker container fixes all three.

Two ways it runs:
  * Standalone (prod): `python worker.py` as a Render Background Worker — its own
    container, off the web dyno. Loops until SIGTERM/SIGINT.
  * Inline (local dev): app.main starts run_loop() in a daemon thread when NOT on
    Render, so clicking a button locally still just works with no 2nd process.

One job at a time: both queues share this single runner, preserving the app's
"one backfill at a time" invariant. The db.py runner writes its own terminal
status (done/error) guarded on status='running'; this worker only fills the gap
on crash/cancel and records exit_code — the two never fight over the status.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time

import psycopg
from psycopg.rows import dict_row

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PY = os.path.join(ROOT, "db.py")

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "3"))
HEARTBEAT_SECONDS = float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "5"))
LOG_FLUSH_SECONDS = float(os.environ.get("WORKER_LOG_FLUSH_SECONDS", "2"))
LOG_CAP = int(os.environ.get("WORKER_LOG_CAP", "200000"))   # keep last ~200 KB

# Job queues, in the order the worker checks them each tick. Both tables share
# the same queue columns (see the job_queue_worker_columns migration).
QUEUES = ("proc.ingest_job", "proc.table_extract_job")


def _connect():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("worker: DATABASE_URL is not set")
    # prepare_threshold=None keeps us safe behind Supabase's transaction pooler,
    # same as the web app's cursor().
    return psycopg.connect(dsn, autocommit=True, prepare_threshold=None,
                           row_factory=dict_row)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _claim(conn, table, wid):
    """Atomically grab the oldest queued row for this table, or None."""
    with conn.cursor() as c:
        c.execute(f"""
            UPDATE {table} SET status='running', worker_id=%s,
                   started_at=now(), heartbeat_at=now()
            WHERE id = (SELECT id FROM {table} WHERE status='queued'
                        ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
            RETURNING *""", (wid,))
        return c.fetchone()


def _append_log(conn, table, jid, chunk):
    if not chunk:
        return
    with conn.cursor() as c:
        c.execute(f"UPDATE {table} SET log_text = "
                  f"right(coalesce(log_text,'') || %s, %s) WHERE id=%s",
                  (chunk, LOG_CAP, jid))


def _heartbeat(conn, table, jid):
    with conn.cursor() as c:
        c.execute(f"UPDATE {table} SET heartbeat_at=now() WHERE id=%s", (jid,))


def _cancel_requested(conn, table, jid) -> bool:
    with conn.cursor() as c:
        c.execute(f"SELECT cancel_requested FROM {table} WHERE id=%s", (jid,))
        r = c.fetchone()
        return bool(r and r["cancel_requested"])


def _finalize(conn, table, jid, *, exit_code, cancelled):
    """Record exit_code, and set a terminal status ONLY if the db.py runner
    didn't already (guard on status='running'), so we never clobber a
    done/error/cancelled it wrote."""
    status = "cancelled" if cancelled else ("done" if exit_code == 0 else "error")
    with conn.cursor() as c:
        c.execute(f"""UPDATE {table}
                      SET exit_code=%s,
                          finished_at=coalesce(finished_at, now()),
                          status = CASE WHEN status='running' THEN %s ELSE status END
                      WHERE id=%s""", (exit_code, status, jid))


def _kill(proc, sig):
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _run_job(conn, table, job):
    """Run one claimed job to completion, streaming output + honouring cancel."""
    jid = job["id"]
    command = job.get("command") or []
    job_env = job.get("job_env") or {}
    env = {**os.environ, **{k: str(v) for k, v in job_env.items()}}
    argv = [sys.executable, DB_PY, *command]

    _append_log(conn, table, jid,
                f"# worker {job['worker_id']} running: db.py {' '.join(command)}\n\n")

    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, bufsize=1,
        start_new_session=True, cwd=ROOT, env=env)

    buf: list[str] = []
    lock = threading.Lock()

    def _reader():
        # Blocks reading the child's output; the main loop drains `buf` to the DB.
        for line in proc.stdout:
            with lock:
                buf.append(line)

    rt = threading.Thread(target=_reader, daemon=True)
    rt.start()

    def _drain():
        with lock:
            chunk = "".join(buf)
            buf.clear()
        _append_log(conn, table, jid, chunk)

    cancelled = False
    last_hb = last_flush = 0.0
    while proc.poll() is None:
        now = time.monotonic()
        if now - last_flush >= LOG_FLUSH_SECONDS:
            _drain()
            last_flush = now
        if now - last_hb >= HEARTBEAT_SECONDS:
            _heartbeat(conn, table, jid)
            if _cancel_requested(conn, table, jid):
                cancelled = True
                _append_log(conn, table, jid, "\n# cancel requested — terminating\n")
                _kill(proc, signal.SIGTERM)
                break
            last_hb = now
        time.sleep(0.3)

    # Wait for exit; escalate to SIGKILL if a cancelled child ignores SIGTERM.
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        _kill(proc, signal.SIGKILL)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    rt.join(timeout=5)
    _drain()
    rc = proc.returncode if proc.returncode is not None else -1
    _finalize(conn, table, jid, exit_code=rc, cancelled=cancelled)
    return rc


def run_once(conn, wid) -> bool:
    """Claim and run at most one job across the queues. Returns True if one ran."""
    for table in QUEUES:
        job = _claim(conn, table, wid)
        if job:
            _run_job(conn, table, job)
            return True
    return False


def run_loop(stop_event=None):
    """Poll the queues until stop_event is set (or forever if None)."""
    wid = _worker_id()
    conn = _connect()
    print(f"worker {wid} started (poll={POLL_SECONDS}s)", flush=True)
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                did = run_once(conn, wid)
            except Exception as e:      # noqa: BLE001 — keep the loop alive
                print(f"worker error: {e!r}", flush=True)
                try:
                    conn.close()
                except Exception:
                    pass
                _interruptible_sleep(POLL_SECONDS, stop_event)
                try:
                    conn = _connect()
                except Exception as e2:      # noqa: BLE001
                    print(f"worker reconnect failed: {e2!r}", flush=True)
                continue
            if not did:
                _interruptible_sleep(POLL_SECONDS, stop_event)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        print(f"worker {wid} stopped", flush=True)


def _interruptible_sleep(seconds, stop_event):
    slept = 0.0
    while (stop_event is None or not stop_event.is_set()) and slept < seconds:
        time.sleep(0.3)
        slept += 0.3


def main():
    stop = threading.Event()

    def _handle(signum, frame):      # noqa: ARG001
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    run_loop(stop)


if __name__ == "__main__":
    main()
