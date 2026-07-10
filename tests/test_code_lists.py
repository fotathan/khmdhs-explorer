"""Official code lists resolve coded fields to text on the act page."""
from tests.helpers import login, make_user


def test_code_label_filter_resolves(client):
    import app.main as m
    # code_list is seeded by conftest (the official-docs seed migration)
    assert "ν.4412" in m._code_label_impl("4", "legal_context")
    assert m._code_label_impl("3", "digital_platform") == "ΕΣΗΔΗΣ Π&Υ"
    assert m._code_label_impl("2", "notice_type") == "Προκήρυξη"
    assert m._code_label_impl("999", "legal_context") == "999"   # unknown → raw code
    assert m._code_label_impl(None, "legal_context") == "—"


def test_act_page_shows_resolved_codes(client, db):
    adam = "TEST-CODES-0001"
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source,
                      legal_context_code, digital_platform_code,
                      contracting_authority_activity_code)
                   VALUES (%s,'notice','Δοκιμή','import','khmdhs','4','3','6')""",
                (adam,))
    make_user("actadmin", "goodpassword1", role="admin")
    login(client, "actadmin", "goodpassword1")
    r = client.get(f"/act/{adam}", follow_redirects=False)
    assert r.status_code == 200
    assert "ΕΣΗΔΗΣ Π" in r.text          # digital_platform 3 → label (& is html-escaped)
    assert "ν.4412" in r.text            # legal_context 4 → label
    assert "Υγεία" in r.text             # authority_activity 6 → label
    # the raw codes must not be shown as the values
    assert "<dd>3</dd>" not in r.text
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
