"""
db.py — minimal PostgreSQL layer for the KHMDHS ingester, plus a CLI runner.

It exposes exactly the three methods khmdhs_ingest.Repository expects:
    db.execute(sql, params)            -> None
    db.execute_returning(sql, params)  -> the first column of the first row
    db.commit()                        -> None
(placeholders are psycopg2-style "%s", which is what the ingester already uses.)

Connection settings come from a DATABASE_URL env var, e.g.
    export DATABASE_URL="postgresql://user:pass@localhost:5432/procurement"
or from the individual PG* libpq vars (PGHOST, PGUSER, ...). Nothing is
hard-coded and no password is stored by this script.

Usage
-----
    pip install "psycopg[binary]"          # psycopg 3   (preferred)
        # or:  pip install psycopg2-binary  # psycopg 2  (also supported)

    # 1) create the schema (runs schema.sql once; safe to re-run if you DROP first)
    python3 db.py init-schema

    # 2) backfill a date range, all five act types, in <=180-day windows
    python3 db.py backfill --start 2023-01-01 --end 2024-12-31

    # 3) sanity counts
    python3 db.py stats

The runner reuses khmdhs_ingest.py for all the API + mapping logic; this file
only owns the database connection and the command-line surface.
"""

from __future__ import annotations
import argparse
import datetime as dt
import os
import sys
import time

# --- driver shim: prefer psycopg3, fall back to psycopg2 -------------------- #
_DRIVER = None
try:
    import psycopg                      # psycopg 3
    _DRIVER = "psycopg3"
except ImportError:
    try:
        import psycopg2                 # psycopg 2
        import psycopg2.extras
        _DRIVER = "psycopg2"
    except ImportError:
        _DRIVER = None


class Database:
    """Thin wrapper giving the ingester its execute/execute_returning/commit API.

    A single connection + single cursor is intentional: the ingester is
    single-threaded and commits once per 180-day window, which keeps memory flat
    and makes a failed window easy to retry without half-applied state.
    """

    def __init__(self, dsn: str | None = None, autocommit: bool = False):
        if _DRIVER is None:
            sys.exit("No Postgres driver. Install one: pip install 'psycopg[binary]'")
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        # If no DSN, psycopg reads standard PG* env vars (PGHOST, PGDATABASE...).
        if _DRIVER == "psycopg3":
            # prepare_threshold=None disables server-side prepared statements:
            # Supabase's transaction pooler can route consecutive statements to
            # different physical backends, so a prepared statement made on one
            # isn't found on the next ("prepared statement _pg3_0 does not exist").
            self.conn = (psycopg.connect(self.dsn, prepare_threshold=None)
                         if self.dsn else psycopg.connect(prepare_threshold=None))
        else:
            self.conn = psycopg2.connect(self.dsn) if self.dsn else psycopg2.connect()
        self.conn.autocommit = autocommit
        self.cur = self.conn.cursor()

    # ---- the three methods Repository depends on --------------------------- #
    @staticmethod
    def _coerce_params(params):
        """Defensive net: psycopg cannot bind a bare dict to %s. The mapper is
        supposed to hand us scalars (or a Json wrapper for jsonb columns), but
        live KHMDHS data occasionally surfaces a {key,value} object in a field
        we didn't anticipate. Rather than crash a whole 180-day window, reduce
        any stray plain dict to its 'value' (falling back to 'key'). Json
        wrapper objects from the driver are left untouched.
        """
        def fix(v):
            if isinstance(v, dict):
                # leave driver Json wrappers / non {key,value} dicts to psycopg
                if "value" in v or "key" in v:
                    return v.get("value", v.get("key"))
            return v
        if isinstance(params, dict):
            return {k: fix(v) for k, v in params.items()}
        if isinstance(params, (list, tuple)):
            return type(params)(fix(v) for v in params)
        return params

    def execute(self, sql: str, params=()):
        self.cur.execute(sql, self._coerce_params(params))

    def execute_returning(self, sql: str, params=()):
        """Run an INSERT ... RETURNING and give back the first column."""
        self.cur.execute(sql, self._coerce_params(params))
        row = self.cur.fetchone()
        return row[0] if row else None

    def commit(self):
        self.conn.commit()

    # ---- helpers used by the CLI (not by the ingester) --------------------- #
    def rollback(self):
        self.conn.rollback()

    def query(self, sql: str, params=()):
        self.cur.execute(sql, params)
        return self.cur.fetchall()

    def dict_cursor(self):
        """A second cursor on THIS connection that yields dict rows — lets code
        written against the app's dict-row cursor (e.g. app.interconnect helpers)
        run inside an ingestion transaction. Shares the connection, so writes
        commit together with self.commit()."""
        if _DRIVER == "psycopg3":
            from psycopg.rows import dict_row
            return self.conn.cursor(row_factory=dict_row)
        import psycopg2.extras
        return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def close(self):
        try:
            self.cur.close()
        finally:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        self.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))


def cmd_init_schema(args):
    schema_path = args.file or os.path.join(HERE, "schema.sql")
    if not os.path.exists(schema_path):
        sys.exit(f"schema file not found: {schema_path}")
    sql = open(schema_path, encoding="utf-8").read()
    with Database(autocommit=True) as db:   # DDL: commit each statement as it runs
        # psycopg can execute a multi-statement script in one call.
        db.cur.execute(sql)
    print(f"schema applied from {schema_path}")


def _finalize_job(db, status: str):
    """Record a terminal status on proc.ingest_job for an admin-launched run.

    Admin backfills are spawned detached and the web request returns at once,
    so the runner itself is what moves its job row out of 'running' on the way
    out. No-op for plain shell runs (no INGEST_JOB_ID). Guarded on
    status='running' so it never overrides a 'cancelled' the cancel button set.

    Reads INGEST_JOB_ID straight from the environment (rather than
    khmdhs_ingest.INGEST_JOB_ID) so this works for ANY harvester the admin panel
    launches — khmdhs backfill and diavgeia-backfill alike.
    """
    raw = os.environ.get("INGEST_JOB_ID")
    if not raw:
        return
    try:
        job_id = int(raw)
    except ValueError:
        return
    try:
        db.rollback()  # clear any failed transaction so the UPDATE can run
    except Exception:
        pass
    try:
        db.execute("""UPDATE proc.ingest_job
                      SET status=%s, finished_at=now()
                      WHERE id=%s AND status='running'""",
                   (status, job_id))
        db.commit()
    except Exception:
        pass


