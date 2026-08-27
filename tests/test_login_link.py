"""Passwordless sign-in links: the token lifecycle, the /login/link routes, and
the properties the feature is only safe if it keeps.

Mail goes through the in-memory backend (EMAIL_BACKEND=memory), so the tests
read the exact message a customer would receive — the link included.
"""
import re

import pyotp
import pytest

from app import auth, login_links
from tests.helpers import connect, enable_mfa, get_csrf, login, make_user

LINK_RE = re.compile(r"/login/link/([A-Za-z0-9_-]{20,})")


@pytest.fixture()
def mail(monkeypatch):
    """Capture outgoing mail instead of sending it."""
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    mailer.clear_outbox()
    yield mailer
    mailer.clear_outbox()


def _set_email(uid, email):
    with connect() as c:
        auth.set_email(c.cursor(), uid, email)


def _request_link(client, email, next_url="/"):
    return client.post("/login/link", data={"email": email, "next": next_url},
                       follow_redirects=False)


def _token_from_mail(mailer):
    [msg] = mailer.outbox()
    m = LINK_RE.search(msg["text"]) or LINK_RE.search(msg["html"])
    assert m, f"no sign-in link in the message:\n{msg['text']}"
    return m.group(1)


# --------------------------------------------------------------------------- #
# Token lifecycle (DB, no HTTP)
# --------------------------------------------------------------------------- #
def test_issue_returns_raw_token_and_stores_only_the_hash(db):
    uid = make_user("tok1", "goodpassword1")
    with connect() as c:
        cur = c.cursor()
        raw = login_links.issue(cur, uid)
        cur.execute("SELECT token_hash FROM proc.login_link WHERE user_id = %s", (uid,))
        stored = cur.fetchone()["token_hash"]
    assert raw and raw != stored
    assert stored == login_links._hash(raw)
    # A database dump must not be a list of live credentials.
    assert raw not in stored


def test_consume_works_once(db):
    uid = make_user("tok2", "goodpassword1")
    with connect() as c:
        cur = c.cursor()
        raw = login_links.issue(cur, uid)
        assert login_links.consume(cur, raw)["user_id"] == uid
        assert login_links.consume(cur, raw) is None      # already spent


def test_peek_does_not_spend_the_token(db):
    """A mail scanner fetching the URL must not burn the customer's link."""
    uid = make_user("tok3", "goodpassword1")
    with connect() as c:
        cur = c.cursor()
        raw = login_links.issue(cur, uid)
        assert login_links.peek(cur, raw)["user_id"] == uid
        assert login_links.peek(cur, raw)["user_id"] == uid
        assert login_links.consume(cur, raw)               # still usable


def test_expired_token_is_refused(db):
    uid = make_user("tok4", "goodpassword1")
    with connect() as c:
        cur = c.cursor()
        raw = login_links.issue(cur, uid, ttl=60)
        cur.execute("UPDATE proc.login_link SET expires_at = now() - interval '1 second' "
                    "WHERE user_id = %s", (uid,))
        assert login_links.peek(cur, raw) is None
        assert login_links.consume(cur, raw) is None


def test_unknown_token_is_refused(db):
    with connect() as c:
        assert login_links.consume(c.cursor(), "not-a-real-token") is None


def test_deactivated_account_cannot_consume(db):
    uid = make_user("tok5", "goodpassword1")
    with connect() as c:
        cur = c.cursor()
        raw = login_links.issue(cur, uid)
        auth.set_active(cur, uid, False)
        assert login_links.consume(cur, raw) is None


def test_issuing_a_new_link_burns_the_previous_one(db):
    """Asking again is what people do when the first mail did not arrive; two
    live credentials for one account is worse than one."""
    uid = make_user("tok6", "goodpassword1")
    with connect() as c:
        cur = c.cursor()
        first = login_links.issue(cur, uid)
        second = login_links.issue(cur, uid)
        assert login_links.consume(cur, first) is None
        assert login_links.consume(cur, second)


def test_password_change_burns_outstanding_links(db):
    uid = make_user("tok7", "goodpassword1")
    with connect() as c:
        cur = c.cursor()
        raw = login_links.issue(cur, uid)
        auth.set_password(cur, uid, "anotherpassword1")
        assert login_links.consume(cur, raw) is None


def test_changing_the_email_burns_links_and_clears_verification(db):
    """A link already sitting in the OLD mailbox must not still open the account."""
    uid = make_user("tok8", "goodpassword1")
    _set_email(uid, "old@example.com")
    with connect() as c:
        cur = c.cursor()
        raw = login_links.issue(cur, uid)
        login_links.mark_email_verified(cur, uid)
        auth.set_email(cur, uid, "new@example.com")
        assert login_links.consume(cur, raw) is None
        cur.execute("SELECT email_verified_at FROM proc.app_user WHERE id = %s", (uid,))
        assert cur.fetchone()["email_verified_at"] is None


