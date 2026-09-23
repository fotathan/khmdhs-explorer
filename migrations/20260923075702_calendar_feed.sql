-- calendar_feed
--
-- The subscribed calendar feed: /calendar/<token>.ics puts a customer's
-- favourited acts, with their submission deadlines, into their own calendar
-- (Google, Outlook, Apple). Spec: docs/specs/calendar-feed.md, slice 4.
--
-- One row per user, holding the ONE live feed URL they have:
--
--   * token_hash — sha256 of the token in the URL. The raw token is shown to
--     the customer once and never stored, the same discipline as
--     proc.login_link. A calendar client sends no cookies, so the URL itself
--     is the credential; a leaked database must not leak working URLs.
--   * user_id is the PRIMARY KEY on purpose: "one live URL per user" is then
--     a fact the database enforces, and "make a new link" is a single
--     INSERT ... ON CONFLICT (user_id) DO UPDATE — atomic, so a double click
--     cannot leave two live URLs. Turning the feed off DELETES the row.
--   * lang — the feed is fetched by a server with no session and no language
--     cookie, so the language is fixed when the customer creates the link.
--   * last_fetch / fetch_count — so an admin can tell whether anyone actually
--     subscribed, which is the question that decides if this was worth it.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
-- Do NOT tick "Run and enable RLS" for this table in Supabase: with no
-- policies, every app INSERT is refused (proc.onboarding, 2026-09-22).

BEGIN;

CREATE TABLE IF NOT EXISTS proc.calendar_feed (
    user_id     bigint PRIMARY KEY
                REFERENCES proc.app_user(id) ON DELETE CASCADE,
    token_hash  text NOT NULL,
    lang        text NOT NULL DEFAULT 'el',
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_fetch  timestamptz,
    fetch_count bigint NOT NULL DEFAULT 0,
    CONSTRAINT calendar_feed_token_hash_uk UNIQUE (token_hash),
    CONSTRAINT calendar_feed_lang_ck CHECK (lang IN ('el', 'en'))
);

COMMENT ON TABLE proc.calendar_feed IS
    'One live .ics feed URL per user. token_hash = sha256 of the URL token; the raw token is shown once and never stored.';
COMMENT ON COLUMN proc.calendar_feed.lang IS
    'Language of the feed text, fixed at creation: the fetching calendar server has no session or language cookie.';

COMMIT;
