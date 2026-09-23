"""/account/favorites — slice 3 of docs/specs/calendar-feed.md.

Favourites on the web, over the table that shipped with the mobile API. The web
module keeps its own copy of the rules (see app/account_favorites.py). What is checked here is the WEB surface: who may
write, that the toggle answers with its own next state, that the list is the
user's own, the cap, and that the star appears where it should and nowhere
else. Needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tests.helpers import get_csrf, grant, login, make_user

A1 = "26PROC019930001"
A2 = "26PROC019930002"
DEADLINE = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture()
def acts(db):
    c = db.cursor()
    for adam, title in ((A1, "Προμήθεια γαντιών"), (A2, "Υπηρεσίες καθαριότητας")):
        c.execute("""INSERT INTO proc.procurement_act
                       (adam, type, title, origin, data_source,
                        final_submission_date, submission_date, ingested_at)
                     VALUES (%s, 'notice', %s, 'import', 'khmdhs', %s, %s, now())""",
                  (adam, title, DEADLINE, DEADLINE - dt.timedelta(days=20)))
    yield c
    c.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)", ([A1, A2],))


def _member(client, name="favcust"):
    uid = make_user(name)
    grant(uid)
    login(client, name, "pw-123456")
    return uid


def _h(client):
    """What _csrf.html makes HTMX send on every request."""
    return {"X-CSRF-Token": get_csrf(client), "HX-Request": "true"}


def _add(client, adam, **kw):
    return client.post(f"/account/favorites/{adam}", headers=_h(client), **kw)


def _remove(client, adam, **kw):
    return client.delete(f"/account/favorites/{adam}", headers=_h(client), **kw)


def _rows(db, uid):
    c = db.cursor()
    c.execute("SELECT adam FROM proc.user_favorite_act WHERE user_id=%s ORDER BY adam",
              (uid,))
    return [r["adam"] for r in c.fetchall()]


# --------------------------------------------------------------------------- #
# Who may write
# --------------------------------------------------------------------------- #
def test_the_list_sends_a_signed_out_visitor_to_login(acts, client):
    r = client.get("/account/favorites", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/account/favorites"


def test_a_signed_out_write_is_refused_not_redirected(acts, client):
    """A signed-out POST is a stale tab or a forgery, not someone who needs the
    login page. CSRF refuses it first; either way nothing is written."""
    assert client.post(f"/account/favorites/{A1}").status_code == 403


def test_a_write_without_the_csrf_header_is_refused(acts, client, db):
    uid = _member(client)
    assert client.post(f"/account/favorites/{A1}").status_code == 403
    assert _rows(db, uid) == []


def test_a_lapsed_customer_may_still_keep_bookmarks(acts, client, db):
    """Signed in is the gate, not entitled: a favourite writes the user's own
    row and exposes nothing. The BUTTON follows the page's gating instead."""
    uid = make_user("lapsedfav")                      # no grant
    login(client, "lapsedfav", "pw-123456")
    assert _add(client, A1).status_code == 200
    assert _rows(db, uid) == [A1]


# --------------------------------------------------------------------------- #
# The toggle answers with its own next state
# --------------------------------------------------------------------------- #
def test_adding_returns_the_on_state_and_writes_the_row(acts, client, db):
    uid = _member(client)
    r = _add(client, A1)
    assert r.status_code == 200
    assert 'aria-pressed="true"' in r.text
    assert f'hx-delete="/account/favorites/{A1}"' in r.text
    assert _rows(db, uid) == [A1]


def test_removing_returns_the_off_state_and_deletes_the_row(acts, client, db):
    uid = _member(client)
    _add(client, A1)
    r = _remove(client, A1)
    assert r.status_code == 200
    assert 'aria-pressed="false"' in r.text
    assert f'hx-post="/account/favorites/{A1}"' in r.text
    assert _rows(db, uid) == []


def test_adding_twice_keeps_one_row(acts, client, db):
    uid = _member(client)
    _add(client, A1)
    assert _add(client, A1).status_code == 200
    assert _rows(db, uid) == [A1]


def test_removing_something_already_gone_is_fine(acts, client):
    """Idempotent, like the mobile path — a double click must not error."""
    _member(client)
    assert _remove(client, A1).status_code == 200
    assert _remove(client, A1).status_code == 200


def test_an_unknown_act_is_404_and_writes_nothing(acts, client, db):
    uid = _member(client)
    assert _add(client, "26PROC000000000").status_code == 404
    assert _rows(db, uid) == []


