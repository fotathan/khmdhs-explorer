"""Which Supabase connection mode this app is configured for, and the one switch
that depends on getting it right.

Supabase offers three ways in, and two of them share a port number:

  direct       db.<ref>.supabase.co:5432   IPv6 unless the IPv4 add-on is bought
  session      ...pooler.supabase.com:5432 IPv4 everywhere, prepared statements OK
  transaction  ...pooler.supabase.com:6543 IPv4, NO prepared statements

Render is IPv4-only and this app is a long-running process holding a pool, so
the session pooler is the match. What the CODE has to get right is narrower:
prepared statements must stay OFF unless someone deliberately turns them on,
because enabling them against the transaction pooler fails on live traffic with
"prepared statement _pg3_N already exists" — and it fails at request time, not
at boot, so a bad value ships quietly.

That is the same failure shape as the LOGIN_LINKS_ENABLED incident in
CLAUDE.md: a dashboard-typed word that reads as affirmative must not be
mistaken for a setting.
"""
import pytest

from tests.helpers import grant, login, make_user


# --------------------------------------------------------------------------- #
# The switch fails towards OFF
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [
    None, "", "   ",
    "true", "TRUE", "yes", "on", "enabled",       # affirmative-looking words
    "-1", "5.5", "five", "5x", "0x5",             # not a plain integer
])
def test_anything_that_is_not_a_plain_integer_means_off(raw):
    from app.main import _prepare_threshold
    assert _prepare_threshold(raw) is None, f"{raw!r} switched prepared statements ON"


@pytest.mark.parametrize("raw,expected", [("0", 0), ("5", 5), ("  5 ", 5), ("100", 100)])
def test_a_plain_integer_is_the_threshold(raw, expected):
    from app.main import _prepare_threshold
    assert _prepare_threshold(raw) == expected


def test_the_default_is_off():
    """Safe for whichever mode the connection string is actually in — turning
    them on is a deliberate act, taken after the mode is confirmed."""
    import os
    from app.main import _prepare_threshold
    assert _prepare_threshold(os.environ.get("DB_PREPARE_THRESHOLD_UNSET_XYZ")) is None


def test_the_pool_is_built_with_whatever_the_parser_returned():
    """No second source of truth: the pool's kwarg IS the parsed value."""
    from app import main as _main
    assert _main._pool.kwargs["prepare_threshold"] == _main._PREPARE_THRESHOLD


# --------------------------------------------------------------------------- #
# /version carries the evidence for sizing the pool
# --------------------------------------------------------------------------- #
def test_version_reports_whether_prepared_statements_are_on(client):
    pool = client.get("/version").json()["pool"]
    assert pool["prepared_statements"] is False
    assert pool["min"] and pool["max"]


def test_pool_statistics_are_admin_only(client):
    """/version is public and the stats are a load signal."""
    assert "stats" not in client.get("/version").json()["pool"]


def test_an_admin_gets_the_numbers_that_size_the_pool(client):
    make_user("ver_admin", "goodpassword1", role="admin")
    login(client, "ver_admin", "goodpassword1")
    stats = client.get("/version").json()["pool"]["stats"]
    # pool_size is the high-water mark; requests_queued counts checkouts that
    # ever had to wait. Still 0 under real traffic means the ceiling is never
    # reached, which is what would justify lowering DB_POOL_MAX.
    for key in ("pool_size", "pool_max", "requests_num", "requests_queued"):
        assert key in stats, f"{key} missing — pool sizing would be guesswork"


def test_max_size_is_a_ceiling_not_a_reservation(client):
    """The premise behind 'lower DB_POOL_MAX': measured false. Sequential
    traffic never grows the pool past min_size, so a lower ceiling would not
    reduce steady-state connections — it would only queue sooner in a burst."""
    from app import main as _main
    make_user("ver_admin2", "goodpassword1", role="admin")
    login(client, "ver_admin2", "goodpassword1")
    for _ in range(10):
        client.get("/healthz")
    stats = client.get("/version").json()["pool"]["stats"]
    assert stats["pool_size"] <= max(_main._POOL_MIN, 2), stats
    assert stats["pool_size"] < stats["pool_max"]
