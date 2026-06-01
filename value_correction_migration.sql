-- ============================================================================
-- Migration: manual value corrections (overlay).
-- Run:  psql "$LOCAL"  -f value_correction_migration.sql
--       psql "$REMOTE" -f value_correction_migration.sql
--       then: SELECT proc.refresh_analytics();
--
-- WHY: KHMDHS sometimes carries a wrong contract value (e.g. a contract worth
-- €6,138 recorded as €300,000,000 because the contractor's VAT number landed in
-- the value field). We must be able to correct the figure used in calculations
-- WITHOUT destroying what the source reported.
--
-- DESIGN (same overlay principle as annotations/merges — harvested data is
-- never mutated): a correction is just an annotation that carries a
-- corrected_value. The original total_cost_with_vat on procurement_act is left
-- exactly as KHMDHS sent it. Calculations use the RESOLVED value:
--     resolved = COALESCE(corrected_value, total_cost_with_vat)
-- The detail page shows the original (struck through) alongside the correction.
-- Corrections survive re-imports because they live in the overlay table.
-- ============================================================================

ALTER TABLE proc.act_annotation
    ADD COLUMN IF NOT EXISTS corrected_value numeric(18,2);

-- Rebuild the "current annotation" view to expose corrected_value.
CREATE OR REPLACE VIEW proc.v_act_annotation_current AS
SELECT DISTINCT ON (adam)
       adam, id, note, tags, flag, author, created_at, corrected_value
FROM proc.act_annotation
WHERE NOT superseded
ORDER BY adam, created_at DESC;

-- Helper: the resolved value for an act = correction if present, else source.
-- Used by analytics and the aggregations page so corrections flow into totals.
CREATE OR REPLACE FUNCTION proc.resolved_value(p_adam text, p_source numeric)
RETURNS numeric AS $$
    SELECT COALESCE(
        (SELECT corrected_value FROM proc.v_act_annotation_current
         WHERE adam = p_adam AND corrected_value IS NOT NULL),
        p_source);
$$ LANGUAGE sql STABLE;
