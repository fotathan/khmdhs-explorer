"""
account_fit.py — the fit score, shown to the customer it belongs to.

Until now fit.py was admin-only: a tab on the CRM card. This module is the
customer's side of the same numbers. Two surfaces:

  * /account/fit — the open tenders that fit their firm best, ranked, each
    with its components ("Ταιριάζουν σε εσάς").
  * /act/<adam>/fit — a small panel on the act page, loaded by HTMX after the
    page, so act_detail itself is untouched and pays nothing for it.

Who sees it
-----------
An ENTITLED customer (has_access) whose profile an admin has switched on:
company_profile.is_active. That column has been there since the profile
shipped, off by default, with the comment "a profile nobody has looked at
should not silently start driving what a customer is shown" — this is the
thing it was waiting for. Nothing turns it on automatically.

Everyone else gets an explanation (the page) or nothing at all (the act
panel). An admin has access but no firm, so they see the empty state too.

What it must not do
-------------------
* Store anything. Fit is computed per (customer, act) per request, like the
  CRM tab. Nothing here is cached, and nothing here may read or write
  proc.act_ai_summary (fit.py's isolation rule, test-enforced for this file).
* Show a bare number. Every score comes with its components, in the
  customer's own voice (CUSTOMER_WHY) — the admin phrases talk ABOUT the firm
  ("δραστηριοποιούνται εκεί"), a customer is addressed ("έχετε συμβάσεις…").
* Show another customer's anything. Every query is keyed on the signed-in
  user's own id; there is no user id in any URL.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    from app import fit as _fit
    from app import i18n as _i18n
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import fit as _fit
    import i18n as _i18n

PAGE = "/account/fit"

# How many ranked tenders the page lists. The ranking itself scores every
# open candidate (fit.CANDIDATE_CAP); this is only how many are shown.
PAGE_ROWS = 50

# Score bands, the same cut points the CRM tab colours by.
HIGH, MID = 70, 45

# fit.py's `why` phrases describe the firm in the third person, for an admin.
# A customer reads about themselves. Keys are fit.py's phrases exactly (they
# are its translation keys); a phrase missing here falls back to the admin
# wording, and a test runs every scorer branch to keep that from happening.
CUSTOMER_WHY = {
    # CPV
    "ακριβής κωδικός CPV": "ίδιος κωδικός CPV με συμβάσεις που έχετε κερδίσει",
    "ίδια ομάδα CPV": "ίδια ομάδα CPV με συμβάσεις που έχετε κερδίσει",
    "ίδιος τομέας CPV": "ίδιος τομέας CPV με συμβάσεις που έχετε κερδίσει",
    "δεν προμηθεύει τίποτα σε αυτούς τους CPV":
        "δεν έχετε συμβάσεις σε αυτούς τους CPV",
    "δεν υπάρχουν κωδικοί CPV για σύγκριση":
        "δεν υπάρχουν κωδικοί CPV για σύγκριση",
    # value
    "εντός του συνήθους εύρους τους": "στο μέγεθος που κερδίζετε συνήθως",
    "κοντά στο σύνηθες εύρος τους": "κοντά στο μέγεθος που κερδίζετε συνήθως",
    "μεγαλύτερος από ό,τι έχουν αναλάβει": "μεγαλύτερος από ό,τι έχετε αναλάβει",
    "μικρότερος από ό,τι διεκδικούν συνήθως":
        "μικρότερος από ό,τι κερδίζετε συνήθως",
    "χωρίς ιστορικό αξιών για σύγκριση": "χωρίς ιστορικό αξιών για σύγκριση",
    "δεν αναφέρεται αξία": "δεν αναφέρεται αξία",
    # geography
    "δραστηριοποιούνται εκεί": "έχετε συμβάσεις σε αυτή την περιφέρεια",
    "ίδια χώρα, άλλη περιφέρεια": "ίδια χώρα, άλλη περιφέρεια",
    "εκτός των περιοχών τους": "εκτός των περιφερειών όπου έχετε συμβάσεις",
    "χωρίς ιστορικό περιοχών": "χωρίς ιστορικό περιοχών",
    "δεν αναφέρεται περιοχή": "δεν αναφέρεται περιοχή",
    # buyer
    "έχουν αναλάβει ξανά από αυτήν": "έχετε ξανά σύμβαση με αυτή την αναθέτουσα",
    "νέα αναθέτουσα γι' αυτούς": "δεν έχετε σύμβαση με αυτή την αναθέτουσα",
    "χωρίς ιστορικό αναθετουσών": "χωρίς ιστορικό αναθετουσών",
}


def customer_why(phrase: str) -> str:
    return CUSTOMER_WHY.get(phrase, phrase)


def band(score: int) -> str:
    return "hi" if score >= HIGH else ("mid" if score >= MID else "lo")


def active_profile(c, user) -> tuple[_fit.Profile | None, dict | None]:
    """(profile, company_profile row) when this user may see fit, else
    (None, row-or-None). The row is returned either way so the page can say
    WHY there is nothing — no profile, or not switched on yet."""
    if not user or not user.get("has_access"):
        return None, None
    c.execute("SELECT * FROM proc.company_profile WHERE user_id = %s",
              (user["id"],))
    row = c.fetchone()
    if not row or not row.get("is_active"):
        return None, row
    profile = _fit.load_profile(c, user["id"])
    if profile is None or not profile.is_usable:
        return None, row
    return profile, row


def act_cpvs(c, adam: str) -> list[str]:
    c.execute("""SELECT DISTINCT oc.cpv_code
                   FROM proc.act_object_detail od
                   JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                  WHERE od.adam = %s AND oc.cpv_code IS NOT NULL
                    AND oc.cpv_code <> ''""", (adam,))
    return [r["cpv_code"] for r in c.fetchall()]


def score_act(c, profile: _fit.Profile, adam: str) -> dict | None:
    """One act, scored for this profile. Notices only: fit answers "should we
    bid", which is a question about a tender, not about an award or payment."""
    c.execute("""SELECT a.adam, a.type, a.nuts_code, a.authority_id,
                        a.total_cost_with_vat,
                        proc.resolved_value(a.adam, a.total_cost_with_vat)
                            AS resolved_value
                   FROM proc.procurement_act a WHERE a.adam = %s""", (adam,))
    act = c.fetchone()
    if not act or act["type"] != "notice":
        return None
    return _fit.explain(profile, act, act_cpvs(c, adam))


