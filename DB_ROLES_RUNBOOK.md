# DB role split — prod rollout runbook

**Goal:** stop the running app + ingestion from connecting as the schema **owner**
(today the prod role owns `proc` and has `CREATEROLE`/`CREATEDB`/`BYPASSRLS` — it
can drop every table). Move them to a **DML-only** `app_runtime` role. Keep the
owner connection string only for migrations and DDL.

The grant migration
(`migrations/20260709201920_app_runtime_least_privilege_role_grants.sql`) and the
role model are **already proven locally**: the whole app (search, analytics,
admin writes, audit) and `db.py` ingestion run under `app_runtime` with zero
permission errors, and it is correctly **denied** `DROP TABLE`, `CREATE TABLE`,
and `CREATE ROLE`.

> Do this when you have a few minutes to watch prod, and ideally alongside the
> external review. It's easily reversible (one Render env change), but a
> misconfigured role/pooler can break prod **writes**, so verify before walking away.

## 1. Apply the grants to prod (as the owner)

```bash
# from the repo, with the OWNER connection string:
DATABASE_URL="$KHMDHS_PROD_DB_URL" python3 migrate.py up
```

This creates `app_runtime` **NOLOGIN** with the scoped grants and records the
migration. (Equivalently, run the .sql via `psql -f`.)

## 2. Give the role a login + password (out of band — never in git)

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # generate
```
Then, as the owner (Supabase SQL editor or psql):
```sql
ALTER ROLE app_runtime WITH LOGIN PASSWORD '<generated-secret>';
```

## 3. Build the app_runtime connection string & TEST it first

Same host/port/db as today, but user = `app_runtime`.

- **Supabase pooler caveat:** through Supavisor (port 6543) the username is
  usually `app_runtime.<project-ref>` (role, dot, project ref), like the current
  `postgres.<ref>`. Confirm the exact form from Supabase → Database → Connection
  pooler. If Supavisor rejects the custom role, use the **session pooler** or the
  direct connection for the app instead.
- **Test before switching anything:**
  ```bash
  DATABASE_URL="<app_runtime URL>" python3 -c "import psycopg,os; \
    c=psycopg.connect(os.environ['DATABASE_URL']); \
    print(c.execute('select current_user, session_user').fetchone())"
  ```
  It must connect and print `app_runtime`.

## 4. Switch the app over

- Render → the web service → **Environment** → set `DATABASE_URL` to the
  `app_runtime` URL. **Keep the owner URL** saved (it stays as `KHMDHS_PROD_DB_URL`
  in your shell for `migrate.py` / DDL).
- Redeploy / restart the service.

## 5. Verify

- `GET /version` → `"db":"ok"`.
- Log in as admin, do a real mutation (e.g. edit an act, launch a tiny job) → 200/303.
- `GET /admin/audit` shows the action.
- If you also run **ingestion** under `app_runtime`: a small `./ingest.sh prod catchup`
  should insert fine (DML). **Note:** `db.py init-schema`, `migrate.py`, and
  `REFRESH MATERIALIZED VIEW` still need the **owner** URL (DDL / matview ownership).

## Rollback

Set `DATABASE_URL` back to the owner URL in Render and restart. Nothing else to undo.

## Who uses which role afterwards

| Task | Role |
|------|------|
| Web app runtime | `app_runtime` |
| Routine ingestion (backfill/catchup/project) | `app_runtime` (DML) |
| Migrations (`migrate.py up`), `db.py init-schema`, matview refresh | **owner** (`KHMDHS_PROD_DB_URL`) |

## Optional next step (not done here)

A finer split — a **read-only** role for the public read paths vs a write role for
admin mutations — would need two pools inside the app (per-request read/write
selection across ~34 call sites). Deferred: high refactor cost, and for a single
trusted process behind auth + CSRF + audit the owner→DML drop above removes the
large majority of the risk.
