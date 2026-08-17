"""
telephony_cloudya.py — NFON *Cloudya* CTI integration (B3 in the spec).

The **alternative** telephony backend to the self-hosted Asterisk/WebRTC stack
in ``telephony.py``. Where that build embeds a softphone and listens to Asterisk
over AMI, this one leans on NFON's own products for the *call* side:

  * **Click-to-call** happens entirely in the **Cloudya Desktop App + CRM Connect
    browser plugin** (numbers on the page become dialable via the OS ``DIAL:``
    handler). There is nothing for us to serve — no code here for it.
  * **Caller ID / screen-pop** is driven by **CRM Connect's generic web lookup**:
    on an inbound call CRM Connect calls a URL with the caller's number; we
    resolve it against the same CRM tables (reusing ``telephony.lookup_caller``)
    and answer with the contact, and/or redirect the agent's browser to the CRM
    record ("URL pop").

So the only server surface is two **read-only, token-gated** endpoints:

  GET /telephony/cloudya/lookup?number=…   -> JSON {name, company, url, …}
  GET /telephony/cloudya/pop?number=…      -> 302 redirect to the CRM record

Both are called by the desktop client, which carries **no app session cookie**,
and both can reveal customer identity — so they are gated on a shared secret
(``CLOUDYA_LOOKUP_TOKEN``), fail closed when it is unset, and are a complete
no-op unless ``CLOUDYA_ENABLED`` is set (mirrors telephony.py's prod-safe
default). See ``CLOUDYA_RUNBOOK.md``.

NOTE: NFON's deeper *programmatic* CTI API (event subscription over their Cloudya
CTI API, per the manual linked in the spec) is a later increment — it needs the
NFON API contract, which is not reproduced here. The URL-lookup/pop mechanism
below is the standard CRM Connect custom-integration path and stands on its own.

Config (env):
  CLOUDYA_ENABLED=1
  CLOUDYA_LOOKUP_TOKEN=<shared secret CRM Connect presents on each call>
  CLOUDYA_NOMATCH_URL=/admin/crm     — where "pop" sends an unrecognised number
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

try:  # reuse the exact CRM lookup + normalisation the Asterisk path uses
    from app.telephony import lookup_caller, normalize_number
except ImportError:  # run with --app-dir=app
    from telephony import lookup_caller, normalize_number  # type: ignore

log = logging.getLogger("telephony.cloudya")


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


CLOUDYA_ENABLED = _env_bool("CLOUDYA_ENABLED")
CLOUDYA_LOOKUP_TOKEN = os.environ.get("CLOUDYA_LOOKUP_TOKEN", "")
CLOUDYA_NOMATCH_URL = os.environ.get("CLOUDYA_NOMATCH_URL", "/admin/crm")


# --------------------------------------------------------------------------- #
# Auth — a shared secret presented by CRM Connect. Header preferred (URLs get
# logged); query param accepted as a fallback for clients that can only template
# a URL. Fails CLOSED: enabled-but-unconfigured never serves PII.
# --------------------------------------------------------------------------- #
def _present_token(request: Request) -> str:
    return (request.headers.get("X-Cloudya-Token")
            or request.query_params.get("token") or "")


def token_ok(provided: str, expected: str) -> bool:
    """Constant-time check. Never true when the expected secret is empty, so a
    missing CLOUDYA_LOOKUP_TOKEN can't accidentally open the endpoints."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


# --------------------------------------------------------------------------- #
# Response shaping (pure — unit-tested)
# --------------------------------------------------------------------------- #
def caller_payload(match: dict | None, number: str) -> dict:
    """Shape a lookup_caller() result for CRM Connect's web data source. Field
    names are stable so the CRM Connect side can map them to its display slots."""
    norm = normalize_number(number)
    base = {"number": norm["national"] or norm["e164"] or number,
            "raw_number": number, "matched": bool(match)}
    if not match:
        return {**base, "name": None, "company": None, "url": None, "source": None}
    return {**base,
            "name": match.get("name"),
            "company": match.get("subtitle"),
            "url": match.get("url"),
            "source": match.get("source"),
            "customer_user_id": match.get("customer_user_id")}


def pop_target(match: dict | None) -> str:
    """Where the browser 'pop' should land: the matched record, else the
    configured no-match landing (a search/list page)."""
    if match and match.get("url"):
        return match["url"]
    return CLOUDYA_NOMATCH_URL


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def make_router(templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/telephony/cloudya", tags=["telephony-cloudya"])

    def _guard(request: Request):
        """Return an error Response if the request may not proceed, else None."""
        if not CLOUDYA_ENABLED:
            return JSONResponse({"error": "disabled"}, status_code=404)
        if not CLOUDYA_LOOKUP_TOKEN:
            log.error("CLOUDYA_ENABLED set but CLOUDYA_LOOKUP_TOKEN missing — "
                      "refusing to serve caller lookups without a token")
            return JSONResponse({"error": "misconfigured"}, status_code=503)
        if not token_ok(_present_token(request), CLOUDYA_LOOKUP_TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return None

    @router.get("/lookup")
    def lookup(request: Request, number: str = ""):
        """Resolve a caller number to a CRM contact for CRM Connect to display."""
        blocked = _guard(request)
        if blocked is not None:
            return blocked
        with cursor() as c:
            match = lookup_caller(c, number)
        return caller_payload(match, number)

    @router.get("/pop")
    def pop(request: Request, number: str = ""):
        """Redirect the agent's browser to the matched CRM record (URL pop)."""
        blocked = _guard(request)
        if blocked is not None:
            return blocked
        with cursor() as c:
            match = lookup_caller(c, number)
        # 302 so the browser follows to the record (or the no-match landing).
        return RedirectResponse(pop_target(match), status_code=302)

    return router
