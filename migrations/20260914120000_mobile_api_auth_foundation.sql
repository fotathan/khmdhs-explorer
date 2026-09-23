-- Native mobile API authentication foundation.
--
-- Browser sessions remain signed cookies. These tables are exclusively for
-- device-scoped native sessions: short-lived opaque access tokens, rotating
-- refresh tokens, and one-use MFA challenges. Raw tokens are never stored.
--
-- Additive and idempotent. Apply before enabling MOBILE_API_ENABLED.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.mobile_device (
    id                bigserial   PRIMARY KEY,
    user_id           bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    installation_id   uuid        NOT NULL,
    platform          text        NOT NULL CHECK (platform IN ('ios', 'android')),
    display_name      text,
    app_version       text        NOT NULL,
    os_version        text,
    locale            text        NOT NULL DEFAULT 'el' CHECK (locale IN ('el', 'en')),
    timezone          text        NOT NULL DEFAULT 'Europe/Athens',
    enabled           boolean     NOT NULL DEFAULT true,
    last_seen_at      timestamptz NOT NULL DEFAULT now(),
    revoked_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mobile_device_installation_uk UNIQUE (user_id, installation_id),
    CONSTRAINT mobile_device_display_name_ck CHECK (display_name IS NULL OR length(display_name) <= 80),
    CONSTRAINT mobile_device_app_version_ck CHECK (length(app_version) BETWEEN 1 AND 32),
    CONSTRAINT mobile_device_os_version_ck CHECK (os_version IS NULL OR length(os_version) <= 32),
    CONSTRAINT mobile_device_timezone_ck CHECK (length(timezone) BETWEEN 1 AND 64)
);

CREATE INDEX IF NOT EXISTS ix_mobile_device_user_active
    ON proc.mobile_device (user_id, last_seen_at DESC)
    WHERE enabled AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS proc.mobile_session (
    id                  uuid        PRIMARY KEY,
    user_id             bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    device_id           bigint      NOT NULL REFERENCES proc.mobile_device(id) ON DELETE CASCADE,
    session_version     integer     NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    last_used_at        timestamptz NOT NULL DEFAULT now(),
    revoked_at          timestamptz,
    revoke_reason       text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mobile_session_expiry_ck CHECK (absolute_expires_at > created_at),
    CONSTRAINT mobile_session_revoke_reason_ck CHECK (
        revoke_reason IS NULL OR revoke_reason IN
        ('logout', 'device_revoked', 'refresh_reuse', 'session_version',
         'expired', 'account_inactive', 'administrative'))
);

CREATE INDEX IF NOT EXISTS ix_mobile_session_user_active
    ON proc.mobile_session (user_id, last_used_at DESC)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_mobile_session_device_active
    ON proc.mobile_session (device_id, last_used_at DESC)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS proc.mobile_refresh_token (
    id                 bigserial   PRIMARY KEY,
    session_id         uuid        NOT NULL REFERENCES proc.mobile_session(id) ON DELETE CASCADE,
    token_hash         bytea       NOT NULL UNIQUE,
    token_hash_version smallint    NOT NULL DEFAULT 1,
    expires_at         timestamptz NOT NULL,
    used_at            timestamptz,
    replaced_by_id     bigint      REFERENCES proc.mobile_refresh_token(id) ON DELETE SET NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mobile_refresh_hash_version_ck CHECK (token_hash_version > 0),
    CONSTRAINT mobile_refresh_expiry_ck CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS ix_mobile_refresh_session_created
    ON proc.mobile_refresh_token (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_mobile_refresh_cleanup
    ON proc.mobile_refresh_token (expires_at, used_at);

CREATE TABLE IF NOT EXISTS proc.mobile_access_token (
    id                 bigserial   PRIMARY KEY,
    session_id         uuid        NOT NULL REFERENCES proc.mobile_session(id) ON DELETE CASCADE,
    token_hash         bytea       NOT NULL UNIQUE,
    token_hash_version smallint    NOT NULL DEFAULT 1,
    expires_at         timestamptz NOT NULL,
    revoked_at         timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mobile_access_hash_version_ck CHECK (token_hash_version > 0),
    CONSTRAINT mobile_access_expiry_ck CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS ix_mobile_access_session_active
    ON proc.mobile_access_token (session_id, expires_at DESC)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_mobile_access_cleanup
    ON proc.mobile_access_token (expires_at);

CREATE TABLE IF NOT EXISTS proc.mobile_auth_challenge (
    id                 bigserial   PRIMARY KEY,
    user_id            bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    challenge_hash     bytea       NOT NULL UNIQUE,
    token_hash_version smallint    NOT NULL DEFAULT 1,
    purpose            text        NOT NULL DEFAULT 'login_mfa'
                                   CHECK (purpose IN ('login_mfa')),
    device             jsonb       NOT NULL,
    attempts           smallint    NOT NULL DEFAULT 0,
    max_attempts       smallint    NOT NULL DEFAULT 5,
    expires_at         timestamptz NOT NULL,
    consumed_at        timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mobile_challenge_hash_version_ck CHECK (token_hash_version > 0),
    CONSTRAINT mobile_challenge_attempts_ck CHECK (
        attempts >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempts <= max_attempts),
    CONSTRAINT mobile_challenge_expiry_ck CHECK (expires_at > created_at),
    CONSTRAINT mobile_challenge_device_ck CHECK (jsonb_typeof(device) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_mobile_challenge_cleanup
    ON proc.mobile_auth_challenge (expires_at, consumed_at);

CREATE TABLE IF NOT EXISTS proc.api_idempotency_key (
    id                  bigserial   PRIMARY KEY,
    user_id             bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    method              text        NOT NULL,
    path                text        NOT NULL,
    key_hash            bytea       NOT NULL,
    request_fingerprint bytea       NOT NULL,
    response_status     smallint,
    response_body       jsonb,
    expires_at          timestamptz NOT NULL DEFAULT (now() + interval '24 hours'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT api_idempotency_method_ck CHECK (method IN ('POST', 'PUT', 'PATCH', 'DELETE')),
    CONSTRAINT api_idempotency_path_ck CHECK (length(path) BETWEEN 1 AND 512),
    CONSTRAINT api_idempotency_status_ck CHECK (
        response_status IS NULL OR response_status BETWEEN 200 AND 599),
    CONSTRAINT api_idempotency_key_uk UNIQUE (user_id, method, path, key_hash)
);

CREATE INDEX IF NOT EXISTS ix_api_idempotency_cleanup
    ON proc.api_idempotency_key (expires_at);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON
      proc.mobile_device,
      proc.mobile_session,
      proc.mobile_refresh_token,
      proc.mobile_access_token,
      proc.mobile_auth_challenge,
      proc.api_idempotency_key
    TO app_runtime;
    GRANT USAGE, SELECT, UPDATE ON SEQUENCE
      proc.mobile_device_id_seq,
      proc.mobile_refresh_token_id_seq,
      proc.mobile_access_token_id_seq,
      proc.mobile_auth_challenge_id_seq,
      proc.api_idempotency_key_id_seq
    TO app_runtime;
  END IF;
END $$;

COMMIT;
