-- crm_activity_migration.sql
--
-- CRM Phase 2: activity records attached to a customer account —
--   notes  : freeform, timestamped, authored.
--   calls  : subject + direction (incoming/outgoing) + status + scheduled date
--            + outcome; assignable to an admin.
--   tasks  : subject + body + status + due date + outcome; assignable.
--
-- All reference proc.app_user (the customer via user_id; the admin author /
-- assignee via *_by / assigned_to). ON DELETE CASCADE on the customer so
-- removing an account cleans up its activity; assignee/author use SET NULL so
-- deleting an admin doesn't erase history.
--
-- Safe/idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.customer_note (
    id         bigserial   PRIMARY KEY,
    user_id    bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    body       text        NOT NULL,
    author_id  bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_customer_note_user ON proc.customer_note (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS proc.customer_call (
    id           bigserial   PRIMARY KEY,
    user_id      bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    subject      text,
    direction    text        NOT NULL DEFAULT 'outgoing'
                             CHECK (direction IN ('incoming', 'outgoing')),
    status       text        NOT NULL DEFAULT 'planned'
                             CHECK (status IN ('planned', 'held', 'not_held',
                                               'not_answered', 'cancelled')),
    scheduled_at timestamptz,
    outcome      text,
    assigned_to  bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    created_by   bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz
);
CREATE INDEX IF NOT EXISTS ix_customer_call_user
    ON proc.customer_call (user_id, coalesce(scheduled_at, created_at) DESC);

CREATE TABLE IF NOT EXISTS proc.customer_task (
    id           bigserial   PRIMARY KEY,
    user_id      bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    subject      text        NOT NULL,
    body         text,
    status       text        NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'done', 'cancelled')),
    due_at       timestamptz,
    outcome      text,
    assigned_to  bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    created_by   bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_customer_task_user ON proc.customer_task (user_id, created_at DESC);

COMMIT;
