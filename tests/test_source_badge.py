"""Every act shows a data-source badge — including KHMDHS (previously only
Diavgeia/TED were badged)."""
from tests.helpers import login, make_user


def _make_act(db, adam, source):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
    cur.execute("""INSERT INTO proc.procurement_act (adam, type, title, origin, data_source)
                   VALUES (%s, 'contract', 'Δοκιμή', 'import', %s)""", (adam, source))
    return adam


def test_khmdhs_act_shows_source_badge(client, db):
    adam = _make_act(db, "SRC-KHMDHS-1", "khmdhs")
    make_user("srcadmin", "goodpassword1", role="admin")
    login(client, "srcadmin", "goodpassword1")
    r = client.get(f"/act/{adam}", follow_redirects=False)
    assert r.status_code == 200
    # match the badge SPAN, not the .src-khmdhs CSS rule that's on every page
    assert 'src-badge src-khmdhs' in r.text
    assert "ΚΗΜΔΗΣ" in r.text
    db.cursor().execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))


def test_diavgeia_act_still_badged(client, db):
    adam = _make_act(db, "SRC-DIAV-1", "diavgeia")
    make_user("srcadmin2", "goodpassword1", role="admin")
    login(client, "srcadmin2", "goodpassword1")
    r = client.get(f"/act/{adam}", follow_redirects=False)
    assert r.status_code == 200
    assert 'src-badge src-diavgeia' in r.text
    assert 'src-badge src-khmdhs' not in r.text     # not double-badged
    db.cursor().execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
