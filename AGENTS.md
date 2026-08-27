# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

A database + web app for Greek public procurement data, sourced from the
KHMDHS Opendata API (`cerpp.eprocurement.gov.gr`) and enriched from Diavgeia
(org directory) and ΓΕΜΗ (business registry). It has two halves:

1. **Ingestion** (`db.py`, `khmdhs_ingest.py`) — pulls acts from the KHMDHS API
   into Postgres.
2. **Web app** (`app/`) — a FastAPI + Jinja2 + HTMX explorer over that data.
   There is no separate JSON API layer: every HTML route also serves JSON if
   the client sends `Accept: application/json`.

Tests live in `tests/` (pytest, run in CI). They build a throwaway database
from `tests/proc_schema.sql` — a schema-only snapshot — so DB-backed tests need
`TEST_DATABASE_URL` (or `DATABASE_URL`) pointing at a database you don't mind
losing, plus `psql` on PATH; they skip without one. Adding a table means
regenerating that snapshot. The root-level `test_*.sql` files are unrelated —
ad-hoc scratch queries, not a test framework, so don't treat them as such.

`LOCAL_RUNBOOK.md` covers running locally with every feature enabled.

## Commands

```bash
pip install -r requirements.txt

# One-time schema setup (applies schema.sql)
python3 db.py init-schema

# Run the web app locally
export DATABASE_URL="postgresql://user:pass@host:5432/procurement"
uvicorn app.main:app --reload --port 8000

# Ingestion (direct, no safety prompts)
python3 db.py backfill --start 2023-01-01 --end 2024-12-31
python3 db.py catchup            # incremental, per-type watermark + overlap
python3 db.py fulltext-backfill  # backfill attachment full text for existing acts
python3 db.py stats              # row counts
python3 db.py progress [--errors-only]

# Digest emails — nothing leaves the machine with the default console backend
python3 cron_digests.py                       # send whatever is due, then exit
DIGEST_DRY_RUN=1 python3 cron_digests.py      # show what would be sent

# Tests
export TEST_DATABASE_URL="postgresql://user:pass@host:5432/khmdhs_test"
pytest -q

# Ingestion (guarded — confirms target DB, masks credentials, requires typing
# "PRODUCTION" for prod). Prefer this over calling db.py directly.
./ingest.sh local backfill --start 2026-06-01 --end 2026-06-19 --types notice
./ingest.sh prod   catchup  --types notice contract
```

Legacy React frontend (see "Two UIs" below) — not the primary app:
```bash
npm run dev    # installs + runs frontend/ via Vite
npm run build
```

## Architecture

### Data model (`schema.sql`)

Single Postgres schema `proc`. **ADAM** (`referenceNumber`) is the universal
natural key across all five KHMDHS act types: `request → notice → auction
(award) → contract → payment`. `act_link` is one edge table capturing that
whole graph, populated from `*RefNo[]` array fields and single-ADAM pointer
fields (see `LINK_FIELDS` / `SINGLE_LINK_FIELDS` in `khmdhs_ingest.py` — the
link vocabulary differs per type, confirmed against live API probes).

KHMDHS enumerations arrive as `{key, value}` pairs; the table stores the
`key` and resolves display labels from `code_list` (or a few hardcoded dicts
in `app/main.py` like `TYPE_LABELS`, `CONTRACT_TYPES`), so labels stay
consistent even when the API's wording drifts between endpoints.

Beyond `schema.sql`, the database evolves through many standalone
`*_migration.sql` files at the repo root (e.g. `analytics_exclusion_migration.sql`,
`merge_migration.sql`, `procedure_family_migration.sql`) — there's no
migration framework or version table; apply them by hand, in the order
implied by what they depend on. Several maintain materialized views
(`proc.mv_analytics_*`, used by `/analytics`) that must be refreshed after
data changes — the `REFRESH MATERIALIZED VIEW` statements live at the bottom
of the relevant migration file.

### Ingestion (`db.py`, `khmdhs_ingest.py`)

`db.py` is a thin DB layer (psycopg3 preferred, psycopg2 fallback) exposing
exactly the `execute` / `execute_returning` / `commit` surface that
`khmdhs_ingest.Repository` needs, plus the CLI (`init-schema`, `backfill`,
`catchup`, `fulltext-backfill`, `stats`, `progress`). `khmdhs_ingest.py` owns
all KHMDHS API + mapping logic (rate limiting at 350 req/min, ≤180-day search
windows since the API silently clamps wider ranges, link-graph extraction).
Swapping the DB layer should never require touching the ingestion logic, and
vice versa.

`catchup` derives its start date per act-type from a watermark (`max(date_to)`
of `status='done'` windows in `proc.ingest_window`), minus an overlap buffer
for late/backdated records — it has no notion of "fetch everything," so a
type with no prior backfill needs an explicit `--start`.

### Web app (`app/`)

