"""Saved searches ("search profiles"): querystring (de)serialization, live-link
resolution, the save/apply routes, and access control."""
from tests.helpers import connect, get_csrf, login, make_user


# --------------------------------------------------------------------------- #
# pure helpers (no DB)
# --------------------------------------------------------------------------- #
def test_params_qs_roundtrip():
    from app.search_profiles import params_from_qs, params_to_qs
    params = {"q": "καθαριότητα", "type": ["notice", "contract"],
              "value_min": "100", "cpv": ["331", "45"]}
    qs = params_to_qs(params)
    assert params_from_qs(qs) == params


def test_params_from_qs_drops_unknown_and_blank():
    from app.search_profiles import params_from_qs
    out = params_from_qs("q=x&junk=y&type=&type=notice&password=secret")
    assert out == {"q": "x", "type": ["notice"]}
    assert "junk" not in out and "password" not in out


# --------------------------------------------------------------------------- #
# effective_params — the LIVE link
# --------------------------------------------------------------------------- #
def test_effective_params_follows_live_link(db):
    from app import auth as _auth
    admin = make_user("sp_admin_el", "goodpassword1", role="admin")
    cust = make_user("sp_cust_el", "goodpassword1", role="customer")
    cur = db.cursor()
    portal = _auth.create_search_profile(
        cur, name="P", scope="portal", owner_id=None,
        params={"q": "νερό", "type": ["notice"]}, based_on_id=None, created_by=admin)
    ref = _auth.create_search_profile(
        cur, name="C", scope="customer", owner_id=cust,
        params=None, based_on_id=portal, created_by=admin)

    prof = _auth.get_search_profile(cur, ref)
    assert _auth.effective_params(cur, prof) == {"q": "νερό", "type": ["notice"]}

    # editing the portal profile propagates through the live link
    _auth.update_search_profile(cur, portal, name="P", params={"q": "φως"},
                                based_on_id=None, is_published=False)
    prof = _auth.get_search_profile(cur, ref)
    assert _auth.effective_params(cur, prof) == {"q": "φως"}


def test_own_params_override_reference(db):
    from app import auth as _auth
    admin = make_user("sp_admin_ov", "goodpassword1", role="admin")
    cust = make_user("sp_cust_ov", "goodpassword1", role="customer")
    cur = db.cursor()
    portal = _auth.create_search_profile(
        cur, name="P", scope="portal", owner_id=None,
        params={"q": "base"}, based_on_id=None, created_by=admin)
    own = _auth.create_search_profile(
        cur, name="C", scope="customer", owner_id=cust,
        params={"q": "override"}, based_on_id=portal, created_by=admin)
    prof = _auth.get_search_profile(cur, own)
    assert _auth.effective_params(cur, prof) == {"q": "override"}


