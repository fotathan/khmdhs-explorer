-- favorite_bid_stage
--
-- Bid pipeline on favourites (docs/specs/bid-pipeline.md, slice 1).
-- A favourite can now carry where the customer stands on that tender:
-- bidding → submitted → won / lost, or no_bid. NULL = just a bookmark.
--
--   * Columns on user_favorite_act, not a table of their own: the pipeline IS
--     the favourites list. Removing the star (web or mobile) forgets the stage
--     with it — the same row, the same owner, the same lifetime.
--   * The mobile API inserts (user_id, adam) only, so nullable columns change
--     nothing for it.
--   * bid_stage_source says who decided: 'user' (picked it) or 'ledger'
--     (confirmed an award we detected). Only a ledger confirmation sets
--     bid_outcome_adam — the award act the confirmation came from. Win/loss
--     data for fit.py is only as good as this distinction.
--   * The FK to procurement_act is added on an all-NULL column: nothing to
--     validate, no table rewrite.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them. app_runtime gets
-- UPDATE through the default privileges of …app_runtime_least_privilege_role_grants.
-- Do NOT tick "Run and enable RLS" for this table in Supabase.

BEGIN;

ALTER TABLE proc.user_favorite_act
    ADD COLUMN IF NOT EXISTS bid_stage         text,
    ADD COLUMN IF NOT EXISTS bid_note          text,
    ADD COLUMN IF NOT EXISTS bid_stage_at      timestamptz,
    ADD COLUMN IF NOT EXISTS bid_stage_source  text,
    ADD COLUMN IF NOT EXISTS bid_outcome_adam  text
        REFERENCES proc.procurement_act(adam) ON DELETE SET NULL;

ALTER TABLE proc.user_favorite_act
    DROP CONSTRAINT IF EXISTS user_favorite_act_bid_stage_ck,
    ADD CONSTRAINT user_favorite_act_bid_stage_ck CHECK (
        bid_stage IS NULL
        OR bid_stage IN ('bidding', 'submitted', 'won', 'lost', 'no_bid')),
    DROP CONSTRAINT IF EXISTS user_favorite_act_bid_source_ck,
    ADD CONSTRAINT user_favorite_act_bid_source_ck CHECK (
        bid_stage_source IS NULL OR bid_stage_source IN ('user', 'ledger')),
    DROP CONSTRAINT IF EXISTS user_favorite_act_bid_note_ck,
    ADD CONSTRAINT user_favorite_act_bid_note_ck CHECK (
        bid_note IS NULL OR length(bid_note) <= 300);

-- The ON DELETE SET NULL above looks favourites up by the award act.
CREATE INDEX IF NOT EXISTS ix_user_favorite_act_outcome
    ON proc.user_favorite_act (bid_outcome_adam)
    WHERE bid_outcome_adam IS NOT NULL;

COMMENT ON COLUMN proc.user_favorite_act.bid_stage IS
    'Customer''s bid pipeline stage for this act; NULL = bookmark only.';
COMMENT ON COLUMN proc.user_favorite_act.bid_stage_source IS
    '''user'' = picked by hand; ''ledger'' = confirmed from a detected award (bid_outcome_adam).';

COMMIT;
