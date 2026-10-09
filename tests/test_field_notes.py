"""Fixed explanations next to an act's coded fields (app/field_notes.py,
docs/specs/public-detail-pages.md slice 1).

What is pinned here, and why each one would fail silently otherwise:

  COVERAGE  every act type, contract type, criterion code, measured contract
            criterion label and real procedure family has a text. A new code
            with no text just shows nothing — nobody would notice.
  FAMILY    procedure notes key on proc.compute_procedure_family's OUTPUT, so
            the raw spellings measured in the data are run through the real
            SQL function. A renamed family would orphan its text.
  LANGUAGE  both languages, and no Greek letter in an English text.
  LINKS     a glossary link names a term that exists (a 404 on a public page).
  TEASER    a gated visitor gets the act-type note (its badge is public) and
            NO procedure / contract-type / criterion note (those fields are
            subscriber-only — owner, 2026-10-09). A subscriber gets all four.
"""
import re

import pytest

from app import field_notes as fn
from app import glossary
from tests.helpers import grant, login, make_user

GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")

# Every raw procedure_type_code measured locally on 2026-10-09 (notices hold a
# code, awards/contracts a label), except codes 9/16/17/19, which have no known
# meaning and fold into «Άλλο / Άγνωστο».
MEASURED_PROCEDURES = [
    "1", "2", "4", "6", "7", "11", "12", "13", "18",
    "Απευθείας ανάθεση (αρ.118/αρ. 328)", "Απευθείας ανάθεση",
    "Ανοιχτή διαδικασία (αρ.27/αρ.264)", "Ανοιχτή διαδικασία",
    "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση (αρ.32/αρ.269)",
    "Διαδικασία άρθρου 128 του ν.4412/16",
    "Συνοπτικός διαγωνισμός (αρ.117/αρ. 327)",
    "Διαπραγμάτευση με προηγούμενη προκήρυξη διαγωνισμού (αρ.266/Βιβλίο ΙΙ)",
    "Απευθείας Ανάθεση - (Covid 19)",
    "Διαπραγμάτευση με προηγούμενη προκήρυξη διαγωνισμού (αρ.266 του ν. 4412/2016)",
    "Κλειστή διαδικασία (αρ.28/αρ.265)",
    "Ανταγωνιστική διαδικασία με διαπραγμάτευση (αρ. 26.2.α-β/Βιβλίο Ι)",
    "Ανταγωνιστικός διάλογος", "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση",
    "Διαδικασία κάτω των ορίων εκτός ν. 4412/2016", "Κλειστή διαδικασία",
    "Ανταγωνιστική διαδικασία με διαπραγμάτευση",
    "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση - (Covid 19)",
    "Σύμπραξη καινοτομίας", "Σύμπραξη καινοτομίας (αρ.31/αρ.268)",
]

# Every assign_criteria_label measured on contracts, 2026-10-09.
MEASURED_CONTRACT_CRITERIA = [
    "Βάσει τιμής", "Βάσει κόστους – βέλτιστη σχέση ποιότητας – τιμής",
    "Βάσει κόστους – άλλο", "Βάσει κόστους – κοστολόγηση κύκλου ζωής",
]


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #
def test_every_act_type_has_a_note():
    from app.main import TYPE_LABELS
    assert set(TYPE_LABELS) == set(fn.ACT_TYPES)


def test_every_contract_type_has_a_note():
    from app.main import CONTRACT_TYPES
    assert set(CONTRACT_TYPES) == set(fn.CONTRACT_TYPES)


def test_every_criterion_code_has_a_note():
    from app.main import ASSIGN_CRITERIA
    for code in ASSIGN_CRITERIA:
        assert fn.note("criteria", code), code


@pytest.mark.parametrize("label", MEASURED_CONTRACT_CRITERIA)
def test_every_measured_contract_criterion_label_has_a_note(label):
    assert fn.note("criteria", label), label


def test_criterion_labels_match_whatever_the_dash_case_or_accents():
    assert (fn.note("criteria", "ΒΑΣΕΙ ΚΟΣΤΟΥΣ - ΒΕΛΤΙΣΤΗ ΣΧΕΣΗ ΠΟΙΟΤΗΤΑΣ — ΤΙΜΗΣ")
            == fn.note("criteria", "1"))


