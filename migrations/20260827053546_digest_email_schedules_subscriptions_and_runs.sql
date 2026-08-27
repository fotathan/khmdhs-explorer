-- migrations/20260827053546_digest_email_schedules_subscriptions_and_runs.sql
-- digest email schedules subscriptions and runs
--
-- Scheduled "new results" emails: a customer is subscribed to one of their
-- search profiles, and on a schedule the app runs that profile's filters over
-- everything ingested since the last send and emails what is new.
--
-- Three tables:
--   digest_schedule     — named cadences an admin defines once (daily 08:00
--                         Europe/Athens, weekly Monday, ...). Exactly one row
--                         may be the PORTAL DEFAULT.
--   digest_subscription — (customer × search profile). schedule_id NULL means
--                         "inherit the portal default"; setting it is the
--                         per-customer, per-profile override. This mirrors the
--                         search_profile.based_on_id idiom: a nullable pointer
--                         that means "fall back to the shared one".
--   digest_run          — one row per attempt (scheduled, manual or test),
--                         the send history and the idempotency record.
--
-- The window is on procurement_act.ingested_at, NOT on the act's own dates: a
-- backdated act published today must still reach the customer today, and
-- ingested_at is the only column that says "this became visible to us when".
-- last_cursor is that high-water mark per subscription, so two runs can never
-- send the same act twice and a missed run is picked up by the next one.
--
-- Wrap the body so a failure leaves nothing half-applied. Idempotent throughout.

BEGIN;

-- ---------------------------------------------------------------------------
-- Schedules
-- ---------------------------------------------------------------------------
-- Cadence is a small enum plus hour/minute rather than a cron expression: it
-- keeps the admin form a set of selects, and "when did this last fire" is
-- computable in app/digests.py with no cron parser dependency.
CREATE TABLE IF NOT EXISTS proc.digest_schedule (
    id           bigserial   PRIMARY KEY,
    name         text        NOT NULL,
    cadence      text        NOT NULL,           -- daily | weekdays | weekly | monthly
    hour         smallint    NOT NULL DEFAULT 8, -- local to `tz`
    minute       smallint    NOT NULL DEFAULT 0,
    weekday      smallint,                       -- 0=Mon .. 6=Sun; weekly only
    day_of_month smallint,                       -- 1..28; monthly only
    tz           text        NOT NULL DEFAULT 'Europe/Athens',
    is_default   boolean     NOT NULL DEFAULT false,
    is_active    boolean     NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    created_by   bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    CONSTRAINT digest_schedule_cadence_ck
        CHECK (cadence IN ('daily', 'weekdays', 'weekly', 'monthly')),
    CONSTRAINT digest_schedule_hour_ck   CHECK (hour   BETWEEN 0 AND 23),
    CONSTRAINT digest_schedule_minute_ck CHECK (minute BETWEEN 0 AND 59),
    -- weekly needs a weekday, monthly a day (capped at 28 so every month has it)
    CONSTRAINT digest_schedule_weekday_ck CHECK (
        (cadence = 'weekly'  AND weekday BETWEEN 0 AND 6) OR
        (cadence <> 'weekly' AND weekday IS NULL)),
    CONSTRAINT digest_schedule_dom_ck CHECK (
        (cadence = 'monthly'  AND day_of_month BETWEEN 1 AND 28) OR
        (cadence <> 'monthly' AND day_of_month IS NULL))
);

-- At most one portal default. Partial unique index = the constraint the
-- resolution chain relies on (subscription → its schedule, else THE default).
CREATE UNIQUE INDEX IF NOT EXISTS ux_digest_schedule_default
    ON proc.digest_schedule ((true)) WHERE is_default;

