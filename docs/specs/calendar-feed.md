# Spec: Calendar feed (.ics) — tender deadlines in the customer's own calendar

**Status:** 2026-09-23. **Slices 1-4 built** and tested against a real DB —
`app/ics.py`, `/act/<adam>/calendar.ics`, favourites on the web, and the
subscribed feed (`app/calendar_feed.py`, migration `20260923075702_calendar_feed`,
applied LOCALLY only). Slices 5-6 not started.
**Repo path:** `docs/specs/calendar-feed.md`
**Companion spec:** `docs/specs/working-day-deadlines.md` (phase 5 consumes it;
phases 1–4 do not depend on it).
**Prompted by:** anunturi.sisap.ro, whose headline feature is a Gantt timeline
of every live participation. A subscribed .ics feed reaches the same place —
the bidder's own agenda, next to everything else they have to do that week —
for a fraction of the build, and it keeps working when our tab is closed.

**Decisions taken:**
- The feed is a **capability URL**. Calendar clients send no cookies; the token
  is the credential, and everything in §3 follows from that.
- A lapsed customer gets an **empty calendar, never a 403** (§5). A 403 makes
  Google permanently disable the subscription, and the recovery path is the
  customer re-adding a URL they no longer have.
- **No new dependency.** RFC 5545 output is one module; the `icalendar` package
  is not worth a line in `requirements.txt` for it. But §4 is not optional —
  the format has two traps that bite Greek text specifically.
- **Favourites first**, saved searches later and opt-in (§2). A saved search can
  match thousands of acts; a calendar that does that is uninstalled the same day.
- Password changes do **not** revoke the feed (§3c). Flagged as yours to confirm.

**Built differently from this draft (slice 1):**
- The module is `app/ics.py`, not `app/icalendar.py`. The app is sometimes
  run with `--app-dir=app`, which puts `app/` directly on `sys.path`; a module
  named `icalendar.py` there would shadow the PyPI package of that name for
  the whole process if anything ever pulled it in.
- An instant **omits DTEND** rather than emitting `DTEND == DTSTART` as §4c
  sketched. RFC 5545 §3.6.1 requires DTEND to be strictly after DTSTART, and
  defines an absent DTEND as ending at DTSTART -- so omitting it is the valid
  spelling of the same thing. `Event.end` is still accepted for a real
  interval.

**Built differently from this draft (slice 2):**
- The path is **`/act/<adam>/calendar.ics`**, not `/act/<adam>.ics`. Two
  segments would collide with the `/act/{adam}` page route -- whichever is
  registered first wins, and the loser renders an HTML 404 stub for an ADAM
  ending in `.ics`. Three segments is also how every other act sub-resource is
  spelled (`/ai`, `/occurrences`, `/attachments.zip`), and `seo.py` already
  treats a third segment as a sub-resource that is never indexable.
