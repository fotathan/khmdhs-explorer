-- ============================================================================
-- Migration: annotation / curation overlay for procurement acts.
-- Run once:  docker exec -i khmdhs-pg psql -U postgres -d procurement < annotations_migration.sql
-- Safe to re-run.
--
-- DESIGN: This table NEVER modifies proc.procurement_act. It is a pure overlay
-- of team notes, tags and review flags keyed by ADAM. Backfills cannot clobber
-- it (the ingester only ever writes procurement_act and its child tables).
-- Each edit is a NEW row, so the table doubles as an append-only audit trail:
-- the "current" annotation for an act is simply its most recent row.
-- ============================================================================

CREATE TABLE IF NOT EXISTS proc.act_annotation (
    id          bigserial PRIMARY KEY,
    adam        text NOT NULL,            -- references procurement_act.adam (no FK:
                                          -- annotations may be made on acts that
                                          -- are referenced but not yet ingested)
    note        text,                     -- free-text note
    tags        text[] NOT NULL DEFAULT '{}',  -- arbitrary team tags
    flag        text,                     -- review status: verified|suspicious|review|null
    author      text NOT NULL DEFAULT '(anonymous)',  -- attribution (not auth)
    created_at  timestamptz NOT NULL DEFAULT now(),
    superseded  boolean NOT NULL DEFAULT false  -- set true when a newer row replaces it
);

CREATE INDEX IF NOT EXISTS ix_annotation_adam
    ON proc.act_annotation (adam, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_annotation_flag
    ON proc.act_annotation (flag) WHERE flag IS NOT NULL AND NOT superseded;
CREATE INDEX IF NOT EXISTS ix_annotation_tags
    ON proc.act_annotation USING gin (tags);

-- A convenience view exposing only the CURRENT (latest, non-superseded)
-- annotation per act — what the UI shows by default.
CREATE OR REPLACE VIEW proc.v_act_annotation_current AS
SELECT DISTINCT ON (adam)
       adam, id, note, tags, flag, author, created_at
FROM proc.act_annotation
WHERE NOT superseded
ORDER BY adam, created_at DESC;
