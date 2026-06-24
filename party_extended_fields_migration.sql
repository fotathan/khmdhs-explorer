-- party_extended_fields_migration.sql
-- ===========================================================================
-- Party-level (authority / contractor) analogue of act_extended_fields_migration.
-- Adds descriptive, identity and contact fields mirrored from the main company
-- tender platform (tender_authority / tender_contractor + their tender_address)
-- that KHMDHS does not provide but manual/other-source data will.
--
-- Purely ADDITIVE and safe: all nullable, nothing existing touched. These
-- columns are NOT written by the import upserts (upsert_authority /
-- _upsert_operator only touch name/vat_number/last_seen on conflict), so a
-- curator's hand-entered values are never clobbered by a re-import.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f party_extended_fields_migration.sql
--   psql "<supabase-direct-url>" -f party_extended_fields_migration.sql
-- ===========================================================================

-- --- Awarding authority -----------------------------------------------------
ALTER TABLE proc.authority
    ADD COLUMN IF NOT EXISTS identifier text;       -- source-side identifier
ALTER TABLE proc.authority
    ADD COLUMN IF NOT EXISTS orgdb_id text;          -- cross-ref to company org DB
ALTER TABLE proc.authority
    ADD COLUMN IF NOT EXISTS street_address text;
ALTER TABLE proc.authority
    ADD COLUMN IF NOT EXISTS contact_email text;
ALTER TABLE proc.authority
    ADD COLUMN IF NOT EXISTS contact_phone text;
ALTER TABLE proc.authority
    ADD COLUMN IF NOT EXISTS contact_fax text;
ALTER TABLE proc.authority
    ADD COLUMN IF NOT EXISTS contact_url text;

-- --- Contractor (economic operator) -----------------------------------------
-- (this table currently has no city/postal/nuts/contact at all)
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS statistical_or_tax_number text;  -- distinct from VAT
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS contact_person text;
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS orgdb_id text;
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS city text;
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS postal_code text;
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS nuts_code varchar(5);
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS street_address text;
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS contact_email text;
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS contact_phone text;
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS contact_fax text;
ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS contact_url text;

ANALYZE proc.authority;
ANALYZE proc.economic_operator;
