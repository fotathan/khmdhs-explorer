# Spec: Tender Service as the single data source (side project)

**Status:** Draft 2026-10-02. **Phase 1 (setup) done 2026-10-02** (§9a); phases
2–6 not started. Open questions in §11.
**Repo path:** `docs/specs/tender-service-single-source.md`
**Prompted by:** "if we can get all acts via the Tender Service API, can we drop
every other source?" The answer is yes for most of the product. This spec says how
to try it **without touching what runs today**, what we would lose, and what has to
be measured before anything is switched.

**Decisions already taken by the owner (2026-10-02):**
- Tender Service becomes the backbone: notices, results, awards, contracts and
  payments, back to 2013, backfilled gradually starting from the most recent.
- Publication delay, legal-record provenance and republishing rights are not
  concerns (same company).
- `referenceNumber` is the link that builds the chain (request → notice → award →
  contract → payment).
- Contractors and authorities are matched by VAT number **and** the Tender Service
  ID. Whether the two can disagree is left for later ("we'll see", §11).
- **Sole traders are one contractor, «Φυσικά Πρόσωπα»**, with no name or ΑΦΜ
  stored, as Tender Service already does. This also applies to the data we already
  hold (§6c).
- Sole-trader **customers get no fit scoring**. GDPR safety comes before the
  feature.
- «Φυσικά Πρόσωπα» is **left out of every top-N contractor list**. One line under
  each list shows their total, «Φυσικά πρόσωπα: N αναθέσεις, € X», so the list
  adds up to the overall total.
- **Nothing we have today is lost.** The trial runs beside production, not instead
  of it (§2).

---

## 1. What Tender Service gives that we would otherwise miss, and what it doesn't

### 1a. Gains
- One feed instead of four ingesters (KHMDHS, Diavgeia, TED, Tender Service), and
  most of the duplicate machinery (`tsg_match.py`) becomes unnecessary.
- History to 2013: **27.5M records** for our key (§5a). We hold ≈3M acts today.
- Result documents that list **several** contractors, plus a separate award or
  contract per contractor. Today's ingester links no contractor on a joint award.
  That is our gap, not theirs: we never collected those documents.
- `numberOfOffers` on KHMDHS-origin records. Our own bid counts were only reliable
  2025-04 … 2026-01 (competition-indicator.md §2a). If Tender Service fills it for
  other years, the competition indicator gains years.
- Sources we don't ingest at all (e.g. isupplies.gr, promitheus, manual entries).

### 1b. What KHMDHS has and Tender Service (as delivered today) doesn't

**Tender items. This is the big one.** Measured on the 2026-08-04 probe sample
(`runs/tsg-probe-day-20260804`, 90 records):

| | KHMDHS (what we store) | Tender Service `tenderItems` |
|---|---|---|
| Present on | every KHMDHS act that has items | 31 of 90 records, and only those whose `dataSource` is KHMDHS (`eprocurement-gov-gr`) |
| Shape | one row per line: `act_object_detail` (3.7M rows) | one **text** block, one line per item: `description 1,00 Τεμάχιο` |
| CPV per line | yes: `object_detail_cpv` (4.4M rows) | no. CPVs only at record level (`cpvCodes`) |
| Cost per line, VAT rate | yes | no |
| Quantity + unit | yes, coded | inside the text, as `1,00 Kg` |
| Delivery place, budget code, green-contract code | yes | no |

What depends on item-level data today:
- **Search by CPV and by category** (`main.py` build_where): an act matches through
  its **line-item** CPVs. With record-level `cpvCodes` this still works, but it has
  to read a different table (§5d).
- **Fit scoring** (`fit.py`) and **entity pages** (`entity_service.py`) weigh CPVs
  by line cost. Record-level CPVs give "the firm did 33141" but not "€80k of the
  €100k was 33141".
- **The act page's item table** and the admin line-item corrections.

Other KHMDHS-only fields to check one by one (§8):
- coded fields (`contract_type_code`, `procedure_family`; Tender Service gives
  display labels)
- funding sources
- ΕΣΗΔΗΣ systemic numbers
- centralised-market flags
- additional contract types
- the KHMDHS-specific `raw_json`

### 1c. API fields we don't use yet

Measured 2026-10-02 on the 21,078 records already stored. They are notices and
results from September 2026, so fields that only appear on contracts, payments
or older records aren't seen here.

**Not anywhere in the app today:**
- `tenderAttachments`: links to the tender's **documents**, on ~98% of non-TED
  records. Today only an admin can attach a document by hand.
  **Not downloadable with the API key as tried on 2026-10-02:** the link
  (`www.promitheies.gr/tender/<uuid>/downloadAttachment/<id>/name/…`) answers
  with the website's login page, with or without `X-API-Key`. On hold: the owner
  checks the access route in the week of 2026-10-05. Until then the links are
  stored in `tsg_record` only, never shown or fetched.
