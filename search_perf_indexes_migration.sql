-- ============================================================================
-- Migration: performance indexes for the search / list pages.
-- Run:  psql "$LOCAL"  -f search_perf_indexes_migration.sql
--       psql "$REMOTE" -f search_perf_indexes_migration.sql
--
-- WHY: the search page's DEFAULT sort is submission_date DESC, and the most
-- common browse pattern is "filter by type, newest first". Neither had an
-- index, so on ~1.4M rows every default search sorted the whole matching set.
-- These indexes let Postgres read rows already in the right order and skip
-- full sorts/scans. Also covers the deadline and NUTS prefix filters.
--
-- Uses CREATE INDEX CONCURRENTLY so it does NOT lock the table during build —
-- important on the live DB with real data. NOTE: CONCURRENTLY cannot run inside
-- a transaction block; run this file with psql directly (not wrapped in BEGIN).
-- If a CONCURRENTLY build is interrupted it can leave an INVALID index; drop it
-- and re-run if so (the IF NOT EXISTS guards make re-running safe otherwise).
-- ============================================================================

-- 1. The big one: default sort is submission_date DESC. A descending index with
--    NULLS LAST matches the ORDER BY exactly so no sort step is needed.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_submission_date
    ON proc.procurement_act (submission_date DESC NULLS LAST);

-- 2. Composite for the dominant pattern "filter by type + sort by date".
--    Serves e.g. type=contract ORDER BY submission_date DESC in one index scan.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_type_submission
    ON proc.procurement_act (type, submission_date DESC NULLS LAST);

-- 3. Deadline filter (final_submission_date range).
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_final_submission
    ON proc.procurement_act (final_submission_date);

-- 4. NUTS prefix filter (nuts_code LIKE 'GR%'). text_pattern_ops makes LIKE
--    prefix matches index-usable.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_nuts_pattern
    ON proc.procurement_act (nuts_code text_pattern_ops);

-- After building, refresh planner stats so it starts using them immediately.
ANALYZE proc.procurement_act;
