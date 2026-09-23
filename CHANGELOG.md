# Changelog

Notable changes to the KHMDHS Explorer, newest first. User-facing features are
also described in the in-app help (`/help`); this file additionally records the
**infrastructure, security, and ops** work that isn't surfaced there.

Dates are the day the change landed on `main` (which auto-deploys to prod on
Render). This project has no version tags — the git history is the source of
truth; this is a curated digest.

## Unreleased

### Changed — «Ο λογαριασμός μου» is a menu, not a page of links
- The username in the masthead opens a dropdown: Αναζητήσεις & ειδοποιήσεις,
  Ρυθμίσεις λογαριασμού, and sign-out (which moved into it). Both mastheads; on
  a phone it opens inline in the nav.
- Every `/account` page carries the same tab strip instead of a "back" link.
  One list feeds both (`_acct_links.html`); favourites and the deadline
  calendar are added there when their pages ship.
- `/account` is now «Ρυθμίσεις λογαριασμού» only: details, password, 2FA, data
  export, deletion. URLs are unchanged.
- Fixed: after a password change the settings page offered «Ενεργοποίηση 2FA»
  to someone who already had it on.

### Added — First-login wizard: from a few answers to saved searches
- `/register` asks «Έχετε συμμετάσχει ποτέ σε δημόσιους διαγωνισμούς;» (required)
  and, on «Ναι», an optional business ΑΦΜ. The ΑΦΜ is format- and check-digit-
  checked; nothing is looked up during sign-up.
- New customers land on `/welcome`: the business (ΑΦΜ → what the firm has won in
  the award ledger), what it offers (CPV classes / categories), keywords
  (suggested from its award titles), regions and size, and an overview that
  counts each search over the last 30 days. It creates up to three ordinary saved
  searches: new tenders in the field, tenders with the keywords, awards in the
  field. Skippable, resumable, re-runnable from `/account/searches`.
- The typed ΑΦΜ is a claim: kept in `proc.onboarding.declared_afm`, never written
  to the customer's identifiers. The CRM card shows it with a one-click link
  through the existing ΓΕΜΗ match route.
- CRM: «Εμπειρία διαγωνισμών» on the card (editable) and as a filter on
  `/admin/crm`, plus a started / completed / skipped line.
- A "set up your searches" band on `/` for customers who have neither finished
  nor skipped it.
- Switch: `ONBOARDING_ENABLED` (default on; `0/false/no/off/disabled` = off).
- Migration `20260921120000_onboarding_wizard.sql` (`customer_profile.
  tender_experience`, `proc.onboarding`) — run on local AND Supabase before the
  code is pushed.
- Spec: `docs/specs/onboarding-wizard.md`. Tests: `tests/test_onboarding.py`.

## 2026-09-17

### Added — Tender Service duplicate handling (web side)
- A Tender Service act that is the same tender as another act can be hidden:
  it is kept, never deleted, so alert history, reminders and favourites
  survive. Its old link redirects to the act we keep, with a short note.
  Admins can open it with `?hidden=1` and restore it.
- A possible duplicate is still sent, labelled «Πιθανή διπλοεγγραφή» in all
  three email layouts and on the "what was sent" page. The label is stored
  with the send.
- New review page `/admin/interconnect/tsg` (confirm / reject / restore).
  Decisions survive every re-import.
- The matching rules live in `tsg_match.py`
  (spec `docs/specs/tender-service-duplicates.md`). They start deciding
  records when the Tender Service ingester ships; nothing is hidden before
  then.
- Migration `20260917130323_tender_service_duplicates.sql` (applied in
  production before this push). It needs no Tender Service tables and has no
  `DO $$` blocks, which the Supabase dashboard editor cannot run.
- The search filter is an anti-join, measured at about 10% extra on filtered
  counts; the unfiltered counter keeps its instant estimate.

## 2026-09-15

