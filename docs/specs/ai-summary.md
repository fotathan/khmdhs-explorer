# Spec: AI Summary — on-demand structured comprehension of a notice

**Status:** Draft, 2026-09-04 — not implemented. Nothing in this document has been built.
**Repo path:** `docs/specs/ai-summary.md`
**Author:** drafted from a v0 UX prototype review ("Tender Information Service — AI Summary tab", `v0-tender-ai-summary.vercel.app`), reconciled against the repo on 2026-09-04.
**Scope:** acts of `type='notice'` only. One shared, cached extraction per act, rendered as a panel on the act detail page. The per-customer "can *I* participate" evaluation, the tender Q&A chat, and attachment-sourced extraction are named here but deliberately deferred — see §14.

---

## 1. Problem

A `notice` arrives as a long Greek document. Everything a bidder needs in order to
answer four questions is in there, and none of it is addressable:

1. **Can I participate?** — participation criteria, certificates, minimum experience.
2. **Is it worth participating?** — value, per-lot split, award criteria and their weights, bid security.
3. **Can I meet the requirements?** — specifications, quantities, standards, delivery and warranty terms.
4. **When do I have to act?** — the deadlines that are *not* `final_submission_date`: questions, clarifications, site visits, opening.

The feed answers roughly a third of this in columns (§4). The rest exists only as
prose in `full_text`, and reading it takes twenty minutes per notice. A bidder
scanning fifteen results a day does not read them; they guess, and they miss
things.

**Goal.** A reader opening a notice can decide in under a minute whether it is
worth a full read, with every extracted claim traceable to the exact words in the
source that produced it.

**Non-goal.** Replacing the document. This is a **first-pass screening tool** and
the UI says so, permanently and without a dismiss button. Nothing here decides
anything for the reader, and nothing here is a substitute for reading the tender
before bidding.

**Non-goal.** Changing search, ranking, or the digest. The panel is additive.

---

## 2. Where this lives in the repo

The prototype was built without repo access. Resolved:

| Prototype concept | Real name here | Note |
|---|---|---|
| "Tender" | `proc.procurement_act` WHERE `type='notice'` | ~2.7M rows total, notices a subset |
| Tender ID | `procurement_act.adam` | |
| Submission Deadline | `procurement_act.final_submission_date` | **a column** — not an AI extraction |
| Estimated Contract Value | `budget`, `total_cost_without_vat`, `total_cost_with_vat`, `currency_code` | columns |
| Awarding Authority | `proc.act_authority` → `proc.authority` | multi-value since the act-parties work |
| Region | `procurement_act.nuts_code`, `proc.act_nuts` | |
| Lots | `procurement_act.number_of_sections`, `proc.tender_lot` | structured lots already exist |
| Award Criteria (MEAT / price) | `procurement_act.criteria_code` | code list `award_criteria` — the *type* only, never the weights |
| "Full Text" tab | `procurement_act.full_text` / `full_text_html` | the main model input |
| "Attachments" tab | `proc.act_attachment` | **local only** — see §6 |
| Extracted tables | `proc.extracted_table` WHERE `is_published` | `rows` jsonb, `rows[0]` is the header |
| "Ask AI about this tender" | — | out of scope, §14 |
| "1/5 Matched" eligibility | — | out of scope, §3 explains why |

New files:

| File | Holds |
|---|---|
| `migrations/<ts>_act_ai_summary_cache_and_jobs.sql` | §9 — `proc.act_ai_summary`, `proc.ai_summary_job` |
| `app/ai_summary.py` | §5–§8 — the section catalogue, the Claude call, the quote gate, cache read/write |
| `app/templates/_panel_ai.html` | §11 — the panel, generic over the section catalogue |
| `app/static/css/ai_summary.css` | panel styling (remember the `</style>` grep rule) |
| `tests/test_ai_summary.py` | §15 — schema, quote gate, precedence, cache key, gating |
| `tests/fixtures/ai_summary/*.json` | recorded model payloads, so most tests need no API key |

Touched: `app/main.py` (one route + one panel mount), `app/templates/beta_act.html`
(the mount), `app/templates/beta_help.html` (§16), `tests/proc_schema.sql`
(regenerated), `LOCAL_RUNBOOK.md` (the new switches).

---

## 3. The single most important decision: two layers, not one

