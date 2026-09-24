# KHMDHS Explorer — Claude Code Guide

Greek procurement platform. FastAPI/HTMX/Jinja2/PostgreSQL. Render + Supabase. ~2.7M acts in proc.procurement_act.

## Me
Domain expert, not a developer. Write all code yourself. Give numbered steps. Complete files, not diffs. Diagnose root cause — don't guess iteratively.

## Hard rules
- DB host: 127.0.0.1 (never localhost). Local port 5433.
- uvicorn: no --reload
- Migrations: run on BOTH local and Supabase before any dependent code push
- Prod takes migrations with `migrate.py up --only <file>…`, never a bare `up`:
  the Tender Service files (…090000, …090100, …120000, …130324) stay PENDING on
  prod on purpose until that source goes live. `status` showing exactly those
  four pending on prod is the healthy state.
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
- Customers manage their OWN on /account/searches (app/account_searches.py):
  save the current filters, rename/delete, and turn an alert on with its
  format/cadence/language/count/marks. _owned() is the only door: rename and
  delete need a profile they OWN, the alert endpoints only one they may APPLY
  (so a published portal profile is subscribable, an unpublished one is a 404).
  Extra recipients stay admin-only on purpose — self-serve "mail these
  addresses too" sends our mail to people who never asked, and deliverability
  is not done. Same for portal profiles and the schedules themselves.
- Recipients per subscription: the account address (unless include_primary is
  off) PLUS proc.digest_recipient rows, each with its own salutation/first/last
  name. One run = one message per recipient; the intro is re-resolved per
  person, so [[salutation]]/[[first_name]]/[[full_name]] greet the reader.
  Sent as soon as ONE message left (the window must not be mailed twice);
  failed addresses go into digest_run.error, the count into n_recipients.
- Three bodies, chosen by subscription.layout: 'list' (email_digest.html)
  prints the acts, 'summary' (email_digest_summary.html) prints window_stats()
  — per-type counts, value, authorities, deadlines — and links out,
  'deadline' (email_digest_deadline.html) looks FORWARD instead. Wording per
  layout: email_template slugs 'digest' / 'digest_summary' / 'digest_deadline'.
- The deadline body ignores the ingest cursor entirely — it cannot use one, as
  "closes within 7 days" slides with the wall clock and would re-list the same
  act every morning. subscription.lead_days holds the reminder marks (default
  {7,1}); each fires at most once per act, recorded in
  proc.digest_deadline_notice, written ONLY when a message actually left. The
  stored row carries the deadline it was sent for, so a moved closing date
  re-arms every mark. Cancelled acts are never chased, and a deadline run must
  never advance last_cursor.
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

## First-login wizard (/welcome)
app/onboarding.py, spec docs/specs/onboarding-wizard.md. /register asks "have
you bid before?" (+ optional ΑΦΜ on yes); the wizard turns the answers into up
to three ORDINARY saved searches (subject / keywords / awards).
- **The ΑΦΜ is a claim.** It lives only in proc.onboarding.declared_afm — never
  customer_profile.vat_number/tax_number/operator_id, never a company_profile.
  The CRM card offers it as a one-click link through company_match's link
  route; nothing links on its own. Test-enforced.
- Sign-up never looks anything up (a slow registry must not block an account)
  and never says an ΑΦΜ is taken.
- Sign-up is ONE transaction (user + test grant + profile + wizard row). The
  pool is autocommit: without it a failed wizard INSERT (prod RLS on
  proc.onboarding, 2026-09-22) left a live half-made account. Test-enforced.
  Never tick Supabase's "Run and enable RLS" for a proc table — no policies =
  every app INSERT refused.
- Suggestions come from fit.ledger_summary — READ-ONLY, sharing
  fit._AWARD_AGG_SQL with seed_from_ledger; a test pins that they agree.
- Keywords are their own search, in `q` (title + text), quoted and joined with
  "or". Never AND them onto the CPV search: build_where would then need both.
- The ledger value band is shown as a HINT, never pre-filled as a filter.
- customer_profile.tender_experience: NULL = never asked, not "no".
  company_match must never write it.
