"""
calendar_feed.py — the subscribed calendar feed, and the one definition of
"an act as a calendar event".

Spec: docs/specs/calendar-feed.md, slices 4 and 5.

What it is
----------
A customer creates ONE private URL on /account/calendar and pastes it into
Google Calendar, Outlook or Apple Calendar once. From then on every act they
have favourited appears on its submission deadline, with reminders, and moves
when the authority moves the deadline — without our tab being open and without
another email. /calendar/<token>.ics is what their calendar server fetches.

Saved searches (slice 5) join in only when the customer ticks them, one by one,
on /account/searches. A search can match thousands of acts and a calendar that
fills up with them is uninstalled the same day, so:

  * favourites always go in first — they are explicit, one click each;
  * search matches fill what is left of CALENDAR_MAX_EVENTS, the soonest
    UPCOMING deadlines first, then the recent past — but only deadlines that
    closed AFTER the search was ticked. So what was in the calendar does not
    vanish the morning after it closes, and ticking a broad search does not
    backfill a month of closed tenders nobody asked to see;
  * a cancelled act is never brought in by a search (the deadline digest
    refuses to chase one, and this must agree with it). A cancelled FAVOURITE
    is still stated as cancelled, as before;
  * each event says which saved search brought it in, so a customer can tell
    why it is there and which box to untick.

Why the URL is the credential
-----------------------------
A calendar server fetches the feed from its own machines, with no cookies and no
way to sign in. So the token in the URL IS the authentication, and everything
below follows from that:

  * 32 random bytes, shown to the customer ONCE, stored only as sha256 — the
    proc.login_link discipline. A leaked table must not leak working URLs, so
    "show it again" is impossible by design; "make a new one" is the answer.
  * One live URL per user, enforced by the PRIMARY KEY on user_id. Making a new
    link is one INSERT ... ON CONFLICT DO UPDATE, so a double click can never
    leave two live URLs. Turning the feed off deletes the row.
  * A deactivated account's URL is a 404 (auth.load_user returns nothing for
    it), as is an unknown or malformed token — the malformed one without
    touching the database.
  * A password change does NOT kill the URL. It cannot sign anyone in or change
    anything; it reads deadlines the customer chose to bookmark. Killing it
    would silently empty their calendar weeks later with no visible cause.
    (Spec §3c flagged this as the owner's call; this is the recommended answer.)

Why a lapsed customer gets 200, not 403
---------------------------------------
Google Calendar permanently disables a subscription that answers 403/404, and
the customer would then have to find and re-add a URL they saw once, months ago.
So a customer without access gets a valid calendar holding ONE all-day notice
("your subscription has lapsed"), and when they renew, the deadlines come back
on the next poll with nobody doing anything. "Access" is auth.load_user's
has_access — the same flag that decides who gets full pages on the site, so the
feed and the site can never disagree about who is entitled.

Polling
-------
Calendar servers poll on their own schedule, several times a day per subscriber,
forever, from a handful of shared addresses. Hence:

  * no per-IP rate limit — Google's shared servers would throttle each other's
    customers; the lookup is one unique-index hit and bad tokens never reach it;
  * an ETag over the body and 304 on If-None-Match, so most polls are empty;
  * a DETERMINISTIC body: DTSTAMP is derived from the data (the latest
    favourite / act change), never from the clock. A body stamped "now" would
    change on every poll and no poll could ever be a 304.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import secrets
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

try:
    from app import auth as _auth
    from app import digests as _digests
    from app import i18n as _i18n
    from app import ics as _ics
    from app import seo as _seo
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import auth as _auth
    import digests as _digests
    import i18n as _i18n
    import ics as _ics
    import seo as _seo

PAGE = "/account/calendar"

# How many events one feed carries, soonest deadline first. The favourites cap
# (account_favorites.MAX_FAVORITES, also 500) means this rarely binds; it is here
# so the body stays bounded whatever happens to that cap later.
CALENDAR_MAX_EVENTS = max(1, int(os.environ.get("CALENDAR_MAX_EVENTS") or 500))

# Deadlines stay in the feed this many days after they pass. Dropping them the
# moment they close would make yesterday's deadline vanish from the customer's
# calendar, which reads as data loss; a month of history does not.
CALENDAR_PAST_DAYS = max(0, int(os.environ.get("CALENDAR_PAST_DAYS") or 30))

# token_urlsafe alphabet, the same guard /digests/<token> uses: anything else
# cannot be a token, so it is refused before the database is touched.
_TOKEN_OK = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

# The feed's DTSTAMP when it has nothing to derive one from (an empty feed).
_EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# The token
# --------------------------------------------------------------------------- #
def _hash(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def issue(c, user_id: int, lang: str) -> str:
    """Create — or replace — the user's feed URL. Returns the RAW token, the
    only time it exists outside the customer's calendar. Replacing resets the
    fetch counters: they describe the URL, and the old URL is dead."""
    raw = secrets.token_urlsafe(32)
    c.execute("""INSERT INTO proc.calendar_feed (user_id, token_hash, lang)
                 VALUES (%s, %s, %s)
                 ON CONFLICT (user_id) DO UPDATE
                    SET token_hash  = EXCLUDED.token_hash,
                        lang        = EXCLUDED.lang,
                        created_at  = now(),
                        last_fetch  = NULL,
                        fetch_count = 0""",
              (user_id, _hash(raw), "en" if lang == "en" else "el"))
    return raw


def turn_off(c, user_id: int) -> None:
    c.execute("DELETE FROM proc.calendar_feed WHERE user_id=%s", (user_id,))


def get_feed(c, user_id: int):
    c.execute("""SELECT user_id, lang, created_at, last_fetch, fetch_count
                 FROM proc.calendar_feed WHERE user_id=%s""", (user_id,))
    return c.fetchone()


def lookup(c, raw: str):
    """The feed row for a presented token, or None."""
    c.execute("""SELECT user_id, lang, created_at
                 FROM proc.calendar_feed WHERE token_hash=%s""", (_hash(raw),))
    return c.fetchone()


def record_fetch(c, user_id: int) -> None:
    c.execute("""UPDATE proc.calendar_feed
                    SET last_fetch = now(), fetch_count = fetch_count + 1
                  WHERE user_id=%s""", (user_id,))


def feed_url(request: Request, raw: str) -> str:
    return f"{_seo.base_url(request)}/calendar/{raw}.ics"


def webcal_url(url: str) -> str:
    """The same URL on the webcal: scheme — one click subscribes in most
    calendar apps instead of downloading a file."""
    return re.sub(r"^https?://", "webcal://", url)


# --------------------------------------------------------------------------- #
# One act as one event — shared with /act/<adam>/calendar.ics
# --------------------------------------------------------------------------- #
def lead_days_for(c, user_id: int) -> tuple:
    """The reminder marks: the customer's own, from their ACTIVE deadline
    alerts (all of them, merged), else the digest default — so the calendar
    reminds on the same days the deadline email does."""
    c.execute("""SELECT lead_days FROM proc.digest_subscription
                 WHERE user_id=%s AND is_active AND layout='deadline'""",
              (user_id,))
    marks = [int(d) for r in c.fetchall() for d in (r["lead_days"] or [])]
    return _digests.clean_lead_days(marks)


def _alarm_text(days: int, lang: str) -> str:
    tr = lambda s: _i18n.translate(s, lang)      # noqa: E731
    if days == 0:
        return tr("λήγει σήμερα")
    if days == 1:
        return tr("λήγει αύριο")
    return tr("λήγει σε {n} ημέρες").replace("{n}", str(days))


def act_event(row, *, lang: str, base_url: str, lead_days) -> _ics.Event:
    """An act row -> its deadline as an ics.Event.

    The row needs adam, title, final_submission_date, authority_name,
    resolved_value / total_cost_with_vat, last_update_date and cancelled — the
    columns act_service.fetch_core and this module's feed query both return.
    ONE definition, used by the one-off download and by the feed, so the two
    can never describe the same deadline differently.
    """
    tr = lambda s: _i18n.translate(s, lang)      # noqa: E731
    adam = row["adam"]
    title = (row.get("title") or "").strip()
    summary = f"{tr('Λήξη υποβολής')} — {title}" if title else tr("Λήξη υποβολής")

    lines = []
    if row.get("authority_name"):
        lines.append(row["authority_name"])
    value = row.get("resolved_value") or row.get("total_cost_with_vat")
    if value:
        # Same Greek thousands separator the act page uses.
        lines.append(f"{tr('Αξία με ΦΠΑ')}: € "
                     + "{:,.0f}".format(value).replace(",", "."))
    lines.append(f"{tr('ΑΔΑΜ')}: {adam}")
    if row.get("searches"):
        # Which ticked saved search brought it in: the customer's own names,
        # never translated. Favourites carry no such line.
        lines.append(f"{tr('Αποθηκευμένη αναζήτηση')}: "
                     + ", ".join(row["searches"]))

    return _ics.Event(
        uid=f"{adam}@khmdhs",
        start=row["final_submission_date"],
        summary=summary,
        description="\n".join(lines),
        url=f"{base_url}/act/{quote(adam, safe='')}",
        sequence=_ics.sequence_from(row.get("last_update_date")),
        cancelled=bool(row.get("cancelled")),
        alarms=tuple(_ics.Alarm(d, _alarm_text(d, lang)) for d in lead_days))


# --------------------------------------------------------------------------- #
# Saved searches in the feed (slice 5)
# --------------------------------------------------------------------------- #
def _main():
    """app.main imported lazily, as digests does: it owns build_where, and
    importing it at module scope would open the DB pool on import."""
    try:
        from app import main as m
    except ImportError:                  # pragma: no cover — run with --app-dir=app
        import main as m                 # type: ignore
    return m


def search_ids(c, user_id: int) -> set:
    """The ids of the saved searches this user has put in their calendar."""
    c.execute("SELECT search_profile_id FROM proc.calendar_search WHERE user_id=%s",
              (user_id,))
    return {r["search_profile_id"] for r in c.fetchall()}


def set_search(c, user_id: int, profile_id: int, on: bool) -> None:
    """Tick or untick one saved search. The caller has already checked that
    the user may apply this profile (account_searches._owned)."""
    if on:
        c.execute("""INSERT INTO proc.calendar_search (user_id, search_profile_id)
                     VALUES (%s, %s) ON CONFLICT DO NOTHING""", (user_id, profile_id))
    else:
        c.execute("""DELETE FROM proc.calendar_search
                     WHERE user_id=%s AND search_profile_id=%s""", (user_id, profile_id))


def opted_in_searches(c, user) -> list:
    """The saved searches in this user's feed that they may STILL apply, each
    with its effective params. A portal profile that has since been
    unpublished drops out here rather than feeding a calendar its owner can no
    longer open; a search with no filters is skipped — it would match the
    whole corpus."""
    c.execute("""SELECT sp.*, cs.created_at AS opted_at
                 FROM proc.calendar_search cs
                 JOIN proc.search_profile sp ON sp.id = cs.search_profile_id
                 WHERE cs.user_id = %s
                 ORDER BY lower(sp.name), sp.id""", (user["id"],))
    out = []
    for profile in c.fetchall():
        if not _auth.can_apply_profile(user, profile):
            continue
        params = _auth.effective_params(c, profile)
        if not params:
            continue
        out.append({"id": profile["id"], "name": profile["name"],
                    "params": params, "opted_at": profile["opted_at"],
                    "updated_at": profile.get("updated_at")})
    return out


_SEARCH_COLS = """
    a.adam, a.title, a.final_submission_date, a.cancelled,
    a.last_update_date, a.total_cost_with_vat,
    proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
    auth.name AS authority_name