def cmd_backfill(args):
    # Import here so `init-schema` works even before deps for the API exist.
    import khmdhs_ingest as ingest

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    types = args.types or ["request", "notice", "auction", "contract", "payment"]

    with Database() as db:
        client = ingest.KhmdhsClient()
        repo = ingest.Repository(db)
        totals = {"windows": 0, "done": 0, "skipped": 0, "errored": 0}
        final_status = "done"
        try:
            for act_type in types:
                print(f"\n=== backfilling {act_type}: {start} .. {end}"
                      f"{' (resume)' if args.resume else ''} ===")
                s = ingest.ingest_type(client, repo, act_type, start, end,
                                        resume=args.resume)
                for k in totals: totals[k] += s[k]
        except BaseException:
            final_status = "error"
            raise
        finally:
            _finalize_job(db, final_status)
    print(f"\nbackfill complete. windows={totals['windows']} "
          f"done={totals['done']} skipped={totals['skipped']} "
          f"errored={totals['errored']}")
    if totals["errored"]:
        print("  (errored windows are recorded with status='error' in proc.ingest_window;"
              " re-run with --resume to retry them, or inspect last_error column.)")


def cmd_fulltext_backfill(args):
    """Mass full-text extraction over acts ALREADY in the database that have no
    text yet. Resumable: each act commits as it finishes, and the selection is
    'never tried' (full_text NULL and full_text_source NULL), so a re-run picks
    up where the last left off. Scanned/no-text acts are marked tried-empty so
    they aren't re-downloaded on the next run. Bounded by --limit per run.
    """
    import khmdhs_ingest as ingest

    types = args.types or ["request", "notice", "auction", "contract", "payment"]
    limit = args.limit

    # Build the WHERE for "untried" acts, with optional type/date filters.
    # Exclude Diavgeia acts: their documents live on diavgeia.gov.gr, not the
    # KHMDHS attachment endpoint this pass uses, so fetching them here would fail
    # and (worse) mark them tried-empty — blocking the dedicated
    # diavgeia-fulltext-backfill. They have their own bulk pass.
    where = ["full_text IS NULL", "full_text_source IS NULL",
             "data_source IS DISTINCT FROM 'diavgeia'"]
    params: list = []
    where.append("type = ANY(%s)")
    params.append(types)
    if args.start:
        where.append("coalesce(submission_date, signed_date) >= %s")
        params.append(dt.date.fromisoformat(args.start))
    if args.end:
        where.append("coalesce(submission_date, signed_date) <= %s")
        params.append(dt.date.fromisoformat(args.end))
    where_sql = " AND ".join(where)

    final_status = "done"
    with Database() as db:
      try:
        # How many are still untried (for a sense of scale)?
        remaining = db.query(
            f"SELECT count(*) FROM proc.procurement_act WHERE {where_sql}", tuple(params)
        )[0][0]
        print(f"untried acts matching filter: {remaining:,}")
        if remaining == 0:
            print("nothing to do.")
            return

        # Pull this run's batch of ADAMs (+ type, needed for the attachment URL).
        rows = db.query(
            f"""SELECT adam, type FROM proc.procurement_act
                WHERE {where_sql}
                ORDER BY coalesce(submission_date, signed_date) DESC NULLS LAST
                LIMIT %s""",
            tuple(params) + (limit,),
        )
        print(f"this run will attempt: {len(rows):,} (limit={limit})\n")

        client = ingest.KhmdhsClient()
        repo = ingest.Repository(db)
        n = {"stored": 0, "garbled": 0, "empty": 0, "error": 0}
        for i, (adam, act_type) in enumerate(rows, start=1):
            status = ingest.extract_full_text_status(client, repo, str(act_type), adam)
            n[status] += 1
            if status == "garbled":
                # Surface garbled extractions in the log, one line each, so they
                # can be found and re-done via the manual OCR path.
                print(f"  ! garbled extraction (flagged for OCR): {adam}")
            db.commit()  # commit each act → resumable
            if i % 100 == 0 or i == len(rows):
                print(f"  {i:>6}/{len(rows)}  stored={n['stored']} "
                      f"garbled={n['garbled']} empty={n['empty']} error={n['error']}")
      except BaseException:
        final_status = "error"
        raise
      finally:
        _finalize_job(db, final_status)

    print(f"\nfull-text backfill run complete. "
          f"stored={n['stored']} garbled={n['garbled']} "
          f"empty={n['empty']} error={n['error']}")
    if n["garbled"]:
        print(f"  {n['garbled']} act(s) extracted GARBLED text (source "
              f"'auto:garbled?') — re-extract via manual OCR on the act page.")
    still = remaining - n["stored"] - n["garbled"] - n["empty"]
    print(f"  approx still-untried after this run: {max(still, 0):,} "
          f"(re-run to continue; 'error' acts will be retried, 'empty'/'garbled' won't)")


