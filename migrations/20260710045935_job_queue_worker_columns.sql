-- migrations/20260710045935_job_queue_worker_columns.sql
-- job queue worker columns
--
-- Move admin-launched jobs from "web process spawns a detached subprocess on its
-- own container" to a DB queue drained by a separate worker (worker.py). Both
-- job tables gain the same set of columns:
--   command       — the db.py argv (after the interpreter + db.py path) to run
--   job_env       — per-run env overrides (e.g. EXTRACT_FULLTEXT, INGEST_JOB_ID)
--   log_text      — stdout/stderr streamed here (replaces the local log file, so
--                   the job page can read it across containers)
--   worker_id     — which worker claimed the job (host:pid), for debugging
--   heartbeat_at  — worker bumps this while running; liveness without a local PID
--   cancel_requested — the UI sets this; the worker sees it and kills its child
--   queued_at     — when the row was enqueued
-- status gains a 'queued' value (the column is free text, no CHECK to alter).
-- The legacy pid / log_path columns stay (nullable) so old rows still render.
--
-- Wrap the body so a failure leaves nothing half-applied. Idempotent.

BEGIN;

ALTER TABLE proc.ingest_job
    ADD COLUMN IF NOT EXISTS command          text[],
    ADD COLUMN IF NOT EXISTS job_env          jsonb,
    ADD COLUMN IF NOT EXISTS log_text         text,
    ADD COLUMN IF NOT EXISTS worker_id        text,
    ADD COLUMN IF NOT EXISTS heartbeat_at     timestamptz,
    ADD COLUMN IF NOT EXISTS cancel_requested boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS queued_at        timestamptz;

ALTER TABLE proc.table_extract_job
    ADD COLUMN IF NOT EXISTS command          text[],
    ADD COLUMN IF NOT EXISTS job_env          jsonb,
    ADD COLUMN IF NOT EXISTS log_text         text,
    ADD COLUMN IF NOT EXISTS worker_id        text,
    ADD COLUMN IF NOT EXISTS heartbeat_at     timestamptz,
    ADD COLUMN IF NOT EXISTS cancel_requested boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS queued_at        timestamptz,
    ADD COLUMN IF NOT EXISTS exit_code        integer;  -- ingest_job already has this

-- The worker claims the oldest queued row (FOR UPDATE SKIP LOCKED); these keep
-- that scan cheap and index the "is anything queued/running?" guard.
CREATE INDEX IF NOT EXISTS ix_ingest_job_queued
    ON proc.ingest_job (id) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS ix_table_extract_job_queued
    ON proc.table_extract_job (id) WHERE status = 'queued';

COMMIT;
