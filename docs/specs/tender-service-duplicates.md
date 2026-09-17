# Spec: Tender Service — duplicate handling at import

**Status:** Built locally, 2026-09-17; not deployed. §13 records where the build differs from this draft.
**Repo path:** `docs/specs/tender-service-duplicates.md`
**Depends on:** the Tender Service ingester (`tsg_ingest.py`), still local-only (`TSG_INGEST_REMOTE`).
**Decisions taken:**
- Fuzzy matches are **never** hidden automatically. Only an exact number hides a record.
- Possible duplicates are **labelled in alerts**, not held back.
- A duplicate an admin confirms is hidden like an exact match (confirmed 2026-09-17).

---

## 1. Problem

Tender Service will deliver every Greek source except the three we ingest
ourselves. Tender Service switches off ΚΗΜΔΗΣ and TED on its side, and
Διαύγεια stays on (§2.3). Much of what arrives is a tender we already hold,
published a second time on a hospital portal, isupplies or promitheus. Some of it
is a tender Tender Service itself holds twice, from two of its sources.

**Goal.** Show each tender once where the evidence is certain. Where it is only
likely, show it and say so. Record every decision and its reason, so any
decision can be explained and reversed.

**Non-goals.**
- Matching award results (Αποτέλεσμα). v1 applies the new rules to notices
  (Προκήρυξη). Results keep today's rule: the record's own ID only.
- Making search or ranking aware of duplicates beyond hiding confirmed ones
  (§6).

---

## 2. What we measured (one week, 2026-09-10 → 16)

Local database, with ΚΗΜΔΗΣ and Διαύγεια brought up to date first. 3,174
Tender Service notices from sources we do not ingest. Scripts live in the
session scratchpad (`dedup_week.sql`, `report.sql`, `sample.sql`); §10 turns them
into a command.

### 2.1 Evidence tiers

| Tier | Evidence | Records | Share | 12 pairs read by hand | Agrees with exact match |
|---|---|---|---|---|---|
| 0 | Exact number | 325 | 10% | – | – |
| 1 | Title + (budget or ref. no.) | 165 | 5% | 12 of 12 real | 64 of 64 |
| 2 | Title only | 77 | 2% | about 10 of 12 | 19 of 22 |
| 3 | Budget or ref. no. only | 156 | 5% | 11 of 12 | 114 of 119 |
| 4 | Same authority + deadline only | 1,452 | 46% | 0 of 12 | 34 of 59 |
| 5 | No candidate | 999 | 32% | – | – |

Tier 0 breaks down as: request number resolved to a notice 200, ΕΣΗΔΗΣ system
number 136, quoted ΑΔΑΜ 104, quoted ΑΔΑ 35.

### 2.2 Facts the design rests on

- **`authorityIdentifier` is our authority code.** It equals `proc.authority.org_id`
  (the ΚΗΜΔΗΣ organisation code) for 95% of authorities, so authority
  matching never needs names.
- **promitheus `externalId` holds the ΕΣΗΔΗΣ number.** The form is `eproc-NNNNNN`,
  and the number equals the ΚΗΜΔΗΣ notice's `raw_json.systemicNumbers[].systemicNumber`.
  158 of 165 promitheus notices match exactly.
  (`proc.act_systemic_number` is empty locally. Read `raw_json`, or backfill
  that table first.)
- **Hospitals defeat title matching.** One hospital had 46 notices closing on
  the same day, titled "Διάφορα αναλώσιμα υγειονομικά υλικά για το τμήμα …".
  Only the budget separated them.
- **isupplies budgets include VAT.** They equal our net amount × 1.06, 1.13,
  1.17 or 1.24.
- **Blocking on authority code misses some real twins.** 61 records with an exact
  match had no fuzzy candidate at all. Mostly that is promitheus, where our
  notice names a different authority (33). So exact rules must run on their own,
  never only inside the candidate search.

### 2.3 Διαύγεια stays on

Tender Service had 1,613 Διαύγεια notices that week, and 1,012 are decision
types we never ingest. We sampled 73 on the Διαύγεια API: 46 Δ.1, 19 2.4.7.1,
6 Α.2, 2 Β.5, and none were ours. Our ingester reads Δ.2.1 / Δ.2.2 / Γ.3.4 only.
Those records are real invitations, and 91 of them have an exact ΚΗΜΔΗΣ twin.

### 2.4 Tender Service duplicates itself

