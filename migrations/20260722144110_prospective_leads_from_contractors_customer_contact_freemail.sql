-- migrations/20260722144110_prospective_leads_from_contractors_customer_contact_freemail.sql
-- Prospective leads created directly from the Contractor Database (economic_operator).
--
-- A lead is a non-login customer account (proc.app_user role=customer) with a
-- stored crm_stage='prospective'. This adds the CRM fields to carry the mapped
-- contractor data + lead metadata, a multi-value contacts child table (main +
-- inactive contacts), and a configurable freemail-domain list used by duplicate
-- detection. Idempotent.

BEGIN;

-- --------------------------------------------------------------------------- --
-- 1. Lead / CRM fields on the (1:1-with-app_user) customer profile.
-- --------------------------------------------------------------------------- --
ALTER TABLE proc.customer_profile
  ADD COLUMN IF NOT EXISTS crm_stage       text,      -- NULL (normal) | 'prospective'
  ADD COLUMN IF NOT EXISTS service         text,      -- e.g. 'TAS'
  ADD COLUMN IF NOT EXISTS manager_id      bigint REFERENCES proc.app_user(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS creation_source text,      -- 'OrgDB' | 'manual' | 'csv'
  ADD COLUMN IF NOT EXISTS operator_id     bigint REFERENCES proc.economic_operator(operator_id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS orgdb_id        text,      -- the contractor's OrgDB id (relation kept on the CRM page)
  ADD COLUMN IF NOT EXISTS tax_number      text,      -- statistical/tax number
  ADD COLUMN IF NOT EXISTS reg_number      text,      -- ΓΕΜΗ / registration number
  ADD COLUMN IF NOT EXISTS postal_code     text,
  ADD COLUMN IF NOT EXISTS is_recipient    boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS ix_customer_profile_stage
  ON proc.customer_profile (crm_stage) WHERE crm_stage IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_customer_profile_operator
  ON proc.customer_profile (operator_id) WHERE operator_id IS NOT NULL;

-- --------------------------------------------------------------------------- --
-- 2. Multi-value customer contacts (main + inactive). The main contact is also
--    mirrored into customer_profile.full_name for the CRM main data.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS proc.customer_contact (
    id           bigserial   PRIMARY KEY,
    user_id      bigint      NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
    ord          smallint    NOT NULL DEFAULT 0,
    first_name   text,
    last_name    text,
    email        text,
    phone        text,
    mobile       text,
    job_title    text,
    is_main      boolean     NOT NULL DEFAULT false,
    is_active    boolean     NOT NULL DEFAULT true,
    is_recipient boolean     NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_customer_contact_user ON proc.customer_contact (user_id);

-- --------------------------------------------------------------------------- --
-- 3. Configurable freemail domains (per-portal; single portal here). Duplicate
--    detection treats a shared email domain as a soft conflict ONLY when the
--    domain is not freemail.
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS proc.crm_freemail_domain (
    domain text PRIMARY KEY
);
INSERT INTO proc.crm_freemail_domain (domain) VALUES
    ('gmail.com'), ('googlemail.com'), ('hotmail.com'), ('yahoo.com'),
    ('outlook.com'), ('live.com'), ('icloud.com'), ('me.com'),
    ('aol.com'), ('protonmail.com'), ('proton.me'),
    ('hotmail.gr'), ('yahoo.gr'), ('windowslive.com'), ('otenet.gr')
ON CONFLICT (domain) DO NOTHING;

-- --------------------------------------------------------------------------- --
-- 4. Grants (belt-and-suspenders; default privileges already cover the owner).
-- --------------------------------------------------------------------------- --
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON
        proc.customer_contact, proc.crm_freemail_domain TO app_runtime';
    EXECUTE 'GRANT USAGE, SELECT, UPDATE ON SEQUENCE proc.customer_contact_id_seq TO app_runtime';
  END IF;
END $$;

COMMIT;
