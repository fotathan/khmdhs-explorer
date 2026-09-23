from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse


_MESSAGES = {
    "el": {
        "invalid_request": "Το αίτημα δεν είναι έγκυρο.",
        "validation_error": "Ένα ή περισσότερα πεδία δεν είναι έγκυρα.",
        "invalid_credentials": "Τα στοιχεία σύνδεσης δεν είναι έγκυρα.",
        "invalid_mfa": "Ο κωδικός επαλήθευσης δεν είναι έγκυρος.",
        "invalid_challenge": "Η διαδικασία επαλήθευσης δεν είναι έγκυρη.",
        "challenge_expired": "Η διαδικασία επαλήθευσης έχει λήξει.",
        "token_expired": "Η συνεδρία έχει λήξει.",
        "invalid_refresh": "Η συνεδρία δεν μπορεί να ανανεωθεί.",
        "invalid_cursor": "Η σελίδα αποτελεσμάτων δεν είναι πλέον έγκυρη.",
        "idempotency_conflict": "Το ίδιο κλειδί αιτήματος χρησιμοποιήθηκε με διαφορετικά δεδομένα.",
        "conflict": "Ο πόρος χρησιμοποιείται ήδη από άλλη συσκευή.",
        "saved_search_limit": "Έχετε φτάσει το όριο αποθηκευμένων αναζητήσεων.",
        "session_revoked": "Η συνεδρία έχει ανακληθεί. Συνδεθείτε ξανά.",
        "access_expired": "Απαιτείται ενεργή πρόσβαση για αυτή τη λειτουργία.",
        "password_change_required": "Απαιτείται αλλαγή κωδικού πρόσβασης.",
        "rate_limited": "Πολλές προσπάθειες. Δοκιμάστε ξανά αργότερα.",
        "not_found": "Ο πόρος δεν βρέθηκε.",
        "service_unavailable": "Η υπηρεσία δεν είναι προσωρινά διαθέσιμη.",
    },
    "en": {
        "invalid_request": "The request is invalid.",
        "validation_error": "One or more fields are invalid.",
        "invalid_credentials": "The sign-in details are invalid.",
        "invalid_mfa": "The verification code is invalid.",
        "invalid_challenge": "The verification attempt is invalid.",
        "challenge_expired": "The verification attempt has expired.",
        "token_expired": "The session has expired.",
        "invalid_refresh": "The session cannot be refreshed.",
        "invalid_cursor": "The result page is no longer valid.",
        "idempotency_conflict": "The same request key was used with different data.",
        "conflict": "The resource is already registered to another device.",
        "saved_search_limit": "You have reached the saved-search limit.",
        "session_revoked": "The session was revoked. Sign in again.",
        "access_expired": "Active access is required for this operation.",
        "password_change_required": "A password change is required.",
        "rate_limited": "Too many attempts. Try again later.",
        "not_found": "The resource was not found.",
        "service_unavailable": "The service is temporarily unavailable.",
    },
}


def language(request: Request) -> str:
    raw = (request.headers.get("accept-language") or "el").lower()
    return "en" if raw.startswith("en") else "el"


def request_id(request: Request) -> str:
    return (getattr(request.state, "api_request_id", None)
            or getattr(request.state, "request_id", "unknown"))


@dataclass
class ApiError(Exception):
    code: str
    status_code: int = 400
    message: str | None = None
    fields: list[dict] | None = None
    headers: dict[str, str] | None = None


def error_response(request: Request, *, code: str, status_code: int,
                   message: str | None = None, fields: list[dict] | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    lang = language(request)
    body = {
        "error": {
            "code": code,
            "message": message or _MESSAGES.get(lang, _MESSAGES["el"]).get(
                code, _MESSAGES[lang]["invalid_request"]),
            "request_id": request_id(request),
        }
    }
    if fields:
        body["error"]["fields"] = fields
    return JSONResponse(body, status_code=status_code, headers=headers or {})


async def api_error_handler(request: Request, exc: ApiError):
    return error_response(request, code=exc.code, status_code=exc.status_code,
                          message=exc.message, fields=exc.fields,
                          headers=exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/v1"):
        return await request_validation_exception_handler(request, exc)
    fields = []
    for err in exc.errors():
        loc = [str(item) for item in err.get("loc", ()) if item not in ("body",)]
        fields.append({"field": ".".join(loc), "code": err.get("type", "invalid")})
    return error_response(request, code="validation_error", status_code=422,
                          fields=fields[:20])