"""


def _search_matches(c, params, *, upcoming: bool, limit: int, since=None):
    """One saved search's deadlines: upcoming ones soonest first, or the
    recent past latest first. Never a cancelled act.

    The past reaches back CALENDAR_PAST_DAYS but never before `since` — the
    moment the search was ticked. Anything that closed earlier was never in
    this customer's calendar, so there is nothing to keep."""
    where, args = _main().build_where(params or {})
    if upcoming:
        window = " AND a.final_submission_date > now()"
        order = "a.final_submission_date, a.adam"
        wargs = []
    else:
        window = (" AND a.final_submission_date <= now()"
                  " AND a.final_submission_date >= greatest("
                  "now() - make_interval(days => %s), %s::timestamptz)")
        order = "a.final_submission_date DESC, a.adam"
        wargs = [CALENDAR_PAST_DAYS, since]
    c.execute(f"""SELECT {_SEARCH_COLS}
                  FROM proc.procurement_act a
                  LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                  WHERE {where}{window}
                    AND coalesce(a.cancelled, false) = false
                  ORDER BY {order}
                  LIMIT %s""", list(args) + wargs + [limit])
    return c.fetchall()


def count_upcoming(c, params) -> int:
    """How many upcoming, not-cancelled deadlines one saved search matches —
    what /account/calendar shows next to it, so a customer can see which
    search is the one crowding their calendar."""
    where, args = _main().build_where(params or {})
    c.execute(f"""SELECT count(*) AS n FROM proc.procurement_act a
                  WHERE {where} AND a.final_submission_date > now()
                    AND coalesce(a.cancelled, false) = false""", list(args))
    return int(c.fetchone()["n"])