# --------------------------------------------------------------------------- #
# Mass table extraction (report-only, Phase 1). Runs the table extractor over a
# job's targeted ΑΔΑΜ list and records, per act, what was found. Modelled on the
# full-text backfill; self-contained job lifecycle in proc.table_extract_*.
# --------------------------------------------------------------------------- #
def _table_outcome(adam: str, act_type: str, data_source: str | None = None,
                   want_tables: bool = False):
    """Classify one act's attachments for table content. Returns a 5-tuple
    (outcome, n_tables, n_files, note, tables): `tables` is the list of clean
    extracted table dicts (source/locator/rows/n_rows/n_cols) when
    want_tables and outcome=='extracted', else None — the caller persists them
    in save mode. Never calls OCR (scanned docs are just flagged 'needs_ocr').
    Fail-soft: never raises."""
    import os as _o
    import sys as _sys
    # The extraction modules (extractors/exporter/ocr) are kept byte-identical
    # with a standalone flat-layout tool and import each other with BARE names
    # (`from extractors import ...`). The web app makes that work by putting the
    # app/ dir on sys.path (main.py); this CLI subprocess must do the same, or
    # `app.ocr`'s bare imports fail. Mirror main.py exactly.
    _app_dir = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "app")
    if _app_dir not in _sys.path:
        _sys.path.insert(0, _app_dir)
    try:
        from app.tables import _fetch_act_document, _fetch_diavgeia_document
        from app.extractors import collect_files, extract_entry
        from app.table_relevance import annotate as _annotate_rel, enabled as _rel_on
    except ImportError:
        from tables import _fetch_act_document, _fetch_diavgeia_document
        from extractors import collect_files, extract_entry
        from table_relevance import annotate as _annotate_rel, enabled as _rel_on
    import khmdhs_ingest
    api_key = bool(_o.environ.get("ANTHROPIC_API_KEY"))

    try:
        # Source-aware fetch: Diavgeia acts' documents live on diavgeia.gov.gr,
        # not the KHMDHS attachment endpoint (mirrors app/tables.py:/fetch).
        if data_source == "diavgeia":
            data, fname = _fetch_diavgeia_document(adam)
        else:
            data, fname = _fetch_act_document(act_type, adam)
    except ValueError:
        return ("no_attachment", 0, 0, "χωρίς συνημμένο", None)
    except Exception as e:  # noqa: BLE001
        return ("error", 0, 0, f"fetch: {type(e).__name__}", None)
    try:
        entries, _errs = collect_files(fname, data)
    except Exception as e:  # noqa: BLE001
        return ("error", 0, 0, f"collect: {type(e).__name__}", None)

    n_files = 0
    total_tables = 0
    n_main = 0
    n_rel = 0
    statuses = set()
    cells = []
    kept = []
    for entry in entries:
        n_files += 1
        try:
            rep = extract_entry(entry)
        except Exception:  # noqa: BLE001
            statuses.add("error")
            continue
        statuses.add(rep.status)
        if rep.status == "ok" and rep.tables:
            total_tables += len(rep.tables)
            # Relevance pass (item/service lists vs TOC/signature/boilerplate
            # grids) — per report, so stitch fragments find their parent.
            n_rel += _annotate_rel(rep.tables)
            n_main += sum(1 for t in rep.tables if t.get("role") != "fragment")
            for t in rep.tables:
                for row in (t.get("rows") or [])[:25]:
                    cells.append(" ".join(str(c) for c in row))
            if want_tables:
                kept.extend(rep.tables)

    if total_tables > 0:
        if khmdhs_ingest.looks_garbled("\n".join(cells)):
            return ("garbled", total_tables, n_files, "αλλοιωμένο περιεχόμενο πινάκων", None)
        # Note carries the relevance tally either way (report + save modes), so
        # the job log flags acts with ONLY irrelevant tables — no review time
        # wasted on them. In save mode only relevant MAIN tables persist (the
        # stitched parent already contains its page fragments' rows).
        if _rel_on():
            note = (f"{n_main} πίνακες, {n_rel} σχετικοί" if n_rel
                    else f"μόνο άσχετοι πίνακες ({n_main})")
            if want_tables:
                kept = [t for t in kept
                        if t.get("relevant") and t.get("role") != "fragment"]
        else:
            note = None  # classifier disabled — behave exactly as before
        return ("extracted", total_tables, n_files, note, kept if want_tables else None)
    if statuses & {"scanned", "image"}:
        note = "σαρωμένο — χρειάζεται OCR" + ("" if api_key else " (χωρίς ANTHROPIC_API_KEY)")
        return ("needs_ocr", 0, n_files, note, None)
    if "no_tables" in statuses:
        return ("no_tables", 0, n_files, None, None)
    if statuses & {"unsupported", "error"}:
        return ("error", 0, n_files, "; ".join(sorted(statuses)), None)
    return ("no_tables", 0, n_files, None, None)


def _finalize_table_job(db, job_id, status):
    """Record the terminal status of a table-extract job (guard on 'running' so
    it never overrides a 'cancelled')."""
    if job_id is None:
        return
    try:
        db.rollback()
    except Exception:
        pass
    try:
        db.execute("""UPDATE proc.table_extract_job
                      SET status=%s, finished_at=now()
                      WHERE id=%s AND status='running'""", (status, job_id))
        db.commit()
    except Exception:
        pass


def _save_act_tables(db, adam, tables):
    """Persist clean extracted tables for one act into proc.extracted_table
    (UNPUBLISHED). Non-destructive & idempotent: skips an act that already has
    any extracted_table rows, so a re-run never duplicates and curator-edited
    tables are never clobbered. Returns the number of rows written."""
    import khmdhs_ingest
    db.cur.execute("SELECT 1 FROM proc.extracted_table WHERE adam=%s LIMIT 1", (adam,))
    if db.cur.fetchone():
        return 0
    saved = 0
    for t in tables:
        db.execute(
            """INSERT INTO proc.extracted_table
                 (adam, source, locator, rows, n_rows, n_cols)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (adam, t["source"], t["locator"],
             khmdhs_ingest._as_jsonb(t["rows"]),
             int(t["n_rows"]), int(t["n_cols"])))
        saved += 1
    return saved


def cmd_extract_tables(args):
    """Process a table-extract job's targeted acts. In save mode (job.save_tables)
    clean extracted tables are persisted to proc.extracted_table (unpublished);
    otherwise the run is report-only. Resumable: only targets not yet marked
    done are processed."""
    job_id = int(args.job)
    with Database() as db:
        n = {"extracted": 0, "garbled": 0, "needs_ocr": 0,
             "no_tables": 0, "no_attachment": 0, "error": 0}
        saved_total = 0
        final_status = "done"
        try:
            db.cur.execute("SELECT save_tables FROM proc.table_extract_job WHERE id=%s",
                           (job_id,))
            srow = db.cur.fetchone()
            save_mode = bool(srow[0] if not hasattr(srow, "keys") else srow["save_tables"])
            db.cur.execute("""SELECT t.adam, a.type, a.title, a.data_source
                              FROM proc.table_extract_target t
                              JOIN proc.procurement_act a ON a.adam = t.adam
                              WHERE t.job_id=%s AND NOT t.done
                              ORDER BY t.ord""", (job_id,))
            targets = db.cur.fetchall()
            total = len(targets)
            print(f"table extraction job {job_id}: {total} act(s) to process"
                  f"{' · save mode' if save_mode else ' · report only'}\n")
            last_commit = time.time()
            for i, row in enumerate(targets, start=1):
                adam = row[0] if not hasattr(row, "keys") else row["adam"]
                act_type = row[1] if not hasattr(row, "keys") else row["type"]
                title = row[2] if not hasattr(row, "keys") else row["title"]
                data_source = row[3] if not hasattr(row, "keys") else row["data_source"]
                outcome, n_tables, n_files, note, tables = _table_outcome(
                    adam, act_type, data_source, want_tables=save_mode)
                n[outcome] = n.get(outcome, 0) + 1
                if note and note.startswith("μόνο άσχετοι"):
                    n["only_irrelevant"] = n.get("only_irrelevant", 0) + 1
                n_saved = 0
                if save_mode and outcome == "extracted" and tables:
                    n_saved = _save_act_tables(db, adam, tables)
                    saved_total += n_saved
                    if n_saved == 0:
                        note = "πίνακες ήδη αποθηκευμένοι — παράλειψη"
                db.execute("""INSERT INTO proc.table_extract_log
                                (job_id, adam, act_type, title, outcome,
                                 n_tables, n_files, note, n_saved)
                              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                           (job_id, adam, act_type, title, outcome,
                            n_tables, n_files, note, n_saved))
                db.execute("""UPDATE proc.table_extract_target SET done=true
                              WHERE job_id=%s AND adam=%s""", (job_id, adam))
                if time.time() - last_commit >= 3:
                    db.commit()
                    last_commit = time.time()
                if i % 50 == 0 or i == total:
                    print(f"  {i:>6}/{total}  extracted={n['extracted']} "
                          f"garbled={n['garbled']} needs_ocr={n['needs_ocr']} "
                          f"no_tables={n['no_tables']} no_attach={n['no_attachment']} "
                          f"error={n['error']} saved={saved_total}")
            db.commit()
        except BaseException:
            final_status = "error"
            raise
        finally:
            _finalize_table_job(db, job_id, final_status)
    print(f"\ntable extraction complete. {dict(n)} saved={saved_total}")


