"""Paywall / freemium teaser across the full tier matrix.

Two layers: (1) has_access derivation in auth.load_user — the signal everything
gates on; (2) the gating actually enforced on the home route, observed via its
JSON response (`gated` flag + the page cap that stops anonymous/expired callers
paging past the first screen of results).
"""
from app import auth
from tests.helpers import connect, expire_sub, grant, login, make_user


def _load(uid):
    with connect() as c:
        return auth.load_user(c.cursor(), uid)


# --- has_access derivation ---
def test_admin_always_has_access(_clean):
    uid = make_user("adm", "goodpassword1", role="admin")
    assert _load(uid)["has_access"] is True


def test_customer_without_subscription_is_gated(_clean):
    uid = make_user("c_nosub", "goodpassword1")
    assert _load(uid)["has_access"] is False


def test_customer_with_active_subscription_has_access(_clean):
    uid = make_user("c_active", "goodpassword1")
    grant(uid, "pro", days=365)
    assert _load(uid)["has_access"] is True


def test_customer_with_expired_subscription_is_gated(_clean):
    uid = make_user("c_exp", "goodpassword1")
    expire_sub(uid, "pro")
    assert _load(uid)["has_access"] is False


def test_inactive_user_does_not_resolve(_clean):
    uid = make_user("c_off", "goodpassword1", active=False)
    assert _load(uid) is None                 # is_active filter → no live session


# --- route enforcement on the home page (JSON: gated flag + page cap) ---
def _home(client, page=2):
    return client.get(f"/?page={page}", headers={"Accept": "application/json"},
                      follow_redirects=False).json()


def test_home_gated_for_anonymous(client):
    j = _home(client)
    assert j["gated"] is True and j["page"] == 1      # capped to page 1


def test_home_gated_for_customer_without_subscription(client):
    make_user("custa", "goodpassword1")
    login(client, "custa", "goodpassword1")
    j = _home(client)
    assert j["gated"] is True and j["page"] == 1


def test_home_gated_for_expired_customer(client):
    uid = make_user("custb", "goodpassword1")
    expire_sub(uid, "pro")
    login(client, "custb", "goodpassword1")
    j = _home(client)
    assert j["gated"] is True and j["page"] == 1


def test_home_full_for_active_customer(client):
    uid = make_user("custc", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "custc", "goodpassword1")
    j = _home(client)
    assert j["gated"] is False and j["page"] == 2     # pagination honoured


def test_home_full_for_admin(client):
    make_user("admn", "goodpassword1", role="admin")
    login(client, "admn", "goodpassword1")
    j = _home(client)
    assert j["gated"] is False and j["page"] == 2
