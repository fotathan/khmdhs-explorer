# Spec: First-login onboarding wizard → saved searches

**Status:** Phase 1 built locally, 2026-09-21; not deployed. §12 records where the
build differs from this draft.
**Repo path:** `docs/specs/onboarding-wizard.md`
**Inspired by:** Bable "City Insights Manager" first login (Welcome → Define
product → Keywords → Overview → ranked radar).
**Decisions taken:**
- Phase 1 uses the award ledger and ΓΕΜΗ only. **No AI website scan.**
- A typed ΑΦΜ is a **claim**. It drives suggestions only. It is never written
  into `customer_profile.vat_number` and never links a contractor (§5).
- The wizard creates **up to three** saved searches, not one (§6).
- Alerts are created **off** (deliverability is not done).
- Fit-score ranking for customers is phase 2 (§11).
- The registration form gets a new **yes/no "tender experience"** question. "Ναι"
  reveals an optional ΑΦΜ field, and the answer picks the wizard's branch (§3a).
  Decided 2026-09-21.

---

## 1. Problem

A new self-registered customer lands on `/` with an empty search box and seven
days of trial. They have to find the CPV filter, the region filter and the
keyword syntax on their own before the product shows anything relevant.
Many never do, and the trial ends before they see their own market.

**Goal.** In under two minutes after registering, the customer has saved
searches that describe their business and has seen results from them.

**Non-goals.**
- Ranking results by fit (phase 2, §11).
- Reading the customer's website with a model (§11, and /ai would change).
- Changing how search, saved searches or alerts work. The wizard only
  *writes* ordinary `proc.search_profile` rows through the existing helper.

---

## 2. What Bable does, and where we differ

| Bable step | Theirs | Ours |
|---|---|---|
| Welcome | "Typically takes less than 90 seconds", skip link | Same, skip link kept |
| Define product | Paste a URL, AI extracts a profile | Type an ΑΦΜ; we read **what the firm has actually won** from the ledger |
| Keywords | 5 required, 10–15 recommended, AI-suggested | 0 required, suggested from past award titles |
| Overview | Review, Back / Done | Review + **how many acts each search would have matched last month** |
| Result | Ranked city cards, 0–100 score | The saved searches' results (ranking = phase 2) |

The advantage we have and they cannot copy: an award ledger keyed by tax
number. A website says what a firm *claims*; the ledger says what it *won*.

---

## 3. Where the wizard appears

- Right after `POST /register` succeeds: redirect to `/welcome` instead of `/`.
- On any later sign-in while the wizard is neither completed nor skipped:
  a dismissible banner on `/` ("Ρυθμίστε τις αναζητήσεις σας σε 2 λεπτά"),
  never a forced redirect. A sign-in link or 2FA flow must not be interrupted
  by it.
- From `/account/searches`: a "Ρύθμιση από την αρχή" link, so a customer can re-run it.
- Admin-created accounts and admins themselves don't get the redirect, only the
  link.
- `ONBOARDING_ENABLED=0` removes the routes, the redirect and the banner. It
  fails towards **off**, with the same parsing as `LOGIN_LINKS_ENABLED`
  (0/false/no/off/n/f/disabled, any case).

---

## 3a. The registration form: tender experience + ΑΦΜ

Two fields are added to `register.html`, below the email:

> **Έχετε συμμετάσχει ποτέ σε δημόσιους διαγωνισμούς;** ( ) Ναι ( ) Όχι
>
> *(shown only when "Ναι" is picked)*
> **ΑΦΜ επιχείρησης** (προαιρετικό) [_________]
> Με το ΑΦΜ βρίσκουμε τις αναθέσεις που έχει ήδη κερδίσει η επιχείρησή σας και
> ρυθμίζουμε τις αναζητήσεις σας με ακρίβεια.

- **The question is required** on self-registration: it is one click, and the
  wizard branches on it. The **ΑΦΜ is always optional**. Nobody is turned away for
  not having one to hand.
- Without JS the ΑΦΜ field is always visible, labelled "(αν απαντήσατε Ναι)". An
  ΑΦΜ sent together with "Όχι" is ignored, not an error.
