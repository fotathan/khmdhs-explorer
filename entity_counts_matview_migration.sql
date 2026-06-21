-- entity_counts_matview_migration.sql
-- ===========================================================================
-- The /contractors and /authorities directory pages sort by activity (contract
-- count), which forced a full aggregation over act_operator (2M) + procurement_
-- act (2.7M) on EVERY page load — ~4.2s, with an 87MB on-disk sort. Those
-- counts only change when new acts are ingested, so we precompute them here
-- and have the pages read these tiny, indexed views instead. Sort+limit over
-- ~134k precomputed rows is milliseconds.
--
-- Counts are merge-aware: summed per CANONICAL key (matching how the pages
-- collapse merged entities), so a merged entity's totals cover all its members.
--
-- REFRESH after each ingest (and once now):
--   REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_contractor_counts;
--   REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_authority_counts;
-- CONCURRENTLY needs the UNIQUE indexes below and avoids locking reads during
-- refresh. (First-time creation can't be concurrent; the CREATE populates it.)
--
-- Run on BOTH local and Supabase:
--   psql "$DATABASE_URL"         -f entity_counts_matview_migration.sql
--   psql "<supabase-direct-url>"  -f entity_counts_matview_migration.sql
-- ===========================================================================

-- ----- Contractors: n_acts + n_buyers per canonical vat --------------------
DROP MATERIALIZED VIEW IF EXISTS proc.mv_contractor_counts;
CREATE MATERIALIZED VIEW proc.mv_contractor_counts AS
WITH keymap AS (
    SELECT eo.operator_id,
           COALESCE(g.canonical_key, eo.vat_number) AS canon_vat
    FROM proc.economic_operator eo
    LEFT JOIN proc.entity_member m
      ON m.kind = 'contractor' AND m.member_key = eo.vat_number
    LEFT JOIN proc.entity_group g ON g.id = m.group_id
)
SELECT k.canon_vat                              AS vat_number,
       count(ao.adam)                           AS n_acts,
       count(DISTINCT a.authority_id)           AS n_buyers
FROM keymap k
JOIN proc.act_operator ao       ON ao.operator_id = k.operator_id
LEFT JOIN proc.procurement_act a ON a.adam = ao.adam
GROUP BY k.canon_vat;

-- UNIQUE index → required for REFRESH … CONCURRENTLY, also the lookup key.
CREATE UNIQUE INDEX ix_mv_contractor_counts_vat
    ON proc.mv_contractor_counts (vat_number);
-- Sort index for the default "by activity" ordering.
CREATE INDEX ix_mv_contractor_counts_acts
    ON proc.mv_contractor_counts (n_acts DESC);

-- ----- Authorities: n_acts / n_notices / n_contracts per canonical org -----
DROP MATERIALIZED VIEW IF EXISTS proc.mv_authority_counts;
CREATE MATERIALIZED VIEW proc.mv_authority_counts AS
WITH keymap AS (
    SELECT auth.org_id,
           COALESCE(g.canonical_key, auth.org_id) AS canon_org
    FROM proc.authority auth
    LEFT JOIN proc.entity_member m
      ON m.kind = 'authority' AND m.member_key = auth.org_id
    LEFT JOIN proc.entity_group g ON g.id = m.group_id
)
SELECT k.canon_org                                              AS org_id,
       count(a.adam)                                            AS n_acts,
       count(a.adam) FILTER (WHERE a.type='notice')            AS n_notices,
       count(a.adam) FILTER (WHERE a.type='contract')          AS n_contracts
FROM keymap k
JOIN proc.procurement_act a ON a.authority_id = k.org_id
GROUP BY k.canon_org;

CREATE UNIQUE INDEX ix_mv_authority_counts_org
    ON proc.mv_authority_counts (org_id);
CREATE INDEX ix_mv_authority_counts_acts
    ON proc.mv_authority_counts (n_acts DESC);

ANALYZE proc.mv_contractor_counts;
ANALYZE proc.mv_authority_counts;

-- Fold the two new views into the existing refresh_analytics() so they refresh
-- as part of your normal post-ingest step (SELECT proc.refresh_analytics();).
-- CONCURRENTLY keeps the directory pages readable during refresh. We re-declare
-- the whole function to append our two REFRESHes after the existing ones; if you
-- later re-run an analytics migration that redefines refresh_analytics(), re-run
-- THIS block too (or merge the two REFRESH lines into that migration).
CREATE OR REPLACE FUNCTION proc.refresh_analytics() RETURNS void AS $$
BEGIN
    BEGIN
        PERFORM proc.refresh_procedure_family();
    EXCEPTION WHEN undefined_function THEN
        NULL;  -- procedure_family migration not applied; skip
    END;
    -- existing analytics views (skip individually if not yet created)
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_totals;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_authorities;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_contractors;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_monthly;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- directory count views (this migration) — CONCURRENTLY needs their UNIQUE
    -- indexes (created above) and keeps reads live during refresh.
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_contractor_counts;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_authority_counts;
    EXCEPTION WHEN undefined_table THEN NULL; END;
END;
$$ LANGUAGE plpgsql;
