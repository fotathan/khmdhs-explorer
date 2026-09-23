"""/calendar/<token>.ics and /account/calendar — slice 4 of
docs/specs/calendar-feed.md.

The emitter is covered by tests/test_ics.py, one act as one event by
tests/test_act_calendar_ics.py. What is pinned here is what the FEED decides:
the URL is the credential and is never stored, who gets what (including the
lapsed customer's 200), that a poll of an unchanged feed is a 304, and that it
never reaches a crawler. Needs TEST_DATABASE_URL.

Every act a fixture inserts is deleted on teardown: proc.procurement_act is not
truncated between tests, and a leftover row breaks unrelated tests later on.
"""
from __future__ import annotations

import datetime as dt
import re

import pytest

from tests.helpers import get_csrf, grant, login, make_user

A_OPEN = "26PROC019940001"
A_OTHER = "26PROC019940002"
A_OLD = "26PROC019940003"
A_ANCIENT = "26PROC019940004"
ALL = [A_OPEN, A_OTHER, A_OLD, A_ANCIENT]
NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture()
def acts(db):
    c = db.cursor()
    rows = ((A_OPEN, "Προμήθεια γαντιών", NOW + dt.timedelta(days=10)),
            (A_OTHER, "Υπηρεσίες καθαριότητας", NOW + dt.timedelta(days=12)),
            (A_OLD, "Πρόσφατα έληξε", NOW - dt.timedelta(days=5)),
            (A_ANCIENT, "Έληξε πριν από καιρό", NOW - dt.timedelta(days=90)))
    for adam, title, deadline in rows:
        c.execute("""INSERT INTO proc.procurement_act
                       (adam, type, title, origin, data_source,
                        final_submission_date, last_update_date, ingested_at)
                     VALUES (%s, 'notice', %s, 'import', 'khmdhs', %s, %s, now())""",
                  (adam, title, deadline, NOW - dt.timedelta(days=30)))
    yield c
    c.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)", (ALL,))


def _member(client, name="calfeed", *, entitled=True):
    uid = make_user(name)
    if entitled:
        grant(uid)
    login(client, name, "pw-123456")
    return uid


def _fav(db, uid, *adams):
    c = db.cursor()
    for adam in adams:
        c.execute("INSERT INTO proc.user_favorite_act (user_id, adam) VALUES (%s, %s)",
                  (uid, adam))


def _new_link(client):
    """Create the link through the page, the way a customer does; return the
    raw token read back out of the one response that shows it."""
    r = client.post("/account/calendar/new",
                    data={"csrf_token": get_csrf(client)})
    assert r.status_code == 200, r.status_code
    m = re.search(r'value="(https?://[^"]+/calendar/([A-Za-z0-9_-]+)\.ics)"', r.text)
    assert m, "the new URL is not on the page"
    return m.group(2), r


def _get(client, token, **kw):
    client.cookies.clear()          # a calendar server has no session
    return client.get(f"/calendar/{token}.ics", **kw)