def search_rows(c, searches, *, budget: int, exclude=()) -> list:
    """The events the ticked searches add, at most `budget` of them.

    Upcoming deadlines first, soonest first, across ALL the searches together
    (not N per search: one broad search must not starve a narrow one's
    tomorrow). Only then, with whatever budget is left, the recent past that
    closed since the search was ticked — so a deadline that closed yesterday
    does not vanish from the calendar. An act
    matched by several searches is one event naming all of them; one already
    in `exclude` (a favourite) is left to the favourite."""
    if budget <= 0 or not searches:
        return []
    skip = set(exclude)
    picked: list = []
    for upcoming in (True, False):
        room = budget - len(picked)
        if room <= 0:
            break
        found: dict = {}
        for s in searches:
            for r in _search_matches(c, s["params"], upcoming=upcoming,
                                     limit=room, since=s["opted_at"]):
                if r["adam"] in skip:
                    continue
                row = found.get(r["adam"])
                if row is None:
                    row = found[r["adam"]] = dict(r)
                    row["searches"] = []
                    row["favorited_at"] = None
                row["searches"].append(s["name"])
                # DTSTAMP input: when this search went into the calendar, or
                # was last edited — whichever is later.
                moments = [m for m in (s["opted_at"], s.get("updated_at"),
                                       row["favorited_at"]) if m]
                row["favorited_at"] = max(moments) if moments else None
        ordered = sorted(found.values(),
                         key=lambda r: (r["final_submission_date"], r["adam"]),
                         reverse=not upcoming)[:room]
        picked.extend(ordered)
        skip.update(r["adam"] for r in ordered)
    return picked


