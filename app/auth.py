"""
auth.py — accounts, password hashing, sessions and role helpers.

Replaces the single shared HTTP-Basic password with real accounts
(proc.app_user) and three runtime tiers:
  anonymous  — no session (public teaser)
  customer   — self-registered, full read
  admin      — full access incl. /admin

Passwords: stdlib scrypt (memory-hard) with a per-user random salt — no third-
party dependency. Format: "scrypt$N$r$p$salt_hex$hash_hex".

Sessions: Starlette SessionMiddleware (signed cookie); this module only reads/
writes request.session and looks users up in the DB. DB helpers take an OPEN
dict-row cursor `c` (the app already opens `with cursor() as c:` everywhere).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

# scrypt params. N=2^14 → ~16 MB working memory per hash: strong for the
# occasional login, cheap enough for the small Render instance.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32
_MAXMEM = 96 * 1024 * 1024


# --------------------------------------------------------------------------- #
# password hashing
# --------------------------------------------------------------------------- #
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P,
                        dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)
        dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=int(n), r=int(r),
                            p=int(p), dklen=len(expected), maxmem=_MAXMEM)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def normalize_username(u: str) -> str:
    return (u or "").strip()


def username_ok(u: str) -> bool:
    u = normalize_username(u)
    return 3 <= len(u) <= 40 and all(
        ch.isalnum() or ch in "._-@" for ch in u)


def password_ok(pw: str) -> bool:
    return isinstance(pw, str) and 8 <= len(pw) <= 200


# --------------------------------------------------------------------------- #
# DB helpers (c = open dict-row cursor)
# --------------------------------------------------------------------------- #
_COLS = "id, username, email, role, is_active, created_at, last_login_at"
# Same columns, qualified — for queries that JOIN the subscription (which also
# has an `id` column, so bare `id` would be ambiguous).
_UCOLS = ", ".join("u." + col.strip() for col in _COLS.split(","))


def _status_label(product_code, active) -> str:
    """Derive the customer-facing status from the current grant.
    None grant -> 'none'; paid -> subscriber/expired_subscriber; anything else
    (test) -> tester/expired_tester."""
    if not product_code:
        return "none"
    if product_code == "paid":
        return "subscriber" if active else "expired_subscriber"
    return "tester" if active else "expired_tester"


def get_by_username(c, username):
    c.execute(f"SELECT {_COLS}, password_hash FROM proc.app_user "
              f"WHERE lower(username) = lower(%s)", (normalize_username(username),))
    return c.fetchone()


def load_user(c, uid):
    """Active user by id — used on every request to resolve the session, so
    deactivation / role change / subscription expiry take effect immediately
    (not on next login). Carries the CURRENT subscription (greatest expires_at)
    plus a derived `status` and `has_access` flag, so the middleware and every
    template can gate on it without a second query."""
    c.execute(f"""
        SELECT {_UCOLS},
               s.product_code AS sub_product,
               s.expires_at   AS sub_expires_at,
               COALESCE(s.expires_at > now(), false) AS sub_active
        FROM proc.app_user u
        LEFT JOIN LATERAL (
            SELECT product_code, expires_at
            FROM proc.user_subscription
            WHERE user_id = u.id
            ORDER BY expires_at DESC
            LIMIT 1
        ) s ON true
        WHERE u.id = %s AND u.is_active
    """, (uid,))
    row = c.fetchone()
    if not row:
        return None
    u = dict(row)
    u["status"] = _status_label(u.get("sub_product"), u.get("sub_active"))
    # Admins always have full access; customers need an active, non-expired sub.
    u["has_access"] = (u["role"] == "admin") or bool(u.get("sub_active"))
    return u


def create_user(c, username, password, role="customer", email=None):
    """Insert a user; raises ValueError on bad input / duplicate."""
    username = normalize_username(username)
    if not username_ok(username):
        raise ValueError("invalid username")
    if not password_ok(password):
        raise ValueError("password must be 8–200 characters")
    if role not in ("admin", "customer"):
        raise ValueError("invalid role")
    email = (email or "").strip() or None
    c.execute(
        """INSERT INTO proc.app_user (username, email, password_hash, role)
           VALUES (%s, %s, %s, %s) RETURNING """ + _COLS,
        (username, email, hash_password(password), role))
    return c.fetchone()


def set_password(c, uid, password):
    if not password_ok(password):
        raise ValueError("password must be 8–200 characters")
    c.execute("UPDATE proc.app_user SET password_hash = %s WHERE id = %s",
              (hash_password(password), uid))


def set_role(c, uid, role):
    if role not in ("admin", "customer"):
        raise ValueError("invalid role")
    c.execute("UPDATE proc.app_user SET role = %s WHERE id = %s", (role, uid))


def set_active(c, uid, active: bool):
    c.execute("UPDATE proc.app_user SET is_active = %s WHERE id = %s",
              (bool(active), uid))


def touch_last_login(c, uid):
    c.execute("UPDATE proc.app_user SET last_login_at = now() WHERE id = %s",
              (uid,))


def list_users(c):
    c.execute(f"SELECT {_COLS} FROM proc.app_user ORDER BY role, lower(username)")
    return c.fetchall()


def count_users(c):
    c.execute("SELECT count(*) AS n FROM proc.app_user")
    row = c.fetchone()
    return row["n"] if row else 0


def list_users_with_subscription(c):
    """All users + their current grant, for the admin UI. `status` derived like
    load_user; `sub_id` lets the admin edit/extend that exact grant."""
    c.execute(f"""
        SELECT {_UCOLS},
               s.id           AS sub_id,
               s.product_code AS sub_product,
               s.expires_at   AS sub_expires_at,
               COALESCE(s.expires_at > now(), false) AS sub_active
        FROM proc.app_user u
        LEFT JOIN LATERAL (
            SELECT id, product_code, expires_at
            FROM proc.user_subscription
            WHERE user_id = u.id
            ORDER BY expires_at DESC
            LIMIT 1
        ) s ON true
        ORDER BY u.role, lower(u.username)
    """)
    out = []
    for r in c.fetchall():
        d = dict(r)
        d["status"] = _status_label(d.get("sub_product"), d.get("sub_active"))
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
# products & subscriptions
# --------------------------------------------------------------------------- #
def product_list(c):
    c.execute("SELECT code, name, default_period_days, is_active "
              "FROM proc.product ORDER BY default_period_days")
    return c.fetchall()


def set_product_default_days(c, code, days):
    days = int(days)
    if days <= 0:
        raise ValueError("period must be a positive number of days")
    c.execute("UPDATE proc.product SET default_period_days = %s WHERE code = %s",
              (days, code))


def current_subscription(c, uid):
    """The user's current grant (greatest expires_at) joined to its product."""
    c.execute("""
        SELECT s.id, s.product_code, p.name AS product_name,
               s.started_at, s.expires_at, s.granted_by,
               (s.expires_at > now()) AS active
        FROM proc.user_subscription s
        JOIN proc.product p ON p.code = s.product_code
        WHERE s.user_id = %s
        ORDER BY s.expires_at DESC
        LIMIT 1
    """, (uid,))
    return c.fetchone()


