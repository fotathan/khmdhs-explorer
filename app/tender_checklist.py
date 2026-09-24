"""
tender_checklist.py — the per-tender checklist and deadline set.

docs/specs/tender-checklist.md. Tier 2, item 3: "per-tender checklist +
deadline set, generated from the extraction".

What it is
----------
A VIEW over two things we already hold, plus one small table:

  * the AI summary's cached payload (proc.act_ai_summary) — the extraction,
    shared by every reader of the act;
  * the act's own record — final_submission_date, which the record owns and
    the model is never asked for (ai-summary spec §4);
  * proc.act_checklist_tick — which items THIS customer has ticked off.

No model call. Nothing is generated here: a checklist exists for an act
exactly when a CURRENT summary exists for it (its input_hash still matches),
and it is rebuilt from that payload on every request.

The isolation rule (ai-summary spec §3)
---------------------------------------
The summary is EXTRACTION — a property of the act, identical for every reader,
which is what makes it cacheable forever. The ticks are the customer's own.
Data flows ONE way: this module reads the payload and joins the customer's
ticks onto it at render time. It never writes proc.act_ai_summary, and
ai_summary.py never reads proc.act_checklist_tick. Both are test-enforced
(tests/test_tender_checklist.py), the same way fit.py's rule is.

What becomes a task
-------------------
Not every extracted fact is something to DO. The award weights and the
"attention" clauses are things to read; the timeline is the deadline set.
Tasks come from:

  eligibility   everything    — what the bidder must be or hold
  pricing       mandatory     — bid security, guarantees (a bank takes days)
  requirements  mandatory     — the specs you must meet on pain of exclusion
  submission    everything    — ΕΕΕΣ, signatures, how the files are structured

"mandatory" is the model's own obligation flag. A payment term or a desirable
spec is not a task, and a checklist of forty things is a checklist nobody uses.

Item identity
-------------
A tick is stored against item_key = sha256(section | fold(label) | fold(quote))
cut to 20 hex chars. The quote is verbatim source text, so a regenerated
summary that finds the same sentence under the same label keeps its ticks. A
regeneration that re-words a label loses that tick — it is not deleted, it
simply no longer matches, and the panel says how many such ticks there are
rather than hiding them.

The server only accepts a key that is in the CURRENT checklist, so the table
can never hold keys for items nobody was shown.

Dates
-----
The deadline set is the record's final_submission_date plus every timeline
item. A timeline item gets a DATE only when its value (or failing that, its
quote) names exactly one: several different dates are ambiguous and the item
is listed undated with its text, never guessed at. Everything is compared on
Europe/Athens local dates (working-day-deadlines spec §5). Each dated
deadline carries both calendar days left and WORKING days left
(app/workdays.py: weekends and Greek public holidays out, Μεγάλη Παρασκευή a
working day, 26 December not — the owner's decision, 2026-09-24).

Who
---
Entitled readers (has_access — testers, subscribers, admins), the same people
who can read the summary. Everyone else gets an empty answer from the panel,
never an error: the act page asks on every load.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

try:
    from app import ai_summary as _ai
    from app import textmatch as _tm
    from app import workdays as _wd
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import ai_summary as _ai
    import textmatch as _tm
    import workdays as _wd

ATHENS = ZoneInfo("Europe/Athens")

# (section key, group heading, which obligations count). None = every item.
# Order is the order a bidder works in: can we take part, what must we put up,
# can we meet the specs, how do we file it.
TASK_SECTIONS = (
    ("eligibility",  "Προϋποθέσεις συμμετοχής", None),
    ("pricing",      "Εγγυήσεις και οικονομικοί όροι", ("mandatory",)),
    ("requirements", "Υποχρεωτικές τεχνικές απαιτήσεις", ("mandatory",)),
    ("submission",   "Υποβολή προσφοράς", None),
)
TASK_SECTION_KEYS = tuple(k for k, _h, _o in TASK_SECTIONS)

KEY_RE = re.compile(r"^[0-9a-f]{20}$")


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
def item_key(section: str, label: str, quote: str) -> str:
    raw = "\x1f".join((section, _tm.fold(label or "").strip(),
                       _tm.fold(quote or "").strip()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


# --------------------------------------------------------------------------- #
# Dates in a timeline item
# --------------------------------------------------------------------------- #
# Folded (lower case, no accents, final sigma → σ) genitive month names, which
# is how a Greek date is written: «12 Ιουνίου 2026».
_MONTHS = {
    "ιανουαριου": 1, "φεβρουαριου": 2, "μαρτιου": 3, "απριλιου": 4,
    "μαιου": 5, "ιουνιου": 6, "ιουλιου": 7, "αυγουστου": 8,
    "σεπτεμβριου": 9, "οκτωβριου": 10, "νοεμβριου": 11, "δεκεμβριου": 12,
}
_NUMERIC_RE = re.compile(
    r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
_WORDS_RE = re.compile(r"\b(\d{1,2})(?:η|ης)?\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b")
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")


def _dates(text: str) -> list[tuple[dt.date, int]]:
    """Every valid calendar date written in `text`, with where it ends."""
    out = []
    folded = _tm.fold(text or "")
    for m in _NUMERIC_RE.finditer(folded):
        if m.group(1):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
        try:
            out.append((dt.date(y, mo, d), m.end()))
        except ValueError:
            continue
    for m in _WORDS_RE.finditer(folded):
        try:
            out.append((dt.date(int(m.group(3)), _MONTHS[m.group(2)],
                                int(m.group(1))), m.end()))
        except ValueError:
            continue
    return out


def parse_when(value: str, quote: str = "") -> tuple[dt.date | None, str | None]:
    """(date, "HH:MM") for a timeline item, or (None, None) when unsure.

    The value first — it is the model's distilled answer — then the quote,
    which is the source's own words. Exactly ONE distinct date or nothing:
    "questions until 12/06, answers by 16/06" is two milestones squeezed into
    one item, and picking either would put a wrong date in front of a bidder.
    A time is taken only when it follows the date in the same text.
    """
    for text in (value, quote):
        found = _dates(text)
        distinct = {d for d, _e in found}
        if len(distinct) != 1:
            if len(distinct) > 1:
                return None, None      # ambiguous: do not fall through to quote
            continue
        date, end = found[0]
        folded = _tm.fold(text)
        tm = _TIME_RE.search(folded, end)
        hhmm = f"{int(tm.group(1)):02d}:{tm.group(2)}" if tm else None
        return date, hhmm
    return None, None


def athens_today() -> dt.date:
    return dt.datetime.now(ATHENS).date()


# The holiday overrides (proc.public_holiday) are re-read at most this often
# per process. Greece moves a holiday weeks ahead, so minutes are plenty, and
# every worker converges without anyone having to "bump" a cache.
HOLIDAY_REFRESH_SECONDS = 600
_holidays_read_at: float | None = None


def refresh_holidays(c, *, force: bool = False) -> None:
    """Load proc.public_holiday into app/workdays.py when stale. A failure
    keeps the previous map (the computed set if there never was one): a
    holiday table hiccup must not take the checklist down."""
    import time
    global _holidays_read_at
    now = time.monotonic()
    if (not force and _holidays_read_at is not None
            and now - _holidays_read_at < HOLIDAY_REFRESH_SECONDS):
        return
    try:
        c.execute("SELECT day, is_holiday, name FROM proc.public_holiday")
        _wd.set_overrides(c.fetchall())
    except Exception:      # noqa: BLE001 — fall back to what we had
        return
    _holidays_read_at = now


# --------------------------------------------------------------------------- #
# Building the checklist — pure, no database
# --------------------------------------------------------------------------- #
def _anchor(paragraphs, item: dict) -> tuple[str | None, int | None]:
    """The full-text paragraph an item's quote sits in (same ids as the act
    page and the AI panel: ft-p-<n>)."""
    if item.get("source") != "full_text":
        return None, None
    offset = item.get("start") or 0
    for p in paragraphs:
        if p.start <= offset < p.end:
            return f"ft-p-{p.index}", p.index
    return None, None


def build(payload: dict, act: dict, *, today: dt.date | None = None) -> dict:
    """The checklist and deadline set for one act, before any ticks.

    `payload` is a VERIFIED summary payload (ai_summary.verify), so every item
    already survived the quote gate. Returns
      {"deadlines": [...], "groups": [...], "keys": set, "n_tasks": int}
    """
    today = today or athens_today()
    paragraphs = _tm.split_full_text(act.get("full_text") or "")
    by_key = {s.get("key"): s for s in (payload or {}).get("sections") or []}

    # ---- deadlines ----------------------------------------------------------
    deadlines = []
    fsd = act.get("final_submission_date")
    if fsd is not None:
        if isinstance(fsd, dt.datetime):
            local = fsd.astimezone(ATHENS) if fsd.tzinfo else fsd
            date, hhmm = local.date(), local.strftime("%H:%M")
        else:
            date, hhmm = fsd, None
        deadlines.append({"label": "Υποβολή προσφορών", "value": None,
                          "date": date, "time": hhmm, "source": "record",
                          "quote": None, "anchor": None, "para": None})
    for item in (by_key.get("timeline") or {}).get("items") or []:
        date, hhmm = parse_when(item.get("value") or "", item.get("quote") or "")
        anchor, para = _anchor(paragraphs, item)
        deadlines.append({"label": item.get("label"), "value": item.get("value"),
                          "date": date, "time": hhmm, "source": "ai",
                          "quote": item.get("quote"), "anchor": anchor,
                          "para": para,
                          "confidence": item.get("confidence")})
    for d in deadlines:
        d["days_left"] = (d["date"] - today).days if d["date"] else None
        d["workdays_left"] = (_wd.working_days_between(today, d["date"])
                              if d["date"] else None)
        d["holiday"] = _wd.holiday_name(d["date"]) if d["date"] else None
        d["is_past"] = d["date"] is not None and d["date"] < today
    # Dated first, soonest first; undated after, in the summary's own order.
    deadlines.sort(key=lambda d: (d["date"] is None, d["date"] or dt.date.max,
                                  d["time"] or "99:99"))

    # ---- tasks --------------------------------------------------------------
    groups, keys = [], set()
    for sec_key, heading, obligations in TASK_SECTIONS:
        items = []
        for item in (by_key.get(sec_key) or {}).get("items") or []:
            if obligations and item.get("obligation") not in obligations:
                continue
            key = item_key(sec_key, item.get("label"), item.get("quote"))
            if key in keys:
                continue                # the same sentence under the same label twice
            keys.add(key)
            anchor, para = _anchor(paragraphs, item)
            items.append({"key": key, "label": item.get("label"),
                          "value": item.get("value"),
                          "obligation": item.get("obligation"),
                          "confidence": item.get("confidence"),
                          "quote": item.get("quote"),
                          "source": item.get("source"),
                          "anchor": anchor, "para": para})
        if items:
            groups.append({"key": sec_key, "heading": heading, "items": items})

    return {"deadlines": deadlines, "groups": groups, "keys": keys,
            "n_tasks": len(keys)}


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def current_row(c, adam: str) -> tuple[dict, dict, dt.datetime | None] | None:
    """(act, payload, generated_at) when the act has a CURRENT summary.

    Current = its input_hash matches the act as it is now. A stale payload's
    items point into text that no longer exists; a checklist built on it
    would tick off requirements the notice may have dropped.
    """
    loaded = _ai.load_inputs(c, adam)      # notices only
    if loaded is None:
        return None
    act, sources = loaded
    if not sources:
        return None
    row = _ai.cached(c, adam, _ai.input_hash(sources))
    if not row or not row.get("payload"):
        return None
    return act, dict(row["payload"]), row.get("generated_at")


def current(c, adam: str) -> tuple[dict, dict] | None:
    """(act, payload) when the act has a CURRENT summary, else None."""
    got = current_row(c, adam)
    return None if got is None else got[:2]


def progress_for(c, user_id, adams, *, today: dt.date | None = None) -> dict:
    """{adam: {"n_done", "n_tasks", "next"}} for the acts among `adams` that
    have a current checklist — the favourites page's one-line summary of each
    (docs/specs/tender-checklist.md, slice 3).

    Batched: one query for which acts have a summary row at all, one for this
    user's ticks on all of them; only acts with a summary pay for the
    current-ness check. `next` is the soonest UPCOMING dated deadline the
    summary found (source 'ai') — the closing date is already on the card.
    n_done counts only ticks on items the current checklist still has, plus
    the customer's own items — the same arithmetic as the panel (view), so
    the two numbers can never disagree.
    """
    adams = [a for a in dict.fromkeys(adams or []) if a]
    if not adams or not _ai.enabled():
        return {}
    today = today or athens_today()
    c.execute("SELECT adam FROM proc.act_ai_summary WHERE adam = ANY(%s)",
              (adams,))
    have = [a for a in adams if a in {r["adam"] for r in c.fetchall()}]
    refresh_holidays(c)
    if not have:
        return {}
    c.execute("""SELECT adam, item_key FROM proc.act_checklist_tick
                  WHERE user_id = %s AND adam = ANY(%s)""", (user_id, have))
    ticked: dict[str, set] = {}
    for r in c.fetchall():
        ticked.setdefault(r["adam"], set()).add(r["item_key"])
    c.execute("""SELECT adam, count(*) AS n,
                        count(*) FILTER (WHERE done_at IS NOT NULL) AS n_done
                   FROM proc.act_checklist_own_item
                  WHERE user_id = %s AND adam = ANY(%s)
                  GROUP BY adam""", (user_id, have))
    own = {r["adam"]: (r["n"], r["n_done"]) for r in c.fetchall()}
    out = {}
    for adam in have:
        got = current(c, adam)
        if got is None:
            continue
        act, payload = got
        cl = build(payload, act, today=today)
        upcoming = [d for d in cl["deadlines"]
                    if d["source"] == "ai" and d["date"] and not d["is_past"]]
        n_own, own_done = own.get(adam, (0, 0))
        if not cl["n_tasks"] and not n_own and not upcoming:
            continue
        # Same arithmetic as view(): the summary's items plus the customer's own.
        out[adam] = {"n_done": len(ticked.get(adam, set()) & cl["keys"]) + own_done,
                     "n_tasks": cl["n_tasks"] + n_own,
                     "next": upcoming[0] if upcoming else None}
    return out


def milestones(c, adam: str) -> list[dict]:
    """The DATED deadlines the summary found for this act — the calendar's
    share of the deadline set (docs/specs/tender-checklist.md, slice 2).

    Only source 'ai': the record's own closing date is already the act's main
    calendar event (calendar_feed.act_event) and must not appear twice. Only
    dated items: "within three days of publication" has no place on a
    calendar until we can count it (app/workdays.py).

    Each carries `uid_key`, stable across polls and across a regeneration
    that keeps the label, and NOT derived from the date — a moved date must
    move the same event, which is what SEQUENCE (from `generated_at`) is for.
    Two items with the same label in one act get -2, -3 suffixes, in date
    order.
    """
    got = current_row(c, adam)
    if got is None:
        return []
    act, payload, generated_at = got
    out, seen = [], {}
    for d in build(payload, act)["deadlines"]:
        if d["source"] != "ai" or d["date"] is None:
            continue
        base = item_key("timeline", d["label"] or "", "")
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f"{base}-{seen[base]}"
        out.append({"uid_key": key, "label": d["label"], "value": d["value"],
                    "date": d["date"], "time": d["time"],
                    "generated_at": generated_at})
    return out


def ticks(c, user_id, adam: str) -> dict[str, dt.datetime]:
    c.execute("""SELECT item_key, done_at FROM proc.act_checklist_tick
                  WHERE user_id = %s AND adam = %s""", (user_id, adam))
    return {r["item_key"]: r["done_at"] for r in c.fetchall()}


def set_tick(c, user_id, adam: str, key: str, done: bool) -> None:
    """Tick or untick. Idempotent both ways. The caller has already checked
    that `key` is in the current checklist."""
    if done:
        c.execute("""INSERT INTO proc.act_checklist_tick (user_id, adam, item_key)
                     VALUES (%s, %s, %s)
                     ON CONFLICT (user_id, adam, item_key) DO NOTHING""",
                  (user_id, adam, key))
    else:
        c.execute("""DELETE FROM proc.act_checklist_tick
                      WHERE user_id = %s AND adam = %s AND item_key = %s""",
                  (user_id, adam, key))


# --------------------------------------------------------------------------- #
# The customer's own items (slice 4)
# --------------------------------------------------------------------------- #
# A line the customer writes themselves — "ask the bank for the guarantee".
# Private to them (every query is keyed on user_id), never near the shared
# summary. Bounded twice, like every table a signed-in user can write to: the
# text length (also a CHECK in the migration) and the rows per act.
OWN_TEXT_MAX = 200
OWN_MAX_PER_ACT = 50
_WS = re.compile(r"\s+")


class OwnItemError(ValueError):
    """A rejected own item. The message is a Greek UI string (a t() key)."""


def clean_own_text(text: str) -> str:
    """Whitespace collapsed, trimmed, and within bounds — or OwnItemError."""
    text = _WS.sub(" ", text or "").strip()
    if not text:
        raise OwnItemError("Γράψτε τι θέλετε να προσθέσετε.")
    if len(text) > OWN_TEXT_MAX:
        raise OwnItemError("Το κείμενο είναι πολύ μεγάλο (έως 200 χαρακτήρες).")
    return text


def own_items(c, user_id, adam: str) -> list[dict]:
    c.execute("""SELECT id, text, done_at, created_at
                   FROM proc.act_checklist_own_item
                  WHERE user_id = %s AND adam = %s
                  ORDER BY created_at, id""", (user_id, adam))
    return c.fetchall()


def add_own(c, user_id, adam: str, text: str) -> dict:
    """Add one. Raises OwnItemError on bad text or a full list."""
    text = clean_own_text(text)
    c.execute("""SELECT count(*) AS n FROM proc.act_checklist_own_item
                  WHERE user_id = %s AND adam = %s""", (user_id, adam))
    if c.fetchone()["n"] >= OWN_MAX_PER_ACT:
        raise OwnItemError("Έχετε φτάσει το όριο δικών σας στοιχείων για αυτή την πράξη.")
    c.execute("""INSERT INTO proc.act_checklist_own_item (user_id, adam, text)
                 VALUES (%s, %s, %s) RETURNING id, text, done_at, created_at""",
              (user_id, adam, text))
    return c.fetchone()


def set_own_done(c, user_id, adam: str, item_id: int, done: bool) -> bool:
    """Tick / untick. False when no such item is THIS user's on THIS act.
    Ticking an already-done item keeps its original done_at."""
    c.execute("""UPDATE proc.act_checklist_own_item
                    SET done_at = CASE WHEN %s THEN coalesce(done_at, now())
                                       ELSE NULL END
                  WHERE id = %s AND user_id = %s AND adam = %s
                  RETURNING id""", (done, item_id, user_id, adam))
    return c.fetchone() is not None


def delete_own(c, user_id, adam: str, item_id: int) -> bool:
    c.execute("""DELETE FROM proc.act_checklist_own_item
                  WHERE id = %s AND user_id = %s AND adam = %s
                  RETURNING id""", (item_id, user_id, adam))
    return c.fetchone() is not None


def view(c, user_id, adam: str, *, error: str = "") -> dict | None:
    """Everything the panel renders for this user and act, or None."""
    got = current(c, adam)
    if got is None:
        return None
    act, payload = got
    refresh_holidays(c)
    cl = build(payload, act)
    own = own_items(c, user_id, adam)
    if not cl["deadlines"] and not cl["groups"] and not own:
        return None
    done = ticks(c, user_id, adam)
    for g in cl["groups"]:
        for item in g["items"]:
            item["done_at"] = done.get(item["key"])
        g["n_done"] = sum(1 for i in g["items"] if i["done_at"])
    cl["own"] = own
    cl["own_done"] = sum(1 for o in own if o["done_at"])
    cl["own_full"] = len(own) >= OWN_MAX_PER_ACT
    # The headline counts everything on the customer's list: the summary's
    # items and their own. progress_for uses the same arithmetic.
    cl["n_done"] = sum(g["n_done"] for g in cl["groups"]) + cl["own_done"]
    cl["n_total"] = cl["n_tasks"] + len(own)
    # Ticks for items this summary no longer has (it was regenerated and a
    # label changed). Kept, counted, never silently dropped.
    cl["n_orphaned"] = len(set(done) - cl["keys"])
    cl["adam"] = adam
    cl["truncated"] = payload.get("truncated")
    cl["error"] = error
    cl["own_text_max"] = OWN_TEXT_MAX
    return cl


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def _reader(request: Request):
    """The signed-in, entitled user, or None."""
    user = getattr(request.state, "user", None)
    if not user or not user.get("has_access"):
        return None
    return user


def make_router(templates: Jinja2Templates, cursor, log_event=None) -> APIRouter:
    router = APIRouter(tags=["account"])

    def _render(request, cl):
        return templates.TemplateResponse(request, "_panel_checklist.html",
                                          {"cl": cl})

    @router.get("/act/{adam}/checklist", response_class=HTMLResponse)
    def panel(adam: str, request: Request):
        """The act page's checklist tab. EMPTY for anyone who may not see it,
        when the feature is off, and when there is no current summary — the
        tab then removes itself (act_page.js data-autohide)."""
        user = _reader(request)
        if user is None or not _ai.enabled():
            return HTMLResponse("")
        try:
            with cursor() as c:
                cl = view(c, user["id"], adam)
        except Exception:      # noqa: BLE001 — a panel must never 500 an act page
            if log_event:
                log_event("checklist_panel_failed", adam=adam)
            return HTMLResponse("")
        if cl is None:
            return HTMLResponse("")
        return _render(request, cl)

    # ---- the customer's own items --------------------------------------- #
    # Registered BEFORE /checklist/{key}: that route would otherwise match
    # ".../checklist/own" first and answer 404 for a malformed key.
    def _own_guard(request, adam):
        """(user, None) when this reader may write own items on this act,
        else (None, response)."""
        user = _reader(request)
        if user is None or not _ai.enabled():
            return None, HTMLResponse("", status_code=403)
        return user, None

    @router.post("/act/{adam}/checklist/own", response_class=HTMLResponse)
    def own_add(adam: str, request: Request, text: str = Form("")):
        user, deny = _own_guard(request, adam)
        if deny:
            return deny
        with cursor() as c:
            # Only on an act that HAS a checklist: the panel is the only
            # place the form exists, and it exists only then.
            if current(c, adam) is None:
                return HTMLResponse("", status_code=404)
            error = ""
            try:
                add_own(c, user["id"], adam, text)
            except OwnItemError as e:
                error = str(e)
            cl = view(c, user["id"], adam, error=error)
        return _render(request, cl) if cl else HTMLResponse("")

    @router.post("/act/{adam}/checklist/own/{item_id}", response_class=HTMLResponse)
    def own_toggle(adam: str, item_id: int, request: Request, done: str = Form("")):
        user, deny = _own_guard(request, adam)
        if deny:
            return deny
        with cursor() as c:
            if not set_own_done(c, user["id"], adam, item_id, done == "1"):
                return HTMLResponse("", status_code=404)
            cl = view(c, user["id"], adam)
        return _render(request, cl) if cl else HTMLResponse("")

    @router.post("/act/{adam}/checklist/own/{item_id}/delete",
                 response_class=HTMLResponse)
    def own_delete(adam: str, item_id: int, request: Request):
        user, deny = _own_guard(request, adam)
        if deny:
            return deny
        with cursor() as c:
            if not delete_own(c, user["id"], adam, item_id):
                return HTMLResponse("", status_code=404)
            cl = view(c, user["id"], adam)
        return _render(request, cl) if cl else HTMLResponse("")

    @router.post("/act/{adam}/checklist/{key}", response_class=HTMLResponse)
    def toggle(adam: str, key: str, request: Request, done: str = Form("")):
        user = _reader(request)
        if user is None or not _ai.enabled():
            return HTMLResponse("", status_code=403)
        if not KEY_RE.match(key):
            return HTMLResponse("", status_code=404)
        with cursor() as c:
            got = current(c, adam)
            if got is None:
                return HTMLResponse("", status_code=404)
            act, payload = got
            # Only a key the reader could have been shown. Anything else would
            # let a script fill the table with rows no checklist will ever read.
            if key not in build(payload, act)["keys"]:
                return HTMLResponse("", status_code=404)
            set_tick(c, user["id"], adam, key, done == "1")
            cl = view(c, user["id"], adam)
        if cl is None:
            return HTMLResponse("")
        return _render(request, cl)

    return router
