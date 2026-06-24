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
import signal
import subprocess
import sys
from urllib.parse import unquote
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates


# ---------------------------------------------------------------------------- #
# Wired up by main.py: we need its template engine and DB cursor() helper.
# ---------------------------------------------------------------------------- #
def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    # Where the subprocess writes its logs (one file per job).
    LOG_DIR = os.environ.get("KHMDHS_LOG_DIR", "/tmp/khmdhs-jobs")
    os.makedirs(LOG_DIR, exist_ok=True)

    ACT_TYPES = ["request", "notice", "auction", "contract", "payment"]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def pid_alive(pid: Optional[int]) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)         # signal 0 = no-op probe
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True             # process exists but is owned by another user

    def reconcile_stale():
        """If a job is recorded as 'running' but the PID is gone, flip it to
        'stale'. Called on every admin page load — cheap, single table scan."""
        with cursor() as c:
            c.execute("""SELECT id, pid FROM proc.ingest_job
                         WHERE status='running'""")
            rows = c.fetchall()
            for r in rows:
                if not pid_alive(r["pid"]):
                    c.execute("""UPDATE proc.ingest_job
                                 SET status='stale', finished_at=now()
                                 WHERE id=%s""", (r["id"],))

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
    @router.get("", response_class=HTMLResponse)
    def admin_home(request: Request):
        reconcile_stale()
        with cursor() as c:
            c.execute("""SELECT id, status, types, date_from, date_to, resume,
                                started_at, finished_at, exit_code, last_error, pid
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
        return templates.TemplateResponse(
            request, "admin_index.html",
            {"jobs": jobs, "window_summary": window_summary,
             "running_now": any_running(),
             "act_types": ACT_TYPES,
             "today": dt.date.today().isoformat(),
             "default_start": (dt.date.today() - dt.timedelta(days=180)).isoformat()},
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
        reconcile_stale()
        with cursor() as c:
            c.execute("""SELECT * FROM proc.ingest_job WHERE id=%s""", (job_id,))
            job = c.fetchone()
            if not job:
                raise HTTPException(404, f"job {job_id} not found")
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
             "is_active": job["status"] == "running"},
        )

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
                    sort: str = Query("recent"),
                    page: int = Query(1, ge=1)):
        """Data-management list: browse/filter ALL acts (imported + authored),
        with a link to edit each. The entry point for the management tool."""
        per_page = 50
        offset = (page - 1) * per_page
        where = ["TRUE"]
        args: list = []
        q = q.strip()
        if q:
            where.append("(translate(proc.f_unaccent(lower(a.title)),'ς','σ') "
                         "LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ') "
                         "OR a.adam ILIKE %s)")
            args += [f"%{q}%", f"%{q}%"]
        if external_id.strip():
            where.append("a.external_id ILIKE %s")
            args.append(f"%{external_id.strip()}%")
        if reference.strip():
            where.append("(a.reference_number ILIKE %s OR a.authority_reference ILIKE %s)")
            args += [f"%{reference.strip()}%", f"%{reference.strip()}%"]
        if data_source.strip():
            where.append("a.data_source = %s")
            args.append(data_source.strip())
        if origin in ("import", "authored"):
            where.append("a.origin = %s")
            args.append(origin)
        if type.strip():
            where.append("a.type = %s")
            args.append(type.strip())
        if source_status.strip():
            where.append("a.source_status = %s")
            args.append(source_status.strip())
        if has_attachments == "1":
            where.append("a.has_attachments IS TRUE")
        elif has_attachments == "0":
            where.append("(a.has_attachments IS NOT TRUE)")
        if date_from.strip():
            where.append("a.submission_date >= %s")
            args.append(date_from.strip())
        if date_to.strip():
            where.append("a.submission_date <= %s")
            args.append(date_to.strip())
        where_sql = " AND ".join(where)

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

        total_pages = max(1, (total + per_page - 1) // per_page)
        return templates.TemplateResponse(
            request, "admin_acts.html",
            {"rows": rows, "total": total, "page": page, "total_pages": total_pages,
             "q": q, "external_id": external_id, "reference": reference,
             "data_source": data_source, "origin": origin, "type": type,
             "source_status": source_status, "has_attachments": has_attachments,
             "date_from": date_from, "date_to": date_to, "sort": sort,
             "sources": sources, "statuses": statuses})

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
            ],
            "Γεωγραφία": [
                ("nuts_code", "Κωδικός NUTS", "text"),
                ("city", "Πόλη", "text"),
                ("postal_code", "Τ.Κ.", "text"),
                ("country", "Χώρα", "text"),
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
        return templates.TemplateResponse(
            request, "admin_act_form.html",
            {"groups": _act_form_fields(), "act": {}, "mode": "create",
             "adam": None, "authority_name": None})

    @router.get("/acts/{adam}/edit", response_class=HTMLResponse)
    def act_edit_form(adam: str, request: Request):
        """Edit form for an AUTHORED act. Imported acts are redirected to the
        overlay-correction tools (their core fields are source-owned)."""
        with cursor() as c:
            c.execute("SELECT * FROM proc.procurement_act WHERE adam = %s", (adam,))
            act = c.fetchone()
            if not act:
                raise HTTPException(404, "act not found")
            if act["origin"] != "authored":
                # Not editable here — send to the existing edit hub.
                return RedirectResponse(url=f"/act/{adam}/edit", status_code=303)
            authority_name = None
            if act.get("authority_id"):
                c.execute("SELECT name FROM proc.authority WHERE org_id = %s",
                          (act["authority_id"],))
                row = c.fetchone()
                authority_name = row["name"] if row else None
        return templates.TemplateResponse(
            request, "admin_act_form.html",
            {"groups": _act_form_fields(), "act": dict(act), "mode": "edit",
             "adam": adam, "authority_name": authority_name})

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
                data[name] = None if raw == "" else raw
            elif kind in ("date",):
                data[name] = None if raw == "" else raw
            else:
                data[name] = None if raw == "" else raw

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
                cols = list(data.keys())
                set_sql = ", ".join(f"{c2} = %s" for c2 in cols)
                vals = [data[c2] for c2 in cols]
                c.execute(
                    f"""UPDATE proc.procurement_act
                        SET {set_sql}, last_edited_by = %s, last_edited_at = now()
                        WHERE adam = %s""",
                    vals + [curator, adam])
            return RedirectResponse(url=f"/admin/acts/{adam}/edit?saved=1",
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
            placeholders = ", ".join(["%s"] * len(all_cols))
            with cursor() as c:
                c.execute("SELECT 1 FROM proc.procurement_act WHERE adam = %s", (adam,))
                if c.fetchone():
                    raise HTTPException(409, f"act {adam} already exists")
                c.execute(
                    f"""INSERT INTO proc.procurement_act ({", ".join(all_cols)})
                        VALUES ({placeholders})""",
                    all_vals)
            return RedirectResponse(url=f"/admin/acts/{adam}/edit?saved=1",
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
                # already owned — just go to the edit form
                return RedirectResponse(url=f"/admin/acts/{adam}/edit", status_code=303)
            c.execute(
                """UPDATE proc.procurement_act
                   SET origin = 'authored',
                       authored_by = COALESCE(authored_by, %s),
                       last_edited_by = %s,
                       last_edited_at = now()
                   WHERE adam = %s AND origin = 'import'""",
                (curator, curator, adam))
        return RedirectResponse(url=f"/admin/acts/{adam}/edit?owned=1", status_code=303)

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
             "flag_labels": FLAG_LABELS})

    @router.get("/act/{adam}/annotate", response_class=HTMLResponse)
    def annotate_form(adam: str, request: Request):
        return templates.TemplateResponse(
            request, "admin_annotate.html", _annotate_context(adam, request))

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

        resp = RedirectResponse(url=f"/admin/act/{adam}/annotate", status_code=303)
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
                                full_text, full_text_extracted_at, full_text_source
                         FROM proc.procurement_act WHERE adam=%s""", (adam,))
            act = c.fetchone()
        if not act:
            raise HTTPException(404, f"act {adam} not found")
        return act

    @router.get("/act/{adam}/edit", response_class=HTMLResponse)
    def act_edit_hub(adam: str, request: Request):
        with cursor() as c:
            c.execute("""SELECT adam, type, title FROM proc.procurement_act
                         WHERE adam=%s""", (adam,))
            act = c.fetchone()
            if not act:
                raise HTTPException(404, f"act {adam} not found")
        return templates.TemplateResponse(request, "act_edit.html", {"act": act})

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
                    WHERE translate(proc.f_unaccent(lower(t.{name})),'ς','σ')
                          LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ')
                       OR t.{key} ILIKE %s
                    GROUP BY t.{key}, t.{name}
                    ORDER BY {order}
                    LIMIT 100
                """, (kind, f"%{q}%", f"%{q}%"))
                candidates = c.fetchall()
            else:
                # BROWSE MODE — the full entity list, paginated and sortable, so
                # duplicates can be discovered without knowing what to search.
                c.execute(f"SELECT count(*) AS n FROM {cfg['table']}")
                total = c.fetchone()["n"]
                c.execute(f"""
                    SELECT t.{key} AS key, t.{name} AS name,
                           count({join_cnt}) AS n_acts,
                           (SELECT g.id FROM proc.entity_member m
                              JOIN proc.entity_group g ON g.id=m.group_id
                              WHERE m.kind=%s AND m.member_key=t.{key}) AS group_id
                    FROM {cfg['table']} t
                    LEFT JOIN {join_tbl}
                    GROUP BY t.{key}, t.{name}
                    ORDER BY {order}
                    LIMIT %s OFFSET %s
                """, (kind, per_page, offset))
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
             "total": total, "total_pages": total_pages, "browse": not q})

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

    return router
