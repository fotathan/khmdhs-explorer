-- checklist_own_items
--
-- The customer's own lines on a tender's checklist (docs/specs/tender-
-- checklist.md, slice 4): "ask the bank for the guarantee", "CVs from the
-- engineers". Private to the customer, next to the items derived from the
-- AI summary.
--
--   * Its own table, not rows in act_checklist_tick: a tick points at an item
--     the SUMMARY owns (a key into a derived list); an own item is content the
--     customer wrote. Different lifetime, different validation.
--   * Never on act_ai_summary: that row is served to everyone and must carry
--     no customer data (ai-summary spec §3).
--   * done_at NULL = open; a timestamp = done. text is bounded here AND in the
--     app (tender_checklist.OWN_TEXT_MAX); the app also caps rows per act.
--   * Both foreign keys cascade.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
-- Do NOT tick "Run and enable RLS" for this table in Supabase: with no
-- policies, every app INSERT is refused (proc.onboarding, 2026-09-22).

BEGIN;

CREATE TABLE IF NOT EXISTS proc.act_checklist_own_item (
    id          bigserial PRIMARY KEY,
    user_id     bigint NOT NULL
                REFERENCES proc.app_user(id) ON DELETE CASCADE,
    adam        text   NOT NULL
                REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    text        text   NOT NULL
                CONSTRAINT act_checklist_own_item_text_ck
                CHECK (length(btrim(text)) BETWEEN 1 AND 200),
    done_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Every read is "this user's items on this act".
CREATE INDEX IF NOT EXISTS ix_act_checklist_own_item_user_adam
    ON proc.act_checklist_own_item (user_id, adam);
-- The cascade from procurement_act looks rows up by act.
CREATE INDEX IF NOT EXISTS ix_act_checklist_own_item_adam
    ON proc.act_checklist_own_item (adam);

COMMENT ON TABLE proc.act_checklist_own_item IS
    'A customer''s own checklist lines on a tender. Private to user_id; done_at NULL = open.';

COMMIT;
