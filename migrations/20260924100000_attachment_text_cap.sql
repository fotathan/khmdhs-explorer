-- attachment_text_cap
--
-- Attachments go to production (Supabase Storage for the bytes). The extracted
-- text stays in Postgres for search-inside and the AI summary, but capped per
-- file at ATTACH_TEXT_MAX_CHARS (default 200,000) because the prod database is
-- a 500 MB free tier. This column records the FULL length when the cap cut
-- something (NULL = nothing was cut), so truncation is never silent.
--
-- Nullable, no default, no rewrite. Existing rows were never capped → NULL.
-- No DO $$ blocks: the Supabase dashboard editor splits them.

BEGIN;

ALTER TABLE proc.act_attachment
    ADD COLUMN IF NOT EXISTS text_total_chars integer;

COMMENT ON COLUMN proc.act_attachment.text_total_chars IS
    'Full extracted length when extracted_text was capped (ATTACH_TEXT_MAX_CHARS); NULL = not capped.';

COMMIT;