def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(tags=["account"])

    def _ctx(extra: dict) -> dict:
        return {"customer_why": customer_why, "band": band, **extra}

    @router.get(PAGE, response_class=HTMLResponse)
    def page(request: Request):
        user = getattr(request.state, "user", None)
        if not user:
            return RedirectResponse(url=f"/login?next={PAGE}", status_code=303)
        try:
            from app import account_favorites as _favs
        except ImportError:              # pragma: no cover
            import account_favorites as _favs
        rows, n_candidates, favorites = [], 0, set()
        with cursor() as c:
            profile, prow = active_profile(c, user)
            if profile is not None:
                ranked = _fit.rank(c, user["id"], limit=PAGE_ROWS)
                rows, n_candidates = ranked["rows"], ranked.get("n_candidates", 0)
                favorites = _favs.favorite_adams(c, user["id"],
                                                 [r["adam"] for r in rows])
        return templates.TemplateResponse(
            request, "account_fit.html",
            _ctx({"rows": rows, "n_candidates": n_candidates,
                  "profile": profile, "prow": prow,
                  "entitled": bool(user.get("has_access")),
                  "favorites": favorites, "nav_active": "account",
                  "lang": _i18n.lang_from_request(request)}))

    @router.get("/act/{adam}/fit", response_class=HTMLResponse)
    def act_panel(adam: str, request: Request):
        """The act page's fit panel. EMPTY (not an error) for anyone who may
        not see fit, and for any act that is not a notice: the page asks for
        this on every load, and a 403 in the console would be noise."""
        user = getattr(request.state, "user", None)
        with cursor() as c:
            profile, _row = active_profile(c, user)
            detail = score_act(c, profile, adam) if profile is not None else None
        if detail is None:
            return HTMLResponse("")
        return templates.TemplateResponse(
            request, "_act_fit.html", _ctx({"fit": detail, "adam": adam}))

    return router