- The ΑΦΜ is **format-checked only** at registration (`gemi_client.normalize_afm`:
  9 digits + check digit). A bad one re-renders the form with the values kept.
  **No ledger or ΓΕΜΗ lookup happens during sign-up.** A slow or failing
  registry must never block an account being created. The lookup runs on the
  wizard's first screen.
- No uniqueness check on the ΑΦΜ. Two employees of one firm may both register,
  and answering "this ΑΦΜ is taken" would tell a stranger that the firm has an
  account with us. The admin sees duplicates on the CRM side.
- The existing `register_submit` checks (invite code, username, password) run
  first and are unchanged.

**Storage.**
- The answer goes to a new column, `customer_profile.tender_experience boolean`.
  `NULL` means "never asked" (admin-created and existing accounts), so it is
  not the same as "Όχι". It is the customer's own answer about themselves, so it
  lives on the profile, unlike the ΑΦΜ.
- The typed ΑΦΜ goes to `proc.onboarding.declared_afm`, **not** to the profile (§5).
  Registration creates the `proc.onboarding` row with it.

**Where the answer is used.**
- It picks the wizard's branch (§4, step 1).
- The CRM card's "at a glance" strip shows "Εμπειρία διαγωνισμών: Ναι / Όχι / —",
  and it can be edited on the Στοιχεία tab.
- It becomes a filter on the `/admin/crm` list. "Has bid before" and "new to public
  tenders" are two different sales conversations.
- `company_match` must never fill or overwrite it. It is not in the fill-if-empty
  set, and a test pins that.

---

## 4. The screens

