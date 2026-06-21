-- editable_names_migration.sql
-- ===========================================================================
-- Lets curators correct garbled / wrong entity names directly. The edit
-- overwrites `name` (simple, what the rest of the app reads), but the FIRST
-- time a name is edited we snapshot the original ingested value into
-- name_original — a one-column safety net so a bad edit or re-import is
-- recoverable, without changing how anything reads `name`.
--
-- Run on BOTH local and Supabase (code that reads name_original ships with it):
--   psql "$DATABASE_URL"        -f editable_names_migration.sql   # local
--   psql "<supabase-direct-url>" -f editable_names_migration.sql   # production
-- ===========================================================================

ALTER TABLE proc.economic_operator
    ADD COLUMN IF NOT EXISTS name_original   text,
    ADD COLUMN IF NOT EXISTS name_edited_at  timestamptz;

ALTER TABLE proc.authority
    ADD COLUMN IF NOT EXISTS name_original   text,
    ADD COLUMN IF NOT EXISTS name_edited_at  timestamptz;