The prototype shows an Eligibility section reading **"1/5 Matched — PENDING REVIEW
/ MATCHED / NOT MATCHED"**. That conflates two things that must not share a cache:

* **Extraction** — *"this notice requires ISO 9001:2015"*. A property of the act.
  True for every reader. Generated once, stored, served to everyone forever.
* **Evaluation** — *"you do not hold ISO 9001:2015"*. A property of the reader.
  Different for every customer, and derived from data (their certificates, their
  years of trading, their turnover) that no other customer may ever see.

Build them as one thing and you get one of two failures: either nothing is
cacheable and the feature costs money on every page view, or one customer's
company profile leaks into another customer's cached view of the act.

**This spec builds the extraction layer only.** Sections render as
*"the notice requires X"*, never *"you do not have X"*. The evaluation layer is
phase 2 (§14) and, when it comes, needs no model call at all — matching a
company profile against extracted criteria is Python.

---

## 4. The record beats the model. Always.

The prototype cites *"Submission Deadline · 27/06/2026 · Contract Notice.pdf p.1"*.
Here that is `procurement_act.final_submission_date`, ingested from KHMDHS.
An LLM reading a PDF is a strictly worse source for it than the feed.

So every field is owned by exactly one of two sides, and the model is never asked
for a field the record owns:

**Record-owned — excluded from the prompt's output schema entirely:**
`final_submission_date`, `signed_date`, `published_eu_date`, `budget`,
`total_cost_without_vat`, `total_cost_with_vat`, `currency_code`, `criteria_code`
(the award-criteria *type*), `procedure_type_code`, `contract_type_code`,
`notice_type_code`, `number_of_sections` and `proc.tender_lot`, `contract_duration`
(+unit), `offers_valid_time` (+unit), `bidding_website`, the authorities, the CPVs,
the NUTS codes.

**Model-owned — genuinely absent from the feed:**
questions/clarifications deadline, site-visit date, offer-opening date, the
**weights** behind a MEAT criterion, bid security (εγγυητική επιστολή συμμετοχής)
amount and percentage, performance guarantee, technical specifications,
quantities, materials, standards and certificates, delivery and response times,
warranty terms, participation criteria, penalty and subcontracting clauses,
the ΕΣΗΔΗΣ system number when it appears only in the body.

**Conflicts.** These are fed to the model as *context* (it reads better with the
budget in hand), so it can still emit a value that contradicts a record-owned
field. When it does:

* the model's value is **discarded**, never rendered;
* the disagreement is appended to `payload.conflicts[]`;
* admins see a flag on the act; customers see nothing.

A conflict is a signal about ingestion quality, not something to put in front of a
bidder. If `conflicts` starts filling up on a field, that field's ingestion is
what needs fixing.

---

## 5. The section catalogue — dynamic, but not free-form

The prototype is right that structure must vary: a cleaning-services notice has no
equipment schedule; a works notice has no software specification. But "dynamic"
must not mean "whatever JSON the model felt like", or the template cannot render
it and no test can assert anything.

The resolution: a **closed catalogue of section types**, each with a fixed schema.
The model emits **only the sections it found evidence for**, in the order the
catalogue defines. Zero sections is a valid, renderable answer.

| Key | Heading (el) | Answers |
|---|---|---|
| `timeline` | Χρονοδιάγραμμα | When do I have to act? |
| `award` | Κριτήρια ανάθεσης | How is this scored? |
| `pricing` | Οικονομικοί όροι | Bid security, guarantees, payment terms, per-lot values |
| `requirements` | Τεχνικές απαιτήσεις | Specifications, equipment, quantities, standards |
| `eligibility` | Κριτήρια συμμετοχής | What must a bidder be or hold? *(extracted, not evaluated — §3)* |
| `submission` | Υποβολή προσφοράς | Platform, format, language, required forms (ΕΕΕΣ) |
| `attention` | Σημεία προσοχής | Penalties, subcontracting limits, unusual liabilities |

Every item in every section shares one envelope:

```json
{
  "label":      "Εγγύηση συμμετοχής",
  "value":      "2% της εκτιμώμενης αξίας, 50.000 €",
  "obligation": "mandatory",          // mandatory | desirable | null
  "quote":      "<verbatim substring of the source text>",
  "source":     "full_text",          // full_text | table:<id> | attachment:<id>
  "confidence": "high"                // high | medium | low
}
```

