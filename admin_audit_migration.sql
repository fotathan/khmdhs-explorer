-- admin_audit_migration.sql
--
-- Admin audit log: one row per state-changing request to an admin surface
-- (POST/PUT/PATCH/DELETE under /admin, /tables, and the inline name-edit /
-- gemi-refresh mutations). Recorded centrally in AuthMiddleware, so it captures
-- who did what — including rejected attempts (403 for non-admins or CSRF).
--
-- Safe/idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.admin_action (
    id          bigserial   PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    user_id     bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    username    text,                       -- snapshot (survives user deletion)
    method      text        NOT NULL,
    path        text        NOT NULL,
    status_code integer,
    ip          text
);

CREATE INDEX IF NOT EXISTS ix_admin_action_at   ON proc.admin_action (at DESC);
CREATE INDEX IF NOT EXISTS ix_admin_action_user ON proc.admin_action (user_id, at DESC);

COMMIT;
