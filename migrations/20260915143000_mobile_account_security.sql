-- Native mobile account-security enrollment state.
--
-- A provisional TOTP secret is kept for five minutes behind a hashed, one-use
-- enrollment identifier. Confirming enrollment moves the secret to app_user;
-- cleanup removes expired/consumed rows after the standard auth audit window.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.mobile_mfa_enrollment (
    id                 bigserial   PRIMARY KEY,
    user_id            bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    enrollment_hash    bytea       NOT NULL UNIQUE,
    token_hash_version smallint    NOT NULL DEFAULT 1,
    secret             text        NOT NULL,
    attempts           smallint    NOT NULL DEFAULT 0,
    max_attempts       smallint    NOT NULL DEFAULT 5,
    expires_at         timestamptz NOT NULL,
    consumed_at        timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mobile_mfa_enrollment_hash_version_ck CHECK (token_hash_version > 0),
    CONSTRAINT mobile_mfa_enrollment_attempts_ck CHECK (
        attempts >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempts <= max_attempts),
    CONSTRAINT mobile_mfa_enrollment_expiry_ck CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS ix_mobile_mfa_enrollment_cleanup
    ON proc.mobile_mfa_enrollment (expires_at, consumed_at);
CREATE INDEX IF NOT EXISTS ix_mobile_mfa_enrollment_user_active
    ON proc.mobile_mfa_enrollment (user_id, created_at DESC)
    WHERE consumed_at IS NULL;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON proc.mobile_mfa_enrollment TO app_runtime;
    GRANT USAGE, SELECT, UPDATE ON SEQUENCE proc.mobile_mfa_enrollment_id_seq TO app_runtime;
  END IF;
END $$;

COMMIT;
