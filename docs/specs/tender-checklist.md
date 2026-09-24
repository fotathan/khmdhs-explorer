# Spec: Per-tender checklist + deadline set

**Status:** slice 1 built 2026-09-24 (branch `feat/tender-checklist`).
**Roadmap:** Tier 2, "per-tender checklist + deadline set, generated from the
extraction" — after fit scoring and attachments-to-prod, which is what makes
the extraction worth building on.
**Depends on:** `docs/specs/ai-summary.md` (the extraction it is built from),
`docs/specs/working-day-deadlines.md` (not built yet — see slice 3).

---

## 1. Problem

The AI summary tells a bidder what a notice requires. It does not help them
*work through* it: which of the twelve things have I already sorted out, and
which date comes first? Today that is a spreadsheet next to the browser.

**Goal.** On every notice that has a summary, a tab that answers two
questions: *what do I have to prepare* (a list the customer ticks off) and
*by when* (every known date, in order, counted from today).

**Non-goals.** Deciding anything. The checklist is the notice's requirements,
not a verdict on the customer ("the notice requires ISO 9001", never "you lack
ISO 9001") — the same extraction/evaluation line as ai-summary §3.

---

## 2. Decisions taken (each reversible)

1. **A view, not a second extraction.** The checklist is rebuilt on every
   request from the act's *current* summary payload plus the record. No model
   call, no new prompt, no cost. An act has a checklist exactly when it has a
   current summary. Summaries are still admin-generated (ai-summary §12), so
   coverage follows wherever admins generate them.
2. **What becomes a task:**

   | Summary section | In the checklist |
   |---|---|
   | `eligibility` | every item |
   | `pricing` | `mandatory` only (bid security, guarantees — not payment terms) |
   | `requirements` | `mandatory` only |
   | `submission` | every item |
   | `award`, `attention` | never — things to read, not to do |
   | `timeline` | the deadline set, not tasks |

   The obligation flag is the model's own. If the lists turn out too long or
   too short on real notices, this table is the one place to change it
   (`tender_checklist.TASK_SECTIONS`).
3. **Deadlines** = the record's `final_submission_date` (labelled as coming
   from the record) + every `timeline` item. An item is dated only when its
   value — or failing that its quote — names exactly **one** date. Two dates in
   one item are listed undated with their text; we never pick one.
   Dates are compared on **Europe/Athens** local dates. "Days left" are
   **calendar** days, and the panel says so.
4. **Ticks are per user**, in `proc.act_checklist_tick`, one row per done
   item. Not on `act_ai_summary` (shared, must carry no customer data) and not
   on `user_favorite_act` (you can work a checklist without starring, and
   un-starring must not throw the work away).
5. **Item identity** = sha256(section | folded label | folded quote)[:20]. A
   regenerated summary that finds the same sentence under the same label keeps
   its ticks. A re-worded label loses the match; the old tick is kept and the
   panel counts it ("1 σημείωση αφορά στοιχεία που η σύνοψη δεν περιέχει
   πλέον") rather than letting the number silently drop.
6. **The server only stores keys from the current checklist.** A POST for any
   other key is a 404, so the table cannot fill with rows nobody was shown.
7. **Who:** entitled readers (`has_access`) — the same people who can read the
   summary. The GET answers empty for everyone else (the tab removes itself);
   the POST answers 403.
8. **No new switch.** It lives and dies with `AI_SUMMARY_ENABLED`.

---

## 3. Isolation

The summary is one row per act, served to everyone. Data flows one way:
`tender_checklist.py` reads the payload and joins the customer's ticks at
render time. It never writes `act_ai_summary`, never reads a company profile,
and `ai_summary.py` never mentions the tick table. All three are
test-enforced (`tests/test_tender_checklist.py`).

---

## 4. What was built (slice 1)

| File | What |
|---|---|
| `migrations/20260924120000_tender_checklist.sql` | `proc.act_checklist_tick` |
| `app/tender_checklist.py` | `build()` (pure), `view()`, `parse_when()`, the two routes |
| `app/templates/_panel_checklist.html` | the panel; swaps itself on every tick |
| `app/static/css/ai_summary.css` | `.cl-*` styles, appended |
| `app/templates/beta_act.html` | the «Λίστα ελέγχου» tab, after the summary |
| `app/main.py` | router include, next to the AI panel routes |
| `app/templates/beta_help.html` | «Λίστα ελέγχου και προθεσμίες» under the AI section |
| `app/i18n_catalog.py` | `_CHECKLIST` (also translates the AI panel's shared chrome) |

Routes: `GET /act/<adam>/checklist`, `POST /act/<adam>/checklist/<key>`
(form `done=1` ticks, anything else unticks).

---

## 5. Next slices (not built)

1. **Deadlines into the calendar.** Dated timeline milestones as extra events
   in `/act/<adam>/calendar.ics` and in the subscribed feed for favourites.
   `calendar_feed.act_event` is the one definition of an act as an event, so
   this needs a second, milestone-shaped event with its own UID scheme — and
   its SEQUENCE must move when the summary is regenerated.
2. **Progress on /account/favorites** — "4 / 11" on each card at stage
   `bidding`/`submitted`. Needs one summary lookup per favourite; batch it.
3. **Working days.** When `app/workdays.py` lands
   (working-day-deadlines spec, slices 1–2), show working days left next to
   calendar days. Blocked on the user's decision about Μεγάλη Παρασκευή and
   26 December.
4. **The customer's own items** ("call the bank", "ask Γιώργος for the CV") —
   a free-text row type on the same table, keyed separately.
5. **Printable / exportable list** for the person who assembles the envelope.
6. **Evaluation layer** (ai-summary §14): pre-tick eligibility items the
   company profile provably satisfies. Needs declared certificates on the
   profile first, and must stay out of the shared payload.

---

## 6. Tests

`tests/test_tender_checklist.py`: date parsing (numeric, ISO, Greek month
names, ambiguity, invalid dates), item identity, which sections become tasks,
deadline order and Athens-date counting, stale/missing summary → nothing, tick
idempotency and per-user privacy, key validation, orphaned-tick counting,
gating, the act-page mount, and both directions of the isolation rule.
