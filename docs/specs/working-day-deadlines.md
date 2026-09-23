# Spec: Working-day deadlines (Greek holidays) → the legal calendar of a notice

**Status:** Draft, 2026-09-23. Nothing built.
**Repo path:** `docs/specs/working-day-deadlines.md`
**Companion spec:** `docs/specs/calendar-feed.md` (consumes this module).
**Prompted by:** anunturi.sisap.ro (Romanian, Arxia). They sell exactly this as
the core of a 100 lei/month product, computed over Romanian holidays and
Law 98/2016 + 101/2016. We hold the same shape of data for Greece and show
none of it.

**Decisions taken:**
- The arithmetic module holds **no legal periods**. Periods are content and
  live beside the glossary, where they are already maintained (§4).
- **No new dependency.** Orthodox Easter is one verified function; a pypi
  holiday package is version drift and a supply-chain surface for ~40 lines (§3).
- All counting happens on **Europe/Athens local dates**, never on UTC
  instants (§5). This is the bug this spec exists to prevent.
- Computed holidays are overridable from a **table**, because Greece moves
  holidays by ministerial decision and a code deploy is the wrong response (§3c).
- Output is **informational and labelled as such**. ΚΗΜΔΗΣ/ΕΣΗΔΗΣ remains the
  authority; we do not become the source of a legal deadline (§8).

---

## 1. Problem

`proc.procurement_act.final_submission_date` is the only date the product puts
in front of a bidder, as a formatted date on the result card
(`_result_card.html:36`) and a countdown on the act page
(`beta_act.html:105`). It answers "when does this close".

It does not answer the questions that actually govern a bid:

- By when must I ask for clarifications, if I want an answer at all?
- Until when may I challenge the terms of this διακήρυξη?
- How many *working* days is "two weeks away", once Καθαρά Δευτέρα, Μεγάλη
  Παρασκευή, Δευτέρα του Πάσχα and Αγίου Πνεύματος are taken out?

The last one is the load-bearing question. Greek movable feasts swing across
seven weeks — Καθαρά Δευτέρα was 23 February in 2026 and is 15 March in 2027 —
and Easter week removes several working days in a block. A countdown in
calendar days is wrong exactly when it matters most, and nobody does this
arithmetic correctly by hand under time pressure.

**Goal.** From data we already hold and index, show a bidder the working-day
calendar of a notice: what falls when, counted properly, with the legal basis
named.

**Non-goals.**
- Becoming the authoritative source for a deadline (§8).
- Legal advice. We state a period and cite where it comes from; we do not tell
  anyone whether they qualify to use it.
- Changing anything in `digests.py`. The deadline digest keeps its own
  calendar-day `lead_days` marks in this slice (§9, phase 3).
- Deadlines we have no anchor for (§6 is explicit about which those are).

---

## 2. Shape of the work

Two modules, deliberately split, because one is arithmetic that is either right
or wrong and the other is legal content that changes when the law changes.

| Module | Holds | Changes when |
|---|---|---|
| `app/workdays.py` | Easter, holidays, working-day arithmetic | Never (or a new fixed holiday) |
| `app/deadlines.py` | Which periods exist, how long, from what anchor, citing what | The law changes |

`workdays.py` has no imports from the app, no database access and no knowledge
of procurement. It is testable as pure arithmetic and is the only place the
holiday set is decided.

---

## 3. `app/workdays.py`

### 3a. Orthodox Easter

Meeus' Julian algorithm, then +13 days to Gregorian. Valid 1900–2099, which is
far past any horizon this product has.

```python
def orthodox_easter(year: int) -> dt.date:
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = ((d + e + 114) % 31) + 1
    return dt.date(year, month, day) + dt.timedelta(days=13)
```

Verified against eight known years while writing this spec: 2023-04-16,
2024-05-05, 2025-04-20, 2026-04-12, 2027-05-02, 2028-04-16, 2029-04-08,
2030-04-28. All correct. The test in §7 pins the same table.

### 3b. The holiday set

Fixed:

| Date | Name |
|---|---|
| 1 Jan | Πρωτοχρονιά |
| 6 Jan | Θεοφάνεια |
| 25 Mar | Εικοστή Πέμπτη Μαρτίου |
| 1 May | Εργατική Πρωτομαγιά |
| 15 Aug | Κοίμηση της Θεοτόκου |
| 28 Oct | Επέτειος του «Όχι» |
| 25 Dec | Χριστούγεννα |
| 26 Dec | Σύναξη Θεοτόκου |

Movable, as offsets from Orthodox Easter Sunday:

