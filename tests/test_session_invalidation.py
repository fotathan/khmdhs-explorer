"""Server-side session invalidation via app_user.session_version.

Changing a credential/privilege bumps session_version; the auth middleware
compares the cookie's stamped version to the DB on every request, so lingering
cookies stop working — except the actor's own session on a self-service change,
which re-stamps itself and stays valid.
"""
from app import auth
from tests.helpers import connect, get_csrf, login, make_user


def _sv(uid):
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT session_version FROM proc.app_user WHERE id = %s", (uid,))
        return cur.fetchone()["session_version"]


def _bump_directly(uid):
    """Simulate a credential change happening in ANOTHER session/process."""
    with connect() as c:
        c.cursor().execute(
            "UPDATE proc.app_user SET session_version = session_version + 1 "
            "WHERE id = %s", (uid,))


# --- unit: the mutating helpers bump the version ---
def test_set_password_bumps_version(_clean):
    uid = make_user("pwbump", "goodpassword1")
    before = _sv(uid)
    with connect() as c:
        auth.set_password(c.cursor(), uid, "anotherpass1")
    assert _sv(uid) == before + 1


def test_set_role_bumps_version(_clean):
    uid = make_user("rolebump", "goodpassword1")
    before = _sv(uid)
    with connect() as c:
        auth.set_role(c.cursor(), uid, "admin")
    assert _sv(uid) == before + 1


def test_mfa_toggle_bumps_version(_clean):
    uid = make_user("mfabump", "goodpassword1")
    before = _sv(uid)
    secret = auth.new_totp_secret()
    _, hashed = auth.gen_recovery_codes()
    with connect() as c:
        auth.enable_mfa(c.cursor(), uid, secret, hashed)
    after_enable = _sv(uid)
    assert after_enable == before + 1
    with connect() as c:
        auth.disable_mfa(c.cursor(), uid)
    assert _sv(uid) == after_enable + 1


def test_recovery_codes_are_high_entropy():
    plain, _ = auth.gen_recovery_codes(3)
    # 5 hyphen-separated groups of 2 hex bytes = 80 bits, e.g. a1b2-c3d4-...-...-...
    for code in plain:
        groups = code.split("-")
        assert len(groups) == 5
        assert all(len(g) == 4 for g in groups)
        assert len(code.replace("-", "")) == 20   # 20 hex chars = 80 bits


# --- integration: a bumped version logs the lingering cookie out ---
def test_bumped_version_invalidates_active_session(client):
    uid = make_user("staleadmin", "goodpassword1", role="admin")
    assert login(client, "staleadmin", "goodpassword1").status_code == 303
    # session works
    assert client.get("/admin/users", follow_redirects=False).status_code == 200
    # a credential change elsewhere bumps the version...
    _bump_directly(uid)
    # ...and the still-held cookie is now rejected (redirected to login)
    assert client.get("/admin/users", follow_redirects=False).status_code == 303


def test_self_service_password_change_keeps_own_session(client):
    make_user("selfchg", "goodpassword1", role="admin")
    assert login(client, "selfchg", "goodpassword1").status_code == 303
    tok = get_csrf(client)
    r = client.post("/account/password",
                    data={"current_password": "goodpassword1",
                          "new_password": "brandnewpass1",
                          "confirm_password": "brandnewpass1"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 200
    # the acting session survived its own password change
    assert client.get("/admin/users", follow_redirects=False).status_code == 200
