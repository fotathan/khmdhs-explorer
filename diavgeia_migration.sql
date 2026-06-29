-- ============================================================================
-- diavgeia_migration.sql — add Diavgeia (diavgeia.gov.gr) as a second source.
--
-- Diavgeia decisions are keyed by ADA (not ADAM) and have a different shape from
-- the KHMDHS-specific procurement_act, so they live in their own ADA-keyed tables
-- (the diavgeia_decision stub from schema.sql, here extended) and REUSE the shared
-- dimension tables (authority, economic_operator, cpv_code) to avoid duplicates.
--
-- Scope (for now): three decision types harvested from the opendata API —
--   Δ.2.1  ΠΕΡΙΛΗΨΗ ΔΙΑΚΗΡΥΞΗΣ / ΔΙΑΚΗΡΥΞΗ   -> Notice
--   Δ.2.2  ΚΑΤΑΚΥΡΩΣΗ                          -> Award
--   Γ.3.4  ΣΥΜΒΑΣΗ                             -> Contract
--
-- Idempotent: safe to re-run (ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT
-- EXISTS). Apply by hand like the other *_migration.sql files:
--   psql "$DATABASE_URL" -f diavgeia_migration.sql
-- ============================================================================

BEGIN;
SET search_path TO proc, public;

-- ---------------------------------------------------------------------------
-- 1. Extend the diavgeia_decision header (already: ada, subject, decision_type,
--    organization_uid, signer_uid, issue_date, document_url, raw_json, ingested_at)
-- ---------------------------------------------------------------------------
ALTER TABLE proc.diavgeia_decision
    ADD COLUMN IF NOT EXISTS protocol_number       text,
    ADD COLUMN IF NOT EXISTS status                text,                 -- PUBLISHED, ...
    ADD COLUMN IF NOT EXISTS version_id            text,
    ADD COLUMN IF NOT EXISTS corrected_version_id  text,
    ADD COLUMN IF NOT EXISTS private_data          boolean,
    ADD COLUMN IF NOT EXISTS publish_timestamp     timestamptz,
    ADD COLUMN IF NOT EXISTS submission_timestamp  timestamptz,
    ADD COLUMN IF NOT EXISTS document_checksum     text,
    ADD COLUMN IF NOT EXISTS api_url               text,                 -- decision .url
    ADD COLUMN IF NOT EXISTS authority_id          text REFERENCES proc.authority(org_id),
    ADD COLUMN IF NOT EXISTS document_type         text,                 -- extraField documentType
    -- unified money (estimatedAmount [notice] / awardAmount [award] / contractAmount [contract])
    ADD COLUMN IF NOT EXISTS amount                numeric(18,2),
    ADD COLUMN IF NOT EXISTS currency_code         text,
    -- notice-specific scalars
    ADD COLUMN IF NOT EXISTS contest_progress_type text,                 -- Διαδικασία Διαγωνισμού
    ADD COLUMN IF NOT EXISTS selection_criterion   text,                 -- manifestSelectionCriterion
    ADD COLUMN IF NOT EXISTS manifest_contract_type text,                -- Τύπος σύμβασης
    ADD COLUMN IF NOT EXISTS org_budget_code       text,                 -- orgBudgetCode
    ADD COLUMN IF NOT EXISTS text_related_ada      text,                 -- textRelatedADA (notice/award)
    -- contract-specific scalars
    ADD COLUMN IF NOT EXISTS contract_type         text,                 -- Είδος πράξης
    ADD COLUMN IF NOT EXISTS number_of_people      integer,
    ADD COLUMN IF NOT EXISTS financed_project      boolean,
    ADD COLUMN IF NOT EXISTS duration              text;

CREATE INDEX IF NOT EXISTS ix_diavgeia_type      ON proc.diavgeia_decision(decision_type);
CREATE INDEX IF NOT EXISTS ix_diavgeia_issue     ON proc.diavgeia_decision(issue_date);
CREATE INDEX IF NOT EXISTS ix_diavgeia_authority ON proc.diavgeia_decision(authority_id);
CREATE INDEX IF NOT EXISTS ix_diavgeia_org       ON proc.diavgeia_decision(organization_uid);

-- ---------------------------------------------------------------------------
-- 2. Child tables (one row -> many), all cascading off the decision's ADA.
--    No FK to diavgeia_decision on the *graph* table (diavgeia_related) since a
--    referenced ADA may not be ingested yet — same rationale as proc.act_link.
-- ---------------------------------------------------------------------------

