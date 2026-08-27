-- migrations/20260827112421_passwordless_email_login_links_alongside_passwords.sql
-- passwordless email login links alongside passwords
--
-- "Email me a sign-in link" as a SECOND path onto /login, not a replacement for
-- the password. Existing accounts, admin roles and 2FA all keep working exactly
-- as they do; a customer who does not want to remember a password gets a link
-- instead.
--
-- Why the token is stored HASHED, unlike proc.digest_run.token
-- ------------------------------------------------------------
-- A digest token opens a result set that still requires the owner's session —
-- it is a pointer, not a credential. A login-link token IS a credential: it
-- creates a session on its own. So only sha256(token) is stored here, and the
-- raw value exists in exactly one place, the email. A leaked database dump is
-- then a list of useless hashes rather than a set of live logins.
-- sha256 (not scrypt) is right for this one: the token is 32 bytes of CSPRNG
-- output, so there is no dictionary to attack and nothing for a slow KDF to buy.
--
-- Single use and short lived: consume_login_link() flips used_at inside the
-- same UPDATE ... WHERE used_at IS NULL that reads the row, so two concurrent
-- clicks cannot both produce a session. expires_at is stamped at issue time
-- (LOGIN_LINK_TTL_SECONDS, default 900 = 15 minutes).
--
-- next_url is carried on the row rather than in the URL: the link lands on an
-- interstitial that must not be turned into an open redirect by editing the
-- query string of a mailed URL. It is re-checked against _safe_next anyway.
--
-- email_verified_at on app_user is the by-product worth keeping. Registration
-- never confirmed the address (email is nullable and unverified), so today
-- nothing in the system knows whether proc.app_user.email is real. Completing a
-- link login proves the account holder reads that mailbox — stamp it and the
-- record exists for the deliverability work later.
--
-- Privileges need no statements here: the least-privilege grants migration
-- (20260709201920) set ALTER DEFAULT PRIVILEGES IN SCHEMA proc for the owner
-- role, so app_runtime picks up DML on this table automatically.
--
-- Idempotent throughout.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.login_link (
    id           bigserial   PRIMARY KEY,
    user_id      bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    token_hash   text        NOT NULL,     -- sha256 hex of the mailed token
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    used_at      timestamptz,              -- NULL = still live; set on consume
    requested_ip text,                     -- who asked, for abuse triage
    next_url     text                      -- where to land, captured at request time
);

COMMENT ON TABLE proc.login_link IS
    'Single-use, short-lived passwordless sign-in tokens mailed to an account''s '
    'own address. Stores sha256(token) only — the raw token exists in the email. '
    'A link completes the password step, NOT the 2FA step.';

-- The lookup every click does, and the guarantee that one token is one row.
CREATE UNIQUE INDEX IF NOT EXISTS ux_login_link_token_hash
    ON proc.login_link (token_hash);

-- Invalidating a user's outstanding links (on use, on password change) touches
-- only the live ones.
CREATE INDEX IF NOT EXISTS ix_login_link_user_live
    ON proc.login_link (user_id) WHERE used_at IS NULL;

-- Expired/spent rows are deleted opportunistically on each issue (the same
-- trick proc.login_throttle uses); this index keeps that sweep cheap.
CREATE INDEX IF NOT EXISTS ix_login_link_expires_at
    ON proc.login_link (expires_at);

ALTER TABLE proc.app_user
  ADD COLUMN IF NOT EXISTS email_verified_at timestamptz;

COMMENT ON COLUMN proc.app_user.email_verified_at IS
    'First time a login link mailed to this address was successfully used — '
    'i.e. proof the account holder reads it. NULL = never confirmed.';

-- Wording for the sign-in email, editable at /admin/email-templates like every
-- other template (so changing a sentence needs no deploy). `subject` is the
-- mail subject and body_html is the INTRO fragment printed above the button;
-- [[field]] tokens resolve exactly as the digest ones do, through
-- digests._soft_resolve — an optional token that resolves to nothing drops out
-- instead of failing the send, because there is no human in the loop.
--
-- Deliberately NO [[link]] token: the URL is placed by the email template
-- (app/templates/email_login_link.html), so an admin editing this wording can
-- never accidentally delete, truncate or HTML-escape the credential.
INSERT INTO proc.email_template (slug, lang, name, subject, body_html) VALUES
  ('login_link', 'el', 'Σύνδεσμος σύνδεσης',
   'Ο σύνδεσμος σύνδεσής σας',
   '<p>Καλημέρα [[full_name]],</p>'
   '<p>Ζητήσατε σύνδεση χωρίς κωδικό. Πατήστε το κουμπί παρακάτω για να '
   'συνδεθείτε στον λογαριασμό σας.</p>'),
  ('login_link', 'en', 'Sign-in link',
   'Your sign-in link',
   '<p>Hello [[full_name]],</p>'
   '<p>You asked to sign in without a password. Use the button below to sign '
   'in to your account.</p>')
ON CONFLICT (slug, lang) DO NOTHING;

COMMIT;
