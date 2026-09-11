"""Where the act detail page puts its panels.

The order of the main column is not decoration: the full text is the document
the reader came for, so it must come BEFORE the comparative panels that sit
around it (top contractors in these CPVs, the extracted tables). This file
pins that order against the rendered page, because a template edit anywhere
in the 500-line column can silently move a panel to the bottom.
"""
import pytest

from tests.helpers import connect, grant, login, make_user

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


def _cleanup(cur):
    cur.execute("DELETE FROM proc.act_operator WHERE adam = ANY(%s)", ([ADAM, PEER],))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)", ([ADAM, PEER],))
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = %s", (VAT,))


@pytest.fixture()
def act(db):
    """A notice with full text whose CPV another contract has already been won on,
    so BOTH panels render and their order is observable."""
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


def test_the_full_text_comes_before_the_top_contractors_panel(reader, act):
    body = reader.get(f"/act/{act}").text
    assert FULLTEXT_H in body, "the full-text panel did not render"
    assert TOPCPV_H in body, "the top-contractors panel did not render"
    assert body.index(FULLTEXT_H) < body.index(TOPCPV_H)
