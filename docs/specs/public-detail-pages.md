# Richer public act & authority pages

Status: slices 1 and 2 built (2026-10-09). Later slices are proposals.

Source: Tender Service's "Enrich Tender Detail Views" and "Enrich Authority
public pages" (the "AI in Sales / Marketing ideas" document), adapted to
Greece. UNSPSC and BKP codes do not exist here and are out of scope.

## 1. Problem

A crawler is an anonymous visitor, and an anonymous visitor gets the teaser.
Measured on 2026-10-09:

- **Act page, public:** the hero only (type badge, title, authority, amounts,
  publication date, deadline), then the register prompt.
- **Authority page, public:** the name, ΑΦΜ and the register prompt.

That is thinner than the Tender Service pages the document calls "thin
content". The document's answer is to put a human-readable sentence next to
each structured field, and to add statistics computed from our own data.

## 2. Owner decisions (2026-10-09)

1. **What stays public on an act page:** title, publication date, deadline,
   authority name, plus the type badge and the amounts that the hero
   already shows. Nothing is removed from what a crawler sees today.
2. **Explanations follow their field.** A field that is subscriber-only gets
   its explanation only for subscribers (the document's rule: "if a field is
   not visible, neither the value nor its description is shown"). So
   procedure, contract type and award criterion notes are subscriber-only.
   The act-type note is public, because the badge is.
3. **Authority description = a sentence built from data only**, never a model
   (slice 2). No generated prose about a real organisation on a public page.
4. **Texts:** drafted by us in Greek and English, checked by the owner.
5. **Authority page, public part:** the data-only sentence, the 12-month
   figures, what it buys (CPV divisions, contract type, procedure, contract
   size, regions) and its open tenders. Contacts are shown as blurred
   placeholders. Main suppliers stay subscriber-only.
6. **Subscribers get the same profile section**, above everything they had.

## 3. Slices

| # | Slice | Needs |
|---|---|---|
| 1 | Fixed explanations on the act page (act type public; procedure, contract type, award criterion for subscribers) | code only |
| 2 | Public authority profile: 12-month figures, a data-only sentence, mix / region panels, open tenders, blurred contacts | precomputed figures (migration), refreshed by `refresh_analytics()` |
| 3 | CPV descriptions: one stored el/en paragraph per CPV in use (≈6,900 codes), generated once | migration, a batch run, a reviewed sample, /ai updated in the same commit |
| 4 | CPV landing pages, related tenders, related authorities | slice 3 |
| 5 | Redesign (side call to action, "never miss a tender like this" → ready-made alert) | slices 1–4 |

## 4. Slice 1 — fixed explanations

`app/field_notes.py` holds the texts, both languages, like `glossary.py`
(content, not UI strings — so not in `i18n_catalog`). One function,
`note(kind, value, lang)`, returns `{"text", "href"}` or `None`; the template
reaches it as the global `field_note`.

| Kind | Keyed by | Values | Shown to |
|---|---|---|---|
| `act_type` | `procurement_act.type` | every `TYPE_LABELS` key | everyone (hero) |
| `procedure` | `procurement_act.procedure_family` | the 12 real families; «Άλλο / Άγνωστο» gets nothing | subscribers |
| `contract_type` | `contract_type_code` | the 5 `CONTRACT_TYPES` codes | subscribers |
| `criteria` | `criteria_code` (notices, awards) or `assign_criteria_label` (contracts) | 4 codes + the 4 measured contract labels | subscribers |

Why these keys:

- **Procedure** is stored raw in `procedure_type_code`: a bare code on
  notices ("6"), a Greek label with article numbers on awards and contracts
  ("Απευθείας ανάθεση (αρ.118/αρ. 328)"), ~25 spellings in all. The
  normalised `procedure_family` column (`procedure_family_migration.sql`)
  already folds them into one label per procedure, so the notes key on that.
- **Contract criterion:** on contracts `assign_criteria_code` holds the
  CONTRACT TYPE code (13/9/10/…, measured: the counts match exactly), while
  `assign_criteria_label` is right. The page already prints the label; the
  note keys on the label, folded (case, accents, dash style).

Wording rules:

- A note explains what the value MEANS, never what this particular authority
  did. It never repeats a figure the page already shows.
- Legal statements are only what is checkable in the law's text; anything
  uncertain is left out rather than guessed (see §6).
- A glossary link is added where a glossary term exists.

Tests (`tests/test_field_notes.py`): every type / family / code / measured
label has a note in both languages; English carries no Greek letters; each
glossary link names an existing term; the gated page shows the act-type note
and NO procedure / contract-type / criterion note; a subscriber sees them.

## 5. Later slices — constraints already known

- **Authority location:** `proc.authority.city` and `nuts_code` are empty for
  all 3,933 authorities. Location can only be derived from the acts' place of
  performance ("mostly in Central Macedonia"), and must say so.
- **Authority type:** `type_code` is filled for 4% of authorities. Not usable.
- **Bidder figures** follow competition.py: median + single-bid share, never a
  mean, always with the period.
- **Blurred values** are replaced on the server (placeholder dots in the HTML),
  never hidden with CSS — a scraper reads the HTML.
- **Mass-generated text** for ranking alone is treated by Google as spam
  ("scaled content abuse"). CPV paragraphs must sit next to our own figures
  for that code, not stand alone.
- FAQ markup no longer earns rich results for commercial sites; FAQ text is
  still content.

## 6. Texts the owner should check first

- **Procedure «Διαδικασία άρθρου 128»:** today's article 128 of ν.4412/2016
  is about directly awarding specialist consultants on very large works
  (> €30M). Our ~40,000 acts with that label are small purchases (median
  €6,200, 2023–2026). The note therefore says only what the authority
  declared and what our data shows, not what the article covers.
- **«Συνοπτικός διαγωνισμός»:** the note says it was abolished by
  ν.4782/2021 (from 1/9/2021 per secondary sources; not checked against the
  ΦΕΚ).
- **Direct award:** no amount is quoted; the note points to article 118.

## 7. Slice 2 — the public authority profile

`app/authority_profile.py` + `_authority_profile.html`, under the header of
`/authority/<id>`, for everyone. Migration `20261009120000_authority_profile`.

**Data.** Two histogram views, refreshed CONCURRENTLY by
`proc.refresh_analytics()`:

- `mv_authority_profile`: one row per (authority, act type, contract type,
  procedure family, NUTS-2, value band) with n, n_valued, value and the
  window.
- `mv_authority_profile_cpv`: one row per (authority, 2-digit CPV division)
  over contracts; a contract counts once per division.

Python sums the rows of an entity group's members (histograms add, medians do
not). Unknown dimensions are '' / -1, never NULL, so the unique index covers
every row. Build time locally: 86 s for both, on ~390k acts.