# --------------------------------------------------------------------------- #
# routes: save + apply
# --------------------------------------------------------------------------- #
def test_admin_can_save_and_apply(client):
    make_user("sp_saver", "goodpassword1", role="admin")
    login(client, "sp_saver", "goodpassword1")
    tok = get_csrf(client)
    r = client.post("/search-profiles",
                    data={"name": "Καθ.", "scope": "portal",
                          "params_qs": "q=test&type=notice", "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 303

    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT id FROM proc.search_profile WHERE name='Καθ.'")
        pid = cur.fetchone()["id"]

    r = client.get(f"/search-profiles/{pid}/apply", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "q=test" in loc and "type=notice" in loc
    # applying tags the redirect with _sp so the search page can badge the profile
    assert "_sp=" in loc


def test_applied_profile_badge_and_clear_button(client):
    """The search page shows a badge for the applied profile (_sp) and ships the
    prominent clear-filters button + nav-loading hooks."""
    make_user("sp_badge", "goodpassword1", role="admin")
    login(client, "sp_badge", "goodpassword1")
    html = client.get("/?type=notice&_sp=My%20Profile", follow_redirects=False).text
    assert "sp-active-badge" in html and "My Profile" in html   # the badge
    assert "clear-btn" in html                                  # prominent clear
    assert "js-nav-loading" in html                             # apply/clear loading
    assert "spDismissProfile" in html                           # dismiss handler


def test_save_form_syncs_live_url_js(client):
    """The save form must read the live URL (window.location.search) into
    params_qs on submit — otherwise filters added via the panel after page load
    (CPV, categories, …) are lost. Guard the client-side sync ships."""
    make_user("sp_livejs", "goodpassword1", role="admin")
    login(client, "sp_livejs", "goodpassword1")
    html = client.get("/", follow_redirects=False).text
    assert "window.location.search" in html
    assert "input[name=params_qs]" in html


def test_save_captures_cpv_and_cat(client):
    """End-to-end: a profile saved with CPV + category params round-trips."""
    make_user("sp_cpvcat", "goodpassword1", role="admin")
    login(client, "sp_cpvcat", "goodpassword1")
    tok = get_csrf(client)
    client.post("/search-profiles",
                data={"name": "cpvcat", "scope": "portal",
                      "params_qs": "type=notice&cpv=33100000-1&cat=health&nuts=EL30",
                      "csrf_token": tok},
                follow_redirects=False)
    from app import auth as _auth
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT params FROM proc.search_profile WHERE name='cpvcat'")
        params = cur.fetchone()["params"]
    assert params["cpv"] == ["33100000-1"]
    assert params["cat"] == ["health"]
    assert params["type"] == ["notice"] and params["nuts"] == ["EL30"]


def test_save_requires_filters_or_reference(client):
    make_user("sp_empty", "goodpassword1", role="admin")
    login(client, "sp_empty", "goodpassword1")
    tok = get_csrf(client)
    r = client.post("/search-profiles",
                    data={"name": "empty", "scope": "portal", "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# access control
# --------------------------------------------------------------------------- #
def test_customer_cannot_create(client):
    make_user("sp_notadmin", "goodpassword1", role="customer")
    login(client, "sp_notadmin", "goodpassword1")
    tok = get_csrf(client)
    r = client.post("/search-profiles",
                    data={"name": "x", "scope": "portal",
                          "params_qs": "q=a", "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 403


def test_customer_cannot_apply_others_profile(client, db):
    from app import auth as _auth
    admin = make_user("sp_admin2", "goodpassword1", role="admin")
    owner = make_user("sp_owner2", "goodpassword1", role="customer")
    other = make_user("sp_other2", "goodpassword1", role="customer")
    cur = db.cursor()
    pid = _auth.create_search_profile(
        cur, name="private", scope="customer", owner_id=owner,
        params={"q": "secret"}, based_on_id=None, created_by=admin)

    login(client, "sp_other2", "goodpassword1")
    r = client.get(f"/search-profiles/{pid}/apply", follow_redirects=False)
    assert r.status_code == 403


def test_customer_sees_only_published_portal_and_own(db):
    from app import auth as _auth
    admin = make_user("sp_admin3", "goodpassword1", role="admin")
    owner = make_user("sp_owner3", "goodpassword1", role="customer")
    cur = db.cursor()
    pub = _auth.create_search_profile(
        cur, name="pub", scope="portal", owner_id=None,
        params={"q": "1"}, based_on_id=None, created_by=admin)
    _auth.set_profile_published(cur, pub, True)
    _auth.create_search_profile(
        cur, name="hidden", scope="portal", owner_id=None,
        params={"q": "2"}, based_on_id=None, created_by=admin)
    _auth.create_search_profile(
        cur, name="mine", scope="customer", owner_id=owner,
        params={"q": "3"}, based_on_id=None, created_by=admin)

    user = {"id": owner, "role": "customer"}
    names = {p["name"] for p in _auth.profiles_for_user(cur, user)}
    assert "pub" in names and "mine" in names and "hidden" not in names
