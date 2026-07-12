# Backup & restore runbook

Free, manual disaster-recovery for the KHMDHS app data until Supabase Pro
managed backups / PITR are enabled. The point is not just to *have* a dump — it
is to **know a restore actually works**. The procedure below was drilled
end-to-end (dump prod → restore into a throwaway Postgres 17 cluster → verify row
counts match) and all key tables matched exactly.

## What this covers (and what it doesn't)

- **Covered:** the application's own data — the entire `proc` schema (acts, links,
  authorities, contractors, users, subscriptions, CRM, TED/Diavgeia, analytics
  matviews, ingest bookkeeping). `backup.sh` dumps exactly this.
- **NOT covered here:** Supabase-managed pieces (its `auth`/`storage` schemas,
  project config). We don't use `auth`/`storage` for the app, so `proc` is the
  recoverable asset. Full-instance recovery is what **Supabase Pro backups / PITR**
  are for — enable those when you upgrade.
- Act attachments (if ever enabled) live in **object storage**, not the DB, and
  are backed up separately by the bucket's own versioning/replication.

## Prerequisites

- A **`pg_dump` whose major version ≥ the server's**. Prod is **Postgres 17**, so
  use a v17 client. On macOS: `brew install postgresql@17` →
  `/usr/local/opt/postgresql@17/bin/pg_dump`. (A v14/16 client refuses to dump a
  v17 server.)
- The **session** connection string, i.e. Supabase's session pooler / direct on
  **port 5432** — *not* the transaction pooler on 6543 (pg_dump needs session
  features). It's the same URL as `DATABASE_URL` with the port swapped to `5432`.

## Back up

```bash
# Session URL = the pooler URL with :6543 → :5432
PG_DUMP=/usr/local/opt/postgresql@17/bin/pg_dump \
  ./backup.sh "postgresql://USER:PW@aws-0-<region>.pooler.supabase.com:5432/postgres"
```

- Writes a compressed, custom-format archive to `backups/khmdhs-proc-<UTC>.dump`
  (~15 MB today). `backups/` is git-ignored — **dumps hold personal data; keep
  them private and off git.**
- Keeps the newest **7** dumps (override with `BACKUP_KEEP`). Custom format lets
  you restore selectively and is compressed.

Every run automatically:

- **Integrity-checks** the fresh archive with `pg_restore --list`. A truncated or
  corrupt dump fails *at creation* (and is deleted) instead of surprising you
  mid-restore. A failed `pg_dump` also cleans up its partial file.
- Writes a **SHA-256 sidecar** (`<dump>.sha256`) so bit-rot or tampering is
  detectable later.

### Verify an existing archive

```bash
./backup.sh --verify backups/khmdhs-proc-<UTC>.dump      # checksum + structure
./backup.sh --verify backups/khmdhs-proc-<UTC>.dump.gpg  # checksum (decrypt for structure)
```

### Encrypt at rest (optional but recommended off-machine)

Dumps contain PII (user emails, password hashes, CRM notes). Set one of these and
the plaintext `.dump` is replaced by an encrypted `.dump.gpg`:

```bash
BACKUP_GPG_RECIPIENT="you@example.com" ./backup.sh "<session-url>"   # public-key
BACKUP_GPG_PASSPHRASE="$(cat ~/.khmdhs_backup_pass)" ./backup.sh ... # symmetric (AES256)
```

Restore an encrypted dump by decrypting first: `gpg -o out.dump -d in.dump.gpg`.

**Cadence & scheduling:** run it on a schedule you're comfortable losing data back
to — weekly is a sane floor for a pilot, daily if acts are being curated often.
Store at least one copy off your laptop (an encrypted drive or a private bucket).
A host `cron` entry (macOS/Linux) that keeps 14 encrypted dumps:

```cron
# 03:15 UTC daily — encrypted prod backup, keep 14
15 3 * * *  PG_DUMP=/usr/local/opt/postgresql@17/bin/pg_dump \
  BACKUP_KEEP=14 BACKUP_GPG_PASSPHRASE="$(cat $HOME/.khmdhs_backup_pass)" \
  DATABASE_URL="postgresql://USER:PW@HOST:5432/postgres" \
  /path/to/KHMDHS/backup.sh >> $HOME/khmdhs-backup.log 2>&1
```

(Render's disk is ephemeral, so schedule this on a machine that persists — your
laptop/NAS, or a tiny always-on box — and push a copy to a private bucket.)

## Restore (into a fresh Postgres 17)

The restore target must be **Postgres 17** (a v17 dump won't cleanly load into
v16 — it emits v17-only settings). To restore into a new database:

```bash
createdb -h HOST -p 5432 -U postgres restore_target

# Pre-create the schemas + extensions proc depends on, so trgm indexes and any
# pgcrypto/uuid-ossp defaults resolve during the load:
psql -h HOST -p 5432 -U postgres -d restore_target <<'SQL'
CREATE SCHEMA IF NOT EXISTS proc;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm     SCHEMA proc;
CREATE EXTENSION IF NOT EXISTS unaccent    SCHEMA proc;
CREATE EXTENSION IF NOT EXISTS pgcrypto    SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" SCHEMA extensions;
SQL

pg_restore --no-owner --no-privileges \
  -h HOST -p 5432 -U postgres -d restore_target \
  backups/khmdhs-proc-<UTC>.dump
```

- `--no-owner --no-privileges` skips the prod role/grant lines so the load doesn't
  depend on `app_runtime`/`postgres` existing on the target. Re-apply the grants
  afterward with `migrations/…_app_runtime_least_privilege_role_grants.sql` if the
  target will run the app.
- The one expected, benign message is `schema "proc" already exists` (we
  pre-created it). Any *other* error is real.
- **Restoring back into Supabase** (real recovery): create a fresh Supabase
  project (Postgres 17), then run the same `pg_restore` against its session-pooler
  URL. `proc` already exists there empty on a new project, so the same benign
  notice applies.

## The drill (prove restore works — do this periodically)

1. `brew install postgresql@17` (once).
2. Run `backup.sh` to produce a fresh dump.
3. Spin up a throwaway v17 cluster, restore into it, and compare row counts to
   prod. A ready-made script did exactly this and reported
   `RESTORE VERIFIED — all row counts match prod`:
   - `initdb` a scratch datadir → start on a spare port (set `LC_ALL` to a valid
     locale, e.g. `en_US.UTF-8`, or the macOS postmaster aborts).
   - `createdb` + pre-create extensions (above) + `pg_restore`.
   - `SELECT count(*)` on `procurement_act`, `act_link`, `authority`, `app_user`,
     `ted_notice`, `diavgeia_decision` and compare to prod.
   - Stop + delete the scratch cluster.

Last drill: **all 6 key tables matched exactly**; 64 tables, 167 indexes (3 trgm),
11 matviews restored; 0 non-benign errors.

## When you upgrade (Supabase Pro)

Enable **daily managed backups + PITR** in the Supabase dashboard. Keep running an
occasional `backup.sh` + drill anyway — a backup you've never restored is a
hypothesis, not a backup.
