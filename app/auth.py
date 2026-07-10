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

import pyotp
from psycopg.types.json import Json

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
    product default) from now. Enforces ONE active product at a time: any
    currently-active grant is expired first. Returns
    (new_subscription_row, n_expired)."""
    c.execute("SELECT default_period_days FROM proc.product "
              "WHERE code = %s AND is_active", (product_code,))
    prod = c.fetchone()
    if not prod:
        raise ValueError("unknown or inactive product")
    days = int(period_days) if period_days else int(prod["default_period_days"])
    if days <= 0:
        raise ValueError("period must be a positive number of days")
    # One product active at a time: expire any current (non-expired) grant so
    # the new one becomes the sole active subscription.
    c.execute("""UPDATE proc.user_subscription SET expires_at = now()
                 WHERE user_id = %s AND expires_at > now()
                 RETURNING id""", (uid,))
    n_expired = len(c.fetchall())
    c.execute("""
        INSERT INTO proc.user_subscription (user_id, product_code, expires_at, granted_by)
        VALUES (%s, %s, now() + (%s * interval '1 day'), %s)
        RETURNING id, product_code, started_at, expires_at
    """, (uid, product_code, days, granted_by))
    return c.fetchone(), n_expired


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


def subscription_history(c, uid):
    """All grants for a user, newest first, with product + granter names — the
    CRM customer page's product history."""
    c.execute("""
        SELECT s.id, s.product_code, p.name AS product_name,
               s.started_at, s.expires_at, s.created_at,
               s.granted_by, g.username AS granted_by_name,
               (s.expires_at > now()) AS active
        FROM proc.user_subscription s
        JOIN proc.product p ON p.code = s.product_code
        LEFT JOIN proc.app_user g ON g.id = s.granted_by
        WHERE s.user_id = %s
        ORDER BY s.expires_at DESC, s.created_at DESC
    """, (uid,))
    return c.fetchall()


# --------------------------------------------------------------------------- #
# CRM: admins/customers split, segments, editable profile
# --------------------------------------------------------------------------- #
# The subscription status as a SQL expression (mirror of _status_label), so the
# CRM can filter/group by it. `s` is the current-subscription lateral alias.
_SEG_CASE = """CASE
    WHEN s.product_code IS NULL THEN 'none'
    WHEN s.product_code = 'paid' AND s.expires_at > now() THEN 'subscriber'
    WHEN s.product_code = 'paid' THEN 'expired_subscriber'
    WHEN s.expires_at > now() THEN 'tester'
    ELSE 'expired_tester'
END"""

_CUR_SUB_JOIN = """
    LEFT JOIN LATERAL (
        SELECT product_code, expires_at
        FROM proc.user_subscription
        WHERE user_id = u.id
        ORDER BY expires_at DESC LIMIT 1
    ) s ON true
"""

# Admin-editable CRM profile columns (kept in one place so routes/templates and
# the upsert stay in sync).
PROFILE_FIELDS = ("full_name", "phone", "mobile", "job_title",
                  "company", "vat_number", "industry",
                  "country", "city", "address",
                  "lead_source", "about")


def list_admins(c):
    c.execute(f"SELECT {_COLS} FROM proc.app_user "
              f"WHERE role = 'admin' ORDER BY lower(username)")
    return c.fetchall()


def _customer_q_clause(q):
    """(sql, args) matching a free-text customer search over username / email /
    profile name / company / ΑΦΜ. Empty when q is blank."""
    q = (q or "").strip()
    if not q:
        return "", []
    like = f"%{q}%"
    sql = ("(u.username ILIKE %s OR u.email ILIKE %s OR p.full_name ILIKE %s "
           "OR p.company ILIKE %s OR p.vat_number ILIKE %s)")
    return " AND " + sql, [like, like, like, like, like]


def customer_segment_counts(c, q=None):
    """{status: n} across customers (optionally within a search), plus 'all'."""
    qsql, qargs = _customer_q_clause(q)
    c.execute(f"""
        SELECT {_SEG_CASE} AS status, count(*) AS n
        FROM proc.app_user u {_CUR_SUB_JOIN}
        LEFT JOIN proc.customer_profile p ON p.user_id = u.id
        WHERE u.role = 'customer'{qsql}
        GROUP BY 1
    """, qargs)
    counts = {r["status"]: r["n"] for r in c.fetchall()}
    counts["all"] = sum(counts.values())
    return counts


