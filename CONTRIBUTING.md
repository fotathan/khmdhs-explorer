# Contributing

Thanks for your interest in improving the KHMDHS Explorer. This guide covers how
to get a working local copy, the project's conventions, and how to get your
changes reviewed and merged.

> **This is a public repository.** Anything you push — to this repo or to a fork —
> is public. Never commit secrets or database dumps (see [Never commit](#never-commit)).

## Contributing without write access (forks)

You don't need any permissions to contribute — fork, branch, and open a pull
request:

1. **Fork** the repo from the GitHub UI. You get `github.com/<you>/khmdhs-explorer`,
   a full copy under your account that you can push to freely.
2. **Branch** off `main` for your change (`git checkout -b my-feature`). Keep one
   logical change per branch/PR — it's much easier to review and merge.
3. **Open a Pull Request** from your branch back to this repo's `main`. The
   maintainer reviews and merges what fits.

Keep your fork current by pulling from upstream before starting new work:

```bash
git remote add upstream https://github.com/fotathan/khmdhs-explorer.git
git fetch upstream && git rebase upstream/main
```

## What this project is

A database + web app for Greek public procurement data (KHMDHS Opendata API,
enriched from Diavgeia and ΓΕΜΗ). Two halves:

- **Ingestion** (`db.py`, `khmdhs_ingest.py`, `ted_ingest.py`, `diavgeia_ingest.py`)
  — pulls acts into Postgres.
- **Web app** (`app/`) — FastAPI + Jinja2 + HTMX explorer. This is the deployed
  app. `frontend/` is a **legacy** React/Vite UI and is not wired into deploys —
  don't assume changes there are needed.

See [`CLAUDE.md`](CLAUDE.md) for a fuller architecture tour.

## Local setup

Requires Python 3.11+ and a local Postgres (a Docker container is fine).

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql://user:pass@localhost:5432/procurement"
python3 db.py init-schema          # apply schema.sql
python3 migrate.py up              # apply tracked migrations
uvicorn app.main:app --reload --port 8000
```

Local secrets (e.g. `GEMI_API_KEY`, `ANTHROPIC_API_KEY`) go in environment
variables, never in git. The optional `tesseract` + Greek language data
(`tesseract-ocr-ell`) enable the free local-OCR tier; the app self-disables it
gracefully if they're absent.

## Conventions (please follow these)

### Migrations — never hand-apply schema changes

Schema changes go through the migration tracker, not ad-hoc `psql`:

```bash
python3 migrate.py new "short_description"   # scaffolds a BEGIN;…COMMIT; file
                                             # and appends to migrations/manifest.txt
# edit the generated migrations/<timestamp>_short_description.sql
python3 migrate.py up                        # apply pending
python3 migrate.py status --strict           # verify all applied
```

Applied migrations are tracked in `proc.schema_migration`. If your change reads a
new column or table, the migration must be part of the same PR.

### Tests — ship them with every feature

There's a pytest suite and it should stay green. Add tests covering new behavior:

```bash
python3 -m pytest                 # full suite
python3 -m pytest tests/test_x.py # a single file
```

DB-backed tests use a snapshot test database and skip automatically without
`TEST_DATABASE_URL` set. If your change alters the schema, update the
hand-maintained schema snapshot at `tests/proc_schema.sql` so the test DB matches
(the suite's `test_migrations.py` enforces this).

### Code style

- Match the surrounding code — naming, comment density, and idioms. The codebase
  favors clear, self-documenting Python over cleverness.
- Server-rendered templates live in `app/templates/`; `beta_*.html` are the
  current default UI and `_*.html` are HTMX partial fragments.
- The UI is fully bilingual (Greek/English). New user-facing strings need both —
  see the i18n catalog (`app/i18n_catalog.py`) and existing `t(...)` usage.
- Keep `app/extractors.py`, `app/exporter.py`, and `app/ocr.py` byte-identical
  with their standalone "Tender Tables" sibling — put KHMDHS-specific logic in
  `app/tables.py` instead.

### Docs

If your change is user-facing, update the in-app help (`app/templates/beta_help.html`)
and add a `CHANGELOG.md` entry.

## Never commit

This repo is public and the app handles a shared credential and procurement data.
Do **not** commit:

- Secrets / connection strings — `DATABASE_URL`, `APP_PASSWORD` / `APP_USERNAME`,
  `ANTHROPIC_API_KEY`, `GEMI_API_KEY`, `ATTACH_S3_*`, any `.env` / `.env.*` file.
- Database dumps or exported procurement data.
- Local machine paths, personal tokens, or attachment files.

`.gitignore` already excludes `.env*` and common secret files — but check
`git status` and `git diff --staged` before your first push. Production
infrastructure is configured out-of-band (Render dashboard env vars,
`sync: false`), so nothing in this repo can reach production data.

## Opening a pull request

- Base your PR on `main`; describe **what** changed and **why**.
- Confirm `python3 -m pytest` passes and CI is green.
- Include any migration + its snapshot update, and note if the change is
  user-facing (help/CHANGELOG updated).
- Small, focused PRs merge fastest. If you're planning something large, open an
  issue first to align on the approach.

## Deployment (maintainer note)

Pushing to `main` auto-deploys to Render. Because the deployed code may read new
columns, **prod database migrations are applied before the dependent code is
pushed** — contributors don't do this, but it's why schema PRs are reviewed with
care.
