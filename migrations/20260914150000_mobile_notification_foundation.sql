-- Native mobile notification preferences, push subscriptions and inbox.
--
-- Provider delivery is intentionally not enabled by this migration. Push
-- tokens are encrypted by the application before storage; only ciphertext
-- and a keyed lookup hash reach PostgreSQL.

BEGIN;

ALTER TABLE proc.mobile_device
  ADD COLUMN IF NOT EXISTS permission_status text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS push_provider text NOT NULL DEFAULT 'expo',
  ADD COLUMN IF NOT EXISTS push_token_ciphertext bytea,
  ADD COLUMN IF NOT EXISTS push_token_hash bytea,
  ADD COLUMN IF NOT EXISTS push_token_key_version smallint;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='mobile_device_permission_status_ck'
                   AND conrelid='proc.mobile_device'::regclass) THEN
    ALTER TABLE proc.mobile_device ADD CONSTRAINT mobile_device_permission_status_ck
      CHECK (permission_status IN ('unknown','granted','denied','provisional'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='mobile_device_push_provider_ck'
                   AND conrelid='proc.mobile_device'::regclass) THEN
    ALTER TABLE proc.mobile_device ADD CONSTRAINT mobile_device_push_provider_ck
      CHECK (push_provider IN ('expo'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='mobile_device_push_token_ck'
                   AND conrelid='proc.mobile_device'::regclass) THEN
    ALTER TABLE proc.mobile_device ADD CONSTRAINT mobile_device_push_token_ck CHECK (
      (push_token_ciphertext IS NULL AND push_token_hash IS NULL AND push_token_key_version IS NULL)
      OR
      (push_token_ciphertext IS NOT NULL AND push_token_hash IS NOT NULL AND push_token_key_version > 0)
    );
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mobile_device_push_token_hash
  ON proc.mobile_device (push_token_hash) WHERE push_token_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS proc.mobile_notification_preference (
  user_id       bigint      PRIMARY KEY REFERENCES proc.app_user(id) ON DELETE CASCADE,
  paused        boolean     NOT NULL DEFAULT false,
  timezone      text        NOT NULL DEFAULT 'Europe/Athens',
  quiet_start   time        NOT NULL DEFAULT '22:00',
  quiet_end     time        NOT NULL DEFAULT '08:00',
  summary_time  time        NOT NULL DEFAULT '08:30',
  daily_cap     smallint    NOT NULL DEFAULT 6,
  lang          text        NOT NULL DEFAULT 'el',
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT mobile_notification_preference_timezone_ck CHECK (length(timezone) BETWEEN 1 AND 64),
  CONSTRAINT mobile_notification_preference_daily_cap_ck CHECK (daily_cap BETWEEN 1 AND 10),
  CONSTRAINT mobile_notification_preference_lang_ck CHECK (lang IN ('el','en'))
);

CREATE TABLE IF NOT EXISTS proc.push_subscription (
  id                 bigserial   PRIMARY KEY,
  user_id            bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
  search_profile_id  bigint      NOT NULL REFERENCES proc.search_profile(id) ON DELETE CASCADE,
  is_active          boolean     NOT NULL DEFAULT false,
  delivery_mode      text        NOT NULL DEFAULT 'daily',
  new_matches        boolean     NOT NULL DEFAULT true,
  deadlines          boolean     NOT NULL DEFAULT true,
  lead_days          smallint[]  NOT NULL DEFAULT ARRAY[7,1]::smallint[],
  last_cursor        timestamptz,
  last_evaluated_at  timestamptz,
  reactivated_at     timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT push_subscription_user_profile_uk UNIQUE (user_id, search_profile_id),
  CONSTRAINT push_subscription_delivery_mode_ck CHECK (delivery_mode IN ('immediate','daily')),
  CONSTRAINT push_subscription_lead_days_ck CHECK (
    cardinality(lead_days) BETWEEN 1 AND 6
    AND array_position(lead_days,NULL) IS NULL
    AND 0 <= ALL(lead_days) AND 90 >= ALL(lead_days)
  )
);

CREATE INDEX IF NOT EXISTS ix_push_subscription_due
  ON proc.push_subscription (delivery_mode, last_cursor, id) WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_push_subscription_user
  ON proc.push_subscription (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS proc.notification_event (
  id                    bigserial   PRIMARY KEY,
  user_id               bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
  push_subscription_id  bigint      REFERENCES proc.push_subscription(id) ON DELETE SET NULL,
  event_type            text        NOT NULL,
  dedupe_key            bytea       NOT NULL UNIQUE,
  target_kind           text        NOT NULL,
  target_id             text        NOT NULL,
  adam                  text,
  title                 text        NOT NULL,
  body                  text        NOT NULL,
  context               jsonb       NOT NULL DEFAULT '{}'::jsonb,
  created_at            timestamptz NOT NULL DEFAULT now(),
  read_at               timestamptz,
  deleted_at            timestamptz,
  expires_at            timestamptz NOT NULL DEFAULT (now() + interval '90 days'),
  CONSTRAINT notification_event_type_ck CHECK (event_type IN ('new_match','deadline','daily_summary')),
  CONSTRAINT notification_event_target_kind_ck CHECK (target_kind IN ('act','search_summary')),
  CONSTRAINT notification_event_target_id_ck CHECK (length(target_id) BETWEEN 1 AND 256),
  CONSTRAINT notification_event_adam_ck CHECK (adam IS NULL OR length(adam) BETWEEN 1 AND 128),
  CONSTRAINT notification_event_title_ck CHECK (length(title) BETWEEN 1 AND 180),
  CONSTRAINT notification_event_body_ck CHECK (length(body) BETWEEN 1 AND 500),
  CONSTRAINT notification_event_context_ck CHECK (jsonb_typeof(context)='object'),
  CONSTRAINT notification_event_expiry_ck CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS ix_notification_event_inbox
  ON proc.notification_event (user_id, created_at DESC, id DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_notification_event_unread
  ON proc.notification_event (user_id, created_at DESC, id DESC)
  WHERE deleted_at IS NULL AND read_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_notification_event_cleanup
  ON proc.notification_event (expires_at, deleted_at);

CREATE TABLE IF NOT EXISTS proc.notification_delivery (
  id                 bigserial   PRIMARY KEY,
  event_id           bigint      NOT NULL REFERENCES proc.notification_event(id) ON DELETE CASCADE,
  device_id          bigint      NOT NULL REFERENCES proc.mobile_device(id) ON DELETE CASCADE,
  state              text        NOT NULL DEFAULT 'queued',
  provider           text        NOT NULL DEFAULT 'expo',
  provider_ticket_id text,
  provider_receipt_id text,
  attempt_count      smallint    NOT NULL DEFAULT 0,
  next_attempt_at    timestamptz NOT NULL DEFAULT now(),
  error_code         text,
  claimed_at         timestamptz,
  claimed_by         text,
  lease_expires_at   timestamptz,
  submitted_at       timestamptz,
  receipt_checked_at timestamptz,
  terminal_at        timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT notification_delivery_event_device_uk UNIQUE (event_id, device_id),
  CONSTRAINT notification_delivery_state_ck CHECK (state IN ('queued','submitting','accepted','retry','rejected','expired')),
  CONSTRAINT notification_delivery_provider_ck CHECK (provider IN ('expo')),
  CONSTRAINT notification_delivery_attempt_ck CHECK (attempt_count BETWEEN 0 AND 20),
  CONSTRAINT notification_delivery_error_code_ck CHECK (error_code IS NULL OR length(error_code) <= 64),
  CONSTRAINT notification_delivery_ticket_ck CHECK (provider_ticket_id IS NULL OR length(provider_ticket_id) <= 256),
  CONSTRAINT notification_delivery_receipt_ck CHECK (provider_receipt_id IS NULL OR length(provider_receipt_id) <= 256)
);

CREATE INDEX IF NOT EXISTS ix_notification_delivery_work
  ON proc.notification_delivery (next_attempt_at, id)
  WHERE state IN ('queued','retry');
CREATE INDEX IF NOT EXISTS ix_notification_delivery_cleanup
  ON proc.notification_delivery (terminal_at) WHERE terminal_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_notification_delivery_receipt
  ON proc.notification_delivery (next_attempt_at, id)
  WHERE state='accepted' AND terminal_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_delivery_ticket
  ON proc.notification_delivery (provider_ticket_id)
  WHERE provider_ticket_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS proc.push_deadline_notice (
  id                    bigserial   PRIMARY KEY,
  push_subscription_id  bigint      NOT NULL REFERENCES proc.push_subscription(id) ON DELETE CASCADE,
  adam                  text        NOT NULL,
  lead_days             smallint    NOT NULL,
  deadline_at           timestamptz NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT push_deadline_notice_uk UNIQUE (push_subscription_id, adam, lead_days, deadline_at),
  CONSTRAINT push_deadline_notice_lead_days_ck CHECK (lead_days BETWEEN 0 AND 90),
  CONSTRAINT push_deadline_notice_adam_ck CHECK (length(adam) BETWEEN 1 AND 128)
);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_runtime') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON
      proc.mobile_notification_preference,
      proc.push_subscription,
      proc.notification_event,
      proc.notification_delivery,
      proc.push_deadline_notice
    TO app_runtime;
    GRANT USAGE, SELECT, UPDATE ON SEQUENCE
      proc.push_subscription_id_seq,
      proc.notification_event_id_seq,
      proc.notification_delivery_id_seq,
      proc.push_deadline_notice_id_seq
    TO app_runtime;
  END IF;
END $$;

COMMIT;
