# Spec: Competition indicator: how many bids does this buyer usually get?

**Status:** Slice 1 built 2026-10-01 (branch `feat/competition-indicator`):
migration `20261001082952_competition_indicator.sql`, `app/competition.py`,
`tests/test_competition.py`. Slices 2 (TED count) and 3 (fit context) not built.
§9's questions were settled with the recommendations; §12 records where the
build departs from the text above.
**Repo path:** `docs/specs/competition-indicator.md`
**Prompted by:** a source review on 2026-10-01 ("does any source carry bidder
data?"). The answer was that no source names the LOSING bidders, but two of them
give the NUMBER of bids, and we store one of those numbers without using it.

**Decisions proposed (§9 lists the ones still open):**
- **Counts only, never names.** No source we hold names a losing bidder in
  structured form (§2). This spec does not try to recover names from PDFs.
- **KHMDHS is the only source the aggregates use.** TED and Tender Service
  counts are stored and shown per act, but kept out of the aggregates by the
  same allowlist /analytics uses (§3, §6).
- **The measured period is always printed.** The KHMDHS field was only filled
  reliably for ten months. A figure without its period would claim something
  about today that we do not know (§2a).
- **Median and single-bid share, never the mean.** Values reach 29,970 (§4).
- **Not part of the fit score in this slice.** Fit shows it as context with
  zero weight. Weights are argued, there is still no win/loss data, and a new
  component would move every live score (§7d).

---

## 1. Problem

A firm deciding whether to bid wants to know two things the notice does not say:
*how many others will be bidding*, and *does this buyer usually end up with a
single offer*. A buyer whose open tenders mostly attract one bid is either an
easy win or a sign that the specification is written for someone. Either way
the bidder wants to know before spending a week on the file.

We hold the raw material. `proc.procurement_act.bids_submitted` is filled on
268,374 KHMDHS contracts and shown as «Προσφορές» on the act page
(`beta_act.html:212`). Nothing aggregates it, so the one place it appears is
the page of a contract that is already awarded, which is the one place it no
longer helps.

**Goal.** For an authority, and for a CPV division, show the typical number
of bids and the share of single-bid awards, with the sample size and the
period the figures come from. Show it where the bid/no-bid decision is made:
the authority page, the notice page and /analytics.

**Non-goals.**
- Naming competitors on a tender. No source has it (§2).
- Predicting the number of bids on a specific open tender. We describe a
  buyer's history and nothing more.
- Changing fit weights (§7d).
- Recovering bidder lists from Diavgeia PDFs. Measured at 2 in 30 award
  decisions, names only and no ΑΦΜ (§2c). Not worth a model call per PDF.

---

## 2. What the sources hold (measured 2026-10-01, local DB)

| Source | Losing bidders named | Number of bids |
|---|---|---|
| KHMDHS | No. `contractingMembersDataList` holds winners | `bidsSubmitted` per contract, stored as `bids_submitted` |
| TED (eForms) | No. In 39 Greek award notices every `LotTender` was a winning one | `ReceivedSubmissionsStatistics` code `tenders`, per lot, in 38 of 39. **Not stored** |
| Diavgeia | Structured `person` = awardee only | Prose only |
| Tender Service | No. `contractors` = winners | `numberOfOffers` on 68 of 21,078 records, all copied from TED; stored as `number_of_offers` |

### 2a. KHMDHS fill rate: the constraint that shapes everything

Share of contracts with `bidsSubmitted` filled, by month of `contractSignedDate`
(3% sample, n ≈ 400–1,100 per month):

| Period | Filled |
|---|---|
| ≤ 2024-12 | 1–4% |
| 2025-01 … 2025-03 | 14–29% (ramp-up) |
| **2025-04 … 2026-01** | **88–100%** |
| 2026-02 | 13% |
| 2026-03 … 2026-07 | 1–2% |

The KEY is present on every record in every month. The field was not removed.
Authorities simply stopped filling it in, which suggests KHMDHS made it optional
again in February 2026. 243,322 of the 268,374 filled values (91%) fall inside
the ten good months.

Consequence: the indicator describes **April 2025 – January 2026**, and
must say so in words on every surface. If KHMDHS starts requiring it again,
the window extends by itself (§5), and the monitor in §8 is how we notice.

### 2b. Distribution

| Procedure family | Contracts with a count | Single-bid share | Mean (1–100 only) |
|---|---|---|---|
| Απευθείας ανάθεση | 200,041 | 79.0% | 1.49 |
| Ανοιχτή διαδικασία | 44,616 | **51.0%** | 3.55 |
| everything else | 23,717 | 73.5% | 1.96 |

Half of open procedures got one bid. That figure is the product.

Out-of-range values: 1,607 rows are 0 or > 100 (max 29,970). A contract
cannot be signed on zero bids, and three-figure counts on small contracts are
typing errors. These rows are excluded and counted as excluded (§4).

### 2c. Why not names from Diavgeia

30 random 2026 «κατακύρωση» decisions (Δ.2.2), PDFs downloaded and read. Two
listed every bidder: a security-services πρόσκληση with three named firms and a
food tender with four. The other 28 named the winner only. None gave an ΑΦΜ for
a losing bidder. That yield does not justify a model call per PDF, and names
without an ΑΦΜ cannot be matched to `economic_operator` reliably.

---

## 3. Which acts count

An act counts toward an aggregate when ALL of these hold:

- `type = 'contract'`
- `bids_submitted BETWEEN 1 AND 100`
- `proc.is_analytics_eligible(adam, total_cost_with_vat, cancelled)`, the
  same gate /analytics uses: not cancelled, under the value ceiling, not
  flagged suspicious, and the source in the allowlist (khmdhs, manual, NULL).
  That allowlist is what keeps TED and Tender Service out (§6).
- visible (`VISIBLE_SQL`, so hidden duplicates never count twice)

**Competitive vs direct.** Every figure is split by
`procedure_family = 'Απευθείας ανάθεση'` versus everything else. A direct
award reports one bid by construction most of the time, so mixing it in
would turn "79% single bid" into a fact about procedure choice, not
competition. **The headline is the competitive figure.** Direct awards
are shown as a secondary line («Απευθείας αναθέσεις: N, εκ των οποίων X% με
μία προσφορά»), because how much a buyer relies on direct awards is itself
worth knowing.

`procedure_family IS NULL` (1,737 filled rows) counts as competitive.
Those are older acts whose code did not map, and excluding them silently
would be worse. Revisit if that turns out wrong.

---

## 4. The figures

Per group (an authority, or a CPV division), for competitive acts:

| Figure | Definition |
|---|---|
| `n` | contracts counted |
| `median_bids` | median of `bids_submitted` |
| `single_share` | share with exactly 1 bid |
| buckets | share with 1 / 2–3 / 4–6 / 7+ bids |
| period | min and max `contract_signed_date` of the counted rows |
| `n_excluded` | rows dropped for 0 or > 100 (shown to admins only) |

No mean, anywhere. One stray value moves a mean, and a bidder reads a mean
as "about this many".

**Minimum sample.** Below 10 counted contracts the figures are not shown.
The panel says «Δεν υπάρχουν αρκετά στοιχεία» instead. From 10 to 29 they
are shown with «περιορισμένο δείγμα». Measured coverage at those thresholds:

| Group | ≥ 10 | ≥ 30 | total |
|---|---|---|---|
| Authorities (competitive only) | 711 | 430 | 1,274 |
| Authorities (all procedures) | 1,313 | 962 | 1,771 |
| CPV divisions (competitive, ≥ 30) | 45 | | |

### 4a. Storage: histograms, not finished statistics

Authority pages merge entity groups (`resolve_entity_group`), and medians do
not add up. So the precomputed form is a **histogram**, from which any
merge can compute exact figures:

```sql
-- migrations/2026MMDDhhmmss_competition_histograms.sql
CREATE MATERIALIZED VIEW proc.mv_competition_authority AS
SELECT a.authority_id,
       (a.procedure_family IS DISTINCT FROM 'Απευθείας ανάθεση') AS competitive,
       a.bids_submitted::int AS bids,
       count(*)::int          AS n,
       min(a.contract_signed_date) AS first_signed,
       max(a.contract_signed_date) AS last_signed
FROM proc.procurement_act a
WHERE <§3 conditions>
GROUP BY 1, 2, 3;

-- same shape keyed on the 2-digit division, one row per (division, contract):
-- a contract spanning two divisions counts once in EACH (the rule
-- authority_top_cpv already uses), never once per line item.
CREATE MATERIALIZED VIEW proc.mv_competition_cpv AS ...;
```

About 1,800 authorities × 2 × ~20 distinct values, so a few tens of thousands
of rows. Both views are refreshed inside `proc.refresh_analytics()`, so there is
no new job. Unique indexes on the key columns allow `REFRESH ... CONCURRENTLY`.

`n_excluded` comes from a tiny third view or a column on the first, whichever
is simpler at build time. It is an admin figure, not a customer one.

A module `app/competition.py` holds the arithmetic: `summarise(rows) →
{n, median, single_share, buckets, first, last, confidence}`. It is pure,
with no DB access, so tests run on hand-built histograms.

---

## 5. Period wording

Every surface prints the period from `first_signed`/`last_signed` of the rows
actually counted. It is never a constant, so if KHMDHS starts filling the field
again the wording follows on the next refresh:

> «Με βάση 214 συμβάσεις ανταγωνιστικών διαδικασιών, υπογεγραμμένες
> Απρ 2025 – Ιαν 2026, που δήλωναν αριθμό προσφορών.»

The phrase «που δήλωναν αριθμό προσφορών» stays. It tells the reader the sample
is a subset, without a lecture about KHMDHS.

---

## 6. TED and Tender Service

**Slice 2 stores the TED count; no slice aggregates it.**

- `ted_ingest.parse_notice_xml` already walks every `LotResult` (line ~390).
  It reads `ReceivedSubmissionsStatistics` where `StatisticsCode = 'tenders'`
  into a new column `proc.ted_lot_result.tenders_received int`. The other
  codes (`t-sme`, `t-esubm`, `t-oth-eea`…) are kept in that row's `raw_json`
  and nothing more in this slice.
- The TED act page shows it per lot, beside the result status.
- Already-ingested notices get it through the existing re-extract path
  (`lots_extracted_at`). That means one XML fetch per notice, at TED's rate
  limit. The 2026-10-01 probe hit a 429 after about 15 rapid requests, so the
  existing pacing stays.

**Why not aggregated:** a Greek above-threshold award is in KHMDHS AND in TED,
and nothing links the two reliably. Counting both double-counts exactly the
large tenders a bidder cares about most. The analytics allowlist excludes TED
for the same reason. Tender Service `number_of_offers` is a copy of TED's,
so the same rule applies.

**When to revisit:** if KHMDHS stays empty, TED becomes the only forward source,
for above-threshold tenders only. That needs a KHMDHS↔TED link first, which is
its own spec.

---

## 7. Where it shows

### 7a. Authority page: a «Ανταγωνισμός» panel

HTMX panel, mounted like `top-cpv`/`top-contractors`:
`GET /authority/{org_id}/competition` → `_panel_authority_competition.html`.
`_is_gated` → `""`, the same teaser rule as its neighbours. Sums the histogram
over the entity group's member ids.

Content: median bids and single-bid share as the two big numbers, the bucket
bar (1 / 2–3 / 4–6 / 7+), the direct-award line, the period sentence, and
«περιορισμένο δείγμα» where §4 says so.

### 7b. Notice page: one line, for an open notice

Under the authority name on a `notice` act:

> «Αυτή η αναθέτουσα: διάμεσος 2 προσφορές, 48% με μία προσφορά (Απρ 2025 –
> Ιαν 2026).» → links to the authority panel.

Shown only when the authority clears the minimum sample. Nothing at all
otherwise: no "insufficient data" line on every small buyer's notices. Entitled
readers only, the same as the rest of the act's analysis. Computed by
`app/competition.py` from the view in one indexed lookup.

### 7c. /analytics: by CPV division

A table beside the existing by-division table: division, contracts counted,
median, single-bid share. Sorted by single-bid share, highest first, because
"where is competition thinnest" is the question. Divisions under the minimum
are left out. The page's existing "materialised views not created yet" fallback
covers a missing view.

### 7d. Fit: context, zero weight

The CRM Ταίριασμα tab and `/account/fit` show the authority's median/single
share as an extra line under the buyer component. **It is not a component
and has no weight.** Adding one would move every live score with no win/loss
data to argue the weight from. The bid pipeline (`bid_pipeline.py`) is
collecting that data. When it exists, a weighted component is a later decision.

Note the isolation rules: `competition.py` reads acts only, never a profile.
It is called beside `fit.explain`, not from it, so `test_fit.py`'s isolation
tests keep holding.

---

## 8. Monitoring the source

An admin-only line on /admin/collection (Συλλογή Δεδομένων, the ingest tab): fill rate of
`bids_submitted` among KHMDHS contracts signed in each of the last six
months. If the field comes back, the line shows it, and so does the period
sentence on every surface (§5). Nothing alerts automatically in this slice.

---

## 9. Questions (settled 2026-10-01: the owner took every recommendation)

1. **Direct awards: secondary line or hidden?** Proposed: shown as a
   secondary line (§3). The other option is to show competitive procedures
   only and say nothing about direct awards.
2. **Minimum sample 10 / «περιορισμένο» under 30?** Proposed as measured in
   §4. A higher floor hides about half the authorities.
3. **Public teaser?** Proposed: gated like the neighbouring panels, so it is
   not indexed. A single public figure per authority ("48% μία προσφορά") is
   strong SEO and a strong reason to sign up, but it is also a claim about a
   named public body on our public pages. That is your judgement, not a code
   question.
4. **Wording of "single bid".** «μία προσφορά» is neutral. Anything stronger
   («χωρίς ανταγωνισμό») implies a judgement the data does not support: one
   bid on a niche product is normal.

---

## 10. Slices

| Slice | Contents | Migration |
|---|---|---|
| 1 | `competition.py` + the two views + authority panel + notice line + /analytics table + monitor line + /help section | yes (views, added to `refresh_analytics`; that function is currently defined in a hand-applied file, `procedure_family_migration.sql`, so the migration must re-create it in full) |
| 2 | TED `tenders_received`: parse, column, act page, re-extract | yes (one column) |
| 3 | Fit context line (§7d) | no |

Each migration goes through `migrate.py` and `manifest.txt`, local and prod
(`up --only <file>`), before the dependent push. CREATE MATERIALIZED VIEW is
plain SQL, with no `DO $$` blocks.

---

## 11. Tests

- `summarise()` on hand-built histograms: median with an even count, the
  bucket edges (1, 3, 4, 6, 7), and the confidence levels at 9 / 10 / 29 / 30.
- Exclusions: a 0-bid and a 150-bid contract never reach the view. A
  cancelled act, a TED act and a hidden duplicate do not count. A direct award
  lands in `competitive = false`. Acts inserted by the test have a yield-teardown
  fixture, per conftest's rule.
- Entity group: two member authorities merge into one exact median.
- Period: the sentence uses the counted rows' min/max, not a constant.
- Gating: the panel answers `""` for a gated visitor. The notice line is
  absent for a non-entitled reader and for an authority under the minimum.
- Isolation: `competition.py` reads no `company_profile*`/`act_ai_summary`.
  `fit.explain`'s output is byte-identical with and without slice 3.
- TED (slice 2): a fixture XML with `StatisticsCode=tenders` gives
  `tenders_received`. A notice without the block gives NULL, never 0.
- i18n: every new string has an EN entry.
- Schema: regenerate `tests/proc_schema.sql` for the new views/column.

---

## 12. As built (slice 1): departures from the text above

- **The histograms carry a `month` column** (§4a showed first/last dates).
  Measured: a min..max of the counted dates reads "Δεκ 1919 – Αυγ 2026" for the
  largest authority, because a few contracts carry mistyped dates. The period is
  now the months holding the **central 90%** of the counted contracts, and
  the wording says so («9 στις 10 υπογράφηκαν …»). Under 20 contracts nothing
  can be trimmed, so the wording drops "9 in 10" (`trimmed` flag). The views
  also leave out dates before 2020 or in the future (77 rows locally). Size
  with the month column: ~41k rows per authority view.
- **The notice line compares like with like** (§7b said competitive
  figures). A direct-award πρόσκληση is compared with the authority's direct
  awards, everything else with its competitive procedures, and the line names
  which. Quoting open-procedure figures on a direct award answered the wrong
  question. `procedure_family` is only filled by `refresh_analytics()`, so a
  notice imported since the last refresh is classified with
  `proc.compute_procedure_family(procedure_type_code)` on the fly.
- **/analytics hides the table from gated readers** (§9.3), the same as the
  authority panel and notice line. The rest of /analytics is unchanged.
- **`n_excluded` lives in `mv_competition_fill`** (§4a left it open): per
  month, contracts, filled, excluded. The admin monitor reads the last six
  months and the total excluded from it.
- **No GRANT in the migration**: the schema's default privileges give
  `app_runtime` SELECT on relations postgres creates in `proc`, and a GRANT
  guarded by `DO $$` is what the migration rules forbid.
- **`refresh_analytics()` is re-created in full** by the migration, copied
  from the live local definition (identical to the one in
  `20260915200000_cpv_contract_wins_rollup.sql`) plus three lines. Check prod's
  definition matches before applying there, or the replace drops whatever
  prod has that local does not.

## 13. Award pages (added 2026-10-01, same branch, no migration)

The owner asked for bidder information on award pages too. Names are still out
(§2): what an award page can say is its own count and where it sits.

- **One endpoint, three kinds.** `/act/<adam>/competition` now answers for a
  notice (unchanged), a contract and an award decision (`type = 'auction'`).
  `competition.for_act` returns `kind` = `notice` / `contract` / `decision`.
- **Contract:** «Προσφορές σε αυτή τη σύμβαση: N», then the authority's half
  of the same kind (competitive / direct) and ONE comparative sentence taken
  from the histogram (`competition.compare`): below the median it quotes the
  share of the authority's contracts that got MORE bids, above it the share
  that got FEWER, at the median «Όσες η διάμεσος». Never "typical is N" — the
  KHMDHS-wide median is 1 even for open procedures (per-lot contracts).
  Under MIN_SHOW the count stays and the comparison is replaced by «δεν έχει
  αρκετές συμβάσεις για σύγκριση». A count outside 1..100 shows no line (the
  bare value is still in the facts list).
- **Award decision:** KHMDHS reports no count on it. Measured locally: 0 of
  728k decisions carry one; 61k link to a contract that does. The line reads
  the linked contracts (`auction_to_contract` from the decision OR
  `contract_from_auction` towards it), not cancelled, not hidden duplicates,
  bids 1..100, and names each (max 10, then «και N ακόμη»). It compares only
  when all of them agree on one count AND one kind of procedure; lots that
  disagree are listed, never merged.
- **Every award line says why there are no names:** «Οι πηγές δημοσιεύουν
  μόνο τον αριθμό των προσφορών και τον ανάδοχο, όχι τους υπόλοιπους
  διαγωνιζόμενους.»
- The contract being viewed is itself inside the authority's histogram when
  it falls in the period. Not removed: one contract in ≥10, and removing it
  needs a second read per page.
- Same gate and isolation as the rest: subscribers only, acts only.