-- ---------------------------------------------------------------------------
-- Subscriptions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.digest_subscription (
    id                bigserial   PRIMARY KEY,
    user_id           bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    search_profile_id bigint      NOT NULL REFERENCES proc.search_profile(id) ON DELETE CASCADE,
    -- NULL = use the portal default schedule; set = per-customer, per-profile
    -- override. ON DELETE SET NULL so deleting a schedule degrades to the
    -- default instead of silently dropping the subscription.
    schedule_id       bigint      REFERENCES proc.digest_schedule(id) ON DELETE SET NULL,
    is_active         boolean     NOT NULL DEFAULT true,
    send_empty        boolean     NOT NULL DEFAULT false,  -- mail even with 0 new results
    max_results       integer     NOT NULL DEFAULT 25,
    lang              text        NOT NULL DEFAULT 'el',
    -- High-water mark of procurement_act.ingested_at already covered. NULL until
    -- the first run, which then starts from created_at (never from the epoch —
    -- a new subscription must not mail out the whole archive).
    last_cursor       timestamptz,
    last_run_at       timestamptz,              -- last SCHEDULED evaluation
    last_sent_at      timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    created_by        bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    CONSTRAINT digest_subscription_lang_ck CHECK (lang IN ('el', 'en')),
    CONSTRAINT digest_subscription_max_ck  CHECK (max_results BETWEEN 1 AND 200)
);

-- One subscription per (customer, profile) — the admin edits it, not duplicates it.
CREATE UNIQUE INDEX IF NOT EXISTS ux_digest_subscription_user_profile
    ON proc.digest_subscription (user_id, search_profile_id);
CREATE INDEX IF NOT EXISTS ix_digest_subscription_due
    ON proc.digest_subscription (is_active, last_run_at);
CREATE INDEX IF NOT EXISTS ix_digest_subscription_schedule
    ON proc.digest_subscription (schedule_id) WHERE schedule_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Run history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proc.digest_run (
    id              bigserial   PRIMARY KEY,
    subscription_id bigint      REFERENCES proc.digest_subscription(id) ON DELETE CASCADE,
    trigger         text        NOT NULL DEFAULT 'schedule',  -- schedule | manual | test
    status          text        NOT NULL,                     -- sent | empty | error
    n_results       integer     NOT NULL DEFAULT 0,
    cursor_from     timestamptz,
    cursor_to       timestamptz,
    recipient       text,
    subject         text,
    error           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    CONSTRAINT digest_run_trigger_ck CHECK (trigger IN ('schedule', 'manual', 'test')),
    CONSTRAINT digest_run_status_ck  CHECK (status IN ('sent', 'empty', 'error'))
);

CREATE INDEX IF NOT EXISTS ix_digest_run_subscription
    ON proc.digest_run (subscription_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_digest_run_started
    ON proc.digest_run (started_at DESC);

-- ---------------------------------------------------------------------------
-- The window index
-- ---------------------------------------------------------------------------
-- Every digest query is "matching acts WHERE ingested_at > cursor". Without
-- this the planner scans procurement_act (millions of rows) per subscription.
CREATE INDEX IF NOT EXISTS ix_procurement_act_ingested_at
    ON proc.procurement_act (ingested_at);

-- ---------------------------------------------------------------------------
-- Seeds
-- ---------------------------------------------------------------------------
-- The portal default schedule. Without one, a subscription with no explicit
-- schedule has nothing to inherit and is simply never due.
INSERT INTO proc.digest_schedule (name, cadence, hour, minute, tz, is_default)
SELECT 'Καθημερινά 08:00', 'daily', 8, 0, 'Europe/Athens', true
WHERE NOT EXISTS (SELECT 1 FROM proc.digest_schedule WHERE is_default);

-- Wording for the digest email, editable at /admin/email-templates like every
-- other template (so changing a sentence needs no deploy). The digest builder
-- uses `subject` and treats body_html as the INTRO fragment shown above the
-- results table; [[field]] tokens resolve from the customer's profile exactly
-- as they do in the CRM builder.
-- No @@token here, unlike the CRM bodies: that marker survives the merge on
-- purpose (a human replaces it before sending), and a digest has no human in
-- the loop. app/digests.intro_html strips any that an admin pastes in anyway.
INSERT INTO proc.email_template (slug, lang, name, subject, body_html) VALUES
  ('digest', 'el', 'Ειδοποίηση αποτελεσμάτων',
   'Νέα αποτελέσματα: [[profile_name]]',
   '<p>Καλημέρα [[full_name]],</p>'
   '<p>Βρέθηκαν νέες πράξεις που ταιριάζουν με το προφίλ αναζήτησης '
   '<strong>[[profile_name]]</strong>.</p>'),
  ('digest', 'en', 'Results digest',
   'New results: [[profile_name]]',
   '<p>Hello [[full_name]],</p>'
   '<p>New acts matching your saved search <strong>[[profile_name]]</strong> '
   'have been published.</p>')
ON CONFLICT (slug, lang) DO NOTHING;

COMMIT;
