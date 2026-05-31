# Deploying KHMDHS Explorer (private, free-tier to start)

This puts your app online behind a password, at **€0/month** to begin with,
using **Supabase** (database) + **Render** (web app). Ingestion/backfills stay
on your laptop — only the web app and database live online.

```
  your laptop  ──(backfills, write)──►  Supabase Postgres  ◄──(reads)──  Render web app  ──►  you + a few people (HTTPS + password)
```

---

## 1 · Database on Supabase

1. Create a project at supabase.com (free tier). Pick a region near you (eu-central / Frankfurt is closest to Greece).
2. Once it's ready: **Project Settings → Database → Connection string → URI**.
   - Copy the **Connection pooler** URI (port **6543**) — best for a web app.
   - It looks like: `postgresql://postgres.xxxx:[PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres`
3. Apply your schema + migrations to Supabase. From your laptop, point the
   tools at the Supabase URL and run them in order:

   ```bash
   export SUPA="postgresql://postgres.xxxx:[PASSWORD]@...pooler.supabase.com:6543/postgres"
   psql "$SUPA" -f schema.sql
   psql "$SUPA" -f app_indexes.sql
   psql "$SUPA" -f admin_migration.sql
   psql "$SUPA" -f annotations_migration.sql
   psql "$SUPA" -f units_migration.sql
   psql "$SUPA" -f merge_migration.sql
   psql "$SUPA" -f perf_indexes_migration.sql
   ```
   (Supabase has `pg_trgm` and `unaccent` available; the migrations enable them.)

4. Load a **subset** of data to stay under the 500 MB free limit. Point your
   ingester at Supabase and backfill a few months, e.g.:

   ```bash
   export DATABASE_URL="$SUPA"
   python3 db.py backfill --start 2024-01-01 --end 2024-03-31 --types notice contract
   python3 load_cpvs.py cpvs.csv
   python3 load_units.py UNECE_Rec20_EL.csv
   ```
   Check size in Supabase: **Database → Usage**. If you approach 500 MB, load
   less, or drop `raw_json` from the hosted copy (see note at the bottom).

---

## 2 · Code on GitHub

```bash
cd /Users/fotathan/PythonApps/KHMDHS
git init
git add .
git commit -m "KHMDHS Explorer"
# create an EMPTY repo on github.com, then:
git remote add origin https://github.com/<you>/khmdhs-explorer.git
git push -u origin main
```

The `.gitignore` keeps your venv, local logs, and any `.env` out of the repo.
**Double-check** `git status` shows no `.env` or password before pushing.

---

## 3 · Web app on Render

1. render.com → **New → Blueprint** → connect your GitHub repo. Render reads
   `render.yaml` and proposes the service. Approve it.
   (Or **New → Web Service → Docker** if you prefer manual setup.)
2. After it builds, go to the service's **Environment** tab and set the two
   secrets:
   - `DATABASE_URL` = your Supabase pooler URI (the `$SUPA` value above)
   - `APP_PASSWORD` = whatever shared password you choose
   (`APP_USERNAME` defaults to `team`; change if you like.)
3. Render redeploys with the env vars. When it's live you get a URL like
   `https://khmdhs-explorer.onrender.com`.
4. Open it — the browser will prompt for username (`team`) and your password.
   That's it: a private, HTTPS, password-protected site.

**Free-tier caveat:** the app **sleeps after ~15 min idle** and takes ~30s to
wake on the next visit. Fine for occasional use. To make it always-on, change
`plan: free` → `plan: starter` ($7/mo) in `render.yaml` (or in the dashboard).

---

## 4 · Day-to-day

- **Browsing:** just visit the URL, log in once per browser session.
- **New data:** run backfills from your laptop against `$SUPA` exactly as you
  do locally. The live site reads the same database, so new data appears
  immediately (restart isn't needed for data — only for code changes).
- **Code changes:** `git push` → Render auto-deploys.
- **Lookup cache:** the filter dropdowns cache in memory at first request. After
  a big backfill, the live app picks up new authorities/types on its next
  wake/restart (free tier sleeps anyway, so this resolves itself).

---

## When you outgrow free

- **Full dataset:** Supabase **Pro** ($25/mo, 8 GB) holds everything. Just keep
  using the same `DATABASE_URL` — no code change.
- **Always-on app:** Render **Starter** ($7/mo), no sleep.
- **Moving off entirely:** because the app is a plain Docker container reading
  `DATABASE_URL`, it runs unchanged on Fly.io, Railway, a VPS, etc. Nothing is
  locked to Render or Supabase.

## Optional: shrink the hosted DB (stay free longer)

Your `procurement_act.raw_json` column is the bulk of the size. If you want the
full structured dataset online but not the raw blobs, you can null them out in
the **hosted** copy only (keep your laptop's full copy intact):

```sql
UPDATE proc.procurement_act SET raw_json = NULL;
VACUUM FULL proc.procurement_act;
```
The app doesn't depend on `raw_json` for any page, so everything keeps working.