- `systemExpirationDate` (planned, §5e) and `authorityId`, the Tender Service id
  (planned, §6b).
- `realisationAddresses` + `realisationAddressesNuts`: the place of
  performance with its NUTS (we keep only the first `nutsCodes` code and a city).
  `region` is on every record.
- `inconsistentSourceData`: Tender Service's own "the source contradicts
  itself" flag (84 records).
- `dataSourceToShow` (display name of the source); `tenderAnalyzerAvailable`
  (a Tender Service feature, not useful to us).

**We have the column, but the Tender Service ingester leaves it empty:**

| API field | Filled on | Our column |
|---|---|---|
| `awardingCriteria` | 56% | `assign_criteria_label` |
| `regulationOfProcurement` | 49% | `regulation_of_procurement` |
| `contractDurationInMonths`, `contractEndDateInternal` | 50%, 24% | `contract_duration`, `end_date` |
| `prolongationOption` | 23% | `prolongation_option` |
| `bidBond` | 3% | `bid_bond_amount` |
| VAT rate / VAT included, EUR / USD amounts | 24–53% | `vat_rate`, `vat_included`, `value_eur`, `value_usd` |
| `eprocurementPortal`, `priceWeighting`, `typeOfBidRequired`, `contractAwardDate` | rare | same-named columns |
| `authorityEmail` / City / Zip / Street / Phone | 91% / 54% / 51% / 49% / 9% | `authority` contact columns |
| `contractorOrgdbId` | 30% | `economic_operator.orgdb_id` |
| contractor address / email / phone | <1% | `economic_operator` contact columns |
| `contractorOrgRoles` | one `WINNER` per line | shows which records list several winners (§6) |

TED-only extras (`tenderCategories`, `mainActivities`, `originalLanguages`,
`sendDate`) are already covered by our TED ingester.

**`numberOfOffers` is a surprise:** 0 of 10,288 KHMDHS-origin records here.
Only 68 TED results carry it. It may live on the contract documents, which we
haven't walked yet. Phase 2 checks it before the competition indicator plans
anything on it (§7, §11 Q9).

**So the realistic end state is probably "Tender Service + KHMDHS items", not
"Tender Service only".** Unless Tender Service holds structured items internally
and can expose them (§11 Q1), KHMDHS stays as a **detail source**: its
ingester keeps fetching the items of the acts we already know, joined by ΑΔΑΜ.
It stops being the source of acts. The trial in §2 is how we find out which of
the two it is.

---

## 2. How to run this without losing what we have

The risk is in the **data**, not the code. A git branch alone doesn't protect
anything, because a branch running against the same database would rewrite the
same tables. The trial gets **its own database, its own branch and its own local
server**. Production and the current local database are never touched.

| Piece | Today (stays as is) | Trial |
|---|---|---|
| Database | `procurement` on 127.0.0.1:5433 | **`procurement_tsg`**, a new database on the same local Postgres |
| Code | `main`, deployed to Render | branch **`exp/tsg-single-source`**, never deployed |
| Server | port 8012 (`.claude/launch.json`) | a second launch entry on **port 8013** with `DATABASE_URL=…/procurement_tsg` |
| Ingesters | KHMDHS / Diavgeia / TED daily | Tender Service only (+ optionally KHMDHS items, §1b) |
| Prod (Render + Supabase) | unchanged | **not involved at all** |

Rules for the trial:
1. **The app code stays one code base.** The trial changes **ingesters, migrations
   and a few queries**, not features. Each such change should work on both
   databases, so it can later be merged into `main` piece by piece and the branch
   never drifts far. `main` is merged into the branch regularly.
2. **Migrations go through `migrate.py` as usual, only on `procurement_tsg`.**
   They are only added to prod's run list if the trial is adopted.
3. **User data.** To test alerts and saved searches realistically, the trial
   database gets a **copy** of the local user tables: accounts, saved searches,
   subscriptions, favourites. `EMAIL_BACKEND=file` so nothing is mailed.
4. **Side by side.** Both servers run at once, so the same search can be compared
   in two browser tabs. The comparison report (§8) is a script that reads both
   databases.
5. **Disk.** The current database is 23 GB for ≈3M acts (`procurement_act` alone
   is 18 GB, mostly full text). 27.5M records means roughly 150–200 GB. The local
   disk has 416 GB free, so that's fine locally. **Production hosting for that
   size is a separate decision (§10), made only if the trial is adopted.**

What ends the trial is one of three decisions (§10):
- **adopt**: Tender Service only
- **hybrid**: Tender Service + KHMDHS items
- **stop**: delete `procurement_tsg`, nothing else changes

