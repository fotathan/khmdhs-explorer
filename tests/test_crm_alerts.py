"""The customer card's saved searches + result-email settings.

Both moved onto /admin/crm/<uid> because they are per-customer questions: which
saved searches someone has, and which of them they are mailed about. What stays
portal-wide at /admin/digests is the cadences and the run history.

The sending behaviour itself (who is entitled, the window, what one email
contained) lives in tests/test_digests.py; here we care about the page and the
endpoints it posts to.
"""
import datetime as dt

import pytest

from app import auth
from tests.helpers import connect, expire_sub, get_csrf, grant, login, make_user

UTC = dt.timezone.utc


@pytest.fixture()
def alerts(_clean):
    """app_user is truncated per test, which cascades subscriptions and profiles
    away; the schedules and the sample acts are not, so clear those by hand."""
    with connect() as c:
        cur = c.cursor()
        cur.execute("DELETE FROM proc.digest_schedule")
        cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'CRMA%'")
        yield cur
        cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'CRMA%'")


def _admin(client, name="crma_admin"):
    uid = make_user(name, "goodpassword1", role="admin")
    login(client, name, "goodpassword1")
    return uid


def _customer(cur, username="crma_cust", email="crma@example.com"):
    uid = auth.create_user(cur, username, "goodpassword1",
                           role="customer", email=email)["id"]
    grant(uid)
    return uid


def _profile(cur, admin_id, owner_id=None, name="Καθαριότητα", params=None):
    return auth.create_search_profile(
        cur, name=name, scope=("customer" if owner_id else "portal"),
        owner_id=owner_id, params=params if params is not None else {"q": "καθαριότητα"},
        based_on_id=None, created_by=admin_id)


