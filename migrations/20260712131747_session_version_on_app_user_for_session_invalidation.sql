-- migrations/20260712131747_session_version_on_app_user_for_session_invalidation.sql
-- session_version on app_user for session invalidation
--
-- Server-side session invalidation. Each login stamps the user's current
-- session_version into the (signed) session cookie; the auth middleware compares
-- it to the DB value on every request. Bumping the DB value therefore forces an
-- immediate re-login of ALL that user's existing sessions — used on password
-- change, MFA enable/disable, role change, and admin password reset, so a stolen
-- or lingering cookie stops working the moment credentials/privileges change.
--
-- Pre-existing sessions (issued before this column existed) carry no version and
-- are treated as stale on the first request after deploy — a one-time, expected
-- re-login for everyone. That is the safe default.
--
-- Idempotent.

BEGIN;

ALTER TABLE proc.app_user
  ADD COLUMN IF NOT EXISTS session_version integer NOT NULL DEFAULT 0;

COMMIT;
