"""Manual act create/edit form: the former free-text label fields now render as
dropdowns constrained to curated option lists (and the authority-activity field
pulls its options from proc.code_list)."""
from tests.helpers import get_csrf, login, make_user

SELECT_FIELDS = [
    "procedure_label", "subtype_of_document", "regulation_of_procurement",
    "type_of_bid_required", "source_status", "e_auction",
    "contracting_authority_activity_code",
]


def _open_new_act_form(client):
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")
    return client.get("/admin/acts/new", follow_redirects=False)


def test_label_fields_render_as_selects(client):
    r = _open_new_act_form(client)
    assert r.status_code == 200
    html = r.text
    for name in SELECT_FIELDS:
        assert f'<select name="{name}">' in html, f"{name} should be a dropdown"


def test_curated_options_present(client):
    html = _open_new_act_form(client).text
    # representative options from each list
    assert "Ανοιχτή διαδικασία" in html          # procedure
    assert "Εθνική Προκήρυξη" in html            # subtype
    assert "Ευρωπαϊκή Ένωση" in html             # regulation
    assert "Υποβολή για όλα τα τμήματα" in html  # bid type
    assert ">Ναι<" in html                        # e_auction yes


def test_authority_activity_options_come_from_code_list(client):
    # authority_activity is seeded into proc.code_list (test snapshot); its Greek
    # labels should populate the dropdown rather than a hardcoded list.
    html = _open_new_act_form(client).text
    assert "Γενικές δημόσιες υπηρεσίες" in html or "Άμυνα" in html


def test_scan_emits_snapped_select_suggestion(client):
    """The deterministic scanner matches a procedure phrase in the text and
    suggests the exact dropdown option value (so accepting it snaps the select)."""
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")
    tok = get_csrf(client)
    text = "Ο διαγωνισμός διενεργείται με ΑΝΟΙΧΤΗ ΔΙΑΔΙΚΑΣΙΑ και κριτήριο την τιμή."
    r = client.post("/admin/acts/extract", data={"text": text},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    proc = [s for s in r.json()["suggestions"]
            if s.get("kind") == "select" and s["target"] == "procedure_label"]
    assert proc and proc[0]["value"] == "Ανοιχτή διαδικασία"
