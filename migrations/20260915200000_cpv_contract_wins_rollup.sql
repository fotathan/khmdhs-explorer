-- Pre-computed CPV × contract-award rows for the act page's "Κορυφαίοι ανάδοχοι
-- σε αυτούς τους κωδικούς CPV" panel (app/main.py act_top_contractors).
--
-- Why: the live query finds every line item carrying the act's CPV codes, joins
-- each to its act and winner, and groups. On a common code (33696500-0, 96k line
-- items) that measured 2.8-4.4s, and 680 of 1,950 open notices carry a code with
-- 10k+ line items. No index removes the work. Read from this view the same
-- ranking takes ~110-150ms (identical top-10, checked with EXCEPT both ways).
--
-- One row per (cpv_code, award): the award is the act_operator row, so a contract
-- with several line items under one code is ONE row — the live query used to add
-- its value once per line item. Values are stored raw; the route applies manual
-- corrections (v_act_annotation_current) at read time, so a correction shows
-- immediately rather than at the next refresh.
--
-- Freshness: refreshed CONCURRENTLY by proc.refresh_analytics(), i.e. after an
-- import, like the other analytics views. Until the first population the route
-- falls back to the live query, so code and migration can land in either order.
--
-- Local build: ~1.3M rows, ~3.6s.

BEGIN;

CREATE MATERIALIZED VIEW IF NOT EXISTS proc.mv_cpv_contract_wins AS
SELECT DISTINCT
       oc.cpv_code,
       ao.id                                      AS award_id,
       a.adam,
       ao.operator_id,
       ao.awarded_value_with_vat                  AS awarded_value,
       a.total_cost_with_vat                      AS source_value,
       coalesce(a.signed_date, a.submission_date) AS won_at
FROM proc.object_detail_cpv oc
JOIN proc.act_object_detail od ON od.id = oc.object_detail_id
JOIN proc.procurement_act a    ON a.adam = od.adam AND a.type = 'contract'
JOIN proc.act_operator ao      ON ao.adam = a.adam AND ao.role = 'winner'
WITH DATA;

-- Unique: required by REFRESH ... CONCURRENTLY. Its cpv_code prefix is also the
-- index the panel's WHERE cpv_code = ANY(...) uses.
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_cpv_contract_wins
    ON proc.mv_cpv_contract_wins (cpv_code, award_id);

COMMENT ON MATERIALIZED VIEW proc.mv_cpv_contract_wins IS
  'One row per (CPV code, winning contract award) for the act page competition panel; refreshed by proc.refresh_analytics().';

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    GRANT SELECT ON proc.mv_cpv_contract_wins TO app_runtime;
  END IF;
END $$;

-- The existing refresh_analytics(), unchanged, plus the new view at the end.
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
END;
$function$;

COMMIT;
