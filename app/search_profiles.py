"""
search_profiles.py — saved searches ("search profiles"), mounted at /search-profiles.

A profile stores the search filter set (the same params the search page reads).
Applying one replays those filters (redirect to /?<querystring>).

Scopes (see the migration): 'portal' (admin-owned, global; visible to customers
only when published) and 'customer' (owned by one customer). A customer profile
may reference a portal profile as a LIVE link (based_on_id) or carry its own
params. Admins create/manage profiles in this phase; customers apply the ones
available to them.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import auth as _auth

# The known search filters (mirrors app.main._params_from). Single-valued vs
# multi-valued (repeated query params).
_SINGLE = ("q", "fulltext", "tables_q", "date_from", "date_to",
           "deadline_from", "deadline_to", "value_min", "value_max", "status", "sort")
_MULTI = ("type", "authority", "contract_type", "procedure_type", "nuts",
          "cpv", "cat", "source")


def params_from_qs(qs: str) -> dict:
    """Parse a raw querystring into the filter dict, keeping only known keys and
    dropping empties."""
    raw = parse_qs(qs or "", keep_blank_values=False)
    out = {}
    for k in _SINGLE:
        v = (raw.get(k) or [""])[0].strip()
        if v:
            out[k] = v
    for k in _MULTI:
        vals = [v.strip() for v in raw.get(k, []) if v.strip()]
        if vals:
            out[k] = vals
    return out


def params_to_qs(params: dict) -> str:
    """Serialize a filter dict back to a querystring (repeats multi keys)."""
    pairs = []
    for k in _SINGLE:
        v = (params or {}).get(k)
        if v:
            pairs.append((k, v))
    for k in _MULTI:
        for item in (params or {}).get(k, []) or []:
            if item:
                pairs.append((k, item))
    return urlencode(pairs)


def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/search-profiles", tags=["search-profiles"])

    def _user(request):
        return getattr(request.state, "user", None)

    def _require_admin(request):
        u = _user(request)
        if not (u and u.get("role") == "admin"):
            raise HTTPException(status_code=403, detail="admins only")
        return u

    # ---- apply (any user, for profiles available to them) ------------------ #
    @router.get("/{pid}/apply")
    def apply_profile(pid: int, request: Request):
        u = _user(request)
        with cursor() as c:
            p = _auth.get_search_profile(c, pid)
            if not p:
                raise HTTPException(404, "profile not found")
            if not _auth.can_apply_profile(u, p):
                raise HTTPException(403, "not allowed")
            params = _auth.effective_params(c, p)
        qs = params_to_qs(params)
        # _sp marks which profile is active so the search page can show a badge.
        # It's not a known filter key, so params_from_qs drops it on any re-save.
        marker = urlencode({"_sp": p["name"]})
        url = "/?" + (qs + "&" + marker if qs else marker)
        return RedirectResponse(url=url, status_code=303)

    # ---- create (admin) ---------------------------------------------------- #
    @router.post("")
    async def create_profile(request: Request,
                             name: str = Form(...),
                             scope: str = Form("customer"),
                             owner_id: str = Form(""),
                             based_on_id: str = Form(""),
                             params_qs: str = Form(""),
                             next: str = Form("/search-profiles/manage")):
        admin = _require_admin(request)
        name = (name or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        if scope not in ("portal", "customer"):
            raise HTTPException(400, "invalid scope")
        owner = int(owner_id) if (scope == "customer" and owner_id.strip()) else None
        if scope == "customer" and owner is None:
            raise HTTPException(400, "a customer profile needs an owner")
        based = int(based_on_id) if based_on_id.strip() else None
        params = params_from_qs(params_qs) if params_qs.strip() else None
        if params is None and based is None:
            raise HTTPException(400, "provide search filters or a reference profile")
        with cursor() as c:
            _auth.create_search_profile(
                c, name=name, scope=scope, owner_id=owner, params=params,
                based_on_id=based, created_by=admin["id"])
        return RedirectResponse(url=next or "/search-profiles/manage", status_code=303)

    # ---- publish toggle / delete (admin) ----------------------------------- #
    @router.post("/{pid}/publish")
    async def toggle_publish(pid: int, request: Request, published: str = Form("")):
        _require_admin(request)
        with cursor() as c:
            _auth.set_profile_published(c, pid, bool(published))
        return RedirectResponse(url="/search-profiles/manage", status_code=303)

    @router.post("/{pid}/delete")
    async def delete_profile(pid: int, request: Request):
        u = _require_admin(request)
        with cursor() as c:
            p = _auth.get_search_profile(c, pid)
            if p and _auth.can_manage_profile(u, p):
                _auth.delete_search_profile(c, pid)
        return RedirectResponse(url="/search-profiles/manage", status_code=303)

    @router.post("/{pid}/rename")
    async def rename_profile(pid: int, request: Request, name: str = Form(...)):
        u = _require_admin(request)
        name = (name or "").strip()
        with cursor() as c:
            p = _auth.get_search_profile(c, pid)
            if p and name and _auth.can_manage_profile(u, p):
                _auth.update_search_profile(
                    c, pid, name=name, params=p["params"],
                    based_on_id=p["based_on_id"], is_published=p["is_published"])
        return RedirectResponse(url="/search-profiles/manage", status_code=303)

    # ---- management page (admin) ------------------------------------------- #
    @router.get("/manage")
    def manage(request: Request):
        _require_admin(request)
        with cursor() as c:
            profiles = _auth.list_all_profiles(c)
            customers = _auth.list_customers(c) if hasattr(_auth, "list_customers") else []
            c.execute("SELECT id, name FROM proc.search_profile "
                      "WHERE scope='portal' ORDER BY lower(name)")
            portal_profiles = c.fetchall()
        return templates.TemplateResponse(
            request, "admin_search_profiles.html",
            {"profiles": profiles, "customers": customers,
             "portal_profiles": portal_profiles, "admin_tab": "profiles"})

    return router
