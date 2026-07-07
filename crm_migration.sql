-- crm_migration.sql
--
-- CRM Phase 1: an editable profile record per customer account, holding the
-- extra contact/company/location/meta fields admins keep about a customer.
-- One row per app_user (1:1); a missing row simply means an empty profile (the
-- CRM page LEFT JOINs it, so no backfill is needed).
--
-- role (admin|customer) still lives on proc.app_user; this table only enriches
-- customers. Phase 2 (notes / calls / tasks) adds separate activity tables.
--
-- Safe/idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.customer_profile (
    user_id     bigint      PRIMARY KEY REFERENCES proc.app_user(id) ON DELETE CASCADE,
    -- Contact
    full_name   text,
    phone       text,
    mobile      text,
    job_title   text,
    -- Company
    company     text,
    vat_number  text,
    industry    text,
    -- Location
    country     text,
    city        text,
    address     text,
    -- CRM meta
    lead_source text,
    about       text,
    -- audit
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  bigint      REFERENCES proc.app_user(id)
);

COMMENT ON TABLE proc.customer_profile IS
    'Admin-editable CRM profile fields for a customer account (1:1 with '
    'proc.app_user). Missing row = empty profile.';

COMMIT;