def list_customers(c, segment="all", q=None):
    """Customers with derived status + a little profile context, optionally
    filtered to one segment (status value, or 'all') and/or a free-text query."""
    qsql, qargs = _customer_q_clause(q)
    c.execute(f"""
        SELECT u.id, u.username, u.email, u.is_active,
               u.created_at, u.last_login_at,
               s.product_code AS sub_product, s.expires_at AS sub_expires_at,
               {_SEG_CASE} AS status,
               p.full_name, p.company
        FROM proc.app_user u {_CUR_SUB_JOIN}
        LEFT JOIN proc.customer_profile p ON p.user_id = u.id
        WHERE u.role = 'customer'{qsql}
        ORDER BY lower(coalesce(p.full_name, u.username))
    """, qargs)
    rows = [dict(r) for r in c.fetchall()]
    if segment and segment != "all":
        rows = [r for r in rows if r["status"] == segment]
    return rows


def get_customer(c, uid):
    """One customer account + derived status (None if not a customer)."""
    c.execute(f"""
        SELECT u.id, u.username, u.email, u.role, u.is_active,
               u.created_at, u.last_login_at,
               s.product_code AS sub_product, s.expires_at AS sub_expires_at,
               {_SEG_CASE} AS status
        FROM proc.app_user u {_CUR_SUB_JOIN}
        WHERE u.id = %s AND u.role = 'customer'
    """, (uid,))
    return c.fetchone()


def get_profile(c, uid):
    c.execute("SELECT * FROM proc.customer_profile WHERE user_id = %s", (uid,))
    return c.fetchone()