`offset` is **not** in the schema — the server computes it in §8. The model is
never asked for a page number or a character position, because it cannot know one
and will invent a plausible-looking one.

Plus one list that is as valuable as the sections:

```json
"not_found": ["Εγγύηση καλής εκτέλεσης", "Ημερομηνία επιτόπιας επίσκεψης"]
```

"The notice does not state a site-visit date" is information. Silence is not.

---

## 6. Inputs, and what is honestly available

| Source | Available in prod? | Used in v1 |
|---|---|---|
| Structured fields + parties + CPVs + lots | yes | yes — as context (§4) |
| `full_text` / `full_text_html` | yes | **yes — the primary input** |
| `proc.extracted_table` (published) | yes, `TABLES_ENABLED` default on | yes — serialised as TSV per table |
| `proc.act_attachment.extracted_text` | **no** — `ATTACHMENTS_ENABLED=0`, table not applied to Supabase | **no** |

That last row is the one thing the prototype gets wrong for us. Its whole
provenance story (*"Technical Specification.pdf · p.12"*) rests on attachments,
and attachments are local-only — `attachment_migration.sql` says so in its own
header. A v1 that promised attachment citations in production would be promising
something the database cannot do.

So v1 reads full text and published tables. The code is written source-agnostic
(`source` is already a discriminated string), and turning attachments on later is
a data change, not a rewrite — but shipping it requires the attachments migration
and object storage to reach prod first.

**Input cap.** The largest `full_text` in the database is 1.03 MB. Greek runs
roughly 2–3 characters per token, so that single document is ~400k tokens — about
€2.00 on Opus 5, for one act. Cap at `AI_SUMMARY_MAX_INPUT_CHARS`, default
`120000` (~€0.25), mirroring `CALL_SUMMARY_MAX_INPUT_CHARS` in
`app/call_summary.py`.

**Truncation is never silent.** When the cap bites, `payload.truncated` is set
with the character count actually read, and the panel says so above the sections:
*"Η ανάλυση κάλυψε τα πρώτα N χαρακτήρα του κειμένου."* A summary that quietly
covered 30% of a document is worse than no summary, because the reader cannot
tell which 70% is missing.

---

## 7. The Claude call

**Model:** `claude-opus-5`. This is the deliberate exception to the
`claude-sonnet-4-6` pinned in `app/ocr.py` and `app/call_summary.py`: OCR reads
characters off a page and a call summary paraphrases a conversation, but this
reads legal Greek prose for obligations and has to decline to answer when the
document does not say. Extraction runs **once per act, ever**, so the price
difference is paid once and read thousands of times. `output_config.effort` is
`high` (the default); adaptive thinking is on by default on Opus 5 and is left on.

**Structured output via a strict tool, not `output_config.format`.** Two reasons:

1. The API rejects `citations` combined with `output_config.format` (400). Even
   though v1 uses the quote gate rather than native citations (§8), keeping the
   citation door open is worth something.
2. One tool per section type, each with `strict: true`, `additionalProperties:
   false` and an explicit `required`, gives schema-valid arguments *by
   construction* and lets the model emit only the sections it found — which is
   exactly the dynamic-structure requirement from §5.

Tool inputs are parsed with `json.loads`, never string-matched — Opus 5 varies
its JSON escaping and raw matching on a serialised input is a latent bug.

**Transport: raw `urllib`, no SDK.** This keeps the single house convention
established by `app/ocr.py` and `app/call_summary.py` (`x-api-key`,
`anthropic-version`, certifi-backed SSL context, no new dependency). For a single
POST per act, the SDK buys nothing. If the Batch pre-warm of §14 ships, revisit
then — batch create/poll/stream-results is where the SDK actually earns its
dependency.

**Prompt caching** buys nothing in v1: one call per act, and nothing is a shared
prefix across acts except the system prompt, which is under the minimum cacheable
length. It becomes the main cost lever the moment the Q&A chat (§14) exists, where
every question re-sends the same document — mark the document block with
`cache_control: {"type": "ephemeral", "ttl": "1h"}` there and read back
`usage.cache_read_input_tokens` to prove it is working.

