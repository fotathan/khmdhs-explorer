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
                        resume: str = Form(default="")):
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
        log_fh = open(log_path, "w")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,             # detach process group
                cwd=root,
                env={**os.environ},                  # inherit DATABASE_URL
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
    def admin_job_detail(job_id: int, request: Request):
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
        with cursor() as c:
            c.execute("""SELECT adam, type, title, signed_date, submission_date,
                                total_cost_with_vat, total_cost_without_vat
                         FROM proc.procurement_act WHERE adam=%s""", (adam,))
            act = c.fetchone()
            if not act:
                raise HTTPException(404, f"act {adam} not found")
            # Current annotation (if any) to pre-fill the form.
            c.execute("""SELECT note, tags, flag, author, created_at,
                                corrected_value, corrected_value_without_vat
                         FROM proc.v_act_annotation_current WHERE adam=%s""", (adam,))
            current = c.fetchone()
            # Full history (audit trail) for this act.
            c.execute("""SELECT note, tags, flag, author, created_at, superseded
                         FROM proc.act_annotation
                         WHERE adam=%s ORDER BY created_at DESC""", (adam,))
            history = c.fetchall()

        from urllib.parse import unquote
        author_cookie = unquote(request.cookies.get("curator", ""))
        return templates.TemplateResponse(
            request, "admin_annotate.html",
            {"act": act, "current": current, "history": history,
             "author_cookie": author_cookie,
             "flags": FLAGS, "flag_labels": FLAG_LABELS})

    @router.post("/act/{adam}/annotate")
    def annotate_save(adam: str,
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

        resp = RedirectResponse(url=f"/admin/act/{adam}/annotate", status_code=303)
        # Remember the curator's name for next time (attribution, not auth).
        # Cookies are latin-1 only, so URL-encode to allow Greek names; the form
        # route unquotes it when prefilling. quote() with no safe-set handles
        # any alphabet safely.
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
                   q: str = Query(""), focus: str = Query("")):
        cfg = _kind_cfg(kind)
        key, name = cfg["key"], cfg["name"]
        join_tbl, join_cnt = cfg["join"]
        q = q.strip()
        candidates = []
        with cursor() as c:
            if q:
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
                    ORDER BY n_acts DESC
                    LIMIT 50
                """, (kind, f"%{q}%", f"%{q}%"))
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

        return templates.TemplateResponse(
            request, "admin_merge.html",
            {"kind": kind, "label": cfg["label"], "q": q, "focus": focus,
             "candidates": candidates, "groups": groups, "gname": gname})

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
