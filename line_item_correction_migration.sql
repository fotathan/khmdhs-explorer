-- ============================================================================
-- Migration: manual correction of LINE-ITEM values (act_object_detail).
-- Run AFTER value_correction_migration.sql:
--   psql "$LOCAL"  -f line_item_correction_migration.sql
--   psql "$REMOTE" -f line_item_correction_migration.sql
--   then: SELECT proc.refresh_analytics();   (CPV panel uses these values)
--
-- WHY: the same KHMDHS source errors that wreck the act-level value also appear
-- in the per-line-item value (act_object_detail.cost_without_vat). Those
-- line-item values ARE summed into the by-CPV analytics panel, so a garbage
-- €300M line item inflates its CPV division. We need to correct line items, fix
-- the CPV totals, and keep the original — same overlay principle as everywhere.
--
-- KEYING: corrections are keyed by (adam, line_no), NOT by the line-item row id.
-- Row ids are reassigned if an act is re-ingested (old rows deleted, new ones
-- inserted), which would orphan corrections. (adam, line_no) is the stable
-- business key — "the Nth line of this act" — so corrections survive re-imports.
-- ============================================================================

CREATE TABLE IF NOT EXISTS proc.line_item_correction (
    id              bigserial PRIMARY KEY,
    adam            text NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    line_no         integer NOT NULL,
    corrected_cost_without_vat numeric(18,2),
    note            text,
    author          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    superseded      boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_lic_adam_line
    ON proc.line_item_correction (adam, line_no) WHERE NOT superseded;

-- Current (non-superseded) correction per (adam, line_no).
CREATE OR REPLACE VIEW proc.v_line_item_correction_current AS
SELECT DISTINCT ON (adam, line_no)
       adam, line_no, corrected_cost_without_vat, note, author, created_at
FROM proc.line_item_correction
WHERE NOT superseded
ORDER BY adam, line_no, created_at DESC;

-- Resolved line-item cost: correction if present, else the source value.
-- Joined by (adam, line_no) so it survives row-id churn.
CREATE OR REPLACE FUNCTION proc.resolved_item_cost(
        p_adam text, p_line_no integer, p_source numeric)
RETURNS numeric AS $$
    SELECT COALESCE(
        (SELECT corrected_cost_without_vat FROM proc.v_line_item_correction_current
         WHERE adam = p_adam AND line_no = p_line_no
           AND corrected_cost_without_vat IS NOT NULL),
        p_source);
$$ LANGUAGE sql STABLE;
