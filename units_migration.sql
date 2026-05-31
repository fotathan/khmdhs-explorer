-- ============================================================================
-- Migration: UNECE Recommendation 20 unit-code catalog.
-- Run once:  docker exec -i khmdhs-pg psql -U postgres -d procurement < units_migration.sql
-- Safe to re-run.
--
-- Then load the catalog:
--   python3 load_units.py path/to/UNECE_Rec20_EL.csv
-- ============================================================================

CREATE TABLE IF NOT EXISTS proc.unit_code (
    code text PRIMARY KEY,
    name text
);
