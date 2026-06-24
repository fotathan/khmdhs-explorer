-- ingest_act_log_migration.sql
-- ===========================================================================
-- Per-act transparency log for admin-launched imports.
--
-- Each act processed by a backfill job (proc.ingest_job) gets one row here:
-- which ΑΔΑΜ, what happened (new / updated / skipped because authored), whether
-- full text was extracted (and how many chars), and when. The admin job page
-- renders this with a direct link to each act, and it is kept for review /
-- documentation of what a given run actually did.
--
-- Only runs launched through the admin UI write here: the runner writes a row
-- only when it is told its job id via the INGEST_JOB_ID env var (set by
-- app/admin.py). A plain CLI backfill with no job id writes nothing, so this is
-- opt-in and never slows the shell path.
--
-- Volume: one row per act per run. A wide date-range backfill can be tens of
-- thousands of rows — bounded by the date range the curator chooses. Rows are
-- removed automatically when their parent job row is deleted (ON DELETE CASCADE).
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f ingest_act_log_migration.sql
--   psql "<supabase-direct-url>" -f ingest_act_log_migration.sql
-- ===========================================================================

CREATE TABLE IF NOT EXISTS proc.ingest_act_log (
    id                   bigserial PRIMARY KEY,
    job_id               integer NOT NULL
                            REFERENCES proc.ingest_job(id) ON DELETE CASCADE,
    adam                 text    NOT NULL,
    act_type             text,
    title                text,
    -- what the upsert did to this act:
    --   'new'              inserted for the first time
    --   'updated'          existing imported act refreshed
    --   'skipped_authored' left untouched (curator-owned, origin='authored')
    action               text    NOT NULL,
    -- full-text extraction outcome for this act on this run:
    full_text_extracted  boolean NOT NULL DEFAULT false,
    full_text_chars      integer,          -- length of text when extracted/seen
    full_text_note       text,             -- 'extracted' | 'no_attachment' |
                                           -- 'no_text' | 'exists' | 'libs_missing'
                                           -- | 'error' | 'disabled' | 'authored'
    logged_at            timestamptz NOT NULL DEFAULT now()
);

-- Job page reads rows for one job, newest first.
CREATE INDEX IF NOT EXISTS ix_ingest_act_log_job
    ON proc.ingest_act_log (job_id, id DESC);
-- "where has this act shown up across runs" lookups.
CREATE INDEX IF NOT EXISTS ix_ingest_act_log_adam
    ON proc.ingest_act_log (adam);
