"""Pure checks for mobile token and retention primitives (no database)."""
from __future__ import annotations

import hashlib
import hmac

import pytest

from app import mobile_auth


def test_token_hash_is_keyed_deterministic_and_not_plaintext(monkeypatch):
    monkeypatch.setenv("MOBILE_TOKEN_HASH_KEY", "unit-test-secret")
    raw = "mrt_example-secret-token"
    expected = hmac.new(b"unit-test-secret", raw.encode(), hashlib.sha256).digest()
    assert mobile_auth.token_hash(raw) == expected
    assert mobile_auth.token_hash(raw) == mobile_auth.token_hash(raw)
    assert mobile_auth.token_hash(raw) != raw.encode()


def test_token_hash_requires_a_production_key(monkeypatch):
    monkeypatch.delenv("MOBILE_TOKEN_HASH_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(mobile_auth.MobileAuthConfigurationError):
        mobile_auth.token_hash("mat_example")


class _CleanupCursor:
    def __init__(self):
        self.commands = []

    def execute(self, sql, params=None):
        self.commands.append(sql)

    def fetchone(self):
        return {"n": 2}


def test_cleanup_is_transactional_and_returns_aggregate_counts_only():
    cursor = _CleanupCursor()
    counts = mobile_auth.cleanup_expired(cursor)
    assert counts == {
        "idempotency_keys": 2,
        "mfa_challenges": 2,
        "mfa_enrollments": 2,
        "access_tokens": 2,
        "refresh_tokens": 2,
        "sessions": 2,
        "devices": 2,
    }
    assert cursor.commands[0] == "BEGIN"
    assert cursor.commands[-1] == "COMMIT"
    assert all("RETURNING id" in sql for sql in cursor.commands[1:-1])
