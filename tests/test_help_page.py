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


# --------------------------------------------------------------------------- #
# Translation coverage
#
# The section test above pins ONE phrase. That is not enough: an untranslated
# key falls back to Greek silently, so the EN page degrades a paragraph at a
# time and nothing fails. Eight paragraphs of the AI-summary section had drifted
# that way before anyone noticed. Assert the whole page instead.
# --------------------------------------------------------------------------- #
def test_every_help_string_has_an_english_translation():
    import pathlib
    import re

    from app.i18n_catalog import UI_EN

    src = pathlib.Path("app/templates/beta_help.html").read_text(encoding="utf-8")
    keys = {m.group(2) for m in
            re.finditer(r"""t\(\s*(["'])(.*?)\1\s*\)""", src, re.S)}
    assert keys, "no t() calls found — the extraction regex has drifted"
    missing = sorted(k for k in keys if k not in UI_EN)
    assert not missing, (
        "beta_help.html has %d string(s) with no entry in UI_EN; the English "
        "page renders them in Greek:\n  - %s" % (len(missing), "\n  - ".join(missing)))


# --------------------------------------------------------------------------- #
# The other two feature-gated sections follow their switch, same rule as the
# passwordless one: the manual must not describe a panel that is not there.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def flag():
    """Set any template global for the duration of one test."""
    from app import main
    saved = {}

    def _set(name, value):
        saved.setdefault(name, main.templates.env.globals.get(name))
        main.templates.env.globals[name] = value

    yield _set
    for name, value in saved.items():
        main.templates.env.globals[name] = value


@pytest.mark.parametrize("global_name, marker", [
    ("ai_summary_enabled", "Σύνοψη διαγωνισμού (AI)"),
    ("telephony_enabled", "Τηλεφωνία (softphone & αναγνώριση κλήσης)"),
])
def test_gated_section_follows_its_switch(client, flag, global_name, marker):
    _as_admin(client)

    flag(global_name, True)
    on = client.get("/help", follow_redirects=False).text
    assert marker in on or marker.replace("&", "&amp;") in on

    flag(global_name, False)
    off = client.get("/help", follow_redirects=False).text
    assert marker not in off and marker.replace("&", "&amp;") not in off