Layout: content on the left, a step rail on the right (Bable's pattern). On a
phone the rail becomes a "Βήμα 2 από 4" line at the top. Every step has Πίσω / Συνέχεια.
Every step after the first also has "Παράλειψη", which keeps what was entered so far.
Each step is its own GET/POST pair (progressive enhancement: it works with JS off,
HTMX only swaps the panel). State lives in `proc.onboarding.answers` (§7), so a
customer who closes the tab resumes where they left off.

### Step 0 — Καλώς ήρθατε
> **Ας στήσουμε το ραντάρ διαγωνισμών σας**
> Πείτε μας τι κάνει η επιχείρησή σας και θα φτιάξουμε αναζητήσεις που σας
> ειδοποιούν για τους διαγωνισμούς που σας αφορούν. (Περίπου 90 δευτερόλεπτα.)
>
> [Ξεκινάμε] · Παράλειψη

### Step 1 — Η επιχείρησή σας
The registration answer (§3a) decides what this screen shows:

| Registration answer | Step 1 |
|---|---|
| Ναι + ΑΦΜ given | Runs the lookup **straight away** and shows the result card below. The customer only confirms. |
| Ναι, no ΑΦΜ | Asks for it once more, with a "Συνέχεια χωρίς ΑΦΜ" link. |
| Όχι | **Skipped entirely.** The wizard opens at step 2 on the manual (categories) path. |
| NULL (re-run by an older account) | Asks the yes/no question first, then as above. |

The ΑΦΜ box, when shown:
> **ΑΦΜ επιχείρησης** (προαιρετικό) [_________] [Αναζήτηση]
> Το χρησιμοποιούμε μόνο για να προτείνουμε κωδικούς και περιοχές από τις
> αναθέσεις που έχει ήδη κερδίσει η επιχείρηση.
>
> Ή: "Συνέχεια χωρίς ΑΦΜ"

The lookup has three outcomes:

1. **Found in the ledger.** We show a summary card:
   > Ο ανάδοχος με αυτό το ΑΦΜ: **ΕΤΑΙΡΕΙΑ Α.Ε.**
   > 34 αναθέσεις · κυρίως 3314 Ιατρικά αναλώσιμα, 3319 … · κυρίως Κεντρική Μακεδονία
   > · τυπικό μέγεθος 8.000 € – 120.000 €
   > [Ναι, αυτή είναι η επιχείρησή μας] [Όχι]

   The wording is "the contractor with this ΑΦΜ", not "you": we have not
   verified the claim. The data is public award data, already on `/contractors/<id>`.
2. **Not in the ledger, found in ΓΕΜΗ.** We show the company name and seat
   ("Είναι αυτή η επιχείρησή σας;"). This gives no CPVs (§10, question 1). The
   customer picks from categories in step 2.
3. **Neither.** The customer carries on manually.

For outcomes 2 and 3 the wording must allow for how the ledger works. It holds
**winners only**, so a firm that answered "Ναι" but has bid and lost, or has won
only as part of a joint venture, is not in it. That is normal, not an error:
> Δεν βρήκαμε αναθέσεις σε αυτό το ΑΦΜ. Αυτό είναι φυσιολογικό αν έχετε
> συμμετάσχει σε διαγωνισμούς αλλά δεν έχετε ακόμη κερδίσει κάποιον — ή αν
> κερδίσατε ως μέλος ένωσης. Ας ρυθμίσουμε τις αναζητήσεις σας χειροκίνητα.

The "Ναι" answer is not changed because nothing was found.

The ΑΦΜ is validated with `gemi_client.normalize_afm` (9 digits + check digit)
before anything is queried.

### Step 2 — Τι προσφέρετε
Chips of **CPV classes (4 digits)** with Greek labels, pre-filled from the ledger
when step 1 found one. The customer can remove chips and add more through the
existing grouped category/CPV picker.

- Ledger pre-fill rule: 4-digit prefixes ordered by `n_acts`, taken until they
  cover 80% of the firm's awards, **at most 10**. Anything finer is too narrow for
  a first search, and 2 digits is too broad (division 33 is all of healthcare).
- Manual path: our categories (`cat=c:<id>` / `s:<id>`), which are friendlier
  than CPV codes for a newcomer. Both kinds of chip can be mixed.
- At least **one** chip is required to continue. Without it search #1 has no
  subject.

### Step 3 — Λέξεις-κλειδιά
> Λέξεις ή φράσεις που εμφανίζονται στους διαγωνισμούς που σας ενδιαφέρουν.
> Πιάνουν και διαγωνισμούς με λάθος κωδικό CPV.

- 0 required; 3–10 recommended; hard cap 15.
- Suggestions (ledger path only): the most frequent 1–2 word terms in the
  firm's past award titles, with a Greek stop-word list and procurement boilerplate
  (προμήθεια, παροχή, υπηρεσιών, ανάθεση, έργο, …) removed. They are shown as greyed
  chips that the customer clicks to add. None are added automatically.
- Each keyword is stored as typed. The builder quotes it (`"…"`) and joins
  keywords with ` OR ` (§6), so the customer never sees search syntax.
- A keyword that is on the boilerplate list is refused with an explanation
  ("πολύ γενικό — θα ταίριαζε σχεδόν σε όλα").

### Step 4 — Περιοχή και μέγεθος
- Regions: NUTS chips, pre-filled from the ledger with the regions holding
  ≥ 10% of the firm's awards. If the firm works in more than 5 regions, "Όλη η
  Ελλάδα" is pre-selected instead, meaning no `nuts` filter.
- Value range: optional and **empty**. When the ledger has a band
  (`MIN_AWARDS_FOR_BAND` met) it is shown as a hint, "Το τυπικό μέγεθος των
  αναθέσεών σας: 3.395 € – 33.969 €", not filled in (§12). A warning says a
  value filter hides tenders that don't publish a budget.
- "Θέλω να βλέπω και σε ποιους ανατίθενται οι διαγωνισμοί του κλάδου μου" (checkbox,
  default **on**) → creates search #3.

### Step 5 — Επισκόπηση
One card per saved search to be created, each with:
- its name (editable),
- its filters in words,
- **"τον τελευταίο μήνα θα είχε βρει N"**: a count over `submission_date` in the last
  30 days, run through the normal `build_where`. A count of 0 or over 1,000 gets a
  one-line hint ("πολύ στενό" / "πολύ ευρύ — προσθέστε περιοχή ή αφαιρέστε λέξεις").

[Πίσω] [Δημιουργία αναζητήσεων]

Done → the searches are created → redirect to `/` with search #1 applied and a
flash message: "Δημιουργήθηκαν 3 αναζητήσεις. Θα τις βρίσκετε στις Αποθηκευμένες αναζητήσεις."

---

## 5. The ΑΦΜ is a claim, not a link

The CLAUDE.md rule for company match: **never auto-link**. The ΑΦΜ decides the
fit score, the ledger link and eventually an invoice. A self-typed ΑΦΜ is
exactly the unverified input that rule is about. So:

- It is stored **only** in `proc.onboarding.declared_afm`, whether it was typed
  at registration (§3a) or in the wizard.
- It is **not** written to `customer_profile.vat_number` / `tax_number` /
  `operator_id`, so `fit.operator_ids_for` never sees it and
  `seed_from_ledger` never runs from it.
- The CRM card's ΓΕΜΗ match panel shows "Δηλωμένο ΑΦΜ στην εγγραφή: 0xxxxxxxx
  (δεν έχει επαληθευτεί)" with a **Σύνδεση με αυτό το ΑΦΜ** button. The button
  posts to the existing link route, which is the only door to the customer
  record and rebuilds everything from the registry and the ledger. It does not
  pre-fill the search box: that box searches by *name*, and an ΑΦΜ typed there
  matches nothing.
- Ledger suggestions come from **read-only** helpers,
  `fit.operator_ids_for_afm(c, afm)` + `fit.ledger_summary(c, op_ids)`. The
  award aggregate is one SQL string (`fit._AWARD_AGG_SQL`) shared with
  `seed_from_ledger`; the CPV and region queries use the same filters, and a
  test asserts both paths return the same numbers. It writes nothing. A test
  asserts that the wizard leaves `company_profile*` and `customer_profile` untouched.

Lookup limits: ledger lookups are cheap, but ΓΕΜΗ has a quota. Every lookup
counts in `proc.login_throttle` under key `onboarding-afm:<user_id>`, with the
login throttle's own limits (`auth._MAX_FAILS` = 8, then a
`_LOCK_SECONDS` = 5-minute lock). ΓΕΜΗ is called only when the ledger has
nothing and `GEMI_API_KEY` is set. Results go through `gemi_client.enrich_one`,
so they land in `proc.gemi_enrichment` and a repeat lookup is served from there.

