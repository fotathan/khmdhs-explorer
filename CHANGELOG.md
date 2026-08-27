# Changelog

Notable changes to the KHMDHS Explorer, newest first. User-facing features are
also described in the in-app help (`/help`); this file additionally records the
**infrastructure, security, and ops** work that isn't surfaced there.

Dates are the day the change landed on `main` (which auto-deploys to prod on
Render). This project has no version tags — the git history is the source of
truth; this is a curated digest.

## 2026-08-27

### Changed — result emails: who gets them, what is in them, and where they are configured
- **Only active testers and subscribers are mailed.** A subscription is no longer
  permission by itself: an expired tester, a lapsed subscriber and a prospective
  lead (a CRM record with no grant) are excluded, and stop being mailed the day
  their grant lapses with no admin action. The gate uses the same status
  expression the CRM segments by (`auth.ENTITLED_STATUSES`), and is applied both
  when the sweep picks candidates and inside `run_subscription`, so the admin's
  "send now" button is not a way around it — a refused send records a run with
  the new `status='skipped'` and the reason.
- **Per-customer settings moved to the CRM customer card** (`/admin/crm/<uid>`),
  which now also lists that customer's **saved searches** (their own, plus any
  portal profile they are mailed about) with a summary of each one's filters.
  A single portal-wide list stopped being the right place to answer "who gets
  what" as soon as there was more than a handful of customers. `/admin/digests`
  keeps what IS portal-wide: the cadences, a read-only overview flagging
  customers who will not be mailed, and the run history.
- **The window is now literally "since your last email".** `last_cursor` moves
  only when a message actually left; an empty run, a failed send or a refused
  one leaves the window intact for the next email instead of consuming it.

### Added — every email's own results page
- Each send records its matched acts in the new `proc.digest_run_item` — the
  **whole** ingest window, not only the `max_results` the message listed — plus
  an unguessable `digest_run.token`.
- The email's **See all results** button now opens `/digests/<token>`: exactly
  the acts that email covered, in the same order, paged 25 at a time. It no
  longer replays the saved search, which drifts — clicked two days later the
  same query returns a different set. If the email showed the first 25 of 80,
  the page shows all 80.
- The link identifies the run, it does not authorise anyone: the route requires
  a signed-in viewer who owns the run (admins may also read it, and the page
  says so). A forwarded link shows a stranger a login page, then a 403.
- The admin run history and the customer card link to the same page
  ("what was sent"), so support can see precisely what a customer received.
- Migration: `20260827073719_digest_recipient_gating_run_items_and_result_links.sql`.

## 2026-08-17

### Added — CTI telephony: in-browser softphone, click-to-call, caller-ID screen-pop
- An open-source stand-in for NFON *Cloudya CRM Connect / NCTI Premium*, built on
  **Asterisk** + a **WebRTC softphone** (JsSIP) embedded in every page — no
  desktop app, browser plugin, or OS protocol handler.
- **Click-to-call** from any `tel:`/`[data-call]` number (or
  `window.khmdhsPhone.call()`); **screen-pop** on incoming calls via a
  background **AMI listener** that looks the caller up **live** in the CRM
  (`customer_profile` → `customer_contact` → `economic_operator`) and pushes the
  match to the agent's browser over a WebSocket. Held calls log to
  `proc.customer_call`.
- Number matching is locale-aware (`phonenumbers`, trailing-digits key) so
  `+30`, `0030`, and national formats resolve to the same record. Widget UI is
  fully EL/EN localised.
- **One-click call from the CRM customer page** — a Call button (the customer's
  phone/mobile) plus click-to-call contact numbers, logged against the customer.
- **Incoming recognition is routed to the customer's assigned manager**
  (`customer_profile.manager_id`), falling back to all online admins; a
  recognition toast shows to whoever is not the one actually answering.
- The softphone is present on **every** page (both `base.html` and
  `beta_base.html`, so admin/CRM/legacy pages included). The screen-pop
  **Open record** link opens in a **new tab** so viewing a caller's page never
  tears down the in-progress call (an active WebRTC call can't survive a
  same-tab reload); the AOR allows multiple concurrent registrations so a
  second tab doesn't evict the call tab.
- **Gated on `TELEPHONY_ENABLED`** — a complete no-op on prod until configured.
  New: migration `20260817100404` (`proc.sip_extension`, CTI columns on
  `customer_call`), `app/telephony.py`, `telephony/` Asterisk compose + configs.
  See **`TELEPHONY_RUNBOOK.md`**.

