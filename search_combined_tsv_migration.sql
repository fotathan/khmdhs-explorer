-- search_combined_tsv_migration.sql
-- ===========================================================================
-- Makes the MAIN search box able to search title + document full text together,
-- fast, by adding a single combined tsvector that covers both — indexed with
-- GIN like the existing full_text_tsv.
--
-- WHY: the main `q` box matched only the title, with a slow LIKE '%...%'
-- substring scan. To let one box search "everything" (title + full text) WITHOUT
-- degrading to a substring scan over 2.7M rows, both must live in one indexed
-- tsvector. (Tables are already separately indexed via extracted_table.content_tsv
-- and are OR-ed in at query time by an EXISTS clause.)
--
-- Greek normalisation matches the existing full_text_tsv exactly:
--   to_tsvector('greek', ...). Title is weighted 'A' (most important),
--   full text 'B', so title hits rank above body hits if ranking is used.
--
-- BEHAVIOUR CHANGE: tsvector matches whole words/stems, not arbitrary
-- substrings. Typing a full word ('καθαρισμός') matches; a partial fragment
-- ('καθαρ') will NOT, unlike the old title LIKE. This is generally more
-- relevant, but it is a change — the ADAM/reference-number fast path (exact
-- prefix) is preserved separately in code.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f search_combined_tsv_migration.sql
--   psql "<supabase-direct-url>" -f search_combined_tsv_migration.sql
-- ===========================================================================

-- Combined, auto-maintained tsvector: title (weight A) + full_text (weight B).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('greek', coalesce(title, '')),     'A') ||
        setweight(to_tsvector('greek', coalesce(full_text, '')), 'B')
    ) STORED;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_search_tsv
    ON proc.procurement_act USING gin (search_tsv);

ANALYZE proc.procurement_act;

-- Optional CPV-description search support: a tsvector on cpv_code.description,
-- so the CPV autosuggest can match by Greek description too (not only by code).
ALTER TABLE proc.cpv_code
    ADD COLUMN IF NOT EXISTS description_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('greek', coalesce(description, ''))) STORED;

CREATE INDEX IF NOT EXISTS ix_cpv_description_tsv
    ON proc.cpv_code USING gin (description_tsv);

-- Prefix lookups on the code itself (for "type 331 → 331*") use the PK, but a
-- text_pattern_ops index makes LIKE 'NNN%' fast and case-stable.
CREATE INDEX IF NOT EXISTS ix_cpv_code_prefix
    ON proc.cpv_code (cpv_code text_pattern_ops);

ANALYZE proc.cpv_code;
