-- full_text_tsv_migration.sql
-- ===========================================================================
-- Makes full-text search FAST and rank-able.
--
-- Problem (seen via EXPLAIN ANALYZE): the existing index
-- ix_act_full_text_gr indexes the *expression* to_tsvector('greek', full_text).
-- The index FINDS matches in ~4ms, but ts_rank() and the recheck then RECOMPUTE
-- that tsvector from the full document text for every candidate row — ~3s total
-- on a common term. Recomputing a tsvector per row is the cost.
--
-- Fix: store the tsvector once in a GENERATED column (Postgres maintains it
-- automatically whenever full_text changes — no app changes, never stale), and
-- index that. Both the match check and ts_rank() then read the stored vector.
-- Typical result: seconds -> tens of milliseconds.
--
-- Postgres 16: STORED generated columns fully supported.
--
-- COST: adding a STORED generated column rewrites the table (writes one
-- tsvector per row). Most rows have empty full_text, so the vectors are tiny,
-- but on 2.7M rows the ALTER still takes a little while and takes a brief lock.
-- Run it when the app isn't under load. On Supabase, prefer the DIRECT
-- connection (5432) and a quiet moment.
--
-- Run with psql (the CREATE INDEX CONCURRENTLY can't be in a transaction):
--   psql "$DATABASE_URL" -f full_text_tsv_migration.sql
-- ===========================================================================

SET search_path TO proc, public;

-- 1. The stored, auto-maintained tsvector. Greek config (confirmed present).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS full_text_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('greek', coalesce(full_text, ''))) STORED;

-- 2. GIN index on the stored column. CONCURRENTLY so it doesn't block reads
--    while building. (If a previous run half-created it, drop and re-run.)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_full_text_tsv
    ON proc.procurement_act USING gin (full_text_tsv);

-- 3. The old expression index (ix_act_full_text_gr) is now redundant — the
--    stored-column index supersedes it. Dropping it reclaims space and speeds
--    writes. Commented out so YOU decide: confirm the new search works first,
--    then drop it.
-- DROP INDEX CONCURRENTLY IF EXISTS proc.ix_act_full_text_gr;

ANALYZE proc.procurement_act;
