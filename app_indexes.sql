-- ============================================================================
-- App support: indexes and extensions for the search UI.
-- Run once:  docker exec -i khmdhs-pg psql -U postgres -d procurement < app_indexes.sql
-- Re-running is safe (IF NOT EXISTS / CONCURRENTLY).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- A diacritic-insensitive immutable wrapper so we can index over it.
-- (Postgres unaccent() is STABLE by default; we wrap it in an IMMUTABLE
--  function so a trigram index over the expression is allowed.)
CREATE OR REPLACE FUNCTION proc.f_unaccent(text)
RETURNS text AS $$ SELECT public.unaccent('public.unaccent', $1) $$
LANGUAGE sql IMMUTABLE PARALLEL SAFE;

-- Trigram index on lower+unaccent(title) for fast Greek substring search.
CREATE INDEX IF NOT EXISTS ix_act_title_trgm
    ON proc.procurement_act
    USING gin (proc.f_unaccent(lower(title)) gin_trgm_ops);

-- Speed up the facet/filter queries that the UI runs constantly.
CREATE INDEX IF NOT EXISTS ix_act_type_signed
    ON proc.procurement_act (type, signed_date DESC);
CREATE INDEX IF NOT EXISTS ix_act_authority_signed
    ON proc.procurement_act (authority_id, signed_date DESC);
CREATE INDEX IF NOT EXISTS ix_act_value
    ON proc.procurement_act (total_cost_with_vat);

-- Helpful for the detail page (related-acts lookup) — already partially covered
-- but a target-side index makes "what links into this notice" instant too.
-- (act_link already has ix_link_source and ix_link_target from schema.sql.)
