-- cpv_label_index_migration.sql
-- The contractor/authority detail pages look up a CPV *division* label
-- (e.g. division "45" -> "Construction work") once per division shown. The old
-- subquery did `cpv_code LIKE 'XX000000-_'`, which can't use an index, so it
-- seq-scanned all cpv_code rows per division, several times per page load
-- (visible as SubPlan Seq Scan on cpv_code in EXPLAIN).
--
-- Fix: a functional index on the 2-char division prefix, restricted to the
-- division ROOT codes (XX000000-N). The rewritten subquery in main.py matches
-- substr(cpv_code,1,2) = division AND substr(cpv_code,3,6) = '000000', which
-- this partial index serves directly. Returns the same label as before.
--
-- Run on BOTH local and Supabase:
--   psql "$DATABASE_URL"         -f cpv_label_index_migration.sql
--   psql "<supabase-direct-url>"  -f cpv_label_index_migration.sql

CREATE INDEX IF NOT EXISTS ix_cpv_division_root
    ON proc.cpv_code (substr(cpv_code, 1, 2))
    WHERE substr(cpv_code, 3, 6) = '000000';

ANALYZE proc.cpv_code;
