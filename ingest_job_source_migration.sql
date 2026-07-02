-- ingest_job_source_migration.sql
--
-- Tag each backfill job with which harvester ran it, so the admin Data
-- Collection UI can launch and monitor BOTH the KHMDHS harvest (db.py backfill)
-- and the Diavgeia harvest (db.py diavgeia-backfill) through the same job
-- infrastructure (proc.ingest_job + the job-detail page + cancel/reconcile).
--
-- The window-level progress a job reads depends on this column:
--   source='khmdhs'   -> proc.ingest_window       (act_type-keyed)
--   source='diavgeia' -> proc.diavgeia_ingest_window (decision_type-keyed)
--
-- Additive and safe: existing rows default to 'khmdhs', so behaviour is
-- unchanged until a Diavgeia job is launched. Apply to local now; apply to
-- production before the admin page is served there (the page SELECTs source).

BEGIN;

ALTER TABLE proc.ingest_job
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'khmdhs';

COMMENT ON COLUMN proc.ingest_job.source IS
    'which harvester this job ran: khmdhs | diavgeia';

COMMIT;