- ONBOARDING_ENABLED fails towards off (same words as LOGIN_LINKS_ENABLED),
  checked per request.

## CRM customer card
/admin/crm/<uid> is tabbed (Details / Alerts / Activity / Compose email) with an
always-visible "at a glance" strip above. The tabs are progressive enhancement:
the script adds `js-tabs` to <html>, and without it every panel renders stacked.
The open tab survives a POST redirect via ?tab= (what the alert forms set),
then #hash, then sessionStorage.

## ΓΕΜΗ company match (CRM card, Στοιχεία tab)
app/company_match.py — which registry company a customer IS, when they gave us
no ΑΦΜ at registration. Admin-only, three routes under /admin/crm/<uid>/
company-match/ (search / link / unlink), one HTMX panel.
- **The registry has no domain search.** Measured: `afm` and `name` filter;
  `email`, `url`, `city`, `companyName`, `coNameEl` are IGNORED — and an
  ignored parameter is not an error, it returns the WHOLE register
  (totalCount ≈ 1.69M) as if it were a result. `gemi_client.REGISTRY_GUARD`
  rejects any response that size. Never delete that check or the test on it.
- So the customer's email domain never queries anything; it CONFIRMS, against
  the candidate's own registry email (76% coverage), after the freemail list.
- The registry's ranking is not trustworthy (searching ΕΛΛΗΝΙΚΑ ΠΕΤΡΕΛΑΙΑ
  returns the exact match SECOND) and its pool is capped at ~20 rows that no
  parameter narrows. We re-rank locally; the free-text query box is how an
  admin reaches a company that fell outside the pool.
- The name score compares against what was SEARCHED (the typed box), and only
  falls back to the profile's company when nothing was typed. A candidate name
  that is a fragment OF the query ('B.T.Prime' → 'prime' in 'food prime') is
  capped at the share it covers; only the query inside a longer name gets the
  0.82 containment floor. Both once put B.T.Prime above FOOD PRIME (tested).
- Name similarity is scaled by WORD coverage of the query (0.5 + 0.5×coverage):
  letters alone scored BT PRIME 67% like 'food prime'. The ledger bonus is a
  tie-breaker (W_LEDGER 0.05 vs W_NAME 0.50) — at 0.10 it outranked a better
  name. Don't raise it back.
- Candidates = our contractor ledger (trigram) + the registry, merged on ΑΦΜ.
  The ledger SQL must spell the fold as `translate(proc.f_unaccent(lower(x)),
  'ς','σ')` — the nesting order of `ix_eo_name_trgm`. `leads._fold_sql` builds
  the other order and would sequential-scan 143k rows. pg_trgm lives in
  `public` locally and `proc` in tests, so its functions and operators are
  schema-qualified from `_trgm_schema()`.
- **Never auto-link**, at any score. The ΑΦΜ decides the fit score, the ledger
  link and eventually an invoice.
- Linking posts ONE field: the ΑΦΜ. Company data is rebuilt server-side from
  the registry + ledger and re-scored — a form field is not a source for
  something written into a customer record (test-enforced).
- Identifiers are WRITTEN (a different existing ΑΦΜ needs confirm=1);
  everything else is fill-only-if-empty through `leads.fill_if_empty`, the
  same helper the lead importer uses. Never touched: full_name, crm_stage,
  service, manager_id, lead_source, about, is_recipient — and app_user.email,
  because auth.set_email kills verification and sign-in links.
- `proc.customer_company_match.filled` is column → the value we wrote; unlink
  reverts a column only while it still holds exactly that, so an admin's later
  edit always survives. A confirmed ΑΦΜ replacement is NOT recorded there.
- ΓΕΜΗ `persons[]` is never imported: sole traders are natural persons.

## Fit scoring (Tier 2, slice 1)
app/fit.py — "is this tender worth bidding for", per customer. Admin-only,
on the CRM card's Ταίριασμα tab. Deterministic arithmetic, NO model.
- **The isolation rule.** proc.act_ai_summary is one row per act served to
  everyone (ai_summary.py §3) and /ai promises the summary cannot see
  customer data. fit.py must never touch it and ai_summary.py must never
  read a profile — both directions are test-enforced (test_fit.py).
