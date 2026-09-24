"""
account_certificates.py — /account/certificates: the customer's own
certificates and their expiry reminders.

docs/specs/evaluation-layer.md §10 (slice 2). The certificate rules live in
app/eligibility_eval.py (catalogue, validation, save/delete); the reminders in
app/cert_reminders.py. This module is only the page.

- Entitled customers only (has_access). The certificates exist to annotate
  the checklist, which is subscriber content; a lapsed customer is shown
  why, not a form.
- Every write is scoped to the signed-in user: delete takes (user, id), so an
  id from someone else's account is a 404, never a delete.
- A row the customer saves is source='customer'; the CRM card shows who
  entered each one. The customer may edit or delete an admin-entered row too
  — it is their company's data. Extra recipients stay admin-only (CLAUDE.md
  "Email alerts"): the reminder goes to the account address and nobody else.
- The reminder switch is OPT-IN (see cert_reminders.py for why).
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    from app import cert_reminders as _rem
    from app import eligibility_eval as _eval
    from app import i18n as _i18n
    from app import mailer as _mailer
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import cert_reminders as _rem
    import eligibility_eval as _eval
    import i18n as _i18n
    import mailer as _mailer

PAGE = "/account/certificates"


def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(tags=["account"])

    def _user(request):
        return getattr(request.state, "user", None)

    def _back(msg: str = "", err: str = ""):
        q = (f"?ok={quote(msg)}" if msg else "") or (f"?err={quote(err)}" if err else "")
        return RedirectResponse(url=PAGE + q, status_code=303)

    @router.get(PAGE, response_class=HTMLResponse)
    def page(request: Request, ok: str = "", err: str = ""):
        user = _user(request)
        if not user:
            return RedirectResponse(url=f"/login?next={PAGE}", status_code=303)
        certs, reminders = [], False
        if user.get("has_access"):
            with cursor() as c:
                certs = _eval.certificates(c, user["id"])
                reminders = _rem.is_enabled(c, user["id"])
        today = _rem.athens_today()
        for x in certs:
            x["days_left"] = (x["valid_until"] - today).days if x["valid_until"] else None
        return templates.TemplateResponse(request, "account_certificates.html", {
            "has_access": bool(user.get("has_access")),
            "certs": certs, "schemes": _eval.CATALOGUE,
            "reminders": reminders, "remind_days": _rem.REMIND_DAYS,
            "has_email": _mailer.valid_address(_mailer.address_for(user)),
            # ok/err are OUR messages round-tripped through the redirect; only
            # known phrases are shown (a crafted ?err= cannot put text on the page).
            "ok": ok if ok in _MESSAGES else "",
            "err": err if err in _MESSAGES else "",
            "nav_active": "account"})

    def _guard(request):
        user = _user(request)
        if not user:
            raise HTTPException(403, "sign in first")
        if not user.get("has_access"):
            raise HTTPException(403, "no active subscription")
        return user

    @router.post(PAGE)
    def save(request: Request, scheme: str = Form(""), holder: str = Form("self"),
             manufacturer: str = Form(""), edition: str = Form(""),
             number: str = Form(""), issuer: str = Form(""),
             valid_until: str = Form("")):
        user = _guard(request)
        with cursor() as c:
            try:
                _eval.save(c, user["id"], scheme=scheme, holder=holder,
                           manufacturer=manufacturer, edition=edition,
                           number=number, issuer=issuer, valid_until=valid_until,
                           by=user["id"], source="customer")
            except _eval.CertError as e:
                return _back(err=str(e))
        return _back("Το πιστοποιητικό αποθηκεύτηκε.")

    @router.post(PAGE + "/{cert_id}/delete")
    def delete(cert_id: int, request: Request):
        user = _guard(request)
        with cursor() as c:
            if not _eval.delete(c, user["id"], cert_id):
                raise HTTPException(404, "not found")
        return _back("Το πιστοποιητικό διαγράφηκε.")

    @router.post(PAGE + "/reminders")
    def reminders(request: Request, on: str = Form("")):
        user = _guard(request)
        with cursor() as c:
            _rem.set_enabled(c, user["id"], on == "1",
                             lang=_i18n.lang_from_request(request))
        return _back("Οι υπενθυμίσεις ενεργοποιήθηκαν." if on == "1"
                     else "Οι υπενθυμίσεις απενεργοποιήθηκαν.")

    return router


_MESSAGES = {
    "Το πιστοποιητικό αποθηκεύτηκε.", "Το πιστοποιητικό διαγράφηκε.",
    "Οι υπενθυμίσεις ενεργοποιήθηκαν.", "Οι υπενθυμίσεις απενεργοποιήθηκαν.",
    "Άγνωστο πρότυπο.", "Άγνωστος κάτοχος.",
    "Συμπληρώστε το όνομα του κατασκευαστή.", "Η έκδοση είναι έτος, π.χ. 2015.",
    "Μη έγκυρη ημερομηνία λήξης.", "Το κείμενο είναι πολύ μεγάλο.",
}