def feed_rows(c, user) -> list:
    """Everything one feed carries: the favourites, then the ticked searches'
    deadlines in whatever room CALENDAR_MAX_EVENTS leaves. Sorted by
    deadline, so the body is the same bytes for the same data."""
    favs = [dict(r) for r in favourite_rows(c, user["id"])]
    # A favourite marked "no bid" (bid_pipeline) leaves the calendar, and a
    # ticked search must not bring it straight back.
    extra = search_rows(c, opted_in_searches(c, user),
                        budget=CALENDAR_MAX_EVENTS - len(favs),
                        exclude={r["adam"] for r in favs}
                                | declined_adams(c, user["id"]))
    return sorted(favs + extra,
                  key=lambda r: (r["final_submission_date"], r["adam"]))


# --------------------------------------------------------------------------- #
# The feed body
# --------------------------------------------------------------------------- #
_FEED_SQL = """
    SELECT a.adam, a.title, a.final_submission_date, a.cancelled,
           a.last_update_date, a.total_cost_with_vat,
           proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
           auth.name AS authority_name,
           f.created_at AS favorited_at
    FROM proc.user_favorite_act f
    JOIN proc.procurement_act a ON a.adam = f.adam
    LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
    WHERE f.user_id = %s
      AND f.bid_stage IS DISTINCT FROM 'no_bid'
      AND a.final_submission_date IS NOT NULL
      AND a.final_submission_date >= now() - make_interval(days => %s)
    ORDER BY a.final_submission_date, a.adam
    LIMIT %s
"""


