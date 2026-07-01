-- ============================================================================
-- attachment_migration.sql — store uploaded attachments per act and search
-- INSIDE them (pdf/docx/xlsx/csv, incl. files inside zips).
--
-- LOCAL-ONLY for now: the raw bytes live on the local filesystem (see
-- app/attachments.py), and only small text + metadata live here. The whole
-- feature is gated behind ATTACHMENTS_ENABLED, so PROD (Supabase free tier)
-- never gets this table or the search clause — apply this migration ONLY to the
-- local DB until we move storage to object storage.
--
--   psql "$LOCAL_DATABASE_URL" -f attachment_migration.sql
-- ============================================================================

BEGIN;
SET search_path TO proc, public;

CREATE TABLE IF NOT EXISTS proc.act_attachment (
    id              bigserial PRIMARY KEY,
    adam            text NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    filename        text,
    mimetype        text,
    size_bytes      bigint,
    checksum        text,                       -- sha256 of the stored bytes
    storage_backend text DEFAULT 'local_fs',
    storage_ref     text,                        -- path/key under ATTACHMENTS_DIR
    extracted_text  text,                        -- searchable content (incl. zipped files)
    -- Greek-stemmed index over the extracted text; regenerates automatically.
    content_tsv     tsvector GENERATED ALWAYS AS
                    (to_tsvector('greek', coalesce(extracted_text, ''))) STORED,
    n_inner         integer,                      -- # of files extracted (zips > 1)
    uploaded_by     text,
    uploaded_at     timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_attachment_adam    ON proc.act_attachment(adam);
CREATE INDEX IF NOT EXISTS ix_attachment_content ON proc.act_attachment USING gin (content_tsv);

COMMIT;
