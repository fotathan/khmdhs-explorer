-- act_attachment_on_prod
--
-- Creates proc.act_attachment where it does not exist yet — that is, on PROD.
--
-- Why this file exists: attachment_migration.sql (repo root) was written as
-- LOCAL-ONLY and never run on prod. When migration tracking started
-- (2026-07-09), every file then in the manifest was recorded on prod as a
-- BASELINE ("already applied"), that one included. So `migrate.py status`
-- showed nothing pending while the table was missing, and the next migration
-- touching it failed ("relation proc.act_attachment does not exist",
-- 2026-09-24). A baselined file is never re-run, hence this new one.
--
-- Same definition as attachment_migration.sql. Every statement is IF NOT
-- EXISTS, so on the local DB (where the table exists) this is a no-op. The
-- table is new and empty on prod: the FK and the GIN index cost nothing.
-- Must run BEFORE 20260924100000_attachment_text_cap.sql.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
-- Do NOT tick "Run and enable RLS" for this table in Supabase.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.act_attachment (
    id              bigserial PRIMARY KEY,
    adam            text NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    filename        text,
    mimetype        text,
    size_bytes      bigint,
    checksum        text,
    storage_backend text DEFAULT 'local_fs',
    storage_ref     text,
    extracted_text  text,
    content_tsv     tsvector GENERATED ALWAYS AS
                    (to_tsvector('greek', coalesce(extracted_text, ''))) STORED,
    n_inner         integer,
    uploaded_by     text,
    uploaded_at     timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_attachment_adam    ON proc.act_attachment(adam);
CREATE INDEX IF NOT EXISTS ix_attachment_content ON proc.act_attachment USING gin (content_tsv);

COMMIT;