def _logical(body):
    return body.replace("\r\n ", "").rstrip("\r\n").split("\r\n")


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #
def test_the_page_sends_a_signed_out_visitor_to_login(client):
    r = client.get("/account/calendar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/account/calendar"


def test_a_signed_out_create_is_refused(client):
    assert client.post("/account/calendar/new").status_code == 403


def test_the_account_page_links_to_it(client):
    _member(client)
    assert 'href="/account/calendar"' in client.get("/account").text


def test_the_new_url_is_shown_once_and_never_stored(acts, client, db):
    uid = _member(client)
    token, r = _new_link(client)
    assert r.headers["cache-control"] == "no-store"
    c = db.cursor()
    c.execute("SELECT token_hash FROM proc.calendar_feed WHERE user_id=%s", (uid,))
    stored = c.fetchone()["token_hash"]
    assert stored != token and token not in stored          # a hash, not the URL
    # ...and a later visit to the page cannot show it again.
    assert token not in client.get("/account/calendar").text


def test_the_page_offers_a_webcal_link(acts, client):
    _member(client)
    token, r = _new_link(client)
    assert f"webcal://testserver/calendar/{token}.ics" in r.text


# --------------------------------------------------------------------------- #
# The feed: who gets what
# --------------------------------------------------------------------------- #
def test_the_feed_carries_the_favourites_and_nothing_else(acts, client, db):
    uid = _member(client)
    _fav(db, uid, A_OPEN)
    token, _ = _new_link(client)
    r = _get(client, token)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    lines = _logical(r.text)
    assert f"UID:{A_OPEN}@khmdhs" in lines
    assert f"UID:{A_OTHER}@khmdhs" not in lines


def test_a_malformed_token_is_404_without_touching_the_database(client, monkeypatch):
    from app import calendar_feed

    def boom(*a, **k):
        raise AssertionError("looked up a token that cannot be one")
    monkeypatch.setattr(calendar_feed, "lookup", boom)
    assert client.get("/calendar/not!a*token.ics").status_code == 404
    assert client.get("/calendar/short.ics").status_code == 404


def test_an_unknown_token_is_404(client):
    assert client.get("/calendar/" + "A" * 43 + ".ics").status_code == 404


def test_nobody_reads_anyone_elses_favourites(acts, client, db):
    one = _member(client, "calone")
    _fav(db, one, A_OPEN)
    client.cookies.clear()
    two = _member(client, "caltwo")
    _fav(db, two, A_OTHER)
    token_two, _ = _new_link(client)
    lines = _logical(_get(client, token_two).text)
    assert f"UID:{A_OTHER}@khmdhs" in lines
    assert f"UID:{A_OPEN}@khmdhs" not in lines


def test_a_lapsed_customer_gets_200_and_a_notice_not_a_403(acts, client, db):
    """A 403 makes Google disable the subscription for good. The lapsed feed
    is a valid calendar with one all-day notice, and no deadlines."""
    uid = _member(client, "callapsed", entitled=False)
    _fav(db, uid, A_OPEN)
    token, _ = _new_link(client)
    r = _get(client, token)
    assert r.status_code == 200
    lines = _logical(r.text)
    assert f"UID:{A_OPEN}@khmdhs" not in lines
    assert f"UID:lapsed-{uid}@khmdhs" in lines
    assert any(ln.startswith("DTSTART;VALUE=DATE:") for ln in lines)


def test_renewing_brings_the_deadlines_back_on_the_next_poll(acts, client, db):
    uid = _member(client, "calrenew", entitled=False)
    _fav(db, uid, A_OPEN)
    token, _ = _new_link(client)
    assert f"UID:{A_OPEN}@khmdhs" not in _get(client, token).text
    grant(uid)
    assert f"UID:{A_OPEN}@khmdhs" in _logical(_get(client, token).text)


def test_a_deactivated_account_is_404(acts, client, db):
    from app import auth
    uid = _member(client)
    token, _ = _new_link(client)
    auth.set_active(db.cursor(), uid, False)
    assert _get(client, token).status_code == 404


def test_a_password_change_does_not_kill_the_feed(acts, client, db):
    """It cannot sign anyone in; killing it would silently empty the
    customer's calendar weeks later (spec §3c)."""
    from app import auth
    uid = _member(client)
    token, _ = _new_link(client)
    auth.set_password(db.cursor(), uid, "a-brand-new-password-1")
    assert _get(client, token).status_code == 200


# --------------------------------------------------------------------------- #
# New link, turn off
# --------------------------------------------------------------------------- #
def test_a_new_link_kills_the_old_one(acts, client, db):
    uid = _member(client)
    old, _ = _new_link(client)
    login(client, "calfeed", "pw-123456")
    new, _ = _new_link(client)
    assert old != new
    assert _get(client, old).status_code == 404
    assert _get(client, new).status_code == 200
    c = db.cursor()
    c.execute("SELECT count(*) AS n FROM proc.calendar_feed WHERE user_id=%s", (uid,))
    assert c.fetchone()["n"] == 1                    # one live URL, always


def test_turning_it_off_kills_the_url(acts, client, db):
    uid = _member(client)
    token, _ = _new_link(client)
    r = client.post("/account/calendar/off",
                    data={"csrf_token": get_csrf(client)}, follow_redirects=False)
    assert r.status_code == 303
    assert _get(client, token).status_code == 404
    c = db.cursor()
    c.execute("SELECT 1 FROM proc.calendar_feed WHERE user_id=%s", (uid,))
    assert c.fetchone() is None


def test_each_fetch_is_counted(acts, client, db):
    uid = _member(client)
    token, _ = _new_link(client)
    _get(client, token)
    _get(client, token)
    c = db.cursor()
    c.execute("SELECT fetch_count, last_fetch FROM proc.calendar_feed WHERE user_id=%s",
              (uid,))
    row = c.fetchone()
    assert row["fetch_count"] == 2 and row["last_fetch"] is not None


# --------------------------------------------------------------------------- #
# Polling: deterministic body, ETag, 304
# --------------------------------------------------------------------------- #
def test_an_unchanged_feed_renders_to_the_same_bytes(acts, client, db):
    """DTSTAMP comes from the data, not the clock — otherwise no poll could
    ever be a 304."""
    uid = _member(client)
    _fav(db, uid, A_OPEN)
    token, _ = _new_link(client)
    one, two = _get(client, token), _get(client, token)
    assert one.content == two.content
    assert one.headers["etag"] == two.headers["etag"]


def test_a_matching_etag_is_a_304(acts, client, db):
    uid = _member(client)
    _fav(db, uid, A_OPEN)
    token, _ = _new_link(client)
    etag = _get(client, token).headers["etag"]
    r = _get(client, token, headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag


def test_a_change_breaks_the_etag(acts, client, db):
    uid = _member(client)
    _fav(db, uid, A_OPEN)
    token, _ = _new_link(client)
    etag = _get(client, token).headers["etag"]
    _fav(db, uid, A_OTHER)
    r = _get(client, token, headers={"If-None-Match": etag})
    assert r.status_code == 200
    assert f"UID:{A_OTHER}@khmdhs" in _logical(r.text)


# --------------------------------------------------------------------------- #
# What goes in
# --------------------------------------------------------------------------- #
def test_recent_past_deadlines_stay_and_old_ones_go(acts, client, db):
    """A deadline that just passed must not vanish from the customer's
    calendar the next morning; one from three months ago can."""
    uid = _member(client)
    _fav(db, uid, A_OLD, A_ANCIENT)
    token, _ = _new_link(client)
    lines = _logical(_get(client, token).text)
    assert f"UID:{A_OLD}@khmdhs" in lines
    assert f"UID:{A_ANCIENT}@khmdhs" not in lines


def test_a_cancelled_favourite_is_stated_not_dropped(acts, client, db):
    uid = _member(client)
    _fav(db, uid, A_OPEN)
    acts.execute("UPDATE proc.procurement_act SET cancelled=true WHERE adam=%s",
                 (A_OPEN,))
    token, _ = _new_link(client)
    lines = _logical(_get(client, token).text)
    assert f"UID:{A_OPEN}@khmdhs" in lines
    assert "STATUS:CANCELLED" in lines


def test_reminders_follow_the_customers_own_deadline_alert(acts, client, db):
    from app import auth as _auth
    from app import digests as dg
    uid = _member(client)
    _fav(db, uid, A_OPEN)
    c = db.cursor()
    admin = make_user("calfeed_admin", role="admin")
    prof = _auth.create_search_profile(
        c, name="Γάντια", scope="portal", owner_id=None, params={"q": "γάντια"},
        based_on_id=None, created_by=admin)
    c.execute("SELECT 1 FROM proc.digest_schedule WHERE is_default")
    if not c.fetchone():
        dg.create_schedule(c, name="Προεπιλογή", cadence="daily", hour=8,
                           minute=0, is_default=True)
    dg.upsert_subscription(c, user_id=uid, search_profile_id=prof,
                           layout="deadline", lead_days=[3])
    token, _ = _new_link(client)
    lines = _logical(_get(client, token).text)
    assert "TRIGGER:-P3D" in lines
    assert "TRIGGER:-P7D" not in lines


def test_without_an_alert_the_digest_default_marks_apply(acts, client, db):
    from app import digests as dg
    uid = _member(client)
    _fav(db, uid, A_OPEN)
    token, _ = _new_link(client)
    lines = _logical(_get(client, token).text)
    for days in dg.DEFAULT_LEAD_DAYS:
        assert f"TRIGGER:-P{days}D" in lines


def test_the_language_is_fixed_when_the_link_is_made(acts, client, db):
    """The calendar server has no language cookie — the link carries it."""
    uid = _member(client)
    _fav(db, uid, A_OPEN)
    client.cookies.set("lang", "en")
    token, _ = _new_link(client)
    body = _get(client, token).text           # _get clears the cookie
    assert "Submission deadline" in body
    assert "Λήξη υποβολής" not in body


# --------------------------------------------------------------------------- #
# Never indexed, never leaked
# --------------------------------------------------------------------------- #
def test_the_feed_is_marked_noindex_and_no_referrer(acts, client):
    _member(client)
    token, _ = _new_link(client)
    r = _get(client, token)
    assert "noindex" in r.headers["x-robots-tag"]
    assert r.headers["referrer-policy"] == "no-referrer"


def test_calendar_is_on_both_seo_lists():
    from app import seo
    assert "/calendar" in seo._NOINDEX_PREFIXES
    assert "/calendar/" in seo._DISALLOW_PATHS


def test_robots_txt_disallows_it_when_indexing_is_on(client, monkeypatch):
    monkeypatch.setenv("SEO_INDEX", "1")
    assert "Disallow: /calendar/" in client.get("/robots.txt").text
