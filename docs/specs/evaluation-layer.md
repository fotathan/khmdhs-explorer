# Spec: Evaluation layer: the customer's certificates against the checklist

**Status:** slice 1 built 2026-09-24 (branch `feat/evaluation-layer`) with the
owner's decisions in §9. Customer self-service (slice 2) not built.
**Roadmap:** Tier 2, tender-checklist.md §5 item 6 and ai-summary.md §14
("per-customer eligibility evaluation"). This is the last open checklist slice.
**Depends on:** tender-checklist.md (the checklist it annotates), ai-summary.md
§3 (the two-layer rule it must not break), fit.py (the company profile it
extends).

---

## 1. Problem

The checklist says «Πιστοποιητικό ISO 9001:2015 ή ισοδύναμο». The bidder holds
one, so they tick it. They still have to check that the certificate is still
valid on the closing date, and they do the same for the next tender and the
one after. The expiry is the part that costs a bid: a certificate that lapses
three days before the deadline makes the offer inadmissible, and nobody notices
until the envelope is assembled.

We know neither fact today. The company profile (fit.py) holds CPVs, regions,
buyers and a value band from the award ledger. It holds nothing the firm
**has**.

---

## 2. What was measured (2026-09-24)

- **Prod has 4 AI summaries** (11 eligibility items, 3 of which name a
  certificate). That is too few to measure matching on summaries, so the
  numbers below come from notice texts.
- **5,000 recent notices** (local DB, `type='notice'` with full_text, closing
  in the last 120 days or later): 677 (13.5%) name an ISO standard.

  | Standard | Notices | What it is |
  |---|---|---|
  | ISO 9001 | 551 | quality management (the firm) |
  | ISO 13485 | 342 | medical-device quality (the firm) |
  | ISO 14001 | 166 | environmental management (the firm) |
  | ISO 45001 / OHSAS 18001 | 88 | health & safety (the firm) |
  | ISO 27001 | 49 | information security (the firm) |
  | ISO 37001 | 30 | anti-bribery (the firm) |
  | ISO 22000 / HACCP | 34 | food safety (the firm) |
  | ISO 22301 | 29 | business continuity (the firm) |
  | ISO 50001 | 28 | energy management (the firm) |
  | ISO 39001 | 21 | road-traffic safety (the firm) |
  | **ISO 13458** | 15 | **not a standard**: a typo for 13485 in real notices |
  | ISO 10993, 15223, 7376… | <10 each | **product** standards (the item, not the firm) |

- 799 of the 5,000 (16%) say «ισοδύναμο».
- Registries (ΜΗΕΕΠ/ΜΕΕΠ for public works) appear in 75. They are graded by
  class and category, so they are not a yes/no certificate.

Two things follow from this. A **closed catalogue of about 12 management-system
certificates** covers nearly everything a firm is asked to HOLD. And the
matcher must ignore **product** standards. «Τα προϊόντα να φέρουν σήμανση
κατά ISO 10993» describes the goods, not the bidder, and it sits in the
requirements section.

---

## 3. The rule this cannot break: the two layers (ai-summary §3)

The summary is extraction: one row per act, served to everyone. Certificates
are evaluation: one customer's private data. So:

- The evaluation is computed **at render time**, joined onto the checklist
  view the same way ticks are. It is never written to `proc.act_ai_summary`,
  never goes into the cache key, and never goes into a prompt.
- `ai_summary.py` never reads the certificate table. The new module never
  writes the summary. Both directions are test-enforced, the same pattern as
  fit.py and tender_checklist.py.
- No model call. Matching is regex over text we already hold.

---

## 4. It suggests; it never ticks

The roadmap line said "pre-tick". This spec rejects that, for the same reason
the bid pipeline's ledger only suggests:

- **A tick means "done for this bid"**: the certified copy is in the folder
  and the ΕΕΕΣ box is filled in. Holding the certificate is not the same thing.
  An auto-tick would mark work as finished when nobody has done it.
- **"Provably" is out of reach for regex.** An item that reads «ISO 9001 και
  τριετής εμπειρία σε ανάλογα έργα» is only half covered by the certificate.
  We would tick an item the firm does not fully meet.

What the customer sees instead, under the item:

