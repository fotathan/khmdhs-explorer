"""Admin authorization (RBAC) and CSRF protection."""
from tests.helpers import get_csrf, login, make_user


def test_admin_denied_for_anonymous(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code != 200          # redirect to login or 403


def test_admin_denied_for_customer(client):
    make_user("cust", "goodpassword1", role="customer")
    login(client, "cust", "goodpassword1")
    r = client.get("/admin/users", follow_redirects=False)
    assert r.status_code != 200


def test_admin_allowed_for_admin(client):
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")
    r = client.get("/admin/users", follow_redirects=False)
    assert r.status_code == 200


def test_csrf_blocks_unsafe_post_without_token(client):
    make_user("cust", "goodpassword1", role="customer")
    login(client, "cust", "goodpassword1")
    r = client.post("/account/password",
                    data={"current_password": "goodpassword1",
                          "new_password": "newpassword1", "confirm_password": "newpassword1"})
    assert r.status_code == 403          # missing CSRF token


def test_csrf_allows_post_with_token(client):
    make_user("cust", "goodpassword1", role="customer")
    login(client, "cust", "goodpassword1")
    token = get_csrf(client)
    assert token
    r = client.post("/account/password",
                    data={"current_password": "goodpassword1",
                          "new_password": "newpassword1", "confirm_password": "newpassword1"},
                    headers={"X-CSRF-Token": token})
    assert r.status_code == 200