| Offset | Name | 2026 | 2027 |
|---|---|---|---|
| −48 | Καθαρά Δευτέρα | 23 Feb | 15 Mar |
| −2 | Μεγάλη Παρασκευή | 10 Apr | 30 Apr |
| +1 | Δευτέρα του Πάσχα | 13 Apr | 3 May |
| +50 | Αγίου Πνεύματος | 1 Jun | 21 Jun |

Easter Sunday itself is always a Sunday and needs no entry.

> **Confirm before building — yours, not mine.**
> Μεγάλη Παρασκευή is listed above as a non-working day. Whether it counts as
> an αργία *for the purpose of computing a procurement deadline* is a legal
> question, not an arithmetic one, and the answer changes results in the single
> busiest week of the Greek calendar. Same question for 26 December. Decide
> both before this ships; the module structure makes either answer a one-line
> change, but the answer must be deliberate.

### 3c. Overrides — `proc.public_holiday`

1 May is moved by ministerial decision when it collides with Holy Week or a
weekend. That decision is published a few weeks ahead and cannot be computed.
Any scheme that requires a code deploy to absorb it will be wrong in
production at least once.

```sql
CREATE TABLE proc.public_holiday (
  day         date    PRIMARY KEY,
  name        text    NOT NULL,
  is_holiday  boolean NOT NULL DEFAULT true,
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
```

`is_holiday=false` is the other half: it *removes* a computed holiday, which is
what "1 May is observed on the 5th this year" actually needs (remove the 1st,
add the 5th).

Seeded empty. Loaded once per process and cached per year; an admin write
bumps the cache. An empty table means the computed set stands, so the feature
works before anyone touches it.

### 3d. Arithmetic

```python
def is_working_day(d: dt.date) -> bool          # not Sat/Sun, not a holiday
def add_working_days(start: dt.date, n: int) -> dt.date
def working_days_between(a: dt.date, b: dt.date) -> int
```

`add_working_days` accepts negative `n` and counts backwards; that is how every
"X days before submission" period is expressed, and having one function for
both directions removes an entire class of off-by-one.

Convention, stated once and tested: `add_working_days(d, 0)` returns `d`
unchanged even when `d` is a holiday. Counting starts from the *next* qualifying
day in the direction of travel. Both are pinned in §7 because both are the kind
of thing a later refactor silently flips.

---

## 4. `app/deadlines.py` — the periods

A table of rules, each naming its anchor, its length, its direction, and the
article it comes from. Nothing here is arithmetic; nothing in `workdays.py` is
legal.

```python
Rule = namedtuple("Rule", "key anchor days direction unit law glossary_slug")
```

- `anchor` — which column on the act the period runs from (§6).
- `unit` — `"working"` or `"calendar"`. Not every Greek period is in working
  days and assuming otherwise is a bug with a Greek accent.
- `glossary_slug` — the term in `app/glossary.py` that explains it, so the UI
  links the period to prose that already exists and is already maintained
  bilingually.

> **The periods themselves are left blank in this spec, on purpose.**
> I am not going to write Greek statutory periods into your repo from memory
> and have them read as verified. `app/glossary.py` already carries your
> thresholds and its docstring already says they are checked against
> ν. 4412/2016 as amended — that is the standard this table has to meet, and
> you are the one who can meet it.
>
> The rules I expect to exist, for you to fill in or strike out:
>
> | key | Runs from | To confirm |
> |---|---|---|
> | `clarification_request` | `final_submission_date`, backwards | period, unit, article |
> | `authority_answer` | `final_submission_date`, backwards | period, unit, article |
> | `submission` | `final_submission_date` | — (the date itself) |
> | `terms_challenge` | publication, forwards | period, unit, article; and whether the anchor is publication or the day the interested party learned of the notice |
>
> A rule with no confirmed period is simply absent from the table and renders
> nothing. Shipping three correct rows beats shipping four with one guess in it.

### 4a. `milestones(act) -> list[Milestone]`

Takes an act row, returns the rules whose anchor is present on it, each resolved
to a date, sorted. A rule whose anchor column is NULL yields nothing — never a
fabricated date. Each milestone carries `{key, date, is_past, working_days_away,
law, glossary_slug}`.

---

## 5. Timezone — the bug this section exists to prevent

`final_submission_date` is `timestamptz`. The periods are counted in Greek
calendar days.

A deadline of `2026-04-14 23:59+03:00` is `2026-04-14T20:59Z`. Counting from
the UTC date is right here and wrong for `2026-04-15 01:00+03:00`, which is
`2026-04-14T22:00Z` — a full day out, and always in the direction that tells a
bidder they have more time than they do.

**Rule: convert to `Europe/Athens` and take `.date()` before any arithmetic.**
Every entry point in `deadlines.py` does this conversion once, at the top. The
functions in `workdays.py` take `dt.date` and refuse `dt.datetime`, so the
conversion cannot be skipped by accident — the type system is the guard.