> ✓ Στο προφίλ σας: **ISO 9001:2015**, ισχύει έως 12/03/2027.

> ⚠ Στο προφίλ σας: **ISO 14001:2015**, λήγει 02/10/2026, **πριν** την
> υποβολή (06/10/2026).

The tick box stays exactly as it is.

**Absence is neutral, never a verdict** (§9.4). "Δεν έχετε ISO 27001" is a
verdict built on a profile that may simply be incomplete. That is the "you do
not hold X" failure ai-summary §3 warns about, and a customer who believes it
may skip a bid they could win. The line reads «ISO 27001: δεν έχει δηλωθεί
στο προφίλ σας», and it appears only for a customer who declared something.

---

## 5. Data model

One new table, per customer (keyed like company_profile, by user):

```
proc.company_certificate      (migrations/20260924150000_company_certificate.sql)
  id            bigserial PK
  user_id       bigint  → app_user, ON DELETE CASCADE
  scheme        text    NOT NULL   -- catalogue key: 'iso9001', 'iso13485', …
  holder        text    NOT NULL   -- 'self' | 'manufacturer' (§9.3)
  manufacturer  text               -- required exactly when holder = manufacturer
  edition       text               -- '2015' (optional; see §6)
  number        text               -- certificate no., for the customer's own reference
  issuer        text               -- certification body
  valid_until   date               -- NULL = not entered; never inferred
  source        text    NOT NULL   -- 'admin' | 'customer'
  created_at / created_by / updated_at / updated_by
  UNIQUE (user_id, scheme, holder, lower(coalesce(manufacturer, '')))
```

Saving the same scheme and holder again updates the row, which is how a
renewal is entered.

- **No files.** A scan of the certificate would be a document store of
  customer data on a 500 MB free tier. The attachments feature is for ACT
  documents, and mixing the two breaks the /ai statement's "the act's own
  documents only". Not in slice 1.
- **No derivation.** Nothing reads certificates out of award acts or the ΓΕΜΗ
  registry. They are declared, full stop.

---

## 6. Matching

A new module, `app/eligibility_eval.py`, with no DB access in the matcher.

**Catalogue** (a closed tuple, the same pattern as ai_summary.SECTIONS): each
scheme has a key, a display name, and a pattern tolerant of how Greek notices
write it: `ISO 9001`, `ΕΝ ISO 9001:2015`, `ΕΛΟΤ EN ISO 9001`, `ISO9001`,
`9001:2015` after an ISO mention. `13458` is aliased to 13485 because real
notices contain that typo. OHSAS 18001 → 45001 (its successor): a notice
asking for OHSAS is answered by a 45001 holder. The reverse is not true: a
firm holding only OHSAS does not meet a 45001 requirement.

**Which items:** every item the checklist shows (eligibility, mandatory
pricing and requirements, submission). The matcher reads label + value +
quote.

**Product vs firm:** a scheme outside the catalogue is never matched, and
that alone excludes 10993/15223/7376. *Changed while building:* the draft also
required a bidder phrase in `requirements` items. Every catalogue scheme is a
management system that only an organisation can hold, so the phrase only
decides WHOSE certificate answers the item (§9.3): an item naming «ο
κατασκευαστής» uses the manufacturer rows, one also naming the bidder («ο
προσφέρων», «ο οικονομικός φορέας», «ο ανάδοχος», …) uses both, and anything
else uses the firm's own.

**Edition:** when the item names an edition (`:2015`) and the customer
declared a different one, the note keeps its validity status and adds «(η
προκήρυξη αναφέρει έκδοση 2015)». When either side has no edition, nothing is
added.

**Validity:** compared on Europe/Athens dates against the act's
final_submission_date. `valid_until` NULL → «χωρίς δηλωμένη ημερομηνία λήξης», never
✓. Past `valid_until` → the note says so. Nothing is hidden.

**Several schemes in one item** («ISO 9001 και ISO 14001»): one line per
scheme, each with its own status.

---

## 7. Where it shows

- **The checklist panel**: the note under the item, as in §4.
- **Print and Excel**: they are built from `view()`, so they carry the notes.
  Excel gets one extra column, «Από το προφίλ σας». Tender-checklist §10's
  "one source" rule holds.