- The profile is DERIVED from the award ledger by ΑΦΜ (seed_from_ledger),
  not from a form. Declared rows survive a re-seed: that is why `source` is
  in the primary key of company_profile_cpv/_nuts.
- Score = CPV .45 / value .20 / geo .20 / buyer .15, and a tender whose CPVs
  the firm has never touched is CAPPED at CPV_FLOOR however good the rest is.
  Weights are argued, not fitted — there is no win/loss data yet.
- CPV weights normalise WITHIN each prefix depth. Globally normalising makes
  every 8-digit code a fraction of its division, so the most specific match
  scores lowest; that bug shipped once and is caught by a test now.
- Real CPVs carry a check digit ('33184100-4', 10 chars) and the seed stores
  them that way. The EXACT level matches on the code before the '-'
  (fit._cpv_base), on both sides; 4/2-digit levels are plain prefixes. It once
  looked up code[:8] instead, so exact matches silently scored as group
  matches (0.7 not 1.0) — fixing it raised live scores. An exact match only
  ADDS: per code it is max(exact, deeper of group/sector), never "deepest
  wins", or a code won once pre-empts the firm's core group and scores DROP.
  Test with real-format codes, not bare 8 digits.
- Components are always shown, never just the total. `why` is a fixed Greek
  phrase (a translation key) and the variable part rides in `detail`.
- **Customers see it** (app/account_fit.py): /account/fit ranks their open
  tenders, and /act/<adam>/fit is an HTMX panel on each notice (act_detail is
  untouched). Only for an ENTITLED customer whose company_profile.is_active an
  admin switched on (CRM Ταίριασμα tab → "Εμφάνιση στον πελάτη"); nothing turns
  it on automatically. The panel answers EMPTY, never 403, for everyone else.
- Customers read fit.py's `why` phrases through account_fit.CUSTOMER_WHY (second
  person). A new phrase in fit.py needs a customer version — a test runs every
  scorer branch and fails otherwise.
- open_tenders scores EVERY open candidate (CANDIDATE_CAP 5000). It used to take
  the 200 closing soonest before scoring, i.e. ~12% of a real profile's
  candidates. It also excludes hidden duplicates (VISIBLE_SQL).
- n_awards counts award ACTS (decision + contract), so customer copy says
  "αναθέσεις", never "συμβάσεις".

## Two providers for the AI summary
**DeepSeek is production; Anthropic is the second option.** AI_SUMMARY_MODEL
defaults to `deepseek-flash`, and the MODEL NAME is the whole switch — deepseek-*
→ api.deepseek.com + DEEPSEEK_API_KEY, anything else → Anthropic Messages +
ANTHROPIC_API_KEY. There is deliberately no AI_SUMMARY_PROVIDER var: two
settings that must agree can disagree, and that posts one key to the other API.
- The prompt, system text and tool schema are IDENTICAL on both. request_params
  builds the Anthropic shape and `_to_openai` rewraps the ENVELOPE only; nothing
  is reworded per provider, which is what makes the A/B result transfer.
  `_stream_openai` translates finish_reason back into the Anthropic stop_reason
  vocabulary at the edge, so one vocabulary reasons downstream.
- `output_config.effort` is ANTHROPIC-ONLY (DeepSeek rejects it); never built
  for a deepseek-* request. Don't "strip it later" — batch needs it kept.
- **Batch is Anthropic-only.** DeepSeek has no batch endpoint, and batch exists
  only to halve the price, so submit_batch REFUSES a deepseek-* model rather
  than falling back to full rate. Its auth comes from `_anthropic_headers`, NOT
  `_headers(key)` — the latter reads the configured model, which is DeepSeek.
- **Changing AI_SUMMARY_MODEL invalidates the cache**: input_hash covers the
  model, so every stored payload AND every queued job goes stale. A provider
  switch is a re-generation under the daily cap, not a config tweak.
- **/ai must follow.** It reads the provider from ai_summary.provider_of() and
  states the PRC hosting + EEA transfer in words, both languages, test-enforced.
  It does NOT repeat the "not used for training" claim for DeepSeek — their open
  platform terms permit training on API data. Don't add that sentence back
  without a contract that says it.
