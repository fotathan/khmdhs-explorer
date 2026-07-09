-- migrations/20260709201920_app_runtime_least_privilege_role_grants.sql
-- app_runtime least-privilege role grants
--
-- Creates a DML-only role for the web app + ingestion runtime, so the running
-- process no longer connects as the schema OWNER (which can DROP tables, do
-- DDL, CREATE ROLE, and bypass RLS). app_runtime can SELECT/INSERT/UPDATE/DELETE
-- in proc and EXECUTE its functions — nothing else.
--
-- IMPORTANT: this migration creates the role NOLOGIN and only sets privileges.
-- Grant it a login + password OUT OF BAND (never in git), e.g. on the DB host:
--     ALTER ROLE app_runtime WITH LOGIN PASSWORD '<generated-secret>';
-- then point the app/ingestion DATABASE_URL at app_runtime. Keep the OWNER
-- connection string for migrations (migrate.py) and DDL only. See
-- DB_ROLES_RUNBOOK.md for the full rollout.
--
-- Idempotent.

BEGIN;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    CREATE ROLE app_runtime NOLOGIN;
  END IF;
END $$;

-- Schema access: proc (app data) + public (pg_trgm / unaccent extension funcs).
GRANT USAGE ON SCHEMA proc   TO app_runtime;
GRANT USAGE ON SCHEMA public TO app_runtime;

-- Data manipulation on everything currently in proc (tables, views, matviews,
-- sequences) + EXECUTE on its functions (proc.f_unaccent, resolved_*, canon_*).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA proc TO app_runtime;
GRANT USAGE, SELECT, UPDATE          ON ALL SEQUENCES IN SCHEMA proc TO app_runtime;
GRANT EXECUTE                        ON ALL FUNCTIONS IN SCHEMA proc TO app_runtime;

-- Same for objects future migrations create in proc. Default privileges are
-- keyed to the role that creates the objects; migrations run as the owner, so
-- these ALTER DEFAULT PRIVILEGES (owned by the current/owner role) cover them.
ALTER DEFAULT PRIVILEGES IN SCHEMA proc
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA proc
  GRANT USAGE, SELECT, UPDATE          ON SEQUENCES TO app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA proc
  GRANT EXECUTE                        ON FUNCTIONS TO app_runtime;

COMMIT;