- **/account/favorites**: an expiry warning only («⚠ πιστοποιητικό λήγει πριν
  την υποβολή») next to the progress figure. This is the case that loses bids.
- **Not** in the digest emails, the calendar or the fit score in this slice.
  (A certificate-aware fit component is plausible later. CPV_FLOOR shows how
  a hard requirement caps a score.)

**Who:** entitled readers (the checklist's own gate) who have at least one
declared certificate. *Changed while building:* the draft reused the fit
panel's `company_profile.is_active` switch, but that switch needs a usable CPV
history, so a firm with no award ledger could never see its certificates.
An admin entering a certificate is already the deliberate act, so no
second switch is needed. Everyone else gets the checklist exactly as it is
today. The panel's response for a customer with no certificates is
byte-identical to today's (test-enforced), and so is the Excel file's shape:
the extra column appears only when there are notes.

---

## 8. Editing

**Slice 1: admin only**, on the CRM card's Ταίριασμα tab, under the
existing profile: a small table (scheme dropdown from the catalogue, edition,
number, issuer, valid until) with add / edit / delete. This reuses the
"admin prepares the profile, then switches it on" flow that fit already has.

**Slice 2: customer self-service** on /account (a «Πιστοποιητικά» page),
source='customer'. It is a separate slice because it is the first customer-
editable part of the company profile and brings its own questions: does a
customer edit override an admin row, and is there an audit trail?

---

## 9. Decisions (the owner, 2026-09-24)

1. **Suggest, never tick**: yes.
2. **Admin-entered first**, customer self-service second: agreed.
3. **Manufacturer certificates**: optionally yes. They are declared with
   holder = manufacturer and the manufacturer's name. An item that names «ο
   κατασκευαστής» is answered from those rows, one naming the bidder too from
   both.
4. **Absence**: shown neutrally («δεν έχει δηλωθεί στο προφίλ σας»), and only
   for a customer who declared at least one certificate. Without a
   self-service editor yet, telling everyone else "not declared" would be
   noise they cannot act on.
5. **Expiry reminder emails**: later.
6. **Catalogue**: the measured list only. HACCP / ΕΛΟΤ 1416 is its own
   scheme. OHSAS 18001 cannot be declared (every such certificate lapsed in
   2021); a notice asking for it is answered by ISO 45001.

The questions as they were put:



1. **Suggest, never tick** (§4). Yes / no?
2. **Admin-entered first, customer self-service second** (§8). Or customer
   first?
3. **Manufacturer certificates:** medical tenders often ask that «ο
   κατασκευαστής» hold ISO 13485, and the bidder is a distributor. Should a
   customer declare "certificates of the manufacturers we carry"? Slice 1 says
   no: 13485 in a requirements item that names the manufacturer is left
   unmatched.
4. **Absence** (§4): never show "not in your profile"? Or show a neutral
   "δεν έχει δηλωθεί" so the customer knows to add it?
5. **Expiry reminders**: a certificate's `valid_until` could itself be a
   calendar event and an email ("λήγει σε 60 ημέρες"). It is useful but it is a
   new mail type, and deliverability is not done. Later?
6. **Catalogue:** the 12 in §2, plus ΕΛΟΤ 1435 / ΕΛΟΤ 1801 (older Greek
   H&S/quality schemes) if you see them in practice.

---

## 10. Tests (when built)

`tests/test_eligibility_eval.py`:

- pattern forms, in Greek, from real notice spellings: `ΕΝ ISO 9001:2015`,
  `ISO9001`, the 13458 typo;
- a product standard is never matched;
- OHSAS → 45001 works one way only;
- edition mismatch;
- validity before / on / after the deadline in Athens time, and NULL validity;
- several schemes in one item;
- per-user privacy (user B never sees A's notes);
- no certificates → the panel is byte-identical to today's;
- not entitled or profile inactive → no notes;
- print and xlsx carry the notes, and the xlsx cell is forced to a string;
- the isolation rule in both directions: `ai_summary.py` source never names
  `company_certificate`, and `eligibility_eval.py` never writes
  `act_ai_summary`.

Migration: one file, the table plus its index, with no `DO $$` blocks. Run on
local and Supabase before the code push. No RLS toggle on Supabase.
