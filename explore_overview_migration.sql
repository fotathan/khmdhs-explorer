-- explore_overview_migration.sql
-- ===========================================================================
-- Makes the /explore page fast by precomputing its overview aggregations.
--
-- WHY: /explore aggregated the full 2.7M-row procurement_act table live, with
-- per-row function calls (resolved_value, canon_authority, canon_contractor) in
-- the SELECT and GROUP BY. That is millions of function evaluations per load,
-- which is why the page hung. (resolved_value was already optimised away in the
-- route; canon_* remained, and the live GROUP BY over millions of rows is itself
-- the dominant cost.)
--
-- WHAT: two materialized views holding the rolled-up totals BY canonical entity
-- and BY act type, with the merge canonicalisation and the corrected (resolved)
-- value already baked in. /explore reads these for the common cases (no filter,
-- or a type filter) — instant. Fine-grained filters (text search, CPV, dates,
-- contract/procedure type) still fall back to a live query against a narrower
-- slice, which is acceptable because it is no longer the whole table.
--
-- The "eligible" rule mirrors the analytics dashboard: not cancelled, value at
-- or under the ceiling, not suspicious-flagged. With the (tiny) annotation view
-- LEFT JOINed once, this is cheap to evaluate at refresh time.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f explore_overview_migration.sql
--   psql "<supabase-direct-url>" -f explore_overview_migration.sql
-- ===========================================================================

-- --- by authority × type ----------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS proc.mv_explore_authority CASCADE;
CREATE MATERIALIZED VIEW proc.mv_explore_authority AS
WITH base AS (
    SELECT
        COALESCE(ga.canonical_key, a.authority_id)              AS auth_key,
        a.type                                                  AS type,
        a.adam                                                  AS adam,
        COALESCE(corr.corrected_value, a.total_cost_with_vat)   AS rv
    FROM proc.procurement_act a
    LEFT JOIN proc.v_act_annotation_current corr ON corr.adam = a.adam
    LEFT JOIN proc.entity_member ma
           ON ma.kind = 'authority' AND ma.member_key = a.authority_id
    LEFT JOIN proc.entity_group ga ON ga.id = ma.group_id
    WHERE a.authority_id IS NOT NULL
      AND NOT a.cancelled
      AND (COALESCE(corr.corrected_value, a.total_cost_with_vat) IS NULL
           OR COALESCE(corr.corrected_value, a.total_cost_with_vat) <= 1000000000000)
      AND (corr.flag IS DISTINCT FROM 'suspicious')
)
SELECT
    auth_key,
    type,
    count(*)                  AS n,
    COALESCE(sum(rv), 0)      AS value
FROM base
GROUP BY auth_key, type;

-- Resolve a display name per canonical authority key (one row per key).
DROP MATERIALIZED VIEW IF EXISTS proc.mv_explore_authority_name CASCADE;
CREATE MATERIALIZED VIEW proc.mv_explore_authority_name AS
SELECT DISTINCT ON (COALESCE(ga.canonical_key, auth.org_id))
       COALESCE(ga.canonical_key, auth.org_id) AS auth_key,
       auth.name                               AS name
FROM proc.authority auth
LEFT JOIN proc.entity_member ma
       ON ma.kind = 'authority' AND ma.member_key = auth.org_id
LEFT JOIN proc.entity_group ga ON ga.id = ma.group_id
ORDER BY COALESCE(ga.canonical_key, auth.org_id), auth.name;

-- --- by contractor × type ---------------------------------------------------
-- Contractor value prefers the per-operator awarded value, else the act's
-- resolved value (matching the live query's COALESCE).
DROP MATERIALIZED VIEW IF EXISTS proc.mv_explore_contractor CASCADE;
CREATE MATERIALIZED VIEW proc.mv_explore_contractor AS
WITH base AS (
    SELECT
        COALESCE(gc.canonical_key, eo.vat_number)              AS contr_key,
        a.type                                                 AS type,
        a.adam                                                 AS adam,
        COALESCE(ao.awarded_value_with_vat,
                 COALESCE(corr.corrected_value, a.total_cost_with_vat)) AS rv
    FROM proc.procurement_act a
    LEFT JOIN proc.v_act_annotation_current corr ON corr.adam = a.adam
    JOIN proc.act_operator ao        ON ao.adam = a.adam
    JOIN proc.economic_operator eo   ON eo.operator_id = ao.operator_id
    LEFT JOIN proc.entity_member mc
           ON mc.kind = 'contractor' AND mc.member_key = eo.vat_number
    LEFT JOIN proc.entity_group gc ON gc.id = mc.group_id
    WHERE NOT a.cancelled
      AND (COALESCE(corr.corrected_value, a.total_cost_with_vat) IS NULL
           OR COALESCE(corr.corrected_value, a.total_cost_with_vat) <= 1000000000000)
      AND (corr.flag IS DISTINCT FROM 'suspicious')
)
SELECT
    contr_key,
    type,
    count(DISTINCT adam)      AS n,
    COALESCE(sum(rv), 0)      AS value
FROM base
GROUP BY contr_key, type;

DROP MATERIALIZED VIEW IF EXISTS proc.mv_explore_contractor_name CASCADE;
CREATE MATERIALIZED VIEW proc.mv_explore_contractor_name AS
SELECT DISTINCT ON (COALESCE(gc.canonical_key, eo.vat_number))
       COALESCE(gc.canonical_key, eo.vat_number) AS contr_key,
       eo.name                                   AS name,
       (gc.canonical_key IS NOT NULL)            AS is_merged
FROM proc.economic_operator eo
LEFT JOIN proc.entity_member mc
       ON mc.kind = 'contractor' AND mc.member_key = eo.vat_number
LEFT JOIN proc.entity_group gc ON gc.id = mc.group_id
ORDER BY COALESCE(gc.canonical_key, eo.vat_number), eo.name;

-- --- indexes for fast read + REFRESH CONCURRENTLY ---------------------------
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_explore_authority
    ON proc.mv_explore_authority (auth_key, type);
CREATE INDEX IF NOT EXISTS ix_mv_explore_authority_value
    ON proc.mv_explore_authority (value DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_explore_authority_name
    ON proc.mv_explore_authority_name (auth_key);

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_explore_contractor
    ON proc.mv_explore_contractor (contr_key, type);
CREATE INDEX IF NOT EXISTS ix_mv_explore_contractor_value
    ON proc.mv_explore_contractor (value DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_explore_contractor_name
    ON proc.mv_explore_contractor_name (contr_key);

ANALYZE proc.mv_explore_authority;
ANALYZE proc.mv_explore_authority_name;
ANALYZE proc.mv_explore_contractor;
ANALYZE proc.mv_explore_contractor_name;

-- --- fold into refresh_analytics() ------------------------------------------
-- Re-defines the function to ALSO refresh the four explore views. Keeps every
-- prior refresh (defensive skip-if-missing). If you later re-run an analytics
-- migration that redefines refresh_analytics(), re-run THIS file afterwards so
-- the explore refreshes are included again.
CREATE OR REPLACE FUNCTION proc.refresh_analytics() RETURNS void AS $$
BEGIN
    BEGIN
        PERFORM proc.refresh_procedure_family();
    EXCEPTION WHEN undefined_function THEN NULL;
    END;
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
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_contractor_counts;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_authority_counts;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- explore overview views (this migration)
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_authority;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_authority_name;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_contractor;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_contractor_name;
    EXCEPTION WHEN undefined_table THEN NULL; END;
END;
$$ LANGUAGE plpgsql;
