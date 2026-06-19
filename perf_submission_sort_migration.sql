-- perf_submission_sort_migration.sql
-- ===========================================================================
-- Fixes the two biggest interactive costs found by EXPLAIN ANALYZE:
--   * default list sort (submission_date DESC) did a full parallel seq scan +
--     top-N sort of 2.67M rows (~1.5s every page load) — no index existed.
--   * filtered+sorted pages (e.g. type=notice) likewise re-sorted from scratch.
--
-- Strategy: index submission_date for the default sort, and add composite
-- (type, submission_date) so the very common "filter by type, newest first"
-- page becomes an index scan with no separate sort step.
--
-- Run once (local AND Supabase). Safe to re-run. CONCURRENTLY so it doesn't
-- lock the table against reads/writes while building on the 2.67M-row table.
-- NOTE: CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so run
-- this file with psql directly (not wrapped in BEGIN/COMMIT).
-- ===========================================================================

SET search_path TO proc, public;

-- Default sort: submission_date DESC NULLS LAST. A DESC NULLS LAST index lets
-- the planner read the top-N straight from the index, skipping the sort.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_submission_date
    ON proc.procurement_act (submission_date DESC NULLS LAST);

-- Filtered-by-type + newest-first (the most common combination on the list
-- page and the type tabs). Composite so both the filter and the order come
-- from one index.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_type_submission
    ON proc.procurement_act (type, submission_date DESC NULLS LAST);

-- Authority detail pages list that authority's acts newest-first.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_authority_submission
    ON proc.procurement_act (authority_id, submission_date DESC NULLS LAST);

-- Deadline sort ("closest deadline" / still-open) — ASC NULLS LAST.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_final_submission
    ON proc.procurement_act (final_submission_date ASC NULLS LAST);

ANALYZE proc.procurement_act;
