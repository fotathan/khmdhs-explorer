"""Login success/failure and the DB-backed brute-force throttle."""
from tests.helpers import login, make_user


def test_login_success_redirects(client):
    make_user("alice", "goodpassword1")
    r = login(client, "alice", "goodpassword1")
    assert r.status_code == 303          # success → redirect


def test_login_wrong_password_401(client):
    make_user("alice", "goodpassword1")
    r = login(client, "alice", "wrongpassword")
    assert r.status_code == 401


def test_login_unknown_user_401(client):
    r = login(client, "nobody", "whatever12")
    assert r.status_code == 401


def test_throttle_locks_after_max_fails(client):
    make_user("bob", "goodpassword1")
    # _MAX_FAILS = 8: attempts 1..8 return 401, the 9th is locked out (429).
    for _ in range(8):
        assert login(client, "bob", "wrongpassword").status_code == 401
    assert login(client, "bob", "wrongpassword").status_code == 429
