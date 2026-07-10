"""Tri-state act booleans: NULL = "Not specified" (default), TRUE = Yes,
FALSE = No — in the display, the create/edit form, and the save parsing."""
import re

from tests.helpers import connect, get_csrf, login, make_user


def _mk_act(db, adam, **cols):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
    keys = ["adam", "type", "title", "origin", "data_source"] + list(cols)
    vals = [adam, "notice", "Δοκιμή", "authored", "manual"] + list(cols.values())
    ph = ",".join(["%s"] * len(keys))
    cur.execute(f"INSERT INTO proc.procurement_act ({','.join(keys)}) VALUES ({ph})", vals)


def _dd(html, label):
    m = re.search(re.escape(label) + r"</dt><dd>(.*?)</dd>", html)
    return re.sub("<[^>]+>", "", m.group(1)).strip() if m else None


def test_act_page_renders_three_states(client, db):
    # vat_rate set so the extended panel shows; the booleans span all three states
    _mk_act(db, "TS-DISP", divided_into_lots=None, is_framework_agreement=True,
            vat_included=False, vat_rate=24)
    make_user("tsadmin", "goodpassword1", role="admin")
    login(client, "tsadmin", "goodpassword1")
    r = client.get("/act/TS-DISP", follow_redirects=False)
    assert r.status_code == 200
    assert _dd(r.text, "Διαίρεση σε τμήματα") == "Δεν προσδιορίζεται"   # NULL
    assert _dd(r.text, "Συμφωνία-πλαίσιο") == "ναι"                     # TRUE
    assert _dd(r.text, "Περιλαμβάνεται ΦΠΑ") == "όχι"                   # FALSE
    db.cursor().execute("DELETE FROM proc.procurement_act WHERE adam='TS-DISP'")


def test_form_uses_tristate_select_not_checkbox(client):
    make_user("tsadmin2", "goodpassword1", role="admin")
    login(client, "tsadmin2", "goodpassword1")
    html = client.get("/admin/acts/new", follow_redirects=False).text
    assert '<select name="divided_into_lots">' in html
    assert 'type="checkbox" name="divided_into_lots"' not in html      # no more checkbox
    assert "Δεν προσδιορίζεται" in html                                 # the default option


def test_save_parses_tristate(client):
    make_user("tsadmin3", "goodpassword1", role="admin")
    login(client, "tsadmin3", "goodpassword1")
    tok = get_csrf(client)
    title = "TRISTATE-SAVE-XYZ"
    r = client.post("/admin/acts/save",
                    data={"_mode": "create", "type": "notice", "title": title,
                          "divided_into_lots": "",          # Not specified → NULL
                          "is_framework_agreement": "1",    # Yes → True
                          "vat_included": "0",              # No → False
                          "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code in (302, 303)
    with connect() as c:
        cur = c.cursor()
        cur.execute("""SELECT divided_into_lots, is_framework_agreement, vat_included
                       FROM proc.procurement_act WHERE title=%s
                       ORDER BY adam DESC LIMIT 1""", (title,))
        row = cur.fetchone()
        cur.execute("DELETE FROM proc.procurement_act WHERE title=%s", (title,))
    assert row["divided_into_lots"] is None
    assert row["is_framework_agreement"] is True
    assert row["vat_included"] is False
