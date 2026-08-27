"""The in-app user manual (/help).

It is the only user-facing description of how the app works, and CLAUDE.md
requires it to be updated whenever a user-facing feature changes — so it is
worth pinning that it renders at all, that it stays admin-only, and that the
passwordless-sign-in section tracks whether that feature is actually switched
on. A manual describing a button that is not on the page is worse than one that
says nothing.
"""
import pytest

from tests.helpers import login, make_user

# A phrase from the passwordless subsection that appears nowhere else on the
# page, in each language.
EL_MARKER = "Σύνδεση χωρίς κωδικό (σύνδεσμος με email)"
EN_MARKER = "Passwordless sign-in (emailed link)"


@pytest.fixture()
def help_flag():
    """Toggle what the template sees.

    The templates read `login_links_enabled` as a Jinja global stamped at import
    time — env is fixed for the life of a process, so that is the right shape
    for the app and the wrong shape for monkeypatch.setenv. Patch the global
    itself, which is what actually decides the rendered page."""
    from app import main
    original = main.templates.env.globals.get("login_links_enabled")

    def _set(value):
        main.templates.env.globals["login_links_enabled"] = value

    yield _set
    main.templates.env.globals["login_links_enabled"] = original


def _as_admin(client):
    make_user("helpadmin", "goodpassword1", role="admin")
    assert login(client, "helpadmin", "goodpassword1").status_code == 303


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #
def test_help_is_not_public(client):
    r = client.get("/help", follow_redirects=False)
    assert r.status_code == 303           # anonymous → login
    assert r.headers["location"].startswith("/login")


def test_help_is_denied_to_a_plain_customer(client):
    make_user("helpcust", "goodpassword1", role="customer")
    login(client, "helpcust", "goodpassword1")
    assert client.get("/help", follow_redirects=False).status_code == 403


def test_help_renders_for_an_admin(client):
    _as_admin(client)
    r = client.get("/help", follow_redirects=False)
    assert r.status_code == 200
    assert "Λογαριασμοί &amp; πρόσβαση" in r.text or "Λογαριασμοί & πρόσβαση" in r.text


# --------------------------------------------------------------------------- #
# The passwordless section follows the feature switch
# --------------------------------------------------------------------------- #
def test_passwordless_section_is_documented_when_the_feature_is_on(client, help_flag):
    _as_admin(client)
    help_flag(True)
    assert EL_MARKER in client.get("/help", follow_redirects=False).text


def test_passwordless_section_is_absent_when_the_feature_is_off(client, help_flag):
    """Prod runs with LOGIN_LINKS_ENABLED=0 until mail deliverability is done —
    the manual must not describe a door that is not there."""
    _as_admin(client)
    help_flag(False)
    body = client.get("/help", follow_redirects=False).text
    assert EL_MARKER not in body
    assert "Γιατί χρειάζεται δεύτερη πατησιά" not in body
    # The rest of the accounts section is untouched.
    assert "Σύνδεση &amp; εγγραφή" in body or "Σύνδεση & εγγραφή" in body


def test_the_section_is_translated_not_silently_greek(client, help_flag):
    """A missing catalog entry falls back to Greek, which on the EN page looks
    like a rendering bug rather than the missing translation it is."""
    _as_admin(client)
    help_flag(True)
    client.cookies.set("lang", "en")
    body = client.get("/help", follow_redirects=False).text
    assert EN_MARKER in body
    assert EL_MARKER not in body
