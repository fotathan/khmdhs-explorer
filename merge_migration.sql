-- ============================================================================
-- Migration: entity identity / merge layer for contractors and authorities.
-- Run once:  docker exec -i khmdhs-pg psql -U postgres -d procurement < merge_migration.sql
-- Safe to re-run.
--
-- PURPOSE: the official source contains duplicate entities — the same real
-- company under transposed VATs (0940012336 vs 094012236) or spelling variants
-- (Ι vs Ϊ). This layer groups raw keys into a single canonical entity WITHOUT
-- touching the harvested economic_operator / authority rows. Re-imports keep
-- landing on the raw rows; the app resolves them to the canonical entity at
-- display time, so merges survive every future backfill.
--
-- DESIGN
--   entity_group  — one row per consolidated real-world entity.
--   entity_member — maps each raw key (VAT for 'contractor', org_id for
--                   'authority') to its group. A key absent from this table is
--                   simply its own standalone entity.
-- Both are pure overlay: deleting a group/its members fully un-merges, with no
-- harm to harvested data.
-- ============================================================================

CREATE TABLE IF NOT EXISTS proc.entity_group (
    id              bigserial PRIMARY KEY,
    kind            text NOT NULL CHECK (kind IN ('contractor','authority')),
    canonical_key   text NOT NULL,          -- the chosen VAT / org_id to represent the group
    display_name    text,                   -- optional override; NULL => use canonical row's name
    created_by      text NOT NULL DEFAULT '(anonymous)',
    created_at      timestamptz NOT NULL DEFAULT now(),
    note            text                    -- optional reason / audit note
);

CREATE TABLE IF NOT EXISTS proc.entity_member (
    group_id    bigint NOT NULL REFERENCES proc.entity_group(id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('contractor','authority')),
    member_key  text NOT NULL,              -- raw VAT (contractor) or org_id (authority)
    PRIMARY KEY (kind, member_key)           -- a raw key can belong to only one group
);

CREATE INDEX IF NOT EXISTS ix_entity_member_group ON proc.entity_member (group_id);

-- Convenience view: for any member key, the canonical key of its group.
-- Keys with no membership resolve to themselves (handled in app code, not here).
CREATE OR REPLACE VIEW proc.v_entity_canonical AS
SELECT m.kind, m.member_key, g.canonical_key, g.display_name, g.id AS group_id
FROM proc.entity_member m
JOIN proc.entity_group g ON g.id = m.group_id;
