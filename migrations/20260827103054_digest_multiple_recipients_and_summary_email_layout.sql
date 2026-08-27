-- migrations/20260827103054_digest_multiple_recipients_and_summary_email_layout.sql
-- digest multiple recipients and summary email layout
--
-- Two things a real customer needs that one address and one body cannot cover:
--
-- 1. MORE THAN ONE READER. A digest went to exactly one address — the account's
--    own — because a subscription is (customer × search profile) and a customer
--    row has one email. In practice the person who signed up is rarely the only
--    one who wants the results: a procurement manager, an assistant and the
--    owner all want the same list. digest_recipient is that list. The account
--    address stays the default (digest_subscription.include_primary), so an
--    existing subscription keeps behaving exactly as it did; the extra rows are
--    additions, and each carries its OWN name so the message can greet the
--    person reading it rather than the account holder.
--
-- 2. MORE THAN ONE SHAPE OF EMAIL. The existing body lists the acts. For a
--    customer whose profile matches a hundred acts a day that is the wrong
--    message: they want to know HOW MANY and OF WHAT, and to open the full list
--    in the app when something looks interesting. digest_subscription.layout
--    picks between the two, and the wording for each lives in proc.email_template
--    under its own slug ('digest' and 'digest_summary'), so both stay editable
--    at /admin/email-templates without a deploy.
--
-- Nothing here changes what an existing subscription sends: layout defaults to
-- 'list', include_primary to true, and a subscription with no digest_recipient
-- rows resolves to exactly the one address it used before.
--
-- Idempotent, wrapped so a failure leaves nothing half-applied.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Which body to send, and whether the account address is one of the readers
-- ---------------------------------------------------------------------------
ALTER TABLE proc.digest_subscription
  ADD COLUMN IF NOT EXISTS layout          text    NOT NULL DEFAULT 'list',
  -- false = mail ONLY the named recipients below. The account holder is then a
  -- CRM record who happens not to read the alert — which is a real arrangement
  -- (an agency address signed up, the client's staff read the results).
  ADD COLUMN IF NOT EXISTS include_primary boolean NOT NULL DEFAULT true;

-- Added separately (and dropped first) so re-running cannot trip over its own
-- constraint; the name is stable, so this is a no-op the second time.
ALTER TABLE proc.digest_subscription DROP CONSTRAINT IF EXISTS digest_subscription_layout_ck;
ALTER TABLE proc.digest_subscription ADD CONSTRAINT digest_subscription_layout_ck
    CHECK (layout IN ('list', 'summary'));

COMMENT ON COLUMN proc.digest_subscription.layout IS
    'Which email body this subscription sends: ''list'' prints the new acts, '
    '''summary'' prints the statistics and links to the full set. The wording of '
    'each comes from proc.email_template slug ''digest'' / ''digest_summary''.';

-- ---------------------------------------------------------------------------
-- 2. The extra readers
-- ---------------------------------------------------------------------------
-- One row per additional person. The name fields are NOT decoration: the intro
-- is re-resolved per recipient, so [[salutation]] / [[first_name]] /
-- [[full_name]] address whoever is reading. All optional — an address alone is
-- a valid recipient and simply falls back to the customer's own greeting.
CREATE TABLE IF NOT EXISTS proc.digest_recipient (
    id              bigserial   PRIMARY KEY,
    subscription_id bigint      NOT NULL REFERENCES proc.digest_subscription(id) ON DELETE CASCADE,
    email           text        NOT NULL,
    salutation      text,                    -- 'Αξιότιμε κύριε', 'Dear', ...
    first_name      text,
    last_name       text,
    ord             smallint    NOT NULL DEFAULT 0,
    is_active       boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    CONSTRAINT digest_recipient_email_ck CHECK (btrim(email) <> '')
);

-- The same person cannot be listed twice on one subscription — case-folded,
-- because 'Maria@x.gr' and 'maria@x.gr' are one mailbox and would otherwise
-- produce two copies of every digest.
CREATE UNIQUE INDEX IF NOT EXISTS ux_digest_recipient_sub_email
    ON proc.digest_recipient (subscription_id, lower(btrim(email)));
CREATE INDEX IF NOT EXISTS ix_digest_recipient_sub
    ON proc.digest_recipient (subscription_id, ord, id);

COMMENT ON TABLE proc.digest_recipient IS
    'Additional addresses one digest subscription is mailed to, beyond the '
    'account''s own (which is included unless digest_subscription.include_primary '
    'is false). Each row carries its own salutation/name so the intro greets the '
    'reader, not the account holder.';

-- ---------------------------------------------------------------------------
-- 3. How many people one run actually reached
-- ---------------------------------------------------------------------------
-- digest_run.recipient already holds the addresses as text; the count is what
-- the history lists, and deriving it by splitting that string would break on
-- the first address containing a comma inside a display name.
ALTER TABLE proc.digest_run
  ADD COLUMN IF NOT EXISTS n_recipients integer NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------
-- 4. Wording for the summary body
-- ---------------------------------------------------------------------------
-- Same [[field]] vocabulary as the list digest, plus the per-recipient tokens
-- the multi-recipient send resolves ([[salutation]], [[first_name]]). Kept as a
-- separate slug rather than a second body on 'digest' so an admin can change
-- one without touching the other.
INSERT INTO proc.email_template (slug, lang, name, subject, body_html) VALUES
  ('digest_summary', 'el', 'Ειδοποίηση αποτελεσμάτων — σύνοψη',
   'Σύνοψη νέων αποτελεσμάτων: [[profile_name]]',
   '<p>Καλημέρα [[full_name]],</p>'
   '<p>Ακολουθεί η σύνοψη των νέων πράξεων που ταιριάζουν με το προφίλ '
   'αναζήτησης <strong>[[profile_name]]</strong>.</p>'),
  ('digest_summary', 'en', 'Results digest — summary',
   'Summary of new results: [[profile_name]]',
   '<p>Hello [[full_name]],</p>'
   '<p>Here is a summary of the new acts matching your saved search '
   '<strong>[[profile_name]]</strong>.</p>')
ON CONFLICT (slug, lang) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 5. Grants (belt-and-suspenders; default privileges already cover the owner)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON proc.digest_recipient TO app_runtime';
    EXECUTE 'GRANT USAGE, SELECT, UPDATE ON SEQUENCE proc.digest_recipient_id_seq TO app_runtime';
  END IF;
END $$;

COMMIT;