### Fixed — the search page: rows first, headline after
- The count and value over the whole matching set ("βρέθηκαν N πράξεις ·
  συνολική αξία €") held back every search: 0.68s first time in production on
  a broad keyword, 2.4s on the 2.9M-act local corpus, against tens of ms for the
  page of rows. The page now renders its rows at once and fetches the headline
  from `GET /search/totals`; a `seq` stops a slow answer for an older filter
  overwriting a newer one. The pager knows there is a next page from one extra
  row. The JSON shape (`Accept: application/json`) keeps the totals inline.
- The totals cache lives ten minutes instead of one (`SEARCH_TOTALS_TTL_SECONDS`
  still overrides), so a repeated search rarely recounts.
- The filter lists (authorities, regions, types, procedures, categories) are
  built in the background at startup instead of by the first search (~2.2s at
  2.9M acts, after every free-plan wake-up), and rebuilt in the background once
  older than an hour (`LOOKUPS_TTL_SECONDS`) — a newly imported authority used
  to stay out of the filter until the next restart.

### Fixed — the act page's competition panel: on demand, correct, and fast
- "Κορυφαίοι ανάδοχοι σε αυτούς τους κωδικούς CPV" loaded with every act page
  and took 1.6s on a 6k-line CPV code, 2.8-4.4s on a 96k-line one (680 of 1,950
  open notices carry a 10k+ code). It now loads only when the Competition tab
  is opened (`hx-trigger="intersect once"`); an empty answer says so rather than
  removing the tab mid-view.
- It added a contract's value once per line item; each award now counts once.
- New `proc.mv_cpv_contract_wins` (migration 20260915200000, refreshed by
  `refresh_analytics()`): one row per (CPV code, award), ~1.3M rows. Read from
  it the panel is ~110-150ms on the 96k-line code. Until it is populated the
  route uses a rewritten live query (~0.7-1.1s there) with identical output.

### Changed — the summary tab stays on notices without full text
- A notice with no full text and no published tables used to lose its
  "Σύνοψη διαγωνισμού" tab silently, which read as a broken feature. The tab
  now stays and says a summary is not possible without the text. No generate
  button for anyone (there is nothing to send); gated readers still see none.

### Fixed — the contractor page for the largest contractors
- Its CPV panel loads on its own (`GET /contractor/<vat>/top-cpv`), as the
  authority's does: ~180ms for a contractor with 20k links.
- The act list starts from the contractor's own links instead of letting the
  planner walk the site-wide submission-date index and probe every act (65k
  probes, 190-470ms, for the 20k-link contractor; ~210ms now, <1ms for small
  ones). Acts sharing a date keep a stable order across pages.

### Fixed — authority/contractor pages: speed, and inflated CPV values
- The CPV tables' value counted an act once per line item, not once per act:
  for the largest authority division 15 showed €1.99bn against €324M counted
  once (division 34: €2.6bn vs €1.18bn). Both pages now reduce to one row per
  (division, act) — contractor: per award — before summing.
- Totals no longer call `proc.resolved_value()` per row (a correction lookup
  per act, ~720ms of ~960ms for 122k acts, with two corrections in the table);
  one join against `v_act_annotation_current` gives identical totals.
- The authority's CPV panel loads on its own (`GET /authority/<id>/top-cpv`,
  ~0.9s for the largest authority — no index removes that work). Measured
  locally, the largest authority's page went from ~1.7s to ~194ms; the largest
  contractor's aggregates from ~530ms to ~220ms combined.

### Added — top contractors on the authority page
- "Κορυφαίοι ανάδοχοι (προμηθευτές)": who wins the authority's contracts, top
  10 by value — contracts only, winners only, across a merged authority's
  records. The mirror of the contractor page's top authorities.
- Loaded on its own (`GET /authority/<id>/top-contractors`, rate-limited, empty
  for a gated visitor): ~1.3s for the largest authority, so the page mounts it
  instead of waiting for it. A test counts the page's queries to keep it so.
- A contractor opened from it links back to the authority.

### Fixed — "back to search" on an act page keeps the search
- The link was a hard-coded `/`, so every filter and the result page were lost.
  The search page now remembers its live URL per tab (sessionStorage), and the
  act page restores it — or, in a new tab, reads it from the referrer. Without
  script the link still keeps the match terms the act URL carries.
- The same for "back to authorities" / "back to contractors": one shared
  template (`_back_to_results.html`) serves all three list → detail pairs.
- The overview (/explore) too: an authority or contractor opened from it links
  back to it, with its filters ("‹ πίσω στη σύνοψη"). Its "view as act list"
  link now carries the live filters instead of the ones the page loaded with.
- And /analytics: it has no filters, but an authority or contractor opened
  from its top-15 tables now links back to it ("‹ πίσω στα στατιστικά").