---

## 6. What gets created

All searches are `scope='customer'`, owned by the user, and created with
`auth.create_search_profile` (the same call `/account/searches` uses). `params`
use only keys already in `search_profiles._SINGLE` / `_MULTI`.

| # | Default name | params | Created when |
|---|---|---|---|
| 1 | Νέοι διαγωνισμοί στο αντικείμενό μου | `type=["notice"]`, `cpv=[…]` and/or `cat=[…]`, `nuts=[…]`, `value_min/max`, `status="active"` | always (step 2 requires a chip) |
| 2 | Διαγωνισμοί με τις λέξεις-κλειδιά μου | `type=["notice"]`, `q='"k1" or "k2" or …'`, `nuts=[…]`, `value_min/max`, `status="active"` | ≥ 1 keyword |
| 3 | Αναθέσεις στον κλάδο μου | `type=["contract"]`, same `cpv`/`cat`/`nuts` as #1 | checkbox in step 4 |

**Why the split.** In `build_where`, CPV values OR together, but the CPV group
ANDs with the keyword box. One combined search would only match acts that have
*both* a chosen CPV *and* a keyword. That is the narrowest possible reading, and
it would miss exactly the mis-coded tenders keywords are meant to catch.

**Why `q` and not `fulltext`** (decided in build, §10 question 3). `q` already
searches the title **and** the document text (`search_tsv`), plus published
tables. `fulltext` reads only the document body, so it would miss a keyword that
appears only in the title. Both use `websearch_to_tsquery`. Quotes, `*` and a
leading `-` are stripped from each keyword first, so a keyword can only ever be
a phrase.

**Limits.**
- If creating the searches would take the customer past `MAX_SAVED_SEARCHES`,
  the wizard creates what fits and says which were not created. It only reaches
  that limit when re-run.
