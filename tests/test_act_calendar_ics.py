"""/act/<adam>/calendar.ics — slice 2 of docs/specs/calendar-feed.md.

The emitter itself is covered by tests/test_ics.py without a database. What is
checked here is only what the ROUTE decides: who may ask, what goes into the one
event, and that the act page offers it. Needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tests.helpers import grant, login, make_user

ADAM = "26PROC019920001"
NO_DEADLINE = "26SYMV019920002"
DEADLINE = dt.datetime(2026, 4, 14, 20, 59, tzinfo=dt.timezone.utc)
UPDATED = dt.datetime(2026, 4, 1, 10, 0, tzinfo=dt.timezone.utc)


@pytest.fixture()
def acts(db):
    c = db.cursor()
    c.execute("""INSERT INTO proc.authority (org_id, name)
                 VALUES ('ORG-CAL-1', 'Γενικό Νοσοκομείο Θεσσαλονίκης')
                 ON CONFLICT (org_id) DO NOTHING""")
    c.execute("""INSERT INTO proc.procurement_act
                   (adam, type, title, origin, data_source, authority_id,
                    final_submission_date, submission_date, total_cost_with_vat,
                    last_update_date, ingested_at)
                 VALUES (%s, 'notice', %s, 'import', 'khmdhs', 'ORG-CAL-1',
                         %s, %s, %s, %s, now())""",
              (ADAM, "Προμήθεια ειδών, 3 τμήματα", DEADLINE,
               DEADLINE - dt.timedelta(days=20), Decimal("1250000"), UPDATED))
    c.execute("""INSERT INTO proc.procurement_act
                   (adam, type, title, origin, data_source, ingested_at)
                 VALUES (%s, 'contract', 'Σύμβαση χωρίς προθεσμία',
                         'import', 'khmdhs', now())""", (NO_DEADLINE,))
    yield c
    c.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)",
              ([ADAM, NO_DEADLINE],))
    c.execute("DELETE FROM proc.authority WHERE org_id = 'ORG-CAL-1'")


def _member(client, name="calcust"):
    uid = make_user(name)
    grant(uid)
    login(client, name, "pw-123456")
    return uid


def _body(client, adam=ADAM):
    r = client.get(f"/act/{adam}/calendar.ics")
    assert r.status_code == 200, r.status_code
    return r, r.content.decode("utf-8")


def _logical(body):
    return body.replace("\r\n ", "").rstrip("\r\n").split("\r\n")


# --------------------------------------------------------------------------- #
# Who may ask
# --------------------------------------------------------------------------- #
def test_anonymous_is_sent_to_login_not_served(acts, client):
    """Anti-scrape, the same bargain /export/acts strikes. Nothing in the file
    is secret — it is the redirect that keeps a crawler out of the endpoint."""
    r = client.get(f"/act/{ADAM}/calendar.ics", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")


def test_a_signed_in_customer_is_served(acts, client):
    _member(client)
    r, body = _body(client)
    assert r.headers["content-type"].startswith("text/calendar")
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.endswith("END:VCALENDAR\r\n")


def test_a_lapsed_customer_may_still_use_a_url_they_kept(acts, client):
    """The route gate is 'signed in', not 'entitled' — the button is hidden for
    a lapsed customer (it lives in the act page's `not gated` block) but the
    deadline is in the teaser hero anyway, so the URL keeps working."""
    make_user("lapsed")                       # no grant
    login(client, "lapsed", "pw-123456")
    assert client.get(f"/act/{ADAM}/calendar.ics").status_code == 200


def test_an_act_without_a_deadline_has_nothing_to_offer(acts, client):
    _member(client)
    assert client.get(f"/act/{NO_DEADLINE}/calendar.ics").status_code == 404


def test_an_unknown_act_is_404(acts, client):
    _member(client)
    assert client.get("/act/26PROC000000000/calendar.ics").status_code == 404


# --------------------------------------------------------------------------- #
# What is in the event
# --------------------------------------------------------------------------- #
def test_the_deadline_is_the_event_and_the_time_is_utc(acts, client):
    _member(client)
    _, body = _body(client)
    assert "DTSTART:20260414T205900Z" in _logical(body)


def test_the_title_is_escaped_not_truncated(acts, client):
    """An unescaped comma does not error — it silently ends the value."""
    _member(client)
    _, body = _body(client)
    summary = next(ln for ln in _logical(body) if ln.startswith("SUMMARY:"))
    assert "Προμήθεια ειδών\\, 3 τμήματα" in summary


def test_the_description_carries_the_authority_value_and_adam(acts, client):
    _member(client)
    _, body = _body(client)
    desc = next(ln for ln in _logical(body) if ln.startswith("DESCRIPTION:"))
    assert "Γενικό Νοσοκομείο Θεσσαλονίκης" in desc
    assert "1.250.000" in desc
    assert ADAM in desc


def test_the_url_points_at_the_act_page_and_is_not_escaped(acts, client):
    _member(client)
    _, body = _body(client)
    assert f"URL:https://testserver/act/{ADAM}" in _logical(body)


def test_the_uid_is_stable_across_requests(acts, client):
    _member(client)
    first = _logical(_body(client)[1])
    second = _logical(_body(client)[1])
    assert f"UID:{ADAM}@khmdhs" in first
    assert f"UID:{ADAM}@khmdhs" in second


def test_sequence_comes_from_last_update_so_a_moved_deadline_moves(acts, client):
    """A client that already holds this UID ignores a changed DTSTART unless
    SEQUENCE rises. Without this the feature fails silently, late."""
    from app import ics

    _member(client)
    _, body = _body(client)
    assert f"SEQUENCE:{ics.sequence_from(UPDATED)}" in _logical(body)

    acts.execute("UPDATE proc.procurement_act SET last_update_date=%s WHERE adam=%s",
                 (UPDATED + dt.timedelta(days=1), ADAM))
    _, later = _body(client)
    assert f"SEQUENCE:{ics.sequence_from(UPDATED + dt.timedelta(days=1))}" \
        in _logical(later)


def test_reminders_use_the_digest_marks(acts, client):
    from app import digests

    _member(client)
    _, body = _body(client)
    lines = _logical(body)
    for days in digests.DEFAULT_LEAD_DAYS:
        assert f"TRIGGER:-P{days}D" in lines


def test_a_cancelled_act_is_marked_and_carries_no_reminders(acts, client):
    _member(client)
    acts.execute("UPDATE proc.procurement_act SET cancelled=true WHERE adam=%s",
                 (ADAM,))
    _, body = _body(client)
    lines = _logical(body)
    assert "STATUS:CANCELLED" in lines
    assert "BEGIN:VALARM" not in lines


# --------------------------------------------------------------------------- #
# Response shape
# --------------------------------------------------------------------------- #
def test_it_downloads_as_a_file_and_is_never_indexed(acts, client):
    _member(client)
    r, _ = _body(client)
    assert r.headers["content-disposition"] == f'attachment; filename="{ADAM}.ics"'
    # A calendar file has no <meta robots> to fall back on.
    assert "noindex" in r.headers["x-robots-tag"]


def test_the_act_page_offers_the_download(acts, client):
    _member(client)
    assert f"/act/{ADAM}/calendar.ics" in client.get(f"/act/{ADAM}").text


def test_the_page_does_not_offer_it_without_a_deadline(acts, client):
    _member(client)
    assert "calendar.ics" not in client.get(f"/act/{NO_DEADLINE}").text
