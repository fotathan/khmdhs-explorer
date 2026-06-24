-- operator_ar_gemi_migration.sql
-- ===========================================================================
-- Dedicated, editable ΓΕΜΗ registry-number column on the contractor table.
-- Seeded fill-only-if-empty from ΓΕΜΗ enrichment (see gemi_client.upsert) and
-- editable in the contractor edit form. Additive / nullable / safe.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f operator_ar_gemi_migration.sql
--   psql "<supabase-direct-url>" -f operator_ar_gemi_migration.sql
-- ===========================================================================

ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS ar_gemi text;   -- ΓΕΜΗ registry number (αρ. ΓΕΜΗ)

ANALYZE proc.economic_operator;
