-- ============================================================================
-- Analytics: by-CPV-division breakdown (notices + contracts, side by side).
-- Run once after analytics_migration.sql:
--   psql "$REMOTE" -f analytics_cpv_migration.sql
--   psql "$REMOTE" -c "SELECT proc.refresh_analytics();"
--
-- WHAT IT MEASURES, per 2-digit CPV division:
--   * contracts: distinct CONTRACT acts that include a line item in this
--     division, and the summed line-item cost_without_vat of those items.
--   * notices:   same, for NOTICE acts.
-- Notices and contracts are kept SEPARATE (a notice is an intention to buy,
-- a contract is an award — adding them would be meaningless).
--
-- HONEST CAVEATS:
--   * Values are WITHOUT VAT (line items store cost_without_vat), so they do
--     NOT reconcile to the with-VAT headline awarded total. They answer
--     "relative distribution across categories", not exact euros.
--   * Value is summed from the line items themselves (each item's own cost),
--     so a multi-line contract is NOT multiplied — no double-count.
--   * Cancelled acts excluded.
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_cpv CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_cpv AS
WITH items AS (
    -- one row per (act, line item, division), with the item's own cost.
    SELECT a.type,
           a.adam,
           substr(oc.cpv_code, 1, 2)        AS division,
           od.cost_without_vat              AS item_cost
    FROM proc.procurement_act a
    JOIN proc.act_object_detail od ON od.adam = a.adam
    JOIN proc.object_detail_cpv  oc ON oc.object_detail_id = od.id
    WHERE a.type IN ('notice','contract') AND NOT a.cancelled
),
agg AS (
    SELECT
        division,
        count(DISTINCT adam) FILTER (WHERE type='contract')        AS contract_count,
        coalesce(sum(item_cost) FILTER (WHERE type='contract'), 0) AS contract_value,
        count(DISTINCT adam) FILTER (WHERE type='notice')          AS notice_count,
        coalesce(sum(item_cost) FILTER (WHERE type='notice'), 0)   AS notice_value
    FROM items
    GROUP BY division
)
SELECT
    agg.*,
    (SELECT description FROM proc.cpv_code
       WHERE cpv_code LIKE agg.division || '000000-_'
       LIMIT 1) AS label
FROM agg
ORDER BY contract_value DESC;
CREATE INDEX ix_mv_cpv_cvalue ON proc.mv_analytics_cpv (contract_value DESC);

-- Extend the one-shot refresh to include every analytics view (5 total).
CREATE OR REPLACE FUNCTION proc.refresh_analytics() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW proc.mv_analytics_totals;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_authorities;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_contractors;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_monthly;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv;
END;
$$ LANGUAGE plpgsql;
