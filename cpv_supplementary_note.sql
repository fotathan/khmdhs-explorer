-- ============================================================================
-- cpv_supplementary_note.sql — annotate the handful of CPV *supplementary
-- vocabulary* codes that legitimately have NO category/subcategory mapping.
--
-- The category/subcategory taxonomy (proc.cpv_category_map) covers 100% of the
-- standard numeric CPV codes (########-#). The only codes left out are CPV
-- supplementary-vocabulary qualifiers (two letters + two digits + check digit,
-- e.g. DA03-0, FB02-0, PA01-7, TA28-3) — these are NOT main CPV classes, so by
-- design they don't belong to the taxonomy. They were stubbed into proc.cpv_code
-- by ingestion when an act line item referenced one, and arrived with no
-- description, which makes them look like a mapping gap. This sets a short note
-- in the (otherwise empty) description so they read as intentional, not a bug.
--
-- Safe + idempotent: scoped to non-standard-format codes that have no map row,
-- and only fills a description that is currently empty (won't overwrite).
--   psql "$DATABASE_URL" -f cpv_supplementary_note.sql
-- ============================================================================

UPDATE proc.cpv_code c
SET description    = COALESCE(NULLIF(c.description, ''),
        'Συμπληρωματικό λεξιλόγιο CPV — εκτός ταξινόμησης κατηγορίας/υποκατηγορίας'),
    description_en = COALESCE(NULLIF(c.description_en, ''),
        'CPV supplementary vocabulary — outside the category/subcategory taxonomy')
WHERE c.cpv_code !~ '^[0-9]{8}-[0-9]$'
  AND NOT EXISTS (SELECT 1 FROM proc.cpv_category_map m WHERE m.cpv_code = c.cpv_code);