## 2026-07-22

### Added — create Prospective Leads directly from the Contractor Database
- On **/contractors**, admins can select contractors and **Import as prospective
  leads** — no XLS export / CSV round-trip. Each becomes a **non-login customer
  account** (`app_user` role=customer, random password) with a stored
  `customer_profile.crm_stage='prospective'`, appearing in the CRM customer list
  under a new **Prospective** segment.
- **Auto field mapping** from `economic_operator` (+ ΓΕΜΗ fallback): company, ΑΦΜ,
  tax/GEMI number, address, and the contact person → a **main contact**; extra
  contacts (from `act_contractor`) import as **inactive** (`proc.customer_contact`).
  Missing email → generated `{customerID}@prospective.com`.
- **Duplicate detection + conflict UI** (three buckets): exact email (update /
  new-email / skip), same non-freemail domain (update / create / skip), strong
  ΑΦΜ/ΓΕΜΗ/tax match (**hard block** — update / skip only), similar company name
  (soft). Freemail domains are a seeded, configurable table.
- Lead metadata: `service='TAS'`, **round-robin manager** across admins,
  `creation_source='OrgDB'`, and a link back to the source contractor shown on
  the CRM page. New migration `20260722144110_*` + `proc.customer_contact` /
  `proc.crm_freemail_domain`.

## 2026-07-13

### Added — free local OCR tier for table extraction
- Table extraction (act edit/create form's Πίνακες tab + the standalone /tables
  tool) now offers a **free "Local OCR (Tesseract)" button before the paid Claude
  button** — matching the tiered escalation the full-text flow already had. A new
  `local_ocr.ocr_image_table` reconstructs a grid from Tesseract word boxes
  (row clustering + x-projection columns); `tables._local_ocr_tables_entry` wraps
  it into the standard editable table. Lower fidelity than Claude on messy tables
  (the curator edits the result), but free and offline. Route `POST /tables/local-ocr`.

### Added — structured tender lots & act scope
- **First-class procurement lots** (`proc.tender_lot` + CPV/NUTS children), owned
  by a tender lifecycle group (`proc.act_group`) — **not** modelled as acts and
  **not** added to `proc.act_type`. Lots are imported from TED or authored by an
  admin.
- **Act scope** (`proc.act_scope` / `proc.act_lot_scope`): each act applies to the
  **whole tender**, **specific lots**, or is **unknown** (the default — absence of
  a row). A DB trigger rejects cross-group lot links and orphaned whole/unknown
  scopes; the "≥1 lot" rule is enforced in the service layer.
- **TED source-native lot snapshots** (`proc.ted_notice_lot` + CPV/NUTS,
  `proc.ted_lot_result`): the notice XML is now parsed **once** into a structured
  result (lots + lot-results) and rendered to text from that same structure
  (`parse_notice_xml` / `render_fulltext`; `parse_fulltext` kept byte-compatible).
- **Lifecycle grouping by identifier** (`proc.act_group_identifier`): multiple TED
  publications of one procedure converge on a single group; lot-results scope the
  award act to its lots. Curator-set scope and authored lots are never overwritten
  by ingestion. Machine-created singleton groups carry an `auto` flag and are
  hidden from the curated group listing.
- **Admin** (`/admin/interconnect/group/{id}`): a Lots section (authored CRUD,
  imported read-only) and a per-act "Applies to" control. **Public act page**: a
  Tender-lots panel and related acts bucketed into whole-tender / per-lot /
  not-determined. Analytics totals are unchanged (lots are not acts).
- **Lot backfill** for the TED back-catalogue: `db.py ted-lot-backfill`
  (+ admin button, `ted_notice.lots_extracted_at` marker) re-fetches the XML of
  notices imported before structured lots existed to capture their lot snapshot,
  without touching stored full text. New TED collections capture lots inline.

### Added — act parties (authorities & contractors on the act)
- Capture **multiple authorities and contractors** on an act, each with full
  detail (name, ΑΦΜ, id, address, contact, notes; contractor also the award
  amount) — stored in new `proc.act_authority` / `proc.act_contractor` child
  tables, surfaced as repeatable blocks on the manual act form and read-only
  panels on the act page.
- **Auto-relate** each party to the normalised `proc.authority` /
  `proc.economic_operator` entity on an exact ΑΦΜ, id, or accent/case/final-sigma
  folded name match (only when unambiguous).
- **Search-and-relate dialog** (per row) backed by admin-gated
  `/admin/api/{authority,contractor}-suggest` for manual linking when there's no
  auto-match.