- And the alert results page (/digests/<token>): an act or authority opened
  from it links back to it and its page ("‹ πίσω στα αποτελέσματα
  ειδοποίησης"). Lists with a variable address are registered by prefix.
- And the search's result cards: an authority opened from a card's authority
  link returns to the search with its filters and page.
- And the act page: an authority or contractor opened from it links back to
  that act ("‹ πίσω στην πράξη"), keeping its match terms.
- And the contractor page: an authority opened from its tables links back to
  that contractor ("‹ πίσω στον ανάδοχο").
- And related acts: an act opened from another act's timeline links back to
  it ("‹ πίσω στην πράξη"). A page that is both an origin and a destination
  never offers itself, nor the page you are returning from (no A ⇄ B loop).
- And the contractor page's acts: an act opened from it links back to that
  contractor ("‹ πίσω στον ανάδοχο").
- And the authority page's acts: an act opened from it links back to that
  authority ("‹ πίσω στην αναθέτουσα").

### Added — tables in the full-text editor, one format for rich full text
- Both Quill editors (act form, /tables + edit hub) enable Quill 2's table
  module with a small toolbar: new 3×3 table, add/delete rows and columns,
  delete table (`app/static/js/quill_tables.js`, `_quill_table_bar.html`).
- `app/rich_text.py` owns the full_text_html allow-list (explicit, not nh3's
  default). The default dropped `li[data-list]` — a bullet list saved from the
  editor came back numbered — and `td[data-row]`, which Quill needs for tables.
- Imported HTML is normalised (page clutter, inline styles and form widgets
  removed; paragraphs, headings, lists, Quill-shaped tables kept) and the plain
  full_text is derived from it. `app/static/css/rich_text.css` styles the act
  page and the editors the same way.

## 2026-09-14

### Added — customer pages usable on a phone
- Below ~760px the masthead folds into a menu button, the search/explore
  filters sit behind a "Φίλτρα" toggle (with a count of active groups), and the
  act, contractor and authority tables become stacked cards. Additive CSS; the
  desktop layout is unchanged and admin/CRM stays desktop-only.
- Follow-up: an authority's "totals by act type" table stacks too — at 393px it
  scrolled sideways and hid the "μόνο αυτές ›" link.

### Added — the act page in tabs (desktop) / an accordion (phone)
- Hero strip with value, publication, deadline and a "closes in N days" badge;
  sections Overview, Tender summary, Items & CPV, Full text, Competition,
  Linked acts, Documents. The open tab lives in the #hash; print shows every
  section; the gated teaser is unchanged. Supersedes the 09-11 panel reorder.

### Added — Fit tab: the profile's awards, authorities and main competitors
- The award and authority counts open a dialog with the rows behind them;
  "main competitors" ranks contractors sharing the same exact CPVs and
  authorities. Computed on request, stored nowhere.

### Added — ΓΕΜΗ search box pre-filled with the profile's company name

### Fixed — Export downloaded the filters the page loaded with
- Filters changed after load (HTMX swaps) never reached the Export links, so a
  79-act search exported 20,000 rows. The URL is now built from the address bar
  at click time.

### Fixed — ΓΕΜΗ candidate ranking
- Scored against the typed term, not the profile's company; a name that is a
  fragment of the query is capped at the share it covers; name similarity is
  scaled by word coverage; the ledger bonus is a tie-breaker (0.10 → 0.05, name
  0.45 → 0.50). For "food prime", both FOOD PRIME companies now rank first.

### Fixed — Fit scored exact CPV matches as group matches
- Real codes carry a check digit ('33184100-4'); the exact level looked up
  code[:8] and never fired. Live scores rise for exact matches; nothing stored.

### Chore
- jsonschema + PyYAML added to the dev requirements (mobile API contract test).

## 2026-09-11

### Fixed — the pager duplicated the filters in the URL
- Pager links carried the querystring AND included the filter form, so every
  page click doubled each filter; the act page then showed one "Γιατί
  ταιριάζει" chip per copy. Dropped the double include on all four pagers and
  dedupe repeats in match_qs, cpv_terms and multi-value filters.

### Performance
- Search: result totals are no longer recomputed on every page.
- DB pool: a connection is health-checked only after sitting idle, not on every
  checkout.
- Act page: the CPV contractor ranking loads off the critical path.
- Static assets are served without a session, cookie or database hit.
- Act page: full text moved above the comparative panels (since replaced by tabs).

### Ops
- Production moved to the Frankfurt Render service (Supabase is in Paris):
  p50 1695 → 832 ms on the same corpus.
- render.yaml no longer lets a Blueprint sync create three paid services
  (worker, digests, catch-up) that were never meant to exist.
- Deployment docs point DATABASE_URL at Supabase's session pooler; prepared
  statements are a switch.

### Fixed — the ΓΕΜΗ company search button did nothing
- The panel is a fragment shared by the customer card (an `include`) and the
  three HTMX routes that swap it. The routes pass `cust_id`; the card's context
  never did, so on a freshly loaded page Jinja rendered the missing value as an
  empty string and the form posted to `/admin/crm//company-match/search` — no
  such route, 404, and nothing visible anywhere but the browser console. The
  search was unreachable from the only place it is ever started.
- `_customer_ctx` now supplies `cust_id`. The panel test asserted only that the
  string `company-match/search` appeared in the page, which was true of the
  broken markup too; it now asserts the customer's id is *in* the URL.

## 2026-09-10

### Added — match a CRM customer to their ΓΕΜΗ company by name or email domain
- Customers register without an ΑΦΜ and only give one when they start paying,
  which left their card unconnectable to the award ledger or a fit score. The
  Στοιχεία tab now has a "Εταιρεία στο ΓΕΜΗ" panel: it searches by company name
  (or, failing that, the email domain's label), offers candidates from BOTH our
  contractor ledger and the registry, and an admin picks one.
- **The registry cannot be asked about a domain.** Measured against the live
  API: only `afm` and `name` filter. An unrecognised parameter is not rejected —
  it is dropped, and the response is the whole register (`totalCount` ≈ 1.69M)
  in the shape of a successful search. `gemi_client.REGISTRY_GUARD` rejects any
  response that size; `tests/test_company_match.py` pins it.
- The email domain therefore confirms rather than queries: the customer's domain
  against the candidate's registry email, freemail domains excluded.
- Its ranking is also not trustworthy (ΕΛΛΗΝΙΚΑ ΠΕΤΡΕΛΑΙΑ comes back *second*
  for its own name), so candidates are re-scored locally on one scale — name
  similarity .45, email domain .25, website .10, seat .10, ledger presence .10,
  with struck-off and branch entries demoted but never hidden. Components are
  always shown, never just a total.
- Linking writes the identifiers and fills **only empty** profile fields, through
  the same `leads.fill_if_empty` helper the lead importer uses. Never touched:
  full name, stage, service, manager, lead source, notes — and the account email.
- Reversible: `proc.customer_company_match.filled` records column → the value
  written, so "Άρση σύνδεσης" clears only what the import added and still holds
  that value. An admin's later correction always survives.
- Only the ΑΦΜ is posted back on link; the company data is rebuilt server-side.
  A registry record runs to 22KB and carries the company's officers — it has no
  business round-tripping through a browser into a customer record. ΓΕΜΗ
  `persons[]` is never imported at all.
- Migration: `proc.customer_company_match`. No new index — `ix_eo_name_trgm`
  already covers the ledger search, provided the fold is spelled in its nesting
  order (`f_unaccent(lower(x))`, not the reverse).


### Changed — the AI summary now runs on DeepSeek, with Anthropic as the second option
- `AI_SUMMARY_MODEL` defaults to `deepseek-flash`. The A/B harness measured it
  at comparable yield to Opus 5 for roughly a twelfth of the cost, so a notice
  now costs about $0.01-0.02 instead of $0.12-0.20.
- **The model name is the only switch.** `deepseek-*` goes to DeepSeek's
  OpenAI-compatible endpoint with `DEEPSEEK_API_KEY`; anything else goes to
  Anthropic Messages with `ANTHROPIC_API_KEY`. Pointing the variable back at
  `claude-opus-5` is the whole rollback. There is no separate provider setting,
  on purpose: two settings that must agree are two that can disagree, and that
  disagreement posts one provider's key to the other's API.
- The prompt, the system text and the tool schema are **identical on both** —
  only the envelope is translated. That is what makes the measured result a
  statement about production rather than about the harness, and the harness now
  re-exports the app's transport instead of keeping its own copy of it.
- **Batch generation stays Anthropic-only** and refuses a DeepSeek model.
  Batch exists to halve the price and DeepSeek publishes no batch endpoint, so
  a silent fallback would charge full rate for a job asked to run cheaply.
- **`/ai` changed with it, in the same commit.** The page names DeepSeek, states
  that it is hosted in the PRC and that the notice text is therefore transferred
  outside the EEA, and says what is sent: only the already-published tender
  document. It does **not** repeat the "not used to train models" claim for
  DeepSeek — that claim rests on Anthropic's commercial terms, and DeepSeek's
  open platform terms permit training on API data. OCR and call summaries are
  still Anthropic, so when either is live the page declares both processors.
- Note before deploying: `input_hash` covers the model, so every stored summary
  and every queued job is stale after the switch. The panel offers regeneration;
  it never serves a payload produced by a different model.

## 2026-09-10

### Added — fit scoring: what a customer could actually bid for
- The first slice of Tier 2's biggest item. `app/fit.py` answers "is this
  tender worth bidding for" for one customer, and the CRM card gains a
  **Ταίριασμα** tab listing the open tenders that match, best first.
- **The profile is derived, not declared.** `seed_from_ledger` builds it from
  the award ledger by ΑΦΜ — what a firm has actually won, from which
  authorities, in which regions, at what values. A linked customer has a
  usable profile with no data entry, and the figures are facts rather than
  what a form would claim. This is the part a notice-feed competitor
  structurally cannot compute: it needs an award ledger keyed by tax number.
- **Deterministic, no model.** CPV overlap (0.45), value band (0.20),
  geography (0.20) and buyer history (0.15). Free, instant, identical on
  every run — and it keeps the promise on `/ai` that customer data never
  reaches a model provider, which an LLM ranker would have quietly broken.
- **Components are always shown, never just a total.** The app puts match
  chips on results and a verbatim quote under every AI claim; a bare "83%"
  would be the one number a reader must take on faith. If the ordering looks
  wrong, the parts say which part is wrong.
- A tender whose CPVs the firm has never touched is **capped**, however well
  the value, region and buyer line up: "we do not sell that" is not something
  a weighted sum should be able to outvote.
- Admin-only, deliberately: a wrong score shown to a paying customer teaches
  them to ignore the feature permanently.

### Two bugs found by looking at real output
- CPV weights were normalised **globally**, so an 8-digit code — necessarily a
  fraction of its own division — always scored near zero and the deepest, most
  specific match was punished hardest. A medical supplier's own speciality
  scored 0.36. Now normalised within each prefix depth.
- The floor on a matched-but-rare CPV group was half credit, which let a
  water-treatment maintenance contract reach 71/100 for a medical supplier on
  the strength of value, region and buyer alone. For a broad supplier those
  three saturate and stop discriminating, so CPV has to carry the signal.

### Notes
- Migration `20260910140318_company_profile_and_fit_score.sql`: four tables.
  **Applied locally; NOT yet on Supabase** — apply it there before this
  reaches prod. Declared profile rows survive a re-seed by design (`source`
  is part of the primary key), so re-deriving never discards a correction.
- `tests/proc_schema.sql` gains the four tables by append rather than a
  re-dump: the committed file is a dump of PRODUCTION, and re-dumping from a
  local database would drag in local-only surface (attachments, telephony)
  and quietly change what CI tests against.
- 34 tests (`tests/test_fit.py`), including both directions of the isolation
  rule: fit never touches the shared summary cache, and the summary still
  cannot read a company profile.

## 2026-09-10

### Fixed — the AI summary's output cap was losing whole generations
- `AI_SUMMARY_MAX_TOKENS` defaulted to 16,000, and a reply that ran past it was
  cut off mid-JSON: the tool call never completed, the generation was
  discarded, and the tokens were **billed anyway**. Observed live on the
  largest notice in the corpus, at $0.66 for nothing.
- The cap is not a spend control — you pay for tokens generated, never for the
  ceiling — so a low one buys nothing and can lose everything. Raised to
  32,000; streaming was already on, which is what makes a large ceiling safe.
- `call_model` checked `stop_reason` for `"refusal"` but never `"max_tokens"`,
  so the truncation surfaced through the JSON parser as *"tool arguments did
  not parse"* — a schema-shaped error several layers from the cause. It is now
  diagnosed where it happens, names `AI_SUMMARY_MAX_TOKENS`, and says the
  tokens were billed. The existing test asserted the old guess-in-the-message;
  it now asserts the stronger guarantee, with a companion test making sure a
  genuinely malformed reply still reports a parse error.

### Added — `ai_summary_ab.py`, so a model change can be measured before it is made
- The AI summary is the most expensive thing the app does per act, and "Opus is
  dear, can we use something cheaper?" cannot be answered from a price list. The
  harness runs a reproducible sample of notices through the **real** prompt,
  schema and quote gate on each candidate model, and reports yield beside cost.
  Anthropic and DeepSeek (OpenAI-compatible) transports; `--probe` asks a
  provider whether it accepts the tool schema at all for a fraction of a cent;
  `--diff` prints the clauses each model found and the other missed, recovered
  from the source so they read as published Greek rather than the folded form
  used for comparison.
- **Coverage decides, then price.** The first version ranked on cost per kept
  item and called a variant finding 30% of the clauses "better value" — that
  metric flatters a model returning few cheap items. The verdict is now
  coverage-first, with $/item demoted to a column. Agreement is matched by
  containment and token overlap, not string equality: an earlier version
  reported 42% between two models that had largely read the same document,
  because they chose different span boundaries around the same sentence.
- Safety, because it spends real money: dry-run by default with a printed
  estimate, `--yes` to spend, JSONL written a line at a time so a crash keeps
  what it already paid for, results de-duplicated so a re-run supersedes the
  failure it replaces, and it **never writes `proc.act_ai_summary`** — an
  experiment must not leave a challenger's payload where the app will serve it.

### What the first run found (12 acts, $3.53 total)
- **Haiku 4.5 cannot run this at all.** It rejects `output_config.effort`, and
  then rejects the schema outright: *"the compiled grammar is too large"*. The
  8.7KB tool schema is past its constrained-decoding limit. 12/12 failed.
- **DeepSeek Flash accepted the same schema** and came out 11.8x cheaper than
  Opus 5 with a higher item count (23.0 vs 20.7 per act) and better agreement
  than Sonnet 5. Greek tokenises **30% denser** on DeepSeek — the opposite of
  what was predicted. It rejects a forced `tool_choice` in thinking mode, which
  matters only because forcing it was the harness's own deviation: production
  declares one tool and does not force it.
- Reading the clause-level diff matters more than the percentages: roughly half
  of what Opus finds and DeepSeek misses is boilerplate — letterheads, form
  footers, table fragments — so the measured gap overstates the real one, and
  the two most alarming misses (a submission deadline, an offer-validity
  period) are already record columns the panel shows from the record anyway.
- Unrelated but visible in the diff: one act's `full_text` is
  encoding-corrupted (`δεν ζχει καταδικαςτεί`), which degrades every feature
  reading it, not just this one.

## 2026-09-10

### Added — /ai, the AI & data-handling statement
- **`/ai` — «Τεχνητή νοημοσύνη & δεδομένα»**, public and bilingual: where AI is
  used here, exactly what is sent to a model provider, what never leaves, who
  the provider is, why the summary can be checked rather than trusted, what the
  system explicitly does *not* do, and what is retained. This is the question a
  first sales meeting opens with once anything on the site says "AI".
- **Not a draft.** `/privacy` and `/terms` carry the placeholder banner because
  they are legal text awaiting a lawyer; every claim on `/ai` is instead a
  statement about how this software behaves, written against `app/ai_summary.py`
  (§3, §4, §8), `app/ocr.py`, `app/call_summary.py` and `app/transcribe.py`. The
  one bracket left is the contact address, as elsewhere.
- **The page reads the live configuration, not the prose.** The state of each
  surface — tender summary, document OCR, call transcription — comes from the
  same predicates the features themselves use (`ai_summary.can_generate`,
  `ocr.api_key_present`, `telephony.TELEPHONY_ENABLED` + a reachable transcription
  backend + a summary key). Flip a switch and the page changes with it. A policy
  page that goes quietly out of date is worse than none, because it gets relied
  on. Telephony is off in production, so the page says so in words rather than
  omitting the subject — someone asking "do you record my calls" has to find the
  answer.
- **Linked where the question actually arises**: from the AI summary panel's
  permanent warning band, not only the site footer.
- Indexable and in the sitemap, unlike the draft legal pages.
- 18 new tests (`tests/test_ai_policy.py`). Beyond the page rendering, they
  assert the two claims that are structural rather than aspirational: that
  `build_sources` — the only thing deciding what reaches the model — takes an
  act and its tables and nothing shaped like a customer, and that no page loads
  a third-party asset. Both are checked against the code, so the page cannot
  drift away from being true without a test failing.
- No migration.

## 2026-09-10

### Added — a public front door: the glossary, and being findable at all
- **`/glossary`** — 34 terms of Greek public procurement explained in plain
  language, in Greek and English, grouped from registries through kinds of act
  to procedures, codes, money and law. Every entry ends in a link into the real
  data (`Απευθείας ανάθεση` → the direct awards), so a definition is a doorway
  rather than a dead end. The text lives in `app/glossary.py` with both
  languages side by side — it is content, not UI chrome, and putting it in the
  i18n catalog would have buried both.
- **A first-visit band on `/`** for a visitor with no account and no filters:
  what the database is, the corpus figures, and starting links by kind of act,
  procedure, category and region. It disappears the moment they search, and is
  never shown to a subscriber. It also gives the search page its only `h1` and
  the internal links that make the facet landings reachable.
- **The site can now be indexed** — `robots.txt`, a sitemap index over the
  acts of the last year plus every authority, contractor and glossary term, a
  canonical URL and a `<meta name="robots">` on every page, and modest
  structured data (breadcrumbs on acts, `Organization` on entities,
  `DefinedTerm` in the glossary). Nothing about who may *read* what changed:
  a crawler is an anonymous visitor and gets the same freemium teaser.

### Notes on the shape of it
- **Off unless the host says otherwise.** `seo.enabled()` is true in production
  (or with `SEO_INDEX=1`) and false everywhere else, where `robots.txt` answers
  a flat `Disallow: /` and the sitemaps 404. The switch fails towards *noindex*:
  `0`, `false`, `no`, `off` and anything unrecognised all mean off. A preview
  deploy indexed as a second copy of the corpus is not something you can undo.
- **A bounded crawl space.** The filter form is a GET form over 2.9M acts.
  Only *single*-facet landings (`?type=contract`, `?nuts=EL30`, …) are
  indexable, drawn from the real code lists so the allowlist cannot drift;
  free text, pagination and sorting are disallowed in `robots.txt`; everything
  else is `noindex, follow` with its canonical pointing at the clean path — so
  `/act/X?q=…` consolidates onto `/act/X` instead of splitting it.
- **The sitemaps are capped**, not complete: `SEO_ACT_WINDOW_DAYS` (365) and
  `SEO_ACT_MAX` (50,000) bound what is advertised, because a free instance
  crawled over 2.9M stale acts is a cost with no return. Counts are cached for
  an hour so a crawler re-reading the index cannot turn it into load.
- **Structured data sits on the teaser render**, not only the full one — the
  anonymous page *is* the page a search engine gets.
- 85 new tests (`tests/test_public_seo.py`, `tests/test_glossary.py`), covering
  the switch, the crawl-space rule, the sitemap contents and cap, and the
  glossary's content integrity — that every term has both languages, that no
  "see it in the data" link points at a facet that no longer exists.
- No migration. Nothing here touches the schema.

## 2026-08-31

### Added — result emails explain themselves, like the search does
- The list behind a result email's **"see all results"** button
  (`/digests/<token>`) now shows the **"Ταιριάζει:"** chips on every act, and
  carries the search terms onto each act link — so opening an act from a result
  email gives the same **"Γιατί ταιριάζει"** panel and highlighted text as
  opening it from a search. Previously that whole explanation stopped at the
  email: the page had no query string to derive it from.
- **The terms are frozen at send time**, in the new `proc.digest_run.params_qs`
  (migration
  `20260831093000_digest_run_search_terms_for_match_explanation.sql`). A run is
  history and a saved search is live: reading today's profile would explain a
  three-week-old email with words that did not select those acts. Runs recorded
  before the column existed fall back to the current profile — the best answer
  still available for them.
- No change to the search path, and none to the email body: the chips are the
  same single batched query, on a page that already knows its rows.

## 2026-08-27

### Added — sign in with an emailed link, alongside the password
- `/login` gains **"Email me a sign-in link"**. A customer enters their account
  address and gets a link that signs them in — no password to remember. The
  password path is untouched: this is a second door, not a replacement, which
  matters because the app already has admin roles, 2FA and existing accounts.
- **The link does not weaken anything it stands next to.** It completes the
  *password* step only: an account with 2FA still gets the TOTP prompt from the
  same half-authenticated state a correct password produces, and an
  admin-issued temporary password still walls the session off until it is
  changed. `tests/test_login_link.py` pins both.
- **The token is a credential and is treated as one.** 32 random bytes, mailed
  once, stored only as `sha256` in the new `proc.login_link` — a database dump
  is a list of useless hashes, not live logins. Single use (the check and the
  spend are one atomic `UPDATE`, so two concurrent clicks cannot both produce a
  session), 15 minutes, and issuing a new link burns the previous one. Changing
  a password or an email address burns every outstanding link too.
- **The mailed URL does not sign anyone in on GET.** Corporate mail scanners
  fetch every link in a message and would spend the token before the human
  clicked it — the single most common way magic links fail in practice. The URL
  opens an interstitial; its button POSTs and spends the token.
- **No account oracle.** `POST /login/link` renders exactly the same
  confirmation for an address with an account, one without, a deactivated one,
  and one whose send failed. Rate-limited on `proc.login_throttle` counting
  every request (it sends mail), keyed on address + IP. A customer who does hit
  the lockout is not locked out of the app — their password still works.
- The wording is an editable template like every other message
  (`proc.email_template` slug `login_link`, EL/EN), but the URL is placed by the
  email template, so no admin edit can truncate or leak the credential.
- New: `proc.app_user.email_verified_at`. Registration never confirmed an
  address, so nothing in the system knew whether an account's email was real; a
  completed link login is that proof, and it is now recorded — a prerequisite
  for the deliverability work.
- `LOGIN_LINKS_ENABLED` removes the routes and the link on `/login`. It fails
  towards off — `0`, `false`, `no`, `off`, `disabled` in any case all disable
  it. (It first shipped understanding only the literal `0`, which left the
  feature quietly ON in prod for anyone who typed `false` into the Render
  dashboard. A safety switch has to fail the safe way.)
  `LOGIN_LINK_TTL_SECONDS` sets the lifetime. **Not for real customers until
  SPF/DKIM/DMARC are done** — a digest in a spam folder is an annoyance, a
  sign-in link in one is a locked-out customer.

### Added — result emails to more than one reader
- A digest used to reach exactly one address, the account's own, because a
  subscription is (customer × search profile) and a customer row has one email.
  In practice the person who signed up is rarely the only one who wants the
  results. The new `proc.digest_recipient` is that list: **an alert now mails
  the account address plus every named reader**, and each row carries its own
  salutation, first name and surname.
- **Every recipient gets their own copy.** The intro is re-resolved per person,
  so `[[salutation]]`, `[[first_name]]` and `[[full_name]]` greet whoever is
  reading — a colleague's copy no longer opens with the customer's name.
- The account address can be **left out** (`include_primary`), for the agency
  account whose staff read the results rather than the account holder. Such a
  subscription is now a valid candidate for the sweep even with no address on
  the account itself, which the old "has an email" test wrongly excluded.
- A send counts as sent as soon as **one** message left: the window has then
  been mailed, and re-sending it so a bounced colleague could get it would put
  the whole set in front of everyone else a second time. Addresses that failed
  are recorded on the run (`digest_run.error`), with the reached count in the
  new `n_recipients`. A subscription with nobody on it records an error rather
  than reporting a successful send of nothing.
- Addresses are de-duplicated case-insensitively, so the same mailbox listed as
  both the account address and a named reader receives one copy, not two. A
  typo is refused when it is typed in, not three days later in a run history.

### Added — a summary result email
- `digest_subscription.layout` picks the shape of the body. The existing one
  (`list`) prints the new acts; the new **`summary`** prints how many acts of
  each type, what they are worth, how many contracting authorities, the open
  deadlines and the next one, the cancellations and the top five authorities —
  then a button through to the full list in the app. For a profile that matches
  a hundred acts a day, a list nobody scrolls is a worse message than four
  numbers and a link.
- The figures are computed in SQL over the **whole** ingest window, not from the
  rows the run recorded: those stop at `DIGEST_ITEM_CAP`, and a summary
  reporting 2000 acts when 5000 matched would be worse than no summary.
  `max_results` therefore applies to the list format only.
- Wording lives in its own template slug, `digest_summary` (alongside `digest`)
  at `/admin/email-templates`, so rewording one cannot change the other.
- Digest bodies now resolve their `[[fields]]` leniently: an optional token with
  no value (a reader listed with an address and no name) drops out and the gap
  is closed, instead of failing the scheduled send for everyone else on the
  list. The CRM email builder keeps its strict behaviour — there a human is
  about to send the message and must fill the hole.

### Changed — the CRM customer card is tabbed
- The card had grown to four unrelated jobs stacked down one page: the record
  itself, the alerts, the activity log and the email composer. It is now
  **Details / Alerts / Activity / Compose email**, with an always-visible "at a
  glance" strip above (company, tax id, phone, current product, active alerts,
  activity counts) so "who is this and are they paying" stays on screen
  whichever tab is open.
- The tabs are progressive enhancement: the panels are hidden by CSS only once
  the script has marked the page, so with JavaScript off the card renders
  stacked exactly as it did before. The open tab survives the redirect every
  form on the card performs (`?tab=`, then `#hash`, then `sessionStorage`), and
  arrow keys move between tabs.
- **One alert is now one card** rather than one table row, carrying its own
  recipient list, its own settings (folded away until needed) and its send
  history. Editing an alert used to mean retyping it into a shared form at the
  bottom of the page.
- Feedback from the send buttons is finally shown: those endpoints redirect back
  to the card with `?flash=`, which the page had been ignoring — a test send
  looked like it did nothing.
- Fixed: unticking **Ενεργή** on a subscription could never deactivate it. An
  unchecked checkbox posts nothing, and the endpoint's default for the field was
  `"on"`, so the absence read as "checked".

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
