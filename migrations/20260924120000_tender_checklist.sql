-- tender_checklist
--
-- Per-tender checklist (docs/specs/tender-checklist.md, slice 1).
-- The checklist itself is DERIVED — rebuilt on every request from the act's
-- current AI summary (proc.act_ai_summary) and its record. The only thing
-- stored is which items a customer has ticked off.
--
--   * One row per (user, act, item) that is DONE; unticking deletes it.
--   * item_key is app/tender_checklist.item_key(): 20 hex chars of
--     sha256(section | folded label | folded quote). The app only accepts a
--     key that is in the act's current checklist.
--   * Its own table, NOT a column on act_ai_summary: the summary is one row per
--     act served to everyone, and must never carry customer data (ai-summary
--     spec §3 — test-enforced in tests/test_tender_checklist.py).
--   * Not tied to user_favorite_act: a customer can work through a checklist
--     without starring the act, and un-starring must not throw the work away.
--   * Both foreign keys cascade. Acts are hidden, never deleted, when they turn
--     out to be duplicates, so the act cascade only fires on a real delete.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
-- Do NOT tick "Run and enable RLS" for this table in Supabase: with no
-- policies, every app INSERT is refused (proc.onboarding, 2026-09-22).

BEGIN;

CREATE TABLE IF NOT EXISTS proc.act_checklist_tick (
    user_id   bigint NOT NULL
              REFERENCES proc.app_user(id) ON DELETE CASCADE,
    adam      text   NOT NULL
              REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    item_key  text   NOT NULL
              CONSTRAINT act_checklist_tick_key_ck CHECK (item_key ~ '^[0-9a-f]{20}$'),
    done_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, adam, item_key)
);

-- The cascade from procurement_act looks rows up by act.
CREATE INDEX IF NOT EXISTS ix_act_checklist_tick_adam
    ON proc.act_checklist_tick (adam);

COMMENT ON TABLE proc.act_checklist_tick IS
    'Checklist items a customer has ticked off on a tender. Row present = done. The checklist itself is derived from act_ai_summary at request time.';

COMMIT;