- OCR and call summaries stay Anthropic. When the summary is on DeepSeek and
  either of those is live, /ai declares BOTH processors.

## Measuring a model change first
`ai_summary_ab.py` measures before you switch: a reproducible sample of notices,
the real prompt/schema/quote-gate, then yield + cost per variant. Coverage
decides, then price — never $/act, and never $/item alone (it flatters a model
that returns few cheap items). Dry-run by default; --yes spends, and that path
is NOT subject to AI_SUMMARY_DAILY_CAP. Results are JSONL under runs/
(git-ignored); it NEVER writes proc.act_ai_summary.
- It RE-EXPORTS the app's transport (ai.provider_of/_to_openai/_stream_openai)
  rather than keeping its own copy. A harness whose transport has drifted is
  measuring something nobody ships, and the drift would be invisible.
- `--probe` = does this provider accept the tool schema at all (a cent).
  `--diff` = the clauses each model missed, as published Greek. Both are the
  cheap steps; run them before costing a port.
- **Haiku 4.5 cannot run this**: it rejects output_config.effort AND the
  schema itself ("the compiled grammar is too large"). Not a config change.
- DeepSeek rejects a FORCED tool_choice in thinking mode — production doesn't
  force one, so "auto" is used on both sides for parity.
- MAX_TOKENS is an output CAP, not a spend cap — you pay for what is
  generated. A cap hit loses the whole generation and still bills it.

## /ai — AI & data-handling statement
Public, bilingual, NOT a draft (unlike /privacy and /terms): every claim is
checked against the code. app/templates/ai_policy.html + the route in main.
- The per-feature on/off state is read from the LIVE predicates
  (ai_summary.can_generate, ocr.api_key_present, TELEPHONY_ENABLED +
  transcribe.backend_configured + call_summary.api_key_present) — never
  written into the prose. Add an AI surface → add it to this page in the
  same commit, or the page starts lying.
- The claims that are structural, and are test-enforced: build_sources takes
  (act, tables) and nothing customer-shaped; no page loads a third-party
  asset. If you change either, /ai is wrong before the tests are.
- Linked from the AI panel's warning band and the footer. Indexed.

## Public surface (SEO + glossary)
The acquisition layer. app/seo.py decides what a CRAWLER sees; it never changes
what a PERSON sees — _is_gated still owns that, and a crawler is an anonymous
visitor getting the same teaser.
- seo.enabled(): production (RENDER / APP_ENV) or SEO_INDEX=1. Everywhere else
  robots.txt is `Disallow: /` and every sitemap 404s. Fails towards NOINDEX —
  0/false/no/off/y-not-given all mean off. Don't invert that default.
- Indexable = a clean page (/, /authorities, /contractors, /glossary,
  /data-sources, a 2-segment detail page) OR exactly ONE allowlisted facet.
  The allowlist is registered from the real code lists (seo.set_facet_values in
  main) so it cannot drift. Everything else is noindex,follow with its
  canonical pointing at the bare path.
- Sitemaps are CAPPED, not complete: SEO_ACT_WINDOW_DAYS (365) +
  SEO_ACT_MAX (50k), 10k URLs per chunk, counts cached an hour.
- Structured data goes on the GATED render too — that is the crawler's page.
- app/glossary.py holds the term text in BOTH languages (not i18n_catalog: it
  is content). Public, deliberately outside the subscription. It is publicly
  indexed legal summary — when a threshold or percentage changes, fix it.
- /help has a "Δημόσια σελίδα & γλωσσάρι" section; keep it in sync.

## Tender Service ingester (fourth source, NOT in production)
tsg_ingest.py; db.py tsg-backfill / tsg-catchup / tsg-project. tsg_probe.py
measures the key, tsg_preview_import.py loads a probe sample and shares the
ingester's mapping.
- proc.tsg_record keeps EVERY record walked; procurement_act gets only what we
  do not hold. held_keys come from externalId/sourceUrl (ΑΔΑΜ, ΑΔΑ, TED) —
  never referenceNumber, which on a notice is its REQUEST. reconcile_held
  withdraws a projection once our own ingester brings the act.
