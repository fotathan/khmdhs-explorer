"""What the act detail page renders itself, and what it defers.

Two rules, both about the main column:

  ORDER   the full text is the document the reader came for, so it comes
          BEFORE the comparative panels that sit around it. A template edit
          anywhere in that 500-line column can silently move a panel to the
          bottom, so the order is pinned against the rendered page.

  COST    "top contractors in these CPV codes" is the page's one expensive
          query — measured at ~1-2.7s on a common CPV code against ~40ms for
          every other query on the page combined. It must not run inline. The
          page mounts it over HTMX and the panel arrives on its own.

The cost rule is enforced by counting the queries the page actually issues, not
by reading the route: the point is that the aggregation stays off the critical
path after someone edits act_detail.
"""
import pytest

from tests.helpers import grant, login, make_user

ADAM = "TEST-LAYOUT-0001"
PEER = "TEST-LAYOUT-0002"          # an awarded contract on the same CPV
CPV = "33184100"
VAT = "999000111"

FULL_TEXT = (
    "ΔΙΑΚΗΡΥΞΗ ΑΝΟΙΚΤΟΥ ΔΙΑΓΩΝΙΣΜΟΥ\n"
    "Αντικείμενο του διαγωνισμού είναι η προμήθεια ιατροτεχνολογικού εξοπλισμού.\n"
)

FULLTEXT_H = "Πλήρες κείμενο"
TOPCPV_H = "Κορυφαίοι ανάδοχοι σε αυτούς τους κωδικούς CPV"
TOPCPV_URL = f"/act/{ADAM}/top-contractors"


def _cleanup(cur):
    cur.execute("DELETE FROM proc.act_operator WHERE adam = ANY(%s)", ([ADAM, PEER],))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)", ([ADAM, PEER],))
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = %s", (VAT,))


@pytest.fixture()
def act(db):
    """A notice with full text whose CPV another contract has already been won
    on, so the deferred panel has something to say."""
    cur = db.cursor()
    _cleanup(cur)                      # a previous failed run leaves rows behind
    cur.execute("""INSERT INTO proc.cpv_code (cpv_code, description)
                   VALUES (%s, 'Χειρουργικά εμφυτεύματα')
                   ON CONFLICT (cpv_code) DO NOTHING""", (CPV,))
    cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                   VALUES (%s, 'ΑΝΑΔΟΧΟΣ ΔΟΚΙΜΗΣ ΑΕ', true)
                   RETURNING operator_id""", (VAT,))
    op = cur.fetchone()["operator_id"]

    for adam, atype, text in ((ADAM, "notice", FULL_TEXT), (PEER, "contract", None)):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, full_text,
                          total_cost_with_vat)
                       VALUES (%s, %s, 'Προμήθεια εξοπλισμού', 'import', 'khmdhs',
                               %s, 50000)""", (adam, atype, text))
        cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                       VALUES (%s, 'είδος') RETURNING id""", (adam,))
        od = cur.fetchone()["id"]
        cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                       VALUES (%s, %s)""", (od, CPV))
    cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                   VALUES (%s, %s, 'winner')""", (PEER, op))
    yield ADAM
    _cleanup(cur)


@pytest.fixture()
def reader(client):
    """An entitled customer: the gated teaser shows no panels at all."""
    uid = make_user("layout_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "layout_cust", "goodpassword1")
    return client


# --------------------------------------------------------------------------- #
# Order
# --------------------------------------------------------------------------- #
def test_the_full_text_comes_before_the_top_contractors_panel(reader, act):
    body = reader.get(f"/act/{act}").text
    assert FULLTEXT_H in body, "the full-text panel did not render"
    assert TOPCPV_URL in body, "the top-contractors panel is not mounted"
    assert body.index(FULLTEXT_H) < body.index(TOPCPV_URL)


def test_the_deferred_panel_lands_above_the_extracted_tables(reader, act, monkeypatch):
    """The mount sits where the panel used to, so nothing shifts once it
    arrives. The tables panel is itself deferred and flag-gated, so the flag is
    turned on here to put the two mounts on the same page."""
    from app.main import templates
    monkeypatch.setitem(templates.env.globals, "tables_enabled", True)
    body = reader.get(f"/act/{act}").text
    assert body.index(TOPCPV_URL) < body.index("tt-pub-tables-mount")


# --------------------------------------------------------------------------- #
# Cost: the aggregation is not on the page's critical path
# --------------------------------------------------------------------------- #
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


def test_the_act_page_never_runs_the_contractor_aggregation(reader, act, monkeypatch):
    qs = _queries_for(reader, monkeypatch, f"/act/{act}")
    offenders = [q for q in qs if "ao.role = 'winner'" in q and "count(DISTINCT" in q]
    assert not offenders, f"the page still runs the aggregation inline: {offenders}"


def test_the_panel_route_is_the_one_that_runs_it(reader, act, monkeypatch):
    """The other half of the assertion above — proof the query still exists and
    the spy would have caught it."""
    qs = _queries_for(reader, monkeypatch, TOPCPV_URL)
    assert any("ao.role = 'winner'" in q and "count(DISTINCT" in q for q in qs)


# --------------------------------------------------------------------------- #
# The panel itself
# --------------------------------------------------------------------------- #
def test_the_panel_renders_the_ranking(reader, act):
    body = reader.get(TOPCPV_URL).text
    assert TOPCPV_H in body
    assert "ΑΝΑΔΟΧΟΣ ΔΟΚΙΜΗΣ ΑΕ" in body
    assert f"/contractor/{VAT}" in body


def test_an_act_with_no_cpv_history_leaves_nothing_behind(db, reader, act):
    """outerHTML swap: an empty answer must be genuinely empty, not a heading
    with an empty table under it."""
    db.cursor().execute("DELETE FROM proc.act_operator WHERE adam = %s", (PEER,))
    assert reader.get(TOPCPV_URL).text.strip() == ""


def test_the_current_act_never_ranks_its_own_winner(db, reader, act):
    """An already-awarded tender listing itself would be a tautology."""
    cur = db.cursor()
    cur.execute("SELECT operator_id FROM proc.economic_operator WHERE vat_number=%s",
                (VAT,))
    op = cur.fetchone()["operator_id"]
    cur.execute("DELETE FROM proc.act_operator WHERE adam = %s", (PEER,))
    cur.execute("UPDATE proc.procurement_act SET type='contract' WHERE adam=%s", (ADAM,))
    cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                   VALUES (%s, %s, 'winner')""", (ADAM, op))
    assert reader.get(TOPCPV_URL).text.strip() == ""


def test_the_panel_is_paid_content(client, act):
    """The page does not mount it for a gated visitor, but the URL is guessable
    — and answering it is exactly what costs a second of database time."""
    assert client.get(TOPCPV_URL).text.strip() == ""
    assert TOPCPV_URL not in client.get(f"/act/{act}").text
