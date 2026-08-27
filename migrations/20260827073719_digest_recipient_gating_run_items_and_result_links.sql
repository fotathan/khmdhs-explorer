-- migrations/20260827073719_digest_recipient_gating_run_items_and_result_links.sql
-- digest recipient gating, per-run result items, and the "see all results" link
--
-- Three things the first digest cut left open, all of them visible only once a
-- real customer list existed:
--
-- 1. WHO MAY BE MAILED. A subscription used to need only an active account with
--    an address. That let an expired tester, a lapsed subscriber or a
--    prospective lead (a CRM record that is not a paying anything) keep
--    receiving results because someone had once scheduled them. Eligibility is
--    now the same expression the CRM segments by: a CURRENT, unexpired grant —
--    i.e. status 'tester' or 'subscriber'. Nothing is stored here; the gate is
--    in app/digests.active_subscriptions and run_subscription. What this
--    migration adds is the vocabulary to RECORD a refusal:
--    digest_run.status gains 'skipped'.
--
-- 2. WHAT WENT INTO ONE EMAIL. The run history recorded a count. A count cannot
--    answer "show me exactly what you mailed me" three days later, and it
--    cannot survive an act being re-ingested or a filter being edited. Each run
--    now writes its matched acts to digest_run_item — every act in the window,
--    not only the handful the email lists (in_email marks those), so nothing
--    that matched is lost when max_results truncates the message.
--
-- 3. THE LINK OUT OF THE EMAIL. "See all results" used to replay the profile's
--    filters as a live search, which drifts: by the time it is clicked the same
--    query returns a different set. digest_run.token is an unguessable handle
--    on the run, and /digests/<token> renders exactly the recorded items. The
--    route still demands a logged-in owner — the token identifies the run, it
--    does not authorise anyone.
--
-- Idempotent, wrapped so a failure leaves nothing half-applied.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. 'skipped' as a run outcome
-- ---------------------------------------------------------------------------
-- An admin pressing "send now" for a customer who is no longer entitled must
-- leave a trace: silence would read as a bug. Dropping and re-adding the CHECK
-- is the only way to widen it; the name is stable so re-running is a no-op.
ALTER TABLE proc.digest_run DROP CONSTRAINT IF EXISTS digest_run_status_ck;
ALTER TABLE proc.digest_run ADD CONSTRAINT digest_run_status_ck
    CHECK (status IN ('sent', 'empty', 'error', 'skipped'));

-- ---------------------------------------------------------------------------
-- 2. The link handle
-- ---------------------------------------------------------------------------
-- NULL for runs that mailed nothing (there is nothing to look at). Unique so a
-- token can never address two runs; the partial index keeps the NULLs out of it.
ALTER TABLE proc.digest_run ADD COLUMN IF NOT EXISTS token text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_digest_run_token
    ON proc.digest_run (token) WHERE token IS NOT NULL;

COMMENT ON COLUMN proc.digest_run.token IS
    'Unguessable handle for /digests/<token>, which renders this run''s recorded '
    'items. Identifies the run only — the route still requires the owner to be '
    'logged in.';

-- ---------------------------------------------------------------------------
-- 3. What one run actually matched
-- ---------------------------------------------------------------------------
-- One row per act. `ord` preserves the order the email listed them in (newest
-- ingest first), so the results page reads the same way the message did.
-- in_email separates the acts the message itself showed from the rest of the
-- window: with max_results = 25 and 80 matches, the email lists 25 and the
-- results page lists all 80 — which is what "see ALL results" has to mean.
CREATE TABLE IF NOT EXISTS proc.digest_run_item (
    id          bigserial   PRIMARY KEY,
    run_id      bigint      NOT NULL REFERENCES proc.digest_run(id) ON DELETE CASCADE,
    adam        text        NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    ord         integer     NOT NULL DEFAULT 0,
    in_email    boolean     NOT NULL DEFAULT false,
    ingested_at timestamptz
);

-- The same act cannot be listed twice in one email.
CREATE UNIQUE INDEX IF NOT EXISTS ux_digest_run_item_run_adam
    ON proc.digest_run_item (run_id, adam);
-- The results page's only query: this run's items, in order.
CREATE INDEX IF NOT EXISTS ix_digest_run_item_run_ord
    ON proc.digest_run_item (run_id, ord);

COMMENT ON TABLE proc.digest_run_item IS
    'The acts one digest run matched: every act in its ingest window, not only '
    'the ones the email listed (in_email marks those). Read back by '
    '/digests/<token>.';

COMMIT;
