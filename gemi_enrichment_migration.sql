-- gemi_enrichment_migration.sql
-- ===========================================================================
-- Stores ΓΕΜΗ (General Commercial Registry) Open Data enrichment, keyed on ΑΦΜ
-- (vat_number), for both contracting authorities (proc.authority.vat_number)
-- and suppliers/contractors (proc.economic_operator.vat_number).
--
-- Source: GET https://opendata-api.businessportal.gr/api/opendata/v1/companies?afm=…
-- Licence: ODC-BY-1.0 (attribution required when displaying ΓΕΜΗ data).
--
-- Flatten-only by choice: the columns below are the fields actually used
-- (identity, address, contact, status, primary ΚΑΔ). The `activities_active`
-- JSONB holds the currently-valid ΚΑΔ list (dtTo IS NULL), latest kadVersion.
-- `raw` keeps the untouched API record so richer fields (persons, capital,
-- stocks, objective) can be recovered later WITHOUT re-fetching — leave it
-- unused if you don't need them; drop the column if you'd rather not store it.
--
-- One row per ΑΦΜ. Re-running enrichment upserts (see backfill script).
--
-- Run:  psql "$DATABASE_URL" -f gemi_enrichment_migration.sql
-- ===========================================================================

SET search_path TO proc, public;

CREATE TABLE IF NOT EXISTS proc.gemi_enrichment (
    afm            text PRIMARY KEY,            -- join key to authority/operator

    -- identity
    ar_gemi        text,                        -- ΓΕΜΗ number
    legal_name     text,                        -- coNameEl
    trade_title    text,                        -- first of coTitlesEl (trimmed)
    legal_type     text,                        -- legalType.descr (ΕΠΕ/ΑΕ/…)
    status         text,                        -- status.descr (Ενεργή/…)
    status_id      int,                         -- status.id
    is_branch      boolean,                     -- isBranch

    -- address
    street         text,
    street_number  text,
    zip_code       text,
    city           text,
    municipality   text,                        -- municipality.descr
    prefecture     text,                        -- prefecture.descr

    -- contact (present in ΓΕΜΗ opendata for many companies)
    phone          text,
    fax            text,
    email          text,
    url            text,

    -- ΚΑΔ — currently active only (dtTo IS NULL), latest kadVersion.
    -- Array of {id, descr, type, kadVersion}. primary_kad is the 'Κύρια' code.
    primary_kad        text,
    primary_kad_descr  text,
    activities_active  jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- dates
    incorporation_date date,                    -- incorporationDate

    -- provenance
    raw            jsonb,                        -- untouched API record (optional)
    match_count    int,                         -- searchMetadata.totalCount seen
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    fetch_status   text NOT NULL DEFAULT 'ok'   -- 'ok' | 'not_found' | 'error' | 'ambiguous'
);

-- Lookups from the two party tables join on afm.
CREATE INDEX IF NOT EXISTS ix_gemi_enrichment_fetched
    ON proc.gemi_enrichment (fetched_at);
CREATE INDEX IF NOT EXISTS ix_gemi_enrichment_status
    ON proc.gemi_enrichment (fetch_status);

-- Optional: GIN on the active-ΚΑΔ array, if you later filter acts/suppliers by
-- a supplier's registered ΚΑΔ.
CREATE INDEX IF NOT EXISTS ix_gemi_enrichment_activities
    ON proc.gemi_enrichment USING gin (activities_active);
