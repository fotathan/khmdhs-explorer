# -*- coding: utf-8 -*-
""""Back to the list" keeps the list — authorities and contractors.

Each list page remembers its live URL (filters AND page) and each detail
page's back link restores it (_back_to_results.html). The browser half cannot
run here, so these pin that both halves render with the SAME list path: a
detail page reading a key its list never writes would silently fall back to
the bare list. The search → act pair is covered in test_search_match.py.
"""
import pytest

from tests.helpers import login, make_user

ORG = "BACK-AUTH-1"
VAT = "999000222"


@pytest.fixture()
def entities(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.authority WHERE org_id = %s", (ORG,))
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = %s", (VAT,))
    cur.execute("INSERT INTO proc.authority (org_id, name) VALUES (%s, 'ΔΗΜΟΣ ΕΠΙΣΤΡΟΦΗΣ')",
                (ORG,))
    cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                   VALUES (%s, 'ΕΠΙΣΤΡΟΦΗ ΑΕ', true)""", (VAT,))
    # the list pages read these; an unpopulated view is an error, not empty
    cur.execute("REFRESH MATERIALIZED VIEW proc.mv_authority_counts")
    cur.execute("REFRESH MATERIALIZED VIEW proc.mv_contractor_counts")
    yield
    cur.execute("DELETE FROM proc.authority WHERE org_id = %s", (ORG,))
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = %s", (VAT,))


@pytest.fixture()
def admin(client):
    make_user("backadmin", "goodpassword1", role="admin")
    login(client, "backadmin", "goodpassword1")
    return client


def _key(path):
    return f"""var PATH="{path}", KEY='khmdhs:lastResults:'+PATH"""


@pytest.mark.parametrize("path", ["/authorities", "/contractors"])
def test_list_page_remembers_itself(admin, entities, path):
    body = admin.get(path, params={"q": "επιστροφ", "page": "1"}).text
    assert _key(path) in body
    assert "sessionStorage.setItem(KEY" in body


@pytest.mark.parametrize("detail, path", [(f"/authority/{ORG}", "/authorities"),
                                          (f"/contractor/{VAT}", "/contractors")])
def test_detail_back_link_restores_its_list(admin, entities, detail, path):
    r = admin.get(detail)
    assert r.status_code == 200
    # no-script fallback: the bare list, never the search page
    assert f'id="back-to-results" href="{path}"' in r.text
    assert _key(path) in r.text
    assert "document.referrer" in r.text
