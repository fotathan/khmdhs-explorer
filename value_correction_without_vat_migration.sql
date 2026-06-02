-- ============================================================================
-- Migration: manual correction of the WITHOUT-VAT value too.
-- Run AFTER value_correction_migration.sql:
--   psql "$LOCAL"  -f value_correction_without_vat_migration.sql
--   psql "$REMOTE" -f value_correction_without_vat_migration.sql
--
-- WHY: a contract can have BOTH values wrong at the source (e.g. with-VAT shows
-- €6,138 after correction but without-VAT still shows €300M because the VAT
-- field also got mangled). We already correct total_cost_with_vat; this adds the
-- parallel corrected_value_without_vat so the without-VAT figure can be fixed
-- too. Same overlay principle: the source value is never mutated.
--
-- NOTE: the without-VAT act-level value is informational on the detail page and
-- is NOT summed into any analytics aggregate (those use with-VAT), so this is a
-- display-consistency correction, not a calculation one. No analytics rebuild
-- is needed for this migration.
-- ============================================================================

ALTER TABLE proc.act_annotation
    ADD COLUMN IF NOT EXISTS corrected_value_without_vat numeric(18,2);

-- Rebuild the "current annotation" view to expose the new column alongside the
-- existing corrected_value.
CREATE OR REPLACE VIEW proc.v_act_annotation_current AS
SELECT DISTINCT ON (adam)
       adam, id, note, tags, flag, author, created_at,
       corrected_value, corrected_value_without_vat
FROM proc.act_annotation
WHERE NOT superseded
ORDER BY adam, created_at DESC;
