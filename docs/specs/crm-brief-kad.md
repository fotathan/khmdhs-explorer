# CRM sales brief + ΚΑΔ→CPV estimate

Status: built 2026-10-07 (branch `feat/crm-brief-kad`). The ΚΑΔ estimate
reads the ΚΑΔ **description** (§3) and needs no data run.

## 1. The problem

The CRM card's «Ταίριασμα» tab ranks open tenders for a customer and lists
their awards, buyers and competitors — but only from the award ledger. A
customer that has never won anything (most new sign-ups) gets an empty tab,
and the useful facts for a salesperson are scattered across one tab and three
dialogs.

## 2. Decisions (owner, 2026-10-07)

| Question | Decision |
|---|---|
| Where the ΚΑΔ→CPV mapping comes from | First "learned from the ledger"; **changed the same day to the ΚΑΔ description** (below) |
| How the summary opens | **Dialog on the card + a printable page** |
| Region for a non-contractor | **Scores, never filters** (fit.W_GEO) |
| Contractor with < 5 awards | **History + a separate ΚΑΔ block** |

How the source was chosen:

1. **Learned from the ledger** needs every contractor's ΚΑΔ from ΓΕΜΗ. The
   registry rate-limits to one call every 6–8 s (measured on the 2026-10-07
   run: 2 s → 429s → 5.8 s → 8.3 s), so 30k contractors = ~3 days. The owner
   stopped the run after ~600.
2. **An official table does not exist.** The only CPV↔CPA mapping was an annex
   to Regulation 2195/2002 on CPA 96 / NACE Rev. 1, deleted since; TED's
   "correspondence" files are CPV 2003↔2008 only (downloaded and checked).
   ΚΑΔ 2025 is NACE Rev. 2.1 / CPA 2.2.
3. **The ΚΑΔ description**: an 8-digit ΚΑΔ names the product or service, and
   the CPV list has Greek descriptions. Measured, it beats both alternatives.

## 3. The estimate source: the ΚΑΔ description (`kad_cpv.describe`)

- Both texts go through Postgres's Greek stemmer (`to_tsvector('greek')`,
  already behind `cpv_code.description_tsv`), so "ΙΑΤΡΙΚΩΝ ΑΝΑΛΩΣΙΜΩΝ" and
  "Ιατρικά αναλώσιμα" meet as `ιατρ` + `αναλωσιμ`.
- Score = cosine over shared word stems, rarer stems weighing more (IDF over
  the 9,465 CPV descriptions). Codes below `DESC_MIN_SCORE` (0.30) dropped,
  at most 25 per ΚΑΔ.
