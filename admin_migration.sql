-- ============================================================================
-- Migration: ingest_job table for the admin UI's launcher.
-- Run once:  docker exec -i khmdhs-pg psql -U postgres -d procurement < admin_migration.sql
-- Safe to re-run.
-- ============================================================================

CREATE TABLE IF NOT EXISTS proc.ingest_job (
    id          bigserial PRIMARY KEY,
    pid         integer,                  -- OS PID of the subprocess (null if not running)
    status      text NOT NULL DEFAULT 'running',
                                          -- running | done | error | cancelled | stale
    types       text[] NOT NULL,          -- act types this job targets
    date_from   date NOT NULL,
    date_to     date NOT NULL,
    resume      boolean NOT NULL DEFAULT false,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    exit_code   integer,                  -- subprocess exit code if it terminated
    log_path    text,                     -- where stdout/stderr was written
    last_error  text                      -- short message if status='error'
);

CREATE INDEX IF NOT EXISTS ix_ingest_job_started
    ON proc.ingest_job (started_at DESC);
CREATE INDEX IF NOT EXISTS ix_ingest_job_status
    ON proc.ingest_job (status);
