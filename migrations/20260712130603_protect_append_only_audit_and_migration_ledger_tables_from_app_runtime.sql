-- migrations/20260712130603_protect_append_only_audit_and_migration_ledger_tables_from_app_runtime.sql
-- protect append-only audit and migration ledger tables from app_runtime
--
-- The base least-privilege migration
-- (20260709201920_app_runtime_least_privilege_role_grants.sql) granted
-- app_runtime SELECT/INSERT/UPDATE/DELETE on ALL tables in proc. Two tables
-- must never be rewritten or erased by the running app, even if the app process
-- is compromised or has a bug:
--
--   proc.schema_migration  — the migration ledger (owned/written by migrate.py,
--     which connects as the schema OWNER, not app_runtime). The app neither
--     reads nor writes it, so app_runtime gets NO privileges at all here.
--     Without this, an attacker with app_runtime could forge/erase applied-
--     migration records and mask schema tampering.
--
--   proc.admin_action      — the admin audit log. It is strictly append-only:
--     the app INSERTs one row per state-changing admin request (app/main.py)
--     and SELECTs to render the audit view (app/admin.py). It must never be
--     UPDATEd or DELETEd by the app, so a bad actor can't rewrite or wipe the
--     trail of what they did. Keep SELECT + INSERT, revoke UPDATE + DELETE.
--
-- Note on FK cleanup: admin_action.user_id is ON DELETE SET NULL. That
-- referential action runs with the table OWNER's privileges, not the current
-- role's, so revoking UPDATE from app_runtime does NOT break user deletion.
--
-- These REVOKEs override the earlier blanket GRANT on existing tables. The base
-- migration's ALTER DEFAULT PRIVILEGES only affects FUTURE tables, so it does
-- not silently re-grant these.
--
-- Idempotent (REVOKE is a no-op if the privilege isn't held). Guarded on the
-- role existing so it's safe to apply on a DB where app_runtime was never
-- created (e.g. a dev box still connecting as owner).

BEGIN;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    -- Migration ledger: app_runtime has no business touching it.
    REVOKE SELECT, INSERT, UPDATE, DELETE
      ON proc.schema_migration FROM app_runtime;

    -- Audit log: append + read only. No rewrites, no deletes.
    REVOKE UPDATE, DELETE
      ON proc.admin_action FROM app_runtime;
  END IF;
END $$;

COMMIT;