- `DESC_STOP`: filler that says HOW a firm trades, not WHAT ("χονδρικό
  εμπόριο", "κατασκευή", "υπηρεσίες"…). Left in, the rare ones are exactly the
  words CPV never uses — high weight, wrong codes.
- **The NACE section limits the CPV type** (`allowed_divisions`): primary,
  manufacturing and trade (01–33, 45–47) → goods divisions 03–44 + 48;
  construction (41–43) → 44, 45, 71; everything else unrestricted. Without it
  a surgical-instrument wholesaler matched "installation of medical
  equipment" services. Repair/installation (50, 51) for goods firms was
  measured and cost more than it found.
- Primary ΚΑΔ counts 1.0, up to 10 secondaries 0.5; each matched code also
  sets its group and division (the depths the scorer grades by).

Measured with `kad_cpv_map.py evaluate` on enriched contractors with ≥ 5
awards in 5 years, top 5 CPV groups predicted from the primary ΚΑΔ:

| 2026-10-07 | firms | hit | precision | award coverage |
|---|---|---|---|---|
| all — naive (5 most common groups) | 731 | 66% | 37% | 27% |
| all — **ΚΑΔ description** | 700 answered | **86%** | **44%** | **30%** |
| all — learned mapping, in-sample | 226 answered | 39% | 28% | 2% |
| excl. ΚΑΔ 46.46 — naive | 509 | 66% | 29% | 11% |
| excl. ΚΑΔ 46.46 — **ΚΑΔ description** | 479 answered | **82%** | **41%** | **24%** |

hit = at least one predicted group was won; precision = share of predicted
groups won; coverage = share of the firm's awards inside them. The sample is
the LARGEST contractors (priority enrichment) and 30% medical wholesale, which
flatters the naive guess — hence the second half. The threshold barely matters
(0.20–0.35: hit 85–88%). Adding "υλικά" to the filler was neutral-to-worse.

## 3b. The learned mapping — measured, not used

`kad_cpv_map.py build` still learns, per ΚΑΔ prefix (8, 6, 4 digits), which
CPVs its contractors win: in FIRMS, gated by MIN_FIRMS 5 / MIN_SUPPORT 0.10 /
MIN_LIFT 2.0. Even in-sample it did worse than guessing (above): the firms we
could enrich are generalists that win a little of everything. It is NOT a
source of the estimate. It stays for:

- `proc.operator_kad` — contractor ΑΦΜ → primary ΚΑΔ + NUTS-2 of the seat:
  the "same ΚΑΔ" peer list. Only as complete as the enrichment (the page says
  out of how many). Prod's 500 MB tier cannot hold raw registry records, so
  `push` copies only this, `kad_cpv_map` and `kad_cpv_build`.
- `evaluate`, to compare again if enrichment ever grows.

Tables: migration `20261007120000_kad_cpv_map.sql`.

## 4. The estimate and the brief

- `kad_cpv.estimate(c, uid)` → an ordinary `fit.Profile` (CPV from the ΚΑΔ
  descriptions + region; no buyers, no value band) scored by the SAME
  `fit.score_open`. Max score is 75 (buyer 0, value neutral) — the page says so.
- ΑΦΜ = the admin-linked company match, else `customer_profile.vat_number`.
  **Never onboarding's `declared_afm`** (a claim; test-enforced).
- Region = registered postal code → NUTS-2 (`POSTAL_NUTS2`, first two digits);
  our authorities carry no NUTS and contractors almost no postal code.
- Peers = `operator_kad` under the same primary ΚΑΔ (8 → 6 → 4 digits, the
  level used is shown), ranked by awards in 3 years, seat region shown. Empty
  usually means "not known": the note says among how many.
- Leaders = most awards in every code of the estimate's 3 strongest CPV
  groups (a description matches a group's GENERAL code, awards carry the
  specific ones), 3 years, with the count inside the customer's region.

`crm_brief.build` picks the basis: `history` (≥ 5 awards), `history+kad`,
`kad`, or `none`. The two are separate blocks with separate numbers.

Surfaces:
- **Card**: «Σύνοψη πελάτη →» in the at-a-glance strip → `#brief-dlg` (outside
  the tab panels — a hidden panel hides a modal inside it). Competitor lists
  load lazily (`/admin/crm/<uid>/brief/<competitors|peers|leaders>`).
- **Print page**: plain GET `/admin/crm/<uid>/brief`, everything inline.
- **Ταίριασμα tab**: a «Εκτίμηση από ΚΑΔ» block under the history list.

Admin-only (everything under /admin). Never shown to the customer. Computed
per request, stored nowhere; never reads `act_ai_summary` (test-enforced).

## 5. Rollout

1. Migration on local + Supabase: `migrate.py up --only 20261007120000_kad_cpv_map.sql`.
2. Merge. The estimate works from then on for any customer linked to a ΓΕΜΗ
   company — no data run.
3. Optional, for the "same ΚΑΔ" peer list only: enrich contractors slowly
   (`gemi_enrich.py --scope suppliers --priority --max-calls N`), then
   `kad_cpv_map.py build` and `push --to "$PROD_DATABASE_URL"`.

## 6. Open / later

- Measured only on the largest contractors; small firms (the customers this
  is for) were not in the sample.
- Generic ΚΑΔ ("μη εξειδικευμένο χονδρικό εμπόριο") match nothing by design;
  their secondary ΚΑΔ carry the estimate.
- No admin override of a match yet (a curated table would sit beside it).
- Customer-facing ΚΑΔ estimate: not planned — a wrong estimate shown to a
  customer teaches them to ignore the feature (same rule as fit visibility).
