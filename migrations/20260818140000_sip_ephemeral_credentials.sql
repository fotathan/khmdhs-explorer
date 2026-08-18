-- migrations/20260818140000_sip_ephemeral_credentials.sql
-- ephemeral SIP credentials (Phase 4, Option A — short-lived auth)
--
-- See TELEPHONY_PROD_PHASE4_CREDENTIALS.md. For a public prod deployment the
-- browser softphone must not hold a durable plaintext SIP secret. This adds the
-- two tables that let /telephony/config mint a SHORT-LIVED secret per agent:
--
--   proc.sip_credential — app bookkeeping: the current ephemeral secret + expiry,
--                         one row per agent (reused within its TTL, then rotated).
--   proc.ps_auths       — the PJSIP realtime `auth` object Asterisk reads (when
--                         wired to realtime on the prod host); the app writes the
--                         rotated password here.
--
-- Opt-in: the app only mints/writes these when SIP_AUTH_MODE=realtime. In the
-- default 'static' mode (local dev, the demo pjsip.conf) they stay empty and the
-- durable sip_extension.sip_secret path is used, so this migration is inert until
-- realtime is turned on. proc.sip_extension.sip_secret is intentionally NOT
-- dropped here — a prod host fully on realtime auth can drop it in a follow-up.
--
-- Wrap the body so a failure leaves nothing half-applied. Idempotent throughout.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.sip_credential (
    user_id    bigint      PRIMARY KEY REFERENCES proc.app_user(id) ON DELETE CASCADE,
    auth_id    text        NOT NULL,       -- matches the pjsip.conf endpoint's auth= name
    secret     text        NOT NULL,       -- current ephemeral password (rotated)
    expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Canonical subset of the columns Asterisk's PJSIP realtime reads for an auth.
CREATE TABLE IF NOT EXISTS proc.ps_auths (
    id         text PRIMARY KEY,           -- = sip_credential.auth_id
    auth_type  text NOT NULL DEFAULT 'userpass',
    username   text NOT NULL,              -- = sip_extension.sip_user (stable)
    password   text NOT NULL,              -- = the ephemeral secret
    realm      text
);

COMMIT;
