# -*- coding: utf-8 -*-
"""/analytics CPV table (proc.mv_analytics_cpv): every euro counted once.

migrations/20261003090000_analytics_cpv_count_once.sql:
  * an item line with several codes of one division counts once there;
  * a notice replaced by a later, not cancelled amendment does not count
    (a chain counts only its last version; a self-reference is no amendment);
  * the source allowlist is proc.analytics_sources(): khmdhs + manual, and
    Tender Service only where the database switches it on.
"""
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = "migrations/20261003090000_analytics_cpv_count_once.sql"
CODES = ("97000000-0", "97100000-1", "97200000-2")   # a division no real code uses
P = "CPVX-"


@pytest.fixture(scope="module")
def _migrated(_schema):
    r = subprocess.run(["psql", os.environ["DATABASE_URL"], "-v", "ON_ERROR_STOP=1", "-q",
                        "-f", str(ROOT / MIGRATION)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{MIGRATION} failed:\n{r.stderr[-2000:]}")


def _cleanup(cur):
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE %s", (P + "%",))
    cur.execute("DELETE FROM proc.cpv_code WHERE cpv_code = ANY(%s)", (list(CODES),))


def _act(cur, adam, cost, codes, *, atype="notice", amends=None, cancelled=False, source="khmdhs"):
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, amended_adam, cancelled, total_cost_with_vat)
                   VALUES (%s, %s::proc.act_type, 'Δοκιμή CPV', 'import', %s, %s, %s, %s)""",
                (P + adam, atype, source, P + amends if amends else None, cancelled, cost))
    cur.execute("INSERT INTO proc.act_object_detail (adam, line_no, cost_without_vat) "
                "VALUES (%s, 1, %s) RETURNING id", (P + adam, cost))
    od = cur.fetchone()["id"]
    for c in codes:
        cur.execute("INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code) VALUES (%s, %s)",
                    (od, c))


@pytest.fixture()
def division(db, _migrated):
    cur = db.cursor()
    _cleanup(cur)
    for c in CODES:
        cur.execute("INSERT INTO proc.cpv_code (cpv_code, description) VALUES (%s, 'Δοκιμαστικός')", (c,))
    _act(cur, "N1", 1000, CODES)                      # three codes, one division: once
    _act(cur, "N3", 700, CODES[:1])                    # amended by N4 → out
    _act(cur, "N4", 800, CODES[:1], amends="N3")       # amended by N8 → out too
    _act(cur, "N8", 900, CODES[:1], amends="N4")       # the chain's last version
    _act(cur, "N5", 100, CODES[:1], amends="N5")       # self-reference: not an amendment
    _act(cur, "N6", 50, CODES[:1])                     # its amendment is cancelled → stays
    _act(cur, "N7", 60, CODES[:1], amends="N6", cancelled=True)
    _act(cur, "C1", 300, CODES[:2], atype="contract")  # contracts: once per line too
    _act(cur, "T1", 9999, CODES[:1], source="tsg")     # not counted where the switch is off

    def read():
        cur.execute("REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv")
        cur.execute("""SELECT contract_count, contract_value, notice_count, notice_value
                       FROM proc.mv_analytics_cpv WHERE division = '97'""")
        return tuple(cur.fetchone().values())
    yield cur, read
    _cleanup(cur)
    cur.execute("REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv WITH NO DATA")


def test_each_line_and_each_procedure_counts_once(division):
    _, read = division
    # notices: N1 1000 (not 3000) + N8 900 + N5 100 + N6 50; contracts: C1 300 (not 600)
    assert read() == (1, 300, 4, 2050)


def test_tender_service_counts_only_where_the_database_switches_it_on(division):
    cur, read = division
    cur.execute("SET khmdhs.single_source = 'on'")
    try:
        assert read() == (1, 300, 5, 2050 + 9999)
    finally:
        cur.execute("RESET khmdhs.single_source")
    assert read() == (1, 300, 4, 2050)


def test_the_migration_has_no_do_blocks():
    sql = "\n".join(line for line in (ROOT / MIGRATION).read_text(encoding="utf-8").splitlines()
                    if not line.lstrip().startswith("--"))
    assert "DO $$" not in sql and "ALTER DATABASE" not in sql