def test_rewriting_the_same_email_keeps_the_verification(db):
    uid = make_user("tok9", "goodpassword1")
    _set_email(uid, "same@example.com")
    with connect() as c:
        cur = c.cursor()
        login_links.mark_email_verified(cur, uid)
        auth.set_email(cur, uid, "SAME@example.com")       # case-insensitive
        cur.execute("SELECT email_verified_at FROM proc.app_user WHERE id = %s", (uid,))
        assert cur.fetchone()["email_verified_at"] is not None


def test_get_by_email_is_case_insensitive_and_never_matches_a_username(db):
    uid = make_user("casey", "goodpassword1")
    _set_email(uid, "Casey@Example.COM")
    with connect() as c:
        cur = c.cursor()
        assert auth.get_by_email(cur, "casey@example.com")["id"] == uid
        assert auth.get_by_email(cur, "casey") is None      # a username is not an address
        assert auth.get_by_email(cur, "") is None
        assert auth.get_by_email(cur, None) is None


# --------------------------------------------------------------------------- #
# Requesting a link
# --------------------------------------------------------------------------- #
def test_request_sends_a_link_to_a_known_address(client, mail):
    uid = make_user("linkuser", "goodpassword1")
    _set_email(uid, "linkuser@example.com")
    r = _request_link(client, "linkuser@example.com")
    assert r.status_code == 200
    assert len(mail.outbox()) == 1
    assert mail.outbox()[0]["intended"] == "linkuser@example.com"
    assert _token_from_mail(mail)


def test_unknown_address_looks_identical_and_sends_nothing(client, mail):
    """The response must not tell an attacker who has an account."""
    uid = make_user("linkuser", "goodpassword1")
    _set_email(uid, "linkuser@example.com")
    known = _request_link(client, "linkuser@example.com")
    mail.clear_outbox()
    unknown = _request_link(client, "nobody@example.com")
    assert unknown.status_code == known.status_code == 200
    assert unknown.text == known.text
    assert mail.outbox() == []


def test_deactivated_account_is_not_mailed(client, mail):
    uid = make_user("gone", "goodpassword1", active=False)
    _set_email(uid, "gone@example.com")
    r = _request_link(client, "gone@example.com")
    assert r.status_code == 200          # same page as everyone else
    assert mail.outbox() == []


def test_malformed_address_is_rejected_before_anything_else(client, mail):
    r = _request_link(client, "not-an-address")
    assert r.status_code == 400
    assert mail.outbox() == []


def test_link_requests_are_throttled(client, mail):
    uid = make_user("floody", "goodpassword1")
    _set_email(uid, "floody@example.com")
    # Every REQUEST counts here (not just failures): the endpoint sends mail, so
    # a successful send is exactly what has to be limited.
    for _ in range(8):
        assert _request_link(client, "floody@example.com").status_code == 200
    assert _request_link(client, "floody@example.com").status_code == 429
    assert len(mail.outbox()) == 8


def test_throttled_user_can_still_sign_in_with_a_password(client, mail):
    """The whole point of shipping this ALONGSIDE passwords."""
    uid = make_user("floody", "goodpassword1")
    _set_email(uid, "floody@example.com")
    for _ in range(9):
        _request_link(client, "floody@example.com")
    assert login(client, "floody", "goodpassword1").status_code == 303


# --------------------------------------------------------------------------- #
# Using a link
# --------------------------------------------------------------------------- #
def test_get_shows_the_interstitial_without_signing_in(client, mail):
    uid = make_user("clicker", "goodpassword1")
    _set_email(uid, "clicker@example.com")
    _request_link(client, "clicker@example.com")
    token = _token_from_mail(mail)

    r = client.get(f"/login/link/{token}", follow_redirects=False)
    assert r.status_code == 200
    assert "clicker" in r.text
    # Fetching the URL — what a mail scanner does — must not authenticate...
    assert client.get("/account", follow_redirects=False).status_code != 200
    # ...nor spend the token.
    assert client.post(f"/login/link/{token}",
                       follow_redirects=False).status_code == 303


