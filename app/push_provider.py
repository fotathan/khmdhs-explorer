"""Small Expo Push Service adapter with bounded, classifiable failures."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import requests

SEND_URL = "https://exp.host/--/api/v2/push/send"
RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
MAX_BATCH = 100
_RETRYABLE_CODES = {"MessageRateExceeded", "ExpoServerError"}


class ProviderTemporaryError(RuntimeError):
    pass


class ProviderPermanentError(RuntimeError):
    pass


@dataclass(frozen=True)
class TicketResult:
    accepted: bool
    ticket_id: str | None = None
    error_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class ReceiptResult:
    status: str  # accepted | rejected | retry | pending
    error_code: str | None = None


def safe_error_code(value, fallback="provider_error") -> str:
    text = str(value or "").strip()
    return text[:64] if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", text) else fallback


class ExpoPushProvider:
    def __init__(self, session=None, timeout: float = 10.0):
        self.session = session or requests.Session()
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        access_token = (os.environ.get("EXPO_ACCESS_TOKEN") or "").strip()
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def _post(self, url: str, body: dict | list) -> dict:
        try:
            response = self.session.post(
                url, json=body, headers=self._headers(), timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProviderTemporaryError("provider_network") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderTemporaryError(f"provider_http_{response.status_code}")
        if response.status_code >= 400:
            raise ProviderPermanentError(f"provider_http_{response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderTemporaryError("provider_invalid_json") from exc
        if not isinstance(payload, dict):
            raise ProviderTemporaryError("provider_invalid_response")
        return payload

    def send(self, messages: list[dict]) -> list[TicketResult]:
        if not messages or len(messages) > MAX_BATCH:
            raise ValueError("Expo batches must contain 1..100 messages")
        payload = self._post(SEND_URL, messages)
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(messages):
            raise ProviderTemporaryError("provider_ticket_mismatch")
        results = []
        for ticket in data:
            if isinstance(ticket, dict) and ticket.get("status") == "ok" and ticket.get("id"):
                results.append(TicketResult(True, str(ticket["id"])[:256]))
                continue
            details = ticket.get("details") if isinstance(ticket, dict) else None
            code = safe_error_code(details.get("error") if isinstance(details, dict) else None)
            results.append(TicketResult(
                False, error_code=code, retryable=code in _RETRYABLE_CODES))
        return results

    def receipts(self, ticket_ids: list[str]) -> dict[str, ReceiptResult]:
        if not ticket_ids or len(ticket_ids) > MAX_BATCH:
            raise ValueError("Expo receipt batches must contain 1..100 IDs")
        payload = self._post(RECEIPTS_URL, {"ids": ticket_ids})
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderTemporaryError("provider_receipt_mismatch")
        results = {}
        for ticket_id in ticket_ids:
            receipt = data.get(ticket_id)
            if receipt is None:
                results[ticket_id] = ReceiptResult("pending")
            elif isinstance(receipt, dict) and receipt.get("status") == "ok":
                results[ticket_id] = ReceiptResult("accepted")
            else:
                details = receipt.get("details") if isinstance(receipt, dict) else None
                code = safe_error_code(
                    details.get("error") if isinstance(details, dict) else None)
                results[ticket_id] = ReceiptResult(
                    "retry" if code in _RETRYABLE_CODES else "rejected", code)
        return results