def test_a_contract_type_code_is_never_read_as_a_criterion():
    """On contracts assign_criteria_code holds the CONTRACT TYPE (13, 9, …),
    so only codes 1-4 may resolve as a criterion."""
    for code in ("9", "10", "12", "13", "14"):
        assert fn.note("criteria", code) is None


def test_measured_procedures_all_land_on_a_family_with_a_note(db):
    cur = db.cursor()
    for raw in MEASURED_PROCEDURES:
        cur.execute("SELECT proc.compute_procedure_family(%s) AS f", (raw,))
        family = cur.fetchone()["f"]
        assert fn.note("procedure", family), f"{raw!r} → {family!r} has no note"


def test_every_procedure_note_is_a_family_the_function_returns(db):
    cur = db.cursor()
    produced = set()
    for raw in MEASURED_PROCEDURES:
        cur.execute("SELECT proc.compute_procedure_family(%s) AS f", (raw,))
        produced.add(cur.fetchone()["f"])
    assert set(fn.PROCEDURES) <= produced, set(fn.PROCEDURES) - produced


def test_unknown_values_get_no_note():
    assert fn.note("procedure", "Άλλο / Άγνωστο") is None
    assert fn.note("procedure", None) is None
    assert fn.note("contract_type", "") is None
    assert fn.note("act_type", "nonsense") is None
    with pytest.raises(ValueError):
        fn.note("no-such-kind", "1")


# --------------------------------------------------------------------------- #
# Language + links
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind,key,entry", fn.all_entries())
def test_both_languages_are_written(kind, key, entry):
    assert GREEK.search(entry["el"]), f"{kind}/{key}: Greek text is not Greek"
    assert not GREEK.search(entry["en"]), f"{kind}/{key}: Greek letters in English"
    assert len(entry["el"]) > 40 and len(entry["en"]) > 40


@pytest.mark.parametrize("kind,key,entry", fn.all_entries())
def test_glossary_links_point_at_real_terms(kind, key, entry):
    if entry.get("glossary"):
        assert glossary.get(entry["glossary"]), entry["glossary"]


def test_the_language_is_honoured():
    el = fn.note("act_type", "notice", "el")
    en = fn.note("act_type", "notice", "en")
    assert el["text"] != en["text"] and el["href"] == en["href"] == "/glossary/prokiryxi"


# --------------------------------------------------------------------------- #
# The act page
# --------------------------------------------------------------------------- #
ADAM = "TEST-FNOTE-0001"


@pytest.fixture()
def act(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (ADAM,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, total_cost_with_vat,
                      submission_date, final_submission_date,
                      procedure_type_code, procedure_family,
                      contract_type_code, criteria_code)
                   VALUES (%s, 'notice', 'Προμήθεια σημειώσεων', 'import', 'khmdhs',
                           12000, now(), now() + interval '5 days',
                           '1', 'Ανοιχτή διαδικασία', '13', '2')""", (ADAM,))
    yield ADAM
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (ADAM,))


@pytest.fixture()
def reader(client):
    uid = make_user("fnote_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "fnote_cust", "goodpassword1")
    return client


def _texts(lang="el"):
    return {k: fn.note(*args, lang)["text"] for k, args in {
        "type": ("act_type", "notice"),
        "procedure": ("procedure", "Ανοιχτή διαδικασία"),
        "contract": ("contract_type", "13"),
        "criteria": ("criteria", "2"),
    }.items()}


def test_a_gated_visitor_gets_the_type_note_and_nothing_else(client, act):
    body = client.get(f"/act/{act}").text
    texts = _texts()
    assert 'class="td-explain type-note"' in body and texts["type"] in body
    assert 'href="/glossary/prokiryxi"' in body
    for k in ("procedure", "contract", "criteria"):
        assert texts[k] not in body, f"the {k} note leaked into the teaser"
    assert 'class="fnote"' not in body


def test_a_subscriber_gets_every_note(reader, act):
    body = reader.get(f"/act/{act}").text
    for k, text in _texts().items():
        assert text in body, f"the {k} note is missing"
    assert body.count('class="fnote"') == 3


def test_the_notes_follow_the_reader_language(reader, act):
    reader.cookies.set("lang", "en")
    body = reader.get(f"/act/{act}").text
    for k, text in _texts("en").items():
        assert text in body, f"the English {k} note is missing"
    assert "Glossary ›" in body
