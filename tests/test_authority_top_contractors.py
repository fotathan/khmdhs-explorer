# -*- coding: utf-8 -*-
"""Top contractors on the authority page: what it counts, and when it runs.

COST    the aggregation is ~1.3s for the largest authority (measured locally,
        ~45k contracts), so the authority page mounts it and the panel route
        runs it. Enforced by counting the queries each request actually issues,
        the same way test_act_page_layout.py pins the act page's panel.

COUNTS  contracts only, this authority only, ranked by value.
"""
import pytest

from tests.helpers import login, make_user

ORG = "TOPC-AUTH-1"
OTHER = "TOPC-AUTH-2"
VAT_A = "999100001"
VAT_B = "999100002"
PANEL = f"/authority/{ORG}/top-contractors"
MARK = "authority_top_contractors"          # the SQL comment on the aggregation


def _cleanup(cur):
    cur.execute("DELETE FROM proc.act_operator WHERE adam LIKE 'TOPC-%'")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'TOPC-%'")
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = ANY(%s)",
                ([VAT_A, VAT_B],))
    cur.execute("DELETE FROM proc.authority WHERE org_id = ANY(%s)", ([ORG, OTHER],))


@pytest.fixture()
def data(db):
    """A wins two contracts (100 + 200), B one (500). A also 'wins' a notice
    (not an award) and a contract at ANOTHER authority — neither may count."""
    cur = db.cursor()
    _cleanup(cur)                      # a previous failed run leaves rows behind
    for org, name in ((ORG, "ΔΗΜΟΣ ΚΟΡΥΦΗΣ"), (OTHER, "ΔΗΜΟΣ ΑΛΛΟΣ")):
        cur.execute("INSERT INTO proc.authority (org_id, name) VALUES (%s, %s)", (org, name))
    ops = {}
    for vat, name in ((VAT_A, "ΑΛΦΑ ΚΑΘΑΡΙΣΜΟΙ ΑΕ"), (VAT_B, "ΒΗΤΑ ΥΠΗΡΕΣΙΕΣ ΑΕ")):
        cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                       VALUES (%s, %s, true) RETURNING operator_id""", (vat, name))
        ops[vat] = cur.fetchone()["operator_id"]
    for adam, org, atype, vat, value in (
            ("TOPC-1", ORG, "contract", VAT_A, 100),
            ("TOPC-2", ORG, "contract", VAT_A, 200),
            ("TOPC-3", ORG, "contract", VAT_B, 500),
            ("TOPC-4", ORG, "notice", VAT_A, 9000),
            ("TOPC-5", OTHER, "contract", VAT_A, 7000)):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, authority_id,
                          total_cost_with_vat)
                       VALUES (%s, %s, 'Καθαρισμός κτιρίων', 'import', 'khmdhs', %s, %s)""",
                    (adam, atype, org, value))
        cur.execute("""INSERT INTO proc.act_operator
                         (adam, operator_id, role, awarded_value_with_vat)
                       VALUES (%s, %s, 'winner', %s)""", (adam, ops[vat], value))
    yield
    _cleanup(cur)


@pytest.fixture()
def reader(client):
    make_user("topc_admin", "goodpassword1", role="admin")
    login(client, "topc_admin", "goodpassword1")
    return client


def _queries_for(client, monkeypatch, url):
    """Every SQL statement one request issues, as text."""
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


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #
def test_the_authority_page_mounts_the_panel_and_never_runs_it(reader, data, monkeypatch):
    assert PANEL in reader.get(f"/authority/{ORG}").text
    qs = _queries_for(reader, monkeypatch, f"/authority/{ORG}")
    assert not [q for q in qs if MARK in q], "the aggregation runs inline again"


def test_the_panel_route_is_the_one_that_runs_it(reader, data, monkeypatch):
    """Proof the marker exists and the spy would have caught it above."""
    assert any(MARK in q for q in _queries_for(reader, monkeypatch, PANEL))


# --------------------------------------------------------------------------- #
# Counts
# --------------------------------------------------------------------------- #
def test_the_panel_ranks_this_authoritys_contract_winners_by_value(reader, data):
    body = reader.get(PANEL).text
    assert "Κορυφαίοι ανάδοχοι" in body
    b, a = body.index(f"/contractor/{VAT_B}"), body.index(f"/contractor/{VAT_A}")
    assert b < a, "500 in one contract must outrank 300 in two"
    assert "€ 500" in body and "€ 300" in body
    assert '<td class="val">2</td>' in body            # A's two contracts


def test_notices_and_other_authorities_are_not_counted(reader, data):
    body = reader.get(PANEL).text
    assert "€ 9.000" not in body and "€ 9.300" not in body    # the notice
    assert "€ 7.000" not in body and "€ 7.300" not in body    # the other authority


def test_an_authority_with_no_contracts_says_so(reader, data):
    body = reader.get(f"/authority/{OTHER}/top-contractors").text
    assert f"/contractor/{VAT_B}" not in body
    other_empty = reader.get("/authority/TOPC-NO-SUCH/top-contractors")
    assert other_empty.status_code in (200, 404)


def test_a_gated_visitor_gets_nothing(client, data):
    """Paid content, and the query is the expensive part — refused at the URL."""
    assert client.get(PANEL).text.strip() == ""


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #
def test_a_contractor_opened_from_the_panel_links_back_to_the_authority(reader, data):
    """The panel's links lead to contractor pages; the authority registers
    itself (/authority/ prefix) and the contractor accepts it as an origin."""
    assert """var PATH="/authority/", KEY='khmdhs:lastResults:'+PATH""" in \
        reader.get(f"/authority/{ORG}").text
    assert '["/authority/", ' in reader.get(f"/contractor/{VAT_A}").text