**Failure is inert.** No API key → the panel does not render and the button is
absent, exactly as `call_summary.api_key_present()` gates the CRM. An API error
is recorded on the job row and shown as a retryable failure. A model call must
never 500 an act page — same rule the match panel already follows in
`app/main.py`.

---

## 8. The quote gate — the hallucination filter

Nothing the model says is trusted on its own authority. Every item carries a
`quote` that must be a **verbatim substring of the source it names**. On return,
the server:

1. normalises quote and source through the existing folding in `app/textmatch.py`
   (accents, final sigma, whitespace, case) — the same normaliser search uses, so
   the two surfaces can never develop different opinions about what "the same
   text" means;
2. locates the quote in the source and records its character offset;
3. **drops the item entirely** if the quote is not found.

Three things fall out of one mechanism:

* **a deterministic hallucination filter** — an invented requirement has no source
  text to quote, and never reaches the page;
* **a character offset**, which is what the deep link needs;
* **a click-through**, reusing the occurrence machinery from
  `app/search_match.py` and `_occurrences.html` — the same jump-to-the-text
  behaviour the search-comprehension work already shipped. The reader clicks a
  requirement and lands on the sentence that produced it, in the official Greek.

Dropped items are counted into `payload.rejected_n` and logged. A run whose
rejection rate is high is a prompt problem, and the number makes it visible
instead of leaving it to be noticed by a customer.

The offsets are stored as computed. They stay valid because the cache is keyed on
a hash of the exact text they point into (§9) — re-ingest the act and the whole
payload is invalidated, offsets included.

---

## 9. Storage and cache invalidation

```
proc.act_ai_summary
  adam            text PRIMARY KEY REFERENCES proc.procurement_act(adam) ON DELETE CASCADE
  input_hash      text NOT NULL      -- see below
  model           text NOT NULL
  prompt_version  int  NOT NULL
  schema_version  int  NOT NULL
  lang            text NOT NULL      -- 'el' in v1
  payload         jsonb NOT NULL     -- sections, conflicts, not_found, truncated, rejected_n
  n_sections      int
  input_tokens    int
  output_tokens   int
  cost_micro_usd  bigint             -- integer micro-dollars; never a float
  generated_by    text               -- user id that triggered it
  generated_at    timestamptz NOT NULL DEFAULT now()
```

**`input_hash` = sha256 of** `full_text` ‖ the ordered `id`+`created_at` of the
published `extracted_table` rows ‖ the ordered `id`+`checksum` of attachments (empty in v1)
‖ `prompt_version` ‖ `schema_version` ‖ `model` ‖ `lang`.

A cached row is served **only** when its `input_hash` equals the hash computed
now. That single rule gives correct behaviour for every case that matters:
re-ingesting the act, a curator publishing a table, editing `full_text`, bumping
the prompt, changing the schema, or switching model all invalidate; a page view
does not. The stale row is kept, not deleted — it is the record of what a reader
was shown last week, and regeneration writes a new row into
`proc.act_ai_summary_history` (same columns, `bigserial` id, no unique on adam).

`cost_micro_usd` exists so "what has this feature cost us" is a `SUM`, not an
estimate. Micro-**dollars**: Anthropic bills in USD, and storing euros would bake
in a conversion rate that ages badly. The euro figures in §13 are illustration.

---

## 10. Running the job

**`worker.py` cannot run this as-is.** It is a *subprocess runner for the `db.py`
CLI* — it claims a queued row, shells out to `db.py`, and streams stdout into
`log_text`. It does not run arbitrary Python.

Two honest options:

* **(a) Add a `db.py ai-summary <adam>` subcommand** and a
  `proc.ai_summary_job` queue with the shared queue columns, then append it to
  `worker.QUEUES`. Reuses claim/heartbeat/cancel/finalize exactly as table
  extraction does, and works unchanged on Render's background worker.
* **(b) Run it inline in the request** with a hard timeout.

**Recommendation: (a).** A 30–60 second Opus 5 call inside a web request ties up a
worker on a single-dyno Render deployment, and a deploy mid-call loses the work
with nothing to retry — the exact failure that motivated the separate worker
container in the first place. The user clicks "Δημιουργία σύνοψης", the panel
polls with htmx like the ingestion job pages already do, and the result lands.

`AI_SUMMARY_JOB_TIMEOUT` (default 180s) bounds a stuck call.

