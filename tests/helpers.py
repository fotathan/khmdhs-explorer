"""Shared test helpers: a DB connection + fixtures-free utilities the tests use."""
from __future__ import annotations

import os
import re

import psycopg
from psycopg.rows import dict_row


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                           prepare_threshold=None, row_factory=dict_row)


def make_user(username, password="pw-123456", role="customer", active=True):
    """Create a user via the app's own auth helper; return its id."""
    from app import auth as _auth
    with connect() as c:
        cur = c.cursor()
        row = _auth.create_user(cur, username, password, role=role)
        uid = row["id"] if isinstance(row, dict) else row
        if not active:
            _auth.set_active(cur, uid, False)
    return uid


def grant(uid, code="pro", days=365):
    from app import auth as _auth
    with connect() as c:
        _auth.grant_product(c.cursor(), uid, code, period_days=days)


def expire_sub(uid, code="pro"):
    """Grant a product, then force its expiry into the past."""
    from app import auth as _auth
    with connect() as c:
        cur = c.cursor()
        _auth.grant_product(cur, uid, code, period_days=365)
        cur.execute("UPDATE proc.user_subscription SET expires_at = now() - interval '1 day' "
                    "WHERE user_id = %s", (uid,))


def enable_mfa(uid):
    """Turn on 2FA for a user directly; return (secret, [recovery_codes])."""
    from app import auth as _auth
    secret = _auth.new_totp_secret()
    plain, hashed = _auth.gen_recovery_codes()
    with connect() as c:
        _auth.enable_mfa(c.cursor(), uid, secret, hashed)
    return secret, plain


def login(client, username, password):
    return client.post("/login",
                       data={"username": username, "password": password},
                       follow_redirects=False)


def logout(client):
    """POST /logout with the CSRF token (logout is a CSRF-protected POST)."""
    return client.post("/logout", headers={"X-CSRF-Token": get_csrf(client)},
                       follow_redirects=False)


_CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')


def get_csrf(client):
    """Pull the CSRF token from a rendered page's <meta> tag (session-backed)."""
    for path in ("/account", "/"):
        m = _CSRF_RE.search(client.get(path, follow_redirects=False).text)
        if m:
            return m.group(1)
    return None
