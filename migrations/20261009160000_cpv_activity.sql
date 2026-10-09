-- CPV activity (docs/specs/public-detail-pages.md, slice 4)
--
-- The public /cpv/<code> page's 12-month figures: how many notices and
-- contracts were published under a code (or anything below it), their value
-- and how many authorities published them. One row per CPV PREFIX of 2 to 8
-- digits, so the page for 33600000-6 reads prefix '336' and the page for
-- 50221100-1 reads '502211'.
--
-- An act counts ONCE per prefix however many of its line items fall under it
-- (DISTINCT (act, prefix) before counting); an act spanning two divisions
-- counts in each. n_authorities is a distinct count computed per prefix
-- directly, so it is exact, not a sum.
--
-- Which acts count: the authority profile's rule (20261009120000) — notices
-- and contracts published in the 12 months up to the refresh day, not
-- cancelled, not hidden duplicates, analytics-allowlisted sources; value =
-- eligible contracts only, correction applied.
--
-- Build time: ~2 min locally (~390k acts). Refreshed CONCURRENTLY by
-- proc.refresh_analytics(). Until it exists the page shows no figures.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.

BEGIN;

CREATE MATERIALIZED VIEW IF NOT EXISTS proc.mv_cpv_activity AS
WITH acts AS (
    SELECT a.adam, a.type::text AS act_type, a.authority_id,
           CASE WHEN a.type = 'contract'
                 AND proc.is_analytics_eligible(a.adam, a.total_cost_with_vat, a.cancelled)
                THEN proc.resolved_value(a.adam, a.total_cost_with_vat) END AS value
    FROM proc.procurement_act a
    WHERE a.type IN ('notice', 'contract')
      AND NOT coalesce(a.cancelled, false)
      AND a.submission_date >= current_date - interval '12 months'
      AND a.submission_date <  current_date + 1
      AND coalesce(a.data_source, 'khmdhs') IN ('khmdhs', 'manual')
      AND NOT EXISTS (SELECT 1 FROM proc.procurement_act hid
                      WHERE hid.duplicate_of IS NOT NULL AND hid.adam = a.adam)
), pairs AS (
    SELECT DISTINCT x.adam, x.act_type, x.authority_id, x.value,
           substr(oc.cpv_code, 1, k) AS prefix
    FROM acts x
    JOIN proc.act_object_detail od ON od.adam = x.adam
    JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
    CROSS JOIN generate_series(2, 8) AS k
)
SELECT prefix,
       (count(*) FILTER (WHERE act_type = 'notice'))::int   AS n_notices,
       (count(*) FILTER (WHERE act_type = 'contract'))::int AS n_contracts,
       count(value)::int                                    AS n_valued,
       coalesce(sum(value), 0)::numeric                     AS value,
       count(DISTINCT authority_id)::int                    AS n_authorities,
       (current_date - interval '12 months')::date          AS period_start,
       current_date                                         AS period_end
FROM pairs
GROUP BY prefix
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_cpv_activity ON proc.mv_cpv_activity (prefix);

COMMENT ON MATERIALIZED VIEW proc.mv_cpv_activity IS
  '12-month notices/contracts per CPV prefix (public CPV pages); refreshed by proc.refresh_analytics().';

-- The existing refresh_analytics(), unchanged, plus the new view ------------
CREATE OR REPLACE FUNCTION proc.refresh_analytics()
 RETURNS void
 LANGUAGE plpgsql
AS $function$
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
    -- explore overview views
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_authority;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_authority_name;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_contractor;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_contractor_name;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- act page competition panel (20260915200000_cpv_contract_wins_rollup.sql)
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_cpv_contract_wins;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- competition indicator (20261001082952_competition_indicator.sql)
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_competition_authority;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_competition_cpv;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_competition_fill;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- public authority profile (20261009120000_authority_profile.sql)
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_authority_profile;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_authority_profile_cpv;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- public CPV pages (20261009160000_cpv_activity.sql)
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_cpv_activity;
    EXCEPTION WHEN undefined_table THEN NULL; END;
END;
$function$;

COMMIT;
