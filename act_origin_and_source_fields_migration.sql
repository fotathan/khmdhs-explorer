-- act_origin_and_source_fields_migration.sql
-- ===========================================================================
-- PREREQUISITE for multi-source act management.
--
-- Adds two things to proc.procurement_act, both purely ADDITIVE and safe
-- (every column nullable / defaulted; no existing data touched, nothing
-- existing breaks):
--
--   1. ORIGIN CLASS  — distinguishes "imported" acts (KHMDHS / future
--      automated sources; read-only core fields + the existing overlay
--      correction system) from "authored" acts (created/edited by a curator;
--      fully editable core fields, never overwritten by re-import).
--
--   2. MULTI-SOURCE FIELDS — fields other data sources provide that KHMDHS
--      does not, taken from the data-management screenshots as the starting
--      set. Stored as their own columns (NOT forced into the Greek code lists),
--      because other sources use their own vocabularies.
--
-- The critical correctness rule this enables (to be enforced in the import
-- pipeline, NOT in this migration):
--     re-import may overwrite core fields ONLY where origin = 'import'.
--     Acts with origin = 'authored' are owned by the curator and must be
--     skipped by any automated importer.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f act_origin_and_source_fields_migration.sql
--   psql "<supabase-direct-url>" -f act_origin_and_source_fields_migration.sql
-- ===========================================================================

-- --- 1. ORIGIN CLASS --------------------------------------------------------
-- 'import'   : created by an automated importer; core fields owned by source.
-- 'authored' : created/edited by a curator; core fields owned by the curator.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'import';

-- Which data source this act came from. KHMDHS acts default to 'khmdhs'.
-- Authored/manual acts set this to 'manual' or a named source.
-- (This is the act-level analogue of authority.source; that column is on a
--  different table and is not used here.)
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS data_source text;

-- Backfill: everything that exists today came from KHMDHS import.
UPDATE proc.procurement_act
SET data_source = 'khmdhs'
WHERE data_source IS NULL;

-- The original URL / record reference at the source (for "Link to Original").
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS source_url text;

-- Curator bookkeeping for authored/edited acts.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS authored_by text;        -- curator who created it
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS last_edited_by text;     -- curator who last edited
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS last_edited_at timestamptz;

-- A guard CHECK so origin can only ever be one of the two known classes.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'procurement_act_origin_chk'
    ) THEN
        ALTER TABLE proc.procurement_act
            ADD CONSTRAINT procurement_act_origin_chk
            CHECK (origin IN ('import', 'authored'));
    END IF;
END $$;

-- --- 2. MULTI-SOURCE FIELDS (from the screenshots) --------------------------
-- These hold values other sources provide that KHMDHS does not, OR provide in
-- a different vocabulary than the Greek code lists. All free-form / nullable so
-- any source can populate what it has and leave the rest blank.

-- External / source-side identifiers
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS external_id text;          -- source's own ID (e.g. WAL-MAY616321-2026)
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS source_uuid text;          -- source's UUID if any
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS authority_reference text;   -- "Authority reference"
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS reference_number text;      -- "Reference Number"

-- Descriptive / classification fields as the source expresses them
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS short_description text;     -- "Short Description"
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS lot_number text;            -- "lotNumber"
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS language text;              -- "Language" (e.g. English)
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS nature_of_contract text;    -- "Nature of Contract" (source's value)
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS type_of_document text;      -- "Type Of Document"
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS subtype_of_document text;   -- "Subtype of document"
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS procedure_label text;       -- "Procedure" as free text (vs procedure_type_code)

-- Tender lifecycle status as the source reports it (expired/active/awarded/…).
-- NOTE: distinct from the existing cancelled/is_modified lifecycle flags; this
-- is the source's own status string, free-form.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS source_status text;

-- Regulatory / procedure detail fields seen in the form
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS regulation_of_procurement text;
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS e_auction text;             -- "e-Auction"
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS dynamic_purchasing_system text;  -- "DPS"

-- Operational flags from the management view
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS has_attachments boolean;    -- "Has Attachments" / Files column
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS qualified_for_ml boolean DEFAULT false;  -- "Qualified for ML-training"
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS send_with_next_sre boolean DEFAULT false; -- "Send with next SRE"

-- --- indexes for the data-management list filters ---------------------------
CREATE INDEX IF NOT EXISTS ix_act_origin       ON proc.procurement_act(origin);
CREATE INDEX IF NOT EXISTS ix_act_data_source  ON proc.procurement_act(data_source);
CREATE INDEX IF NOT EXISTS ix_act_external_id  ON proc.procurement_act(external_id);
CREATE INDEX IF NOT EXISTS ix_act_source_status ON proc.procurement_act(source_status);

ANALYZE proc.procurement_act;

-- ===========================================================================
-- AFTER THIS MIGRATION (reminders for the build that follows — no action here):
--   * Import pipeline: when upserting, set origin='import', data_source as
--     appropriate, and SKIP any act whose existing origin='authored'.
--   * New-act create form: insert with origin='authored', data_source='manual',
--     authored_by=<curator>.
--   * Detail/edit UI: core fields editable only when origin='authored';
--     imported acts keep the existing overlay-correction editing.
-- ===========================================================================