- Re-running the wizard creates **new** searches. It never edits or deletes the
  ones from a previous run. The overview lists the existing ones by name, so the
  customer can see what they already have. `proc.onboarding.created_profile_ids`
  records which were made by the wizard.
- Alerts: none are created. The flash message links to `/account/searches`, where the
  customer can turn one on once alerts are open to customers.

---

## 7. Storage

Migration `…_onboarding.sql`. It runs on **both** local and Supabase before the
dependent code is pushed. No `DO $$` blocks.

```sql
CREATE TABLE proc.onboarding (
    user_id             bigint PRIMARY KEY REFERENCES proc.app_user(id) ON DELETE CASCADE,
    step                smallint NOT NULL DEFAULT 0,
    answers             jsonb    NOT NULL DEFAULT '{}'::jsonb,
    declared_afm        text,
    ledger_found        boolean,
    started_at          timestamptz NOT NULL DEFAULT now(),
    completed_at        timestamptz,
    skipped_at          timestamptz,
    created_profile_ids bigint[] NOT NULL DEFAULT '{}'
);
```

Same migration:

```sql
ALTER TABLE proc.customer_profile ADD COLUMN tender_experience boolean;
COMMENT ON COLUMN proc.customer_profile.tender_experience IS
  'Self-declared at registration: has bid in public tenders before. NULL = never asked.';
```

Registration writes a `customer_profile` row (today a missing row means an empty
profile), but fills only this column.

`answers` holds the chips, keywords, regions, value range and names between
steps. It is validated server-side at every POST: only known CPV prefixes,
category ids and NUTS codes are kept. `tests/proc_schema.sql` is regenerated.