def _watermark(db, act_type: str):
    """The latest end-date of a successfully-completed window for this type,
    or None if the type has never been backfilled. This is our 'last caught
    up to' marker — derived from ingest_window, so no extra state to keep."""
    rows = db.query("""SELECT max(date_to) FROM proc.ingest_window
                       WHERE act_type=%s AND status='done'""", (act_type,))
    return rows[0][0] if rows and rows[0][0] else None


def cmd_catchup(args):
    """Incremental 'fetch everything since last run' per act type.

    For each type: start = (latest done window end - overlap days), end = today.
    The overlap re-fetches recent days so late-published / backdated records
    aren't missed; upserts make the redundant rows harmless. Types never
    backfilled before have no watermark, so they require an explicit --start
    (we refuse to silently fetch all of history)."""
    import khmdhs_ingest as ingest

    end = dt.date.today()
    overlap = dt.timedelta(days=args.overlap_days)
    types = args.types or ["request", "notice", "auction", "contract", "payment"]
    explicit_start = dt.date.fromisoformat(args.start) if args.start else None

    with Database() as db:
        client = ingest.KhmdhsClient()
        repo = ingest.Repository(db)
        totals = {"windows": 0, "done": 0, "skipped": 0, "errored": 0}
        any_run = False
        for act_type in types:
            wm = _watermark(db, act_type)
            if wm is not None:
                start = wm - overlap
                origin = f"watermark {wm} − {args.overlap_days}d"
            elif explicit_start is not None:
                start = explicit_start
                origin = f"--start (no prior history for {act_type})"
            else:
                print(f"\n=== {act_type}: SKIPPED — never backfilled and no "
                      f"--start given. Run a full backfill first, or pass "
                      f"--start YYYY-MM-DD. ===")
                continue
            if start > end:
                start = end
            print(f"\n=== catching up {act_type}: {start} .. {end}  ({origin}) ===")
            s = ingest.ingest_type(client, repo, act_type, start, end, resume=False)
            for k in totals:
                totals[k] += s[k]
            any_run = True

        # surface the watermarks after the run, for the log/audit trail.
        if any_run:
            print("\nnew watermarks (latest done window end per type):")
            for act_type in types:
                wm = _watermark(db, act_type)
                print(f"  {act_type:9s} {wm if wm else '—'}")

    print(f"\ncatch-up complete. windows={totals['windows']} "
          f"done={totals['done']} skipped={totals['skipped']} "
          f"errored={totals['errored']}")
    if totals["errored"]:
        print("  (errored windows recorded with status='error'; "
              "inspect with: python3 db.py progress --errors-only)")
    return totals


def cmd_diavgeia_backfill(args):
    """Backfill Diavgeia decisions (notice/award/contract) over an issueDate range.

    Parallel to `backfill`, but for the Diavgeia source: ADA-keyed rows in
    proc.diavgeia_decision (+ children), windowed in proc.diavgeia_ingest_window.
    """
    import diavgeia_ingest as di

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    names = args.types or di.TYPE_NAMES

    with Database() as db:
        client = di.DiavgeiaClient()
        repo = di.DiavgeiaRepository(db, client)
        totals = {"windows": 0, "done": 0, "skipped": 0, "errored": 0}
        # Mirror cmd_backfill: on an admin-launched run, move the ingest_job row
        # out of 'running' on the way out (done on clean exit, error on crash).
        final_status = "done"
        try:
            for name in names:
                uid = di.NAME_TO_UID[name]
                print(f"\n=== diavgeia {name} ({uid}): {start} .. {end}"
                      f"{' (resume)' if args.resume else ''} ===")
                s = di.ingest_type(client, repo, uid, start, end, resume=args.resume)
                for k in totals:
                    totals[k] += s[k]
            # Authority dedup runs as a bounded post-pass (one API call per distinct
            # organization), decoupled from the per-decision hot path. --skip-resolve
            # to defer it to a later `diavgeia-resolve`.
            if not args.skip_resolve:
                print("\n=== resolving authorities (dedupe by ΑΦΜ) ===")
                n = repo.resolve_authorities()
                print(f"  resolved {n} distinct organizations into proc.authority")
            # Project into procurement_act so the web app surfaces the new acts.
            if not args.skip_project:
                print("\n=== projecting into procurement_act (app-facing) ===")
                n = repo.project_all()
                print(f"  {n} Diavgeia acts present in proc.procurement_act")
            # Full-text pass (opt-in via EXTRACT_FULLTEXT): now that the acts are
            # projected, fetch each handled act's document and store its text.
            # Runs per-act-logged run only (needs INGEST_JOB_ID to know the set).
            if di.EXTRACT_FULLTEXT and di.INGEST_JOB_ID:
                print("\n=== extracting full text (fetch + parse documents) ===")
                ft = repo.extract_fulltext_pass(di.INGEST_JOB_ID)
                print(f"  full text: {ft['extracted']} extracted, "
                      f"{ft['garbled']} garbled, {ft['empty']} without text "
                      f"(of {ft['seen']} handled)")
        except BaseException:
            final_status = "error"
            raise
        finally:
            _finalize_job(db, final_status)
    print(f"\ndiavgeia backfill complete. windows={totals['windows']} "
          f"done={totals['done']} skipped={totals['skipped']} "
          f"errored={totals['errored']}")
    if totals["errored"]:
        print("  (errored windows recorded with status='error' in "
              "proc.diavgeia_ingest_window; re-run with --resume to retry them.)")
    return totals


