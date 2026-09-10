"""/account/searches — the customer's own saved searches and their alerts.

Everything here was admin-only before, so most of these tests are about the
access model rather than the happy path: what a customer may now write, whose
rows they may write it to, and what stays with an admin.
"""
import pytest

from tests.helpers import connect, expire_sub, get_csrf, grant, login, make_user

PAGE = "/account/searches"


@pytest.fixture()
def clean(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.digest_schedule")
    yield cur


def _profile(cur, *, name, owner=None, scope=None, params=None, published=False,
             created_by=None):
    from app import auth as _auth
    scope = scope or ("customer" if owner else "portal")
    pid = _auth.create_search_profile(
        cur, name=name, scope=scope, owner_id=owner,
        params=params if params is not None else {"q": "καθαριότητα"},
        based_on_id=None, created_by=created_by or owner)
    if published:
        _auth.set_profile_published(cur, pid, True)
    return pid


def _customer(cur, username, email=None, entitled=True):
    from app import auth as _auth
    uid = _auth.create_user(cur, username, "goodpassword1", role="customer",
                            email=email or f"{username}@example.com")["id"]
    if entitled:
        grant(uid)
    return uid


# --------------------------------------------------------------------------- #
# Getting to the page
# --------------------------------------------------------------------------- #
def test_a_signed_out_visitor_is_sent_to_the_login(client):
    r = client.get(PAGE, follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_a_signed_out_post_is_refused_rather_than_redirected(client):
    """A POST with no session is a stale tab or a forgery, not somebody who
    needs the login page."""
    r = client.post(PAGE, data={"name": "x", "params_qs": "q=y"},
                    follow_redirects=False)
    assert r.status_code in (403, 401)


def test_the_page_lists_only_the_customers_own_searches(client, clean):
    cur = clean
    mine = _customer(cur, "as_mine")
    _profile(cur, name="Δική μου", owner=mine)
    other = _customer(cur, "as_other")
    _profile(cur, name="Κάποιου άλλου", owner=other)
    _profile(cur, name="Αδημοσίευτο πύλης", published=False)

    login(client, "as_mine", "goodpassword1")
    body = client.get(PAGE).text
    assert "Δική μου" in body
    assert "Κάποιου άλλου" not in body
    assert "Αδημοσίευτο πύλης" not in body


# --------------------------------------------------------------------------- #
# Creating
# --------------------------------------------------------------------------- #
def test_a_customer_saves_the_filters_they_are_looking_at(client, clean):
    cur = clean
    _customer(cur, "as_save")
    login(client, "as_save", "goodpassword1")
    r = client.post(PAGE, data={"name": "Καθαριότητα Αττικής",
                                "params_qs": "q=καθαριότητα&type=notice",
                                "next": "/?q=καθαριότητα",
                                "csrf_token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    cur.execute("SELECT scope, owner_user_id, params FROM proc.search_profile "
                "WHERE name = 'Καθαριότητα Αττικής'")
    row = cur.fetchone()
    # The endpoint decides the scope and the owner — there is no field for
    # either, which is why it is safe to expose where the admin one was not.
    assert row["scope"] == "customer"
    assert row["params"] == {"q": "καθαριότητα", "type": ["notice"]}


def test_a_saved_search_with_no_filters_is_refused(client, clean):
    """It would match the whole corpus, and an alert on it would mail thousands
    of acts a day."""
    cur = clean
    _customer(cur, "as_nofilter")
    login(client, "as_nofilter", "goodpassword1")
    client.post(PAGE, data={"name": "Τα πάντα", "params_qs": "",
                            "csrf_token": get_csrf(client)},
                follow_redirects=False)
    cur.execute("SELECT count(*) AS n FROM proc.search_profile WHERE name='Τα πάντα'")
    assert cur.fetchone()["n"] == 0


def test_the_saved_search_cap_is_enforced(client, clean, monkeypatch):
    from app import account_searches as acs
    monkeypatch.setattr(acs, "MAX_SAVED_SEARCHES", 2)
    cur = clean
    uid = _customer(cur, "as_cap")
    _profile(cur, name="Α", owner=uid)
    _profile(cur, name="Β", owner=uid)
    login(client, "as_cap", "goodpassword1")
    client.post(PAGE, data={"name": "Γ", "params_qs": "q=x",
                            "csrf_token": get_csrf(client)},
                follow_redirects=False)
    cur.execute("SELECT count(*) AS n FROM proc.search_profile "
                "WHERE owner_user_id = %s", (uid,))
    assert cur.fetchone()["n"] == 2


# --------------------------------------------------------------------------- #
# Renaming and deleting — own rows only
# --------------------------------------------------------------------------- #
def test_a_customer_renames_and_deletes_their_own(client, clean):
    cur = clean
    uid = _customer(cur, "as_edit")
    pid = _profile(cur, name="Παλιό", owner=uid)
    login(client, "as_edit", "goodpassword1")

    client.post(f"{PAGE}/{pid}/rename", data={"name": "Νέο",
                                              "csrf_token": get_csrf(client)},
                follow_redirects=False)
    cur.execute("SELECT name FROM proc.search_profile WHERE id=%s", (pid,))
    assert cur.fetchone()["name"] == "Νέο"

    client.post(f"{PAGE}/{pid}/delete", data={"csrf_token": get_csrf(client)},
                follow_redirects=False)
    cur.execute("SELECT count(*) AS n FROM proc.search_profile WHERE id=%s", (pid,))
    assert cur.fetchone()["n"] == 0


def test_another_customers_search_is_not_found_rather_than_forbidden(client, clean):
    """404, not 403: an id that belongs to someone else must not be
    distinguishable from one that does not exist, or this endpoint becomes a
    way to count other customers' saved searches."""
    cur = clean
    victim = _customer(cur, "as_victim")
    pid = _profile(cur, name="Ξένο", owner=victim)
    _customer(cur, "as_attacker")
    login(client, "as_attacker", "goodpassword1")

    tok = get_csrf(client)
    assert client.post(f"{PAGE}/{pid}/rename", data={"name": "hacked",
                                                     "csrf_token": tok},
                       follow_redirects=False).status_code == 404
    assert client.post(f"{PAGE}/{pid}/delete", data={"csrf_token": tok},
                       follow_redirects=False).status_code == 404
    cur.execute("SELECT name FROM proc.search_profile WHERE id=%s", (pid,))
    assert cur.fetchone()["name"] == "Ξένο"


def test_a_customer_cannot_rename_a_portal_profile_they_are_mailed_about(client,
                                                                          clean):
    """Being mailed about a shared search is not owning it."""
    from app import digests as dg
    cur = clean
    uid = _customer(cur, "as_portal")
    pid = _profile(cur, name="Πύλης", published=True)
    dg.upsert_subscription(cur, user_id=uid, search_profile_id=pid)
    login(client, "as_portal", "goodpassword1")

    assert client.post(f"{PAGE}/{pid}/rename",
                       data={"name": "δικό μου τώρα",
                             "csrf_token": get_csrf(client)},
                       follow_redirects=False).status_code == 404
    # ...but it IS on their page, because they are mailed about it.
    assert "Πύλης" in client.get(PAGE).text


# --------------------------------------------------------------------------- #
# The alert
# --------------------------------------------------------------------------- #
def test_turning_an_alert_on_stores_every_setting(client, clean):
    from app import digests as dg
    cur = clean
    uid = _customer(cur, "as_alert")
    pid = _profile(cur, name="Ειδοποίηση", owner=uid)
    sched = dg.create_schedule(cur, name="Εβδομαδιαία", cadence="weekly",
                               weekday=0, hour=9, minute=0)
    login(client, "as_alert", "goodpassword1")

    r = client.post(f"{PAGE}/{pid}/alert", data={
        "is_active": "1", "layout": "deadline", "schedule_id": str(sched),
        "lang": "en", "max_results": "40", "lead_days": "10, 2",
        "csrf_token": get_csrf(client)}, follow_redirects=False)
    assert r.status_code == 303

    subs = dg.list_subscriptions(cur, user_id=uid)
    assert len(subs) == 1
    sub = subs[0]
    assert sub["layout"] == "deadline" and sub["lang"] == "en"
    assert sub["schedule_id"] == sched and sub["max_results"] == 40
    assert list(sub["lead_days"]) == [10, 2]
    # The account address is the whole point of a self-serve alert, so it is
    # not an option the customer can turn off by accident.
    assert sub["include_primary"] is True


def test_an_alert_can_be_switched_off_and_removed(client, clean):
    from app import digests as dg
    cur = clean
    uid = _customer(cur, "as_off")
    pid = _profile(cur, name="Διακοπή", owner=uid)
    login(client, "as_off", "goodpassword1")
    tok = get_csrf(client)

    client.post(f"{PAGE}/{pid}/alert", data={"is_active": "1", "layout": "list",
                                             "lang": "el", "max_results": "25",
                                             "csrf_token": tok},
                follow_redirects=False)
    # Unticking "Ενεργή" posts nothing for it, which must mean off.
    client.post(f"{PAGE}/{pid}/alert", data={"layout": "list", "lang": "el",
                                             "max_results": "25",
                                             "csrf_token": tok},
                follow_redirects=False)
    assert dg.list_subscriptions(cur, user_id=uid)[0]["is_active"] is False

    client.post(f"{PAGE}/{pid}/alert/delete", data={"csrf_token": tok},
                follow_redirects=False)
    assert dg.list_subscriptions(cur, user_id=uid) == []


def test_a_customer_cannot_subscribe_to_an_unpublished_portal_profile(client,
                                                                       clean):
    """A profile they were never shown must not become mailable by guessing
    its id."""
    from app import digests as dg
    cur = clean
    uid = _customer(cur, "as_secret")
    pid = _profile(cur, name="Κρυφό", published=False)
    login(client, "as_secret", "goodpassword1")

    r = client.post(f"{PAGE}/{pid}/alert",
                    data={"is_active": "1", "layout": "list", "lang": "el",
                          "max_results": "25", "csrf_token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 404
    assert dg.list_subscriptions(cur, user_id=uid) == []


def test_a_customer_cannot_set_an_alert_on_someone_elses_search(client, clean):
    from app import digests as dg
    cur = clean
    victim = _customer(cur, "as_v2")
    pid = _profile(cur, name="Ξένο2", owner=victim)
    _customer(cur, "as_a2")
    login(client, "as_a2", "goodpassword1")

    r = client.post(f"{PAGE}/{pid}/alert",
                    data={"is_active": "1", "layout": "list", "lang": "el",
                          "max_results": "25", "csrf_token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 404
    assert dg.list_subscriptions(cur, user_id=victim) == []


def test_an_inactive_schedule_falls_back_to_the_portal_default(client, clean):
    """Rather than pinning the alert to a cadence nobody runs."""
    from app import digests as dg
    cur = clean
    uid = _customer(cur, "as_sched")
    pid = _profile(cur, name="Πρόγραμμα", owner=uid)
    dg.create_schedule(cur, name="Προεπιλογή", cadence="daily", hour=8,
                       minute=0, is_default=True)
    dead = dg.create_schedule(cur, name="Ανενεργό", cadence="daily", hour=6,
                              minute=0)
    cur.execute("UPDATE proc.digest_schedule SET is_active = false WHERE id = %s",
                (dead,))
    login(client, "as_sched", "goodpassword1")

    client.post(f"{PAGE}/{pid}/alert",
                data={"is_active": "1", "layout": "list", "lang": "el",
                      "max_results": "25", "schedule_id": str(dead),
                      "csrf_token": get_csrf(client)}, follow_redirects=False)
    sub = dg.list_subscriptions(cur, user_id=uid)[0]
    assert sub["schedule_id"] is None
    assert dg.resolve_schedule(cur, sub)["name"] == "Προεπιλογή"


def test_marks_survive_a_save_that_does_not_post_them(client, clean):
    from app import digests as dg
    cur = clean
    uid = _customer(cur, "as_marks")
    pid = _profile(cur, name="Σημεία", owner=uid)
    login(client, "as_marks", "goodpassword1")
    tok = get_csrf(client)

    client.post(f"{PAGE}/{pid}/alert",
                data={"is_active": "1", "layout": "deadline", "lang": "el",
                      "max_results": "25", "lead_days": "21, 5",
                      "csrf_token": tok}, follow_redirects=False)
    client.post(f"{PAGE}/{pid}/alert",
                data={"is_active": "1", "layout": "deadline", "lang": "en",
                      "max_results": "25", "csrf_token": tok},
                follow_redirects=False)
    sub = dg.list_subscriptions(cur, user_id=uid)[0]
    assert sub["lang"] == "en" and list(sub["lead_days"]) == [21, 5]


# --------------------------------------------------------------------------- #
# Entitlement — settings are kept, sending is what stops
# --------------------------------------------------------------------------- #
def test_a_lapsed_customer_still_saves_settings_and_is_told_why(client, clean):
    from app import digests as dg
    cur = clean
    uid = _customer(cur, "as_lapsed")
    expire_sub(uid)
    pid = _profile(cur, name="Ληγμένο", owner=uid)
    login(client, "as_lapsed", "goodpassword1")

    body = client.get(PAGE).text
    assert "δεν έχει ενεργό προϊόν" in body

    client.post(f"{PAGE}/{pid}/alert",
                data={"is_active": "1", "layout": "list", "lang": "el",
                      "max_results": "25", "csrf_token": get_csrf(client)},
                follow_redirects=False)
    # The row exists — it resumes on the next grant...
    assert len(dg.list_subscriptions(cur, user_id=uid)) == 1
    # ...but the sweep will not pick it up in the meantime.
    assert all(s["user_id"] != uid for s in dg.active_subscriptions(cur))


# --------------------------------------------------------------------------- #
# The entry point on the search page
# --------------------------------------------------------------------------- #
def test_the_search_page_offers_a_customer_the_save_popover(client, clean):
    cur = clean
    _customer(cur, "as_popover")
    login(client, "as_popover", "goodpassword1")
    body = client.get("/?q=test").text
    assert 'action="/account/searches"' in body
    # ...and not the admin one, which can create portal profiles and hand a
    # saved search to somebody else.
    assert 'action="/search-profiles"' not in body


def test_an_admin_still_gets_the_admin_popover(client, clean):
    make_user("as_admin_pop", "goodpassword1", role="admin")
    login(client, "as_admin_pop", "goodpassword1")
    body = client.get("/?q=test").text
    assert 'action="/search-profiles"' in body
    assert 'action="/account/searches"' not in body


def test_a_signed_out_visitor_is_offered_neither(client, clean):
    body = client.get("/?q=test").text
    assert 'action="/account/searches"' not in body
    assert 'action="/search-profiles"' not in body
