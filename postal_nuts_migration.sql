-- ============================================================================
-- postal_nuts_migration.sql — Greek postal code → NUTS-3 region mapping.
--
-- Source: Eurostat correspondence table pc2025_EL_NUTS-2024_v1.0 (data/…csv).
-- Used by the act form: typing a Τ.Κ. (postal code) auto-fills the act's NUTS
-- region. One postal code maps to exactly one NUTS-3 region.
--
-- Idempotent. Apply, then load the rows with:  python3 db.py load-postal-nuts
--   psql "$DATABASE_URL" -f postal_nuts_migration.sql
-- ============================================================================

BEGIN;
SET search_path TO proc, public;

CREATE TABLE IF NOT EXISTS proc.postal_nuts (
    postal_code text PRIMARY KEY,
    nuts_code   varchar(8) NOT NULL REFERENCES proc.nuts_code(nuts_code)
);
CREATE INDEX IF NOT EXISTS ix_postal_nuts_nuts ON proc.postal_nuts(nuts_code);

COMMIT;
