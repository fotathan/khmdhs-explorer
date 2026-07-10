# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A database + web app for Greek public procurement data, sourced from the
KHMDHS Opendata API (`cerpp.eprocurement.gov.gr`) and enriched from Diavgeia
(org directory) and ΓΕΜΗ (business registry). It has two halves:

1. **Ingestion** (`db.py`, `khmdhs_ingest.py`) — pulls acts from the KHMDHS API
   into Postgres.
2. **Web app** (`app/`) — a FastAPI + Jinja2 + HTMX explorer over that data.
   There is no separate JSON API layer: every HTML route also serves JSON if
   the client sends `Accept: application/json`.

There is no automated test suite. The root-level `test_*.sql` files are
ad-hoc scratch queries, not a test framework — don't treat them as such.

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
  and the `BasicAuthMiddleware` (single shared password via `APP_PASSWORD`/
  `APP_USERNAME`; a no-op if `APP_PASSWORD` is unset, e.g. local dev).
  `/admin` and `/tables` sit behind this same middleware — there's no
  separate auth layer for them.
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

`Dockerfile` runs `uvicorn app.main:app` on `$PORT`, reading `DATABASE_URL`
and the optional `APP_PASSWORD`/`APP_USERNAME` auth gate from the
environment at runtime — no secrets baked into the image. `render.yaml`
deploys it as a Render Blueprint with those two vars marked `sync: false`
(set in the dashboard, not in git).

For putting act attachments on S3-compatible object storage
(`ATTACHMENTS_BACKEND=s3`) instead of the ephemeral local disk, see
`OBJECT_STORAGE_RUNBOOK.md` at the repo root (bucket + `ATTACH_S3_*` env,
plus creating `proc.act_attachment` on prod first).
