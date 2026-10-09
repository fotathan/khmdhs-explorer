-- authority profile (docs/specs/public-detail-pages.md, slice 2)
--
-- The public authority page's 12-month figures: how much it publishes, what
-- it buys, how, where and at what size. Two HISTOGRAM views, one row per
-- combination, summed in Python (app/authority_profile.py) — the page merges
-- entity groups, so the per-org rows have to add up.
--
-- Which acts count:
--   - notices and contracts PUBLISHED (submission_date) in the 12 months up to
--     the refresh day; period_start / period_end carry that window so the page
--     prints the period the figures really cover;
--   - not cancelled, not a hidden duplicate;
--   - source on the analytics allowlist (khmdhs, manual, NULL) — the same rule
--     as /analytics: TED, Tender Service and Diavgeia duplicate KHMDHS acts.
--   - value: CONTRACTS only, and only when proc.is_analytics_eligible (under
--     the value ceiling, not flagged) — with the manual correction applied.
--     Summing notices too would count one tender twice.
--
-- Unknown dimensions are '' (and value_band -1), never NULL, so the unique
-- index REFRESH ... CONCURRENTLY needs covers every row.
--
-- Freshness: refreshed CONCURRENTLY by proc.refresh_analytics(). Until the
-- views exist the page shows no profile (authority_profile.load answers None),
-- so code and migration can land in either order.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.

BEGIN;

-- 1. Per authority: counts, value, mixes ------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS proc.mv_authority_profile AS
WITH base AS (
    SELECT a.authority_id,
           a.type::text                                          AS act_type,
           coalesce(a.contract_type_code, '')                    AS contract_type,
           coalesce(a.procedure_family, '')                      AS procedure_family,
           CASE WHEN a.nuts_code ~ '^EL[0-9]{2}'
                THEN left(a.nuts_code, 4) ELSE '' END            AS nuts2,
           CASE WHEN a.type = 'contract'
                 AND proc.is_analytics_eligible(a.adam, a.total_cost_with_vat, a.cancelled)
                THEN proc.resolved_value(a.adam, a.total_cost_with_vat) END AS value
    FROM proc.procurement_act a
    WHERE a.type IN ('notice', 'contract')
      AND a.authority_id IS NOT NULL
      AND NOT coalesce(a.cancelled, false)
      AND a.submission_date >= current_date - interval '12 months'
      AND a.submission_date <  current_date + 1
      AND coalesce(a.data_source, 'khmdhs') IN ('khmdhs', 'manual')
      AND NOT EXISTS (SELECT 1 FROM proc.procurement_act hid
                      WHERE hid.duplicate_of IS NOT NULL AND hid.adam = a.adam)
)
SELECT authority_id, act_type, contract_type, procedure_family, nuts2,
       CASE WHEN value IS NULL      THEN -1
            WHEN value <    10000   THEN 0
            WHEN value <    30000   THEN 1
            WHEN value <   100000   THEN 2
            WHEN value <   500000   THEN 3
            WHEN value <  1000000   THEN 4
            ELSE 5 END::smallint                        AS value_band,
       count(*)::int                                    AS n,
       count(value)::int                                AS n_valued,
       coalesce(sum(value), 0)::numeric                 AS value,
       (current_date - interval '12 months')::date      AS period_start,
       current_date                                     AS period_end
FROM base
GROUP BY 1, 2, 3, 4, 5, 6
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_authority_profile
    ON proc.mv_authority_profile
       (authority_id, act_type, contract_type, procedure_family, nuts2, value_band);

COMMENT ON MATERIALIZED VIEW proc.mv_authority_profile IS
  '12-month notice/contract histogram per authority (public authority profile); refreshed by proc.refresh_analytics().';

-- 2. Per authority and CPV division (contracts) -------------------------------
-- One row per (division, contract) BEFORE counting: six line items in one
-- division count once; a contract spanning two divisions counts in each.
CREATE MATERIALIZED VIEW IF NOT EXISTS proc.mv_authority_profile_cpv AS
WITH counted AS (
    SELECT a.adam, a.authority_id,
           CASE WHEN proc.is_analytics_eligible(a.adam, a.total_cost_with_vat, a.cancelled)
                THEN proc.resolved_value(a.adam, a.total_cost_with_vat) END AS value
    FROM proc.procurement_act a
    WHERE a.type = 'contract'
      AND a.authority_id IS NOT NULL
      AND NOT coalesce(a.cancelled, false)
      AND a.submission_date >= current_date - interval '12 months'
      AND a.submission_date <  current_date + 1
      AND coalesce(a.data_source, 'khmdhs') IN ('khmdhs', 'manual')
      AND NOT EXISTS (SELECT 1 FROM proc.procurement_act hid
                      WHERE hid.duplicate_of IS NOT NULL AND hid.adam = a.adam)
), lines AS (
    SELECT DISTINCT c.authority_id, substr(oc.cpv_code, 1, 2) AS division,
           c.adam, c.value
    FROM counted c
    JOIN proc.act_object_detail od ON od.adam = c.adam
    JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
)
SELECT authority_id, division,
       count(*)::int                    AS n,
       coalesce(sum(value), 0)::numeric AS value
FROM lines
GROUP BY 1, 2
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_authority_profile_cpv
    ON proc.mv_authority_profile_cpv (authority_id, division);

COMMENT ON MATERIALIZED VIEW proc.mv_authority_profile_cpv IS
  '12-month contracts per authority and 2-digit CPV division (public authority profile); refreshed by proc.refresh_analytics().';

-- 3. The existing refresh_analytics(), unchanged, plus the two new views -----
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
END;
$function$;

COMMIT;