def upsert_profile(c, uid, values: dict, updated_by=None):
    """Insert/update the customer's profile row from a dict of PROFILE_FIELDS."""
    cols = list(PROFILE_FIELDS)
    vals = [(values.get(k) or None) for k in cols]
    set_clause = ", ".join(f"{k} = EXCLUDED.{k}" for k in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    c.execute(f"""
        INSERT INTO proc.customer_profile (user_id, {", ".join(cols)}, updated_at, updated_by)
        VALUES (%s, {placeholders}, now(), %s)
        ON CONFLICT (user_id) DO UPDATE
          SET {set_clause}, updated_at = now(), updated_by = EXCLUDED.updated_by
    """, [uid] + vals + [updated_by])


def set_email(c, uid, email):
    """Update a customer's contact email (unique index may raise — caller guards)."""
    c.execute("UPDATE proc.app_user SET email = %s WHERE id = %s",
              ((email or "").strip() or None, uid))


# --------------------------------------------------------------------------- #
# CRM activities: notes / calls / tasks (Phase 2)
# --------------------------------------------------------------------------- #
CALL_DIRECTIONS = ("outgoing", "incoming")
CALL_STATUSES = ("planned", "held", "not_held", "not_answered", "cancelled")
TASK_STATUSES = ("open", "done", "cancelled")


def admin_options(c):
    """Active admins for the assignee dropdowns."""
    c.execute("SELECT id, username FROM proc.app_user "
              "WHERE role = 'admin' AND is_active ORDER BY lower(username)")
    return c.fetchall()


def _nullable(v):
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def _as_uid(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---- notes ---- #
def add_note(c, uid, body, author_id):
    body = (body or "").strip()
    if not body:
        raise ValueError("empty note")
    c.execute("INSERT INTO proc.customer_note (user_id, body, author_id) "
              "VALUES (%s, %s, %s)", (uid, body, author_id))


def list_notes(c, uid):
    c.execute("""SELECT n.id, n.body, n.created_at, a.username AS author
                 FROM proc.customer_note n
                 LEFT JOIN proc.app_user a ON a.id = n.author_id
                 WHERE n.user_id = %s ORDER BY n.created_at DESC""", (uid,))
    return c.fetchall()


# ---- calls ---- #
def add_call(c, uid, subject, direction, status, scheduled_at, outcome,
             assigned_to, created_by):
    if direction not in CALL_DIRECTIONS:
        direction = "outgoing"
    if status not in CALL_STATUSES:
        status = "planned"
    c.execute("""INSERT INTO proc.customer_call
                 (user_id, subject, direction, status, scheduled_at, outcome,
                  assigned_to, created_by)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
              (uid, _nullable(subject), direction, status, _nullable(scheduled_at),
               _nullable(outcome), _as_uid(assigned_to), created_by))


def list_calls(c, uid):
    c.execute("""SELECT k.id, k.subject, k.direction, k.status, k.scheduled_at,
                        k.outcome, k.created_at,
                        asg.username AS assigned_name, cr.username AS created_name
                 FROM proc.customer_call k
                 LEFT JOIN proc.app_user asg ON asg.id = k.assigned_to
                 LEFT JOIN proc.app_user cr  ON cr.id  = k.created_by
                 WHERE k.user_id = %s
                 ORDER BY coalesce(k.scheduled_at, k.created_at) DESC""", (uid,))
    return c.fetchall()


def set_call_status(c, call_id, status, outcome=None):
    if status not in CALL_STATUSES:
        raise ValueError("invalid call status")
    c.execute("""UPDATE proc.customer_call
                 SET status = %s,
                     outcome = COALESCE(NULLIF(%s, ''), outcome),
                     updated_at = now()
                 WHERE id = %s""", (status, outcome, call_id))


# ---- tasks ---- #
def add_task(c, uid, subject, body, status, due_at, outcome, assigned_to, created_by):
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("empty subject")
    if status not in TASK_STATUSES:
        status = "open"
    c.execute("""INSERT INTO proc.customer_task
                 (user_id, subject, body, status, due_at, outcome,
                  assigned_to, created_by)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
              (uid, subject, _nullable(body), status, _nullable(due_at),
               _nullable(outcome), _as_uid(assigned_to), created_by))


def list_tasks(c, uid):
    c.execute("""SELECT t.id, t.subject, t.body, t.status, t.due_at, t.outcome,
                        t.created_at, t.completed_at,
                        asg.username AS assigned_name, cr.username AS created_name
                 FROM proc.customer_task t
                 LEFT JOIN proc.app_user asg ON asg.id = t.assigned_to
                 LEFT JOIN proc.app_user cr  ON cr.id  = t.created_by
                 WHERE t.user_id = %s
                 ORDER BY (t.status = 'open') DESC,
                          coalesce(t.due_at, t.created_at) ASC""", (uid,))
    return c.fetchall()


def set_task_status(c, task_id, status, outcome=None):
    if status not in TASK_STATUSES:
        raise ValueError("invalid task status")
    completed = "now()" if status == "done" else "NULL"
    c.execute(f"""UPDATE proc.customer_task
                  SET status = %s,
                      outcome = COALESCE(NULLIF(%s, ''), outcome),
                      completed_at = {completed}
                  WHERE id = %s""", (status, outcome, task_id))


# ---- cross-customer activity search (CRM aggregate pages) ---- #
def _act_filters(where, args, q, q_cols, status, statuses, assigned_to,
                 date_from, date_to, date_expr):
    """Shared filter builder for the call/task/note search queries. Mutates
    `where`/`args`. `q_cols` are the entity text columns to OR into the free-text
    match (customer name/company are always added)."""
    if q and q.strip():
        like = f"%{q.strip()}%"
        cols = list(q_cols) + ["u.username", "p.full_name", "p.company"]
        where.append("(" + " OR ".join(f"{c2} ILIKE %s" for c2 in cols) + ")")
        args += [like] * len(cols)
    if status and status in statuses:
        where.append("x.status = %s")
        args.append(status)
    aid = _as_uid(assigned_to)
    if aid:
        where.append("x.assigned_to = %s")
        args.append(aid)
    if date_from and date_from.strip():
        where.append(f"{date_expr} >= %s::date")
        args.append(date_from.strip())
    if date_to and date_to.strip():
        where.append(f"{date_expr} < (%s::date + 1)")
        args.append(date_to.strip())


_ACT_CUSTOMER_JOIN = """
    FROM proc.{table} x
    JOIN proc.app_user u ON u.id = x.user_id
    LEFT JOIN proc.customer_profile p ON p.user_id = x.user_id
    LEFT JOIN proc.app_user asg ON asg.id = x.assigned_to
"""


def search_calls(c, q=None, status=None, assigned_to=None,
                 date_from=None, date_to=None, limit=200):
    where, args = ["1=1"], []
    _act_filters(where, args, q, ["x.subject", "x.outcome"],
                 status, CALL_STATUSES, assigned_to, date_from, date_to,
                 "coalesce(x.scheduled_at, x.created_at)")
    c.execute(f"""
        SELECT x.id, x.user_id, x.subject, x.direction, x.status,
               x.scheduled_at, x.outcome, x.created_at,
               u.username AS customer_username, p.full_name AS customer_name,
               p.company AS customer_company, asg.username AS assigned_name
        {_ACT_CUSTOMER_JOIN.format(table="customer_call")}
        WHERE {' AND '.join(where)}
        ORDER BY coalesce(x.scheduled_at, x.created_at) DESC
        LIMIT {int(limit)}
    """, args)
    return c.fetchall()


def search_tasks(c, q=None, status=None, assigned_to=None,
                 date_from=None, date_to=None, limit=200):
    where, args = ["1=1"], []
    _act_filters(where, args, q, ["x.subject", "x.body", "x.outcome"],
                 status, TASK_STATUSES, assigned_to, date_from, date_to,
                 "coalesce(x.due_at, x.created_at)")
    c.execute(f"""
        SELECT x.id, x.user_id, x.subject, x.body, x.status, x.due_at,
               x.outcome, x.created_at, x.completed_at,
               u.username AS customer_username, p.full_name AS customer_name,
               p.company AS customer_company, asg.username AS assigned_name
        {_ACT_CUSTOMER_JOIN.format(table="customer_task")}
        WHERE {' AND '.join(where)}
        ORDER BY (x.status = 'open') DESC, coalesce(x.due_at, x.created_at) DESC
        LIMIT {int(limit)}
    """, args)
    return c.fetchall()


def search_notes(c, q=None, assigned_to=None, date_from=None, date_to=None, limit=200):
    """Notes have no status/assignee; `assigned_to` filters by author instead."""
    where, args = ["1=1"], []
    if q and q.strip():
        like = f"%{q.strip()}%"
        cols = ["x.body", "u.username", "p.full_name", "p.company"]
        where.append("(" + " OR ".join(f"{c2} ILIKE %s" for c2 in cols) + ")")
        args += [like] * len(cols)
    aid = _as_uid(assigned_to)
    if aid:
        where.append("x.author_id = %s")
        args.append(aid)
    if date_from and date_from.strip():
        where.append("x.created_at >= %s::date")
        args.append(date_from.strip())
    if date_to and date_to.strip():
        where.append("x.created_at < (%s::date + 1)")
        args.append(date_to.strip())
    c.execute(f"""
        SELECT x.id, x.user_id, x.body, x.created_at,
               u.username AS customer_username, p.full_name AS customer_name,
               p.company AS customer_company, a.username AS author
        FROM proc.customer_note x
        JOIN proc.app_user u ON u.id = x.user_id
        LEFT JOIN proc.customer_profile p ON p.user_id = x.user_id
        LEFT JOIN proc.app_user a ON a.id = x.author_id
        WHERE {' AND '.join(where)}
        ORDER BY x.created_at DESC
        LIMIT {int(limit)}
    """, args)
    return c.fetchall()


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
# login throttle (DB-backed; per (username, ip))
#
# Backed by proc.login_throttle rather than process memory so lockouts survive
# a restart and are shared across workers (in-memory state was per-process and
# reset on deploy). Takes the open cursor `c` like the other helpers here.
# --------------------------------------------------------------------------- #
_MAX_FAILS = 8
_LOCK_SECONDS = 300


def throttle_blocked(c, key) -> int:
    """Seconds remaining if locked out, else 0."""
    c.execute("""
        SELECT GREATEST(0, EXTRACT(EPOCH FROM (locked_until - now())))::int AS s
        FROM proc.login_throttle
        WHERE key = %s AND locked_until IS NOT NULL AND locked_until > now()
    """, (key,))
    row = c.fetchone()
    return int(row["s"]) if row else 0


def throttle_fail(c, key):
    """Record a failed attempt; lock the key once it crosses _MAX_FAILS."""
    c.execute("""
        INSERT INTO proc.login_throttle (key, fail_count, locked_until, updated_at)
        VALUES (%s, 1, NULL, now())
        ON CONFLICT (key) DO UPDATE SET
            fail_count   = proc.login_throttle.fail_count + 1,
            locked_until = CASE
                WHEN proc.login_throttle.fail_count + 1 >= %s
                THEN now() + make_interval(secs => %s)
                ELSE NULL END,
            updated_at   = now()
    """, (key, _MAX_FAILS, _LOCK_SECONDS))
    # Opportunistic cleanup so spray attacks can't grow the table unbounded:
    # drop idle rows (no active lock, untouched for a day). Cheap, indexed.
    c.execute("""
        DELETE FROM proc.login_throttle
        WHERE updated_at < now() - interval '1 day'
          AND (locked_until IS NULL OR locked_until < now())
    """)


def throttle_reset(c, key):
    c.execute("DELETE FROM proc.login_throttle WHERE key = %s", (key,))


# --------------------------------------------------------------------------- #
# Two-factor auth (TOTP + one-time recovery codes)
#
# Opt-in per account (recommended for admins). A user enrols self-service: we
# store the base32 TOTP secret and a set of scrypt-hashed one-time recovery
# codes (so a lost authenticator doesn't lock them out). mfa_enabled gates the
# extra step at login.
# --------------------------------------------------------------------------- #
MFA_ISSUER = "KHMDHS"


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    """otpauth:// URI for the authenticator QR / manual entry."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=MFA_ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    """True if `code` is a valid TOTP for `secret` (±1 step for clock skew)."""
    if not secret or not code:
        return False
    code = str(code).strip().replace(" ", "")
    if not code.isdigit():
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:      # noqa: BLE001
        return False


def gen_recovery_codes(n: int = 10):
    """Return (plaintext_codes, hashed_codes). Show the plaintext ONCE; store
    only the hashes."""
    plain = ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(n)]
    hashed = [hash_password(code) for code in plain]
    return plain, hashed


def get_mfa(c, uid):
    c.execute("SELECT mfa_enabled, mfa_secret, mfa_recovery_codes "
              "FROM proc.app_user WHERE id = %s", (uid,))
    return c.fetchone()


def enable_mfa(c, uid, secret, hashed_codes):
    c.execute("UPDATE proc.app_user SET mfa_enabled = true, mfa_secret = %s, "
              "mfa_recovery_codes = %s WHERE id = %s", (secret, hashed_codes, uid))


def disable_mfa(c, uid):
    c.execute("UPDATE proc.app_user SET mfa_enabled = false, mfa_secret = NULL, "
              "mfa_recovery_codes = '{}' WHERE id = %s", (uid,))


def consume_recovery_code(c, uid, code) -> bool:
    """Verify a one-time recovery code and, on match, remove it. Returns True on
    a successful (consumed) match."""
    code = (code or "").strip().lower().replace(" ", "")
    if not code:
        return False
    row = get_mfa(c, uid)
    if not row:
        return False
    codes = list(row["mfa_recovery_codes"] or [])
    for i, h in enumerate(codes):
        if verify_password(code, h):
            del codes[i]
            c.execute("UPDATE proc.app_user SET mfa_recovery_codes = %s WHERE id = %s",
                      (codes, uid))
            return True
    return False


# --------------------------------------------------------------------------- #
# Self-service account data (GDPR: access / erasure)
# --------------------------------------------------------------------------- #
def export_account(c, uid):
    """Every piece of personal data held for a user, for self-service export.
    Excludes the password hash."""
    c.execute("""SELECT id, username, email, role, is_active, created_at, last_login_at
                 FROM proc.app_user WHERE id = %s""", (uid,))
    account = c.fetchone()
    c.execute("""SELECT id, product_code, started_at, expires_at, created_at
                 FROM proc.user_subscription WHERE user_id = %s ORDER BY created_at""", (uid,))
    subs = c.fetchall()
    c.execute("""SELECT full_name, phone, mobile, job_title, company, vat_number,
                        industry, country, city, address, lead_source, about, updated_at
                 FROM proc.customer_profile WHERE user_id = %s""", (uid,))
    profile = c.fetchone()
    c.execute("""SELECT id, body, created_at FROM proc.customer_note
                 WHERE user_id = %s ORDER BY created_at""", (uid,))
    notes = c.fetchall()
    c.execute("""SELECT id, subject, direction, status, scheduled_at, outcome, created_at
                 FROM proc.customer_call WHERE user_id = %s ORDER BY created_at""", (uid,))
    calls = c.fetchall()
    c.execute("""SELECT id, subject, body, status, due_at, outcome, created_at, completed_at
                 FROM proc.customer_task WHERE user_id = %s ORDER BY created_at""", (uid,))
    tasks = c.fetchall()
    return {"account": account, "subscriptions": subs, "profile": profile,
            "notes": notes, "calls": calls, "tasks": tasks}


def delete_account(c, uid):
    """Hard-delete the user. FKs cascade their subscriptions, profile, notes,
    calls and tasks; admin audit rows keep a username snapshot (user_id → NULL)."""
    c.execute("DELETE FROM proc.app_user WHERE id = %s", (uid,))


# --------------------------------------------------------------------------- #
# Search profiles (saved searches)
#
# scope 'portal'   — admin-owned, global; visible to customers only when
#                    is_published. scope 'customer' — owned by one customer.
# A customer profile may reference a portal profile (based_on_id) as a LIVE link:
# effective filters = own params if set, else the referenced profile's params.
# `params` is the search filter dict the search page reads.
# --------------------------------------------------------------------------- #
def create_search_profile(c, *, name, scope, owner_id, params, based_on_id, created_by):
    c.execute("""INSERT INTO proc.search_profile
                   (name, scope, owner_user_id, based_on_id, params, created_by)
                 VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
              (name, scope, owner_id, based_on_id,
               Json(params) if params is not None else None, created_by))
    return c.fetchone()["id"]


def get_search_profile(c, pid):
    c.execute("SELECT * FROM proc.search_profile WHERE id = %s", (pid,))
    return c.fetchone()


def update_search_profile(c, pid, *, name, params, based_on_id, is_published):
    c.execute("""UPDATE proc.search_profile
                 SET name=%s, params=%s, based_on_id=%s, is_published=%s, updated_at=now()
                 WHERE id=%s""",
              (name, Json(params) if params is not None else None,
               based_on_id, is_published, pid))


def set_profile_published(c, pid, published):
    c.execute("UPDATE proc.search_profile SET is_published=%s, updated_at=now() "
              "WHERE id=%s AND scope='portal'", (published, pid))


def delete_search_profile(c, pid):
    c.execute("DELETE FROM proc.search_profile WHERE id = %s", (pid,))


def effective_params(c, profile):
    """The profile's own params, else (live link) the referenced profile's."""
    if profile.get("params") is not None:
        return profile["params"]
    bid = profile.get("based_on_id")
    if bid:
        c.execute("SELECT params FROM proc.search_profile WHERE id=%s", (bid,))
        r = c.fetchone()
        if r and r["params"] is not None:
            return r["params"]
    return {}


def list_all_profiles(c):
    """All profiles + owner username, for the admin management page."""
    c.execute("""SELECT sp.*, u.username AS owner_username,
                        b.name AS based_on_name
                 FROM proc.search_profile sp
                 LEFT JOIN proc.app_user u ON u.id = sp.owner_user_id
                 LEFT JOIN proc.search_profile b ON b.id = sp.based_on_id
                 ORDER BY sp.scope, lower(sp.name)""")
    return c.fetchall()


def profiles_for_user(c, user):
    """Profiles a user may APPLY on the search page: admins get every portal
    profile; everyone else gets published portal profiles plus their own."""
    if user and user.get("role") == "admin":
        c.execute("SELECT * FROM proc.search_profile WHERE scope='portal' "
                  "ORDER BY lower(name)")
        return c.fetchall()
    uid = user.get("id") if user else None
    c.execute("""SELECT * FROM proc.search_profile
                 WHERE (scope='portal' AND is_published)
                    OR (scope='customer' AND owner_user_id=%s)
                 ORDER BY scope DESC, lower(name)""", (uid,))
    return c.fetchall()


def can_apply_profile(user, profile):
    """Whether `user` may apply/see `profile`."""
    is_admin = bool(user and user.get("role") == "admin")
    if profile["scope"] == "portal":
        return bool(profile["is_published"]) or is_admin
    return is_admin or bool(user and user.get("id") == profile["owner_user_id"])


def can_manage_profile(user, profile):
    """Whether `user` may edit/delete `profile` (admins, or a customer's own)."""
    is_admin = bool(user and user.get("role") == "admin")
    if profile["scope"] == "portal":
        return is_admin
    return is_admin or bool(user and user.get("id") == profile["owner_user_id"])
