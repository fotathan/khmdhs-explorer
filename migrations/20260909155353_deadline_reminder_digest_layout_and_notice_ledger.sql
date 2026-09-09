-- migrations/20260909155353_deadline_reminder_digest_layout_and_notice_ledger.sql
-- deadline reminder digest layout and notice ledger
--
-- A THIRD shape of scheduled email, and the one thing the existing two cannot
-- express.
--
-- The list and summary digests both window on procurement_act.ingested_at and
-- walk a monotone cursor: "everything that arrived since your last message".
-- That answers "what is new". It cannot answer "what am I about to miss",
-- because a submission deadline approaches on the wall clock, not on our
-- ingest. An act that arrived three weeks ago and closes on Friday was mailed
-- once, three weeks ago, and is never mentioned again.
--
-- The deadline layout windows FORWARD on final_submission_date instead: the
-- acts matching the saved search that close within the next N days. Since the
-- window slides with time rather than with data, a cursor cannot bound it —
-- the same act would be listed every single morning until it closed. What
-- bounds it is a ledger of what has already been said.
--
-- 1. digest_subscription.lead_days — the reminder marks, in days before the
--    deadline. Default '{7,1}': one warning a week out, one the day before.
--    Each mark fires at most once per act, so an act reaches the reader twice
--    over its life, not fourteen times.
--
-- 2. digest_deadline_notice — one row per (subscription, act, mark) that has
--    actually been mailed. It carries the DEADLINE it was sent for: when an
--    authority extends or brings forward a closing date, the stored deadline no
--    longer matches the act's, every mark re-arms and the customer is told
--    again. A moved deadline is news, and the whole promise of this layout is
--    that nothing closes unannounced.
--
-- Nothing here changes an existing subscription: layout still defaults to
-- 'list', and lead_days is simply ignored by the two ingest-window bodies.
--
-- Idempotent, wrapped so a failure leaves nothing half-applied.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. The reminder marks, and the third layout
-- ---------------------------------------------------------------------------
ALTER TABLE proc.digest_subscription
  ADD COLUMN IF NOT EXISTS lead_days smallint[] NOT NULL DEFAULT '{7,1}';

COMMENT ON COLUMN proc.digest_subscription.lead_days IS
    'Deadline layout only: how many days before final_submission_date to remind, '
    'largest first. Each mark fires at most once per act (proc.digest_deadline_notice), '
    'so ''{7,1}'' means one warning a week out and one the day before.';

-- Dropped first so re-running cannot trip over its own constraint; the name is
-- stable, so the second run is a no-op.
ALTER TABLE proc.digest_subscription DROP CONSTRAINT IF EXISTS digest_subscription_layout_ck;
ALTER TABLE proc.digest_subscription ADD CONSTRAINT digest_subscription_layout_ck
    CHECK (layout IN ('list', 'summary', 'deadline'));

-- At least one mark, none of them absurd. A 0 is legitimate and means "on the
-- closing day itself"; the upper bound keeps a mistyped 3650 from turning a
-- reminder into a dump of the entire forward corpus.
ALTER TABLE proc.digest_subscription DROP CONSTRAINT IF EXISTS digest_subscription_lead_days_ck;
ALTER TABLE proc.digest_subscription ADD CONSTRAINT digest_subscription_lead_days_ck
    CHECK (array_length(lead_days, 1) BETWEEN 1 AND 6
           AND 0 <= ALL (lead_days) AND 90 >= ALL (lead_days));

COMMENT ON COLUMN proc.digest_subscription.layout IS
    'Which email body this subscription sends: ''list'' prints the acts ingested '
    'since the last message, ''summary'' prints statistics over that same window, '
    '''deadline'' looks forward instead and prints the acts whose submission '
    'deadline is approaching. Wording per layout comes from proc.email_template '
    'slug ''digest'' / ''digest_summary'' / ''digest_deadline''.';

-- ---------------------------------------------------------------------------
-- 2. What has already been said, to whom, about which closing date
-- ---------------------------------------------------------------------------
-- This is the deadline layout's equivalent of digest_subscription.last_cursor,
-- and it has to be per-act because the window is not a time range that can be
-- consumed: "closes within 7 days" contains the same act on seven consecutive
-- mornings. A row here is written ONLY when a message actually left, exactly as
-- the cursor only moves on a real send — a failed or refused run leaves every
-- mark armed for the next one.
CREATE TABLE IF NOT EXISTS proc.digest_deadline_notice (
    subscription_id bigint      NOT NULL REFERENCES proc.digest_subscription(id) ON DELETE CASCADE,
    adam            text        NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    lead_days       smallint    NOT NULL,
    -- The closing date this reminder was about. A later run compares it with
    -- the act's current final_submission_date: if the authority moved it, the
    -- mark is treated as unspent and the reader is told about the new date.
    deadline        timestamptz,
    run_id          bigint      REFERENCES proc.digest_run(id) ON DELETE SET NULL,
    sent_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subscription_id, adam, lead_days)
);

-- The lookup the send query makes per candidate act, and the one an admin makes
-- when asking "what have we already told this customer about".
CREATE INDEX IF NOT EXISTS ix_digest_deadline_notice_sub_sent
    ON proc.digest_deadline_notice (subscription_id, sent_at DESC);

COMMENT ON TABLE proc.digest_deadline_notice IS
    'One row per (digest subscription, act, reminder mark) already mailed. What '
    'stops a deadline digest repeating the same act every morning; the stored '
    'deadline re-arms every mark if the authority moves the closing date.';

-- ---------------------------------------------------------------------------
-- 3. Wording for the deadline body
-- ---------------------------------------------------------------------------
-- Same [[field]] vocabulary as the other two, resolved per recipient. Its own
-- slug so an admin can reword the reminder without touching the daily digest —
-- the two say very different things and are read in very different moods.
INSERT INTO proc.email_template (slug, lang, name, subject, body_html) VALUES
  ('digest_deadline', 'el', 'Ειδοποίηση προθεσμιών',
   'Προθεσμίες που πλησιάζουν: [[profile_name]]',
   '<p>Καλημέρα [[full_name]],</p>'
   '<p>Οι παρακάτω διαγωνισμοί από το προφίλ αναζήτησης '
   '<strong>[[profile_name]]</strong> κλείνουν σύντομα.</p>'),
  ('digest_deadline', 'en', 'Deadline reminder',
   'Deadlines approaching: [[profile_name]]',
   '<p>Hello [[full_name]],</p>'
   '<p>The following tenders from your saved search '
   '<strong>[[profile_name]]</strong> are closing soon.</p>')
ON CONFLICT (slug, lang) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 4. Grants (belt-and-suspenders; default privileges already cover the owner)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON proc.digest_deadline_notice TO app_runtime';
  END IF;
END $$;

COMMIT;
