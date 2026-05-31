-- ============================================================================
-- Analytics materialized views — deduplicated "awarded value".
-- Run once:  psql "$REMOTE" -f analytics_migration.sql
-- Refresh after backfills:  SELECT proc.refresh_analytics();
--
-- DEFINITION of "awarded value" used throughout:
--   the total_cost_with_vat of CONTRACT acts only.
--   * payments are EXCLUDED (summing contracts + payments double-counts the
--     same money — a payment is disbursement against a contract).
--   * notices are EXCLUDED (pre-award estimates, not awards).
-- So every figure here answers "how much was AWARDED", never mixing in
-- disbursements. Counts are of contracts.
--
-- MERGE-AWARE: contractor/authority identity is resolved through the
-- entity_group/entity_member overlay, so merged duplicates roll into one line.
-- ============================================================================

-- Helper: canonical key for a contractor VAT (itself if unmerged).
CREATE OR REPLACE FUNCTION proc.canon_contractor(vat text) RETURNS text AS $$
  SELECT COALESCE(g.canonical_key, vat)
  FROM (SELECT vat) s
  LEFT JOIN proc.entity_member m
    ON m.kind='contractor' AND m.member_key = vat
  LEFT JOIN proc.entity_group g ON g.id = m.group_id;
$$ LANGUAGE sql STABLE;

-- Helper: canonical key for an authority org_id.
CREATE OR REPLACE FUNCTION proc.canon_authority(org text) RETURNS text AS $$
  SELECT COALESCE(g.canonical_key, org)
  FROM (SELECT org) s
  LEFT JOIN proc.entity_member m
    ON m.kind='authority' AND m.member_key = org
  LEFT JOIN proc.entity_group g ON g.id = m.group_id;
$$ LANGUAGE sql STABLE;

-- ---------------------------------------------------------------------------- #
-- 1. Headline totals (single row).
-- ---------------------------------------------------------------------------- #
DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_totals CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_totals AS
SELECT
    count(*)                                  AS n_contracts,
    coalesce(sum(total_cost_with_vat), 0)     AS awarded_value,
    count(DISTINCT authority_id)              AS n_authorities,
    min(submission_date)                      AS earliest,
    max(submission_date)                      AS latest
FROM proc.procurement_act
WHERE type = 'contract' AND NOT cancelled;

-- ---------------------------------------------------------------------------- #
-- 2. Top authorities by awarded value (merge-aware).
-- ---------------------------------------------------------------------------- #
DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_authorities CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_authorities AS
SELECT
    proc.canon_authority(a.authority_id)      AS authority_id,
    count(*)                                  AS n_contracts,
    coalesce(sum(a.total_cost_with_vat), 0)   AS awarded_value
FROM proc.procurement_act a
WHERE a.type = 'contract' AND NOT a.cancelled AND a.authority_id IS NOT NULL
GROUP BY proc.canon_authority(a.authority_id);
CREATE INDEX ix_mv_auth_value ON proc.mv_analytics_authorities (awarded_value DESC);

-- ---------------------------------------------------------------------------- #
-- 3. Top contractors by awarded value (merge-aware).
--    Uses per-operator awarded_value_with_vat where present, else the contract
--    total. Each (contract, operator) counted once.
-- ---------------------------------------------------------------------------- #
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
WHERE a.type = 'contract' AND NOT a.cancelled
GROUP BY proc.canon_contractor(eo.vat_number);
CREATE INDEX ix_mv_contractor_value ON proc.mv_analytics_contractors (awarded_value DESC);

-- ---------------------------------------------------------------------------- #
-- 4. Monthly awarded-value trend (by submission month).
-- ---------------------------------------------------------------------------- #
DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_monthly CASCADE;
CREATE MATERIALIZED VIEW proc.mv_analytics_monthly AS
SELECT
    date_trunc('month', submission_date)::date AS month,
    count(*)                                   AS n_contracts,
    coalesce(sum(total_cost_with_vat), 0)      AS awarded_value
FROM proc.procurement_act
WHERE type = 'contract' AND NOT cancelled AND submission_date IS NOT NULL
GROUP BY date_trunc('month', submission_date)
ORDER BY month;

-- ---------------------------------------------------------------------------- #
-- One-shot refresh function — run after each backfill / merge change.
-- ---------------------------------------------------------------------------- #
CREATE OR REPLACE FUNCTION proc.refresh_analytics() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW proc.mv_analytics_totals;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_authorities;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_contractors;
    REFRESH MATERIALIZED VIEW proc.mv_analytics_monthly;
END;
$$ LANGUAGE plpgsql;
