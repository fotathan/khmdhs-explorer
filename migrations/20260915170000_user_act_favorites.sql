-- Account-level act favorites shared by customer clients.
-- A favorite is deliberately separate from saved-search and notification
-- subscriptions: it bookmarks one act and never enables delivery by itself.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.user_favorite_act (
  user_id     bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
  adam        text        NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT user_favorite_act_pk PRIMARY KEY (user_id, adam)
);

CREATE INDEX IF NOT EXISTS ix_user_favorite_act_recent
  ON proc.user_favorite_act (user_id, created_at DESC, adam DESC);

COMMENT ON TABLE proc.user_favorite_act IS
  'Customer bookmarks for individual procurement acts; does not imply notification delivery.';

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_runtime') THEN
    GRANT SELECT, INSERT, DELETE ON proc.user_favorite_act TO app_runtime;
  END IF;
END $$;

COMMIT;