---

## 11. The surface

A panel on the act detail page, mounted the same way the published tables already
are — `hx-get`, `hx-trigger="load"`, `hx-swap="outerHTML"` — so it costs the page
nothing when there is no summary and collapses to nothing on `type != 'notice'`.

```
{% if ai_summary_enabled and n.type == 'notice' %}
<div id="ai-summary-mount" hx-get="/act/{{ n.adam }}/ai" hx-trigger="load" hx-swap="outerHTML"></div>
{% endif %}
```

Three states, all rendered by `_panel_ai.html`:

* **absent** — no summary, and the reader may generate one → the disclosure banner
  plus one button;
* **running** — a job is queued or running → poll;
* **present** — the sections.

The panel keeps four things from the prototype verbatim, because they are the
parts that make it safe:

1. **The screening banner, permanent and undismissable.** *"Εργαλείο πρώτης
   αξιολόγησης — επιβεβαιώστε πάντα τα στοιχεία στα επίσημα έγγραφα πριν
   υποβάλετε προσφορά."*
2. **Question-shaped section headings.** "Πότε πρέπει να ενεργήσω;" not
   "Ημερομηνίες".
3. **`AI extracted` on every section** — the boundary between what the record says
   and what a model said is visible without hovering anything.
4. **"Αναφορά προβλήματος" per section**, writing to the existing annotation
   plumbing. Every wrong extraction a customer reports is a prompt test case.

It drops one thing: the prototype's **Relevant / Not relevant** vote sits at the
top of an AI panel, where it reads as a verdict *on the summary*. What it is
actually worth — a per-customer signal about the *act* — belongs next to the act's
title, not inside this panel, and belongs to the saved-search work. Out of scope.

Sections render generically by key from the catalogue, so adding a section type is
a change to `ai_summary.py` and a heading string, not to the template.

---

## 12. Gating, switches, and cost control

| Switch | Default | Effect |
|---|---|---|
| `AI_SUMMARY_ENABLED` | **off** | absent → OFF. Routes, panel and button all disappear |
| `ANTHROPIC_API_KEY` | — | absent → generation impossible, panel shows cached rows read-only |
| `AI_SUMMARY_MODEL` | `claude-opus-5` | |
| `AI_SUMMARY_MAX_INPUT_CHARS` | `120000` | §6 |
| `AI_SUMMARY_DAILY_CAP` | `50` | acts generated per day, whole deployment |
| `AI_SUMMARY_JOB_TIMEOUT` | `180` | seconds |

**The switch fails towards off, and defaults to off.** Unlike `LOGIN_LINKS_ENABLED`
— which is on by default because with `EMAIL_BACKEND=console` it is harmless —
this one spends money on every use, so absent means off and *every* spelling of
"no" (`0/false/no/off/n/f/disabled`, any case) means off. Reuse
`login_links._OFF_VALUES` rather than writing a second vocabulary, and do not
narrow it to `== "1"`: the reason that lesson is in `CLAUDE.md` is that a
dashboard-typed value silently left a feature on in production once already.

**Who may read** a cached summary: entitled users — `auth.ENTITLED_STATUSES`
(`tester`, `subscriber`) plus admins — gated the same way the rest of the detail
page is.

**Who may generate** in v1: **admins only.** The first weeks of a model feature
are when the prompt is wrong, and the person absorbing that should be you, not a
customer. Widening to entitled users is a one-line change once the rejection rate
(§8) and the reported-issue count are boring.

**Rate limit** on generation via `proc.login_throttle` with key
`aisummary:<uid>`, counting every request — same discipline as the login link,
where the counter is the thing that costs money, not the failure.

---

## 13. What it costs

A typical Greek notice `full_text` is 20k–60k characters ≈ 8k–25k tokens. On
Opus 5 ($5.00/MTok in, $25.00/MTok out) with a ~3k-token structured output:

| | input | output | per act |
|---|---|---|---|
| typical notice | €0.04–0.12 | €0.075 | **€0.12–0.20** |
| at the 120k-char cap | €0.25 | €0.075 | €0.33 |

Paid **once per act, ever**, and read by every customer who opens it. Against 2.7M
acts, precomputing is unthinkable; against the few hundred notices a week that a
digest actually puts in front of someone, it is noise. `AI_SUMMARY_DAILY_CAP`
bounds the worst case at roughly €10/day while the prompt is still settling.

