-- calendar_search
--
-- Saved searches in the calendar feed (docs/specs/calendar-feed.md, slice 5).
-- A customer ticks "show in my calendar" on /account/searches and the
-- upcoming deadlines that search matches join their favourites in
-- /calendar/<token>.ics.
--
-- One row per (user, saved search) that is opted in; unticking DELETES it.
--
--   * Its own table, not a column on search_profile: a portal profile is
--     shared, and one customer's opt-in must not put it in everybody's
--     calendar. Not a column on digest_subscription either: a customer may
--     want the calendar without the email.
--   * Both foreign keys cascade: deleting the saved search, or the account,
--     removes the opt-in with it.
--   * created_at feeds the feed's DTSTAMP, which is derived from the data so
--     an unchanged feed renders to the same bytes (ETag / 304).
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
-- Do NOT tick "Run and enable RLS" for this table in Supabase: with no
-- policies, every app INSERT is refused (proc.onboarding, 2026-09-22).

BEGIN;

CREATE TABLE IF NOT EXISTS proc.calendar_search (
    user_id           bigint NOT NULL
                      REFERENCES proc.app_user(id) ON DELETE CASCADE,
    search_profile_id bigint NOT NULL
                      REFERENCES proc.search_profile(id) ON DELETE CASCADE,
    created_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, search_profile_id)
);

-- The cascade from search_profile looks rows up by profile id.
CREATE INDEX IF NOT EXISTS ix_calendar_search_profile
    ON proc.calendar_search (search_profile_id);

COMMENT ON TABLE proc.calendar_search IS
    'Saved searches a customer has put in their .ics calendar feed. Row present = opted in.';

COMMIT;