def test_the_compact_star_stays_compact_across_the_swap(acts, client):
    """The result-card star swaps itself too; if the answer came back full-size
    the card layout would jump on every click."""
    _member(client)
    r = _add(client, A1, params={"compact": "true"})
    assert "compact" in r.text
    assert f"/account/favorites/{A1}?compact=true" in r.text
    assert "Στα αγαπημένα</span>" not in r.text          # no visible label


# --------------------------------------------------------------------------- #
# The cap
# --------------------------------------------------------------------------- #
def test_the_cap_answers_off_with_the_reason_and_writes_nothing(acts, client, db,
                                                                 monkeypatch):
    from app import account_favorites
    monkeypatch.setattr(account_favorites, "MAX_FAVORITES", 1)
    uid = _member(client)
    _add(client, A1)
    r = _add(client, A2)
    assert r.status_code == 200
    assert 'aria-pressed="false"' in r.text            # the page tells the truth
    assert "όριο αγαπημένων" in r.text
    assert _rows(db, uid) == [A1]


# --------------------------------------------------------------------------- #
# The list is yours, newest first
# --------------------------------------------------------------------------- #
def test_the_list_shows_your_favourites_newest_first(acts, client, db):
    uid = _member(client)
    _add(client, A1)
    _add(client, A2)
    # created_at defaults to now(); separate them so the order is deterministic.
    db.cursor().execute("""UPDATE proc.user_favorite_act
                              SET created_at = now() - interval '1 hour'
                            WHERE user_id=%s AND adam=%s""", (uid, A1))
    page = client.get("/account/favorites").text
    assert page.index("Υπηρεσίες καθαριότητας") < page.index("Προμήθεια γαντιών")


def test_nobody_sees_anyone_elses_favourites(acts, client):
    _member(client, "favone")
    _add(client, A1)
    client.cookies.clear()
    _member(client, "favtwo")
    page = client.get("/account/favorites").text
    assert "Προμήθεια γαντιών" not in page


def test_an_empty_list_says_how_to_start(acts, client):
    _member(client)
    assert "Πατήστε το αστέρι" in client.get("/account/favorites").text


def test_the_account_page_links_to_the_list(acts, client):
    _member(client)
    assert 'href="/account/favorites"' in client.get("/account").text


# --------------------------------------------------------------------------- #
# Where the star appears
# --------------------------------------------------------------------------- #
def test_the_act_page_shows_the_star_in_its_current_state(acts, client):
    _member(client)
    page = client.get(f"/act/{A1}").text
    assert f'hx-post="/account/favorites/{A1}"' in page
    _add(client, A1)
    page = client.get(f"/act/{A1}").text
    assert f'hx-delete="/account/favorites/{A1}"' in page


def test_search_results_carry_the_star_for_a_signed_in_viewer(acts, client):
    _member(client)
    _add(client, A1)
    page = client.get("/search", params={"q": "γαντιών"},
                      headers={"HX-Request": "true"}).text
    assert f'hx-delete="/account/favorites/{A1}?compact=true"' in page


def test_search_results_carry_no_star_for_an_anonymous_viewer(acts, client):
    page = client.get("/search", params={"q": "γαντιών"},
                      headers={"HX-Request": "true"}).text
    assert "/account/favorites/" not in page


# --------------------------------------------------------------------------- #
# One meaning of "favourite" across web and phone
# --------------------------------------------------------------------------- #
def test_a_web_favourite_is_a_mobile_favourite(acts, client, db):
    """The web module keeps its own copy of the favourite rules (so the web app
    deploys without the mobile API). When the mobile module IS present, prove
    the two still agree about the shared table."""
    mobile_favorites = pytest.importorskip("app.mobile_favorites")
    uid = _member(client)
    _add(client, A1)
    assert mobile_favorites.favorite_adams(db.cursor(), uid, [A1, A2]) == {A1}


def test_the_web_path_does_not_import_the_mobile_modules():
    """main.py loads account_favorites at startup. If it ever pulls in
    mobile_favorites again, the web app cannot deploy without the mobile API."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app" / "account_favorites.py"
    imported = set()
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{a.name}" for a in node.names}
            imported.add(node.module or "")
    assert not [m for m in imported if "mobile" in m or "api_v1" in m], imported


def test_re_adding_at_the_cap_keeps_the_star_on(acts, client, db, monkeypatch):
    """At the cap only a NEW favourite is refused; re-adding one that exists
    must not report it as off."""
    from app import account_favorites
    monkeypatch.setattr(account_favorites, "MAX_FAVORITES", 1)
    uid = _member(client)
    _add(client, A1)
    r = _add(client, A1)
    assert 'aria-pressed="true"' in r.text
    assert "όριο" not in r.text
    assert _rows(db, uid) == [A1]
