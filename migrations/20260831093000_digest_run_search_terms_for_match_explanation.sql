-- migrations/20260831093000_digest_run_search_terms_for_match_explanation.sql
-- digest run search terms, so a result mail's list can explain itself
--
-- The search page tells a reader WHY a row is there: "Ταιριάζει: καθαριότητα 7"
-- chips on every card, and the same terms carried onto the act link so the
-- detail page can highlight them. /digests/<token> — the list one result email
-- contained — could not do either, because it has no query string: its rows come
-- from proc.digest_run_item, recorded at send time, not from a live search.
--
-- The terms are recoverable from the subscription's search profile, but that
-- profile is LIVE and the run is HISTORY. Edit the saved search a week after the
-- send and the page would explain last week's acts against this week's words —
-- confidently, and wrongly. So the run records the filter set it was actually
-- built with, exactly as digest_run_item records the acts it actually matched.
--
-- params_qs is the same querystring shape app/search_profiles.params_to_qs
-- produces and params_from_qs parses (q / fulltext / cpv / every other filter),
-- so nothing new has to know how a saved search is spelled.
--
-- Runs sent BEFORE this column existed keep NULL. The results page falls back to
-- the live profile for those — the best answer still available for them — and
-- every run from here on carries its own.
--
-- Additive and idempotent: an existing run is untouched, and no code path reads
-- this column without a fallback.

BEGIN;

ALTER TABLE proc.digest_run
  ADD COLUMN IF NOT EXISTS params_qs text;

COMMENT ON COLUMN proc.digest_run.params_qs IS
    'The saved search''s filters as a querystring, frozen at send time '
    '(app/search_profiles.params_to_qs). Read by /digests/<token> to explain why '
    'each act matched and to carry the terms onto the act links. NULL on runs '
    'sent before the column existed — those fall back to the live profile.';

COMMIT;