def cmd_diavgeia_fulltext_backfill(args):
    """Mass full-text extraction over Diavgeia acts ALREADY projected into
    procurement_act that don't have text yet. The Diavgeia counterpart of
    fulltext-backfill: selects 'never tried' acts (full_text NULL and
    full_text_source NULL, data_source='diavgeia'), fetches + parses each act's
    Diavgeia document, and stores the text. Resumable (each act commits as it
    finishes; scanned/no-document acts are marked tried-empty so a re-run skips
    them) and bounded by --limit. Logs one per-act row per attempt when launched
    from the admin panel (INGEST_JOB_ID), so the job page shows the list + filter.
    """
    import diavgeia_ingest as di

    limit = args.limit
    where = ["data_source='diavgeia'", "(full_text IS NULL OR full_text='')",
             "full_text_source IS NULL", "origin <> 'authored'"]
    params: list = []
    if args.start:
        where.append("coalesce(submission_date, signed_date) >= %s")
        params.append(dt.date.fromisoformat(args.start))
    if args.end:
        where.append("coalesce(submission_date, signed_date) <= %s")
        params.append(dt.date.fromisoformat(args.end))
    where_sql = " AND ".join(where)

    final_status = "done"
    with Database() as db:
      try:
        repo = di.DiavgeiaRepository(db, di.DiavgeiaClient())
        remaining = db.query(
            f"SELECT count(*) FROM proc.procurement_act WHERE {where_sql}",
            tuple(params))[0][0]
        print(f"untried Diavgeia acts matching filter: {remaining:,}")
        if remaining == 0:
            print("nothing to do.")
            return
        rows = db.query(
            f"""SELECT adam, type, title FROM proc.procurement_act
                WHERE {where_sql}
                ORDER BY coalesce(submission_date, signed_date) DESC NULLS LAST
                LIMIT %s""",
            tuple(params) + (limit,))
        print(f"this run will attempt: {len(rows):,} (limit={limit})\n")

        n = {"stored": 0, "garbled": 0, "empty": 0, "error": 0}
        for i, (adam, act_type, title) in enumerate(rows, start=1):
            extracted, chars, note = di._extract_diavgeia_full_text(repo, adam)
            if note in ("extracted", "ocr_local"):
                n["stored"] += 1
            elif note == "garbled":
                n["garbled"] += 1
                print(f"  ! garbled extraction (flagged for OCR): {adam}")
            elif note in ("no_attachment", "no_text", "libs_missing"):
                # tried and got nothing → mark so a re-run skips it.
                repo.mark_full_text_attempted_empty(adam, note)
                n["empty"] += 1
            else:  # 'error' / 'exists' / anything else
                n["error" if note == "error" else "empty"] += 1
            if di.INGEST_JOB_ID:
                # procurement_act.type is already a readable act_type enum
                # (notice/contract/…), which type_label renders directly.
                repo.log_act(di.INGEST_JOB_ID, adam, str(act_type), title,
                             "updated", extracted, chars, note)
            db.commit()  # commit each act → resumable
            if i % 100 == 0 or i == len(rows):
                print(f"  {i:>6}/{len(rows)}  stored={n['stored']} "
                      f"garbled={n['garbled']} empty={n['empty']} error={n['error']}")
      except BaseException:
        final_status = "error"
        raise
      finally:
        _finalize_job(db, final_status)
    print(f"\ndiavgeia full-text backfill: stored={n['stored']} "
          f"garbled={n['garbled']} empty={n['empty']} error={n['error']}")


def cmd_diavgeia_resolve(args):
    """Resolve Diavgeia references decoupled from ingest: authority dedup (by ΑΦΜ)
    always; signer/unit dictionary labels with --dictionaries (one API call per
    new uid — can be large). Idempotent and re-runnable."""
    import diavgeia_ingest as di
    with Database() as db:
        repo = di.DiavgeiaRepository(db, di.DiavgeiaClient())
        n = repo.resolve_authorities()
        print(f"authorities: resolved {n} distinct organizations.")
        if args.dictionaries:
            ns, nu = repo.resolve_dictionaries()
            print(f"dictionaries: {ns} signers, {nu} units labelled.")


def cmd_diavgeia_project(args):
    """Project Diavgeia decisions into proc.procurement_act (+ reused child
    tables) so the web app surfaces them like KHMDHS acts. Set-based, idempotent,
    and never touches acts a curator has taken ownership of (origin='authored')."""
    import diavgeia_ingest as di
    with Database() as db:
        n = di.DiavgeiaRepository(db).project_all()
        print(f"projected — {n} Diavgeia acts present in proc.procurement_act.")


def cmd_load_postal_nuts(args):
    """Load the Greek postal-code → NUTS-3 mapping into proc.postal_nuts.

    The Eurostat CSV (data/pc2025_EL_NUTS-2024_v1.0.csv) is BOM-prefixed,
    ';'-delimited and single-quoted ('NUTS3';'CODE'). Idempotent upsert; rows
    whose NUTS code isn't in proc.nuts_code are skipped (and reported)."""
    import csv
    path = args.file or os.path.join(HERE, "data", "pc2025_EL_NUTS-2024_v1.0.csv")
    if not os.path.exists(path):
        sys.exit(f"file not found: {path}")
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader, None)                     # header: NUTS3;CODE
        for r in reader:
            if len(r) < 2:
                continue
            nuts = r[0].strip().strip("'").strip()
            code = r[1].strip().strip("'").strip()
            if nuts and code:
                rows.append((code, nuts))
    inserted = skipped = 0
    with Database() as db:
        valid = {r[0] for r in db.query("SELECT nuts_code FROM proc.nuts_code")}
        for code, nuts in rows:
            if nuts not in valid:
                skipped += 1
                continue
            db.execute("""INSERT INTO proc.postal_nuts (postal_code, nuts_code)
                          VALUES (%s,%s)
                          ON CONFLICT (postal_code) DO UPDATE
                            SET nuts_code=EXCLUDED.nuts_code""", (code, nuts))
            inserted += 1
        db.commit()
    print(f"postal_nuts loaded from {path}: {inserted} upserted, "
          f"{skipped} skipped (NUTS not in proc.nuts_code).")


def _diavgeia_watermark(db, decision_type: str):
    import diavgeia_ingest as di
    return di.watermark(db, decision_type)


def cmd_diavgeia_catchup(args):
    """Incremental Diavgeia fetch since last run, per decision type.

    start = (latest done window end − overlap days), end = today. Types never
    backfilled have no watermark and need an explicit --start (we refuse to
    silently fetch all of history)."""
    import diavgeia_ingest as di

    end = dt.date.today()
    overlap = dt.timedelta(days=args.overlap_days)
    names = args.types or di.TYPE_NAMES
    explicit_start = dt.date.fromisoformat(args.start) if args.start else None

    with Database() as db:
        client = di.DiavgeiaClient()
        repo = di.DiavgeiaRepository(db, client)
        totals = {"windows": 0, "done": 0, "skipped": 0, "errored": 0}
        any_run = False
        for name in names:
            uid = di.NAME_TO_UID[name]
            wm = di.watermark(db, uid)
            if wm is not None:
                start = wm - overlap
                origin = f"watermark {wm} − {args.overlap_days}d"
            elif explicit_start is not None:
                start = explicit_start
                origin = f"--start (no prior history for {name})"
            else:
                print(f"\n=== {name}: SKIPPED — never backfilled and no --start "
                      f"given. Run diavgeia-backfill first, or pass --start. ===")
                continue
            if start > end:
                start = end
            print(f"\n=== catching up diavgeia {name} ({uid}): {start} .. {end}"
                  f"  ({origin}) ===")
            s = di.ingest_type(client, repo, uid, start, end, resume=False)
            for k in totals:
                totals[k] += s[k]
            any_run = True

        if any_run:
            # Resolve authorities for any new orgs and project into
            # procurement_act, so caught-up acts immediately show their authority
            # in the web app (mirrors what diavgeia-backfill does at the end).
            print("\n=== resolving authorities (dedupe by ΑΦΜ / name) ===")
            n = repo.resolve_authorities()
            print(f"  resolved {n} distinct organizations into proc.authority")
            print("=== projecting into procurement_act (app-facing) ===")
            m = repo.project_all()
            print(f"  {m} Diavgeia acts present in proc.procurement_act")
            print("\nnew watermarks (latest done window end per type):")
            for name in names:
                wm = di.watermark(db, di.NAME_TO_UID[name])
                print(f"  {name:9s} {wm if wm else '—'}")

    print(f"\ndiavgeia catch-up complete. windows={totals['windows']} "
          f"done={totals['done']} skipped={totals['skipped']} "
          f"errored={totals['errored']}")
    return totals