def fetch_act(c, adam: str):
    """One act, with exactly the columns act_event needs — plus duplicate_of,
    which main._duplicate_redirect reads.

    Deliberately its own small query rather than act_service.fetch_core: that
    module belongs to the uncommitted mobile API (it imports api_v1.schemas),
    and the one-off download must deploy without it."""
    c.execute("""
        SELECT a.adam, a.type, a.title, a.final_submission_date, a.cancelled,
               a.last_update_date, a.total_cost_with_vat, a.duplicate_of,
               proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
               auth.name AS authority_name
        FROM proc.procurement_act a
        LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
        WHERE a.adam = %s""", (adam,))
    return c.fetchone()


def declined_adams(c, user_id: int) -> set:
    """Favourites the customer marked 'no_bid'. Out of the feed on every path."""
    c.execute("""SELECT adam FROM proc.user_favorite_act
                  WHERE user_id = %s AND bid_stage = 'no_bid'""", (user_id,))
    return {r["adam"] for r in c.fetchall()}


def favourite_rows(c, user_id: int):
    """Favourited acts with a deadline, from CALENDAR_PAST_DAYS ago onwards,
    except those marked 'no_bid'. Cancelled ones are INCLUDED — act_event marks them STATUS:CANCELLED, which
    is how a client learns to strike out an event it has already imported."""
    c.execute(_FEED_SQL, (user_id, CALENDAR_PAST_DAYS, CALENDAR_MAX_EVENTS))
    return c.fetchall()


def _stamp(rows, fallback) -> dt.datetime:
    """A DTSTAMP derived from the data, so an unchanged feed renders to the
    same bytes on every poll and the ETag can match."""
    moments = [m for r in rows
               for m in (r.get("favorited_at"), r.get("last_update_date"))
               if isinstance(m, dt.datetime)]
    best = max(moments) if moments else fallback
    if best.tzinfo is None:
        best = best.replace(tzinfo=dt.timezone.utc)
    return best


def render_feed(rows, *, lang: str, base_url: str, lead_days,
                fallback_stamp: dt.datetime) -> str:
    events = [act_event(r, lang=lang, base_url=base_url, lead_days=lead_days)
              for r in rows]
    return _ics.render(events, name=_i18n.translate("ΚΗΜΔΗΣ — Προθεσμίες", lang),
                       dtstamp=_stamp(rows, fallback_stamp))


def render_lapsed(*, user_id: int, lang: str, base_url: str, today: dt.date,
                  fallback_stamp: dt.datetime) -> str:
    """The calendar a customer without access gets: valid, and holding one
    all-day notice on today's date instead of their deadlines (spec §5).

    One stable UID whose SEQUENCE is the day number, so a client moves the same
    entry forward each day rather than collecting one notice per day of the
    lapse."""
    tr = lambda s: _i18n.translate(s, lang)      # noqa: E731
    notice = _ics.Event(
        uid=f"lapsed-{user_id}@khmdhs",
        start=today,
        summary=tr("Η συνδρομή σας έχει λήξει — οι προθεσμίες θα "
                   "επανεμφανιστούν με την ανανέωση."),
        description=tr("Οι προθεσμίες των αγαπημένων σας επιστρέφουν σε αυτό "
                       "το ημερολόγιο μόλις ανανεωθεί η συνδρομή, χωρίς "
                       "καμία ενέργεια από εσάς."),
        url=f"{base_url}/account",
        sequence=(today - dt.date(2020, 1, 1)).days)
    return _ics.render([notice], name=tr("ΚΗΜΔΗΣ — Προθεσμίες"),
                       dtstamp=fallback_stamp)


def _etag(body: str) -> str:
    return '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32] + '"'


