"""Saved searches in the calendar feed — slice 5 of docs/specs/calendar-feed.md.

A customer ticks a saved search on /account/searches and the deadlines it
matches join their favourites in /calendar/<token>.ics. What is pinned here:

  * who may tick what — the same APPLY rule as the email alert;
  * favourites always go in first; searches fill what is left of
    CALENDAR_MAX_EVENTS, upcoming deadlines (soonest first) before the recent
    past;
  * a search never brings in a cancelled act;
  * an event says which saved search brought it in.

The searches here are ADAM prefixes ("26PROC01995"): build_where turns a q that
looks like an ADAM into a prefix match, which is exact, where a word search
would depend on Greek stemming. Every act the fixture inserts is deleted on
teardown (proc.procurement_act is never truncated). Needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import datetime as dt
import re

import pytest

from tests.helpers import expire_sub, get_csrf, grant, login, make_user

PREFIX = "26PROC01995"
A_SOON = PREFIX + "0001"        # upcoming, 3 days
A_LATER = PREFIX + "0002"       # upcoming, 20 days
A_CANCELLED = PREFIX + "0003"   # upcoming, but cancelled
A_RECENT = PREFIX + "0004"      # closed 4 days ago
A_ANCIENT = PREFIX + "0005"     # closed 90 days ago
OTHER_PREFIX = "26PROC01996"
A_ELSEWHERE = OTHER_PREFIX + "0001"   # upcoming, matched by no search here
ALL = [A_SOON, A_LATER, A_CANCELLED, A_RECENT, A_ANCIENT, A_ELSEWHERE]
NOW = dt.datetime.now(dt.timezone.utc)
PAGE = "/account/searches"


@pytest.fixture()
def acts(db):
    c = db.cursor()
    rows = ((A_SOON, "Προμήθεια, γάντια; τμήμα 1", 3, False),
            (A_LATER, "Υπηρεσίες καθαριότητας", 20, False),
            (A_CANCELLED, "Ακυρώθηκε", 5, True),
            (A_RECENT, "Έληξε πρόσφατα", -4, False),
            (A_ANCIENT, "Έληξε πριν από καιρό", -90, False),
            (A_ELSEWHERE, "Άλλη αναζήτηση", 4, False))
    for adam, title, days, cancelled in rows:
        c.execute("""INSERT INTO proc.procurement_act
                       (adam, type, title, origin, data_source, cancelled,
                        final_submission_date, last_update_date, ingested_at)
                     VALUES (%s, 'notice', %s, 'import', 'khmdhs', %s, %s, %s, now())""",
                  (adam, title, cancelled, NOW + dt.timedelta(days=days),
                   NOW - dt.timedelta(days=30)))
    yield c
    c.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)", (ALL,))


def _member(client, name="calsearch", *, entitled=True, role="customer"):
    uid = make_user(name, role=role)
    if entitled:
        grant(uid)
    login(client, name, "pw-123456")
    return uid


def _profile(db, *, name, owner=None, q=PREFIX, published=False):
    from app import auth as _auth
    c = db.cursor()
    pid = _auth.create_search_profile(
        c, name=name, scope="customer" if owner else "portal", owner_id=owner,
        params={"q": q}, based_on_id=None, created_by=owner)
    if published:
        _auth.set_profile_published(c, pid, True)
    return pid


def _tick(client, pid, on=True):
    data = {"csrf_token": get_csrf(client)}
    if on:
        data["on"] = "1"
    return client.post(f"{PAGE}/{pid}/calendar", data=data, follow_redirects=False)


def _new_link(client):
    r = client.post("/account/calendar/new", data={"csrf_token": get_csrf(client)})
    assert r.status_code == 200, r.status_code
    m = re.search(r'/calendar/([A-Za-z0-9_-]+)\.ics', r.text)
    assert m, "the new URL is not on the page"
    return m.group(1)


def _feed(client, token):
    """Fetch as a calendar server would: no session. Signs the client out, so
    call it after the last signed-in request of a test."""
    client.cookies.clear()
    return client.get(f"/calendar/{token}.ics")


def _logical(body):
    return body.replace("\r\n ", "").rstrip("\r\n").split("\r\n")


def _uids(body):
    return [ln[4:].split("@")[0] for ln in _logical(body) if ln.startswith("UID:")]


def _ticked_days_ago(db, uid, days):
    db.cursor().execute("""UPDATE proc.calendar_search
                           SET created_at = now() - make_interval(days => %s)
                           WHERE user_id = %s""", (days, uid))


def _ticked(db, uid):
    c = db.cursor()
    c.execute("SELECT search_profile_id FROM proc.calendar_search WHERE user_id=%s",
              (uid,))
    return {r["search_profile_id"] for r in c.fetchall()}


# --------------------------------------------------------------------------- #
# Ticking a search
# --------------------------------------------------------------------------- #
def test_a_customer_ticks_and_unticks_their_own_search(acts, client, db):
    uid = _member(client)
    pid = _profile(db, name="Γάντια", owner=uid)
    assert _tick(client, pid).status_code == 303
    assert _ticked(db, uid) == {pid}
    assert "στο ημερολόγιο" in client.get(PAGE).text
    _tick(client, pid)                              # twice is still once
    assert _ticked(db, uid) == {pid}
    assert _tick(client, pid, on=False).status_code == 303
    assert _ticked(db, uid) == set()


def test_a_signed_out_tick_is_refused(client, db):
    assert client.post(f"{PAGE}/1/calendar", data={"on": "1"}).status_code == 403


def test_someone_elses_search_cannot_be_ticked(acts, client, db):
    victim = make_user("calsearch_victim")
    pid = _profile(db, name="Ξένη", owner=victim)
    uid = _member(client)
    assert _tick(client, pid).status_code == 404
    assert _ticked(db, uid) == set()


def test_an_unpublished_portal_search_cannot_be_ticked(acts, client, db):
    pid = _profile(db, name="Κρυφή")
    uid = _member(client)
    assert _tick(client, pid).status_code == 404
    assert _ticked(db, uid) == set()


def test_a_published_portal_search_can_be_ticked_without_an_alert(acts, client, db):
    """Seeing a shared search's deadlines is not editing it — and once ticked
    it stays on the page even with no alert, or there is no box to untick."""
    pid = _profile(db, name="Κοινή της πύλης", published=True)
    uid = _member(client)
    assert _tick(client, pid).status_code == 303
    assert _ticked(db, uid) == {pid}
    assert "Κοινή της πύλης" in client.get(PAGE).text


def test_deleting_the_search_takes_it_out_of_the_calendar(acts, client, db):
    uid = _member(client)
    pid = _profile(db, name="Προσωρινή", owner=uid)
    _tick(client, pid)
    client.post(f"{PAGE}/{pid}/delete", data={"csrf_token": get_csrf(client)},
                follow_redirects=False)
    assert _ticked(db, uid) == set()


# --------------------------------------------------------------------------- #
# What the feed carries
# --------------------------------------------------------------------------- #
def test_a_ticked_search_brings_its_deadlines_into_the_feed(acts, client, db):
    uid = _member(client)
    pid = _profile(db, name="Γάντια & καθαριότητα", owner=uid)
    _tick(client, pid)
    _ticked_days_ago(db, uid, 10)       # A_RECENT closed AFTER the tick
    token = _new_link(client)
    r = _feed(client, token)
    assert r.status_code == 200
    uids = _uids(r.text)
    assert A_SOON in uids and A_LATER in uids
    assert A_RECENT in uids                 # a deadline that just closed stays
    assert A_ANCIENT not in uids
    assert A_ELSEWHERE not in uids          # nothing the search does not match
    # The event says which search brought it in (commas escaped by ics.py).
    assert "Αποθηκευμένη αναζήτηση: Γάντια & καθαριότητα" in r.text.replace("\r\n ", "")


def test_ticking_a_search_does_not_backfill_closed_deadlines(acts, client, db):
    """A search ticked today brings upcoming deadlines only. Its recent past
    would be a month of closed tenders the customer never had in their
    calendar; what stays is only what closed after the tick."""
    uid = _member(client)
    _tick(client, _profile(db, name="Σήμερα", owner=uid))
    token = _new_link(client)
    uids = _uids(_feed(client, token).text)
    assert A_SOON in uids
    assert A_RECENT not in uids


def test_an_unticked_search_brings_nothing(acts, client, db):
    uid = _member(client)
    _profile(db, name="Μη επιλεγμένη", owner=uid)
    token = _new_link(client)
    assert _uids(_feed(client, token).text) == []


def test_a_search_never_brings_in_a_cancelled_act(acts, client, db):
    uid = _member(client)
    _tick(client, _profile(db, name="Όλες", owner=uid))
    token = _new_link(client)
    assert A_CANCELLED not in _uids(_feed(client, token).text)


def test_a_cancelled_favourite_is_still_stated(acts, client, db):
    """The search rule does not weaken slice 4's: a favourite that gets
    cancelled is emitted STATUS:CANCELLED, not dropped."""
    uid = _member(client)
    db.cursor().execute("INSERT INTO proc.user_favorite_act (user_id, adam) "
                        "VALUES (%s, %s)", (uid, A_CANCELLED))
    _tick(client, _profile(db, name="Όλες", owner=uid))
    token = _new_link(client)
    lines = _logical(_feed(client, token).text)
    assert f"UID:{A_CANCELLED}@khmdhs" in lines
    assert "STATUS:CANCELLED" in lines


def test_an_act_in_favourites_and_a_search_is_one_event(acts, client, db):
    uid = _member(client)
    db.cursor().execute("INSERT INTO proc.user_favorite_act (user_id, adam) "
                        "VALUES (%s, %s)", (uid, A_SOON))
    _tick(client, _profile(db, name="Όλες", owner=uid))
    token = _new_link(client)
    assert _uids(_feed(client, token).text).count(A_SOON) == 1


def test_an_act_two_searches_match_is_one_event_naming_both(acts, client, db):
    uid = _member(client)
    _tick(client, _profile(db, name="Πρώτη", owner=uid))
    _tick(client, _profile(db, name="Δεύτερη", owner=uid, q=A_SOON))
    token = _new_link(client)
    body = _feed(client, token).text
    assert _uids(body).count(A_SOON) == 1
    unfolded = body.replace("\r\n ", "")
    assert re.search(r"Αποθηκευμένη αναζήτηση: (Πρώτη\\, Δεύτερη|Δεύτερη\\, Πρώτη)",
                     unfolded)


def test_favourites_always_fit_and_searches_fill_the_rest_soonest_first(
        acts, client, db, monkeypatch):
    from app import calendar_feed
    monkeypatch.setattr(calendar_feed, "CALENDAR_MAX_EVENTS", 2)
    uid = _member(client)
    db.cursor().execute("INSERT INTO proc.user_favorite_act (user_id, adam) "
                        "VALUES (%s, %s)", (uid, A_ELSEWHERE))
    _tick(client, _profile(db, name="Όλες", owner=uid))
    token = _new_link(client)
    uids = _uids(_feed(client, token).text)
    # The favourite, then the ONE soonest upcoming search match. Not the
    # recent past one: upcoming deadlines come first when room is short.
    assert sorted(uids) == sorted([A_ELSEWHERE, A_SOON])


def test_a_search_that_is_no_longer_published_drops_out(acts, client, db):
    from app import auth as _auth
    pid = _profile(db, name="Πύλης", published=True)
    _member(client)
    _tick(client, pid)
    token = _new_link(client)
    assert A_SOON in _uids(_feed(client, token).text)
    _auth.set_profile_published(db.cursor(), pid, False)
    assert _uids(_feed(client, token).text) == []


def test_a_lapsed_customer_gets_the_notice_not_the_search(acts, client, db):
    uid = _member(client)
    _tick(client, _profile(db, name="Όλες", owner=uid))
    token = _new_link(client)
    expire_sub(uid)
    uids = _uids(_feed(client, token).text)
    assert uids == [f"lapsed-{uid}"]


def test_an_unchanged_feed_with_searches_is_the_same_bytes(acts, client, db):
    uid = _member(client)
    _tick(client, _profile(db, name="Όλες", owner=uid))
    token = _new_link(client)
    first, second = _feed(client, token), _feed(client, token)
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]


# --------------------------------------------------------------------------- #
# /account/calendar
# --------------------------------------------------------------------------- #
def test_the_calendar_page_lists_ticked_searches_with_their_count(acts, client, db):
    uid = _member(client)
    _tick(client, _profile(db, name="Γάντια μόνο", owner=uid, q=A_SOON))
    text = client.get("/account/calendar").text
    assert "Γάντια μόνο" in text
    assert "<strong>1</strong>" in text


def test_the_calendar_page_warns_when_the_cap_binds(acts, client, db, monkeypatch):
    from app import calendar_feed
    monkeypatch.setattr(calendar_feed, "CALENDAR_MAX_EVENTS", 1)
    uid = _member(client)
    _tick(client, _profile(db, name="Όλες", owner=uid))
    assert "Το ημερολόγιο χωράει έως 1 προθεσμίες" in client.get("/account/calendar").text
