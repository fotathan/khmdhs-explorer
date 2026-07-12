"""Admin-issued temporary passwords + mandatory change on next login.

An admin issues a one-time temp password; the user is then walled off to the
force-change page until they set their own password (no email provider needed).
"""
import re

from app import auth
from tests.helpers import connect, get_csrf, login, logout, make_user


def _csrf_from(client, path):
    """Read the CSRF meta from a specific page (during forced-change, / and
    /account redirect away, so get_csrf() can't reach the token)."""
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"',
                  client.get(path, follow_redirects=False).text)
    return m.group(1) if m else None


def _flag(uid):
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT must_change_password AS f, session_version AS sv "
                    "FROM proc.app_user WHERE id = %s", (uid,))
        return cur.fetchone()


# --- unit (request _clean for a truncated app_user per test) ---
def test_set_password_must_change_sets_flag_and_bumps_version(_clean):
    uid = make_user("tempu", "goodpassword1")
    before = _flag(uid)
    with connect() as c:
        auth.set_password(c.cursor(), uid, "Temp-abcdefgh", must_change=True)
    after = _flag(uid)
    assert after["f"] is True
    assert after["sv"] == before["sv"] + 1


def test_self_service_password_clears_flag(_clean):
    uid = make_user("tempu2", "goodpassword1")
    with connect() as c:
        auth.set_password(c.cursor(), uid, "Temp-abcdefgh", must_change=True)
    assert _flag(uid)["f"] is True
    with connect() as c:                      # a normal set clears it
        auth.set_password(c.cursor(), uid, "newpassword1")
    assert _flag(uid)["f"] is False


def test_gen_temp_password_is_valid():
    p = auth.gen_temp_password()
    assert p.startswith("Temp-")
    assert auth.password_ok(p)


# --- integration: admin issues, user is forced to change ---
def test_admin_issue_temp_then_forced_change_flow(client):
    make_user("bossx", "goodpassword1", role="admin")
    cust_id = make_user("custx", "goodpassword1", role="customer")

    # admin issues a temp password for the customer
    login(client, "bossx", "goodpassword1")
    tok = get_csrf(client)
    r = client.post(f"/admin/users/{cust_id}/temp-password",
                    data={"back": "/admin/users"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 200
    m = re.search(r'class="tp-pass"[^>]*value="(Temp-[^"]+)"', r.text)
    assert m, "temp password should be shown once"
    temp = m.group(1)
    assert _flag(cust_id)["f"] is True
    logout(client)

    # customer logs in with the temp password, then is walled off to the change page
    assert login(client, "custx", temp).status_code == 303
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/force-password"
    # even the account page is off-limits until they change
    assert client.get("/account", follow_redirects=False).headers["location"] == "/account/force-password"

    # set a real password -> flag clears, normal access restored
    tok = _csrf_from(client, "/account/force-password")
    r = client.post("/account/force-password",
                    data={"new_password": "myrealpass1", "confirm_password": "myrealpass1"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert _flag(cust_id)["f"] is False
    assert client.get("/", follow_redirects=False).status_code == 200

    # the temp password no longer works; the new one does
    logout(client)
    assert login(client, "custx", temp).status_code == 401
    assert login(client, "custx", "myrealpass1").status_code == 303


def test_force_password_rejects_mismatch(client):
    uid = make_user("mismatchu", "goodpassword1", role="customer")
    with connect() as c:
        auth.set_password(c.cursor(), uid, "Temp-abcdefgh", must_change=True)
    login(client, "mismatchu", "Temp-abcdefgh")
    tok = _csrf_from(client, "/account/force-password")
    r = client.post("/account/force-password",
                    data={"new_password": "onepassword1", "confirm_password": "different1"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 400
    assert _flag(uid)["f"] is True     # still forced