def _act(cur, adam, *, title="Καθαριότητα κτιρίων", ingested_at=None):
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, submission_date,
                      ingested_at)
                   VALUES (%s, 'notice', %s, 'import', 'khmdhs', now(),
                           coalesce(%s, now()))""", (adam, title, ingested_at))


# --------------------------------------------------------------------------- #
# Saved searches on the customer card
# --------------------------------------------------------------------------- #
def test_the_customers_own_saved_searches_are_listed(client, alerts):
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    _profile(cur, admin, owner_id=cust, name="Ασφαλτοστρώσεις",
             params={"q": "άσφαλτος", "cpv": ["45233000-9"]})

    html = client.get(f"/admin/crm/{cust}").text
    assert "Ασφαλτοστρώσεις" in html
    # and the filters are summarised, so two saved searches are distinguishable
    # without opening either.
    assert "άσφαλτος" in html and "45233000-9" in html


def test_someone_elses_saved_search_does_not_appear(client, alerts):
    cur = alerts
    admin = _admin(client)
    mine = _customer(cur, "crma_mine", "mine@example.com")
    theirs = _customer(cur, "crma_theirs", "theirs@example.com")
    _profile(cur, admin, owner_id=theirs, name="ΞένοΠροφίλ")

    assert "ΞένοΠροφίλ" not in client.get(f"/admin/crm/{mine}").text


def test_a_portal_profile_shows_up_once_the_customer_is_mailed_about_it(client,
                                                                        alerts):
    """A portal profile is not "theirs" until an alert points at it — then it is
    as much part of their picture as one they own."""
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    portal = _profile(cur, admin, name="ΠύληΚαθαριότητα")

    # <b>name</b> is the table row; the profile is in the "add an alert" select
    # from the start (any portal profile may be subscribed to), which is not the
    # same as being part of this customer's picture.
    assert "<b>ΠύληΚαθαριότητα</b>" not in client.get(f"/admin/crm/{cust}").text
    dg.upsert_subscription(cur, user_id=cust, search_profile_id=portal)
    assert "<b>ΠύληΚαθαριότητα</b>" in client.get(f"/admin/crm/{cust}").text


# --------------------------------------------------------------------------- #
# The alert settings, now on the customer card
# --------------------------------------------------------------------------- #
def test_an_admin_creates_an_alert_from_the_customer_card(client, alerts):
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    prof = _profile(cur, admin, owner_id=cust)
    dg.create_schedule(cur, name="Προεπιλογή", cadence="daily", hour=8,
                       minute=0, is_default=True)

    r = client.post("/admin/digests/subscriptions",
                    data={"user_id": str(cust), "search_profile_id": str(prof),
                          "lang": "en", "max_results": "10", "is_active": "1",
                          "back": f"/admin/crm/{cust}",
                          "csrf_token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    # `back` returns the admin to the card they were on, not to /admin/digests.
    assert r.headers["location"].startswith(f"/admin/crm/{cust}")

    [sub] = dg.list_subscriptions(cur, user_id=cust)
    assert sub["lang"] == "en" and sub["max_results"] == 10


def test_saving_again_edits_the_same_alert(client, alerts):
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    prof = _profile(cur, admin, owner_id=cust)

    for n in ("10", "40"):
        client.post("/admin/digests/subscriptions",
                    data={"user_id": str(cust), "search_profile_id": str(prof),
                          "lang": "el", "max_results": n, "is_active": "1",
                          "back": f"/admin/crm/{cust}",
                          "csrf_token": get_csrf(client)},
                    follow_redirects=False)
    subs = dg.list_subscriptions(cur, user_id=cust)
    assert len(subs) == 1 and subs[0]["max_results"] == 40


def test_back_only_honours_a_same_site_path(client, alerts):
    """`back` is echoed into a redirect, so it must not become an open redirect."""
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    prof = _profile(cur, admin, owner_id=cust)

    r = client.post("/admin/digests/subscriptions",
                    data={"user_id": str(cust), "search_profile_id": str(prof),
                          "lang": "el", "max_results": "25", "is_active": "1",
                          "back": "//evil.example/steal",
                          "csrf_token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/digests?tab=subscriptions"
    assert dg.list_subscriptions(cur, user_id=cust)      # the save still happened


def test_deleting_an_alert_from_the_card_returns_to_the_card(client, alerts):
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    prof = _profile(cur, admin, owner_id=cust)
    sub_id = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof)

    r = client.post(f"/admin/digests/subscriptions/{sub_id}/delete",
                    data={"back": f"/admin/crm/{cust}",
                          "csrf_token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/admin/crm/{cust}")
    assert dg.list_subscriptions(cur, user_id=cust) == []


def test_a_lapsed_customer_card_says_no_email_will_be_sent(client, alerts):
    """The settings are kept — an admin re-granting the product must not have to
    rebuild them — but the page has to stop looking like it is working."""
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    prof = _profile(cur, admin, owner_id=cust)
    dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof)

    assert "δεν είναι ενεργός δοκιμαστής" not in client.get(f"/admin/crm/{cust}").text
    expire_sub(cust)
    html = client.get(f"/admin/crm/{cust}").text
    assert "δεν είναι ενεργός δοκιμαστής" in html
    assert prof and "Καθαριότητα" in html          # the subscription is still there


def test_the_card_links_to_what_each_send_contained(client, alerts):
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    prof = _profile(cur, admin, owner_id=cust)
    sub_id = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof)
    cur.execute("UPDATE proc.digest_subscription SET created_at = now() - "
                "interval '2 days' WHERE id = %s", (sub_id,))
    _act(cur, "CRMA-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    import os
    os.environ["EMAIL_BACKEND"] = "memory"
    try:
        res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    finally:
        os.environ.pop("EMAIL_BACKEND", None)
    assert res["status"] == "sent"
    assert f"/digests/{res['token']}" in client.get(f"/admin/crm/{cust}").text


# --------------------------------------------------------------------------- #
# The portal-wide page is now read-only
# --------------------------------------------------------------------------- #
def test_the_digests_overview_points_settings_at_the_crm_card(client, alerts):
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    prof = _profile(cur, admin, owner_id=cust)
    dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof)

    html = client.get("/admin/digests?tab=subscriptions").text
    assert f"/admin/crm/{cust}" in html
    # No create/edit form here any more — that is what moved.
    assert 'action="/admin/digests/subscriptions"' not in html


def test_the_overview_flags_a_customer_who_will_not_be_mailed(client, alerts):
    from app import digests as dg
    cur = alerts
    admin = _admin(client)
    cust = _customer(cur)
    prof = _profile(cur, admin, owner_id=cust)
    dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof)
    expire_sub(cust)

    assert "χωρίς ενεργή πρόσβαση" in client.get("/admin/digests?tab=subscriptions").text
