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
- Recipients per subscription: the account address (unless include_primary is
  off) PLUS proc.digest_recipient rows, each with its own salutation/first/last
  name. One run = one message per recipient; the intro is re-resolved per
  person, so [[salutation]]/[[first_name]]/[[full_name]] greet the reader.
  Sent as soon as ONE message left (the window must not be mailed twice);
  failed addresses go into digest_run.error, the count into n_recipients.
- Two bodies, chosen by subscription.layout: 'list' (email_digest.html) prints
  the acts, 'summary' (email_digest_summary.html) prints window_stats() —
  per-type counts, value, authorities, deadlines — and links out. Wording per
  layout: email_template slugs 'digest' / 'digest_summary'.
- Digest bodies resolve [[fields]] through digests._soft_resolve, NOT
  email_builder.resolve_fields: an empty optional token drops out instead of
  failing the send (no human in the loop).
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

## CRM customer card
/admin/crm/<uid> is tabbed (Details / Alerts / Activity / Compose email) with an
always-visible "at a glance" strip above. The tabs are progressive enhancement:
the script adds `js-tabs` to <html>, and without it every panel renders stacked.
The open tab survives a POST redirect via ?tab= (what the alert forms set),
then #hash, then sessionStorage.

## Tests
pytest in tests/, runs in CI. Needs TEST_DATABASE_URL (throwaway DB) + psql.
Schema comes from tests/proc_schema.sql — regenerate it when you add a table.
Ship tests with every feature.

## Local dev
LOCAL_RUNBOOK.md — how to run with every feature switched on, and what each
switch needs.
