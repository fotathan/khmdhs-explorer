-- extracted_table_tsv_migration.sql
-- ===========================================================================
-- Adds a searchable, Greek-stemmed tsvector over each extracted table's cell
-- content, so the main search page can filter acts by "their published tables
-- contain X" — combinable with every other filter (type, date, full-text…).
--
-- Mirrors full_text_tsv_migration.sql: a STORED generated column (Postgres
-- maintains it automatically whenever `rows` changes — never stale, no app
-- code), plus a GIN index restricted to published rows (the only ones the
-- public filter ever reads).
--
-- WHY THIS EXPRESSION
--   `rows` is JSONB: an array of row-arrays of text cells, e.g.
--       [["Είδος","Αξία"],["Χαρτί Α4","4,50"]]
--   jsonb_path_query_array(rows, '$[*][*]') flattens every cell (all rows, all
--   columns) into a single JSON array; ::text renders it to one string. The
--   JSON punctuation (brackets/quotes/commas) is just token separators to
--   to_tsvector, so the Greek words inside still tokenize and stem correctly.
--   Both jsonb_path_query_array and to_tsvector(<const cfg>, …) are IMMUTABLE,
--   which a GENERATED column requires. (A jsonb_array_elements-based flatten
--   would NOT be allowed here — it's set-returning, not immutable-scalar.)
--
-- VERIFY BEFORE THE INDEX (cheap sanity check on a known published table):
--   SELECT to_tsvector('greek',
--            coalesce(jsonb_path_query_array(rows, '$[*][*]')::text, ''))
--   FROM proc.extracted_table WHERE is_published LIMIT 1;
--   -- you should see stemmed Greek lexemes from the cell text.
--
-- COST: this table is small (curator-published tables, not 2.7M acts), so the
-- column add + index build are quick. CONCURRENTLY still used so it never
-- blocks reads.
--
-- Run with psql (CREATE INDEX CONCURRENTLY can't run inside a transaction):
--   psql "$DATABASE_URL" -f extracted_table_tsv_migration.sql
-- ===========================================================================

SET search_path TO proc, public;

-- 1. Stored, auto-maintained tsvector over all cell text of the table.
ALTER TABLE proc.extracted_table
    ADD COLUMN IF NOT EXISTS content_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector('greek',
            coalesce(jsonb_path_query_array(rows, '$[*][*]')::text, ''))
    ) STORED;

-- 2. GIN index, restricted to published rows — the only set the public filter
--    queries. Partial index keeps it small and writes cheap.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_extracted_table_content_tsv
    ON proc.extracted_table USING gin (content_tsv)
    WHERE is_published;

ANALYZE proc.extracted_table;
