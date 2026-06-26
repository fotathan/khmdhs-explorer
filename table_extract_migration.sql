-- table_extract_migration.sql
-- ===========================================================================
-- Mass table-extraction jobs (Phase 1: report-only). A job runs the table
-- extractor over a filtered set of acts and records, per act, what was found:
-- extracted / garbled / needs_ocr / no_tables / no_attachment / error. Mirrors
-- the backfill-job machinery (detached subprocess, per-act log, lifecycle) but
-- self-contained so it never entangles proc.ingest_job.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f table_extract_migration.sql
--   psql "<supabase-direct-url>" -f table_extract_migration.sql
-- ===========================================================================

CREATE TABLE IF NOT EXISTS proc.table_extract_job (
    id           bigserial PRIMARY KEY,
    status       text NOT NULL DEFAULT 'running',  -- running|done|error|cancelled|stale
    filter_desc  text,                             -- human description of the filter
    total_acts   integer,                          -- size of the targeted set
    save_tables  boolean NOT NULL DEFAULT false,   -- Phase 2; Phase 1 = report-only
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    pid          integer,
    log_path     text,
    last_error   text
);

-- The materialised ΑΔΑΜ worklist for a job (so the runner reads it from the DB
-- instead of a huge argv). Removed with the job.
CREATE TABLE IF NOT EXISTS proc.table_extract_target (
    job_id  integer NOT NULL REFERENCES proc.table_extract_job(id) ON DELETE CASCADE,
    adam    text NOT NULL,
    ord     integer NOT NULL DEFAULT 0,
    done    boolean NOT NULL DEFAULT false,
    PRIMARY KEY (job_id, adam)
);

-- One row per processed act.
CREATE TABLE IF NOT EXISTS proc.table_extract_log (
    id         bigserial PRIMARY KEY,
    job_id     integer NOT NULL REFERENCES proc.table_extract_job(id) ON DELETE CASCADE,
    adam       text NOT NULL,
    act_type   text,
    title      text,
    outcome    text NOT NULL,   -- extracted|garbled|needs_ocr|no_tables|no_attachment|error
    n_tables   integer,         -- tables found (when extracted/garbled)
    n_files    integer,         -- attachment files inspected
    note       text,
    logged_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_tel_job ON proc.table_extract_log (job_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_tet_job ON proc.table_extract_target (job_id, ord);

ANALYZE proc.table_extract_job;