- `app/main.py` — the FastAPI app: search/explore/detail pages for acts,
  authorities, and contractors; full-text search; analytics. Owns the shared
  `cursor()` context manager (one autocommit connection, `prepare_threshold=None`
  because Supabase's pooler can route consecutive queries to different
  physical connections — disabling prepared statements keeps it pooler-safe)
  and `AuthMiddleware`, which resolves the session to a live account on every
  request and enforces the role gate.
- `app/auth.py` — real accounts in `proc.app_user`, not a shared password.
  Three tiers: anonymous (public teaser), customer (full read), admin (adds
  `/admin`). Passwords are stdlib scrypt; sessions are a signed cookie
  (Starlette `SessionMiddleware`) carrying a `session_version` that the
  middleware re-checks against the DB, so a password/role/2FA change kills
  existing sessions immediately. Optional TOTP 2FA with hashed recovery codes.
  `app/login_links.py` adds passwordless sign-in links mailed to the account
  address — a second path onto `/login` that completes the PASSWORD step only,
  so 2FA still applies.
- Admin surfaces are gated by `_is_admin_path` + `AuthMiddleware` (role check
  plus an audit row in `proc.admin_action`), not by a path prefix alone:
  `/admin`, `/help`, `/tables` (except `/tables/public`), and the inline
  mutating routes that live under the public trees (`/name-edit`,
  `/name-cancel`, `/gemi-refresh`).
- `app/admin.py` — mounted at `/admin`: launches backfills as detached
  subprocesses (`db.py backfill ...`) tracked in `proc.ingest_job`, so the web
  request returns immediately; survives uvicorn restarts since jobs aren't
  in-process. Only one backfill runs at a time.
- `app/tables.py` — mounted at `/tables`: tender-document table extraction
  (Excel export from a tender's attachments, fetched by ΑΔΑΜ or uploaded
  directly). `app/extractors.py`, `app/exporter.py`, and `app/ocr.py` are
  kept **byte-identical** with a standalone "Tender Tables" sibling tool —
  don't introduce KHMDHS-specific logic into those three files; anything
  KHMDHS-aware belongs in `app/tables.py` itself. OCR (`app/ocr.py`, scanned
  PDFs/images via the Claude API) is opt-in per file and gated separately on
  `ANTHROPIC_API_KEY` being present.
- `app/mailer.py` + `app/digests.py` — scheduled "new results" emails.
  `mailer` is the only place the app sends mail from:
  `EMAIL_BACKEND=console|memory|file|smtp`, console by default so nothing leaves
  the machine until SMTP is configured. `digests` owns a subscription =
  (customer × search profile): on a schedule it replays that profile's filters
  over acts whose `ingested_at` falls in `(last_cursor, now]` and mails what is
  new. The schedule falls back `subscription.schedule_id` → the `is_default` row
  of `proc.digest_schedule`. Admin UI at `/admin/digests`; wording is the
  `digest` slug in `proc.email_template`. Fired by `cron_digests.py` or by
  `DIGEST_SCHEDULER=1` in-process — never both. **Deliverability is not
  implemented.**
- `app/gemi_client.py` (shared) + root `gemi_enrich.py` (standalone backfill
  CLI) — ΓΕΜΗ business-registry enrichment by ΑΦΜ, used both on-demand (admin
  button on contractor/authority pages) and offline. Keep parsing/upsert
  logic in `gemi_client.py` so both paths stay identical.
- Templates (`app/templates/`) are server-rendered Jinja2 + HTMX partials
  (`_*.html` are partial fragments returned to HTMX swaps, not full pages).
  The `beta_*.html` templates are the **current, default** UI (promoted from
  a redesign); plain-named templates like `index.html`/`explore.html` are
  the pre-redesign ones, several still referenced as fallbacks — check
  `app/main.py` route bodies before assuming a given template is dead.

### Two UIs — don't confuse them

`frontend/` is a separate React + Vite + Supabase-JS app (`Procurement
Explorer`) that queries Supabase directly from the browser using
`VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY`. It predates the Jinja/HTMX
redesign and is not wired into `Dockerfile`/`render.yaml` — the deployed app
is `app/main.py` under uvicorn. Treat `frontend/` as legacy unless told
otherwise; don't assume changes to `app/templates/` need a corresponding
`frontend/src` change, or vice versa.

### Deployment

`Dockerfile` runs `uvicorn app.main:app` on `$PORT`, reading `DATABASE_URL` and
`SECRET_KEY` (which signs the session cookies — without it the app falls back
to an insecure built-in key and sessions are forgeable) from the environment at
runtime — no secrets baked into the image. `render.yaml` deploys it as a Render
Blueprint with both vars marked `sync: false` (set in the dashboard, not in
git). The Blueprint also declares a worker and two cron services; they exist
only once the Blueprint is applied, and are not created by pushing.