# --------------------------------------------------------------------------- #
# TED (third source) — mirrors the Diavgeia commands.
# --------------------------------------------------------------------------- #
def cmd_ted_backfill(args):
    """Backfill TED notices (Search API, ITERATION) for a buyer-country over a
    publication-date range → proc.ted_notice, windowed in proc.ted_ingest_window.
    Projects a digest into procurement_act unless --skip-project."""
    if getattr(args, "fulltext", False):
        os.environ["EXTRACT_FULLTEXT"] = "1"
    import ted_ingest as ti
    if getattr(args, "fulltext", False):
        ti.EXTRACT_FULLTEXT = True          # in case the module was already imported
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    with Database() as db:
        final_status = "done"
        try:
            client, repo = ti.TedClient(), ti.TedRepository(db)
            print(f"\n=== TED {args.country}: {start} .. {end}"
                  f"{' (resume)' if args.resume else ''}"
                  f"{' +fulltext' if ti.EXTRACT_FULLTEXT else ''} ===")
            s = ti.ingest_country(client, repo, args.country, start, end, resume=args.resume)
            if not args.skip_project:
                print("\n=== projecting into procurement_act (app-facing) ===")
                n = ti.project_all(db)
                print(f"  {n} TED notices present; digest projected into procurement_act")
        except BaseException:
            final_status = "error"
            raise
        finally:
            _finalize_job(db, final_status)   # no-op unless launched via admin
    print(f"\nTED backfill complete. windows={s['windows']} done={s['done']} "
          f"skipped={s['skipped']} errored={s['errored']} notices={s['notices']}")
    if s["errored"]:
        print("  (errored windows recorded status='error' in proc.ted_ingest_window; "
              "re-run with --resume to retry them.)")
    return s


def cmd_ted_catchup(args):
    """Incremental TED fetch since last run: start = (watermark − overlap), end =
    today. No prior history requires an explicit --start."""
    import ted_ingest as ti
    end = dt.date.today()
    overlap = dt.timedelta(days=args.overlap_days)
    explicit_start = dt.date.fromisoformat(args.start) if args.start else None
    with Database() as db:
        client, repo = ti.TedClient(), ti.TedRepository(db)
        wm = ti.watermark(db, args.country)
        if wm is not None:
            start, origin = wm - overlap, f"watermark {wm} − {args.overlap_days}d"
        elif explicit_start is not None:
            start, origin = explicit_start, "--start (no prior history)"
        else:
            sys.exit("TED never backfilled and no --start given. "
                     "Run ted-backfill first, or pass --start.")
        if start > end:
            start = end
        print(f"\n=== catching up TED {args.country}: {start} .. {end}  ({origin}) ===")
        s = ti.ingest_country(client, repo, args.country, start, end, resume=False)
        if not args.skip_project:
            m = ti.project_all(db)
            print(f"  projected; {m} TED notices present")
        wm2 = ti.watermark(db, args.country)
        print(f"  new watermark: {wm2 if wm2 else '—'}")
    print(f"\nTED catch-up complete. windows={s['windows']} done={s['done']} "
          f"skipped={s['skipped']} errored={s['errored']}")
    return s


def cmd_ted_project(args):
    """Project TED notices into proc.procurement_act (idempotent; never touches
    origin='authored')."""
    import ted_ingest as ti
    with Database() as db:
        n = ti.project_all(db)
        print(f"projected — {n} TED notices present in proc.ted_notice / procurement_act.")


def cmd_ted_fulltext_backfill(args):
    """Fetch + store full text for TED notices ALREADY imported without it
    (parse each notice's eForms XML). Bounded by --limit, resumable; projects the
    text into procurement_act. TED counterpart of diavgeia-fulltext-backfill."""
    import ted_ingest as ti
    with Database() as db:
        final_status = "done"
        try:
            client, repo = ti.TedClient(), ti.TedRepository(db)
            n = ti.fulltext_pass(client, repo, limit=args.limit)
            print(f"TED full text: {n['extracted']} extracted, {n['empty']} empty "
                  f"(of {n['seen']} tried)")
            m = ti.project_all(db)
            print(f"  projected; {m} TED notices present")
        except BaseException:
            final_status = "error"
            raise
        finally:
            _finalize_job(db, final_status)   # no-op unless launched via admin


def cmd_create_user(args):
    """Create an application account (proc.app_user) — used to bootstrap the
    first admin on a fresh DB / prod, before the /admin/users UI is reachable.
    Password is read from --password or prompted (hidden). Hashing/validation
    reuse app/auth.py."""
    import getpass
    _app = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
    if _app not in sys.path:
        sys.path.insert(0, _app)
    try:
        from app.auth import hash_password, username_ok, password_ok
    except ImportError:
        from auth import hash_password, username_ok, password_ok

    username = (args.username or "").strip()
    role = args.role
    if not username_ok(username):
        sys.exit("invalid username (3–40 chars: letters, digits, . _ - @)")
    if role not in ("admin", "customer"):
        sys.exit("role must be admin|customer")
    pw = args.password or getpass.getpass("Password: ")
    if not password_ok(pw):
        sys.exit("password must be 8–200 characters")
    email = (args.email or "").strip() or None
    with Database() as db:
        if db.query("SELECT 1 FROM proc.app_user WHERE lower(username)=lower(%s)",
                    (username,)):
            sys.exit(f"user {username!r} already exists")
        db.execute(
            "INSERT INTO proc.app_user (username, email, password_hash, role) "
            "VALUES (%s,%s,%s,%s)",
            (username, email, hash_password(pw), role))
        db.commit()
    print(f"created {role} account: {username}")


