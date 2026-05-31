-- ============================================================================
-- Migration: performance indexes for entity directories and search.
-- Run once:  docker exec -i khmdhs-pg psql -U postgres -d procurement < perf_indexes_migration.sql
-- Safe to re-run. Run ANALYZE afterwards (included at the end).
--
-- WHY: the /contractors and /authorities directory pages aggregate act counts
-- per entity and search across names. Without these indexes those queries do
-- sequential scans + nested loops that become minutes-slow on the full dataset.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Aggregation joins: act_operator by operator and by adam; acts by authority.
CREATE INDEX IF NOT EXISTS ix_act_operator_operator
    ON proc.act_operator(operator_id);
CREATE INDEX IF NOT EXISTS ix_act_operator_adam
    ON proc.act_operator(adam);
CREATE INDEX IF NOT EXISTS ix_act_authority
    ON proc.procurement_act(authority_id);

-- Merge-layer lookups (resolve a key -> its group, and back).
CREATE INDEX IF NOT EXISTS ix_entity_member_key
    ON proc.entity_member(kind, member_key);

-- Trigram indexes on the NORMALISED name expression so the Greek-aware
-- "LIKE '%term%'" search can use an index instead of scanning every row.
-- The expression must match EXACTLY what the app queries:
--   translate(proc.f_unaccent(lower(name)),'ς','σ')
CREATE INDEX IF NOT EXISTS ix_eo_name_trgm
    ON proc.economic_operator
    USING gin (translate(proc.f_unaccent(lower(name)),'ς','σ') gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_auth_name_trgm
    ON proc.authority
    USING gin (translate(proc.f_unaccent(lower(name)),'ς','σ') gin_trgm_ops);

ANALYZE proc.act_operator;
ANALYZE proc.procurement_act;
ANALYZE proc.economic_operator;
ANALYZE proc.authority;
ANALYZE proc.entity_member;
