-- migrations/20260917130324_tender_service_duplicates_tsg_record.sql
-- Tender Service duplicate handling — the part that lives on proc.tsg_record.
-- REQUIRES 20260915090000_tender_service_source_tables.sql (and the skip_reason
-- migration): run it only where the Tender Service tables exist. The core part
-- (20260917130323) is independent of it.
--
-- The decision on the record (outcome, rule, tier, target) and the three things
-- matching looks up: the authority code, the deadline day and the exact keys
-- (ΕΣΗΔΗΣ / quoted ΑΔΑΜ, request, ΑΔΑ).
--
-- Paste-safe: no DO $$ … $$ blocks; every statement is idempotent on its own.

BEGIN;

ALTER TABLE proc.tsg_record
    ADD COLUMN IF NOT EXISTS authority_code text,
    ADD COLUMN IF NOT EXISTS deadline_date  date,
    ADD COLUMN IF NOT EXISTS match_keys     text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS match_outcome  text,
    ADD COLUMN IF NOT EXISTS match_rule     text,
    ADD COLUMN IF NOT EXISTS match_tier     smallint,
    ADD COLUMN IF NOT EXISTS matched_adam   text,
    ADD COLUMN IF NOT EXISTS matched_at     timestamptz;

ALTER TABLE proc.tsg_record DROP CONSTRAINT IF EXISTS tsg_record_match_outcome_check;
ALTER TABLE proc.tsg_record ADD CONSTRAINT tsg_record_match_outcome_check
    CHECK (match_outcome IN ('new', 'hidden', 'flagged', 'out_of_scope'));

-- Fill the lookup columns for records already stored; new ones are written by
-- tsg_match.record_scope. A deadline that is not 'dd.MM.yy…' stays NULL.
UPDATE proc.tsg_record
   SET authority_code = nullif(btrim(raw_json->>'authorityIdentifier'), ''),
       deadline_date  = CASE WHEN raw_json->>'deadlineDate' ~ '^\d{2}\.\d{2}\.\d{2}(\s|$)'
                             THEN to_date(left(raw_json->>'deadlineDate', 8), 'DD.MM.YY') END
 WHERE authority_code IS NULL AND deadline_date IS NULL;

CREATE INDEX IF NOT EXISTS ix_tsg_record_block ON proc.tsg_record (authority_code, deadline_date)
    WHERE projected_adam IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_tsg_record_match_keys ON proc.tsg_record USING gin (match_keys);

COMMIT;
