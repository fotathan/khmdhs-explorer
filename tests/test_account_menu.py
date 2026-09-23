"""The account menu: "my account" is a menu of dedicated pages, not one hub.

The masthead username opens a dropdown (_acct_menu.html) and every /account
page carries the same tab strip (_acct_nav.html); both render ONE list
(_acct_links.html). What is pinned here: every page is reachable from every
page, the current one is marked, sign-out still posts, and /account itself is
now only the settings page. Needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import re

import pytest

from tests.helpers import get_csrf, grant, login, make_user

# Favourites and the deadline calendar join when their pages ship.
PAGES = ("/account/searches", "/account")


@pytest.fixture()
def member(client, db):
    uid = make_user("menucust")
    grant(uid)
    login(client, "menucust", "pw-123456")
    return uid


def _tabs(html):
    m = re.search(r'<nav class="acct-tabs".*?</nav>', html, re.S)
    assert m, "no account tab strip on the page"
    return m.group(0)


def _menu(html):
    m = re.search(r'<details class="am">.*?</details>', html, re.S)
    assert m, "no account menu in the masthead"
    return m.group(0)


def _on(fragment):
    return re.findall(r'<a href="([^"]+)" class="on">', fragment)


@pytest.mark.parametrize("page", PAGES)
def test_every_account_page_links_every_other(client, member, page):
    html = client.get(page).text
    for block in (_tabs(html), _menu(html)):
        for target in PAGES:
            assert f'href="{target}"' in block, (page, target)


@pytest.mark.parametrize("page", PAGES)
def test_the_current_page_and_only_it_is_marked(client, member, page):
    html = client.get(page).text
    assert _on(_tabs(html)) == [page]
    assert _on(_menu(html)) == [page]


def test_the_2fa_page_sits_under_settings(client, member):
    assert _on(_tabs(client.get("/account/mfa").text)) == ["/account"]


def test_the_menu_is_on_ordinary_pages_with_nothing_marked(client, member):
    html = client.get("/").text
    assert _on(_menu(html)) == []
    assert 'class="acct-tabs"' not in html


def test_sign_out_lives_in_the_menu_and_still_works(client, member):
    menu = _menu(client.get("/").text)
    assert 'action="/logout"' in menu
    r = client.post("/logout", headers={"X-CSRF-Token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code in (302, 303)
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_anonymous_visitors_get_no_menu(client, db):
    html = client.get("/").text
    assert '<details class="am">' not in html


def test_account_is_the_settings_page_not_a_hub(client, member):
    html = client.get("/account").text
    assert 'action="/account/password"' in html
    # The link card moved into the menu; it must not come back as a button
    # on the settings page.
    assert "Διαχείριση αναζητήσεων & ειδοποιήσεων" not in html


def test_password_change_keeps_the_2fa_state(client, member, db, monkeypatch):
    """The password POST re-renders /account; it used to drop mfa_enabled and
    offer "turn 2FA on" to someone who had it on."""
    from app import auth
    monkeypatch.setattr(auth, "get_mfa", lambda c, uid: {"mfa_enabled": True})
    r = client.post("/account/password",
                    data={"current_password": "wrong", "new_password": "x" * 10,
                          "confirm_password": "x" * 10},
                    headers={"X-CSRF-Token": get_csrf(client)})
    assert r.status_code == 400
    assert 'href="/account/mfa">Διαχείριση 2FA' in r.text


def test_english_labels(client, member):
    client.get("/set-lang?lang=en")
    tabs = _tabs(client.get("/account").text)
    for label in ("Searches &amp; alerts", "Account settings"):
        assert label in tabs
