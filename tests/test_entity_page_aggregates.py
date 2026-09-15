# -*- coding: utf-8 -*-
"""The authority and contractor pages' aggregates: correct, and cheap.

VALUE   a CPV division's value counts each act (contractor: each award) ONCE,
        however many of its line items fall in that division. It was summed
        over the line-item join, which inflated a busy division up to 6× for
        the largest authority.

COST    no aggregate on either page calls proc.resolved_value() per row — that
        lookup was ~720ms of the authority totals for 122k acts. A manual
        correction still applies, through one join.

DEFER   the authority's CPV panel (~0.9s for the largest authority) is mounted,
        not run inline — counted from the queries the page actually issues.
"""
import re

import pytest

from tests.helpers import login, make_user

ORG = "AGG-AUTH-1"
VAT = "999200001"
CPV_PANEL = f"/authority/{ORG}/top-cpv"
MARK = "authority_top_cpv"

CODES = {"15800000-6": "Διάφορα προϊόντα διατροφής",
         "15100000-9": "Ζωικά προϊόντα",
         "33100000-1": "Ιατρικές συσκευές",
         "45200000-9": "Εργασίες κατασκευής"}


def _cleanup(cur):
    cur.execute("DELETE FROM proc.act_annotation WHERE adam LIKE 'AGG-%'")
    cur.execute("""DELETE FROM proc.object_detail_cpv WHERE object_detail_id IN
                   (SELECT id FROM proc.act_object_detail WHERE adam LIKE 'AGG-%')""")
    cur.execute("DELETE FROM proc.act_object_detail WHERE adam LIKE 'AGG-%'")
    cur.execute("DELETE FROM proc.act_operator WHERE adam LIKE 'AGG-%'")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'AGG-%'")
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = %s", (VAT,))
    cur.execute("DELETE FROM proc.authority WHERE org_id = %s", (ORG,))


def _act(cur, adam, atype, value, cpvs):
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, authority_id,
                      total_cost_with_vat)
                   VALUES (%s, %s, 'Δοκιμή', 'import', 'khmdhs', %s, %s)""",
                (adam, atype, ORG, value))
    for code in cpvs:                                  # one line item per code
        cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                       VALUES (%s, 'είδος') RETURNING id""", (adam,))
        cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                       VALUES (%s, %s)""", (cur.fetchone()["id"], code))


@pytest.fixture()
def data(db):
    cur = db.cursor()
    _cleanup(cur)
    for code, desc in CODES.items():
        cur.execute("""INSERT INTO proc.cpv_code (cpv_code, description) VALUES (%s, %s)
                       ON CONFLICT (cpv_code) DO NOTHING""", (code, desc))
    cur.execute("INSERT INTO proc.authority (org_id, name) VALUES (%s, 'ΔΗΜΟΣ ΑΘΡΟΙΣΜΑΤΩΝ')", (ORG,))
    cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                   VALUES (%s, 'ΓΑΜΜΑ ΚΑΤΑΣΚΕΥΑΣΤΙΚΗ ΑΕ', true) RETURNING operator_id""", (VAT,))
    op = cur.fetchone()["operator_id"]

    # N1: three line items, all in division 15 — counts once (1000, not 3000).
    _act(cur, "AGG-N1", "notice", 1000, ["15800000-6"] * 3)
    # N2: one item in 15, one in 33 — counts in both divisions.
    _act(cur, "AGG-N2", "notice", 400, ["15100000-9", "33100000-1"])
    # C1: two items in division 45, contractor awarded 2500 — once, not twice.
    _act(cur, "AGG-C1", "contract", 5000, ["45200000-9", "45200000-9"])
    cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role, awarded_value_with_vat)
                   VALUES ('AGG-C1', %s, 'winner', 2500)""", (op,))
    # C2: source value 800, manually corrected to 900, no awarded value.
    _act(cur, "AGG-C2", "contract", 800, ["45200000-9"])
    cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                   VALUES ('AGG-C2', %s, 'winner')""", (op,))
    cur.execute("""INSERT INTO proc.act_annotation (adam, corrected_value, author)
                   VALUES ('AGG-C2', 900, 'test')""")
    yield
    _cleanup(cur)


@pytest.fixture()
def reader(client):
    make_user("agg_admin", "goodpassword1", role="admin")
    login(client, "agg_admin", "goodpassword1")
    return client


def _queries_for(client, monkeypatch, url):
    import psycopg
    seen = []
    real = psycopg.Cursor.execute

    def spy(self, query, params=None, **kw):
        seen.append(str(query))
        return real(self, query, params, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", spy)
    client.get(url)
    monkeypatch.undo()
    return seen


def _division(body, division):
    """(count, value) of one CPV division row, as rendered."""
    m = re.search(r'<code class="adam">%s</code>.*?<td class="val">(\d+)</td>\s*'
                  r'<td class="val">€ ([\d.]+)</td>' % division, body, re.S)
    assert m, f"division {division} not rendered"
    return int(m.group(1)), m.group(2)


# --------------------------------------------------------------------------- #
# Value: once per act per division
# --------------------------------------------------------------------------- #
def test_an_authority_notice_counts_once_per_division(reader, data):
    body = reader.get(CPV_PANEL).text
    assert _division(body, "15") == (2, "1.400")     # 1000 once + 400; was 3.400
    assert _division(body, "33") == (1, "400")       # N2 appears in both divisions


def test_a_contractor_award_counts_once_per_division(reader, data):
    body = reader.get(f"/contractor/{VAT}").text
    assert _division(body, "45") == (2, "3.400")     # 2500 once + corrected 900; was 5.900


# --------------------------------------------------------------------------- #
# Corrections still apply, without a lookup per row
# --------------------------------------------------------------------------- #
def test_totals_apply_a_manual_correction(reader, data):
    # authority contracts: 5000 + 900 (corrected from 800)
    assert "€ 5.900" in reader.get(f"/authority/{ORG}").text
    # contractor contracts: awarded 2500 + corrected 900
    assert "€ 3.400" in reader.get(f"/contractor/{VAT}").text


@pytest.mark.parametrize("url", [f"/authority/{ORG}", f"/contractor/{VAT}"])
def test_no_page_aggregate_calls_resolved_value_per_row(reader, data, monkeypatch, url):
    grouped = [q for q in _queries_for(reader, monkeypatch, url)
               if "GROUP BY" in q and "resolved_value(" in q]
    assert not grouped, f"an aggregate still resolves values row by row: {grouped}"


# --------------------------------------------------------------------------- #
# The authority's CPV panel is deferred
# --------------------------------------------------------------------------- #
def test_the_authority_page_mounts_the_cpv_panel_and_never_runs_it(reader, data, monkeypatch):
    assert CPV_PANEL in reader.get(f"/authority/{ORG}").text
    assert not [q for q in _queries_for(reader, monkeypatch, f"/authority/{ORG}") if MARK in q]


def test_the_cpv_panel_route_is_the_one_that_runs_it(reader, data, monkeypatch):
    assert any(MARK in q for q in _queries_for(reader, monkeypatch, CPV_PANEL))


def test_a_gated_visitor_gets_no_cpv_panel(client, data):
    assert client.get(CPV_PANEL).text.strip() == ""
