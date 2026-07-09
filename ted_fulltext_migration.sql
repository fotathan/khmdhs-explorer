-- ted_fulltext_migration.sql
--
-- Opt-in TED full-text: a notice's Description (Συνοπτική Παρουσίαση) and body
-- (Προκήρυξη) live in its eForms/UBL XML, not the Search-API metadata. Store
-- them source-native on proc.ted_notice; ted_ingest.project_all copies the full
-- text into procurement_act (surfaced in the act's "Πλήρες κείμενο" panel).
--
-- Safe/idempotent.

BEGIN;

ALTER TABLE proc.ted_notice
    ADD COLUMN IF NOT EXISTS description          text,
    ADD COLUMN IF NOT EXISTS full_text            text,
    ADD COLUMN IF NOT EXISTS full_text_extracted_at timestamptz;

COMMIT;
