-- migrations/20260917130323_tender_service_duplicates.sql
-- Tender Service duplicate handling (docs/specs/tender-service-duplicates.md)
-- — the CORE part: everything the web app reads. It does NOT need the Tender
-- Service tables, so it runs on a database that has never had them
-- (production). The tsg_record columns are the next migration,
-- 20260917130324_tender_service_duplicates_tsg_record.sql.
--
-- Paste-safe: no DO $$ … $$ blocks (the Supabase dashboard editor splits them
-- apart and the whole script fails to parse). Every statement is idempotent on
-- its own; the foreign key is dropped and re-added, which is instant because it
-- is NOT VALID.
--
-- 1. procurement_act.duplicate_of — a Tender Service act that is the same
--    tender as another act is HIDDEN (kept, pointing at the act we show),
--    never deleted: a delete cascades through the emailed history, reminder
--    ledgers, favourites and notes, and breaks links already sent.
--    Nullable, no default: instant on ~2.9M rows. The foreign key is NOT VALID
--    because every existing row is NULL (nothing to check) and validating would
--    scan the table; it is still enforced for every new value. ON DELETE SET
--    NULL is how a hidden act comes back when the act it pointed at is deleted;
--    the partial index keeps that delete (and the search filter) off the heap.
-- 2. tsg_match_run — one row per matching pass: counts and warnings.
-- 3. duplicate_candidate — the review queue for possible duplicates; an
--    admin's confirm/reject lives here and survives every re-import.
-- 4. digest_run_item — the duplicate label as it was SENT.
-- 5. match_setting — thresholds (the table Interconnection already uses).
--
-- The partial index build reads the whole act table once (seconds locally).
-- Run on the direct connection (port 5432), not the pooler.

BEGIN;

-- 1 ------------------------------------------------------------------------
ALTER TABLE proc.procurement_act ADD COLUMN IF NOT EXISTS duplicate_of text;

ALTER TABLE proc.procurement_act DROP CONSTRAINT IF EXISTS procurement_act_duplicate_of_fkey;
ALTER TABLE proc.procurement_act
    ADD CONSTRAINT procurement_act_duplicate_of_fkey
    FOREIGN KEY (duplicate_of) REFERENCES proc.procurement_act(adam)
    ON DELETE SET NULL NOT VALID;

CREATE INDEX IF NOT EXISTS ix_act_duplicate_of ON proc.procurement_act (duplicate_of)
    WHERE duplicate_of IS NOT NULL;

-- 2 ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.tsg_match_run (
    id           bigserial PRIMARY KEY,
    trigger      text NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    job_id       bigint REFERENCES proc.ingest_job(id) ON DELETE SET NULL,
    counts       jsonb NOT NULL DEFAULT '{}',
    warnings     jsonb NOT NULL DEFAULT '[]'
);

-- 3 ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.duplicate_candidate (
    id             bigserial PRIMARY KEY,
    adam           text NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    candidate_adam text NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    tier           smallint NOT NULL CHECK (tier BETWEEN 1 AND 3),
    rank           smallint NOT NULL DEFAULT 1,
    signals        jsonb NOT NULL DEFAULT '{}',
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'confirmed', 'rejected', 'superseded')),
    first_found_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    decided_by     bigint REFERENCES proc.app_user(id) ON DELETE SET NULL,
    decided_at     timestamptz,
    found_by_run   bigint REFERENCES proc.tsg_match_run(id) ON DELETE SET NULL,
    CONSTRAINT duplicate_candidate_pair UNIQUE (adam, candidate_adam),
    CONSTRAINT duplicate_candidate_not_self CHECK (adam <> candidate_adam)
);
CREATE INDEX IF NOT EXISTS ix_duplicate_candidate_pending
    ON proc.duplicate_candidate (adam, rank) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS ix_duplicate_candidate_candidate
    ON proc.duplicate_candidate (candidate_adam);
CREATE INDEX IF NOT EXISTS ix_duplicate_candidate_queue
    ON proc.duplicate_candidate (status, tier);

-- 4 ------------------------------------------------------------------------
ALTER TABLE proc.digest_run_item
    ADD COLUMN IF NOT EXISTS dup_candidate_adam text,
    ADD COLUMN IF NOT EXISTS dup_tier smallint;

-- 5 ------------------------------------------------------------------------
INSERT INTO proc.match_setting (key, value) VALUES
    ('tsg_title_min', 50),                -- title similarity × 100 that counts
    ('tsg_deadline_days', 1),             -- candidate block: deadline ± days
    ('tsg_exact_deadline_days', 3),       -- an exact number further apart only flags
    ('tsg_budget_tolerance_cents', 50),
    ('tsg_round_budget_candidates', 5),   -- a round budget alone counts only up to this many candidates
    ('tsg_recheck_days', 45)              -- how long a shown act keeps being re-checked
ON CONFLICT (key) DO NOTHING;

COMMENT ON COLUMN proc.procurement_act.duplicate_of IS
    'Set on a Tender Service act that is the same tender as this act: hidden from '
    'search, alerts and sitemaps; /act redirects. See tsg_match.py.';
COMMENT ON TABLE proc.duplicate_candidate IS
    'Possible duplicates found by tsg_match.py: shown, labelled in alerts, reviewed '
    'on /admin/interconnect/tsg. confirmed = hidden; rejected = never flagged again.';

COMMIT;
