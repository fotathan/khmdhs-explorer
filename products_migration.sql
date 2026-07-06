-- products_migration.sql
--
-- Subscription layer on top of proc.app_user. A customer's read access becomes
-- time-boxed: they hold a subscription to a *product* with an expiry. When it
-- lapses they fall back to the anonymous teaser until re-granted.
--
--   product        — the catalogue (test / paid) with an admin-editable default
--                    period. Access is identical between products; they differ
--                    only in default duration and the resulting status label.
--   user_subscription — one row per grant (history kept for audit). A user's
--                    CURRENT grant is the row with the greatest expires_at.
--
-- Derived customer statuses (computed in app/auth.py, not stored):
--   tester / subscriber / expired_tester / expired_subscriber / none
--
-- role (admin|customer) stays the RBAC axis; admins are never gated and have no
-- subscription. Apply to local now; apply to prod before deploying the change.
--
-- Safe/idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.product (
    code                text        PRIMARY KEY,          -- 'test' | 'paid'
    name                text        NOT NULL,
    default_period_days integer     NOT NULL CHECK (default_period_days > 0),
    is_active           boolean     NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- Seed the two products. ON CONFLICT DO NOTHING so re-running never clobbers an
-- admin's edited default_period_days.
INSERT INTO proc.product (code, name, default_period_days) VALUES
    ('test', 'Δοκιμαστικό',   7),
    ('paid', 'Συνδρομή',    365)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS proc.user_subscription (
    id           bigserial   PRIMARY KEY,
    user_id      bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    product_code text        NOT NULL REFERENCES proc.product(code),
    started_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    granted_by   bigint      REFERENCES proc.app_user(id),   -- NULL = self (registration)
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Current-grant lookup: greatest expires_at per user.
CREATE INDEX IF NOT EXISTS ix_user_subscription_user_expires
    ON proc.user_subscription (user_id, expires_at DESC);

COMMENT ON TABLE proc.product IS
    'Subscription products. Access is identical; default_period_days is the '
    'admin-editable default grant length (test=7, paid=365).';
COMMENT ON TABLE proc.user_subscription IS
    'Per-grant subscription history. Current grant = greatest expires_at for '
    'the user; when that is in the past the customer falls back to the teaser.';

-- Backfill: every existing active customer WITHOUT any subscription gets a
-- 7-day test grant, so nobody loses the access they have today. Admins skip.
INSERT INTO proc.user_subscription (user_id, product_code, expires_at, granted_by)
SELECT u.id, 'test', now() + (7 * interval '1 day'), NULL
FROM proc.app_user u
WHERE u.role = 'customer'
  AND u.is_active
  AND NOT EXISTS (SELECT 1 FROM proc.user_subscription s WHERE s.user_id = u.id);

COMMIT;
