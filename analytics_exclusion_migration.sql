-- ============================================================================
-- Migration: exclude implausible / flagged contracts from analytics.
-- Run:  psql "$LOCAL"  -f analytics_exclusion_migration.sql
--       psql "$REMOTE" -f analytics_exclusion_migration.sql
--       then: SELECT proc.refresh_analytics();
--
-- WHY: KHMDHS contains some contracts with corrupt values (e.g. a €9.7M
-- contract recorded as €9.6B — inflated ~1000x by a source data error; a small
-- municipal landscaping job recorded as €376B). Summed naively these dominate
-- and make the dashboard meaningless. We exclude them from the AGGREGATES while
-- keeping the records fully visible and searchable.
--
-- TWO exclusion rules (a contract is excluded from analytics if EITHER holds):
--   1. total_cost_with_vat > €500,000,000  (the sanity ceiling; almost nothing
--      genuine in Greek public procurement exceeds this).
--   2. it has a current annotation flag = 'suspicious' (manual judgement for
--      bad values BELOW the ceiling).
-- Records are never modified or deleted — only left out of the sums.
--
-- The threshold lives in one function so it's defined once and reused by every
-- view and by the app (for the "excluded" badge).
-- ============================================================================

-- Sanity ceiling, in euros (with VAT). Single source of truth.
CREATE OR REPLACE FUNCTION proc.analytics_value_ceiling()
RETURNS numeric AS $$ SELECT 500000000::numeric $$ LANGUAGE sql IMMUTABLE;

-- Is this contract eligible to be counted in analytics?
-- (not cancelled, value within the ceiling, not flagged suspicious)
CREATE OR REPLACE FUNCTION proc.is_analytics_eligible(
        p_adam text, p_value numeric, p_cancelled boolean)
RETURNS boolean AS $$
    SELECT (NOT coalesce(p_cancelled, false))
       AND (p_value IS NULL OR p_value <= proc.analytics_value_ceiling())
       AND NOT EXISTS (
            SELECT 1 FROM proc.v_act_annotation_current a
            WHERE a.adam = p_adam AND a.flag = 'suspicious');
$$ LANGUAGE sql STABLE;

-- --------------------------------------------------------------------------- #
-- Rebuild the analytics views with the exclusion applied.
-- (Same definitions as analytics_migration / analytics_cpv, plus the filter.)
-- --------------------------------------------------------------------------- #

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_totals CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_totals AS
SELECT
    count(*)                                  AS n_contracts,
    coalesce(sum(total_cost_with_vat), 0)     AS awarded_value,
    count(DISTINCT authority_id)              AS n_authorities,
    min(submission_date)                      AS earliest,
    max(submission_date)                      AS latest
FROM proc.procurement_act
WHERE type = 'contract'
  AND proc.is_analytics_eligible(adam, total_cost_with_vat, cancelled);

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_authorities CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_authorities AS
SELECT
    proc.canon_authority(a.authority_id)      AS authority_id,
    count(*)                                  AS n_contracts,
    coalesce(sum(a.total_cost_with_vat), 0)   AS awarded_value
FROM proc.procurement_act a
WHERE a.type = 'contract' AND a.authority_id IS NOT NULL
  AND proc.is_analytics_eligible(a.adam, a.total_cost_with_vat, a.cancelled)
GROUP BY proc.canon_authority(a.authority_id);
CREATE INDEX ix_mv_auth_value ON proc.mv_analytics_authorities (awarded_value DESC);

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_contractors CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_contractors AS
SELECT
    proc.canon_contractor(eo.vat_number)      AS vat_number,
    count(DISTINCT a.adam)                     AS n_contracts,
    coalesce(sum(coalesce(ao.awarded_value_with_vat,
                          a.total_cost_with_vat)), 0) AS awarded_value
FROM proc.act_operator ao
JOIN proc.economic_operator eo ON eo.operator_id = ao.operator_id
JOIN proc.procurement_act a ON a.adam = ao.adam
WHERE a.type = 'contract'
  AND proc.is_analytics_eligible(a.adam, a.total_cost_with_vat, a.cancelled)
GROUP BY proc.canon_contractor(eo.vat_number);
CREATE INDEX ix_mv_contractor_value ON proc.mv_analytics_contractors (awarded_value DESC);

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_monthly CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_monthly AS
SELECT
    date_trunc('month', submission_date)::date AS month,
    count(*)                                   AS n_contracts,
    coalesce(sum(total_cost_with_vat), 0)      AS awarded_value
FROM proc.procurement_act
WHERE type = 'contract' AND submission_date IS NOT NULL
  AND proc.is_analytics_eligible(adam, total_cost_with_vat, cancelled)
GROUP BY date_trunc('month', submission_date)
ORDER BY month;

-- By-CPV: exclude the same contracts; notices are not value-capped (their
-- estimates aren't the headline figure) but cancelled/flagged still excluded.
DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_cpv CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_cpv AS
WITH items AS (
    SELECT a.type, a.adam,
           substr(oc.cpv_code, 1, 2)  AS division,
           od.cost_without_vat        AS item_cost
    FROM proc.procurement_act a
    JOIN proc.act_object_detail od ON od.adam = a.adam
    JOIN proc.object_detail_cpv  oc ON oc.object_detail_id = od.id
    WHERE a.type IN ('notice','contract') AND NOT a.cancelled
      AND NOT EXISTS (SELECT 1 FROM proc.v_act_annotation_current an
                      WHERE an.adam = a.adam AND an.flag = 'suspicious')
      AND (a.type <> 'contract'
           OR a.total_cost_with_vat IS NULL
           OR a.total_cost_with_vat <= proc.analytics_value_ceiling())
),
agg AS (
    SELECT division,
        count(DISTINCT adam) FILTER (WHERE type='contract')        AS contract_count,
        coalesce(sum(item_cost) FILTER (WHERE type='contract'), 0) AS contract_value,
        count(DISTINCT adam) FILTER (WHERE type='notice')          AS notice_count,
        coalesce(sum(item_cost) FILTER (WHERE type='notice'), 0)   AS notice_value
    FROM items GROUP BY division
)
SELECT agg.*,
    (SELECT description FROM proc.cpv_code
       WHERE cpv_code LIKE agg.division || '000000-_' LIMIT 1) AS label
FROM agg ORDER BY contract_value DESC;
CREATE INDEX ix_mv_cpv_cvalue ON proc.mv_analytics_cpv (contract_value DESC);

-- Refresh function: re-normalise procedure families (if present) then all MVs.
CREATE OR REPLACE FUNCTION proc.refresh_analytics() RETURNS void AS $$
BEGIN
    BEGIN
        PERFORM proc.refresh_procedure_family();
    EXCEPTION WHEN undefined_function THEN
        NULL;  -- procedure_family migration not applied; skip
    END;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_totals;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_authorities;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_contractors;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_monthly;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv;
END;
$$ LANGUAGE plpgsql;