def grant_product(c, uid, product_code, granted_by=None, period_days=None):
    """Grant `product_code` to user `uid`, expiring `period_days` (or the
    product default) from now. Returns the new subscription row."""
    c.execute("SELECT default_period_days FROM proc.product "
              "WHERE code = %s AND is_active", (product_code,))
    prod = c.fetchone()
    if not prod:
        raise ValueError("unknown or inactive product")
    days = int(period_days) if period_days else int(prod["default_period_days"])
    if days <= 0:
        raise ValueError("period must be a positive number of days")
    c.execute("""
        INSERT INTO proc.user_subscription (user_id, product_code, expires_at, granted_by)
        VALUES (%s, %s, now() + (%s * interval '1 day'), %s)
        RETURNING id, product_code, started_at, expires_at
    """, (uid, product_code, days, granted_by))
    return c.fetchone()


def set_subscription_expiry(c, sub_id, expires_at):
    """Set an absolute expiry (admin edit)."""
    c.execute("UPDATE proc.user_subscription SET expires_at = %s WHERE id = %s",
              (expires_at, sub_id))


def extend_subscription(c, sub_id, days):
    """Add `days` to a grant, extending from the later of its current expiry or
    now (so extending an already-expired grant starts from today)."""
    days = int(days)
    c.execute("""UPDATE proc.user_subscription
                 SET expires_at = greatest(expires_at, now()) + (%s * interval '1 day')
                 WHERE id = %s""", (days, sub_id))


# --------------------------------------------------------------------------- #
# session (Starlette request.session)
# --------------------------------------------------------------------------- #
def login_session(request, user):
    request.session["uid"] = user["id"]
    request.session["username"] = user["username"]
    request.session["role"] = user["role"]


def logout_session(request):
    request.session.clear()


def session_uid(request):
    try:
        return request.session.get("uid")
    except Exception:      # SessionMiddleware not active (shouldn't happen)
        return None


# --------------------------------------------------------------------------- #
# login throttle (in-memory; per (username, ip))
# --------------------------------------------------------------------------- #
_FAILS: dict = {}
_MAX_FAILS = 8
_LOCK_SECONDS = 300


def throttle_blocked(key) -> int:
    """Seconds remaining if locked out, else 0."""
    rec = _FAILS.get(key)
    if not rec:
        return 0
    _, until = rec
    return max(0, int(until - time.time()))


def throttle_fail(key):
    n, _ = _FAILS.get(key, (0, 0))
    n += 1
    until = time.time() + _LOCK_SECONDS if n >= _MAX_FAILS else 0
    _FAILS[key] = (n, until)


def throttle_reset(key):
    _FAILS.pop(key, None)
