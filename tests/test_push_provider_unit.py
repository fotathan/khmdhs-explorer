"""Expo adapter classification without external network calls."""
from __future__ import annotations

import pytest

from app.push_provider import (
    ExpoPushProvider,
    ProviderTemporaryError,
)


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_ticket_results_preserve_order_and_classify_retryable_errors():
    session = Session(Response(200, {"data": [
        {"status": "ok", "id": "ticket-1"},
        {"status": "error", "details": {"error": "DeviceNotRegistered"}},
        {"status": "error", "details": {"error": "MessageRateExceeded"}},
    ]}))
    results = ExpoPushProvider(session).send([
        {"to": "one"}, {"to": "two"}, {"to": "three"}])
    assert results[0].accepted and results[0].ticket_id == "ticket-1"
    assert results[1].error_code == "DeviceNotRegistered"
    assert not results[1].retryable
    assert results[2].retryable
    assert len(session.calls) == 1


def test_receipts_distinguish_success_pending_and_unregistered():
    session = Session(Response(200, {"data": {
        "ticket-1": {"status": "ok"},
        "ticket-3": {"status": "error",
                     "details": {"error": "DeviceNotRegistered"}},
    }}))
    results = ExpoPushProvider(session).receipts(
        ["ticket-1", "ticket-2", "ticket-3"])
    assert results["ticket-1"].status == "accepted"
    assert results["ticket-2"].status == "pending"
    assert results["ticket-3"].status == "rejected"


def test_http_rate_limit_is_temporary_and_batches_are_bounded():
    provider = ExpoPushProvider(Session(Response(429, {})))
    with pytest.raises(ProviderTemporaryError):
        provider.send([{"to": "one"}])
    with pytest.raises(ValueError):
        provider.send([])
    with pytest.raises(ValueError):
        provider.send([{"to": "x"}] * 101)
