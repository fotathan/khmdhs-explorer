"""Self-service password change and re-auth-before-delete."""
from tests.helpers import connect, get_csrf, login, make_user


def test_password_change_rejects_wrong_current(client):
    make_user("user1", "origpass1234", role="customer")
    login(client, "user1", "origpass1234")
    tok = get_csrf(client)
    r = client.post("/account/password",
                    data={"current_password": "WRONGpass", "new_password": "newpass1234",
                          "confirm_password": "newpass1234"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400


def test_password_change_rejects_mismatch(client):
    make_user("user1", "origpass1234", role="customer")
    login(client, "user1", "origpass1234")
    tok = get_csrf(client)
    r = client.post("/account/password",
                    data={"current_password": "origpass1234", "new_password": "newpass1234",
                          "confirm_password": "DIFFERENT12"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 400


def test_password_change_succeeds_and_takes_effect(client):
    make_user("user1", "origpass1234", role="customer")
    login(client, "user1", "origpass1234")
    tok = get_csrf(client)
    r = client.post("/account/password",
                    data={"current_password": "origpass1234", "new_password": "newpass1234",
                          "confirm_password": "newpass1234"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    # old password no longer works; new one does
    assert login(client, "user1", "origpass1234").status_code == 401
    assert login(client, "user1", "newpass1234").status_code == 303


def test_account_delete_requires_password(client):
    uid = make_user("victim", "origpass1234", role="customer")
    login(client, "victim", "origpass1234")
    tok = get_csrf(client)
    # wrong password → refused, user still present
    r = client.post("/account/delete", data={"confirm": "on", "password": "WRONGpass"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 403
    with connect() as c:
        cur = c.cursor(); cur.execute("SELECT count(*) AS n FROM proc.app_user WHERE id=%s", (uid,))
        assert cur.fetchone()["n"] == 1
    # correct password → deleted (redirect out)
    r = client.post("/account/delete", data={"confirm": "on", "password": "origpass1234"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code in (302, 303)
    with connect() as c:
        cur = c.cursor(); cur.execute("SELECT count(*) AS n FROM proc.app_user WHERE id=%s", (uid,))
        assert cur.fetchone()["n"] == 0