- One day per slice: a query stops at offset 10,000, and `_to` is EXCLUSIVE.
  Only status 'done' is skipped by --resume or moves the watermark; a walk
  short of its total is 'incomplete', never done (offset paging over a live set).
- The daily cap shows ONLY as customer_api_limit_reached in a 400 body —
  Rate-Limit-Remaining still says hundreds left. It raises QuotaExhausted.
- One contractor tax number per notice: a joint award links no operator.
  Winner upserts never rename an existing economic_operator.
- Cyprus is out of scope: a record whose NUTS codes are ALL non-EL is stored but
  never projected (tsg_record.skip_reason). A scope or mapping rule change only
  reaches already-stored records through `tsg-project --reproject`.
- A refresh never moves ingested_at (digest windows). Refused on a non-local DB
  unless TSG_INGEST_REMOTE is on (fails towards off); not in cron_catchup.
- /analytics is an ALLOWLIST (khmdhs, manual, NULL) in is_analytics_eligible and
  mv_analytics_cpv. Don't turn it back into a list of excluded sources.

## Tender Service duplicates
tsg_match.py decides, per Tender Service record, hidden / flagged / new.
Spec: docs/specs/tender-service-duplicates.md (measured on one week). The web
side is live. The ingester integration (tsg_ingest.py, `db.py tsg-match`, the
tsg_record columns in migration …130324) ships with the Tender Service ingester.
- **Only an exact number hides.** The numbers are ext_id, esidis (promitheus
  `eproc-N` = ΚΗΜΔΗΣ `systemicNumbers`), a quoted ΑΔΑΜ / request (via act_link) /
  labelled ΑΔΑ, tsg_twin, and admin. The number must name a notice with a
  deadline within ±3 days (a re-tender quotes the failed notice); several such
  notices are one procedure published twice (ΠΕΡΙΛΗΨΗ + ΔΙΑΚΗΡΥΞΗ) and it hides
  behind the closest. Otherwise the record is a tier 1 flag. Fuzzy matches
  (same authority code + deadline ±1, then title / VAT-aware budget / ref no.)
  NEVER hide: they are labelled in alerts and queued for review. Same authority +
  deadline alone is not evidence — hospitals repeat generic titles. The user
  decided this.
- **Hide, never delete.** procurement_act.duplicate_of points at the act we
  show. A delete cascades through digest_run_item, reminder ledgers,
  favourites and notes. /act/<hidden> 302s to the target (admins: ?hidden=1).
- The filter is `app/act_visibility.VISIBLE_SQL`, an ANTI-JOIN.
  `a.duplicate_of IS NULL` measured ~3x slower on seq-scan counts (it unpacks
  every row to the last column). build_where starts with it, and
  `where == VISIBLE_SQL` still takes the reltuples fast path.