-- CPV codes (notice Δ.2.1 cpv[]). Reuses the shared cpv_code dimension.
CREATE TABLE IF NOT EXISTS proc.diavgeia_decision_cpv (
    ada       text NOT NULL REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE,
    cpv_code  varchar(10) NOT NULL REFERENCES proc.cpv_code(cpv_code),
    ord       integer,
    PRIMARY KEY (ada, cpv_code)
);
CREATE INDEX IF NOT EXISTS ix_diavgeia_cpv_code ON proc.diavgeia_decision_cpv(cpv_code);

-- Awarded / contracted parties (award Δ.2.2 + contract Γ.3.4 person[]).
-- Reuses economic_operator when an ΑΦΜ is present; name-only persons keep their
-- name on the row with operator_id NULL.
CREATE TABLE IF NOT EXISTS proc.diavgeia_decision_person (
    id          bigserial PRIMARY KEY,
    ada         text NOT NULL REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE,
    operator_id bigint REFERENCES proc.economic_operator(operator_id),
    afm         text,
    name        text,
    afm_type    text,                                  -- dictionary VAT_TYPE
    afm_country text,                                  -- dictionary EE_MEMBER
    ord         integer
);
CREATE INDEX IF NOT EXISTS ix_diavgeia_person_ada ON proc.diavgeia_decision_person(ada);
CREATE INDEX IF NOT EXISTS ix_diavgeia_person_op  ON proc.diavgeia_decision_person(operator_id);

-- Signers (signerIds[]) and units (unitIds[]) — Diavgeia's OWN id space, resolved
-- against the diavgeia_signer / diavgeia_unit dictionaries below (not the
-- KHMDHS-keyed proc.signer / proc.org_unit).
CREATE TABLE IF NOT EXISTS proc.diavgeia_decision_signer (
    ada        text NOT NULL REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE,
    signer_uid text NOT NULL,
    ord        integer,
    PRIMARY KEY (ada, signer_uid)
);

CREATE TABLE IF NOT EXISTS proc.diavgeia_decision_unit (
    ada      text NOT NULL REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE,
    unit_uid text NOT NULL,
    ord      integer,
    PRIMARY KEY (ada, unit_uid)
);

-- Thematic categories (thematicCategoryIds[]) — id only (no per-id endpoint).
CREATE TABLE IF NOT EXISTS proc.diavgeia_decision_thematic (
    ada           text NOT NULL REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE,
    thematic_uid  text NOT NULL,
    PRIMARY KEY (ada, thematic_uid)
);

-- Related-decision graph (relatedDecisions[].relatedDecisionsADA + textRelatedADA).
-- ADA -> ADA edges; target may not be ingested, so no FK (cf. proc.act_link).
CREATE TABLE IF NOT EXISTS proc.diavgeia_related (
    source_ada text NOT NULL,
    target_ada text NOT NULL,
    kind       text NOT NULL,                          -- 'related' | 'text_related'
    discovered_at timestamptz DEFAULT now(),
    PRIMARY KEY (source_ada, target_ada, kind)
);
CREATE INDEX IF NOT EXISTS ix_diavgeia_related_src ON proc.diavgeia_related(source_ada);
CREATE INDEX IF NOT EXISTS ix_diavgeia_related_tgt ON proc.diavgeia_related(target_ada);

-- Attachments (attachments[]) — empty in samples, but cheap to capture.
CREATE TABLE IF NOT EXISTS proc.diavgeia_attachment (
    id       bigserial PRIMARY KEY,
    ada      text NOT NULL REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE,
    filename text,
    mimetype text,
    url      text,
    checksum text
);
CREATE INDEX IF NOT EXISTS ix_diavgeia_attachment_ada ON proc.diavgeia_attachment(ada);

-- ---------------------------------------------------------------------------
-- 3. Diavgeia-native dictionaries (cached from /units, /signers lookups).
--    Organizations are deduped into the shared proc.authority table instead.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.diavgeia_unit (
    uid      text PRIMARY KEY,
    label    text,
    category text
);

CREATE TABLE IF NOT EXISTS proc.diavgeia_signer (
    uid        text PRIMARY KEY,
    first_name text,
    last_name  text
);

-- ---------------------------------------------------------------------------
-- 4. Ingestion bookkeeping — windowed backfill over issueDate, per decision_type.
--    Mirrors proc.ingest_window but kept separate so KHMDHS watermark logic in
--    db.py is untouched. decision_type is the Diavgeia uid (e.g. 'Δ.2.1').
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.diavgeia_ingest_window (
    id            bigserial PRIMARY KEY,
    decision_type text NOT NULL,
    date_from     date NOT NULL,
    date_to       date NOT NULL,
    status        text NOT NULL DEFAULT 'pending',     -- pending|running|done|error
    pages_done    integer DEFAULT 0,
    total_pages   integer,
    last_error    text,
    started_at    timestamptz,
    finished_at   timestamptz,
    UNIQUE (decision_type, date_from, date_to)
);

COMMIT;