This gives the admin a funnel for free: started / completed / skipped, and at
which step people stop. It appears as one line on `/admin/crm` ("Onboarding: 41
ξεκίνησαν, 29 ολοκλήρωσαν, 6 παρέλειψαν").

---

## 8. Code layout

- `app/onboarding.py`: router (`/welcome`, `/welcome/step/{n}` GET+POST,
  `/welcome/afm` HTMX lookup, `/welcome/finish`, `/welcome/skip`), the pure
  `build_profiles(answers) -> list[(name, params)]`, and the keyword
  rules (quote, boilerplate list, cap).
- `app/fit.py`: `ledger_summary_for_afm` (read-only), shared by `seed_from_ledger`.
- `app/templates/onboarding.html` + `_onboarding_step*.html`.
- `app/main.py`: register redirect, banner flag, router include behind
  `ONBOARDING_ENABLED`. `register_submit` reads `tender_experience` + `afm`.
- `app/templates/register.html`: the yes/no question and the conditional ΑΦΜ
  field. It stays unchanged when `ONBOARDING_ENABLED` is off.
- `app/crm.py` + CRM card templates: the glance-strip value, the edit field on
  Στοιχεία, and the list filter.
- i18n: every string goes through `t()` with EN entries in `i18n_catalog.py`.
- `/help` (beta_help.html): a section "Πρώτη ρύθμιση", written in the same commit.
- `LOCAL_RUNBOOK.md`: the switch, and that the ΓΕΜΗ path needs `GEMI_API_KEY`.

---

## 9. Tests (`tests/test_onboarding.py`)

1. `build_profiles`: CPV-only → 2 searches (#1, #3); CPV + keywords → 3;
   checkbox off → no #3; `"k1" OR "k2"` quoting, including a keyword
   containing a quote character.
2. Every generated `params` survives `params_from_qs(params_to_qs(p)) == p`, so
   a created search is editable like a hand-made one.
3. Boilerplate keyword refused; 16th keyword refused; step 2 cannot be passed
   with zero chips.
4. **The claim rule:** after a full run with a ledger-hit ΑΦΜ,
   `customer_profile` and `company_profile*` are unchanged, and
   `fit.operator_ids_for(user)` is still empty.
5. Ledger summary on a fixture contractor: the 80%/10-chip cut, the ≥10% region
   rule, the >5 regions → all-Greece rule, and value band only above
   `MIN_AWARDS_FOR_BAND`.
6. `ledger_summary_for_afm` and `seed_from_ledger` agree on the same fixture,
   which guards the refactor.
7. Register → 303 to `/welcome`. With `ONBOARDING_ENABLED=false` → 303 to `/`
   and `/welcome` is a 404.
8. Throttle: the 11th lookup in an hour is refused. The ΓΕΜΗ client is
   monkeypatched, so no network.
9. Tampered POST (unknown CPV/NUTS/category id in the form) → dropped, not saved.
10. `MAX_SAVED_SEARCHES` reached → partial creation reported, nothing over the cap.
11. Resume: leave at step 3, come back → step 3 with the answers intact.
12. Registration: the question missing → form re-rendered with an error and
    values kept; "Ναι" + bad ΑΦΜ → error; "Ναι" + no ΑΦΜ → account created;
    "Όχι" + an ΑΦΜ → account created and the ΑΦΜ **discarded**.
13. Registration makes **no** network call: the ΓΕΜΗ client is patched to raise,
    and sign-up still succeeds.
14. The ΑΦΜ typed at registration lands in `onboarding.declared_afm`. The
    customer_profile row has `tender_experience` set and **nothing else**:
    `vat_number`, `tax_number` and `operator_id` stay NULL.
15. Branching: "Όχι" → the wizard opens at step 2; "Ναι" + ΑΦΜ → step 1 renders
    the lookup result without a second submit.
16. Registering with an ΑΦΜ another account already declared succeeds, and the
    response is identical to any other sign-up (no leak).
17. The company-match link flow leaves `tender_experience` untouched.

---

## 10. Open questions

Closed 2026-09-21 ("go with the defaults"):

1. **ΓΕΜΗ gives ΚΑΔ, not CPV.** No crosswalk in phase 1: ΓΕΜΗ only confirms the
   company name. A hand-made crosswalk for the ~50 most common ΚΑΔ stays a
   possible later step.
2. **Search #3 (awards)** is on by default.
3. **Keywords use `q`**, not `fulltext` (§6: `q` already covers title and text).
4. **Invite-mode registrations** get the same wizard.

---

## 12. Where the build differs from this draft

- **The value band is a hint, not a pre-fill** (step 4). On real data (a
  medical supplier, 788 awards) p10–p90 was 3.395 € – 33.969 €. As a filter that
  hides a fifth of the sizes the firm actually wins, plus every tender with no
  published budget. The customer can still type a range.
- **Keywords use `q`** (§6).
- **The declared ΑΦΜ is a link button on the CRM card**, not a pre-filled search
  box (§5).
- **Lookup throttle** reuses the login throttle's limits, not a separate
  10-per-hour counter (§5).
- **The ledger helper** is `operator_ids_for_afm` + `ledger_summary`, sharing
  `_AWARD_AGG_SQL` with the seed, rather than one refactored function (§5).
- **Keyword suggestions keep the titles' own spelling.** Most titles are in
  capitals, and lowercasing them drops the accents ("αγγειοπλαστικης"), so the
  chips show the title's form and are drawn in capitals. `πρακτικό` / `αίτημα`
  / `χρήση` / `ανάγκες` were added to the generic list after a real run.
- **A customer who already keeps saved searches gets no band on `/`** (§3).
  An older account that found its way without us is not nagged.
- **The switch is checked per request** (a 404 when off) rather than by not
  mounting the routes, so both states are testable in one process.

---

## 11. Phase 2 (not in this spec)

- **Fit ranking for customers.** Once an admin has confirmed the ΑΦΜ,
  `seed_from_ledger` runs, and the customer's results for search #1 can be sorted
  by `fit.score` with the components shown (Bable's 0–100 badge, but explained).
  The isolation rule with `act_ai_summary` stays as it is.
- **Website scan.** Only if the ledger path proves insufficient for firms with
  no awards. It would be a new AI surface: /ai updated in the same commit,
  the provider declared, and customer text sent to a model for the first time.
  That is a legal decision before it is a code one.
- **Alerts in the wizard**, once deliverability (SPF/DKIM/DMARC, unsubscribe) is done.
