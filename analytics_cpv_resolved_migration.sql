-- ============================================================================
-- Migration: by-CPV analytics uses RESOLVED line-item costs.
-- Run AFTER line_item_correction_migration.sql:
--   psql "$LOCAL"  -f analytics_cpv_resolved_migration.sql
--   psql "$REMOTE" -f analytics_cpv_resolved_migration.sql
--   then: SELECT proc.refresh_analytics();
--
-- Supersedes the mv_analytics_cpv definition in analytics_resolved_migration.
-- Only change: item_cost now uses proc.resolved_item_cost(adam, line_no, cost)
-- so a corrected line-item value flows into the CPV division totals. The
-- contract-level eligibility (cancelled / suspicious / with-VAT ceiling) is
-- unchanged.
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_cpv CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_cpv AS
WITH items AS (
    SELECT a.type, a.adam,
           substr(oc.cpv_code, 1, 2)  AS division,
           proc.resolved_item_cost(a.adam, od.line_no, od.cost_without_vat) AS item_cost
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
