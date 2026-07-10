-- migrations/20260710123854_search_profile.sql
-- search profile
--
-- Saved searches ("search profiles"). Two scopes:
--   portal   — admin-owned, global; exposed to customers only when is_published.
--   customer — owned by one customer (owner_user_id), exclusive to them + admins.
-- A customer profile may reference a portal profile (based_on_id) as a LIVE link:
-- effective filters = own params if set, else the referenced profile's params.
-- params is the search filter set (the same dict the search page reads).
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.search_profile (
    id            bigserial PRIMARY KEY,
    name          text NOT NULL,
    scope         text NOT NULL DEFAULT 'customer',
    owner_user_id bigint REFERENCES proc.app_user(id) ON DELETE CASCADE,
    based_on_id   bigint REFERENCES proc.search_profile(id) ON DELETE SET NULL,
    params        jsonb,
    is_published  boolean NOT NULL DEFAULT false,
    created_by    bigint REFERENCES proc.app_user(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT search_profile_scope_chk CHECK (scope IN ('portal', 'customer')),
    -- portal profiles are global (no owner); customer profiles must have an owner
    CONSTRAINT search_profile_owner_chk CHECK (
        (scope = 'portal'   AND owner_user_id IS NULL) OR
        (scope = 'customer' AND owner_user_id IS NOT NULL)),
    -- must resolve to some filters: either its own params or a reference
    CONSTRAINT search_profile_params_chk CHECK (params IS NOT NULL OR based_on_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_search_profile_owner
    ON proc.search_profile (owner_user_id) WHERE owner_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_search_profile_portal
    ON proc.search_profile (is_published) WHERE scope = 'portal';

COMMIT;
