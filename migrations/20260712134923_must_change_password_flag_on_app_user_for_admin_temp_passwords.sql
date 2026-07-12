-- migrations/20260712134923_must_change_password_flag_on_app_user_for_admin_temp_passwords.sql
-- must_change_password flag on app_user for admin temp passwords
--
-- When an admin issues a temporary password, this flag is set. The auth
-- middleware then forces the user to the change-password page and blocks every
-- other route until they set their own password (which clears the flag). Lets
-- an admin onboard or unlock a user without an email provider.
--
-- Additive, default false; instant (catalog-stored default, no table rewrite).
-- Idempotent.

BEGIN;

ALTER TABLE proc.app_user
  ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;

COMMIT;
