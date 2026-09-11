"""The connection pool's checkout check, and what it is allowed to skip.

psycopg_pool runs the `check` callback on EVERY checkout, and
ConnectionPool.check_connection sends an empty query — one full round trip to
the database each time. A signed-in act page checks out five times, so an eager
check bought five round trips per page to re-prove a connection that had been
used milliseconds earlier.

app.main._check_if_idle skips the check for a connection that was returned to
the pool recently. That is only safe because of a guarantee psycopg_pool makes
on the OTHER side of the loop, and both halves are pinned here:

  * a connection that breaks WHILE IN USE never re-enters the pool —
    _return_connection discards any connection whose transaction_status is
    UNKNOWN. So a skipped check can never hand out a connection that died
    during someone else's request;
  * a connection that dies while SITTING IDLE is, by definition, idle — so it
    crosses the threshold and is still checked.

If the first of those ever stops being true, the skip becomes a bug, which is
why it is asserted against the real pool rather than taken on trust.
"""
import time

import pytest
from psycopg_pool import ConnectionPool


@pytest.fixture()
def checks(monkeypatch):
    """Count real check_connection calls (the round trip we are trying to avoid)."""
    from app import main as _main
    calls = []
    real = ConnectionPool.check_connection

    def spy(conn):
        calls.append(1)
        return real(conn)

    monkeypatch.setattr(ConnectionPool, "check_connection", staticmethod(spy))
    # Drain whatever is in the pool so each test starts from a known state.
    with _main.cursor() as c:
        c.execute("SELECT 1")
    calls.clear()
    return calls


def test_a_connection_used_moments_ago_is_not_re_checked(db, checks):
    """The case that made this worth changing: several cursor blocks in one
    request, all reusing the connection the previous block just returned."""
    from app.main import cursor
    for _ in range(5):
        with cursor() as c:
            c.execute("SELECT 1")
    assert checks == [], f"{len(checks)} needless round trip(s) across 5 checkouts"


def test_a_connection_that_has_been_sitting_is_checked(db, checks, monkeypatch):
    from app import main as _main
    monkeypatch.setattr(_main, "_POOL_CHECK_IDLE_S", 0.05)
    with _main.cursor() as c:
        c.execute("SELECT 1")
    checks.clear()
    time.sleep(0.06)
    with _main.cursor() as c:
        c.execute("SELECT 1")
    assert len(checks) == 1, "an idle connection went unverified"


def test_zero_restores_a_check_on_every_checkout(db, checks, monkeypatch):
    """The escape hatch: DB_POOL_CHECK_IDLE_SECONDS=0 is the old behaviour."""
    from app import main as _main
    monkeypatch.setattr(_main, "_POOL_CHECK_IDLE_S", 0.0)
    for _ in range(3):
        with _main.cursor() as c:
            c.execute("SELECT 1")
    assert len(checks) == 3


def test_a_brand_new_connection_is_not_checked_on_its_first_use(db, checks):
    """`configure` has just opened it; proving it alive costs a round trip and
    proves nothing."""
    from app import main as _main
    conn = _main._pool.getconn()
    try:
        _main._pool.putconn(conn)
    finally:
        pass
    checks.clear()
    with _main.cursor() as c:
        c.execute("SELECT 1")
    assert checks == []


# --------------------------------------------------------------------------- #
# The guarantee the skip depends on
# --------------------------------------------------------------------------- #
def test_a_connection_broken_in_use_never_comes_back_from_the_pool(db):
    """psycopg_pool's half of the bargain. Kill the backend mid-block; the next
    checkout must still get a working connection, WITHOUT the checkout check
    being what saves us."""
    from app import main as _main
    from psycopg.pq import TransactionStatus

    killed = None
    with pytest.raises(Exception):
        with _main.cursor() as c:
            c.execute("SELECT pg_backend_pid() AS pid")
            pid = c.fetchone()["pid"]
            killed = pid
            # Terminating our own backend breaks this connection underneath us.
            c.execute("SELECT pg_terminate_backend(%s)", (pid,))
            c.execute("SELECT 1")
    assert killed is not None

    # Never check on checkout, so ONLY the return-path discard can save this.
    _main._POOL_CHECK_IDLE_S, saved = float("inf"), _main._POOL_CHECK_IDLE_S
    try:
        with _main.cursor() as c:
            c.execute("SELECT pg_backend_pid() AS pid")
            assert c.fetchone()["pid"] != killed, \
                "the pool handed back the connection it had just broken"
            assert c.connection.pgconn.transaction_status != TransactionStatus.UNKNOWN
    finally:
        _main._POOL_CHECK_IDLE_S = saved
