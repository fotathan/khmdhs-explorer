-- full_text_migration.sql
-- Adds a "Full Text" field to procurement acts: the readable text extracted
-- from an act's official KHMDHS attachment(s).
--
-- Storage choice: a plain column on proc.procurement_act (simplest). NOTE this
-- means a future re-import of the same ADAM can overwrite it — the ingester is
-- written to fill it ONLY when empty, so it won't clobber a value that's
-- already there, but a forced full re-import would. If you later want it to
-- survive re-imports unconditionally, move it to an overlay table keyed by adam
-- (same pattern as act_annotation) and this column can be dropped.
--
-- Idempotent: safe to run more than once.

SET search_path TO proc, public;

-- 1. The column.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS full_text text;

-- 2. Bookkeeping: when and how the text was last extracted, so the UI can show
--    provenance and the ingester can tell auto-filled from manually-edited.
--    'source' is free text, e.g. 'auto:import', 'manual:24PROC… → Διακήρυξη.pdf'.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS full_text_extracted_at timestamptz;
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS full_text_source text;

-- 3. Full-text search index (Greek-aware where available, else 'simple').
--    Build a tsvector on the fly via a GIN expression index so search can use
--    it later without a stored generated column. Greek stemming needs the
--    'greek' text-search config; if your Postgres lacks it, swap to 'simple'.
DO $$
BEGIN
    -- Prefer the 'greek' config if it exists on this server.
    IF EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'greek') THEN
        EXECUTE $idx$
            CREATE INDEX IF NOT EXISTS ix_act_full_text_gr
            ON proc.procurement_act
            USING gin (to_tsvector('greek', coalesce(full_text, '')))
        $idx$;
    ELSE
        EXECUTE $idx$
            CREATE INDEX IF NOT EXISTS ix_act_full_text_simple
            ON proc.procurement_act
            USING gin (to_tsvector('simple', coalesce(full_text, '')))
        $idx$;
    END IF;
END
$$;

-- A trigram index would also help ILIKE '%word%' fallback searches, but needs
-- pg_trgm; left out to keep this migration dependency-free. Add later if wanted:
--   CREATE EXTENSION IF NOT EXISTS pg_trgm;
--   CREATE INDEX ix_act_full_text_trgm ON proc.procurement_act
--     USING gin (full_text gin_trgm_ops);
