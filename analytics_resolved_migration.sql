-- ============================================================================
-- Migration: make analytics use RESOLVED (correction-aware) values.
-- Run AFTER value_correction_migration.sql:
--   psql "$LOCAL"  -f analytics_resolved_migration.sql
--   psql "$REMOTE" -f analytics_resolved_migration.sql
--   then: SELECT proc.refresh_analytics();
--
-- Supersedes the view definitions in analytics_exclusion_migration.sql. The
-- only change: every place that used a.total_cost_with_vat now uses
-- proc.resolved_value(adam, total_cost_with_vat), so a manual correction flows
-- into:
--   * the value shown/summed, AND
--   * the €500M ceiling test (correcting €300M→€6,138 makes the contract
--     eligible again and it re-enters the analytics).
-- Suspicious-flag and cancelled exclusions are unchanged.
-- ============================================================================

-- Eligibility now tests the RESOLVED value against the ceiling.
CREATE OR REPLACE FUNCTION proc.is_analytics_eligible(
        p_adam text, p_value numeric, p_cancelled boolean)
RETURNS boolean AS $$
    SELECT (NOT coalesce(p_cancelled, false))
       AND (proc.resolved_value(p_adam, p_value) IS NULL
            OR proc.resolved_value(p_adam, p_value) <= proc.analytics_value_ceiling())
       AND NOT EXISTS (
            SELECT 1 FROM proc.v_act_annotation_current a
            WHERE a.adam = p_adam AND a.flag = 'suspicious');
$$ LANGUAGE sql STABLE;

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_totals CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_totals AS
SELECT
    count(*)                                                AS n_contracts,
    coalesce(sum(proc.resolved_value(adam, total_cost_with_vat)), 0) AS awarded_value,
    count(DISTINCT authority_id)                            AS n_authorities,
    min(submission_date)                                    AS earliest,
    max(submission_date)                                    AS latest
FROM proc.procurement_act
WHERE type = 'contract'
  AND proc.is_analytics_eligible(adam, total_cost_with_vat, cancelled);

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_authorities CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_authorities AS
SELECT
    proc.canon_authority(a.authority_id)      AS authority_id,
    count(*)                                  AS n_contracts,
    coalesce(sum(proc.resolved_value(a.adam, a.total_cost_with_vat)), 0) AS awarded_value
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
                          proc.resolved_value(a.adam, a.total_cost_with_vat))), 0) AS awarded_value
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
    coalesce(sum(proc.resolved_value(adam, total_cost_with_vat)), 0) AS awarded_value
FROM proc.procurement_act
WHERE type = 'contract' AND submission_date IS NOT NULL
  AND proc.is_analytics_eligible(adam, total_cost_with_vat, cancelled)
GROUP BY date_trunc('month', submission_date)
ORDER BY month;

-- CPV view is line-item based (its values come from act_object_detail, not the
-- contract total), so corrections to the contract value don't change it; we
-- only refresh the eligibility filter for cancelled/flagged/ceiling via the
-- resolved-aware is_analytics_eligible already used. Rebuild to pick up the
-- updated function reference for the ceiling test on the contract total.
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
           OR proc.resolved_value(a.adam, a.total_cost_with_vat) IS NULL
           OR proc.resolved_value(a.adam, a.total_cost_with_vat) <= proc.analytics_value_ceiling())
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
