# -*- coding: utf-8 -*-
"""The redesigned act and authority pages (docs/specs/public-detail-pages.md
slice 5, after Tender Service's two designs).

LOCKED     a gated reader sees the subscriber fields' LABELS as locked rows —
           procedure, contract type, criterion, place of performance, CPV —
           and never their values; nor the region in the breadcrumb.
SIDE CARD  a gated reader is offered registration; a subscriber gets the
           act's own actions there (document, favourite, calendar).
BLOCKS     related open tenders (same CPV division, never the act itself,
           never a closed one), the buyer card and glossary cards, for both.
SUBSCRIBER CPV codes with their official names and stored notes, linked
           to their public /cpv pages.
AUTHORITY  the hero carries the four figures; the FAQ and latest acts are
           built from data only.
"""
import pytest

from tests.helpers import grant, login, make_user

ADAM = "RDSN-ACT-1"
OTHER = "RDSN-ACT-2"       # open notice, same division → related
CLOSED = "RDSN-ACT-3"      # closed notice, same division → never related
AUTH = "RDSN-AUTH-1"
CPV = "97100000-3"
NOTE_EL = ("Η κατηγορία καλύπτει δοκιμαστικές υπηρεσίες επανασχεδιασμού σελίδων, "
           "όπως διάταξη, κάρτες και ετικέτες για δημόσιους διαγωνισμούς.")
NUTS = "ZZ8R"
NUTS_LABEL = "Περιοχή επανασχεδιασμού"


def _cleanup(cur):
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'RDSN-%'")
    cur.execute("DELETE FROM proc.authority WHERE org_id LIKE 'RDSN-%'")
    cur.execute("DELETE FROM proc.cpv_code WHERE cpv_code = %s", (CPV,))
    cur.execute("DELETE FROM proc.nuts_code WHERE nuts_code = %s", (NUTS,))


def _act(cur, adam, *, deadline_days, title):
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, authority_id,
                      total_cost_with_vat, submission_date, final_submission_date,
                      procedure_type_code, procedure_family, contract_type_code,
                      criteria_code, nuts_code)
                   VALUES (%s, 'notice', %s, 'import', 'khmdhs', %s, 50000,
                           now() - interval '3 days',
                           now() + make_interval(days => %s),
                           '1', 'Ανοιχτή διαδικασία', '13', '2', %s)""",
                (adam, title, AUTH, deadline_days, NUTS))
    cur.execute("INSERT INTO proc.act_object_detail (adam) VALUES (%s) RETURNING id", (adam,))
    od = cur.fetchone()["id"]
    cur.execute("INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code) "
                "VALUES (%s, %s)", (od, CPV))


@pytest.fixture()
def acts(db):
    cur = db.cursor()
    _cleanup(cur)
    cur.execute("INSERT INTO proc.nuts_code (nuts_code, label) VALUES (%s, %s)", (NUTS, NUTS_LABEL))
    cur.execute("INSERT INTO proc.cpv_code (cpv_code, description, description_en) "
                "VALUES (%s, 'Δοκιμαστικός επανασχεδιασμός', 'Test redesign')", (CPV,))
    cur.execute("""INSERT INTO proc.cpv_note (cpv_code, text_el, text_en, model,
                     prompt_version, input_hash) VALUES (%s, %s, 'en', 'm', 1, 'h')""",
                (CPV, NOTE_EL))
    cur.execute("""INSERT INTO proc.authority (org_id, name, contact_email)
                   VALUES (%s, 'ΔΗΜΟΣ ΕΠΑΝΑΣΧΕΔΙΑΣΜΟΥ', 'desk@rdsn.example')""", (AUTH,))
    _act(cur, ADAM, deadline_days=10, title="Διαγωνισμός επανασχεδιασμού")
    _act(cur, OTHER, deadline_days=20, title="Άλλος ανοιχτός διαγωνισμός")
    _act(cur, CLOSED, deadline_days=-2, title="Κλειστός διαγωνισμός")
    yield cur
    _cleanup(cur)


@pytest.fixture()
def reader(client):
    uid = make_user("rdsn_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "rdsn_cust", "goodpassword1")
    return client


# --------------------------------------------------------------------------- #
# Act page — gated
# --------------------------------------------------------------------------- #
def test_subscriber_fields_are_locked_for_a_gated_reader(client, acts):
    body = client.get(f"/act/{ADAM}").text
    for label in ("Διαδικασία", "Είδος σύμβασης", "Κριτήριο ανάθεσης", "Τόπος εκτέλεσης"):
        assert label in body, f"the {label} row is missing"
    assert body.count('class="dv-locked"') == 4
    for value in ("Ανοιχτή διαδικασία", "Προμήθειες", "Βάσει τιμής", NUTS_LABEL, CPV):
        assert value not in body, f"{value!r} leaked into the teaser"
    assert "Οι κωδικοί CPV της πράξης είναι ορατοί με εγγραφή." in body


def test_the_gated_side_card_offers_registration(client, acts):
    body = client.get(f"/act/{ADAM}").text
    assert 'class="td-cta" href="/register?next=' in body
    assert 'class="act-actions"' not in body
    assert "Δέχεται προσφορές" in body


def test_related_tenders_are_open_and_never_the_act_itself(client, acts):
    body = client.get(f"/act/{ADAM}").text
    rel = body[body.index("Σχετικοί ανοιχτοί διαγωνισμοί"):]
    rel = rel[: rel.index("</section>")]
    assert f"/act/{OTHER}" in rel
    assert f"/act/{ADAM}" not in rel and f"/act/{CLOSED}" not in rel


def test_the_buyer_and_glossary_cards_are_public(client, acts):
    body = client.get(f"/act/{ADAM}").text
    assert "Η αναθέτουσα αρχή" in body and f'href="/authority/{AUTH}"' in body
    assert 'href="/glossary/prokiryxi"' in body and 'href="/glossary/cpv"' in body
    assert 'href="/glossary/anoikti-diadikasia"' not in body   # the procedure is locked


# --------------------------------------------------------------------------- #
# Act page — subscriber
# --------------------------------------------------------------------------- #
def test_a_subscriber_sees_the_values_and_the_cpv_notes(reader, acts):
    body = reader.get(f"/act/{ADAM}").text
    assert 'class="dv-locked"' not in body
    assert "Ανοιχτή διαδικασία" in body and NUTS_LABEL in body
    assert f'href="/cpv/{CPV}"' in body and "Δοκιμαστικός επανασχεδιασμός" in body
    assert NOTE_EL in body
    assert 'href="/glossary/anoikti-diadikasia"' in body


def test_the_subscriber_side_card_holds_the_actions(reader, acts):
    body = reader.get(f"/act/{ADAM}").text
    side = body[body.index('class="td-side"'):]
    assert 'class="act-actions"' in side
    assert f"/act/{ADAM}/calendar.ics" in side
    assert "cpv=97&amp;" in side           # similar tenders: the act's division


# --------------------------------------------------------------------------- #
# Authority page
# --------------------------------------------------------------------------- #
def test_the_authority_page_has_the_design_blocks(client, acts):
    body = client.get(f"/authority/{AUTH}").text
    assert 'class="dv-hero"' in body and "Στοιχεία αναθέτουσας" in body
    assert "desk@rdsn.example" not in body and 'class="ap-blur"' in body
    assert "Πρόσφατες πράξεις" not in body or f"/act/{ADAM}" in body