- The route is gated on **signed in**, matching `/export/acts` ('anti-scrape +
  DoS gate'), not on entitlement. Nothing in the file is hidden from a gated
  visitor: the title, authority, value and deadline are all in the act-page
  hero that anonymous callers already receive. The BUTTON lives in the page's
  `not gated` actions block, so a lapsed customer does not see it while a URL
  they kept still works.
- **No extra rate limit.** `/export/acts` throttles because it returns
  thousands of rows; this is one primary-key lookup, strictly cheaper than the
  act page beside it, which is unthrottled.
- Reminders reuse `digests.DEFAULT_LEAD_DAYS`, so the calendar and the
  deadline mail fire on the same days.

**Built differently from this draft (slice 3):**
- Routes: `GET /account/favorites` (the list), `POST` / `DELETE
  /account/favorites/<adam>` (the toggle). The toggle answers with its own
  next state (`_favorite_toggle.html` swaps itself), so there is no client-side
  state to drift. **Revised 2026-09-23:** the web module first wrote through
  `mobile_favorites`, but that module pulls in the uncommitted mobile API and
  `main.py` loads favourites at startup, so the web app could not deploy
  without it. `account_favorites.py` now has its own three statements with the
  same rules; the shared TABLE is the contract, and a test cross-checks the two
  copies when the mobile module is present.
- Gate: **signed in**, not entitled. A favourite writes the user's own row and
  exposes nothing. The button sits in the act page's `not gated` block, like
  the calendar button — same split as slice 2.
- **`MAX_FAVORITES` cap** (default 500, env-tunable), web path only. The table
  is writable by anyone signed in, which is why `MAX_SAVED_SEARCHES` exists; the
  mobile path predates this and is untouched. At the cap the toggle answers
  OFF with the reason, so the page tells the truth about what was written.
- The result-card star is **opt-in**: it renders only when the caller passes
  `favorites` (the search page does, the favourites page does). The digest
  results page passes nothing and is unchanged — `_result_card.html` promises
  to stay free of gating concerns, and this keeps that promise. Opting that
  page in later is one line in its route.
- Hidden Tender Service duplicates are **not** filtered from the list. The user
  bookmarked that exact act and `/act/<hidden>` already redirects; silently
  dropping a row they created is harder to explain. Matches mobile.

**Built differently from this draft (slice 4):**
- **Table shape.** `user_id` is the PRIMARY KEY (no `id`, no `revoked_at`):
  "one live URL per user" is then enforced by the database, a new link is one
  `INSERT ... ON CONFLICT (user_id) DO UPDATE` (a double click cannot leave two
  live URLs), and turning the feed off DELETES the row. Added `lang`: the
  fetching calendar server has no language cookie, so the language is fixed
  when the link is made.
- **The URL is shown once**, in the response to the create POST (which renders
  the page, `Cache-Control: no-store`, rather than redirecting — a redirect
  would carry the token in its own URL). Later visits show status only.
- **Access** = `auth.load_user(...)['has_access']`, the flag that decides who
  gets full pages on the site, rather than a second copy of the digest gate.
  The feed and the site cannot disagree about who is entitled.
- **Recent past deadlines stay** for `CALENDAR_PAST_DAYS` (30). §2 said future
  only, but then yesterday's deadline vanishes from the customer's calendar
  the next morning, which reads as data loss.
- **Reminder marks** = the union of the customer's active deadline-alert
  `lead_days`, through `digests.clean_lead_days`; the digest default otherwise.
- **DTSTAMP is derived from the data** (latest favourite / act change), never
  the clock. Otherwise every poll changes the body and no poll is a 304.
- **No per-IP rate limit**: Google polls every subscription from a few shared
  addresses. Malformed tokens are refused before the database.
- **Password change does not revoke** — §3c's recommendation, applied. It is
  still the owner's call; the change is one line in `auth.set_password`.
- `app/ics.py` gained **all-day events** (a `date` start → `DTSTART;VALUE=DATE`)
  for the lapsed notice.
- One definition of an act as an event: `calendar_feed.act_event`, used by
  the feed AND by the slice 2 download (refactored onto it).
- Found while checking the pages visually: the `acct-*` styles lived inside
  `account.html`, so slice 3's `/account/favorites` rendered unstyled. They are
  now one partial, `_acct_styles.html`, included by account, favourites and
  calendar (`account_mfa.html` keeps its own older copy).

---

## 1. Problem

Deadline pressure is the thing the product is for, and today it lives entirely
in our email and our web page. The customer's actual planning surface — the
calendar they open every morning, that their phone nags them from, that their
colleagues can see — has nothing from us in it.

The deadline digest (`email_digest_deadline.html`) puts a reminder in the inbox
on the marks in `subscription.lead_days`. An inbox is where mail goes to be
triaged. A calendar is where commitments go to be *scheduled around*, and a
submission deadline is a commitment.

**Goal.** A customer subscribes once to a URL, and from then on every tender
they care about appears in their own calendar, with reminders, updating itself
when a deadline moves.

**Non-goals.**
- A calendar UI inside the product. The customer already has one and it is
  better than anything we would build. This spec exists to avoid building it.
- Two-way sync, invitations, or anything involving a Google/Microsoft OAuth
  scope. We emit a file over HTTP. That is the whole integration.
- Replacing the deadline digest. Both survive; they reach different habits.

---

## 2. What is in the feed

**Phase 1 — favourites.** `proc.user_favorite_act` already exists
(`migrations/20260915170000_user_act_favorites.sql`): one row per (user, act),
an explicit bookmark, bounded by how many a person can be bothered to click.
Exactly the right shape.

> **Dependency worth knowing before you plan this.** Favourites are wired into
> the **mobile API only** (`app/mobile_favorites.py`). There is no favourite
> button anywhere on the web — `grep favorite app/main.py` returns nothing. A
> web customer therefore has no way to put anything in this feed. Adding the
> button is small and the table and ownership logic are done, but it is a real
> prerequisite and it is in the build order at §8 as its own slice.

**Phase 2 — saved searches, opt-in per search.** A checkbox on
`/account/searches`, next to the alert controls that are already there. Bounded
by a hard cap (`CALENDAR_MAX_EVENTS`, default 500) taking the soonest deadlines
first, for the same reason `digests.py` caps at `DIGEST_ITEM_CAP`.

Only acts with a `final_submission_date` in the future, and never a cancelled
one — the deadline digest already refuses to chase cancelled acts and this must
agree with it. A favourited act that gets cancelled is not dropped from the
feed, it is emitted `STATUS:CANCELLED` (§4d).

---

## 3. The token

### 3a. Why it is not a session

`/digests/<token>` requires a signed-in user and checks ownership
(`digests.py:make_results_router`). That is right for a page a human opens from
an email. It is impossible here: Google Calendar and Outlook fetch the URL from
a server, with no cookies and no way to sign in. The URL must authenticate by
itself.

### 3b. Storage

The pattern `app/login_links.py` already uses: 32 random bytes,
`secrets.token_urlsafe(32)`, shown to the customer once, stored as sha256.

```sql
CREATE TABLE proc.calendar_feed (
  id           bigserial PRIMARY KEY,
  user_id      bigint NOT NULL REFERENCES proc.app_user(id) ON DELETE CASCADE,
  token_hash   text NOT NULL UNIQUE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  revoked_at   timestamptz,
  last_fetch   timestamptz,
  fetch_count  bigint NOT NULL DEFAULT 0
);
CREATE INDEX ix_calendar_feed_live ON proc.calendar_feed (user_id)
  WHERE revoked_at IS NULL;
```

Unlike a login link the token does not expire — a calendar subscription that
dies after fifteen minutes is not a feature. `last_fetch`/`fetch_count` are
there so an admin can answer "is this customer actually using it", which is the
question that decides whether the feature was worth building.

One live token per user. Rotating revokes the old one; the UI says plainly that
the old URL stops working and has to be re-added, because a silent break is
worse than a warned one.

### 3c. What kills it

| Event | Revokes? |
|---|---|
| Account deactivated (`is_active=false`) | **Yes** — checked on every fetch |
| Customer clicks "rotate" | Yes, that is what it is |
| Password change | **No** — see below |
| Email change | No |

`auth.kill_login_links` burns sign-in links on `set_password` and `set_email`,
correctly: those are credentials that complete a login. This token is not. It
reads a list of public tender deadlines that the customer chose to bookmark; it
cannot sign in, cannot change anything, and grants nothing the corpus does not
already publish. Revoking it on a password change would silently empty a
customer's calendar weeks later, with no message and no obvious cause.

> **Yours to confirm.** If you would rather it behave like a credential and
> rotate with the password, it is one line in `auth.set_password` — but then
> `/account` has to tell the customer at the moment they change it, or the
> failure is invisible. My recommendation is the table above.

### 3d. Guards

- Token shape checked by regex *before* any database work, exactly as
  `digests.py` does it (`^[A-Za-z0-9_-]{16,128}$`).
- Constant-time comparison is unnecessary — the lookup is by hash on a unique
  index, so there is no per-character oracle.
- Entitlement per §5.
- `/calendar` added to **both** `seo._DISALLOW_PATHS` and
  `seo._NOINDEX_PREFIXES`. A capability URL must never reach a sitemap or a
  crawler, and those two lists are where that is decided.
- `Referrer-Policy: no-referrer` on the response, so the token cannot leak out
  through a referer header.

---

## 4. Emitting RFC 5545 — `app/ics.py`

A pure module: rows in, text out, no database, no request. Easy to test
exhaustively, which matters because the failure mode of bad ICS is a client
that silently imports nothing.

### 4a. The two traps

**Line folding.** Lines are limited to 75 **octets**, continuation lines
beginning with a single space. Greek is two bytes per character in UTF-8, so
folding by character count produces lines up to 150 octets, and folding at an
arbitrary byte can split a multi-byte sequence and corrupt the text. Fold on
octet boundaries, never inside a UTF-8 sequence. This is the trap that will
mangle Greek titles specifically, and it will look fine in every ASCII test.

**Escaping.** In TEXT values: `\` → `\\`, `;` → `\;`, `,` → `\,`, newline →
`\n`. Colon is *not* escaped in TEXT values. Tender titles contain commas and
semicolons constantly ("Προμήθεια ειδών, 3 τμήματα"), so an unescaped title
silently truncates the field at the comma.

Also: **CRLF** line endings, mandatory, everywhere.

### 4b. Calendar headers

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//KHMDHS Explorer//Calendar Feed//EL
CALSCALE:GREGORIAN
X-WR-CALNAME:ΚΗΜΔΗΣ — Προθεσμίες
X-WR-TIMEZONE:Europe/Athens
REFRESH-INTERVAL;VALUE=DURATION:PT6H
X-PUBLISHED-TTL:PT6H
```

No `METHOD`. A subscription feed with `METHOD:PUBLISH` is read by some clients
as an iTIP message rather than a calendar, which is not what we want.
`X-WR-*` are non-standard and universally honoured; the refresh pair is a hint
clients may ignore (§6).

### 4c. One deadline

```
BEGIN:VEVENT
UID:<adam>@khmdhs
DTSTAMP:20260923T090000Z
DTSTART:20260414T205900Z
DTEND:20260414T205900Z
SUMMARY:Λήξη υποβολής — <title>
DESCRIPTION:<authority>\nΠροϋπολογισμός: …\n<url>
URL:https://…/act/<adam>
SEQUENCE:<n>
STATUS:CONFIRMED
BEGIN:VALARM
TRIGGER:-P7D
ACTION:DISPLAY
DESCRIPTION:Λήξη υποβολής σε 7 ημέρες
END:VALARM
END:VEVENT
```

- **UID** — stable and globally unique. `<adam>@khmdhs` is both. Stability is
  what makes an update an update instead of a duplicate.
- **Times in UTC** (`Z`). The client renders them in the viewer's zone, which
  for these customers is Athens. Emitting a `TZID` would require shipping a
  `VTIMEZONE` block; UTC avoids it entirely and is exactly correct.
- **Duration.** `DTSTART == DTEND` at the deadline instant. Alternative worth
  considering: start 30 minutes earlier so the block visually leads up to the
  cutoff in a day view. Either is fine; pick one and keep it.
- **SEQUENCE** — **the one that silently breaks things.** A client that has
  already imported a UID ignores a changed `DTSTART` unless `SEQUENCE` is
  higher. A moved deadline that does not move in the customer's calendar is
  worse than no feature at all, because they trust it. Derive it from the act's
  `last_update_date` (integer seconds since a fixed epoch, monotonic by
  construction) rather than a counter we would have to store and could get
  wrong.
- **VALARM** — reuse the customer's own `subscription.lead_days` when they have
  a deadline alert, else the same `{7,1}` default the digest uses. One `VALARM`
  per mark. This is where the feature quietly replaces SISAP's alerting: their
  server sends the reminders, ours lets the customer's own calendar do it, on
  the device they already trust for reminders.

### 4d. Cancelled

`STATUS:CANCELLED`, same UID, bumped `SEQUENCE`. **Do not simply drop the
VEVENT** — several clients keep an event they have already imported when it
vanishes from the feed, leaving a dead deadline in the customer's calendar
forever. Cancellation has to be stated, not implied.

---

## 5. Entitlement, and why it is not a 403

Same gate as the digests: `auth.ENTITLED_STATUSES` (`tester`, `subscriber`)
plus admins, matching `digests.active_subscriptions`.

A customer whose grant has lapsed gets **200 with a valid, nearly empty
calendar** containing a single all-day event on today's date: "Η συνδρομή σας
έχει λήξει — οι προθεσμίες θα επανεμφανιστούν με την ανανέωση."

Because a 403 or 404 makes Google Calendar disable the subscription outright,
and Outlook stop polling. The customer then has to find and re-add a URL they
were shown once, months ago. When they renew, the empty feed simply fills back
up on the next poll, with no action from anyone. The failure mode chooses
itself.

A revoked token or a deactivated account **does** get a 404 — there the goal is
that the URL stops working.

---

## 6. Caching and load

Calendar clients poll on their own schedule and ignore `REFRESH-INTERVAL` when
they feel like it. Google is unpredictable, in the hours range; Outlook
similar. Assume several fetches a day per subscriber, forever.

- `ETag` = hash of the rendered body. Return **304** on `If-None-Match`. Most
  polls change nothing, so most polls become 304s.
- `Cache-Control: private, max-age=1800`.
- The query is bounded by favourites (§2) and `CALENDAR_MAX_EVENTS`, so it is
  small and indexed by construction. `ix_act_final_submission` already exists.

Even so, the body is rendered before the ETag can be computed. If polling ever
shows up in the Render metrics, cache the rendered body per user keyed on
`max(last_update_date)` across their set — but not before, because that is a
cache to invalidate and this is a feature that may get twelve users.

---

## 7. Surfaces

**`/account` — one panel.** The feed URL, a copy button, a `webcal://` link
(one click to subscribe in most clients), short instructions for Google
Calendar / Outlook / Apple Calendar, and "Δημιουργία νέου συνδέσμου" with the
warning from §3b. If no token exists yet, a single button creates one — nothing
is minted for customers who never ask.

**Per-act download — no token needed.** `/act/<adam>.ics` returns a one-event
calendar for an act the viewer can already see. No table, no credential, no
subscription; it reuses `app/ics.py` and nothing else. It is the cheapest
useful thing in either of these specs and it is worth shipping first (§8).

**`/help`** gains a section (memory: *help-page-upkeep*).

---

## 8. Build order

1. **`app/ics.py` + `tests/test_ics.py`.** Pure emitter. No
   migration, no routes, no auth. All of §4 including the folding and escaping
   tests — Greek titles with commas and semicolons, a title long enough to fold
   mid-word, a 200-character title.
2. **`/act/<adam>.ics`.** One route, no new table, immediately useful, and it
   proves the emitter against real clients before any of the token machinery
   exists. Ship it and open the file in Apple Calendar, Google and Outlook.
3. **Favourites on the web.** The §2 prerequisite: a button on the act page and
   the result card, a list under `/account`. The table, the ownership rules and
   the queries are all done in `app/mobile_favorites.py`; this is a web surface
   over existing logic, not new behaviour.
4. **Migration `proc.calendar_feed` + `/calendar/<token>.ics` + the `/account`
   panel.** Local and Supabase before the code push (memory:
   *render-autodeploy-migrate-first*); regenerate `tests/proc_schema.sql`;
   add `/calendar` to both lists in `seo.py`.
5. *(Optional, later)* **Saved-search opt-in** per §2.
6. *(Needs the other spec)* **Milestones as events** — once
   `deadlines.milestones()` exists, each confirmed rule becomes its own VEVENT
   with its own UID (`<adam>-clarification@khmdhs`). The clarification cutoff in
   a bidder's calendar is worth more than the submission date, because it is
   the one they do not know about.

Slices 1 and 2 are a single sitting and depend on nothing. Slice 3 is worth
doing regardless of whether the feed ever ships — a favourite button is missing
from the web product today and that is a gap on its own.

---

## 9. Tests

`tests/test_ics.py` (pure) and `tests/test_calendar_feed.py` (routes).

1. **Folding** never splits a UTF-8 sequence; every emitted line ≤ 75 octets;
   unfolding reproduces the input exactly. Property-style over Greek strings.
2. **Escaping** — a title containing `, ; \` and a newline round-trips.
3. **CRLF** everywhere, including the last line.
4. **SEQUENCE** rises when `last_update_date` rises, and is stable when it does
   not. The §4c failure, pinned.
5. **Cancelled** acts emit `STATUS:CANCELLED` and are present, not absent.
6. **UID stability** across two renders of the same act.
7. **Token** — unknown, malformed, revoked and deactivated-account tokens all
   404, and the malformed one without touching the database.
8. **Lapsed entitlement returns 200** with a valid empty calendar, not 403.
   This is §5's decision and it is the one a future refactor will helpfully
   "fix" into a 403.
9. **Ownership** — user A's token never yields user B's favourites.
10. **ETag/304** on an unchanged feed.
11. **`/calendar` is in `seo._DISALLOW_PATHS` and `_NOINDEX_PREFIXES`**, and no
    sitemap contains it.