def test_post_signs_in(client, mail):
    uid = make_user("clicker", "goodpassword1")
    _set_email(uid, "clicker@example.com")
    _request_link(client, "clicker@example.com")
    token = _token_from_mail(mail)

    r = client.post(f"/login/link/{token}", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/account", follow_redirects=False).status_code == 200


def test_second_post_is_refused(client, mail):
    uid = make_user("clicker", "goodpassword1")
    _set_email(uid, "clicker@example.com")
    _request_link(client, "clicker@example.com")
    token = _token_from_mail(mail)
    client.post(f"/login/link/{token}", follow_redirects=False)

    from fastapi.testclient import TestClient
    from app.main import app
    fresh = TestClient(app, base_url="https://testserver")
    assert fresh.post(f"/login/link/{token}", follow_redirects=False).status_code == 410


def test_invalid_token_page_is_gone_not_an_error(client):
    r = client.get("/login/link/totally-made-up-token", follow_redirects=False)
    assert r.status_code == 410


def test_link_login_marks_the_address_verified(client, mail, db):
    uid = make_user("verified", "goodpassword1")
    _set_email(uid, "verified@example.com")
    _request_link(client, "verified@example.com")
    client.post(f"/login/link/{_token_from_mail(mail)}", follow_redirects=False)
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT email_verified_at FROM proc.app_user WHERE id = %s", (uid,))
        assert cur.fetchone()["email_verified_at"] is not None


def test_next_url_is_honoured_and_cannot_be_an_open_redirect(client, mail):
    uid = make_user("nexty", "goodpassword1")
    _set_email(uid, "nexty@example.com")

    _request_link(client, "nexty@example.com", next_url="/account")
    r = client.post(f"/login/link/{_token_from_mail(mail)}", follow_redirects=False)
    assert r.headers["location"] == "/account"

    mail.clear_outbox()
    _request_link(client, "nexty@example.com", next_url="https://evil.example/steal")
    r = client.post(f"/login/link/{_token_from_mail(mail)}", follow_redirects=False)
    assert r.headers["location"] == "/"


# --------------------------------------------------------------------------- #
# The property that matters most: a link is NOT a way around 2FA
# --------------------------------------------------------------------------- #
def test_link_does_not_bypass_two_factor(client, mail):
    uid = make_user("boss2fa", "goodpassword1", role="admin")
    _set_email(uid, "boss2fa@example.com")
    enable_mfa(uid)
    _request_link(client, "boss2fa@example.com")

    r = client.post(f"/login/link/{_token_from_mail(mail)}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login/mfa"
    # Not authenticated yet — the second factor is still owed.
    assert client.get("/admin/users", follow_redirects=False).status_code != 200


def test_link_plus_totp_completes_the_login(client, mail):
    uid = make_user("boss2fa", "goodpassword1", role="admin")
    _set_email(uid, "boss2fa@example.com")
    secret, _ = enable_mfa(uid)
    _request_link(client, "boss2fa@example.com")
    client.post(f"/login/link/{_token_from_mail(mail)}", follow_redirects=False)

    r = client.post("/login/mfa", data={"code": pyotp.TOTP(secret).now()},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/admin/users", follow_redirects=False).status_code == 200


def test_link_login_still_forces_a_temporary_password_change(client, mail):
    """must_change_password is a property of the ACCOUNT, not of how it signed in."""
    uid = make_user("temped", "goodpassword1")
    _set_email(uid, "temped@example.com")
    with connect() as c:
        auth.set_password(c.cursor(), uid, "Temp-abc123456", must_change=True)
    _request_link(client, "temped@example.com")
    client.post(f"/login/link/{_token_from_mail(mail)}", follow_redirects=False)

    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/force-password"


# --------------------------------------------------------------------------- #
# The email itself
# --------------------------------------------------------------------------- #
def test_email_carries_the_link_in_both_html_and_text(client, mail):
    uid = make_user("bodies", "goodpassword1")
    _set_email(uid, "bodies@example.com")
    _request_link(client, "bodies@example.com")
    [msg] = mail.outbox()
    assert LINK_RE.search(msg["html"]) and LINK_RE.search(msg["text"])
    # The raw token must never be the thing stored server-side.
    token = _token_from_mail(mail)
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT 1 FROM proc.login_link WHERE token_hash = %s",
                    (login_links._hash(token),))
        assert cur.fetchone()


def test_email_wording_comes_from_the_template_and_survives_its_deletion(client, mail, db):
    uid = make_user("wording", "goodpassword1")
    _set_email(uid, "wording@example.com")
    with connect() as c:
        cur = c.cursor()
        auth.upsert_email_template(
            cur, slug="login_link", lang="el", name="Σύνδεσμος",
            subject="Μπείτε στον λογαριασμό σας",
            body_html="<p>Γεια [[full_name]] — ορίστε ο σύνδεσμος.</p>")
    _request_link(client, "wording@example.com")
    assert mail.outbox()[0]["subject"] == "Μπείτε στον λογαριασμό σας"

    # An admin emptying the table must not lock everybody out.
    mail.clear_outbox()
    with connect() as c:
        c.execute("DELETE FROM proc.email_template WHERE slug = 'login_link'")
    _request_link(client, "wording@example.com")
    assert len(mail.outbox()) == 1
    assert _token_from_mail(mail)


def test_empty_merge_field_drops_out_instead_of_failing_the_send(client, mail, db):
    """No human is in the loop: a customer with no profile name must still get in."""
    uid = make_user("nameless", "goodpassword1")
    _set_email(uid, "nameless@example.com")
    with connect() as c:
        auth.upsert_email_template(
            c.cursor(), slug="login_link", lang="el", name="Σύνδεσμος",
            subject="Σύνδεση", body_html="<p>Καλημέρα [[full_name]], ορίστε.</p>")
    _request_link(client, "nameless@example.com")
    [msg] = mail.outbox()
    assert "[[full_name]]" not in msg["html"]
    assert "Καλημέρα, ορίστε." in msg["html"]


# --------------------------------------------------------------------------- #
# The kill switch
# --------------------------------------------------------------------------- #
def test_routes_are_gone_when_the_feature_is_switched_off(client, monkeypatch):
    monkeypatch.setenv("LOGIN_LINKS_ENABLED", "0")
    assert client.get("/login/link", follow_redirects=False).status_code == 404
    assert client.post("/login/link", data={"email": "a@b.com"},
                       follow_redirects=False).status_code == 404