Hospitals post the same invitation on isupplies **and** Διαύγεια (as a Δ.1
decision). 76 of our notices were matched by Tender Service records from two or
more of its sources. There are 588 candidate Διαύγεια↔isupplies pairs, an upper
bound inflated by generic titles. **Ask Tender Service** whether it groups these
internally. If the API exposed that group ID, or the ΑΔΑΜ it linked a record to,
it would replace most of §3.3.

---

## 3. Matching, per notice, in this order

The first step that decides wins. Steps run inside `project_record`, before
anything is written to `procurement_act`.

### 3.1 Out of scope (exists)

All NUTS codes are non-EL → `skip_reason = 'outside Greece'`. Unchanged.

### 3.2 Exact rules → hidden

Each rule yields zero or more of our notices (`type = 'notice'`,
`data_source <> 'tsg'`).

| Code | Where the number comes from | Resolves to |
|---|---|---|
| `ext_id` | `externalId` / `sourceUrl` (today's `held_keys`) | the act with that ΑΔΑΜ / ΑΔΑ / TED number |
| `esidis` | `externalId` matching `^eproc-(\d+)$` | notices whose `systemicNumbers` contain it |
| `quoted_adam` | `\d{2}PROC\d{9}` in title, contentDescription, tenderText, referenceNumber, authorityReference | that notice |
| `quoted_req` | `\d{2}REQ\d{9}` in the same fields | the notices `act_link` connects to that request |
| `quoted_ada` | an ΑΔΑ within 5 characters of the label "ΑΔΑ" / "Α.Δ.Α." | the act whose ΑΔΑ that is |

**Guards.** A rule hides the record only when all three hold:
1. It resolves to **exactly one** notice. Several → go to §3.4 as tier 1 with
   signal `ambiguous_exact`.
2. That notice's deadline is within **±3 days** of the record's. A
   re-tender (επαναπροκήρυξη) quotes the failed earlier notice. Outside the
   window → tier 1 with signal `exact_far_deadline`.
3. The number is not shared by more than `max_shared` acts (the setting
   Interconnection already uses, default 30). A placeholder is not an identity.

`referenceNumber` stays a non-identity on its own (tsg_ingest docstring). It
counts only through `quoted_req`, and only via a request→notice link.

### 3.3 Tender Service twin → hidden

Two Tender Service notices share an exact key: the same ΕΣΗΔΗΣ number, quoted
ΑΔΑΜ or quoted ΑΔΑ, or one quotes the other's ΑΔΑ. The one from the
lower-priority source is hidden, pointing at the other.

Source priority (proposed, §11):
promitheus > Διαύγεια > manual_input > isupplies > hospital and other portals.
The same guards as §3.2 apply.

### 3.4 Possible duplicate → shown, labelled, queued for review

**Candidates.** Our notices (ΚΗΜΔΗΣ and Διαύγεια) and non-hidden Tender Service
notices that meet both conditions:
- same authority code: `authorityIdentifier = procurement_act.authority_id`;
- deadline within ±1 calendar day.

**Signals per candidate.**

| Signal | Definition |
|---|---|
| `title` | trigram similarity of the folded titles (`f_unaccent`, lower, ς→σ) after stripping portal boilerplate (§3.6) |
| `budget` | the record's estimate equals our net amount, or net × (1 + VAT) for VAT in {6, 13, 17, 24}%, within €0.50. Records the VAT rate used |
| `ref_no` | a 4–7 digit number from the record's title (years 2020–2039 excluded) appears as a whole number in our title or full text |
| `cpv` | the 8-digit CPV sets overlap. Recorded, but no tier uses it (it rarely separates hospital tenders) |
| `n_candidates` | how many candidates the block produced |

**Tiers.**

| Tier | Rule |
|---|---|
| 1 | title ≥ 0.5 and (budget or ref_no); or budget and ref_no; or an exact rule that failed a guard |
| 2 | title ≥ 0.5 |
| 3 | budget or ref_no |
| — | anything else: **no flag, nothing stored** (the old tier 4) |

**Round-budget guard.** A budget match on a multiple of €100 with
`n_candidates > 5` does not count on its own. The tier 3 false match in the
sample was €29,890 vs €29,890.25; this guard would not have caught it. It
narrows the risk rather than removing it. Tunable.

The best candidate decides the tier. Up to 3 candidates are stored.

All thresholds live in `proc.match_setting`, the existing table, with new keys
prefixed `tsg_`: `tsg_title_min` 50, `tsg_deadline_days` 1,
`tsg_exact_deadline_days` 3, `tsg_budget_tolerance_cents` 50,
`tsg_round_budget_candidates` 5.

### 3.5 Otherwise → new

The record is projected as today, with no flag.

### 3.6 Boilerplate stripped before comparing titles

A list in code, extended as portals show up:
- isupplies: `\s*-\s*αριθμός διαγωνισμού:\s*\d+\s*$`
- dypethessaly: trailing amounts `\d[\d.]*,\d{2} EUR$`
- promitheus lots: leading `^\d+\.\s`

---

## 4. Re-checking later

The twin often arrives after the Tender Service copy. `match_open(db)` re-runs
§3.2–3.4 for Tender Service notices that meet all three conditions:
- not hidden;
- deadline ≥ today − 1;
- first seen within 45 days.

It runs:
- at the end of `tsg-backfill` and `tsg-catchup`, replacing `reconcile_held`,
  which it covers;
- at the end of `catchup` (ΚΗΜΔΗΣ) and `diavgeia-catchup`, and so in
  `cron_catchup.py`, but only when the Tender Service source is enabled;
- on demand: `db.py tsg-match [--since YYYY-MM-DD] [--dry-run]`.

State changes a re-check may make:

| From | To | When |
|---|---|---|
| new | flagged | a candidate appeared |
| new / flagged | hidden | an exact rule now resolves; open flags → `superseded` |
| flagged | flagged (other tier or candidate) | signals changed; the row is updated, not duplicated |
| flagged | new | every candidate is gone; flags → `superseded` |
| hidden (automatic) | shown | the act it pointed at was deleted |

**Admin decisions are never overridden** (§5.3). A re-check, a re-import or
`tsg-project --reproject` must leave a `confirmed` or `rejected` pair as it is.

---

## 5. Data model

One migration through `migrate.py` + `migrations/manifest.txt`. Run it on local
**and** Supabase before any dependent push. Regenerate `tests/proc_schema.sql`.

### 5.1 `proc.procurement_act`, hide instead of delete

```
ALTER TABLE proc.procurement_act
  ADD COLUMN duplicate_of text REFERENCES proc.procurement_act(adam) ON DELETE SET NULL;
```

Nullable with no default, so adding it is instant on 2.9M rows. Only
Tender Service acts ever set it in v1. `_remove_projections` stops deleting and
sets `duplicate_of` instead.

Why this matters: deleting today cascades through `digest_run_item` (the emailed
history), `digest_deadline_notice`, favourites, notes, AI summaries and
attachments. It also breaks links that were already mailed or pushed.

### 5.2 `proc.tsg_record`, the decision on the record

| Column | |
|---|---|
| `match_outcome` | `new` / `hidden` / `flagged` / `out_of_scope` |
| `match_rule` | `ext_id`, `esidis`, `quoted_adam`, `quoted_req`, `quoted_ada`, `tsg_twin`, `admin` or NULL |
| `match_tier` | 1–3 or NULL |
| `matched_adam` | the act it is hidden behind or flagged against |
| `matched_at` | when this outcome was set |

`held_adam` stays as it is. It becomes the same value as `matched_adam` when
`match_outcome = 'hidden'`.

### 5.3 `proc.duplicate_candidate`, the review queue

| Column | |
|---|---|
| `id` | bigserial |
| `adam` | the Tender Service act (`TSG:…`) |
| `candidate_adam` | our act, or another Tender Service act |
| `tier` | 1–3 |
| `signals` | jsonb: title score, budget + VAT rate, ref_no value, cpv overlap, n_candidates, guard notes |
| `status` | `pending` / `confirmed` / `rejected` / `superseded` |
| `first_found_at`, `last_seen_at` | |
| `decided_by`, `decided_at` | admin user, NULL while pending |
| `found_by_run` | → `proc.tsg_match_run.id` |

Unique on (`adam`, `candidate_adam`). Both columns cascade on delete.

**Confirm** does four things:
1. sets `duplicate_of` and `match_rule = 'admin'`;
2. marks the other pending rows for that act `superseded`;
3. copies the copy's `digest_deadline_notice` rows onto the kept act (§7.4);
4. records the pair in Interconnection (`interconnect.set_duplicate`) so the group
   shows there.

**Reject:** that pair is never flagged again. The act can still be flagged
against a different candidate.

### 5.4 `proc.tsg_match_run`, one row per matching pass

| Column | |
|---|---|
| `id`, `started_at`, `finished_at` | |
| `trigger` | `tsg-backfill`, `tsg-catchup`, `khmdhs-catchup`, `diavgeia-catchup`, `manual` |
| `job_id` | → `proc.ingest_job`, when admin-launched |
| `counts` | jsonb, see §8.2 |
| `warnings` | jsonb array, see §8.3 |

### 5.5 `proc.ingest_act_log`, extended

Add `matched_adam`, `match_rule`, `match_tier`. New `action` values: `hidden`,
`flagged`, `unhidden`. Tender Service runs launched from the admin page start
writing this log. They do not today.

---

## 6. What a hidden act does

| Surface | Behaviour |
|---|---|
| Search (`build_where`), mobile search, entity pages, exports | excluded: `a.duplicate_of IS NULL`. **Measure the search plans** (the recent perf work) before shipping |
| `/act/TSG:…` | 302 to `/act/<duplicate_of>`, with a one-line notice "Shown as the same tender from another source". 302 because the decision can be undone |
| Sitemaps, canonical | excluded; canonical → the kept act |
| `/digests/<token>`, favourites, notes | still resolve. The page shows the kept act's link next to the copy |
| Alerts | never sent, never reminded (§7) |
| Admin | visible, with an "Unhide" action (clears `duplicate_of`, pair → `rejected`) |

---

## 7. Labels in alerts

A flagged Tender Service act (`match_outcome = 'flagged'`, at least one
`pending` candidate) is sent normally and carries a label.

### 7.1 Wording (i18n catalog)

- el: **Πιθανή διπλοεγγραφή** — ίσως ίδιο με «{title}» ({source}, {date})
- en: **Possible duplicate** — may be the same as "{title}" ({source}, {date})

`{title}` links to the candidate act, and `{source}` is its source badge label.
Only the best candidate is named; if there are more: "+N".

### 7.2 Where the label appears

| Surface | Change |
|---|---|
| Digest, list layout (`email_digest.html`, plain-text too) | label under the act's title |
| Digest, summary layout (`email_digest_summary.html`) | per-type counts add "of which N possible duplicates". Counts stay as they are, with no silent de-duplication |
| Digest, deadline layout (`email_digest_deadline.html`) | label under the act |
| `/digests/<token>` (`digest_results.html`) | label, as it was when sent (§7.3) |
| Push (`notification_worker`) | the event payload carries `possible_duplicate: true` + candidate ADAM; the mobile app shows the label. Push text gets a short suffix: el "· πιθανή διπλοεγγραφή", en "· possible duplicate" |
| Web result card, act page | **not in v1** (§11) |

**Pairs in the same message.** When both acts of a pair are in one message,
only the Tender Service one carries the label. It then reads "same message".
When the candidate went out in an earlier run for that subscription, the label
adds "sent on {date}".

### 7.3 Freeze what was sent

`digest_run_item` gets `dup_candidate_adam` and `dup_tier`, written at send
time, so the history shows the label the customer actually saw, even after an
admin decides.

### 7.4 Deadline reminders

- Hidden acts are never chased. `closing_acts` and the push deadline events
  filter `duplicate_of IS NULL`.
- A flagged pair may be chased twice. Both reminders carry the label.
- On confirm, the copy's `digest_deadline_notice` rows are copied to the kept
  act (same subscription, mark and deadline). A mark already sent for the copy
  does not fire again for the original.

### 7.5 Digest windows

Unchanged. `ingested_at` still decides, and a re-check never moves it. An act
that is hidden later stays in the history of the runs that sent it.

---

## 8. Run logs

### 8.1 Command output

`format_summary` gains a matching block:

```
matching: notices=1653  new=1021  hidden=187  flagged=132  out_of_scope=15
  hidden by rule:  ext_id=0 esidis=24 quoted_req=61 quoted_adam=33 quoted_ada=9 tsg_twin=60
  flagged by tier: 1=58 2=27 3=47   (pending review now: 412)
  re-check:        new→flagged=12 →hidden=19 flagged→new=3 unhidden=0
  by source:       isupplies new=402 hidden=21 flagged=64 | diavgeia … | …
WARNING esidis matched 0 of 23 promitheus notices (usual ≥ 90%) — feed format changed?
```

### 8.2 `tsg_match_run.counts`

Everything in 8.1, keyed by source, so the admin job page can render a
per-source table, and each number links to the filtered list of records.

### 8.3 Warnings, and when they fire

| Warning | Fires when |
|---|---|
| `rule_went_quiet` | a rule that matched ≥ 50% of its source's records over the last 7 runs matches < 10% now (≥ 10 records) |
| `unknown_authority_rate` | > 10% of notices carry an `authorityIdentifier` we do not know |
| `ambiguous_exact` | any record hit guard 1 |
| `exact_far_deadline` | any record hit guard 2 |
| `review_backlog` | pending candidates > 500, or the oldest pending one is > 7 days old |
| `unhidden` | any hidden act came back because its target was deleted |

Warnings are also printed by `cron_catchup.py`, so they reach the Render log.

### 8.4 Per-record trail

`tsg_record` (§5.2) holds the current decision. `duplicate_candidate` holds the
history of each pair. `ingest_act_log` (§5.5) holds what each admin-launched run
did. Together they answer "why is this tender hidden / labelled?" without
reading logs.

---

## 9. Admin review

A new tab on `/admin/interconnect`: **Tender Service**.

- **Queue.** Pending candidates, ordered by the act's deadline (soonest first),
  then tier. Filter by source, tier and authority.
- **Row.** Both titles, both budgets (with the VAT rate that matched), both
  deadlines, signals and the source badges.
- **Actions.** Confirm / Reject / Compare. Compare opens the existing
  `/admin/interconnect/compare` side by side.
- **Workload.** About 400 flags a week (§2.1), roughly 80 per working day.
  Options to shrink it are in §11. v1 adds a filter: **"matches a customer's
  saved search"**, so the flags customers can actually see are reviewed first.

Admin-only. Every confirm, reject and unhide is written to `proc.admin_action`.

---

## 10. Build pieces

| Piece | Where |
|---|---|
| number extraction, signals, tiers (no database) | `tsg_match.py` (new, pure functions) |
| candidate query, `match_record`, `match_open`, run record | `tsg_match.py` |
| call sites | `tsg_ingest.project_record`, `project_all`; `db.py` catchup, diavgeia-catchup, new `tsg-match` |
| hide instead of delete | `tsg_ingest._remove_projections` |
| filters | `app/main.py build_where`, `app/digests.py new_acts / window_stats / closing_acts`, `app/notification_worker.py`, `app/mobile_search.py`, `app/seo.py`, entity pages |
| redirect | `act_detail` in `app/main.py` |
| labels | the three digest templates + plain-text builders, `digest_results.html`, `notification_worker._labels`, OpenAPI schema (`docs/specs/mobile-api-v1.openapi.yaml`) |
| review tab | `app/interconnect.py` + template |
| i18n | `app/i18n_catalog.py` |
| docs | `/help` (beta_help.html), `/data-sources` (add Tender Service), CLAUDE.md section, LOCAL_RUNBOOK |

### 10.1 Tests

- **Pure functions.**
  - number extraction: ΑΔΑ label distance, years excluded;
  - boilerplate stripping;
  - VAT budget equality;
  - tier table, including the round-budget guard.
- **Database-backed.**
  - each exact rule hides, and each guard turns a would-be hide into tier 1;
  - `tsg_twin` keeps the higher-priority source;
  - re-check transitions (§4 table), including an exact twin that arrives
    after a flag;
  - confirmed/rejected survive `tsg-project --reproject`;
  - a hidden act is absent from search, digest windows, deadline reminders and
    sitemaps;
  - `/act/TSG:…` redirects;
  - deleting the target unhides and warns;
  - confirm copies deadline notices, so no second reminder fires;
  - labels render in all three layouts and both languages; `/digests/<token>`
    shows the frozen label.
- **Fixtures.** Real-format records from the week's sample: the 46-notice
  hospital day, the Μ493/Μ511 budget coincidence, a promitheus lot pair, a
  Διαύγεια↔isupplies twin.

---

## 11. Open questions

1. **Confirmed duplicate = hidden.** This was proposed and not objected to;
   confirm before building.
2. **Source priority** for Tender Service twins (§3.3).
3. **Label on the web** result card and act page too, or alerts only? v1:
   alerts only.
4. **Review workload.** About 80 flags a day. Options:
   - review only flags that match a saved search (v1 filter);
   - drop tier 2 from the queue while still labelling it;
   - ask Tender Service for its internal grouping (§2.4).
5. **Results (Αποτέλεσμα).** Apply the same rules in v2? That needs award
   candidates, not notices.
6. **Thresholds** were set from 36 hand-read pairs. Label ~100 pairs before
   production and revisit `tsg_title_min` and the round-budget guard.

---

## 12. Rollout

1. The migration (§5) on local and Supabase.
2. Build and test locally. Run `db.py tsg-match --dry-run` over the loaded week.
   Its counts should match §2.1 within rounding.
3. Hand-label ~100 flags. Adjust the settings.
4. Tender Service goes to production only after this ships. That is a product
   decision (`TSG_INGEST_REMOTE`), and it is when alerts start carrying labels.

---

## 13. As built (2026-09-17)

Where the build differs from the draft above, and what the loaded week showed.

### 13.1 Differences

- **Several notices inside the deadline window hide, not flag.** Guard 1
  ("exactly one notice") is gone. ΚΗΜΔΗΣ often publishes one procedure twice
  (a ΠΕΡΙΛΗΨΗ and the full ΔΙΑΚΗΡΥΞΗ, same ΕΣΗΔΗΣ number, same deadline): 55
  promitheus records a week hit it. Notices outside ±3 days are dropped first,
  and the record hides behind the closest remaining one (then the latest
  published). A re-tender still only flags, because its deadline is elsewhere.
  The info note `several_notices` is counted, not warned.
- **Twins and fuzzy Tender Service candidates come only from a DIFFERENT
  portal.** A hospital's own records share generic titles ("Παραγγελία
  τμήματος 26/0006050 …"): with same-portal pairs allowed, attikonhospital
  got 69 false flags.
- **An exact match seen for the first time is never projected.** There is no
  act row to hide. `/act/TSG:<id>` still redirects (via `tsg_record.held_adam`).
- **The hide filter is an anti-join** (`app/act_visibility.VISIBLE_SQL`), not
  `a.duplicate_of IS NULL`. On a sequential scan the latter unpacks every row to
  its last column: 200 → 600 ms on a filtered count. The anti-join added
  ≤ 10%, measured with 5,000 hidden rows. The unfiltered counter still uses
  the reltuples estimate.
- **Title similarity is computed in Python** (pg_trgm's trigram set over
  folded text), so it does not depend on which schema holds the extension.
- **Review-queue filter.** "Matches a customer's saved search" became
  **"already sent to customers"** (an email item or a push event): cheap, and
  it is what a customer has actually seen.
- **Not built:**
  - the `ingest_act_log` columns (§5.5), because no admin page launches
    Tender Service runs;
  - the label on the web result card and act page (§11.3, alerts only);
  - results matching (§11.5).
- **Two migrations, not one.** `…130323_tender_service_duplicates.sql` is
  the core part the web app reads, independent of the Tender Service tables,
  which production does not have. `…130324_…_tsg_record.sql` holds the
  `tsg_record` columns. Neither uses `DO $$` blocks: the Supabase dashboard
  editor split one and the script failed to parse (2026-09-17). The app reads
  `tsg_record` only through `tsg_match.tsg_tables()`.
- `db.py tsg-match [--dry-run]` re-checks without projecting.
  `ingest.sh … tsg-match` is allowed.

### 13.2 The loaded week, final rules

Notices from sources other than ΚΗΜΔΗΣ, TED and Cyprus: 3,700.

| Outcome | Records |
|---|---|
| Hidden | 946: ext_id 601 (Διαύγεια ΑΔΑ we hold), esidis 136, quoted_req 121, tsg_twin 46, quoted_adam 42 |
| Flagged | 758: tier 1 266, tier 2 357, tier 3 135 |
| New | 2,071 |

- **promitheus:** 154 of 165 hidden. attikonhospital, gpapanikolaou, DEI and
  the school directorates: 0 flags.
- **isupplies:** 526 flags, 346 of them against another Tender Service copy
  (the Διαύγεια Δ.1 twin). Those pairs have near-identical titles but no budget
  on the Διαύγεια side, so they land in tier 2. Under the decision above they
  stay visible and labelled. Tender Service's own grouping (§2.4) would clear
  most of them.
- **Stability:** a second pass straight after (`tsg-match --dry-run`) changed
  nothing.
- **Runtime:** a full `tsg-project --reproject` of 21,000 records takes 7–12
  minutes locally, mostly the existing act upsert.
