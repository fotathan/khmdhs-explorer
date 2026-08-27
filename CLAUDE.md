# KHMDHS Explorer — Claude Code Guide

Greek procurement platform. FastAPI/HTMX/Jinja2/PostgreSQL. Render + Supabase. ~2.7M acts in proc.procurement_act.

## Me
Domain expert, not a developer. Write all code yourself. Give numbered steps. Complete files, not diffs. Diagnose root cause — don't guess iteratively.

## Hard rules
- DB host: 127.0.0.1 (never localhost). Local port 5433.
- uvicorn: no --reload
- Migrations: run on BOTH local and Supabase before any dependent code push
- CREATE INDEX CONCURRENTLY needs direct port 5432 (not the pooler)
- After any CSS edit: grep for </style> to verify

## Never break (main.py wirings)
- TABLES_ENABLED
- full_text detail columns
- reltuples counter fix (pg_class.reltuples)
- root-anchored WITH RECURSIVE chain query

## Companion
Tender Tables shares 3 files byte-identical: extractors.py, exporter.py, ocr.py

## Email alerts (digests)
Scheduled result emails, one subscription per customer × search profile.
- Per-customer settings live on /admin/crm/<uid> (saved searches + alerts).
  /admin/digests holds ONLY the schedules, a read-only overview and the history.
- Recipients: active testers/subscribers only (auth.ENTITLED_STATUSES) — admins
  too, since they have access without a grant. Gated in active_subscriptions AND
  again in run_subscription (records status='skipped').
- Schedule falls back: subscription.schedule_id → the is_default row of
  proc.digest_schedule.
- Window is procurement_act.ingested_at, half-open (last_cursor, now].
  last_cursor moves ONLY when an email actually went out — an empty/failed/
  skipped run leaves the window for the next email.
- Every send writes its matched acts to proc.digest_run_item (the WHOLE window,
  capped at DIGEST_ITEM_CAP=2000; in_email marks the ones the message listed)
  plus an unguessable digest_run.token. The email's "see all results" opens
  /digests/<token>, which needs login + ownership (admins may also read).
- app/mailer.py is the ONLY place mail is sent. EMAIL_BACKEND defaults to
  console — nothing leaves the machine until SMTP is configured.
- Fired by cron_digests.py OR DIGEST_SCHEDULER=1 in-process. Never both.
- Deliverability (SPF/DKIM/DMARC, unsubscribe) NOT done. Not for real customers yet.

## Tests
pytest in tests/, runs in CI. Needs TEST_DATABASE_URL (throwaway DB) + psql.
Schema comes from tests/proc_schema.sql — regenerate it when you add a table.
Ship tests with every feature.

## Local dev
LOCAL_RUNBOOK.md — how to run with every feature switched on, and what each
switch needs.