---

## 14. Out of scope — and what each one needs first

| Deferred | Blocked on |
|---|---|
| **Per-customer eligibility evaluation** ("1/5 matched") | a company profile — certificates, years trading, turnover, capabilities. Needs a CRM data model and a customer-facing editor. No model call once it exists (§3) |
| **"Ask AI about this tender" chat** | this panel shipping first. Then it is prompt caching (§7) over the same document, plus per-user rate limiting and a much harder abuse surface |
| **Attachment-sourced extraction** | `attachment_migration.sql` + object storage reaching prod (§6) |
| **Batch pre-warm of digest acts** | this panel shipping. 50% cost via the Batch API; generate overnight for the notices a digest is about to mail, so the customer clicking through finds it already there. Reconsider the SDK at that point (§7) |
| **English summaries** | a decision that English is worth doubling per-act cost. The cache is already keyed on `lang`, so it is additive |
| **Relevant / Not relevant voting** | belongs to saved searches, not here (§11) |

---

## 15. Tests

Ship with the feature, per `CLAUDE.md`. Most need no API key — recorded payloads
in `tests/fixtures/ai_summary/` drive everything except one live smoke test that
skips when `ANTHROPIC_API_KEY` is absent.

* **Schema** — every section type in the catalogue round-trips; an unknown section
  key is dropped, not rendered; zero sections renders an empty-but-valid panel.
* **Quote gate** — an item whose quote is absent from the source is dropped and
  counted; an item whose quote differs only by accents/final sigma/case **is**
  kept and its offset is correct (this is the regression that matters — it is the
  same folding search uses); the computed offset lands on the quote.
* **Precedence** — a model value contradicting `final_submission_date` never
  reaches the payload's sections and does appear in `conflicts`.
* **Cache key** — editing `full_text` invalidates; publishing a table invalidates;
  bumping `prompt_version` invalidates; a plain page view does not.
* **Truncation** — over the cap sets `truncated` and the panel says so.
* **Gating** — `AI_SUMMARY_ENABLED` unset, `"0"`, `"false"`, `"FALSE"`, `"off"`,
  `"disabled"` all remove the routes; a non-entitled user gets no panel; a
  non-admin cannot POST a generation; `type != 'notice'` renders nothing.
* **Inert without a key** — no `ANTHROPIC_API_KEY`, cached rows still render, the
  generate button is absent, nothing 500s.

`tests/proc_schema.sql` regenerated for the two new tables.

---

## 16. Acceptance criteria

- [ ] A notice with `full_text` produces at least a `timeline` and one other section, in Greek.
- [ ] Every rendered item links to the exact sentence in the full-text panel that produced it.
- [ ] No rendered item exists whose quote is not present in the source.
- [ ] No rendered item restates a value the record already owns (§4).
- [ ] A second reader opening the same act triggers zero API calls.
- [ ] Editing `full_text` and reopening offers regeneration rather than serving the stale payload.
- [ ] `AI_SUMMARY_ENABLED=false` removes the routes, the panel and the button.
- [ ] No `ANTHROPIC_API_KEY`: the page renders normally, cached summaries still show, nothing errors.
- [ ] A model or network failure leaves the act page fully usable.
- [ ] `SUM(cost_micro_usd)` answers "what has this cost".
- [ ] `/help` (`beta_help.html`) documents the panel, per the standing rule that user-facing changes update the manual.

---

## 17. Decisions taken, and how to overturn them

Three were open when this was drafted. Taken as follows, each reversible:

1. **Greek only in v1.** The documents are Greek and the extraction quotes them
   verbatim; an English payload would either translate the quotes (breaking the
   quote gate against the Greek source) or mix languages. `lang` is in the cache
   key, so adding English later is additive and costs a second generation per act.
2. **Sections in v1: `timeline`, `award`, `pricing`, `requirements`,
   `submission`, `attention`.** `eligibility` is *extracted and rendered
   read-only* — the criteria are useful on their own — but nothing is evaluated
   against the reader (§3). To overturn: build the company profile first.
3. **No SDK.** Raw `urllib`, matching `ocr.py` and `call_summary.py`. Revisit if
   the Batch pre-warm ships (§7, §14).