**Which acts count:**
- notices and contracts PUBLISHED (submission_date) in the 12 months up to
  the refresh day;
- not cancelled, not a hidden duplicate;
- source on the analytics allowlist (khmdhs, manual, NULL), as for
  /analytics: TED, Tender Service and Diavgeia repeat KHMDHS acts.

Value is counted on CONTRACTS only, and only when analytics-eligible
(ceiling, flag), with the manual correction applied. An over-ceiling contract
counts in n but not in the value. Measured 2026-10-09: with the allowlist,
region and contract type are declared on 100% of contracts and procedure on
92%.

**Breakdowns** (contracts): CPV division, contract type, procedure, region,
size band (with VAT: <10k, 10–30k, 30–100k, 100–500k, 0.5–1M, ≥1M).
- A bar is a share of the contracts that DECLARE the field; how many do not
  is said under the panel.
- The top 5 rows are shown; the rest fold into «Λοιπά».
- A share never prints as 0% or 100% unless it is exactly that (<1%, >99%).

**The sentence** (`sentence()`) only re-states figures the page shows:
- counts, with the singular where it applies;
- the value only when something is valued;
- up to two CPV divisions, each at least 20% of contracts ("mostly" at 10%
  overstated it);
- the top region, as a share of ALL contracts so that «Όλες» means all.
  Thresholds: 100% → all, ≥ 90% → almost all, ≥ 50% → most, otherwise
  "several regions, most often X".

A year with nothing published says so. The sentence is also the page's meta
description.

**Open tenders:** up to 5 visible, uncancelled notices with a future
deadline, soonest first (~40 ms on the largest authority).

**Blurred contacts:** a gated reader sees the LABEL of each contact field the
authority has, and dots in place of the value. The value is never in the
HTML (test-enforced).

**Failure:** no populated views (migration not applied, or the test schema)
→ `load()` answers None and the page renders without a profile. Any error
→ logged, page renders without it.

**Deploy:** apply the migration on prod BEFORE merging (it builds the views
WITH DATA, ~1–2 min). Prod takes it with `migrate.py up --only`.

