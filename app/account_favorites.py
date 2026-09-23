"""
account_favorites.py — /account/favorites: a customer's own bookmarked acts.

Why this exists
---------------
`proc.user_favorite_act` shipped with the mobile API (migration …170000), but
nothing on the WEB ever wrote a row, so a browser customer — all of them today —
could not mark an act at all. This module is that missing surface. It is also
the prerequisite for the subscribed calendar feed (docs/specs/calendar-feed.md
§2): a feed of favourites is empty until something can favourite.

Why it does not import app/mobile_favorites.py
----------------------------------------------
It used to, so web and phone shared one implementation. But main.py loads this
module at startup, and mobile_favorites pulls in mobile_search and
api_v1/schemas — uncommitted work that is not in production. Importing it made
the whole web app impossible to deploy without the mobile API. The three
statements it needed are small, so they live here now.

What keeps web and phone meaning the same thing is the TABLE, not shared code:
one row per (user, act) is a favourite, whoever wrote it. The rules below match
mobile_favorites exactly — add only for an act that exists (INSERT … SELECT
FROM procurement_act), a repeat add is a no-op, remove is idempotent — and
tests/test_account_favorites.py cross-checks the two whenever the mobile module
is present. Change a rule here, change it there.

What a favourite is, and is not
-------------------------------
One bookmark on one act, owned by one user. It does NOT subscribe anyone to
anything. Alerts live on saved searches (/account/searches); this is the "keep
an eye on this one" gesture, and it is deliberately not wired to mail.

Who may
-------
SIGNED IN, not entitled. A favourite writes the user's own row and exposes no
act data at all, so there is nothing for a grant to gate. The BUTTON is
rendered inside the act page's `not gated` block, like every other act action,
so a lapsed customer is not offered it — the same split /act/<adam>/calendar.ics
makes: the gate on the surface is a product decision, the gate on the endpoint
is about what the endpoint can leak.

The cap
-------
Any signed-in user can write to this table, which is exactly the situation
MAX_SAVED_SEARCHES exists for in account_searches.py. MAX_FAVORITES is the same
kind of bound — far more than anybody will click, small enough that a script
pointed at the endpoint cannot fill the table. The mobile path predates it.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    from app import i18n as _i18n
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import i18n as _i18n

PAGE = "/account/favorites"

# See the module docstring. Not a licensing lever — a bound on a table anyone
# signed in can write to.
MAX_FAVORITES = max(1, int(os.environ.get("MAX_FAVORITES") or 500))


# --------------------------------------------------------------------------- #
# The table. Same rules as app/mobile_favorites.py — see the docstring.
# --------------------------------------------------------------------------- #
def favorite_adams(c, user_id, adams) -> set:
    """Which of these adams this user has favourited. One query for a whole
    page of results, never one per row — the same shape as the match chips."""
    adams = list(adams or [])
    if not adams:
        return set()
    c.execute("""SELECT adam FROM proc.user_favorite_act
                 WHERE user_id=%s AND adam = ANY(%s)""", (user_id, adams))
    return {str(r["adam"]) for r in c.fetchall()}


def count_favorites(c, user_id) -> int:
    c.execute("SELECT count(*) AS n FROM proc.user_favorite_act WHERE user_id=%s",
              (user_id,))
    return int(c.fetchone()["n"])


def add_favorite(c, user_id, adam) -> bool:
    """Favourite an act. True if it is (now, or already) a favourite; False if
    no such act exists.

    INSERT … SELECT FROM procurement_act rather than a plain INSERT: an unknown
    ADAM then inserts nothing instead of tripping the foreign key, and "does the
    act exist" and "write the row" are one statement, not a race."""
    c.execute("""INSERT INTO proc.user_favorite_act (user_id, adam)
                 SELECT %s, a.adam FROM proc.procurement_act a WHERE a.adam = %s
                 ON CONFLICT (user_id, adam) DO NOTHING
                 RETURNING adam""", (user_id, adam))
    if c.fetchone():
        return True
    # Nothing inserted: either it was already a favourite, or the act is unknown.
    return adam in favorite_adams(c, user_id, [adam])


def remove_favorite(c, user_id, adam) -> None:
    """Idempotent: removing something already gone is the same answer, so a
    double click never errors."""
    c.execute("DELETE FROM proc.user_favorite_act WHERE user_id=%s AND adam=%s",
              (user_id, adam))


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/account/favorites", tags=["account"])

    def _signed_in(request):
        """Write guard. A signed-out write is a stale tab or a forgery, not
        somebody who needs the login page — the GET handler redirects instead."""
        u = getattr(request.state, "user", None)
        if not u:
            raise HTTPException(403, "sign in first")
        return u

    def _toggle(request, adam: str, favorited: bool, *, compact=False,
                error: str = ""):
        return templates.TemplateResponse(
            request, "_favorite_toggle.html",
            {"adam": adam, "favorited": favorited, "compact": compact,
             "error": error})

    # ---- the list ---------------------------------------------------------- #
    @router.get("", response_class=HTMLResponse)
    def page(request: Request):
        user = getattr(request.state, "user", None)
        if not user:
            return RedirectResponse(url=f"/login?next={PAGE}", status_code=303)
        try:
            from app import main as web
        except ImportError:              # pragma: no cover
            import main as web
        with cursor() as c:
            # Newest bookmark first: the reason someone opens this page is
            # usually the thing they marked last.
            #
            # Hidden duplicates are NOT filtered out. The user bookmarked this
            # exact act; /act/<hidden> already redirects to the one we show, so
            # the link works, and silently dropping a row they created would be
            # harder to explain than a redirect. The mobile list does the same.
            c.execute(
                f"""SELECT f.created_at AS favorited_at, {web.SELECT_COLS}
                    FROM proc.user_favorite_act f
                    JOIN proc.procurement_act a ON a.adam = f.adam
                    LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                    WHERE f.user_id = %s
                    ORDER BY f.created_at DESC, f.adam DESC
                    LIMIT %s""",
                (user["id"], MAX_FAVORITES))
            rows = c.fetchall()
        return templates.TemplateResponse(
            request, "account_favorites.html",
            {"rows": rows, "nav_active": "account",
             "favorites": {r["adam"] for r in rows},
             "lang": _i18n.lang_from_request(request)})

    # ---- the toggle -------------------------------------------------------- #
    @router.post("/{adam}", response_class=HTMLResponse)
    def add(adam: str, request: Request, compact: bool = False):
        user = _signed_in(request)
        with cursor() as c:
            if (adam not in favorite_adams(c, user["id"], [adam])
                    and count_favorites(c, user["id"]) >= MAX_FAVORITES):
                # Answer with the button still OFF plus the reason, so the page
                # tells the truth about what the table now holds.
                return _toggle(request, adam, False, compact=compact,
                               error="Έχετε φτάσει το όριο αγαπημένων.")
            if not add_favorite(c, user["id"], adam):
                raise HTTPException(404, "not_found")
        return _toggle(request, adam, True, compact=compact)

    @router.delete("/{adam}", response_class=HTMLResponse)
    def remove(adam: str, request: Request, compact: bool = False):
        user = _signed_in(request)
        with cursor() as c:
            remove_favorite(c, user["id"], adam)
        return _toggle(request, adam, False, compact=compact)

    return router