def _matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    tags = [t.strip() for t in if_none_match.split(",")]
    return etag in [t[2:] if t.startswith("W/") else t for t in tags]


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(tags=["calendar"])

    # ---- the feed a calendar server fetches -------------------------------- #
    @router.get("/calendar/{token}.ics")
    def feed(token: str, request: Request):
        if not _TOKEN_OK.match(token or ""):
            raise HTTPException(404, "not found")
        base = _seo.base_url(request)
        # "Today" in Athens — the same zone helper the digests use, so the
        # lapsed notice changes date at Greek midnight, summer and winter.
        today = dt.datetime.now(_digests._tz(_digests.DEFAULT_TZ)).date()
        with cursor() as c:
            row = lookup(c, token)
            if not row:
                raise HTTPException(404, "not found")
            user = _auth.load_user(c, row["user_id"])
            if not user:
                # Deactivated: here the goal IS that the URL stops working.
                raise HTTPException(404, "not found")
            record_fetch(c, row["user_id"])
            if user.get("has_access"):
                rows = feed_rows(c, user)
                body = render_feed(rows, lang=row["lang"], base_url=base,
                                   lead_days=lead_days_for(c, row["user_id"]),
                                   fallback_stamp=row["created_at"])
            else:
                body = render_lapsed(user_id=row["user_id"], lang=row["lang"],
                                     base_url=base, today=today,
                                     fallback_stamp=row["created_at"])

        etag = _etag(body)
        headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=1800",
            # A capability URL: never indexed, never leaked onward.
            "X-Robots-Tag": "noindex",
            "Referrer-Policy": "no-referrer",
        }
        if _matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        headers["Content-Disposition"] = 'inline; filename="khmdhs.ics"'
        return Response(content=body.encode("utf-8"),
                        media_type="text/calendar; charset=utf-8",
                        headers=headers)

    # ---- /account/calendar ------------------------------------------------- #
    def _page(request, user, *, new_url=None, status=200):
        lang = _i18n.lang_from_request(request)
        with cursor() as c:
            feed_row = get_feed(c, user["id"])
            marks = lead_days_for(c, user["id"])
            c.execute("""SELECT count(*) AS n
                         FROM proc.user_favorite_act f
                         JOIN proc.procurement_act a ON a.adam = f.adam
                         WHERE f.user_id=%s AND a.final_submission_date >= now()
                           AND f.bid_stage IS DISTINCT FROM 'no_bid'""",
                      (user["id"],))
            upcoming = int(c.fetchone()["n"])
            searches = [{"id": s["id"], "name": s["name"],
                         "upcoming": count_upcoming(c, s["params"])}
                        for s in opted_in_searches(c, user)]
        # Whether the ticked searches together overflow what the feed carries;
        # the page then says so instead of letting deadlines go missing quietly.
        over_cap = (upcoming + sum(s["upcoming"] for s in searches)
                    > CALENDAR_MAX_EVENTS)
        resp = templates.TemplateResponse(
            request, "account_calendar.html",
            {"feed": feed_row, "new_url": new_url,
             "webcal": webcal_url(new_url) if new_url else None,
             "has_access": bool(user.get("has_access")),
             "marks": list(marks), "upcoming": upcoming, "lang": lang,
             "searches": searches, "over_cap": over_cap,
             "max_events": CALENDAR_MAX_EVENTS,
             "nav_active": "account"},
            status_code=status)
        # The page may be carrying the raw URL; nothing may keep a copy.
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @router.get(PAGE, response_class=HTMLResponse)
    def page(request: Request):
        user = getattr(request.state, "user", None)
        if not user:
            return RedirectResponse(url=f"/login?next={PAGE}", status_code=303)
        return _page(request, user)

    @router.post(PAGE + "/new", response_class=HTMLResponse)
    def new(request: Request):
        """Create the link, or replace it. The response is the ONLY place the
        raw URL is ever shown — which is why this renders the page instead of
        redirecting: a redirect would have to carry the token in its own URL."""
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(403, "sign in first")
        with cursor() as c:
            raw = issue(c, user["id"], _i18n.lang_from_request(request))
        return _page(request, user, new_url=feed_url(request, raw))

    @router.post(PAGE + "/off")
    def off(request: Request):
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(403, "sign in first")
        with cursor() as c:
            turn_off(c, user["id"])
        return RedirectResponse(url=PAGE, status_code=303)

    return router
