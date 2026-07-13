"""Multi-value authorities/contractors on the manual act form: repeated rows
save into proc.act_authority / proc.act_contractor and reload on edit."""
import re

from tests.helpers import connect, get_csrf, login, make_user


def _admin(client):
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")


def _rows(table, adam):
    with connect() as c:
        cur = c.cursor()
        cur.execute(f"SELECT * FROM proc.{table} WHERE adam=%s ORDER BY ord", (adam,))
        return cur.fetchall()


def test_multiple_authorities_and_contractor_round_trip(client):
    _admin(client)
    tok = get_csrf(client)
    data = {
        "_mode": "create", "type": "notice", "title": "Δοκιμαστική πράξη",
        # two authorities
        "authority_0_name": "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ", "authority_0_afm": "090123456",
        "authority_0_city": "Αθήνα",
        "authority_1_name": "ΠΕΡΙΦΕΡΕΙΑ ΑΤΤΙΚΗΣ", "authority_1_afm": "090999888",
        # one contractor with an award amount (Greek number format)
        "contractor_0_name": "ΑΝΑΔΟΧΟΣ ΑΕ", "contractor_0_afm": "099111222",
        "contractor_0_award_amount": "12.345,67",
        "contractor_0_award_vat_included": "1",
        # an empty extra contractor row must be dropped
        "contractor_1_name": "", "contractor_1_afm": "",
    }
    r = client.post("/admin/acts/save", data=data,
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 303
    adam = re.search(r"/admin/act/([^/]+)/edit", r.headers["location"]).group(1)

    auths = _rows("act_authority", adam)
    assert [a["name"] for a in auths] == ["ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ", "ΠΕΡΙΦΕΡΕΙΑ ΑΤΤΙΚΗΣ"]
    assert [a["ord"] for a in auths] == [0, 1]
    assert auths[0]["city"] == "Αθήνα"

    contr = _rows("act_contractor", adam)
    assert len(contr) == 1                       # the empty row was dropped
    assert contr[0]["name"] == "ΑΝΑΔΟΧΟΣ ΑΕ"
    assert float(contr[0]["award_amount"]) == 12345.67
    assert contr[0]["award_vat_included"] is True

    # the edit form (HTMX fields panel) reloads the rows
    html = client.get(f"/admin/act/{adam}/panel/fields", follow_redirects=False).text
    assert "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ" in html
    assert "ΠΕΡΙΦΕΡΕΙΑ ΑΤΤΙΚΗΣ" in html
    assert "ΑΝΑΔΟΧΟΣ ΑΕ" in html


def test_editing_replaces_party_rows(client):
    _admin(client)
    tok = get_csrf(client)
    r = client.post("/admin/acts/save",
                    data={"_mode": "create", "type": "notice", "title": "π",
                          "authority_0_name": "ΑΡΧΗ Α", "authority_0_afm": "1"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    adam = re.search(r"/admin/act/([^/]+)/edit", r.headers["location"]).group(1)
    assert [a["name"] for a in _rows("act_authority", adam)] == ["ΑΡΧΗ Α"]

    # edit: replace with a different single authority
    tok = get_csrf(client)
    client.post("/admin/acts/save",
                data={"_mode": "edit", "_adam": adam, "type": "notice", "title": "π",
                      "authority_0_name": "ΑΡΧΗ Β", "authority_0_afm": "2"},
                headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert [a["name"] for a in _rows("act_authority", adam)] == ["ΑΡΧΗ Β"]


def test_auto_relate_on_exact_match(client):
    _admin(client)
    with connect() as c:
        cur = c.cursor()
        cur.execute("INSERT INTO proc.authority (org_id, vat_number, name) VALUES (%s,%s,%s)",
                    ("ORG-B1", "090123456", "Δήμος Αθηναίων"))
        cur.execute("INSERT INTO proc.economic_operator (vat_number, name) "
                    "VALUES (%s,%s) RETURNING operator_id", ("099111222", "ΑΝΑΔΟΧΟΣ ΑΕ"))
        op_id = cur.fetchone()["operator_id"]
    tok = get_csrf(client)
    r = client.post("/admin/acts/save", data={
        "_mode": "create", "type": "notice", "title": "π",
        "authority_0_name": "Κάτι άσχετο", "authority_0_afm": "090123456",    # ΑΦΜ match
        "authority_1_name": "ΔΗΜΟΣ ΑΘΗΝΑΙΩΝ", "authority_1_afm": "",          # folded-name match
        "contractor_0_name": "ανάδοχος αε", "contractor_0_afm": "",           # folded-name match
        "contractor_1_name": "Άγνωστος Ανάδοχος", "contractor_1_afm": "000",  # no match
    }, headers={"X-CSRF-Token": tok}, follow_redirects=False)
    adam = re.search(r"/admin/act/([^/]+)/edit", r.headers["location"]).group(1)

    auths = _rows("act_authority", adam)
    assert auths[0]["authority_id"] == "ORG-B1"    # by ΑΦΜ
    assert auths[1]["authority_id"] == "ORG-B1"    # by accent/case-folded name

    contr = _rows("act_contractor", adam)
    assert contr[0]["operator_id"] == op_id        # by folded name
    assert contr[1]["operator_id"] is None         # no match → unlinked


def test_ambiguous_name_does_not_auto_relate(client):
    _admin(client)
    with connect() as c:
        cur = c.cursor()
        for oid in ("ORG-D1", "ORG-D2"):           # two authorities, same name
            cur.execute("INSERT INTO proc.authority (org_id, name) VALUES (%s,%s)",
                        (oid, "ΚΟΙΝΟ ΟΝΟΜΑ"))
    tok = get_csrf(client)
    r = client.post("/admin/acts/save", data={
        "_mode": "create", "type": "notice", "title": "π",
        "authority_0_name": "Κοινό Όνομα", "authority_0_afm": "",
    }, headers={"X-CSRF-Token": tok}, follow_redirects=False)
    adam = re.search(r"/admin/act/([^/]+)/edit", r.headers["location"]).group(1)
    assert _rows("act_authority", adam)[0]["authority_id"] is None   # ambiguous → not linked
