-- migrations/20260710165807_tristate_boolean_backfill.sql
-- tristate boolean backfill
--
-- These act booleans are tri-state: NULL = the source never stated it (Not
-- specified), TRUE = Yes, FALSE = explicit No. The columns below are ONLY ever
-- populated by the manual create/edit act form — no ingest (KHMDHS/Diavgeia/TED)
-- writes them. The old form used a checkbox, so "left unchecked" was stored as
-- FALSE instead of NULL, i.e. an implicit No that was never actually stated.
--
-- The form now offers Yes/No/Not-specified (defaults to NULL), so new acts are
-- correct. This one-off fixes the existing checkbox-artifact rows: on these
-- form-only columns, FALSE could only have come from an unchecked box, so it is
-- reset to NULL (Not specified). Columns fed by the API with genuine explicit
-- FALSE (e.g. mixed_contract, no_end_date, option_right, amends_previous) are
-- deliberately NOT touched.
--
-- Idempotent (re-running changes nothing once the rows are NULL).

BEGIN;

UPDATE proc.procurement_act SET divided_into_lots          = NULL WHERE divided_into_lots          IS FALSE;
UPDATE proc.procurement_act SET is_framework_agreement     = NULL WHERE is_framework_agreement     IS FALSE;
UPDATE proc.procurement_act SET alternative_offers_allowed = NULL WHERE alternative_offers_allowed IS FALSE;
UPDATE proc.procurement_act SET prolongation_option        = NULL WHERE prolongation_option        IS FALSE;
UPDATE proc.procurement_act SET vat_included               = NULL WHERE vat_included               IS FALSE;

COMMIT;
