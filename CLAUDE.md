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

## Passwordless sign-in links
"Email me a sign-in link" on /login — a SECOND path in, never a replacement.
app/login_links.py owns it; the routes live next to /login in main.py.
- The link completes the PASSWORD step only. 2FA still runs (same mfa_pending
  state), must_change_password still walls the session off. Never change that.
- Token: 32 random bytes, mailed once, stored as sha256 in proc.login_link.
  Single use (consume = one atomic UPDATE ... WHERE used_at IS NULL), 15 min
  (LOGIN_LINK_TTL_SECONDS). Issuing a new one burns the old one; so do
  set_password and set_email (auth.kill_login_links).
- The mailed URL does NOT sign in on GET — mail scanners fetch it and would
  burn the token. GET renders an interstitial; its POST spends it.
- POST /login/link answers IDENTICALLY for known, unknown and deactivated
  addresses. Nothing may leak who has an account.
- Rate limit: proc.login_throttle, counting EVERY request (it sends mail), key
  "loginlink:<email>|<ip>". Never reset on success. A locked-out customer still
  has their password — that is why this ships alongside.
- Wording: proc.email_template slug 'login_link' (el/en), resolved through
  digests._soft_resolve. The URL is placed by email_login_link.html, never by
  the admin-editable fragment.
- A completed link login stamps app_user.email_verified_at — the only proof in
  the system that an address is real (registration never confirmed it).
- LOGIN_LINKS_ENABLED=0 removes the routes and the link on /login. The
  switch fails towards OFF: 0/false/no/off/n/f/disabled (any case) all
  disable it. Don't narrow that back to == "0" — a dashboard-typed "false"
  silently leaving the feature on is how it went out live once already.

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
