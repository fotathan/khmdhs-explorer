"""
admin.py — backfill launcher and job management UI.

What it provides
----------------
  GET  /admin                  — launcher form + recent jobs
  POST /admin/jobs             — start a new backfill (subprocess), redirect to detail
  GET  /admin/jobs/{id}        — one job's live status (auto-refreshing if running)
  POST /admin/jobs/{id}/cancel — terminate the subprocess for a running job
  GET  /admin/jobs/{id}/log    — tail of the subprocess stdout/stderr

Design notes
------------
* Backfills are long-running; we don't block the request. We spawn `db.py
  backfill` as a detached subprocess and return immediately. The job is
  tracked in proc.ingest_job (pid + status + params); fine-grained per-window
  progress is read from proc.ingest_window which the runner already writes.
* Only ONE running backfill at a time. The API itself is rate-limited so
  concurrency wouldn't help; and serial runs keep semantics clean (no two
  writers for the same window).
* If uvicorn is restarted, jobs survive (they're detached subprocesses). We
  detect "stale" rows (status='running' but PID is gone) and surface them
  honestly rather than showing them as still running.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import signal
import subprocess
import sys
from urllib.parse import unquote
from typing import Optional

from fastapi import (APIRouter, File, Form, HTTPException, Query, Request,
                     UploadFile, status)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates


# ---------------------------------------------------------------------------- #
# Wired up by main.py: we need its template engine and DB cursor() helper.
# ---------------------------------------------------------------------------- #
def _clean_number(s):
    """Normalize a number/currency string in any common format to a DB-ready
    numeric string, so a value the curator pasted or typed ('1.234.567,89 €',
    '1,234,567.89', '1234,56') lands correctly in a numeric column instead of
    breaking the cast. Greek-primary: a lone comma is the decimal separator, a
    lone dot introducing a 3-digit group is a thousands separator. Mirrors the
    act form's cleanNumber(). Returns the ORIGINAL string when no number can be
    parsed, so genuinely bad input still fails loudly (no silent data loss)."""
    if s is None:
        return None
    t = re.sub(r"[^0-9.,-]", "", str(s))
    neg = t.startswith("-")
    t = t.replace("-", "")
    if not t:
        return s
    commas, dots = t.count(","), t.count(".")
    lc, ld = t.rfind(","), t.rfind(".")
    if not commas and not dots:
        num = t
    elif lc > ld:                                   # comma is the rightmost sep
        num = (t.replace(".", "").replace(",", "")  # 1,234,567 → thousands
               if commas > 1
               else t.replace(".", "").replace(",", "."))  # 1.234,56 → decimal
    else:                                           # dot is rightmost / only dots
        if lc != -1:
            num = t.replace(",", "")                # 1,234.56 → English decimal
        elif dots > 1:
            num = t.replace(".", "")                # 1.234.567 → Greek thousands
        else:
            tail = len(t) - ld - 1                  # single dot, no comma: ambiguous
            num = t.replace(".", "") if (tail == 3 and ld <= 3) else t
    try:
        v = round(float(num), 2)
    except ValueError:
        return s
    if neg:
        v = -v
    return str(int(v)) if v == int(v) else str(v)   # whole → int (safe for int cols)


def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    # Where the subprocess writes its logs (one file per job).
    LOG_DIR = os.environ.get("KHMDHS_LOG_DIR", "/tmp/khmdhs-jobs")
    os.makedirs(LOG_DIR, exist_ok=True)

    ACT_TYPES = ["request", "notice", "auction", "contract", "payment"]

    # Diavgeia decision types the admin panel can harvest. Keys are the readable
    # names stored in proc.ingest_job.types; values are the Diavgeia decision-type
    # UIDs used as decision_type in proc.diavgeia_ingest_window. Source of truth
    # is diavgeia_ingest.NAME_TO_UID — kept in sync here so the web app doesn't
    # have to import the ingest module (and its API deps).
    DIAVGEIA_TYPES = {"notice": "Δ.2.1", "award": "Δ.2.2", "contract": "Γ.3.4"}
    DIAVGEIA_TYPE_NAMES = list(DIAVGEIA_TYPES)
    DIAVGEIA_UID_TO_NAME = {v: k for k, v in DIAVGEIA_TYPES.items()}

    # How many per-act log rows the job page shows inline; the rest are reachable
    # via the CSV download. Keeps the page light when a run touched many acts.
    ACT_LOG_PREVIEW = 200

    # Full-text filter for the job page's per-act list (garbled stores text, so
    # full_text_extracted is true for it — hence the explicit garbled split).
    _FT_FILTERS = {
        "all": "",
        "with": "AND full_text_extracted AND full_text_note IS DISTINCT FROM 'garbled'",
        "without": "AND NOT full_text_extracted",
        "garbled": "AND full_text_note = 'garbled'",
    }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def pid_alive(pid: Optional[int]) -> bool:
        """Whether a launched backfill PID is still genuinely running.

        Backfills are spawned detached but stay children of this web process,
        so a finished one becomes a <defunct> zombie until reaped — and
        os.kill(zombie, 0) SUCCEEDS, which used to make a completed job look
        like it was still running forever (page kept auto-refreshing, new jobs
        stayed blocked). So we first try a non-blocking reap: if it has exited
        we collect it here and report not-alive."""
        if not pid:
            return False
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                return False        # exited just now and reaped → finished
            if reaped == 0:
                return True         # still our running child
        except ChildProcessError:
            pass                    # not our child (e.g. after a web restart)
        except OSError:
            pass
        try:
            os.kill(pid, 0)         # signal 0 = no-op probe
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True             # process exists but is owned by another user

    def reconcile_stale():
        """Move jobs out of 'running' once their detached subprocess is gone.

        The runner records its own terminal status on a clean exit; this is the
        safety net for runs that were killed/crashed before doing so (and for
        jobs that predate that runner change). We infer the outcome from the
        per-window rows the runner did write: every window finished ⇒ 'done'
        (or 'error' if any errored); work still unfinished or no windows ever
        registered ⇒ 'stale'. Called on every admin page load — cheap."""
        with cursor() as c:
            c.execute("""SELECT id, pid, types, date_from, date_to, source
                         FROM proc.ingest_job WHERE status='running'""")
            rows = c.fetchall()
            for r in rows:
                if pid_alive(r["pid"]):
                    continue
                # Which window table holds this job's progress depends on source.
                if r["source"] == "diavgeia":
                    uids = [DIAVGEIA_TYPES[t] for t in (r["types"] or [])
                            if t in DIAVGEIA_TYPES]
                    c.execute("""SELECT
                                   count(*) AS total,
                                   count(*) FILTER (WHERE status IN ('pending','running')) AS unfinished,
                                   count(*) FILTER (WHERE status='error') AS errored
                                 FROM proc.diavgeia_ingest_window
                                 WHERE decision_type = ANY(%s)
                                   AND date_from >= %s AND date_to <= %s""",
                              (uids, r["date_from"], r["date_to"]))
                else:
                    c.execute("""SELECT
                                   count(*) AS total,
                                   count(*) FILTER (WHERE status IN ('pending','running')) AS unfinished,
                                   count(*) FILTER (WHERE status='error') AS errored
                                 FROM proc.ingest_window
                                 WHERE act_type = ANY(%s)
                                   AND date_from >= %s AND date_to <= %s""",
                              (r["types"], r["date_from"], r["date_to"]))
                w = c.fetchone()
                if not w["total"] or w["unfinished"]:
                    new_status = "stale"
                elif w["errored"]:
                    new_status = "error"
                else:
                    new_status = "done"
                c.execute("""UPDATE proc.ingest_job
                             SET status=%s, finished_at=now()
                             WHERE id=%s AND status='running'""",
                          (new_status, r["id"]))

    def any_running() -> bool:
        with cursor() as c:
            c.execute("""SELECT id, pid FROM proc.ingest_job
                         WHERE status='running'""")
            for r in c.fetchall():
                if pid_alive(r["pid"]):
                    return True
        return False

    def project_root() -> str:
        """Find the directory containing db.py (parent of app/)."""
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.dirname(here)

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #
    @router.get("")
    def admin_home():
        # The admin landing defaults to the acts-management tab.
        return RedirectResponse(url="/admin/acts", status_code=303)

    @router.get("/collection", response_class=HTMLResponse)
    def admin_collection(request: Request):
        """Συλλογή Δεδομένων tab — backfill launcher + job history."""
        reconcile_stale()
        with cursor() as c:
            c.execute("""SELECT id, status, types, date_from, date_to, resume,
                                started_at, finished_at, exit_code, last_error, pid,
                                source
                         FROM proc.ingest_job
                         ORDER BY started_at DESC LIMIT 30""")
            jobs = c.fetchall()
            # Per-type ingestion summary so the user knows what's been covered.
            c.execute("""SELECT act_type,
                                count(*) AS windows,
                                sum(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done_n,
                                sum(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_n,
                                min(date_from) AS covered_from,
                                max(date_to) AS covered_to
                         FROM proc.ingest_window
                         GROUP BY act_type ORDER BY act_type""")
            window_summary = c.fetchall()
            # Same coverage roll-up for Diavgeia, keyed by decision_type UID which
            # we re-label to a readable name for the template.
            diavgeia_summary = []
            try:
                c.execute("""SELECT decision_type,
                                    count(*) AS windows,
                                    sum(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done_n,
                                    sum(CASE WHEN status='error' THEN 1 ELSE 0 END) AS error_n,
                                    min(date_from) AS covered_from,
                                    max(date_to) AS covered_to
                             FROM proc.diavgeia_ingest_window
                             GROUP BY decision_type ORDER BY decision_type""")
                for r in c.fetchall():
                    r["act_type"] = DIAVGEIA_UID_TO_NAME.get(
                        r["decision_type"], r["decision_type"])
                    diavgeia_summary.append(r)
            except Exception:
                # Diavgeia window table absent (e.g. an environment without the
                # diavgeia migration) — just show no Diavgeia coverage.
                diavgeia_summary = []
        return templates.TemplateResponse(
            request, "admin_index.html",
            {"jobs": jobs, "window_summary": window_summary,
             "diavgeia_summary": diavgeia_summary,
             "running_now": any_running(),
             "act_types": ACT_TYPES,
             "diavgeia_types": DIAVGEIA_TYPE_NAMES,
             "today": dt.date.today().isoformat(),
             "default_start": (dt.date.today() - dt.timedelta(days=180)).isoformat(),
             "admin_tab": "collection"},
        )

    @router.post("/jobs")
    def admin_start_job(request: Request,
                        date_from: str = Form(...),
                        date_to: str = Form(""),
                        types: list[str] = Form(default=[]),
                        resume: str = Form(default=""),
                        extract_fulltext: str = Form(default="")):
        # Validate inputs up front so a bad request fails before we spawn anything.
        try:
            df = dt.date.fromisoformat(date_from)
            dtt = dt.date.fromisoformat(date_to) if date_to else dt.date.today()
        except ValueError:
            raise HTTPException(400, "invalid date (expected YYYY-MM-DD)")
        if dtt < df:
            raise HTTPException(400, "date_to must be on or after date_from")
        chosen = [t for t in types if t in ACT_TYPES]
        if not chosen:
            chosen = ACT_TYPES[:]   # all five
        resume_flag = bool(resume)
        fulltext_flag = bool(extract_fulltext)

        # Refuse if another backfill is already running. The reconcile call
        # ensures we don't get blocked by a stale 'running' row.
        reconcile_stale()
        if any_running():
            raise HTTPException(409,
                "another backfill is already running; cancel it or wait for it to finish")

        # Build the subprocess command. We invoke the same db.py CLI you use
        # from the shell, with the same flags — keeps one code path.
        root = project_root()
        cmd = [sys.executable, os.path.join(root, "db.py"), "backfill",
               "--start", df.isoformat(), "--end", dtt.isoformat(),
               "--types", *chosen]
        if resume_flag:
            cmd.append("--resume")

        # Insert the job row FIRST, get its id, then open the log file using
        # that id so the log path is deterministic.
        with cursor() as c:
            c.execute("""INSERT INTO proc.ingest_job
                         (status, types, date_from, date_to, resume)
                         VALUES ('running', %s, %s, %s, %s) RETURNING id""",
                      (chosen, df, dtt, resume_flag))
            job_id = c.fetchone()["id"]
        log_path = os.path.join(LOG_DIR, f"job-{job_id}.log")

        # Spawn DETACHED: own process group, stdout/stderr to the log file.
        # On POSIX this means uvicorn restarting won't kill the backfill, and
        # we can later signal the whole group with os.killpg().
        # Subprocess environment: inherit everything (incl. DATABASE_URL), and
        # turn on full-text extraction for THIS run only if the toggle was set.
        # khmdhs_ingest reads EXTRACT_FULLTEXT; it defaults off otherwise, so a
        # plain backfill stays fast.
        job_env = {**os.environ}
        if fulltext_flag:
            job_env["EXTRACT_FULLTEXT"] = "1"
        else:
            job_env.pop("EXTRACT_FULLTEXT", None)
        # Tell the runner its job id so it writes per-act rows to
        # proc.ingest_act_log (the job page's transparency log).
        job_env["INGEST_JOB_ID"] = str(job_id)

        log_fh = open(log_path, "w")
        # Record what this run is doing at the top of its own log, so the job
        # detail page shows whether full-text extraction was on.
        log_fh.write(
            f"# backfill job {job_id}: types={chosen} "
            f"{df.isoformat()}..{dtt.isoformat()} resume={resume_flag} "
            f"extract_fulltext={fulltext_flag}\n\n")
        log_fh.flush()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,             # detach process group
                cwd=root,
                env=job_env,                         # inherit + optional fulltext
            )
        except Exception as e:
            with cursor() as c:
                c.execute("""UPDATE proc.ingest_job
                             SET status='error', last_error=%s, finished_at=now()
                             WHERE id=%s""", (f"spawn failed: {e!r}", job_id))
            log_fh.close()
            raise HTTPException(500, f"failed to launch subprocess: {e!r}")

        with cursor() as c:
            c.execute("""UPDATE proc.ingest_job SET pid=%s, log_path=%s
                         WHERE id=%s""", (proc.pid, log_path, job_id))

        return RedirectResponse(url=f"/admin/jobs/{job_id}", status_code=303)

    @router.post("/jobs/fulltext")
    def admin_start_fulltext_job(request: Request,
                                 types: list[str] = Form(default=[]),
                                 limit: str = Form(default="5000")):
        """Spawn a mass full-text backfill (db.py fulltext-backfill) over acts
        already in the LOCAL database that have no text yet. Resumable & bounded
        by --limit; uses the same job infrastructure as the harvest backfill so
        progress/log/monitoring all work. For PRODUCTION, use ingest.sh from the
        terminal instead — this UI runs where the web server runs."""
        chosen = [t for t in types if t in ACT_TYPES]
        if not chosen:
            chosen = ACT_TYPES[:]
        try:
            lim = max(1, int(limit))
        except ValueError:
            lim = 5000

        reconcile_stale()
        if any_running():
            raise HTTPException(409,
                "another job is already running; cancel it or wait for it to finish")

        root = project_root()
        cmd = [sys.executable, os.path.join(root, "db.py"), "fulltext-backfill",
               "--types", *chosen, "--limit", str(lim)]

        today = dt.date.today()
        with cursor() as c:
            c.execute("""INSERT INTO proc.ingest_job
                         (status, types, date_from, date_to, resume)
                         VALUES ('running', %s, %s, %s, %s) RETURNING id""",
                      (chosen, today, today, False))
            job_id = c.fetchone()["id"]
        log_path = os.path.join(LOG_DIR, f"job-{job_id}.log")

        log_fh = open(log_path, "w")
        log_fh.write(f"# full-text backfill job {job_id}: types={chosen} "
                     f"limit={lim}\n\n")
        log_fh.flush()
        try:
            proc = subprocess.Popen(
                cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
                cwd=root, env={**os.environ},
            )
        except Exception as e:
            with cursor() as c:
                c.execute("""UPDATE proc.ingest_job
                             SET status='error', last_error=%s, finished_at=now()
                             WHERE id=%s""", (f"spawn failed: {e!r}", job_id))
            log_fh.close()
            raise HTTPException(500, f"failed to launch subprocess: {e!r}")

        with cursor() as c:
            c.execute("""UPDATE proc.ingest_job SET pid=%s, log_path=%s
                         WHERE id=%s""", (proc.pid, log_path, job_id))

        return RedirectResponse(url=f"/admin/jobs/{job_id}", status_code=303)

    @router.post("/jobs/diavgeia")
    def admin_start_diavgeia_job(request: Request,
                                 date_from: str = Form(...),
                                 date_to: str = Form(""),
                                 types: list[str] = Form(default=[]),
                                 resume: str = Form(default=""),
                                 extract_fulltext: str = Form(default="")):
        """Spawn a Diavgeia harvest (db.py diavgeia-backfill) over an issueDate
        range. Parallel to admin_start_job but for the Diavgeia source: harvests
        decisions, dedups authorities, and projects into procurement_act so the
        acts surface in the app. Tracked in the same proc.ingest_job table with
        source='diavgeia', so the job page / cancel / monitoring all work."""
        try:
            df = dt.date.fromisoformat(date_from)
            dtt = dt.date.fromisoformat(date_to) if date_to else dt.date.today()
        except ValueError:
            raise HTTPException(400, "invalid date (expected YYYY-MM-DD)")
        if dtt < df:
            raise HTTPException(400, "date_to must be on or after date_from")
        chosen = [t for t in types if t in DIAVGEIA_TYPES]
        if not chosen:
            chosen = DIAVGEIA_TYPE_NAMES[:]
        resume_flag = bool(resume)

        reconcile_stale()
        if any_running():
            raise HTTPException(409,
                "another backfill is already running; cancel it or wait for it to finish")

        root = project_root()
        cmd = [sys.executable, os.path.join(root, "db.py"), "diavgeia-backfill",
               "--start", df.isoformat(), "--end", dtt.isoformat(),
               "--types", *chosen]
        if resume_flag:
            cmd.append("--resume")

        with cursor() as c:
            c.execute("""INSERT INTO proc.ingest_job
                         (status, types, date_from, date_to, resume, source)
                         VALUES ('running', %s, %s, %s, %s, 'diavgeia')
                         RETURNING id""",
                      (chosen, df, dtt, resume_flag))
            job_id = c.fetchone()["id"]
        log_path = os.path.join(LOG_DIR, f"job-{job_id}.log")

        fulltext_flag = bool(extract_fulltext)
        job_env = {**os.environ}
        # Opt-in full-text: fetch + parse each handled act's Diavgeia document and
        # store its text (post-projection pass). diavgeia_ingest reads
        # EXTRACT_FULLTEXT, same as the KHMDHS harvest.
        if fulltext_flag:
            job_env["EXTRACT_FULLTEXT"] = "1"
        else:
            job_env.pop("EXTRACT_FULLTEXT", None)
        job_env["INGEST_JOB_ID"] = str(job_id)

        log_fh = open(log_path, "w")
        log_fh.write(
            f"# diavgeia backfill job {job_id}: types={chosen} "
            f"{df.isoformat()}..{dtt.isoformat()} resume={resume_flag} "
            f"extract_fulltext={fulltext_flag}\n\n")
        log_fh.flush()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd=root,
                env=job_env,
            )
        except Exception as e:
            with cursor() as c:
                c.execute("""UPDATE proc.ingest_job
                             SET status='error', last_error=%s, finished_at=now()
                             WHERE id=%s""", (f"spawn failed: {e!r}", job_id))
            log_fh.close()
            raise HTTPException(500, f"failed to launch subprocess: {e!r}")

        with cursor() as c:
            c.execute("""UPDATE proc.ingest_job SET pid=%s, log_path=%s
                         WHERE id=%s""", (proc.pid, log_path, job_id))

        return RedirectResponse(url=f"/admin/jobs/{job_id}", status_code=303)

    @router.post("/jobs/diavgeia-fulltext")
    def admin_start_diavgeia_fulltext_job(request: Request,
                                          limit: str = Form(default="5000")):
        """Mass full-text extraction over Diavgeia acts ALREADY in the LOCAL
        database that have no text yet — the Diavgeia counterpart of
        /admin/jobs/fulltext (db.py diavgeia-fulltext-backfill). Resumable &
        bounded by --limit; tracked as a source='diavgeia' job so the job page /
        cancel / per-act log all work. Runs where the web server runs; for
        production use ingest.sh from the terminal."""
        try:
            lim = max(1, int(limit))
        except ValueError:
            lim = 5000

        reconcile_stale()
        if any_running():
            raise HTTPException(409,
                "another job is already running; cancel it or wait for it to finish")

        root = project_root()
        cmd = [sys.executable, os.path.join(root, "db.py"),
               "diavgeia-fulltext-backfill", "--limit", str(lim)]

        today = dt.date.today()
        with cursor() as c:
            c.execute("""INSERT INTO proc.ingest_job
                         (status, types, date_from, date_to, resume, source)
                         VALUES ('running', %s, %s, %s, %s, 'diavgeia')
                         RETURNING id""",
                      (DIAVGEIA_TYPE_NAMES[:], today, today, False))
            job_id = c.fetchone()["id"]
        log_path = os.path.join(LOG_DIR, f"job-{job_id}.log")

        job_env = {**os.environ}
        job_env["INGEST_JOB_ID"] = str(job_id)

        log_fh = open(log_path, "w")
        log_fh.write(f"# diavgeia full-text backfill job {job_id}: limit={lim}\n\n")
        log_fh.flush()
        try:
            proc = subprocess.Popen(
                cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
                cwd=root, env=job_env,
            )
        except Exception as e:
            with cursor() as c:
                c.execute("""UPDATE proc.ingest_job
                             SET status='error', last_error=%s, finished_at=now()
                             WHERE id=%s""", (f"spawn failed: {e!r}", job_id))
            log_fh.close()
            raise HTTPException(500, f"failed to launch subprocess: {e!r}")

        with cursor() as c:
            c.execute("""UPDATE proc.ingest_job SET pid=%s, log_path=%s
                         WHERE id=%s""", (proc.pid, log_path, job_id))

        return RedirectResponse(url=f"/admin/jobs/{job_id}", status_code=303)

    @router.get("/jobs/{job_id}", response_class=HTMLResponse)
    def admin_job_detail(job_id: int, request: Request,
                         ftf: str = Query("all")):
        """Job detail: window progress, raw log tail, and the per-act
        transparency log (proc.ingest_act_log) for this run. `ftf` filters the
        per-act list by full-text outcome: all | with | without | garbled."""
        if ftf not in _FT_FILTERS:
            ftf = "all"
        reconcile_stale()
        with cursor() as c:
            c.execute("""SELECT * FROM proc.ingest_job WHERE id=%s""", (job_id,))
            job = c.fetchone()
            if not job:
                raise HTTPException(404, f"job {job_id} not found")

            if job["source"] == "diavgeia":
                # Diavgeia progress lives in a different window table, keyed by
                # decision_type UID; re-label to a readable name for the page.
                uids = [DIAVGEIA_TYPES[t] for t in (job["types"] or [])
                        if t in DIAVGEIA_TYPES]
                c.execute("""SELECT decision_type, date_from, date_to, status,
                                    last_error, started_at, finished_at
                             FROM proc.diavgeia_ingest_window
                             WHERE decision_type = ANY(%s)
                               AND date_from >= %s AND date_to <= %s
                             ORDER BY decision_type, date_from""",
                          (uids, job["date_from"], job["date_to"]))
                windows = c.fetchall()
                for w in windows:
                    w["act_type"] = DIAVGEIA_UID_TO_NAME.get(
                        w["decision_type"], w["decision_type"])
                c.execute("""SELECT status, count(*) FROM proc.diavgeia_ingest_window
                             WHERE decision_type = ANY(%s)
                               AND date_from >= %s AND date_to <= %s
                             GROUP BY status""",
                          (uids, job["date_from"], job["date_to"]))
                counts = {r["status"]: r["count"] for r in c.fetchall()}
            else:
                # Detailed window progress for the act types this job touches,
                # scoped to its date range. Same source of truth the runner writes.
                c.execute("""SELECT act_type, date_from, date_to, status,
                                    last_error, started_at, finished_at
                             FROM proc.ingest_window
                             WHERE act_type = ANY(%s)
                               AND date_from >= %s AND date_to <= %s
                             ORDER BY act_type, date_from""",
                          (job["types"], job["date_from"], job["date_to"]))
                windows = c.fetchall()
                # Roll-up counters for the header.
                c.execute("""SELECT status, count(*) FROM proc.ingest_window
                             WHERE act_type = ANY(%s)
                               AND date_from >= %s AND date_to <= %s
                             GROUP BY status""",
                          (job["types"], job["date_from"], job["date_to"]))
                counts = {r["status"]: r["count"] for r in c.fetchall()}

            # Per-act transparency log for this run: counts by action, full-text
            # tally, and the most recent rows (the rest are in the CSV download).
            # Keyed by job_id, so it's identical for both sources — the KHMDHS and
            # Diavgeia harvests both write proc.ingest_act_log.
            c.execute("""SELECT action, count(*) AS n
                         FROM proc.ingest_act_log WHERE job_id=%s
                         GROUP BY action""", (job_id,))
            act_actions = {r["action"]: r["n"] for r in c.fetchall()}
            c.execute("""SELECT count(*) AS total,
                                count(*) FILTER (WHERE full_text_extracted) AS ft_yes,
                                count(*) FILTER (WHERE full_text_note='garbled') AS ft_garbled
                         FROM proc.ingest_act_log WHERE job_id=%s""", (job_id,))
            ftrow = c.fetchone()
            act_log_total = ftrow["total"] if ftrow else 0
            act_ft_yes = ftrow["ft_yes"] if ftrow else 0
            act_ft_garbled = ftrow["ft_garbled"] if ftrow else 0
            c.execute(f"""SELECT adam, act_type, title, action,
                                full_text_extracted, full_text_chars,
                                full_text_note, logged_at
                         FROM proc.ingest_act_log
                         WHERE job_id=%s {_FT_FILTERS[ftf]}
                         ORDER BY id DESC LIMIT %s""",
                      (job_id, ACT_LOG_PREVIEW))
            act_log = c.fetchall()

        # Tail the log file (last ~4 KB) without loading the whole thing.
        log_tail = ""
        if job["log_path"] and os.path.exists(job["log_path"]):
            try:
                with open(job["log_path"], "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 4096))
                    log_tail = f.read().decode("utf-8", "replace")
            except Exception:
                pass

        return templates.TemplateResponse(
            request, "admin_job.html",
            {"job": job, "windows": windows, "counts": counts,
             "log_tail": log_tail,
             "alive": pid_alive(job["pid"]),
             "is_active": job["status"] == "running",
             "act_log": act_log, "act_actions": act_actions,
             "act_log_total": act_log_total, "act_ft_yes": act_ft_yes,
             "act_ft_garbled": act_ft_garbled, "ftf": ftf,
             "act_log_preview": ACT_LOG_PREVIEW},
        )

    @router.get("/jobs/{job_id}/acts.csv")
    def admin_job_acts_csv(job_id: int, ftf: str = Query("all")):
        """Download the per-act log for a run as CSV — for documentation and
        offline review. Uncapped; honours the same full-text filter (ftf) as the
        page so a filtered view exports the matching rows."""
        if ftf not in _FT_FILTERS:
            ftf = "all"
        import csv as _csv
        import io as _io
        with cursor() as c:
            c.execute(f"""SELECT logged_at, adam, act_type, action,
                                full_text_extracted, full_text_chars,
                                full_text_note, title
                         FROM proc.ingest_act_log
                         WHERE job_id=%s {_FT_FILTERS[ftf]}
                         ORDER BY id""", (job_id,))
            rows = c.fetchall()
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["logged_at", "adam", "act_type", "action",
                    "full_text_extracted", "full_text_chars",
                    "full_text_note", "title"])
        for r in rows:
            w.writerow([r["logged_at"], r["adam"], r["act_type"], r["action"],
                        r["full_text_extracted"], r["full_text_chars"],
                        r["full_text_note"], r["title"]])
        return Response(
            content=buf.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="job-{job_id}-acts.csv"'})

    @router.post("/jobs/{job_id}/cancel")
    def admin_cancel_job(job_id: int):
        with cursor() as c:
            c.execute("""SELECT id, pid, status FROM proc.ingest_job
                         WHERE id=%s""", (job_id,))
            job = c.fetchone()
            if not job:
                raise HTTPException(404, f"job {job_id} not found")
            if job["status"] != "running":
                # Nothing to cancel; idempotent redirect.
                return RedirectResponse(url=f"/admin/jobs/{job_id}", status_code=303)

            pid = job["pid"]
            killed = False
            if pid and pid_alive(pid):
                try:
                    # SIGTERM to the whole process group (we used
                    # start_new_session, so the leader = the subprocess).
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    killed = True
                except ProcessLookupError:
                    pass
                except Exception as e:
                    raise HTTPException(500, f"failed to signal pid {pid}: {e!r}")

            c.execute("""UPDATE proc.ingest_job
                         SET status='cancelled', finished_at=now(),
                             last_error=%s
                         WHERE id=%s""",
                      ('cancelled via UI' if killed else 'PID already gone', job_id))
        return RedirectResponse(url=f"/admin/jobs/{job_id}", status_code=303)

    # ------------------------------------------------------------------ #
    # Annotation / curation layer.
    # An overlay of team notes/tags/flags keyed by ADAM. NEVER mutates the
    # harvested procurement_act data. Each save appends a new row and marks
    # prior rows for that ADAM superseded, giving a built-in audit trail.
    # ------------------------------------------------------------------ #
    FLAGS = ["", "verified", "review", "suspicious"]
    FLAG_LABELS = {
        "verified":   "Επαληθευμένο",
        "review":     "Προς έλεγχο",
        "suspicious": "Ύποπτο",
    }

    def _acts_filter(q="", external_id="", reference="", data_source="",
                     origin="", type="", source_status="", has_attachments="",
                     date_from="", date_to="", cpv="", cat=None):
        """Build the WHERE for the acts-management list from the filter params.
        Returns (where_sql, args, human_description). Shared by the list page and
        the mass table-extraction launcher so both target the same set."""
        where = ["TRUE"]
        args: list = []
        desc: list = []
        q = q.strip()
        if q:
            where.append("(translate(proc.f_unaccent(lower(a.title)),'ς','σ') "
                         "LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ') "
                         "OR a.adam ILIKE %s)")
            args += [f"%{q}%", f"%{q}%"]; desc.append(f"αναζήτηση «{q}»")
        if external_id.strip():
            where.append("a.external_id ILIKE %s")
            args.append(f"%{external_id.strip()}%"); desc.append("external id")
        if reference.strip():
            where.append("(a.reference_number ILIKE %s OR a.authority_reference ILIKE %s)")
            args += [f"%{reference.strip()}%", f"%{reference.strip()}%"]; desc.append("αναφορά")
        if data_source.strip():
            where.append("a.data_source = %s")
            args.append(data_source.strip()); desc.append(f"πηγή={data_source.strip()}")
        if origin in ("import", "authored"):
            where.append("a.origin = %s")
            args.append(origin); desc.append(origin)
        if type.strip():
            where.append("a.type = %s")
            args.append(type.strip()); desc.append(f"τύπος={type.strip()}")
        if source_status.strip():
            where.append("a.source_status = %s")
            args.append(source_status.strip()); desc.append(f"κατάσταση={source_status.strip()}")
        if has_attachments == "1":
            where.append("a.has_attachments IS TRUE"); desc.append("με συνημμένα")
        elif has_attachments == "0":
            where.append("(a.has_attachments IS NOT TRUE)"); desc.append("χωρίς συνημμένα")
        if date_from.strip():
            where.append("a.submission_date >= %s")
            args.append(date_from.strip()); desc.append(f"από {date_from.strip()}")
        if date_to.strip():
            where.append("a.submission_date <= %s")
            args.append(date_to.strip()); desc.append(f"έως {date_to.strip()}")
        # CPV — space-separated prefixes, OR'd; an act matches if any of its line
        # items has a CPV starting with any prefix. Mirrors the public search
        # filter (main.py) so both pages target the same set.
        cpvs = cpv.split()
        if cpvs:
            like_terms = " OR ".join(["oc.cpv_code LIKE %s"] * len(cpvs))
            where.append(f"""EXISTS (
                SELECT 1 FROM proc.act_object_detail od
                JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                WHERE od.adam = a.adam AND ({like_terms})
            )""")
            for cv in cpvs:
                args.append(f"{cv}%")
            desc.append("CPV " + " ".join(cpvs))
        # Category / subcategory — derived from CPV via cpv_category_map. Values
        # are "c:<id>" (whole category) or "s:<id>" (subcategory); they OR
        # together. Mirrors the public search filter (main.py) so both pages
        # target the same set.
        cat_vals = cat or []
        if cat_vals:
            cat_ids = [int(v[2:]) for v in cat_vals if v.startswith("c:") and v[2:].isdigit()]
            sub_ids = [int(v[2:]) for v in cat_vals if v.startswith("s:") and v[2:].isdigit()]
            conds = []
            if cat_ids:
                conds.append("m.category_id = ANY(%s)")
            if sub_ids:
                conds.append("m.subcategory_id = ANY(%s)")
            if conds:
                where.append(f"""EXISTS (
                    SELECT 1 FROM proc.act_object_detail od
                    JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                    JOIN proc.cpv_category_map m ON m.cpv_code = oc.cpv_code
                    WHERE od.adam = a.adam AND ({" OR ".join(conds)})
                )""")
                if cat_ids:
                    args.append(cat_ids)
                if sub_ids:
                    args.append(sub_ids)
                desc.append("κατηγορία")
        return " AND ".join(where), args, " · ".join(desc) or "όλες οι πράξεις"

    @router.get("/acts", response_class=HTMLResponse)
    def acts_manage(request: Request,
                    q: str = Query(""),
                    external_id: str = Query(""),
                    reference: str = Query(""),
                    data_source: str = Query(""),
                    origin: str = Query(""),
                    type: str = Query(""),
                    source_status: str = Query(""),
                    has_attachments: str = Query(""),
                    date_from: str = Query(""),
                    date_to: str = Query(""),
                    cpv: str = Query(""),
                    cat: list[str] = Query(default=[]),
                    sort: str = Query("recent"),
                    page: int = Query(1, ge=1)):
        """Data-management list: browse/filter ALL acts (imported + authored),
        with a link to edit each. The entry point for the management tool."""
        per_page = 50
        offset = (page - 1) * per_page
        _reconcile_table_jobs()
        where_sql, args, _ = _acts_filter(
            q, external_id, reference, data_source, origin, type,
            source_status, has_attachments, date_from, date_to, cpv, cat)

        order = {"recent": "a.submission_date DESC NULLS LAST",
                 "oldest": "a.submission_date ASC NULLS LAST",
                 "title": "a.title ASC",
                 "edited": "a.last_edited_at DESC NULLS LAST"}.get(
                     sort, "a.submission_date DESC NULLS LAST")

        with cursor() as c:
            c.execute(f"SELECT count(*) AS n FROM proc.procurement_act a WHERE {where_sql}", args)
            total = c.fetchone()["n"]
            c.execute(f"""
                SELECT a.adam, a.type, a.title, a.origin, a.data_source,
                       a.external_id, a.reference_number, a.source_status,
                       a.submission_date, a.total_cost_with_vat,
                       a.has_attachments, a.last_edited_by, a.last_edited_at,
                       auth.name AS authority_name
                FROM proc.procurement_act a
                LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                WHERE {where_sql}
                ORDER BY {order}
                LIMIT %s OFFSET %s
            """, args + [per_page, offset])
            rows = c.fetchall()
            # distinct data sources for the filter dropdown
            c.execute("""SELECT DISTINCT data_source FROM proc.procurement_act
                         WHERE data_source IS NOT NULL ORDER BY data_source""")
            sources = [r["data_source"] for r in c.fetchall()]
            # distinct source statuses present
            c.execute("""SELECT DISTINCT source_status FROM proc.procurement_act
                         WHERE source_status IS NOT NULL AND source_status <> ''
                         ORDER BY source_status""")
            statuses = [r["source_status"] for r in c.fetchall()]
            # Category taxonomy for the two-level filter dropdown (derived from
            # CPV via cpv_category_map). Categories carry their subcategories so
            # the template renders one grouped multi-select with optgroups.
            c.execute("SELECT id, name, name_en FROM proc.tender_category ORDER BY name")
            categories = [{"id": r["id"], "name": r["name"], "name_en": r["name_en"], "subs": []}
                          for r in c.fetchall()]
            cat_by_id = {ct["id"]: ct for ct in categories}
            c.execute("""SELECT id, name, name_en, parent_category_id
                         FROM proc.tender_subcategory ORDER BY name""")
            for r in c.fetchall():
                parent = cat_by_id.get(r["parent_category_id"])
                if parent is not None:
                    parent["subs"].append({"id": r["id"], "name": r["name"], "name_en": r["name_en"]})
            # Recent mass table-extraction jobs (Phase 1).
            c.execute("""SELECT id, status, filter_desc, total_acts,
                                started_at, finished_at
                         FROM proc.table_extract_job ORDER BY id DESC LIMIT 8""")
            table_jobs = c.fetchall()

        total_pages = max(1, (total + per_page - 1) // per_page)
        return templates.TemplateResponse(
            request, "admin_acts.html",
            {"rows": rows, "total": total, "page": page, "total_pages": total_pages,
             "q": q, "external_id": external_id, "reference": reference,
             "data_source": data_source, "origin": origin, "type": type,
             "source_status": source_status, "has_attachments": has_attachments,
             "date_from": date_from, "date_to": date_to, "cpv": cpv, "sort": sort,
             "cat": cat, "categories": categories,
             "sources": sources, "statuses": statuses, "admin_tab": "acts",
             "table_jobs": table_jobs,
             "max_table_extract": MAX_TABLE_EXTRACT,
             "max_table_extract_save": MAX_TABLE_EXTRACT_SAVE})

    # ------------------------------------------------------------------ #
    # Mass table extraction over a filtered act set (Phase 1: report-only).
    # Self-contained job system in proc.table_extract_*; mirrors the backfill
    # job pattern (detached subprocess, per-act log, lifecycle).
    # ------------------------------------------------------------------ #
    # Per-job caps. Saving tables hits the doc server AND writes rows, so it is
    # capped tighter than a report-only run. Both overridable via env.
    MAX_TABLE_EXTRACT = int(os.environ.get("MAX_TABLE_EXTRACT", "500"))
    MAX_TABLE_EXTRACT_SAVE = int(os.environ.get("MAX_TABLE_EXTRACT_SAVE", "100"))
    _TABLE_OUTCOMES = ("extracted", "garbled", "needs_ocr",
                       "no_tables", "no_attachment", "error")
    _TOF = {
        "all": "",
        "extracted": "AND outcome='extracted'",
        "garbled": "AND outcome='garbled'",
        "needs_ocr": "AND outcome='needs_ocr'",
        "no_tables": "AND outcome='no_tables'",
        "failed": "AND outcome IN ('no_attachment','error')",
    }

    def _reconcile_table_jobs():
        with cursor() as c:
            c.execute("SELECT id, pid FROM proc.table_extract_job WHERE status='running'")
            rows = c.fetchall()
            for r in rows:
                if pid_alive(r["pid"]):
                    continue
                c.execute("""SELECT count(*) FILTER (WHERE NOT done) AS pending,
                                    count(*) AS total
                             FROM proc.table_extract_target WHERE job_id=%s""", (r["id"],))
                t = c.fetchone()
                new = "done" if (t["total"] and not t["pending"]) else "stale"
                c.execute("""UPDATE proc.table_extract_job SET status=%s, finished_at=now()
                             WHERE id=%s AND status='running'""", (new, r["id"]))

    @router.post("/extract-tables")
    def extract_tables_launch(request: Request,
                              q: str = Form(""), external_id: str = Form(""),
                              reference: str = Form(""), data_source: str = Form(""),
                              origin: str = Form(""), type: str = Form(""),
                              source_status: str = Form(""), has_attachments: str = Form(""),
                              date_from: str = Form(""), date_to: str = Form(""),
                              cpv: str = Form(""), cat: list[str] = Form(default=[]),
                              save_tables: str = Form("")):
        where_sql, args, desc = _acts_filter(
            q, external_id, reference, data_source, origin, type,
            source_status, has_attachments, date_from, date_to, cpv, cat)
        save = save_tables == "1"
        if save:
            desc += " · +αποθήκευση πινάκων"
        _reconcile_table_jobs()
        with cursor() as c:
            c.execute("SELECT id, pid FROM proc.table_extract_job WHERE status='running'")
            for r in c.fetchall():
                if pid_alive(r["pid"]):
                    raise HTTPException(409, "Εκτελείται ήδη μαζική εξαγωγή πινάκων· "
                                             "περιμένετε ή ακυρώστε την.")
            c.execute(f"SELECT count(*) AS n FROM proc.procurement_act a WHERE {where_sql}", args)
            total = c.fetchone()["n"]
            if total == 0:
                raise HTTPException(400, "Καμία πράξη δεν ταιριάζει στο φίλτρο.")
            cap = MAX_TABLE_EXTRACT_SAVE if save else MAX_TABLE_EXTRACT
            if total > cap:
                raise HTTPException(400, f"Το φίλτρο επιστρέφει {total} πράξεις "
                    f"(όριο {cap}{' με αποθήκευση' if save else ''}). "
                    f"Περιορίστε το φίλτρο{' ή τρέξτε χωρίς αποθήκευση' if save else ''}.")
            c.execute("""INSERT INTO proc.table_extract_job
                           (status, filter_desc, total_acts, save_tables)
                         VALUES ('running', %s, %s, %s) RETURNING id""",
                      (desc, total, save))
            job_id = c.fetchone()["id"]
            c.execute(f"""INSERT INTO proc.table_extract_target (job_id, adam, ord)
                          SELECT %s, a.adam,
                                 (row_number() OVER (ORDER BY a.submission_date DESC NULLS LAST))::int - 1
                          FROM proc.procurement_act a WHERE {where_sql}""",
                      [job_id] + args)

        root = project_root()
        log_path = os.path.join(LOG_DIR, f"table-job-{job_id}.log")
        cmd = [sys.executable, os.path.join(root, "db.py"),
               "extract-tables", "--job", str(job_id)]
        log_fh = open(log_path, "w")
        log_fh.write(f"# table extraction job {job_id}: {desc} ({total} acts)\n\n")
        log_fh.flush()
        try:
            proc = subprocess.Popen(
                cmd, stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True, cwd=root, env={**os.environ})
        except Exception as e:
            with cursor() as c:
                c.execute("""UPDATE proc.table_extract_job SET status='error',
                             last_error=%s, finished_at=now() WHERE id=%s""",
                          (f"spawn failed: {e!r}", job_id))
            log_fh.close()
            raise HTTPException(500, f"failed to launch subprocess: {e!r}")
        with cursor() as c:
            c.execute("UPDATE proc.table_extract_job SET pid=%s, log_path=%s WHERE id=%s",
                      (proc.pid, log_path, job_id))
        return RedirectResponse(url=f"/admin/table-jobs/{job_id}", status_code=303)

    @router.get("/table-jobs/{job_id}", response_class=HTMLResponse)
    def table_job_detail(job_id: int, request: Request, tof: str = Query("all")):
        if tof not in _TOF:
            tof = "all"
        _reconcile_table_jobs()
        with cursor() as c:
            c.execute("SELECT * FROM proc.table_extract_job WHERE id=%s", (job_id,))
            job = c.fetchone()
            if not job:
                raise HTTPException(404, f"table job {job_id} not found")
            c.execute("""SELECT outcome, count(*) AS n,
                                coalesce(sum(n_tables),0) AS tabs,
                                coalesce(sum(n_saved),0) AS saved
                         FROM proc.table_extract_log WHERE job_id=%s GROUP BY outcome""",
                      (job_id,))
            by = {r["outcome"]: r for r in c.fetchall()}
            counts = {o: (by[o]["n"] if o in by else 0) for o in _TABLE_OUTCOMES}
            total_logged = sum(counts.values())
            total_tables = sum(by[o]["tabs"] for o in by)
            total_saved = sum(by[o]["saved"] for o in by)
            c.execute(f"""SELECT adam, act_type, title, outcome, n_tables, n_saved,
                                 n_files, note, logged_at
                          FROM proc.table_extract_log
                          WHERE job_id=%s {_TOF[tof]}
                          ORDER BY id DESC LIMIT %s""", (job_id, ACT_LOG_PREVIEW))
            log = c.fetchall()
        log_tail = ""
        if job["log_path"] and os.path.exists(job["log_path"]):
            try:
                with open(job["log_path"], "rb") as f:
                    f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 4096))
                    log_tail = f.read().decode("utf-8", "replace")
            except Exception:
                pass
        return templates.TemplateResponse(
            request, "admin_table_job.html",
            {"job": job, "counts": counts, "total_logged": total_logged,
             "total_tables": total_tables, "total_saved": total_saved,
             "log": log, "tof": tof,
             "alive": pid_alive(job["pid"]), "is_active": job["status"] == "running",
             "act_log_preview": ACT_LOG_PREVIEW, "log_tail": log_tail,
             "outcomes": _TABLE_OUTCOMES, "admin_tab": "acts"})

    @router.get("/table-jobs/{job_id}/acts.csv")
    def table_job_csv(job_id: int, tof: str = Query("all")):
        if tof not in _TOF:
            tof = "all"
        import csv as _csv
        import io as _io
        with cursor() as c:
            c.execute(f"""SELECT logged_at, adam, act_type, outcome, n_tables,
                                 n_saved, n_files, note, title
                          FROM proc.table_extract_log
                          WHERE job_id=%s {_TOF[tof]} ORDER BY id""", (job_id,))
            rows = c.fetchall()
        buf = _io.StringIO(); w = _csv.writer(buf)
        w.writerow(["logged_at", "adam", "act_type", "outcome", "n_tables",
                    "n_saved", "n_files", "note", "title"])
        for r in rows:
            w.writerow([r["logged_at"], r["adam"], r["act_type"], r["outcome"],
                        r["n_tables"], r["n_saved"], r["n_files"], r["note"], r["title"]])
        return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 f'attachment; filename="table-job-{job_id}.csv"'})

    @router.post("/table-jobs/{job_id}/cancel")
    def table_job_cancel(job_id: int):
        with cursor() as c:
            c.execute("SELECT pid, status FROM proc.table_extract_job WHERE id=%s", (job_id,))
            job = c.fetchone()
            if not job:
                raise HTTPException(404, f"table job {job_id} not found")
            if job["status"] == "running":
                pid = job["pid"]
                if pid and pid_alive(pid):
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                    except Exception:
                        pass
                c.execute("""UPDATE proc.table_extract_job
                             SET status='cancelled', finished_at=now()
                             WHERE id=%s""", (job_id,))
        return RedirectResponse(url=f"/admin/table-jobs/{job_id}", status_code=303)

    # ----- Authored-act edit / create form ---------------------------------- #
    # The fields a curator may set on an AUTHORED act, grouped for the form.
    # (Imported acts are NOT edited here — they keep the overlay-correction
    # tools. The form/save routes below refuse non-authored acts.)
    def _act_form_fields():
        # (name, label, kind) — kind drives the input type in the template.
        return {
            "Ταυτότητα & πηγή": [
                ("title", "Τίτλος", "textarea"),
                ("short_description", "Σύντομη περιγραφή", "textarea"),
                ("external_id", "External ID", "text"),
                ("reference_number", "Αριθμός αναφοράς", "text"),
                ("authority_reference", "Αναφορά αρχής", "text"),
                ("data_source", "Πηγή δεδομένων", "text"),
                ("source_url", "Σύνδεσμος πηγής", "text"),
                ("source_status", "Κατάσταση πηγής", "text"),
                ("language", "Γλώσσα", "text"),
            ],
            "Κατηγοριοποίηση": [
                ("type", "Τύπος πράξης", "type"),
                ("nature_of_contract", "Είδος σύμβασης (πηγής)", "text"),
                ("type_of_document", "Τύπος εγγράφου", "text"),
                ("subtype_of_document", "Υποτύπος εγγράφου", "text"),
                ("procedure_label", "Διαδικασία", "text"),
                ("regulation_of_procurement", "Κανονισμός", "text"),
                ("e_auction", "Ηλεκτρονικός πλειστηριασμός", "text"),
                ("dynamic_purchasing_system", "ΔΣΑ (DPS)", "text"),
                ("lot_number", "Αριθμός τμήματος", "text"),
                ("divided_into_lots", "Διαίρεση σε τμήματα", "bool"),
                ("is_framework_agreement", "Συμφωνία-πλαίσιο", "bool"),
                ("type_of_bid_required", "Τύπος απαιτούμενης προσφοράς", "text"),
                ("alternative_offers_allowed", "Εναλλακτικές προσφορές", "bool"),
            ],
            "Προσφορές & παράταση": [
                ("number_of_offers", "Αριθμός προσφορών", "number"),
                ("prolongation_option", "Δικαίωμα παράτασης", "bool"),
                ("prolongation_in_months", "Παράταση (μήνες)", "number"),
            ],
            "Ημερομηνίες": [
                ("signed_date", "Ημ. υπογραφής", "date"),
                ("submission_date", "Ημ. δημοσίευσης", "date"),
                ("final_submission_date", "Λήξη υποβολής", "date"),
                ("start_date", "Έναρξη", "date"),
                ("end_date", "Λήξη", "date"),
            ],
            "Οικονομικά": [
                ("budget", "Προϋπολογισμός", "number"),
                ("total_cost_without_vat", "Αξία χωρίς ΦΠΑ", "number"),
                ("total_cost_with_vat", "Αξία με ΦΠΑ", "number"),
                ("currency_code", "Νόμισμα", "text"),
                ("vat_rate", "Συντελεστής ΦΠΑ (%)", "number"),
                ("vat_included", "Περιλαμβάνεται ΦΠΑ", "bool"),
                ("value_eur", "Αξία σε EUR", "number"),
                ("value_usd", "Αξία σε USD", "number"),
                ("estimated_price_min", "Εκτιμώμενη τιμή (ελάχ.)", "number"),
                ("estimated_price_max", "Εκτιμώμενη τιμή (μέγ.)", "number"),
                ("yearly_budget", "Ετήσιος προϋπολογισμός", "number"),
                ("bid_bond_amount", "Εγγύηση συμμετοχής", "number"),
                ("price_weighting", "Βαρύτητα τιμής (%)", "number"),
            ],
            "Επιλεξιμότητα": [
                ("eligibility_criteria", "Κριτήρια καταλληλότητας", "textarea"),
                ("eligibility_category", "Κατηγορία καταλληλότητας", "text"),
            ],
            "Αναφορές & πλατφόρμα": [
                ("journal_number", "Αριθμός δημοσίευσης (Journal)", "text"),
                ("eprocurement_portal", "Πλατφόρμα ηλ. προμηθειών", "text"),
            ],
            "Γεωγραφία": [
                ("nuts_code", "Κωδικός NUTS", "nuts"),
                ("city", "Πόλη", "text"),
                ("postal_code", "Τ.Κ.", "postal"),
                ("country", "Χώρα", "country"),
            ],
            "Επικοινωνία": [
                ("contact_email", "Email επικοινωνίας", "text"),
                ("contact_phone", "Τηλέφωνο", "text"),
                ("contact_fax", "Φαξ", "text"),
                ("street_address", "Διεύθυνση (οδός)", "text"),
                ("contact_url", "Ιστότοπος", "text"),
            ],
            "Αναθέτουσα αρχή": [
                ("authority_id", "ΑΦΜ/κωδικός αρχής", "authority"),
            ],
            "Επισημάνσεις": [
                ("cancelled", "Ακυρωμένη", "bool"),
                ("is_modified", "Ορθή επανάληψη", "bool"),
                ("has_attachments", "Έχει συνημμένα", "bool"),
            ],
        }

    # Flat name→kind map and ordered name list, derived from the groups.
    def _field_kinds():
        kinds = {}
        for grp in _act_form_fields().values():
            for name, _label, kind in grp:
                kinds[name] = kind
        return kinds

    @router.get("/acts/new", response_class=HTMLResponse)
    def act_create_form(request: Request):
        """Blank form to author a new act from scratch."""
        ctx = {"groups": _act_form_fields(), "act": {}, "mode": "create",
               "adam": None, "authority_name": None,
               "full_text": "", "full_text_html": ""}
        ctx.update(_ocr_flags())
        return templates.TemplateResponse(request, "admin_act_form.html", ctx)

    @router.get("/acts/{adam}/edit")
    def act_edit_form(adam: str):
        """Editing an existing act now lives in the unified act-edit hub
        (Βασικά πεδία tab); redirect there so there's one edit page per act."""
        return RedirectResponse(url=f"/admin/act/{adam}/edit?tab=fields",
                                status_code=303)

    @router.post("/acts/save")
    async def act_save(request: Request):
        """Insert (create) or update (edit) an AUTHORED act. Shared by both
        forms. Never touches imported acts."""
        form = await request.form()
        mode = form.get("_mode", "create")
        kinds = _field_kinds()

        # Build a {column: value} dict, coercing by field kind.
        data = {}
        for name, kind in kinds.items():
            raw = (form.get(name) or "").strip()
            if kind == "bool":
                data[name] = (name in form)  # checkbox present => True
            elif kind in ("number",):
                # Accept any pasted/typed money format and store the DB-ready
                # numeric string (safety net behind the form's paste cleaner).
                data[name] = None if raw == "" else _clean_number(raw)
            elif kind in ("date",):
                data[name] = None if raw == "" else raw
            else:
                data[name] = None if raw == "" else raw

        # Act-level CPV codes — multi-value, outside the scalar field map.
        cpv_codes = []
        for code in form.getlist("cpv_code"):
            code = (code or "").strip()
            if code and code not in cpv_codes:
                cpv_codes.append(code)

        def _save_cpv(c, adam):
            c.execute("DELETE FROM proc.act_cpv WHERE adam=%s", (adam,))
            for i, code in enumerate(cpv_codes):
                c.execute("INSERT INTO proc.act_cpv (adam, cpv_code, ord) "
                          "VALUES (%s,%s,%s)", (adam, code, i))

        # Place(s) of performance — multi-value NUTS, outside the scalar field
        # map. The first/primary code is mirrored into procurement_act.nuts_code
        # automatically (data["nuts_code"] = form.get("nuts_code") = first).
        nuts_codes = []
        for code in form.getlist("nuts_code"):
            code = (code or "").strip()
            if code and code not in nuts_codes:
                nuts_codes.append(code)

        def _save_nuts(c, adam):
            c.execute("DELETE FROM proc.act_nuts WHERE adam=%s", (adam,))
            for code in nuts_codes:
                c.execute("INSERT INTO proc.act_nuts (adam, nuts_code) "
                          "VALUES (%s,%s) ON CONFLICT DO NOTHING", (adam, code))

        curator = unquote(request.cookies.get("curator", "")) or "curator"

        if mode == "edit":
            adam = (form.get("_adam") or "").strip()
            if not adam:
                raise HTTPException(400, "missing adam")
            with cursor() as c:
                c.execute("SELECT origin FROM proc.procurement_act WHERE adam = %s",
                          (adam,))
                row = c.fetchone()
                if not row:
                    raise HTTPException(404, "act not found")
                if row["origin"] != "authored":
                    raise HTTPException(403, "imported acts are not editable here")
                set_parts = [f"{c2} = %s" for c2 in data.keys()]
                vals = [data[c2] for c2 in data.keys()]
                # Full text now saves in the SAME submit as the fields (the unified
                # split-view form always posts the hidden inputs). Only touch it
                # when the form carried it, so other callers are unaffected.
                if "full_text" in form:
                    try:
                        from app.tables import sanitize_full_text_html
                    except ImportError:
                        from tables import sanitize_full_text_html
                    ft = (form.get("full_text") or "").strip()
                    set_parts += ["full_text = %s", "full_text_html = %s",
                                  "full_text_source = %s",
                                  "full_text_extracted_at = now()"]
                    vals += [ft or None,
                             sanitize_full_text_html(form.get("full_text_html") or "") or None,
                             f"manual:{adam}"]
                set_sql = ", ".join(set_parts)
                c.execute(
                    f"""UPDATE proc.procurement_act
                        SET {set_sql}, last_edited_by = %s, last_edited_at = now()
                        WHERE adam = %s""",
                    vals + [curator, adam])
                _save_cpv(c, adam)
                _save_nuts(c, adam)
            return RedirectResponse(url=f"/admin/act/{adam}/edit?tab=fields&saved=1",
                                    status_code=303)
        else:
            # CREATE — generate an adam if none supplied. Authored acts get a
            # MANUAL-prefixed id unless the curator typed an external/own adam.
            adam = (form.get("adam") or "").strip()
            if not adam:
                import uuid
                adam = "MANUAL-" + uuid.uuid4().hex[:12].upper()
            # data_source: keep what the form set, else 'manual'
            ds = data.get("data_source") or "manual"
            base_vals = [adam, "authored", ds, curator, curator, dt.datetime.now()]
            # avoid double-setting data_source (it's in data too)
            field_cols = [c2 for c2 in data.keys() if c2 != "data_source"]
            all_cols = ["adam", "origin", "data_source", "authored_by",
                        "last_edited_by", "last_edited_at"] + field_cols
            all_vals = base_vals + [data[c2] for c2 in field_cols]
            # Full text pasted on the create form. It's columns on the act row,
            # so it saves in this same INSERT (no need to create the act first).
            # Mirrors /tables/fulltext/save: plain text + sanitised HTML.
            ft = (form.get("full_text") or "").strip()
            if ft:
                try:
                    from app.tables import sanitize_full_text_html
                except ImportError:
                    from tables import sanitize_full_text_html
                all_cols += ["full_text", "full_text_html",
                             "full_text_source", "full_text_extracted_at"]
                all_vals += [ft,
                             sanitize_full_text_html(form.get("full_text_html") or ""),
                             f"manual:{adam}", dt.datetime.now()]
            placeholders = ", ".join(["%s"] * len(all_cols))
            with cursor() as c:
                c.execute("SELECT 1 FROM proc.procurement_act WHERE adam = %s", (adam,))
                if c.fetchone():
                    raise HTTPException(409, f"act {adam} already exists")
                c.execute(
                    f"""INSERT INTO proc.procurement_act ({", ".join(all_cols)})
                        VALUES ({placeholders})""",
                    all_vals)
                _save_cpv(c, adam)
                _save_nuts(c, adam)
            return RedirectResponse(url=f"/admin/act/{adam}/edit?tab=fields&saved=1",
                                    status_code=303)

    @router.post("/acts/{adam}/take-ownership")
    def act_take_ownership(adam: str, request: Request):
        """Convert an IMPORTED act into an AUTHORED one, so its core fields become
        fully editable and re-import will never touch it again. One-way action."""
        curator = unquote(request.cookies.get("curator", "")) or "curator"
        with cursor() as c:
            c.execute("SELECT origin FROM proc.procurement_act WHERE adam = %s", (adam,))
            row = c.fetchone()
            if not row:
                raise HTTPException(404, "act not found")
            if row["origin"] == "authored":
                # already owned — just go to the edit hub's fields tab
                return RedirectResponse(url=f"/admin/act/{adam}/edit?tab=fields",
                                        status_code=303)
            c.execute(
                """UPDATE proc.procurement_act
                   SET origin = 'authored',
                       authored_by = COALESCE(authored_by, %s),
                       last_edited_by = %s,
                       last_edited_at = now()
                   WHERE adam = %s AND origin = 'import'""",
                (curator, curator, adam))
        return RedirectResponse(url=f"/admin/act/{adam}/edit?tab=fields&owned=1",
                                status_code=303)

    # ------------------------------------------------------------------ #
    # Party (authority / contractor) edit forms. Parties are harvested, so
    # there is no "create" — only edit of descriptive / identity / contact
    # fields. These columns are never written by the import upserts, so a
    # curator's values survive re-imports. The NAME keeps its own inline editor
    # on the detail page; the keys (org_id / vat) are not editable here.
    # ------------------------------------------------------------------ #
    def _authority_form_fields():
        return {
            "Ταυτότητα": [
                ("identifier", "Αναγνωριστικό πηγής", "text"),
                ("orgdb_id", "OrgDB ID", "text"),
            ],
            "Κατηγοριοποίηση": [
                ("type_code", "Κωδικός τύπου", "text"),
                ("classification_code", "Κωδικός κατηγορίας", "text"),
                ("aaht", "ΑΑΗΤ", "text"),
            ],
            "Τοποθεσία": [
                ("nuts_code", "Κωδικός NUTS", "text"),
                ("city", "Πόλη", "text"),
                ("postal_code", "Τ.Κ.", "text"),
                ("country", "Χώρα", "text"),
                ("street_address", "Διεύθυνση (οδός)", "text"),
            ],
            "Επικοινωνία": [
                ("contact_email", "Email", "text"),
                ("contact_phone", "Τηλέφωνο", "text"),
                ("contact_fax", "Φαξ", "text"),
                ("contact_url", "Ιστότοπος", "text"),
            ],
        }

    def _contractor_form_fields():
        return {
            "Ταυτότητα": [
                ("statistical_or_tax_number", "Στατιστικός/φορολογικός αρ.", "text"),
                ("contact_person", "Υπεύθυνος επικοινωνίας", "text"),
                ("orgdb_id", "OrgDB ID", "text"),
                ("ar_gemi", "Αρ. ΓΕΜΗ", "text"),
            ],
            "Στοιχεία": [
                ("is_greek_vat", "Ελληνικό ΑΦΜ", "bool"),
                ("country", "Χώρα", "text"),
            ],
            "Τοποθεσία": [
                ("nuts_code", "Κωδικός NUTS", "text"),
                ("city", "Πόλη", "text"),
                ("postal_code", "Τ.Κ.", "text"),
                ("street_address", "Διεύθυνση (οδός)", "text"),
            ],
            "Επικοινωνία": [
                ("contact_email", "Email", "text"),
                ("contact_phone", "Τηλέφωνο", "text"),
                ("contact_fax", "Φαξ", "text"),
                ("contact_url", "Ιστότοπος", "text"),
            ],
        }

    def _party_coerce(groups, form):
        """Build {column: value} from a submitted party form, by field kind."""
        data = {}
        for grp in groups.values():
            for name, _label, kind in grp:
                if kind == "bool":
                    data[name] = (name in form)
                else:
                    data[name] = (form.get(name) or "").strip() or None
        return data

    @router.get("/authority/{org_id}/edit", response_class=HTMLResponse)
    def authority_edit_form(org_id: str, request: Request):
        with cursor() as c:
            c.execute("SELECT * FROM proc.authority WHERE org_id=%s", (org_id,))
            party = c.fetchone()
        if not party:
            raise HTTPException(404, "authority not found")
        return templates.TemplateResponse(
            request, "admin_party_form.html",
            {"groups": _authority_form_fields(), "party": dict(party),
             "kind": "authority", "kind_label": "Αναθέτουσα αρχή", "ident": org_id,
             "name": party["name"], "back_url": f"/authority/{org_id}",
             "action_url": f"/admin/authority/{org_id}/save"})

    @router.post("/authority/{org_id}/save")
    async def authority_save(org_id: str, request: Request):
        form = await request.form()
        data = _party_coerce(_authority_form_fields(), form)
        with cursor() as c:
            c.execute("SELECT 1 FROM proc.authority WHERE org_id=%s", (org_id,))
            if not c.fetchone():
                raise HTTPException(404, "authority not found")
            cols = list(data.keys())
            set_sql = ", ".join(f"{col} = %s" for col in cols)
            c.execute(f"UPDATE proc.authority SET {set_sql} WHERE org_id = %s",
                      [data[col] for col in cols] + [org_id])
        return RedirectResponse(url=f"/authority/{org_id}", status_code=303)

    @router.get("/contractor/{vat}/edit", response_class=HTMLResponse)
    def contractor_edit_form(vat: str, request: Request):
        with cursor() as c:
            c.execute("SELECT * FROM proc.economic_operator WHERE vat_number=%s", (vat,))
            party = c.fetchone()
        if not party:
            raise HTTPException(404, "contractor not found")
        return templates.TemplateResponse(
            request, "admin_party_form.html",
            {"groups": _contractor_form_fields(), "party": dict(party),
             "kind": "contractor", "kind_label": "Ανάδοχος", "ident": vat,
             "name": party["name"], "back_url": f"/contractor/{vat}",
             "action_url": f"/admin/contractor/{vat}/save"})

    @router.post("/contractor/{vat}/save")
    async def contractor_save(vat: str, request: Request):
        form = await request.form()
        data = _party_coerce(_contractor_form_fields(), form)
        with cursor() as c:
            c.execute("SELECT 1 FROM proc.economic_operator WHERE vat_number=%s", (vat,))
            if not c.fetchone():
                raise HTTPException(404, "contractor not found")
            cols = list(data.keys())
            set_sql = ", ".join(f"{col} = %s" for col in cols)
            c.execute(f"UPDATE proc.economic_operator SET {set_sql} WHERE vat_number = %s",
                      [data[col] for col in cols] + [vat])
        return RedirectResponse(url=f"/contractor/{vat}", status_code=303)

    @router.get("/curate", response_class=HTMLResponse)
    def curate_search(request: Request,
                      q: str = Query(""),
                      flag: str = Query(""),
                      annotated: str = Query(""),
                      page: int = Query(1, ge=1)):
        """Search acts to curate. Can filter to only-annotated or by flag, or
        free-text on title/ADAM."""
        per_page = 25
        offset = (page - 1) * per_page
        where = ["TRUE"]
        args: list = []
        q = q.strip()
        if q:
            where.append("(translate(proc.f_unaccent(lower(a.title)),'ς','σ') "
                         "LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ') "
                         "OR a.adam ILIKE %s)")
            args.append(f"%{q}%")
            args.append(f"%{q}%")
        if flag in FLAG_LABELS:
            where.append("cur.flag = %s")
            args.append(flag)
        if annotated == "1":
            where.append("cur.adam IS NOT NULL")
        where_sql = " AND ".join(where)

        with cursor() as c:
            c.execute(f"""
                SELECT count(*) AS n
                FROM proc.procurement_act a
                LEFT JOIN proc.v_act_annotation_current cur ON cur.adam = a.adam
                WHERE {where_sql}
            """, args)
            total = c.fetchone()["n"]
            c.execute(f"""
                SELECT a.adam, a.type, a.title, a.signed_date, a.submission_date,
                       a.total_cost_with_vat,
                       cur.note, cur.tags, cur.flag, cur.author, cur.created_at
                FROM proc.procurement_act a
                LEFT JOIN proc.v_act_annotation_current cur ON cur.adam = a.adam
                WHERE {where_sql}
                ORDER BY (cur.adam IS NOT NULL) DESC,
                         a.submission_date DESC NULLS LAST
                LIMIT %s OFFSET %s
            """, args + [per_page, offset])
            rows = c.fetchall()

        total_pages = max(1, (total + per_page - 1) // per_page)
        return templates.TemplateResponse(
            request, "admin_curate.html",
            {"rows": rows, "q": q, "flag": flag, "annotated": annotated,
             "total": total, "page": page, "total_pages": total_pages,
             "flag_labels": FLAG_LABELS, "admin_tab": "curate"})

    @router.get("/act/{adam}/annotate")
    def annotate_form(adam: str):
        """The annotation editor now lives in the act-edit hub's "Σημειώσεις"
        tab; redirect this standalone URL there (the POST below stays — the hub
        panel posts to it and gets the refreshed panel back)."""
        return RedirectResponse(url=f"/admin/act/{adam}/edit?tab=annotate",
                                status_code=303)

    @router.post("/act/{adam}/annotate")
    def annotate_save(adam: str,
                      request: Request,
                      note: str = Form(""),
                      tags: str = Form(""),
                      flag: str = Form(""),
                      corrected_value: str = Form(""),
                      corrected_value_without_vat: str = Form(""),
                      author: str = Form("")):
        # Parse a user-typed money value (optional). Accepts comma or dot
        # decimals, ignores thousands separators and currency symbols. Returns a
        # rounded float, or None if blank/invalid/negative.
        def parse_money(s: str):
            s = (s or "").strip()
            if not s:
                return None
            cleaned = (s.replace("€", "").replace(" ", "")
                         .replace(".", "").replace(",", ".")
                       if s.count(",") == 1 and s.rfind(",") > s.rfind(".")
                       else s.replace("€", "").replace(" ", "").replace(",", ""))
            try:
                v = round(float(cleaned), 2)
                return v if v >= 0 else None
            except ValueError:
                return None

        with cursor() as c:
            c.execute("SELECT 1 FROM proc.procurement_act WHERE adam=%s", (adam,))
            if not c.fetchone():
                raise HTTPException(404, f"act {adam} not found")

            author = (author or "").strip() or "(anonymous)"
            note = (note or "").strip()
            flag = flag if flag in FLAG_LABELS else None
            tag_list = [t.strip() for t in tags.replace(",", " ").split() if t.strip()]

            corrected = parse_money(corrected_value)
            corrected_wo = parse_money(corrected_value_without_vat)

            # If everything is empty, treat as "clear annotation": just supersede.
            c.execute("""UPDATE proc.act_annotation SET superseded=true
                         WHERE adam=%s AND NOT superseded""", (adam,))
            if note or tag_list or flag or corrected is not None or corrected_wo is not None:
                c.execute("""INSERT INTO proc.act_annotation
                                 (adam, note, tags, flag, corrected_value,
                                  corrected_value_without_vat, author)
                             VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                          (adam, note or None, tag_list, flag, corrected,
                           corrected_wo, author))

        from urllib.parse import quote
        panel = request.query_params.get("panel") == "1"
        if panel:
            # Called from the act-edit hub via HTMX: re-render the annotate panel
            # in place (with the new history) instead of a full-page redirect.
            data = _annotate_context(adam, request)
            data["saved"] = True
            resp = templates.TemplateResponse(request, "_panel_annotate.html", data)
            resp.set_cookie("curator", quote(author),
                            max_age=60 * 60 * 24 * 365, samesite="lax")
            return resp

        resp = RedirectResponse(url=f"/admin/act/{adam}/edit?tab=annotate",
                                status_code=303)
        # Remember the curator's name for next time (attribution, not auth).
        # Cookies are latin-1 only, so URL-encode to allow Greek names; the form
        # route unquotes it when prefilling. quote() with no safe-set handles
        # any alphabet safely.
        resp.set_cookie("curator", quote(author), max_age=60 * 60 * 24 * 365,
                        samesite="lax")
        return resp

    # ------------------------------------------------------------------ #
    # Act-edit hub: one page per act hosting Σημειώσεις / Πλήρες κείμενο /
    # Πίνακες as lazy HTMX tabs. The standalone pages (annotate, /tables,
    # /tables/fulltext) keep working; the hub reuses their logic via these
    # panel fragments. New features become new tabs here.
    # ------------------------------------------------------------------ #
    def _annotate_context(adam: str, request: Request) -> dict:
        """Shared context for the annotate panel + standalone page."""
        with cursor() as c:
            c.execute("""SELECT adam, type, title, signed_date, submission_date,
                                total_cost_with_vat, total_cost_without_vat
                         FROM proc.procurement_act WHERE adam=%s""", (adam,))
            act = c.fetchone()
            if not act:
                raise HTTPException(404, f"act {adam} not found")
            c.execute("""SELECT note, tags, flag, author, created_at,
                                corrected_value, corrected_value_without_vat
                         FROM proc.v_act_annotation_current WHERE adam=%s""", (adam,))
            current = c.fetchone()
            c.execute("""SELECT note, tags, flag, author, created_at, superseded
                         FROM proc.act_annotation
                         WHERE adam=%s ORDER BY created_at DESC""", (adam,))
            history = c.fetchall()
        from urllib.parse import unquote
        author_cookie = unquote(request.cookies.get("curator", ""))
        return {"act": act, "current": current, "history": history,
                "author_cookie": author_cookie,
                "flags": FLAGS, "flag_labels": FLAG_LABELS}

    def _act_basic(adam: str):
        """Minimal act row for the fulltext/tables panels."""
        with cursor() as c:
            c.execute("""SELECT adam, type, title,
                                full_text, full_text_html,
                                full_text_extracted_at, full_text_source
                         FROM proc.procurement_act WHERE adam=%s""", (adam,))
            act = c.fetchone()
        if not act:
            raise HTTPException(404, f"act {adam} not found")
        return act

    _EDIT_TABS = ("fields", "annotate", "fulltext", "tables")

    @router.get("/act/{adam}/edit", response_class=HTMLResponse)
    def act_edit_hub(adam: str, request: Request, tab: str = "annotate"):
        """The single edit page per act: Βασικά πεδία / Σημειώσεις / Πλήρες
        κείμενο / Πίνακες as tabs. `tab` deep-links a starting tab (used by the
        detail-page links and the redirects from the old standalone URLs)."""
        if tab not in _EDIT_TABS:
            tab = "annotate"
        # "Πλήρες κείμενο" was merged into "Βασικά πεδία"; keep old deep-links
        # (detail page, redirects) working by mapping them to the merged tab.
        if tab == "fulltext":
            tab = "fields"
        with cursor() as c:
            c.execute("""SELECT adam, type, title, origin FROM proc.procurement_act
                         WHERE adam=%s""", (adam,))
            act = c.fetchone()
            if not act:
                raise HTTPException(404, f"act {adam} not found")
        return templates.TemplateResponse(request, "act_edit.html",
                                          {"act": act, "tab": tab})

    def _ocr_flags() -> dict:
        """OCR-tier availability for the extraction widget/preview buttons."""
        try:
            from app.ocr import api_key_present
        except ImportError:
            from ocr import api_key_present
        try:
            from app.tables import _local_ocr_enabled
        except ImportError:
            from tables import _local_ocr_enabled
        return {"ocr_available": api_key_present(),
                "local_ocr_available": _local_ocr_enabled()}

    def _fields_panel_context(adam: str) -> dict:
        """Context for the Βασικά-πεδία panel. The CPV picker lives inside the
        authored edit form (shown after take-ownership). Act-level CPV codes
        (proc.act_cpv) are a curator overlay; imports populate line-item CPVs
        (object_detail_cpv) but never act_cpv. So when act_cpv is empty we SEED
        the picker from the act's distinct line-item CPVs — the ones already
        shown on the act detail page — so that after taking ownership the form
        is pre-filled with the existing codes, ready to edit/save."""
        with cursor() as c:
            c.execute("SELECT * FROM proc.procurement_act WHERE adam=%s", (adam,))
            act = c.fetchone()
            if not act:
                raise HTTPException(404, f"act {adam} not found")
            authority_name = None
            if act.get("authority_id"):
                c.execute("SELECT name FROM proc.authority WHERE org_id=%s",
                          (act["authority_id"],))
                row = c.fetchone()
                authority_name = row["name"] if row else None
            c.execute("""SELECT ac.cpv_code, cc.description
                         FROM proc.act_cpv ac
                         LEFT JOIN proc.cpv_code cc ON cc.cpv_code = ac.cpv_code
                         WHERE ac.adam=%s ORDER BY ac.ord, ac.cpv_code""", (adam,))
            cpvs = c.fetchall()
            cpv_seeded = False
            if not cpvs:
                c.execute("""SELECT DISTINCT oc.cpv_code, cc.description
                             FROM proc.act_object_detail od
                             JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                             LEFT JOIN proc.cpv_code cc ON cc.cpv_code = oc.cpv_code
                             WHERE od.adam=%s ORDER BY oc.cpv_code""", (adam,))
                cpvs = c.fetchall()
                cpv_seeded = bool(cpvs)
            # NUTS region chips: the multi-valued act_nuts, falling back to the
            # single procurement_act.nuts_code so existing acts pre-populate.
            c.execute("""SELECT an.nuts_code, nc.label
                         FROM proc.act_nuts an
                         LEFT JOIN proc.nuts_code nc ON nc.nuts_code = an.nuts_code
                         WHERE an.adam=%s ORDER BY an.nuts_code""", (adam,))
            nuts = c.fetchall()
            if not nuts and act.get("nuts_code"):
                c.execute("SELECT nuts_code, label FROM proc.nuts_code WHERE nuts_code=%s",
                          (act["nuts_code"],))
                row = c.fetchone()
                nuts = [row] if row else [{"nuts_code": act["nuts_code"], "label": None}]
        ctx = {"groups": _act_form_fields(), "act": dict(act), "adam": adam,
               "mode": "edit",
               "authority_name": authority_name, "origin": act["origin"],
               "cpvs": cpvs, "cpv_seeded": cpv_seeded, "nuts": nuts,
               "full_text": act.get("full_text"),
               "full_text_html": act.get("full_text_html")}
        ctx.update(_ocr_flags())
        return ctx

    @router.get("/act/{adam}/panel/fields", response_class=HTMLResponse)
    def panel_fields(adam: str, request: Request):
        """Core scalar fields. Editable for AUTHORED acts (the CPV picker is part
        of that form, pre-seeded from line-item CPVs when act_cpv is empty); an
        imported act shows a take-ownership prompt instead, since its core fields
        are source-owned."""
        return templates.TemplateResponse(
            request, "_panel_fields.html", _fields_panel_context(adam))

    @router.get("/act/{adam}/panel/annotate", response_class=HTMLResponse)
    def panel_annotate(adam: str, request: Request):
        return templates.TemplateResponse(
            request, "_panel_annotate.html", _annotate_context(adam, request))

    @router.get("/act/{adam}/panel/fulltext", response_class=HTMLResponse)
    def panel_fulltext(adam: str, request: Request):
        try:
            from app.ocr import api_key_present
        except ImportError:
            from ocr import api_key_present
        return templates.TemplateResponse(
            request, "_panel_fulltext.html",
            {"act": _act_basic(adam), "ocr_available": api_key_present()})

    @router.get("/act/{adam}/panel/tables", response_class=HTMLResponse)
    def panel_tables(adam: str, request: Request):
        try:
            from app.ocr import api_key_present
        except ImportError:
            from ocr import api_key_present
        return templates.TemplateResponse(
            request, "_panel_tables.html",
            {"prefill_adam": adam, "ocr_available": api_key_present()})

    # ------------------------------------------------------------------ #
    # Line-item value corrections. Per-item editable cost_without_vat,
    # keyed by (adam, line_no) so corrections survive re-imports. Feeds the
    # by-CPV analytics and the detail-page line-item table. Original kept.
    # ------------------------------------------------------------------ #
    @router.get("/act/{adam}/items", response_class=HTMLResponse)
    def items_form(adam: str, request: Request):
        with cursor() as c:
            c.execute("""SELECT adam, title, total_cost_with_vat
                         FROM proc.procurement_act WHERE adam=%s""", (adam,))
            act = c.fetchone()
            if not act:
                raise HTTPException(404, f"act {adam} not found")
            # Line items with any current correction joined in.
            c.execute("""
                SELECT od.line_no, od.short_description, od.quantity,
                       od.unit_code, od.cost_without_vat,
                       lic.corrected_cost_without_vat AS corrected
                FROM proc.act_object_detail od
                LEFT JOIN proc.v_line_item_correction_current lic
                       ON lic.adam = od.adam AND lic.line_no = od.line_no
                WHERE od.adam = %s
                ORDER BY od.line_no
            """, (adam,))
            items = c.fetchall()
        from urllib.parse import unquote
        author_cookie = unquote(request.cookies.get("curator", ""))
        return templates.TemplateResponse(
            request, "admin_items.html",
            {"act": act, "items": items, "author_cookie": author_cookie})

    @router.post("/act/{adam}/items")
    async def items_save(adam: str, request: Request):
        form = await request.form()
        author = (form.get("author") or "").strip() or "(anonymous)"

        def parse_money(s: str):
            s = (s or "").strip()
            if not s:
                return None
            cleaned = (s.replace("€", "").replace(" ", "")
                         .replace(".", "").replace(",", ".")
                       if s.count(",") == 1 and s.rfind(",") > s.rfind(".")
                       else s.replace("€", "").replace(" ", "").replace(",", ""))
            try:
                v = round(float(cleaned), 2)
                return v if v >= 0 else None
            except ValueError:
                return None

        with cursor() as c:
            c.execute("SELECT 1 FROM proc.procurement_act WHERE adam=%s", (adam,))
            if not c.fetchone():
                raise HTTPException(404, f"act {adam} not found")
            # Each correctable line item posts a field named "item_{line_no}".
            # Supersede that line's prior correction, insert a new one if a value
            # was given (blank clears it).
            c.execute("""SELECT line_no FROM proc.act_object_detail
                         WHERE adam=%s ORDER BY line_no""", (adam,))
            line_nos = [r["line_no"] for r in c.fetchall()]
            for ln in line_nos:
                raw = form.get(f"item_{ln}", "")
                val = parse_money(raw)
                c.execute("""UPDATE proc.line_item_correction SET superseded=true
                             WHERE adam=%s AND line_no=%s AND NOT superseded""",
                          (adam, ln))
                if val is not None:
                    c.execute("""INSERT INTO proc.line_item_correction
                                     (adam, line_no, corrected_cost_without_vat, author)
                                 VALUES (%s,%s,%s,%s)""", (adam, ln, val, author))

        resp = RedirectResponse(url=f"/admin/act/{adam}/items", status_code=303)
        from urllib.parse import quote
        resp.set_cookie("curator", quote(author), max_age=60 * 60 * 24 * 365,
                        samesite="lax")
        return resp

    # ------------------------------------------------------------------ #
    # Entity merge UI — group duplicate contractors / authorities.
    # Pure overlay (entity_group/entity_member); never touches harvested rows.
    # ------------------------------------------------------------------ #
    def _kind_cfg(kind: str):
        if kind == "contractor":
            return {"table": "proc.economic_operator", "key": "vat_number",
                    "name": "name", "label": "Ανάδοχοι",
                    "join": ("proc.act_operator ao ON ao.operator_id = t.operator_id",
                             "ao.adam")}
        if kind == "authority":
            return {"table": "proc.authority", "key": "org_id",
                    "name": "name", "label": "Αρχές",
                    "join": ("proc.procurement_act a ON a.authority_id = t.org_id",
                             "a.adam")}
        raise HTTPException(404, "unknown entity kind")

    @router.get("/merge/{kind}", response_class=HTMLResponse)
    def merge_home(kind: str, request: Request,
                   q: str = Query(""), focus: str = Query(""),
                   sort: str = Query("name"),
                   page: int = Query(1, ge=1),
                   per_page: int = Query(100, ge=1, le=500)):
        cfg = _kind_cfg(kind)
        key, name = cfg["key"], cfg["name"]
        join_tbl, join_cnt = cfg["join"]
        q = q.strip()
        candidates = []
        total = 0
        # Sort options — name (best for spotting duplicates) is the default;
        # activity surfaces the biggest entities first.
        order = {"name": f"t.{name} ASC",
                 "name_desc": f"t.{name} DESC",
                 "activity": "n_acts DESC NULLS LAST"}.get(sort, f"t.{name} ASC")
        offset = (page - 1) * per_page
        # Once entities are merged, the non-canonical members are represented by
        # their group's canonical entity, so they must not appear as their own
        # rows in the candidate list (the canonical and all ungrouped entities
        # stay). The bottom "Υπάρχουσες ενοποιήσεις" section still lists every
        # member under its group.
        member_filter = f"""NOT EXISTS (
                    SELECT 1 FROM proc.entity_member em
                    JOIN proc.entity_group eg ON eg.id = em.group_id
                    WHERE em.kind = %s AND em.member_key = t.{key}
                      AND eg.canonical_key <> em.member_key)"""
        with cursor() as c:
            if q:
                # SEARCH MODE — matching records, ranked by activity, capped.
                c.execute(f"""
                    SELECT t.{key} AS key, t.{name} AS name,
                           count({join_cnt}) AS n_acts,
                           (SELECT g.id FROM proc.entity_member m
                              JOIN proc.entity_group g ON g.id=m.group_id
                              WHERE m.kind=%s AND m.member_key=t.{key}) AS group_id
                    FROM {cfg['table']} t
                    LEFT JOIN {join_tbl}
                    WHERE (translate(proc.f_unaccent(lower(t.{name})),'ς','σ')
                           LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ')
                        OR t.{key} ILIKE %s)
                      AND {member_filter}
                    GROUP BY t.{key}, t.{name}
                    ORDER BY {order}
                    LIMIT 100
                """, (kind, f"%{q}%", f"%{q}%", kind))
                candidates = c.fetchall()
            else:
                # BROWSE MODE — the full entity list (minus merged-away members),
                # paginated and sortable, so duplicates can be discovered.
                c.execute(f"""SELECT count(*) AS n FROM {cfg['table']} t
                              WHERE {member_filter}""", (kind,))
                total = c.fetchone()["n"]
                c.execute(f"""
                    SELECT t.{key} AS key, t.{name} AS name,
                           count({join_cnt}) AS n_acts,
                           (SELECT g.id FROM proc.entity_member m
                              JOIN proc.entity_group g ON g.id=m.group_id
                              WHERE m.kind=%s AND m.member_key=t.{key}) AS group_id
                    FROM {cfg['table']} t
                    LEFT JOIN {join_tbl}
                    WHERE {member_filter}
                    GROUP BY t.{key}, t.{name}
                    ORDER BY {order}
                    LIMIT %s OFFSET %s
                """, (kind, kind, per_page, offset))
                candidates = c.fetchall()
            # Existing groups for this kind.
            c.execute("""
                SELECT g.id, g.canonical_key, g.display_name, g.created_by,
                       g.created_at,
                       array_agg(m.member_key ORDER BY m.member_key) AS members
                FROM proc.entity_group g
                JOIN proc.entity_member m ON m.group_id=g.id
                WHERE g.kind=%s
                GROUP BY g.id
                ORDER BY g.created_at DESC
            """, (kind,))
            groups = c.fetchall()
            # Resolve display names for group members.
            gname = {}
            for g in groups:
                c.execute(f"""SELECT {key} AS key, {name} AS name
                              FROM {cfg['table']} WHERE {key} = ANY(%s)""",
                          (g["members"],))
                gname[g["id"]] = {r["key"]: r["name"] for r in c.fetchall()}

        total_pages = max(1, (total + per_page - 1) // per_page) if not q else 1
        return templates.TemplateResponse(
            request, "admin_merge.html",
            {"kind": kind, "label": cfg["label"], "q": q, "focus": focus,
             "candidates": candidates, "groups": groups, "gname": gname,
             "sort": sort, "page": page, "per_page": per_page,
             "total": total, "total_pages": total_pages, "browse": not q,
             "admin_tab": f"merge-{kind}"})

    @router.post("/merge/{kind}/create")
    def merge_create(kind: str,
                     keys: list[str] = Form(default=[]),
                     canonical: str = Form(""),
                     display_name: str = Form(""),
                     author: str = Form(""),
                     note: str = Form("")):
        cfg = _kind_cfg(kind)
        keys = [k.strip() for k in keys if k.strip()]
        if len(keys) < 2:
            raise HTTPException(400, "select at least two records to merge")
        author = (author or "").strip() or "(anonymous)"
        display_name = (display_name or "").strip() or None
        key, name = cfg["key"], cfg["name"]
        join_tbl, join_cnt = cfg["join"]

        with cursor() as c:
            # Which of the selected keys already belong to a group?
            c.execute("""SELECT member_key, group_id FROM proc.entity_member
                         WHERE kind=%s AND member_key = ANY(%s)""", (kind, keys))
            existing = c.fetchall()
            existing_groups = sorted({r["group_id"] for r in existing})

            # Decide the target group.
            if not existing_groups:
                # No selection is in a group yet → create a fresh group.
                if canonical not in keys:
                    c.execute(f"""
                        SELECT t.{key} AS key, count({join_cnt}) AS n
                        FROM {cfg['table']} t
                        LEFT JOIN {join_tbl}
                        WHERE t.{key} = ANY(%s)
                        GROUP BY t.{key} ORDER BY n DESC LIMIT 1
                    """, (keys,))
                    row = c.fetchone()
                    canonical = row["key"] if row else keys[0]
                c.execute("""INSERT INTO proc.entity_group
                                (kind, canonical_key, display_name, created_by, note)
                             VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                          (kind, canonical, display_name, author, note or None))
                target_gid = c.fetchone()["id"]
            else:
                # Merge everything into the FIRST existing group; absorb any
                # other groups the selection spans (group-to-group merge).
                target_gid = existing_groups[0]
                for other in existing_groups[1:]:
                    c.execute("""UPDATE proc.entity_member SET group_id=%s
                                 WHERE group_id=%s""", (target_gid, other))
                    c.execute("""DELETE FROM proc.entity_group WHERE id=%s""",
                              (other,))
                # Optional canonical / display-name override on the surviving group.
                if canonical in keys:
                    c.execute("""UPDATE proc.entity_group SET canonical_key=%s
                                 WHERE id=%s""", (canonical, target_gid))
                if display_name:
                    c.execute("""UPDATE proc.entity_group SET display_name=%s
                                 WHERE id=%s""", (display_name, target_gid))

            # Attach every selected key not already a member of the target group.
            c.execute("""SELECT member_key FROM proc.entity_member
                         WHERE group_id=%s""", (target_gid,))
            already = {r["member_key"] for r in c.fetchall()}
            for k in keys:
                if k not in already:
                    c.execute("""INSERT INTO proc.entity_member
                                     (group_id, kind, member_key)
                                 VALUES (%s,%s,%s)
                                 ON CONFLICT (kind, member_key) DO UPDATE
                                   SET group_id=EXCLUDED.group_id""",
                              (target_gid, kind, k))

        resp = RedirectResponse(url=f"/admin/merge/{kind}", status_code=303)
        resp.set_cookie("curator", author, max_age=60*60*24*365, samesite="lax")
        return resp

    @router.post("/merge/{kind}/unmerge")
    def merge_unmerge(kind: str, group_id: int = Form(...)):
        _kind_cfg(kind)  # validate kind
        with cursor() as c:
            # ON DELETE CASCADE removes members automatically.
            c.execute("""DELETE FROM proc.entity_group WHERE id=%s AND kind=%s""",
                      (group_id, kind))
        return RedirectResponse(url=f"/admin/merge/{kind}", status_code=303)

    # ------------------------------------------------------------------ #
    # ATTACHMENTS — upload/store original files per act and search inside
    # them. LOCAL-ONLY: bytes live on the local filesystem (app/attachments),
    # the DB holds only text+metadata, and everything is gated on
    # attachments.enabled() (ATTACHMENTS_ENABLED) so prod stores nothing.
    # ------------------------------------------------------------------ #
    def _attachments():
        try:
            from app import attachments as _att
        except ImportError:
            import attachments as _att   # flat layout (--app-dir=app)
        return _att

    def _attach_ctx(adam: str) -> dict:
        att = _attachments()
        rows = []
        if att.enabled():
            with cursor() as c:
                c.execute("""SELECT id, filename, mimetype, size_bytes, n_inner,
                                    (extracted_text IS NOT NULL AND extracted_text <> '') AS searchable,
                                    uploaded_at
                             FROM proc.act_attachment WHERE adam=%s
                             ORDER BY id DESC""", (adam,))
                rows = c.fetchall()
        return {"adam": adam, "attachments": rows, "attachments_enabled": att.enabled()}

    @router.get("/act/{adam}/panel/attachments", response_class=HTMLResponse)
    def panel_attachments(adam: str, request: Request):
        return templates.TemplateResponse(
            request, "_panel_attachments.html", _attach_ctx(adam))

    @router.post("/act/{adam}/attachments", response_class=HTMLResponse)
    async def attachment_upload(adam: str, request: Request,
                                files: list[UploadFile] = File(default=[])):
        att = _attachments()
        if not att.enabled():
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Τα συνημμένα είναι ανενεργά εδώ.</div>",
                status_code=403)
        with cursor() as c:
            c.execute("SELECT 1 FROM proc.procurement_act WHERE adam=%s", (adam,))
            if not c.fetchone():
                raise HTTPException(404, "act not found")
        try:
            from app.extractors import extract_text_from_upload, collect_files
        except ImportError:
            from extractors import extract_text_from_upload, collect_files
        curator = unquote(request.cookies.get("curator", "")) or "curator"
        for uf in files:
            data = await uf.read()
            if not data:
                continue
            try:
                meta = att.store(adam, uf.filename, data)
            except att.AttachmentError as exc:
                return HTMLResponse(
                    f"<div class='tt-flash tt-error'>{exc}</div>", status_code=400)
            # Searchable text (reuses the extractor: unpacks zips, reads
            # pdf/docx/xlsx/csv). Fail-soft — a file we can't read is still stored.
            text, n_inner = None, None
            try:
                text = extract_text_from_upload(uf.filename, data)
                entries, _ = collect_files(uf.filename, data)
                n_inner = len(entries)
            except Exception:  # noqa: BLE001
                pass
            with cursor() as c:
                c.execute(
                    """INSERT INTO proc.act_attachment
                         (adam, filename, mimetype, size_bytes, checksum,
                          storage_backend, storage_ref, extracted_text, n_inner, uploaded_by)
                       VALUES (%s,%s,%s,%s,%s,'local_fs',%s,%s,%s,%s)""",
                    (adam, uf.filename, meta["mimetype"], meta["size"], meta["checksum"],
                     meta["storage_ref"], text, n_inner, curator))
                c.execute("UPDATE proc.procurement_act SET has_attachments=true WHERE adam=%s",
                          (adam,))
        return templates.TemplateResponse(
            request, "_attachment_list.html", _attach_ctx(adam))

    @router.get("/act/{adam}/attachments/{aid}/download")
    def attachment_download(adam: str, aid: int):
        att = _attachments()
        if not att.enabled():
            raise HTTPException(403, "attachments disabled")
        with cursor() as c:
            c.execute("""SELECT filename, mimetype, storage_ref
                         FROM proc.act_attachment WHERE id=%s AND adam=%s""", (aid, adam))
            row = c.fetchone()
        if not row:
            raise HTTPException(404, "attachment not found")
        try:
            data = att.load(row["storage_ref"])
        except att.AttachmentError:
            raise HTTPException(404, "stored file missing")
        return Response(
            content=data, media_type=row["mimetype"] or "application/octet-stream",
            headers={"Content-Disposition": att.content_disposition(row["filename"])})

    @router.delete("/act/{adam}/attachments/{aid}", response_class=HTMLResponse)
    def attachment_delete(adam: str, aid: int):
        att = _attachments()
        if not att.enabled():
            raise HTTPException(403, "attachments disabled")
        with cursor() as c:
            c.execute("""DELETE FROM proc.act_attachment WHERE id=%s AND adam=%s
                         RETURNING storage_ref""", (aid, adam))
            row = c.fetchone()
        if row:
            att.remove(row["storage_ref"])
        return HTMLResponse("")

    return router