def cmd_grant_product(args):
    """Grant a subscription product (test/paid) to an existing account — CLI
    parity with the /admin/users grant button (handy on prod). Uses the product
    default period unless --days overrides it."""
    username = (args.username or "").strip()
    with Database() as db:
        rows = db.query("SELECT id FROM proc.app_user WHERE lower(username)=lower(%s)",
                        (username,))
        if not rows:
            sys.exit(f"user {username!r} not found")
        uid = rows[0][0]
        prod = db.query("SELECT default_period_days FROM proc.product "
                        "WHERE code=%s AND is_active", (args.product,))
        if not prod:
            sys.exit(f"unknown or inactive product {args.product!r}")
        days = args.days or int(prod[0][0])
        if days <= 0:
            sys.exit("period must be a positive number of days")
        # One product active at a time: expire any current grant first.
        db.execute("UPDATE proc.user_subscription SET expires_at = now() "
                   "WHERE user_id = %s AND expires_at > now()", (uid,))
        db.execute(
            "INSERT INTO proc.user_subscription (user_id, product_code, expires_at, granted_by) "
            "VALUES (%s, %s, now() + (%s * interval '1 day'), NULL)",
            (uid, args.product, days))
        db.commit()
    print(f"granted {args.product} ({days}d) to {username}")


def cmd_stats(args):
    with Database() as db:
        rows = db.query("""
            SELECT type, count(*) FROM proc.procurement_act GROUP BY type ORDER BY type
        """)
        print("procurement_act by type:")
        for t, c in rows:
            print(f"  {t:9s} {c:>12,}")
        for tbl in ("authority", "economic_operator", "act_link",
                    "act_object_detail", "act_operator"):
            (n,) = db.query(f"SELECT count(*) FROM proc.{tbl}")[0]
            print(f"  {tbl:18s} {n:>12,}")

        # Diavgeia source (skip quietly if the migration hasn't been applied).
        try:
            rows = db.query("""SELECT decision_type, count(*) FROM proc.diavgeia_decision
                               GROUP BY decision_type ORDER BY decision_type""")
        except Exception:
            db.rollback()
            return
        print("\ndiavgeia_decision by type:")
        for t, c in rows:
            print(f"  {t:9s} {c:>12,}")
        for tbl in ("diavgeia_decision_cpv", "diavgeia_decision_person",
                    "diavgeia_related"):
            (n,) = db.query(f"SELECT count(*) FROM proc.{tbl}")[0]
            print(f"  {tbl:24s} {n:>12,}")

        # TED source (skip quietly if the migration hasn't been applied).
        try:
            (n,) = db.query("SELECT count(*) FROM proc.ted_notice")[0]
        except Exception:
            db.rollback()
            return
        print(f"\nted_notice {n:>26,}")
        (nc,) = db.query("SELECT count(*) FROM proc.ted_notice_cpv")[0]
        print(f"  ted_notice_cpv {nc:>18,}")


def cmd_progress(args):
    with Database() as db:
        if args.errors_only:
            sql = """SELECT act_type, date_from, date_to, status, last_error
                     FROM proc.ingest_window
                     WHERE status='error' """
            params = []
            if args.type:
                sql += " AND act_type=%s"
                params.append(args.type)
            sql += " ORDER BY act_type, date_from"
            rows = db.query(sql, tuple(params))
            if not rows:
                print("no errored windows.")
                return
            for t, df, dt_, _s, err in rows:
                print(f"  {t:9s} {df} .. {dt_}  ERROR: {err}")
            return

        # summary by (type, status)
        sql = """SELECT act_type, status, count(*), min(date_from), max(date_to)
                 FROM proc.ingest_window """
        params = []
        if args.type:
            sql += " WHERE act_type=%s"
            params.append(args.type)
        sql += " GROUP BY act_type, status ORDER BY act_type, status"
        rows = db.query(sql, tuple(params))
        if not rows:
            print("no ingest_window rows yet — run a backfill first.")
            return
        cur_type = None
        for t, status, n, mn, mx in rows:
            if t != cur_type:
                print(f"\n[{t}]")
                cur_type = t
            print(f"  {status:8s} {n:>5}   ({mn} .. {mx})")


def cmd_clear_mfa(args):
    """Break-glass: turn OFF two-factor for a user (e.g. a locked-out admin who
    lost their authenticator and recovery codes). Run by an operator with DB
    access; the user can re-enrol afterwards."""
    with Database() as db:
        rows = db.query("SELECT id, username, mfa_enabled FROM proc.app_user "
                        "WHERE lower(username) = lower(%s)", (args.user,))
        if not rows:
            print(f"no such user: {args.user!r}")
            return
        uid, uname, enabled = rows[0]
        db.execute("UPDATE proc.app_user SET mfa_enabled = false, mfa_secret = NULL, "
                   "mfa_recovery_codes = '{}' WHERE id = %s", (uid,))
        db.commit()
        print(f"2FA cleared for {uname!r} (was enabled={enabled}). They can re-enrol at /account/mfa.")


