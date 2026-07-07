"""
crm.py — CRM admin area (mounted at /admin/crm, admin-gated by AuthMiddleware).

Phase 1: list customers segmented by subscription status, and a per-customer
page with an editable profile, product/subscription history, and the grant /
extend / set-expiry controls (reachable here, not only from /admin/users).

Data helpers live in app/auth.py (list_customers, customer_segment_counts,
get_customer, get_profile, upsert_profile, subscription_history, grant_product,
extend_subscription, set_subscription_expiry, current_subscription, product_list).
Phase 2 will add notes / calls / tasks.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    from app import auth as _auth
except ImportError:  # run with --app-dir=app
    import auth as _auth

# Segment values shown as tabs (order matters); mirror auth._status_label.
SEGMENTS = ("all", "subscriber", "tester",
            "expired_subscriber", "expired_tester", "none")


def make_crm_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/admin/crm", tags=["crm"])

    def _admin_uid(request):
        u = getattr(request.state, "user", None)
        return u.get("id") if u else None

    @router.get("", response_class=HTMLResponse)
    def crm_list(request: Request, segment: str = "all"):
        if segment not in SEGMENTS:
            segment = "all"
        with cursor() as c:
            counts = _auth.customer_segment_counts(c)
            customers = _auth.list_customers(c, segment)
        return templates.TemplateResponse(request, "admin_crm.html", {
            "customers": customers, "counts": counts, "segment": segment,
            "segments": SEGMENTS, "admin_tab": "crm"})

    def _customer_ctx(request, uid, error=None, ok=None):
        with cursor() as c:
            cust = _auth.get_customer(c, uid)
            if not cust:
                raise HTTPException(404, "customer not found")
            profile = _auth.get_profile(c, uid)
            history = _auth.subscription_history(c, uid)
            products = _auth.product_list(c)
            current = _auth.current_subscription(c, uid)
        return {"cust": cust, "profile": profile or {}, "history": history,
                "products": products, "current": current,
                "fields": _auth.PROFILE_FIELDS,
                "error": error, "ok": ok, "admin_tab": "crm"}

    @router.get("/{uid}", response_class=HTMLResponse)
    def crm_customer(uid: int, request: Request, ok: str = None):
        return templates.TemplateResponse(
            request, "admin_crm_customer.html",
            _customer_ctx(request, uid, ok=ok))

    @router.post("/{uid}/profile")
    async def crm_profile_save(uid: int, request: Request):
        form = await request.form()
        values = {k: (form.get(k) or "").strip() for k in _auth.PROFILE_FIELDS}
        email = (form.get("email") or "").strip()
        try:
            with cursor() as c:
                if not _auth.get_customer(c, uid):
                    raise HTTPException(404, "customer not found")
                _auth.upsert_profile(c, uid, values,
                                     updated_by=_admin_uid(request))
                _auth.set_email(c, uid, email)
        except HTTPException:
            raise
        except Exception:   # unique email clash etc.
            return templates.TemplateResponse(
                request, "admin_crm_customer.html",
                _customer_ctx(request, uid,
                              error="Το email χρησιμοποιείται ήδη ή είναι μη έγκυρο."),
                status_code=400)
        return RedirectResponse(f"/admin/crm/{uid}?ok=profile", status_code=303)

    @router.post("/{uid}/grant")
    async def crm_grant(uid: int, request: Request):
        form = await request.form()
        product = form.get("product") or ""
        days_raw = (form.get("days") or "").strip()
        try:
            period_days = int(days_raw) if days_raw else None
            with cursor() as c:
                _auth.grant_product(c, uid, product,
                                    granted_by=_admin_uid(request),
                                    period_days=period_days)
        except (ValueError, TypeError):
            return templates.TemplateResponse(
                request, "admin_crm_customer.html",
                _customer_ctx(request, uid, error="Μη έγκυρη ανάθεση."),
                status_code=400)
        return RedirectResponse(f"/admin/crm/{uid}?ok=granted", status_code=303)

    @router.post("/{uid}/subscription")
    async def crm_subscription(uid: int, request: Request):
        form = await request.form()
        extend_days = (form.get("extend_days") or "").strip()
        expires_at = (form.get("expires_at") or "").strip()
        with cursor() as c:
            sub = _auth.current_subscription(c, uid)
            if not sub:
                return templates.TemplateResponse(
                    request, "admin_crm_customer.html",
                    _customer_ctx(request, uid,
                                  error="Δεν υπάρχει ενεργό προϊόν — αναθέστε πρώτα."),
                    status_code=400)
            try:
                if extend_days:
                    _auth.extend_subscription(c, sub["id"], int(extend_days))
                elif expires_at:
                    _auth.set_subscription_expiry(c, sub["id"], expires_at + " 23:59:59")
            except (ValueError, TypeError):
                return templates.TemplateResponse(
                    request, "admin_crm_customer.html",
                    _customer_ctx(request, uid, error="Μη έγκυρη ημερομηνία/διάρκεια."),
                    status_code=400)
        return RedirectResponse(f"/admin/crm/{uid}?ok=subscription", status_code=303)

    return router