- **Scanner** now auto-fills parties from the full text, **validated against the
  entity DB**: a ΑΦΜ or an organisation-name line is only offered when it exists
  in the DB, and accepting it links the row to that entity.
- Manual act form: the former free-text fields (procedure, document sub-type,
  status, regulation, bid type, activity, e-auction) are now **dropdowns**; the
  scanner snaps detected values to the nearest option.

### Security
- **pillow 12.2.0 → 12.3.0** (PYSEC-2026-2253…2257), caught by CI's pip-audit.

## 2026-07-12

### Security & hardening
- **Admin-issued temporary passwords** with mandatory change on next login
  (`app_user.must_change_password`) — onboard/unlock a user without an email
  provider.
- **Server-side session invalidation** (`app_user.session_version`, checked each
  request, bumped on password/MFA/role change); enabling 2FA now re-verifies the
  current password; recovery codes widened to ~80 bits.
- **DB-record protection**: revoked `app_runtime` from the migration ledger
  (`proc.schema_migration`) entirely and from UPDATE/DELETE on the append-only
  audit log (`proc.admin_action`).
- **Backups**: `backup.sh` gained a `pg_restore --list` integrity gate, SHA-256
  sidecars, optional GPG encryption, and a `--verify` mode (+ cron example).
- **Self-hosted Fira webfonts** (vendored under `/static/fonts`); dropped both
  Google Fonts origins from the CSP — the app now pulls **no** third-party
  frontend resources (privacy: no visitor-IP leak to Google).
- **Container runs as a non-root user** (uid 10001) — defence-in-depth.
- **Logout is a CSRF-protected POST** (was GET) — no link/prefetch logout.
- **Abuse protection**: rate-limit the public search route; centralised real
  client-IP extraction behind Render's proxy (`X-Forwarded-For`).

### CI / dependencies
- New CI `lint` job: ruff (correctness subset), pip-audit (CVE gate), and a
  migration-manifest consistency check; added Dependabot.
- Upgraded genuinely-vulnerable pins instead of ignoring them: jinja2 3.1.6,
  requests 2.33.0, fastapi 0.139.0 → starlette 1.3.1, python-multipart 0.0.31.

### Tooling / tests
- Test coverage for the background job worker (claim / finalize / cancel /
  stale-recovery) and the paywall tier matrix.
- `loadtest.py` — a tiny stdlib load generator for the read paths (search,
  analytics, detail), no dependencies.

## 2026-07-11

### Added — manual curation
- **Deterministic full-text field scanner** (no AI): parses ΑΦΜ, CPV,
  postal→NUTS, dates, amounts, and title from an act's text into one-click
  candidates; highlights matches in the editor; floating always-visible results
  panel; recognises more written Greek date formats.
- `/version` reports OCR capability (tesseract / Greek data / Anthropic key).

### Fixed
- 422 on full-text file upload when creating a new act; scanner close button;
  local OCR now logs render failures instead of swallowing them.

## 2026-07-10

### Added
- **Search profiles** (saved searches) for portal and customers, with live
  links, loading feedback, and an active-profile badge.
- **Act export** to CSV / XLSX for signed-in users, with DoS guards and a
  download spinner.
- Coded act fields resolved to labels from the official KHMDHS code lists;
  award-criterion label shown instead of the raw code.
- Tri-state act booleans (Yes / No / Not specified); unspecified booleans hidden
  on the act page; ΚΗΜΔΗΣ source badge (parity with Diavgeia/TED).
- Per-line delivery / realisation addresses on act items.

### Infrastructure & security
- **Schema migration tracker** (`proc.schema_migration` + `migrate.py`).
- **Scoped `app_runtime` DB role** (least-privilege DML) split from the owner.
- Security headers + CSP, vendored HTMX/Quill, password self-service, per-IP
  rate limits; DB-backed login throttle.
- Optional **TOTP two-factor auth**; structured JSON request logging + request
  IDs; pytest suite + GitHub Actions CI.
- Admin-launched jobs moved to a **worker** (off the web process); scheduled
  ingestion via **Render Cron**; S3-compatible attachment backend; `backup.sh` +
  runbook.
- Fixed CPV/NUTS typeahead (CSP was blocking htmx `js:` `hx-vals`).

## 2026-07-09

### Added
- GDPR self-service account page (data export + deletion); data-provenance
  sidecard, site footer, privacy/terms pages; unified top navigation.
