-- migrations/20260710083903_mfa_totp_on_app_user.sql
-- mfa totp on app_user
--
-- Optional TOTP two-factor for any account (recommended for admins). A user
-- enrolls self-service: we store the base32 TOTP secret + a set of one-time
-- recovery codes (scrypt-hashed, same as passwords) so a lost authenticator
-- doesn't lock them out. mfa_enabled gates the extra step at login.
--
-- Idempotent.

BEGIN;

ALTER TABLE proc.app_user
    ADD COLUMN IF NOT EXISTS mfa_secret         text,
    ADD COLUMN IF NOT EXISTS mfa_enabled        boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS mfa_recovery_codes text[] NOT NULL DEFAULT '{}';

COMMIT;