- Twins inside Tender Service only count across DIFFERENT portals (a
  hospital's own records share titles). The better-ranked source stays
  (SOURCE_RANK).
- Admin confirm/reject lives in proc.duplicate_candidate and survives every
  re-import. Confirm copies the spent reminder marks to the kept act. Review
  queue: /admin/interconnect/tsg.
- Alert labels: digests.annotate_duplicates, frozen in digest_run_item.dup_*.
- The CORE migration (…130323) must not touch tsg_* tables: prod has none. App
  code that reads tsg_record goes through `tsg_match.tsg_tables(c)`. No
  `DO $$` blocks in migrations: the Supabase dashboard editor splits them and the
  script fails to parse (test-enforced for these files).
- Tests: tests/test_duplicate_visibility.py (the web side, no Tender Service
  tables needed).

## Calendar feed & favourites (docs/specs/calendar-feed.md)
Favourites on the web (/account/favorites, app/account_favorites.py) share the
TABLE with the mobile API, not the code: account_favorites must NOT import
app/mobile_favorites.py (main.py loads it at startup; the mobile modules are
not deployed). The two copies of the rules must agree — a test cross-checks
them when the mobile module is present, and another forbids the import.
/calendar/<token>.ics (app/calendar_feed.py) puts favourited deadlines into
the customer's own calendar; /account/calendar makes the link.
- **The URL is the credential** (calendar servers send no cookies). Only its
  sha256 is stored; the raw URL is shown ONCE. Never store or re-display it.
- A customer without access gets **200 + one all-day notice, never 403/404** —
  Google permanently disables a subscription that answers 403. Only an unknown
  token, a turned-off feed or a deactivated account is a 404.
- DTSTAMP is derived from the data, never the clock, or no poll is ever a 304.
- /calendar is in BOTH seo._NOINDEX_PREFIXES and seo._DISALLOW_PATHS.
- Saved searches join the feed only when ticked on /account/searches
  (proc.calendar_search, slice 5). Favourites always fit first; searches fill
  the rest of CALENDAR_MAX_EVENTS, upcoming soonest first. A search never
  brings in a cancelled act, and its past reaches back only to the tick —
  never a backfill of closed tenders.
- calendar_feed.act_event is the ONE definition of an act as an event, used by
  the feed and by /act/<adam>/calendar.ics. SEQUENCE comes from
  last_update_date — without it a moved deadline does not move in the client.
- app/ics.py folds at 75 OCTETS (Greek is 2 bytes/char) and escapes `\ ; ,`.
  Its tests are written in Greek on purpose; ASCII tests pass broken code.

## Bid pipeline on favourites (docs/specs/bid-pipeline.md)
app/bid_pipeline.py; stage columns on proc.user_favorite_act; UI on
/account/favorites (_bid_stage.html). Stages: bidding / submitted / won / lost /
no_bid; NULL = bookmark. Removing the star forgets the stage.
- **The ledger suggests, it never writes.** detect_outcomes is read-only;
  confirm_from_ledger RE-DETECTS and takes nothing from the form. Only it sets
  bid_stage_source='ledger' + bid_outcome_adam; a manual stage change drops
  both. That split is the win/loss data fit.py will be calibrated on — keep it.
- "Awarded to others" is confirmable as a loss ONLY when the customer's ΑΦΜ is
  linked (fit.operator_ids_for — never onboarding's declared_afm). Lots, joint
  ventures and a winners-only ledger are why nothing is automatic.
- Detection is for entitled users only (winner names are act data).
- no_bid leaves the calendar feed on BOTH paths (favourites and ticked
  searches — calendar_feed.declined_adams).
- Isolation: bid_pipeline must not read act_ai_summary (test-enforced).

## Attachments (app/attachments.py)
Files an admin attaches to an act — mainly the ΕΣΗΔΗΣ διακήρυξη of a big
tender whose KHMDHS text is only a περίληψη. ATTACHMENTS_ENABLED (default off).
- Bytes: Supabase Storage in prod (S3 endpoint, PRIVATE bucket, path-style
  addressing, ATTACH_MAX_MB=50 — the free plan's per-file limit). Never Postgres.
- Text: proc.act_attachment.extracted_text, capped at ATTACH_TEXT_MAX_CHARS
  (200k) because prod's DB is a 500 MB free tier; text_total_chars records the
  full length when cut. Truncation is shown, never silent.
- Downloads (/act/<adam>/attachment/<id>, attachments.zip) follow the act
  page's teaser rule: gated callers are redirected to the act. The zip is built
  in memory, so it refuses past ATTACH_ZIP_MAX_MB.
- The AI summary reads them as "attachment:<id>" sources (build_sources' third
  argument — still only the ACT's own documents; test_ai_policy pins it). An
  act with no attachments keeps its exact cache key. /ai names attached
  documents only while attachments_on.
- A failed row insert removes the stored object (no orphans).

## Tests
pytest in tests/, runs in CI. Needs TEST_DATABASE_URL (throwaway DB) + psql.
Schema comes from tests/proc_schema.sql — regenerate it when you add a table.
Ship tests with every feature.
- conftest `_clean` truncates the USER side only. procurement_act, authority
  and economic_operator are never truncated: a test that inserts one owns a
  yield-teardown fixture that deletes it (see `acts` in
  test_duplicate_visibility.py). A setup-only delete still leaks the file's
  last rows. A leak breaks search counts and the public stats strip, in other
  files, depending on order; test_public_seo's stats test now catches it.

## Local dev
LOCAL_RUNBOOK.md — how to run with every feature switched on, and what each
switch needs.
