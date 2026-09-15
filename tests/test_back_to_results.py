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


@pytest.mark.parametrize("path", ["/authorities", "/contractors", "/explore",
                                  "/analytics"])
def test_list_page_remembers_itself(admin, entities, path):
    r = admin.get(path, params={"q": "επιστροφ"})
    assert r.status_code == 200
    assert _key(path) in r.text
    assert "sessionStorage.setItem(KEY" in r.text
    assert "sessionStorage.setItem('khmdhs:lastList', PATH)" in r.text


@pytest.mark.parametrize("detail, path", [(f"/authority/{ORG}", "/authorities"),
                                          (f"/contractor/{VAT}", "/contractors")])
def test_detail_back_link_restores_its_list(admin, entities, detail, path):
    r = admin.get(detail)
    assert r.status_code == 200
    # no-script fallback: the bare list, never the search page
    assert f'id="back-to-results" href="{path}"' in r.text
    # both lists that lead here, the entity's own list first (the default)
    assert f'var LISTS=[["{path}", ' in r.text
    assert '["/explore", ' in r.text
    assert '["/analytics", ' in r.text
    assert "document.referrer" in r.text


def test_authority_back_link_also_returns_to_the_search(admin, entities):
    """Search result cards link the authority, so the search is one of its
    origins — listed last, so the default label stays the authorities list."""
    body = admin.get(f"/authority/{ORG}").text
    assert '["/", ' in body
    assert body.index('["/authorities", ') < body.index('["/", ')
    assert '["/", ' not in admin.get(f"/contractor/{VAT}").text   # cards link no contractor


@pytest.mark.parametrize("detail", [f"/authority/{ORG}", f"/contractor/{VAT}"])
def test_detail_back_link_also_returns_to_the_act(admin, entities, detail):
    """The act page links its authority and contractors; an act address varies,
    so it is accepted by prefix — and listed last, so a stale act never
    outranks a list."""
    body = admin.get(detail).text
    assert '["/act/", ' in body
    assert body.rstrip().count('["/act/", ') == 1
    assert body.index('["/act/", ') > body.index('var LISTS=')


def test_authority_back_link_also_returns_to_the_contractor(admin, entities):
    """The contractor page links the authorities it worked for; that page
    registers itself by prefix and the authority accepts it as an origin."""
    contractor = admin.get(f"/contractor/{VAT}").text
    assert """var PATH="/contractor/", KEY='khmdhs:lastResults:'+PATH""" in contractor
    assert "var PFX=true;" in contractor

    authority = admin.get(f"/authority/{ORG}").text
    assert '["/contractor/", ' in authority

    # A registered detail page overwrites the last-visited mark, so each back
    # link also keeps the origin it chose, under its own address — or act →
    # contractor → authority → back loses the act.
    assert "BACK='khmdhs:backFrom:'" in contractor
    assert "var SELF=BACK+location.pathname" in contractor


def test_explore_list_link_follows_the_live_filters(admin, entities):
    body = admin.get("/explore").text
    assert 'class="back-link js-as-list"' in body
    assert "'/'+window.location.search" in body
