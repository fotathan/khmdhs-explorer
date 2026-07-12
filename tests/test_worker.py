"""Background job worker (worker.py): queue claim/finalize mechanics, log
capping, cancellation of a running job, and crash-recovery via reconcile_stale.

The queue-mechanics tests drive the worker's DB helpers directly; the two
end-to-end tests run a real subprocess with worker.DB_PY monkeypatched to a
throwaway script (so no real ingestion / network happens).
"""
import time

import pytest

import worker
from tests.helpers import connect, login, make_user


def _reset():
    with connect() as c:      # CASCADE: ingest_act_log has an FK to ingest_job
        c.execute("TRUNCATE proc.ingest_job CASCADE")
        c.execute("TRUNCATE proc.table_extract_job CASCADE")


@pytest.fixture(autouse=True)
def _jobs_ready(_schema):
    """Ensure the schema exists (skips the whole module without a test DB) and
    each test starts with empty queues."""
    _reset()
    yield


def _enqueue(status="queued", command=None):
    with connect() as c:
        cur = c.cursor()
        cur.execute(
            """INSERT INTO proc.ingest_job (types, date_from, date_to, status, command, queued_at)
               VALUES (ARRAY['notice'], '2026-01-01', '2026-01-02', %s, %s, now())
               RETURNING id""",
            (status, command or []))
        return cur.fetchone()["id"]


def _job(jid):
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT status, exit_code, worker_id, log_text "
                    "FROM proc.ingest_job WHERE id=%s", (jid,))
        return cur.fetchone()


# --- claim ---
def test_claim_moves_queued_to_running():
    jid = _enqueue()
    with connect() as c:
        row = worker._claim(c, "proc.ingest_job", "host:1")
    assert row["id"] == jid
    assert row["status"] == "running"
    assert row["worker_id"] == "host:1"
    with connect() as c:                      # nothing left queued
        assert worker._claim(c, "proc.ingest_job", "host:1") is None


def test_claim_takes_oldest_first():
    first = _enqueue()
    _enqueue()
    with connect() as c:
        row = worker._claim(c, "proc.ingest_job", "w")
    assert row["id"] == first


# --- finalize: status derivation + the no-clobber guard ---
def test_finalize_done_error_cancelled():
    for exit_code, cancelled, expect in [(0, False, "done"),
                                         (2, False, "error"),
                                         (-15, True, "cancelled")]:
        jid = _enqueue(status="running")
        with connect() as c:
            worker._finalize(c, "proc.ingest_job", jid,
                             exit_code=exit_code, cancelled=cancelled)
        j = _job(jid)
        assert j["status"] == expect
        assert j["exit_code"] == exit_code


def test_finalize_never_clobbers_terminal_status():
    """If db.py already wrote a terminal status, finalize records exit_code but
    must NOT overwrite the status (guard: only when status='running')."""
    jid = _enqueue(status="done")
    with connect() as c:
        worker._finalize(c, "proc.ingest_job", jid, exit_code=1, cancelled=False)
    j = _job(jid)
    assert j["status"] == "done"              # guard held
    assert j["exit_code"] == 1                # exit_code still recorded


# --- log capping + cancel flag ---
def test_append_log_caps_to_tail(monkeypatch):
    jid = _enqueue()
    monkeypatch.setattr(worker, "LOG_CAP", 50)
    with connect() as c:
        worker._append_log(c, "proc.ingest_job", jid, "A" * 20)
        worker._append_log(c, "proc.ingest_job", jid, "B" * 100)
    log = _job(jid)["log_text"]
    assert len(log) == 50
    assert log == "B" * 50                    # keeps the newest tail


def test_cancel_requested_flag():
    jid = _enqueue()
    with connect() as c:
        assert worker._cancel_requested(c, "proc.ingest_job", jid) is False
        c.execute("UPDATE proc.ingest_job SET cancel_requested=true WHERE id=%s", (jid,))
        assert worker._cancel_requested(c, "proc.ingest_job", jid) is True


# --- end-to-end run + cancellation (real subprocess, fake db.py) ---
def test_run_once_runs_job_to_done(tmp_path, monkeypatch):
    fake = tmp_path / "fakedb.py"
    fake.write_text("import sys\nprint('hello from job')\nsys.exit(0)\n")
    monkeypatch.setattr(worker, "DB_PY", str(fake))
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setattr(worker, "LOG_FLUSH_SECONDS", 0.1)
    jid = _enqueue(command=["ignored"])
    with connect() as c:
        assert worker.run_once(c, "host:1") is True
    j = _job(jid)
    assert j["status"] == "done"
    assert j["exit_code"] == 0
    assert "hello from job" in (j["log_text"] or "")


def test_cancellation_terminates_running_job(tmp_path, monkeypatch):
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(worker, "DB_PY", str(sleeper))
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setattr(worker, "LOG_FLUSH_SECONDS", 0.1)
    jid = _enqueue(command=[])
    with connect() as c:      # cancel already requested when the worker picks it up
        c.execute("UPDATE proc.ingest_job SET cancel_requested=true WHERE id=%s", (jid,))
    t0 = time.monotonic()
    with connect() as c:
        worker.run_once(c, "host:1")
    elapsed = time.monotonic() - t0
    assert elapsed < 15                       # SIGTERM'd promptly, not left to sleep 60s
    assert _job(jid)["status"] == "cancelled"


# --- crash recovery: reconcile_stale flips a dead 'running' job ---
def test_stale_running_job_is_reconciled(client):
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")
    jid = _enqueue(status="running")
    with connect() as c:      # worker died: heartbeat far in the past, no window progress
        c.execute("UPDATE proc.ingest_job "
                  "SET heartbeat_at = now() - interval '1 hour' WHERE id=%s", (jid,))
    # loading an admin page runs reconcile_stale()
    assert client.get("/admin/collection", follow_redirects=False).status_code == 200
    assert _job(jid)["status"] == "stale"
