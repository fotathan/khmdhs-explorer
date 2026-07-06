-- user_accounts_migration.sql
--
-- Real accounts + roles, replacing the single shared HTTP-Basic password.
-- Three access tiers exist at runtime: anonymous (no row — public teaser),
-- 'customer' (self-registered, full read), 'admin' (full + /admin). Sessions are
-- signed cookies (no server-side session table needed).
--
-- Passwords are stored as scrypt hashes (app/auth.py) — never plaintext.
-- Apply to local now; apply to prod before deploying the auth change, and create
-- the first admin with:  python3 db.py create-user --username you --role admin
--
-- Safe/idempotent. Case-insensitive uniqueness via lower() unique indexes (no
-- citext extension dependency).

BEGIN;

CREATE TABLE IF NOT EXISTS proc.app_user (
    id             bigserial PRIMARY KEY,
    username       text        NOT NULL,
    email          text,
    password_hash  text        NOT NULL,
    role           text        NOT NULL DEFAULT 'customer'
                               CHECK (role IN ('admin', 'customer')),
    is_active      boolean     NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);

-- Case-insensitive uniqueness for username (always) and email (when present).
CREATE UNIQUE INDEX IF NOT EXISTS ux_app_user_username
    ON proc.app_user (lower(username));
CREATE UNIQUE INDEX IF NOT EXISTS ux_app_user_email
    ON proc.app_user (lower(email)) WHERE email IS NOT NULL;

COMMENT ON TABLE proc.app_user IS
    'Application accounts. role=admin (full + /admin) | customer (full read). '
    'Anonymous visitors have no row and get the public teaser tier.';

COMMIT;
