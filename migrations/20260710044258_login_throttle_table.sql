-- migrations/20260710044258_login_throttle_table.sql
-- login throttle table
--
-- Move the login brute-force throttle off in-process memory (app/auth.py's
-- _FAILS dict) into the DB so lockouts survive restarts and are shared across
-- workers. One row per (username_lower|ip) key.
--
-- Wrap the body so a failure leaves nothing half-applied. Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.login_throttle (
    key          text PRIMARY KEY,               -- "<username_lower>|<ip>"
    fail_count   integer     NOT NULL DEFAULT 0,
    locked_until timestamptz,                     -- NULL until the threshold trips
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Supports the cheap sweep of stale rows in auth.throttle_fail's cleanup.
CREATE INDEX IF NOT EXISTS idx_login_throttle_updated_at
    ON proc.login_throttle (updated_at);

COMMIT;