The countdown already on `beta_act.html:105` is client-side from an ISO string
and is a *calendar*-time countdown ("2 days left"). It stays as it is. The
working-day figure is a separate, server-rendered statement, because the browser
does not know about Καθαρά Δευτέρα and is not going to be taught.

---

## 6. What we can anchor, and what we cannot

Honest scoping. On `proc.procurement_act` we hold `final_submission_date`
(indexed, `ix_act_final_submission`), `submission_date`, `published_eu_date`,
`last_update_date`, `signed_date`.

- **A notice with a closing date** — everything anchored on
  `final_submission_date` works. This is the case that matters and it is the
  large majority of what a bidder looks at.
- **Challenging the terms of a διακήρυξη** — anchored on publication, which we
  have.
- **Challenging an award decision** — runs from the *notification* of that
  decision to the affected party. We do not hold that date and cannot infer it;
  it is per-bidder and not in the corpus. This milestone is **out of scope** and
  the UI must not imply we know it. If a bidder needs it, the glossary explains
  the rule and they count from their own notification.

`deadlines.py` never invents an anchor. A missing column is a missing
milestone.

---

## 7. Tests — `tests/test_workdays.py`, `tests/test_deadlines.py`

Pure-Python, no database except the override table.

1. **Easter**, pinned to the eight-year table in §3a.
2. **Movable feasts** derived from it, pinned for 2026 and 2027 (§3b table).
3. **Easter week arithmetic** — the cluster case. Ten working days back from a
   date just after Πάσχα must skip Μεγάλη Παρασκευή, Easter Monday and two
   weekends. Assert the exact date, computed by hand in the test, not by
   re-running the implementation.
4. **Direction symmetry** — `add_working_days(add_working_days(d, -n), n)`
   returns to a working day on or after `d` for a range of `d` and `n`.
5. **Zero and boundary** — `add_working_days(d, 0) == d` including when `d` is a
   Sunday and when `d` is 25 March.
6. **Overrides** — inserting `is_holiday=false` on a computed holiday makes it a
   working day; inserting a new date removes it. Cache invalidation included.
7. **Timezone** — an act closing `01:00+03:00` and one closing `23:59+03:00` on
   the same Athens date produce identical milestone dates. This is §5's bug, as
   a test.
8. **No anchor, no milestone** — an act with `final_submission_date IS NULL`
   yields an empty list, not a list of `None`s.
9. **Rules cite live glossary slugs** — every `glossary_slug` in the rules table
   resolves in `app/glossary.py`. Stops the citation rotting when a term is
   renamed, the same way the existing specs pin their cross-references.

---

## 8. Presentation, and not becoming the source

A block on the act page, under the existing deadline, for `type = 'notice'`
with a `final_submission_date`. Per milestone: the label, the date, the
working-days-away figure, the article, and a link to the glossary term.

Bilingual through the existing `t()`. New strings go in
`app/i18n_catalog.py` as usual.

One line of standing text, always visible, in both languages: the dates are
computed for guidance, and ΚΗΜΔΗΣ/ΕΣΗΔΗΣ is the authority. SISAP carries the
same disclaimer on every screen and in their FAQ; it is not decoration. We are
in a stronger position than they are — we show a period and name the article
rather than telling anyone what to do — and that posture should be visible.

`/help` gains a section (memory: *help-page-upkeep* — this is user-facing, so
`beta_help.html` is not optional).

---

## 9. Build order

Each slice ships on its own.

1. **`app/workdays.py` + `tests/test_workdays.py`.** No UI, no migration, no
   app wiring. Pure arithmetic, fully tested. This is the whole foundation and
   it is the smallest, most certain piece.
2. **Migration `proc.public_holiday` + the override layer.** Local and Supabase
   both, before anything depends on it (memory:
   *render-autodeploy-migrate-first*). Seeded empty.
3. **`app/deadlines.py` with the confirmed rules, plus the act-page block.**
   Only the rules §4 marks as confirmed. Regenerate `tests/proc_schema.sql`.
4. **`/help` + glossary cross-links.**
5. *(Later, separate decision)* **`lead_days` in working days.** `digests.py`
   counts calendar days today and the ledger `proc.digest_deadline_notice`
   stores the mark as an integer. Switching the unit changes which marks fire
   for acts already in flight. It is a behaviour change to a live email path
   with a "never break" note attached, so it is a slice of its own with its own
   decision, not a side effect of this one.

Slices 1–2 have no user-visible surface and cannot regress anything. Slice 3 is
the first one that needs your legal confirmations from §4.