---

## 3. Identity: what an act is called

Every customer-facing thing is keyed by the act's id: `/act/<id>` URLs,
favourites, alert history (`digest_run_item`), checklist ticks, calendar UIDs,
SEO pages. So the key rule is:

| Tender Service record | Our key |
|---|---|
| `externalId` is a ΑΔΑΜ (`ADAM_RE`: `26PROC…`, `REQ`, `AWRD`, `SYMV`, `PAY`) | **the ΑΔΑΜ** (same as today) |
| `externalId` is a Diavgeia ΑΔΑ (with Tender Service's timestamp suffix stripped, `ada_of`) | the key `diavgeia_ingest.py` gives it today (the bare ΑΔΑ) |
| TED (`ted_of`) | the key `ted_ingest.py` gives it today |
| anything else | `TSG:<internalID>` (as today) |

Because Tender Service holds every KHMDHS act, existing ids survive and no
customer data has to be re-pointed. **To verify in the trial:** the share of our
KHMDHS acts that come back with their ΑΔΑΜ (§8, target ≈100%). Every act that
doesn't is a favourite or an alert link that would break.

`proc.tsg_record` keeps the raw record for every key, as it does now.

---

## 4. The chain from `referenceNumber`

Today `act_link` (3.8M rows) is filled from KHMDHS's own references, and the
root-anchored `WITH RECURSIVE` chain query reads it. **The query does not change.**
Only the ingester writes `act_link` rows, from `referenceNumber`:

- a notice's `referenceNumber` = its request (already known, `tsg_ingest.held_keys`)
- a result/award/contract/payment's `referenceNumber` = the act it follows.
  **The owner to specify which document type points to which (§11 Q2).**
- a reference to a record we don't hold yet is stored and resolved when that
  record arrives. Backfill runs newest-first, so most links arrive before their
  target.

**Check in the trial:** for acts in both databases, the chain built from
`referenceNumber` should equal the one built from KHMDHS. Count missing and extra
links by type.

---

## 5. Collection

### 5a. Backfill (history)
Newest first, one publication day at a time, by document type, **always with
`status=ACTIVE_AND_EXPIRED`** (§5e). Today's ingester walks every day twice, once
per status (`PUBLICATION_SLICES`); the trial replaces that with one walk. The new
types (contract, payment, per-contractor award) are added as slices. Resumable;
only a day with status `done` is skipped.

**What our key sees** (measured 2026-10-02, `status=ACTIVE_AND_EXPIRED`, by
publication year). It is 27,514,538 in total, the same number Tender Service's
own search shows for that profile.

| Year | Records | Year | Records |
|---|---|---|---|
| 2012 | 324 | 2020 | 2,226,903 |
| 2013 | 266,999 | 2021 | 2,245,161 |
| 2014 | 658,068 | 2022 | 2,470,191 |
| 2015 | 1,014,789 | 2023 | 2,778,029 |
| 2016 | 1,657,092 | 2024 | 2,677,561 |
| 2017 | 1,804,058 | 2025 | 2,881,026 |
| 2018 | 2,333,362 | 2026 (to 1.10) | 2,128,352 |
| 2019 | 2,372,613 | | |

Without a status filter the API returns only 398,957 (the ACTIVE ones).

**The backfill must walk publication days, never update days.** Only 7,180,429
records carry a `lastUpdated` at all. The ~20M older ones have none and are
invisible to an update filter.

**Budget:** 27.5M at 10 per page ≈ **2.75M requests**. At the rate limit
(300 / 10 min = at most 43,200 a day, if no daily cap applies) that is
**≥ 64 days** of continuous walking. Newest first, so the useful years arrive
first. A recent year is ~2.8M records ≈ 280k requests ≈ a week. The daily cap
on our key (set by the owner) decides the real pace.

### 5b. During the day: the update filter

The API filters by `lastUpdated_from` / `lastUpdated_to`, **by day only**
(format `dd.MM.yy`, `_to` exclusive). The catch-up already uses it
(`UPDATED_SLICES`). **Measured 2026-10-02:**

| Query (ACTIVE_AND_EXPIRED, all types) | Total |
|---|---|
| `lastUpdated` 01.10.26 → 02.10.26 | 27,549 |
| `lastUpdated_from` = `01.10.26 12:00` (or `01.10.26T12:00:00`) | **7,180,429**: the time makes the filter be **ignored**, and the answer is the whole archive the key can see |

Records changed and records published per day, the week before:

| Day | Updated | Published | Updated − published ≈ old records re-touched |
|---|---|---|---|
| Thu 25.09 | 20,759 | 10,766 | ~10,000 |
| Fri 26.09 | 14,780 | 177 | ~14,600 |
| Sat 27.09 | 9,456 | 296 | ~9,200 |
| Sun 28.09 | 13,162 | 11,564 | ~1,600 |
| Mon 29.09 | 20,125 | 11,894 | ~8,200 |
| Tue 30.09 | 21,870 | 13,369 | ~8,500 |
| Wed 01.10 | 27,549 | 18,172 | ~9,400 |

(The "published" day and the "updated" day are not the same records, so the last
column is an estimate.)

What follows:
1. **Never put a time in a date filter.** It silently returns everything. A
   test pins the date format; the ingester refuses any total over a sanity
   limit (like `gemi_client.REGISTRY_GUARD` does for ΓΕΜΗ).
2. **Without a time filter, every poll re-reads the whole day.** One pass over a
   day of 10k–28k changes costs **1,000–2,800 requests** at 10 per page, and
   at 300 requests / 10 min that is up to ~95 minutes. Four polls a day would be
   4×. This is the cost of "frequently during the day" today.
3. **A busy day is over the 10,000 offset cap** (27,549 on 01.10, and 18,172
   published the same day). An update walk must be sliced by `typeOfDocument`
   too, and further (e.g. `dataSource`) if one type still passes 10,000;
   `over_cap` is recorded, never truncated.
4. **Re-touched old records are real changes**: corrections, and new
   documents or attachments (owner, 2026-10-02). They are often more than the
   new records, and on Fridays and Saturdays nearly all of the volume. We want
   them. `content_hash` keeps a record whose content didn't change from being
   rewritten, and they never move `ingested_at`.
5. **New records always carry `lastUpdated`.** Of 18,172 records published on
   01.10, 12,079 have `lastUpdated` 01.10 and the other 6,093 were already
   changed again on 02.10. None had an earlier date. So the update walk of a
   day also brings in that day's new records, and a record changed the next
   day is caught by the next night's walk.

**The API stays as it is** (owner, 2026-10-02). No time filter, no sorting, no
larger page will be requested. The trial is designed around that.

**How the trial polls:**
- **during the day:** the **publication** day only (new records, 0.2k–18k)
- **once after midnight:** the full **update** walk of the previous day. It
  catches every change, and the day is closed so the walk is stable.

A poll never moves `ingested_at` of an act it already holds, so alert windows
stay correct.

### 5c. Hard limits to keep
- A query stops at offset 10,000. A day + type slice over that is recorded
  `over_cap`, never truncated silently. If busy days hit it, add one more slicing
  dimension (e.g. `dataSource`).
- `_to` is exclusive; unknown parameters are ignored rather than rejected
  (re-checked by `tsg_probe.py`); the daily cap shows only as
  `customer_api_limit_reached` in a 400 body.

### 5d. CPVs at act level
Record-level `cpvCodes` go into `proc.act_cpv` (it exists, 0 rows today). Search,
category, fit and entity queries read **"item CPVs if the act has items, else
`act_cpv`"**, behind one SQL helper so the rule is written once. In hybrid mode,
KHMDHS acts keep their item CPVs; the rest use `act_cpv`.

### 5e. ACTIVE / EXPIRED

Every record is either `ACTIVE` or `EXPIRED`. Tender Service moves it from
ACTIVE to EXPIRED when its internal end date arrives. The API exposes that date
as **`systemExpirationDate` = the internalEndDate** (confirmed by the owner,
2026-10-02). The system sets it automatically (notice: deadline day; result:
publication + 62 days), but **it can be changed by hand**, so the date itself
is what counts, never a rule recomputed by us. The switch to EXPIRED is a
**once-a-day run after the following midnight** (owner; matches the
measurement below).

**Measured 2026-10-02** (7 API requests, plus the 21,078 records already stored):

| | Result |
|---|---|
| `status` filter, TENDER published 2026-09-01 | none → **88** · ACTIVE → **88** · EXPIRED → **1,451** · ACTIVE_AND_EXPIRED → **1,539** (= 88 + 1,451) · a made-up value → **0** |
| `systemExpirationDate`, notices | the deadline day: same day 6,164, next day 2,661 (a deadline at 22:00 expires at the next midnight) |
| `systemExpirationDate`, results («Αποτέλεσμα») | publication + **62 days**, all 12,249 |
| When the switch happens | notices fetched 8–13.5 h after their expiry midnight were still ACTIVE (1,085), and EXPIRED from 32 h on: the once-a-day run after the next midnight. |
| Old records are re-touched | 2,002 EXPIRED notices had `lastUpdated` = 2026-09-30; the sampled ones were published in **March 2018** |

What this means for the trial:
1. **No filter means ACTIVE only.** A walk without `status` silently drops
   everything closed: for a day 4 weeks back, 94% of the notices. Every query
   names a status. A test pins that no slice is built without one.
2. **One walk with `ACTIVE_AND_EXPIRED`, never two.**
   - It halves the requests.
   - It removes the reason today's ingester marks days `incomplete`: a record
     switching status in the middle of a walk moved from one filtered set to the
     other and was missed by both.
   - The 10,000-offset cap now applies to the combined total. Measured days are
     1.5k–2.7k per type, far below it; `over_cap` still guards it.
3. **A wrong value returns 0, not everything.** A day whose whole walk returns
   0 is recorded as suspect, not `done` (a spelling change in the API would
   otherwise empty the trial silently).
4. **Expiry comes from `systemExpirationDate`, not from `status`.**
   - It is stored as its own column on the act (`expires_at`).
   - "Expired" is computed when we read it: `expires_at` has passed (Athens
     time). That makes it right from the first minute, while `status` lags up to
     a day, and our stored copy of `status` may never be refreshed if the
     nightly switch doesn't touch `lastUpdated`. Measure this in the trial
     (§8.9).
   - A hand-changed date reaches us like any edit, through the update walk.
   - `source_status` stays as information only.
   - The **submission deadline** (`final_submission_date`) stays what customers
     see and what "closes within N days", reminders and the calendar use.
     `expires_at` is Tender Service's own end of life for the record; for a
     notice it is the same day.
5. **The catch-up sees history changes.** Tender Service re-touches old records
   (2018 notices updated on 2026-09-30). The `lastUpdated` walk pulls them in,
   they cost requests, and `content_hash` keeps unchanged ones from being
   rewritten. They must never move `ingested_at` (alerts would mail 2018).
6. **Backfill of a past day is stable; today is not.** A day whose records have
   all expired won't change status again, so `done` is safe. For the last ~62
   days (results) the status still moves, but under ACTIVE_AND_EXPIRED that no
   longer changes what the walk returns.

---

## 6. Authorities and contractors

### 6a. Identifiers (meanings to be confirmed, §11 Q3)
Seen in the probe:
- authorities: `authorityId` (Tender Service id), `authorityIdentifier`,
  `authorityOrgdbId`
- contractors: `contractorVatUid`, `contractorStatisticalOrTaxNumber`,
  `contractorOrgdbId`
- address and contact fields on both

### 6b. Matching and enrichment
1. Match by **VAT number** first, then by **Tender Service id**. The id is stored
   on our row (new column `tsg_id` on `authority` and `economic_operator`), so the
   next import matches directly.
2. **Fill only empty fields; never rename** an existing row (`leads.fill_if_empty`,
   the rule the lead importer and the ΓΕΜΗ match already follow). An admin's edit
   survives every import.
3. Two of our rows with the same VAT number or `tsg_id` are **listed for review**,
   never merged automatically (they are linked from millions of acts).

### 6c. Natural persons → «Φυσικά Πρόσωπα»
One `economic_operator` row named «Φυσικά Πρόσωπα», with no ΑΦΜ. Every sole-trader
award links to it.

**Rule, measured on 119,401 contractors with a 9-digit Greek ΑΦΜ (local DB,
2026-10-02):**

| ΑΦΜ starts with | Rows | Name has a legal form (Α.Ε., ΙΚΕ, Ο.Ε., «ΚΑΙ ΣΙΑ», …) | Name in KHMDHS person format «ΕΠΩΝΥΜΟ,,ΟΝΟΜΑ,ΠΑΤΡΩΝΥΜΟ» |
|---|---|---|---|
| 08, 09, 80, 99 | 42,020 | 79% | 3 |
| 00–07, 1 | 75,305 | 1% | 7,331 |
| 3 | 1,577 | 2% | 67 |
| 2, 4–7 | 499 | 39% (foreign firms, odd entries) | 4 |

**Person =** ΑΦΜ starts with 00–07, 1 or 3 **and** the name has no legal form.
The ~1% excluded are old companies that got a personal-range ΑΦΜ, e.g.
«ΑΡΗΤΗ Α.Ε.» 029…. Where the rule is wrong, it errs on the safe side: an old
company with no legal form in its name gets folded in, which costs us a company
name, never a person's data.

Contractors with no 9-digit ΑΦΜ (16,251) are classified only by the person name
format.

**Prefer Tender Service's own rule if it has one (§11 Q6).** Both systems should
split people from companies the same way.

**Existing data** (in `procurement_tsg` first; in prod only on adoption):
re-point every `act_operator` / `act_contractor` link from a person row to
«Φυσικά Πρόσωπα», then delete the person rows. Also:
- `name_original`, contact fields and `ar_gemi` go with them
- ΓΕΜΗ enrichment rows (`gemi_enrichment`) for those ΑΦΜs are deleted
- the ingester's packed-name reassembly (`_winner_name`) is no longer needed for
  storage

### 6d. Where «Φυσικά Πρόσωπα» shows up
- **Top-N contractor lists** (analytics, authority pages, CPV pages): left out,
  with one line under the list: «Φυσικά πρόσωπα: N αναθέσεις, € X».
- **Totals**: included.
- **Act page**: shown as the contractor «Φυσικά Πρόσωπα», not a link to a profile.
- **Search by contractor**: not offered for it.
- **Fit / bid pipeline / company match / onboarding**: a customer whose ΑΦΜ is in
  the person range gets no ledger. Fit is off for them. The CRM company-match
  panel says why instead of offering a link.

---

## 7. What changes in features

| Feature | Change |
|---|---|
| Search, saved searches, alerts, deadline reminders, calendar | none; they read `procurement_act` + `ingested_at` |
| CPV / category filter | reads item CPVs or `act_cpv` (§5d) |
| Act page | item table only where items exist; otherwise CPV list + text |
| Chain / interconnect | same query, links from `referenceNumber` (§4) |
| /analytics, `mv_analytics_cpv` | the source allowlist (`is_analytics_eligible`) becomes "Tender Service + manual". Every published figure shifts once; compare before/after (§8). |
| Competition indicator | `bids_submitted` ← `numberOfOffers`. The period is still computed from the data, never a constant. |
| Fit scoring | record-level CPVs where no items exist (value weighting weaker); off for sole traders |
| Duplicates (`tsg_match.py`, review queue) | only Tender-Service-internal twins remain |
| Coded filters (contract type, procedure) | a label → code map, built from the trial data; every unmapped label is listed |
| Glossary, /data-sources, /ai, /help | text updated on adoption (sources named) |

---

## 8. Measurements the trial must produce

A script reads both databases for the same period (start with one recent month,
then one month from each earlier year) and writes one report:

1. **Coverage**: our KHMDHS / Diavgeia / TED acts per day and type vs Tender
   Service records that carry the same key. Missing and extra, by type.
2. **Identity**: % of our KHMDHS acts that come back with their ΑΔΑΜ (§3).
3. **Chain**: links from `referenceNumber` vs KHMDHS links, by type pair (§4).
4. **Items**: % of acts with `tenderItems`; what a structured parse of the text
   recovers (quantity, unit) vs `act_object_detail`.
5. **Field fill**: per field (`numberOfOffers`, values, deadlines, CPVs, NUTS,
   contractor VAT, authority ids), % filled on each side, and % that agree.
6. **Contractors**: joint awards, i.e. how many contractors per result vs ours;
   VAT-vs-id conflicts (one VAT with several ids, one id with several VATs).
7. **Statistics**: the /analytics headline figures and the competition medians
   on both databases.
8. **Cost**: requests per day for backfill and for each intra-day poll; database
   size per million records.
9. **The nightly switch**: for records whose `systemExpirationDate` passed, does
   the next update walk bring them back with `status = EXPIRED` (i.e. does the
   switch touch `lastUpdated`)? And how many re-touched old records change
   anything we store (`content_hash` differs) vs nothing at all.

---

## 9. Phases

1. **Set up**: `procurement_tsg`, branch, second launch entry, user-table copy.
   No feature work.
2. **Ingest one month** with today's ingester switched to "Tender Service is the
   source" (§3 keys, all document types). Run the coverage and identity
   measurements (§8.1–2). **This phase alone answers whether the idea holds.**
3. **Chain + parties**: `referenceNumber` links (§4), the multi-contractor
   documents, VAT + id matching, «Φυσικά Πρόσωπα» (§6). Measurements §8.3, §8.6.
4. **Act-level CPVs and statistics** (§5d, §7). Measurements §8.5, §8.7.
5. **Backfill** newest-first as far as the cap allows. Measurement §8.8.
6. **Decision** (§10).

Tests ship with every phase, as always (`tests/`, against a throwaway DB). The
natural-person rule gets its own test with real-shaped ΑΦΜs and names.

### 9a. Phase 1 as built (2026-10-02)
- **Branch** `exp/tsg-single-source` (from `main` @ 6a4ab51), checked out in its
  own folder `../KHMDHS-tsg` (git worktree), so `main` stays where it is.
- **Database** `procurement_tsg` on the local Postgres (127.0.0.1:5433):
  - the full schema of `procurement` (140 tables/views, 329 indexes,
    `schema_migration` → `migrate.py status`: 110 applied, 0 pending)
  - copied, row counts checked one by one:
    - reference data: code lists, CPV, NUTS, postal NUTS, units, categories,
      products, match rules, freemail domains, holidays, entity groups
    - registries: `authority`, `org_unit`, `economic_operator` (145,719),
      `gemi_enrichment`
    - user side: accounts, subscriptions, CRM rows, saved searches, digest
      schedules/subscriptions/recipients, email templates, calendar feeds,
      onboarding, company profiles, certificates
    - **every Tender Service record already downloaded** (`tsg_record` 21,078 +
      `tsg_ingest_window`), so phase 2 doesn't re-spend quota on those days
  - **not copied:** every act and act-detail table, Diavgeia, TED, KHMDHS ingest
    logs, AI summaries, digest history, mobile/push/telephony, login links.
  - Favourites, checklist ticks and digest history point at acts, so they are
    copied **after** phase 2, only for acts that exist in the trial.
  - Id counters were moved past the copied rows; materialized views populated
    (empty).
- **Server** `khmdhs-tsg-trial` in `.claude/launch.json`, port **8013**, running
  the branch folder against `procurement_tsg`. Switched off there:
  - `DIGEST_SCHEDULER` (no alert runs until we test them on purpose)
  - `AI_SUMMARY_ENABLED` (no model spend)
  - `ATTACHMENTS_ENABLED`, `SEO_INDEX`

  Mail goes to `../KHMDHS-tsg/outbox` only.
- Same browser host as 8012, so the login cookie is shared. The user ids are the
  same in both databases, so that is harmless.
- Size: 432 MB before any acts.

### 9b. Phase 2: collection started 2026-10-02

**September 2026 = 249,373 records** (`ACTIVE_AND_EXPIRED`), about 25,000 requests.

The 14 document types, measured on 01.10 (18,172 records, the types sum to
18,170):

| Type | Records |
|---|---|
| PRIOR_INFORMATION | 9,209 |
| PAYMENT_ORDER | 2,761 |
| RESULT | 2,652 |
| TENDER | 1,892 |
| CONTRACT | 1,451 |
| CORRECTION | 81 |
| OTHER_INFORMATION | 54 |
| CANCELLATION | 52 |
| PROCUREMENT_PLAN | 10 |
| CONSULTATION | 8 |
| CORRECTION_CANCELLATION, BUDGET, DECISION, LOT | 0 |

An invalid type value (`PAYMENT`) is a **400**, so the enum list in
`tsg_ingest.DOCUMENT_TYPES` (from ConnectContractors' schema) is the only one used.

**Code on the branch:**
- `tsg_ingest.py`:
  - `backfill_single_source`: newest day first; one `pub:all` walk per day; a day
    over the offset cap is split into the 14 `pub:type:*` windows, and the
    records outside the types are reported as a gap
  - a whole empty day is not believed (`error`, walked again)
  - `TsgClient(min_interval=…)` paces requests
  - a 429 waits for the full reset (≥ 60 s); four 429s in a row stop the run
  - resume uses min/max days
- `db.py tsg-backfill --single-source --min-interval`.
- Tests: `tests/test_tsg_ingest.py` (11 new; full suite green).

**The run:** `runs/phase2_september.py` (git-ignored), started detached,
PID in `runs/phase2.pid`, log `runs/phase2_september.log`.
- 4 s between requests (150 / 10 min, half the key's limit)
- at most 10,000 requests per calendar day, then a pause until 00:10
- stops when all 30 days are complete
- touching `runs/phase2.stop` ends it cleanly at the next check
- expected: about 3 days

**Built offline (no requests), 2026-10-02:**
- **Projection**: `db.py tsg-project --single-source`
  (`tsg_ingest.project_single_source`).
  - Keys per §3. The ΑΔΑΜ kind sets the act type
    (REQ/PROC/AWRD/SYMV/PAY → request/notice/auction/contract/payment);
    otherwise the label decides, and unknown labels are counted.
  - Two records with one key: the newest publication keeps it, the other gets
    `skip_reason = 'same key … as …'`.
  - No duplicate matching.
  - About 30 ms per record, so ~2 h for the full month: run it after the
    collection, not during.
  - The 21,078 records copied from `procurement` need `--reproject` once:
    they carry that database's projection hash.
- **Report**: `tsg_coverage.py --start … --end … --out runs/…md`, read-only on
  both databases.
  - It compares only days that both sides hold completely, per
    (source, type).
  - Our local KHMDHS awards, contracts and payments stop at 2026-08-04, and its
    notices and requests at 2026-09-16. So September compares only requests and
    notices (1–16.09) for KHMDHS, plus Diavgeia and TED up to their last
    ingest. A type with no common days is reported as "no overlap", not as
    missing.
  - To compare awards/contracts/payments, either catch our local KHMDHS up
    first or add a July month to the trial.

### 9c. Analytics switched on for the trial (2026-10-02)
- Migration `20261002220000_analytics_sources_switch.sql`:
  - The allowlist becomes `proc.analytics_sources()` = {khmdhs, manual}, plus
    tsg **only where the database sets `khmdhs.single_source = 'on'`**.
  - `is_analytics_eligible` and `mv_analytics_cpv` read it.
  - Still an allowlist, and identical results wherever the setting is absent.
  - Applied to `procurement_tsg` only, with `migrate.py up --only`. **Not on
    `procurement`, not on Supabase.**
- `ALTER DATABASE procurement_tsg SET khmdhs.single_source = 'on'`, then
  `refresh_analytics()`, then restart the trial server (the setting applies to
  new connections).
- The competition views keep their own `khmdhs` filter: they need
  `bids_submitted`, which Tender Service doesn't fill.
- Seen on the first data, to check in phase 4:
  - **CPV 22** (printed matter) showed €3.7B from 361 notices. Checked
    2026-10-02: not a parsing problem, the same money counted several times.
    - **(a)** The projection puts all of a record's CPV codes on one line with
      the whole value. Tender Service's code order means nothing (its first
      code matches our main code 26% of the time; the codes aren't sorted),
      and the view summed the line once per code: €3,748M vs €837M.
    - **(b)** Two amendment notices of one €415.6M tender, each with the full
      value.
    - The same two flaws exist in production at a smaller scale: about 7% of
      2026 KHMDHS notice value is double counting.
    - Fixed by migration `20261003090000_analytics_cpv_count_once.sql`, made
      on branch `fix/analytics-cpv-count-once` off `main` (for review) and
      cherry-picked here. It counts one row per (item, division), and skips a
      notice that a later, not cancelled amendment replaces. Applied to
      `procurement_tsg`: division 22 is now €837M.
    - (b) still applies in the trial: Tender Service has no `amended_adam`;
      the chain (phase 3) has to supply it.
  - Tender Service labels Diavgeia contracts «Αποτέλεσμα», so they become
    `auction`. Our Diavgeia ingester calls 37k of September's decisions
    `contract`.

---

## 10. The decision at the end

- **Adopt**: Tender Service only. Needs §8.4 to show items are not needed, or that
  Tender Service can deliver them structured.
- **Hybrid**: Tender Service for acts, parties, chain and statistics; KHMDHS
  ingester kept only to fetch items (and any coded fields §8.5 shows missing) for
  acts we already hold, by ΑΔΑΜ.
- **Stop**: drop `procurement_tsg`.

Adopting either way then needs, in this order:
1. the «Φυσικά Πρόσωπα» fold on prod data
2. production hosting sized for the history kept (prod does not have to hold all
   27.5M; a window like the SEO one is an option)
3. prod migrations with `migrate.py up --only …`
4. turning off the old ingesters
5. the public text updates

---

## 11. Open questions (for the owner / Tender Service side)

1. **Items.** Does Tender Service hold KHMDHS items structured internally (line,
   CPV, quantity, unit, cost), and can the API expose them? This decides adopt vs
   hybrid.
2. **Chain.** For each document type, which type does `referenceNumber` point to?
   Is it always one id, or can it be several?
3. **Party identifiers.** What exactly `authorityId`, `authorityIdentifier`,
   `authorityOrgdbId`, `contractorVatUid`, `contractorStatisticalOrTaxNumber` and
   `contractorOrgdbId` mean; which are stable across years.
4. ~~Page size~~: not asked; the API stays as it is (owner). 10 per page.
5. ~~Update-time filter~~: not asked; the API stays as it is (owner). Day-only
   `lastUpdated` (§5b).
6. **Natural persons.** What rule does Tender Service use to fold them into
   «Φυσικά Πρόσωπα»? We should use the same one.
7. **Document types.** The full list of `typeOfDocument` / `subTypeOfDocument`
   values, including per-contractor awards, contracts and payments.
8. **Formats.** Amounts (`'24.924 EUR'`: always Greek grouping?), dates
   (timezone; currently assumed Europe/Athens).
9. **`numberOfOffers`.** From which year is it filled for KHMDHS-origin records?
10. **VAT vs Tender Service id disagreements.** Deferred by the owner; §8.6
    measures how often they happen.
11. ~~internalEndDate~~: answered. It is `systemExpirationDate`, set by the
    system but sometimes changed by hand; always use the date (§5e).
12. **The ACTIVE→EXPIRED switch** runs once a day after the next midnight
    (answered). Still open: does it change `lastUpdated`? The trial measures it
    (§8.9).
13. ~~Old records re-touched~~: answered. Corrections and added
    documents/attachments, i.e. real changes, frequency unknown. The trial
    measures it (§8.9).
14. ~~Archive size~~: answered. 27,514,538, the same as Tender Service's
    search. 7.18M was the share that carries a `lastUpdated` (§5a).
15. **Attachments.** How are `tenderAttachments` downloaded with an API key?
    On hold; the owner checks the week of 2026-10-05 (§1c).
