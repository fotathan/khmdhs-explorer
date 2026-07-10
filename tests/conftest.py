"""
Pytest fixtures for the KHMDHS app.

The test database is built from tests/proc_schema.sql — a schema-only snapshot of
prod's `proc` schema (no data). Point TEST_DATABASE_URL (or DATABASE_URL) at a
DEDICATED, throwaway Postgres database; the schema is dropped and rebuilt each
session. Requires a `psql` client on PATH and a server that has the pg_trgm +
unaccent contrib extensions (any Postgres 14+ works).
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # import app / migrate / worker from repo root

TEST_DB = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

# Configure the environment BEFORE anything imports app.main (its DB pool opens
# at import time).
os.environ.pop("RENDER", None)                       # never treat tests as prod
if TEST_DB:
    os.environ["DATABASE_URL"] = TEST_DB
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-0000000000")
os.environ.setdefault("RATELIMIT_ENABLED", "0")      # the rate-limit test opts in
os.environ.setdefault("ATTACHMENTS_ENABLED", "0")
os.environ.setdefault("TABLES_ENABLED", "0")
os.environ.setdefault("REGISTRATION_MODE", "open")
os.environ.setdefault("RUN_INLINE_WORKER", "0")      # no background worker in tests


def _build_schema():
    sql = (ROOT / "tests" / "proc_schema.sql").read_text()
    # Make the v17-generated snapshot loadable by any psql/server:
    sql = re.sub(r"^SET transaction_timeout = .*$\n", "", sql, flags=re.M)  # PG17-only GUC
    sql = re.sub(r"^\\(restrict|unrestrict)\b.*$\n", "", sql, flags=re.M)   # psql17 meta-cmds
    # The snapshot creates schema proc but carries no CREATE EXTENSION; the trgm
    # indexes need pg_trgm + unaccent present in proc first.
    sql = sql.replace(
        "CREATE SCHEMA proc;",
        "CREATE SCHEMA proc;\n"
        "CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA proc;\n"
        "CREATE EXTENSION IF NOT EXISTS unaccent SCHEMA proc;\n",
        1,
    )
    from tests.helpers import connect  # noqa: E402
    with connect() as c:
        c.execute("DROP SCHEMA IF EXISTS proc CASCADE")
    r = subprocess.run(["psql", TEST_DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"],
                       input=sql, text=True, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("schema build failed:\n" + r.stderr[-3000:])


@pytest.fixture(scope="session")
def _schema():
    if not TEST_DB:
        pytest.skip("set TEST_DATABASE_URL (or DATABASE_URL) to a throwaway DB to run DB tests")
    _build_schema()
    from tests.helpers import connect
    with connect() as c:      # seed the static product catalogue (snapshot has no data)
        c.execute("INSERT INTO proc.product (code, name, default_period_days) "
                  "VALUES ('pro', 'Pro', 365) ON CONFLICT (code) DO NOTHING")
    yield


@pytest.fixture(scope="session", autouse=True)
def _close_pool_at_end():
    """Close app.main's DB pool at session end so its background thread doesn't
    trigger a noisy PythonFinalizationError at interpreter shutdown."""
    yield
    import sys
    m = sys.modules.get("app.main")
    if m is not None:
        try:
            m._pool.close()
        except Exception:
            pass


@pytest.fixture()
def _clean(_schema):
    """Truncate the mutable tables so each test starts from a known state."""
    from tests.helpers import connect
    with connect() as c:
        c.execute("TRUNCATE proc.app_user CASCADE")       # cascades to subs/profile/etc.
        c.execute("TRUNCATE proc.login_throttle")
        c.execute("TRUNCATE proc.admin_action CASCADE")
    yield


@pytest.fixture()
def db(_clean):
    from tests.helpers import connect
    conn = connect()
    yield conn
    conn.close()


@pytest.fixture()
def client(_clean):
    # Import here (not at module top) so a missing DB doesn't blow up collection
    # of the pure-unit tests. No `with` — we don't want the lifespan to close the
    # module-global pool between tests.
    from fastapi.testclient import TestClient
    from app.main import app
    # https base_url so the Secure session cookie (SESSION_SECURE defaults on when
    # SECRET_KEY is set) is actually sent back on subsequent requests.
    return TestClient(app, base_url="https://testserver")
