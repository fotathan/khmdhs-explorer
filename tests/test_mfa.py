"""Two-factor auth: TOTP verify, enrolment, login second factor, recovery codes."""
import re

import pyotp
from app import auth
from tests.helpers import connect, enable_mfa, get_csrf, login, make_user


# --- unit ---
def test_verify_totp():
    secret = auth.new_totp_secret()
    assert auth.verify_totp(secret, pyotp.TOTP(secret).now())
    assert not auth.verify_totp(secret, "000000")
    assert not auth.verify_totp(secret, "")
    assert not auth.verify_totp("", "123456")


def test_recovery_codes_hashed():
    plain, hashed = auth.gen_recovery_codes(5)
    assert len(plain) == len(hashed) == 5
    assert all(auth.verify_password(p, h) for p, h in zip(plain, hashed))
    # hashes are not the plaintext
    assert all(p != h for p, h in zip(plain, hashed))


# --- login second factor ---
def test_password_alone_does_not_authenticate(client):
    uid = make_user("boss2fa", "goodpassword1", role="admin")
    enable_mfa(uid)
    r = login(client, "boss2fa", "goodpassword1")
    assert r.status_code == 303
    assert r.headers["location"] == "/login/mfa"
    # still not authenticated — admin area denied
    assert client.get("/admin/users", follow_redirects=False).status_code != 200


def test_login_completes_with_totp(client):
    uid = make_user("boss2fa", "goodpassword1", role="admin")
    secret, _ = enable_mfa(uid)
    login(client, "boss2fa", "goodpassword1")
    r = client.post("/login/mfa", data={"code": pyotp.TOTP(secret).now()},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/admin/users", follow_redirects=False).status_code == 200


def test_login_mfa_rejects_wrong_code(client):
    uid = make_user("boss2fa", "goodpassword1", role="admin")
    enable_mfa(uid)
    login(client, "boss2fa", "goodpassword1")
    r = client.post("/login/mfa", data={"code": "000000"}, follow_redirects=False)
    assert r.status_code == 401
    assert client.get("/admin/users", follow_redirects=False).status_code != 200


def test_recovery_code_works_once(client):
    uid = make_user("boss2fa", "goodpassword1", role="admin")
    _, recovery = enable_mfa(uid)
    code = recovery[0]
    login(client, "boss2fa", "goodpassword1")
    assert client.post("/login/mfa", data={"code": code},
                       follow_redirects=False).status_code == 303
    assert client.get("/admin/users", follow_redirects=False).status_code == 200
    # the same recovery code cannot be reused
    client.get("/logout")
    login(client, "boss2fa", "goodpassword1")
    assert client.post("/login/mfa", data={"code": code},
                       follow_redirects=False).status_code == 401


# --- enrolment ---
def test_enrollment_flow(client):
    make_user("enroller", "goodpassword1", role="admin")
    login(client, "enroller", "goodpassword1")
    # the setup page renders the provisional secret (also stored in the session)
    html = client.get("/account/mfa").text
    m = re.search(r'class="mfa-secret">([A-Z2-7]+)<', html)
    assert m, "setup page should show the TOTP secret"
    secret = m.group(1)
    tok = get_csrf(client)
    r = client.post("/account/mfa/enable",
                    data={"code": pyotp.TOTP(secret).now()},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert "Recovery codes" in r.text or "Εφεδρικοί" in r.text
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT mfa_enabled FROM proc.app_user WHERE username='enroller'")
        assert cur.fetchone()["mfa_enabled"] is True


def test_enrollment_rejects_bad_code(client):
    make_user("enroller", "goodpassword1", role="admin")
    login(client, "enroller", "goodpassword1")
    client.get("/account/mfa")     # establish the provisional secret
    tok = get_csrf(client)
    r = client.post("/account/mfa/enable", data={"code": "000000"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT mfa_enabled FROM proc.app_user WHERE username='enroller'")
        assert cur.fetchone()["mfa_enabled"] is False