def main():
    ap = argparse.ArgumentParser(description="KHMDHS database bootstrap & runner.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_mfa = sub.add_parser("clear-mfa", help="break-glass: disable 2FA for a user")
    p_mfa.add_argument("--user", required=True, help="username to clear 2FA for")
    p_mfa.set_defaults(func=cmd_clear_mfa)

    p_init = sub.add_parser("init-schema", help="apply schema.sql to the database")
    p_init.add_argument("--file", help="path to schema.sql (default: alongside db.py)")
    p_init.set_defaults(func=cmd_init_schema)

    p_bf = sub.add_parser("backfill", help="harvest a date range into the database")
    p_bf.add_argument("--start", required=True, help="YYYY-MM-DD")
    p_bf.add_argument("--end", help="YYYY-MM-DD (default: today)")
    p_bf.add_argument("--types", nargs="+",
                      choices=["request", "notice", "auction", "contract", "payment"],
                      help="subset of act types (default: all five)")
    p_bf.add_argument("--resume", action="store_true",
                      help="skip windows already marked 'done'; retry running/error/pending")
    p_bf.set_defaults(func=cmd_backfill)

    p_ft = sub.add_parser("fulltext-backfill",
                          help="extract & store full text for already-imported "
                               "acts that don't have it yet (resumable, --limit)")
    p_ft.add_argument("--types", nargs="+",
                      choices=["request", "notice", "auction", "contract", "payment"],
                      help="subset of act types (default: all five)")
    p_ft.add_argument("--limit", type=int, default=5000,
                      help="max acts to attempt this run (default: 5000). "
                           "Re-run to continue; it's resumable.")
    p_ft.add_argument("--start", help="YYYY-MM-DD; only acts on/after this date")
    p_ft.add_argument("--end", help="YYYY-MM-DD; only acts on/before this date")
    p_ft.set_defaults(func=cmd_fulltext_backfill)

    p_dft = sub.add_parser("diavgeia-fulltext-backfill",
                           help="extract & store full text for already-imported "
                                "DIAVGEIA acts that don't have it yet "
                                "(resumable, --limit)")
    p_dft.add_argument("--limit", type=int, default=5000,
                       help="max acts to attempt this run (default: 5000). "
                            "Re-run to continue; it's resumable.")
    p_dft.add_argument("--start", help="YYYY-MM-DD; only acts on/after this date")
    p_dft.add_argument("--end", help="YYYY-MM-DD; only acts on/before this date")
    p_dft.set_defaults(func=cmd_diavgeia_fulltext_backfill)

    p_cu = sub.add_parser("catchup",
                          help="incremental fetch since last run (per type), "
                               "with an overlap buffer for late records")
    p_cu.add_argument("--types", nargs="+",
                      choices=["request", "notice", "auction", "contract", "payment"],
                      help="subset of act types (default: all five)")
    p_cu.add_argument("--overlap-days", type=int, default=7,
                      help="re-fetch this many days before the watermark "
                           "(default: 7) to catch late/backdated records")
    p_cu.add_argument("--start", help="YYYY-MM-DD; used only for types that have "
                      "never been backfilled (no watermark)")
    p_cu.set_defaults(func=cmd_catchup)

    p_dbf = sub.add_parser("diavgeia-backfill",
                           help="harvest Diavgeia decisions (notice/award/contract) "
                                "for an issueDate range")
    p_dbf.add_argument("--start", required=True, help="YYYY-MM-DD (issue date)")
    p_dbf.add_argument("--end", help="YYYY-MM-DD (default: today)")
    p_dbf.add_argument("--types", nargs="+",
                       choices=["notice", "award", "contract"],
                       help="subset of decision types (default: all three)")
    p_dbf.add_argument("--resume", action="store_true",
                       help="skip windows already marked 'done'; retry running/error/pending")
    p_dbf.add_argument("--skip-resolve", action="store_true",
                       help="don't run authority dedup after the backfill "
                            "(defer it to `diavgeia-resolve`)")
    p_dbf.add_argument("--skip-project", action="store_true",
                       help="don't project into procurement_act after the backfill "
                            "(defer it to `diavgeia-project`)")
    p_dbf.set_defaults(func=cmd_diavgeia_backfill)

    p_dr = sub.add_parser("diavgeia-resolve",
                          help="resolve Diavgeia authorities (dedupe by ΑΦΜ) and, "
                               "with --dictionaries, signer/unit labels")
    p_dr.add_argument("--dictionaries", action="store_true",
                      help="also fetch signer/unit labels (one API call per new "
                           "uid; can be large)")
    p_dr.set_defaults(func=cmd_diavgeia_resolve)

    p_dp = sub.add_parser("diavgeia-project",
                          help="project Diavgeia decisions into procurement_act so "
                               "the web app surfaces them (idempotent)")
    p_dp.set_defaults(func=cmd_diavgeia_project)

    p_pn = sub.add_parser("load-postal-nuts",
                          help="load the Greek postal-code → NUTS-3 mapping into "
                               "proc.postal_nuts (idempotent)")
    p_pn.add_argument("--file", help="path to the CSV (default: data/pc2025_EL_NUTS-2024_v1.0.csv)")
    p_pn.set_defaults(func=cmd_load_postal_nuts)

    p_dcu = sub.add_parser("diavgeia-catchup",
                           help="incremental Diavgeia fetch since last run (per type), "
                                "with an overlap buffer for late records")
    p_dcu.add_argument("--types", nargs="+",
                       choices=["notice", "award", "contract"],
                       help="subset of decision types (default: all three)")
    p_dcu.add_argument("--overlap-days", type=int, default=7,
                       help="re-fetch this many days before the watermark (default: 7)")
    p_dcu.add_argument("--start", help="YYYY-MM-DD; used only for types that have "
                       "never been backfilled (no watermark)")
    p_dcu.set_defaults(func=cmd_diavgeia_catchup)

    p_tbf = sub.add_parser("ted-backfill",
                           help="harvest TED notices (Search API, ITERATION) for a "
                                "publication-date range")
    p_tbf.add_argument("--start", required=True, help="YYYY-MM-DD (publication date)")
    p_tbf.add_argument("--end", help="YYYY-MM-DD (default: today)")
    p_tbf.add_argument("--country", default="GRC", help="TED buyer-country (default: GRC)")
    p_tbf.add_argument("--resume", action="store_true",
                       help="skip windows already marked 'done'")
    p_tbf.add_argument("--fulltext", action="store_true",
                       help="also fetch + parse each notice's XML for its "
                            "description + full text (slower, more requests)")
    p_tbf.add_argument("--skip-project", action="store_true",
                       help="don't project into procurement_act (defer to ted-project)")
    p_tbf.set_defaults(func=cmd_ted_backfill)

    p_tft = sub.add_parser("ted-fulltext-backfill",
                           help="fetch full text for TED notices already imported "
                                "without it (parse the eForms XML)")
    p_tft.add_argument("--limit", type=int, default=5000,
                       help="max notices per run (default: 5000)")
    p_tft.set_defaults(func=cmd_ted_fulltext_backfill)

    p_tcu = sub.add_parser("ted-catchup",
                           help="incremental TED fetch since last run (watermark − overlap)")
    p_tcu.add_argument("--country", default="GRC")
    p_tcu.add_argument("--overlap-days", type=int, default=7,
                       help="re-fetch this many days before the watermark (default: 7)")
    p_tcu.add_argument("--start", help="YYYY-MM-DD; used only if no prior history")
    p_tcu.add_argument("--skip-project", action="store_true")
    p_tcu.set_defaults(func=cmd_ted_catchup)

    p_tp = sub.add_parser("ted-project",
                          help="project TED notices into procurement_act (idempotent)")
    p_tp.set_defaults(func=cmd_ted_project)

    p_et = sub.add_parser("extract-tables",
                          help="report-only table extraction over a job's acts")
    p_et.add_argument("--job", required=True, help="proc.table_extract_job id")
    p_et.set_defaults(func=cmd_extract_tables)

    p_uc = sub.add_parser("create-user",
                          help="create an app account (bootstrap the first admin)")
    p_uc.add_argument("--username", required=True)
    p_uc.add_argument("--role", default="admin", choices=["admin", "customer"])
    p_uc.add_argument("--email")
    p_uc.add_argument("--password", help="omit to be prompted (hidden input)")
    p_uc.set_defaults(func=cmd_create_user)

    p_gp = sub.add_parser("grant-product",
                          help="grant a subscription product (test/paid) to a user")
    p_gp.add_argument("--username", required=True)
    p_gp.add_argument("--product", required=True, choices=["test", "paid"])
    p_gp.add_argument("--days", type=int,
                      help="override the product's default period (days)")
    p_gp.set_defaults(func=cmd_grant_product)

    p_st = sub.add_parser("stats", help="print row counts")
    p_st.set_defaults(func=cmd_stats)

    p_pr = sub.add_parser("progress",
                          help="show backfill window status (pending/running/done/error)")
    p_pr.add_argument("--type",
                      choices=["request", "notice", "auction", "contract", "payment"],
                      help="filter by act type")
    p_pr.add_argument("--errors-only", action="store_true",
                      help="only show windows with status='error', with last_error")
    p_pr.set_defaults(func=cmd_progress)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
