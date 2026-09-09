"""The deadline reminder digest: the forward window, and the ledger that stops
it repeating itself.

The other two bodies are covered by test_digests.py; everything here is about
what makes this one different — it looks forward on final_submission_date, it
has no cursor to move, and what keeps the same act out of tomorrow's message is
proc.digest_deadline_notice rather than a high-water mark.
"""
import datetime as dt

import pytest

from tests.helpers import get_csrf, grant, login, make_user

UTC = dt.timezone.utc


# --------------------------------------------------------------------------- #
# The reminder marks (no DB)
# --------------------------------------------------------------------------- #
def test_marks_are_parsed_deduplicated_and_ordered_largest_first():
    from app.digests import clean_lead_days
    assert clean_lead_days("7, 1") == (7, 1)
    assert clean_lead_days("1,7,7") == (7, 1)
    assert clean_lead_days([1, 30, 3]) == (30, 3, 1)
    assert clean_lead_days("14 και 2") == (14, 2)      # any separator


def test_unusable_marks_fall_back_to_the_default_rather_than_raising():
    """This runs inside a scheduled send with nobody watching: a subscription
    whose marks were mistyped must still remind somebody."""
    from app.digests import DEFAULT_LEAD_DAYS, clean_lead_days
    assert clean_lead_days(None) == DEFAULT_LEAD_DAYS
    assert clean_lead_days("") == DEFAULT_LEAD_DAYS
    assert clean_lead_days("αύριο") == DEFAULT_LEAD_DAYS
    assert clean_lead_days([9999]) == DEFAULT_LEAD_DAYS     # out of range, so empty


def test_zero_is_a_legitimate_mark_meaning_the_closing_day_itself():
    from app.digests import clean_lead_days
    assert clean_lead_days("3, 0") == (3, 0)


def test_too_many_marks_keep_the_ones_closest_to_the_deadline():
    from app.digests import clean_lead_days
    # The distant warnings are a nicety; the last one before closing is not.
    assert clean_lead_days([1, 2, 3, 4, 5, 6, 7, 8]) == (6, 5, 4, 3, 2, 1)


def test_the_layout_is_accepted_and_has_its_own_wording_slug():
    from app import digests as dg
    assert dg.check_layout("deadline") == "deadline"
    assert dg.LAYOUT_SLUGS["deadline"] == "digest_deadline"


@pytest.mark.parametrize("hours,lang,expected", [
    (5, "el", "σήμερα"),
    (30, "el", "αύριο"),
    (24 * 6, "el", "σε 6 ημέρες"),
    (5, "en", "today"),
    (30, "en", "tomorrow"),
    (24 * 6, "en", "in 6 days"),
])
def test_the_countdown_phrase_inflects_per_language(hours, lang, expected):
    """Greek inflects «ημέρα»/«ημέρες», which a catalog keyed on the Greek
    source string cannot express — hence the phrase is built in Python."""
    from app.digests import _closing_in
    now = dt.datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
    out = _closing_in(now + dt.timedelta(hours=hours), now, lang)
    assert out["left_label"] == expected


def test_an_act_closing_within_hours_is_urgent_not_negative():
    from app.digests import _closing_in
    now = dt.datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
    out = _closing_in(now + dt.timedelta(hours=2), now, "el")
    assert out["days_left"] == 0 and out["urgent"] is True


# --------------------------------------------------------------------------- #
# DB fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def clean_digests(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.digest_schedule")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'DDL%'")
    yield cur
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'DDL%'")


@pytest.fixture()
def memory_mail(monkeypatch):
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    monkeypatch.delenv("EMAIL_REDIRECT_TO", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    mailer.clear_outbox()
    yield mailer
    mailer.clear_outbox()


def _act(cur, adam, *, closes_in_days=None, deadline=None,
         title="Καθαριότητα κτιρίων", cancelled=False, now=None):
    """An act with a submission deadline — the only column this body reads."""
    now = now or dt.datetime.now(UTC)
    if deadline is None and closes_in_days is not None:
        # Six hours of cushion so "closes in 3 days" still floors to 3 by the
        # time run_subscription takes its own `now` a few milliseconds later.
        deadline = now + dt.timedelta(days=closes_in_days, hours=6)
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, submission_date,
                      final_submission_date, cancelled, ingested_at)
                   VALUES (%s, 'notice', %s, 'import', 'khmdhs',
                           now() - interval '30 days', %s, %s, now())""",
                (adam, title, deadline, cancelled))
    return deadline


def _subscribed(cur, tag, *, leads="7, 1", params=None):
    """An entitled customer with a deadline-layout subscription."""
    from app import auth as _auth
    from app import digests as dg
    admin = make_user(f"ddl_admin_{tag}", "goodpassword1", role="admin")
    cust = _auth.create_user(cur, f"ddl_cust_{tag}", "goodpassword1",
                             role="customer", email=f"{tag}@example.com")["id"]
    grant(cust)
    prof = _auth.create_search_profile(
        cur, name=f"Καθαριότητα {tag}", scope="portal", owner_id=None,
        params=params if params is not None else {"q": "καθαριότητα"},
        based_on_id=None, created_by=admin)
    dg.create_schedule(cur, name="Προεπιλογή", cadence="daily", hour=8,
                       minute=0, is_default=True)
    sub_id = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                                    layout="deadline", lead_days=leads)
    return admin, cust, prof, sub_id


# --------------------------------------------------------------------------- #
# The forward window
# --------------------------------------------------------------------------- #
def test_only_acts_closing_inside_the_widest_mark_are_chased(clean_digests):
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "window")
    now = dt.datetime.now(UTC)
    _act(cur, "DDL-SOON", closes_in_days=2, now=now)
    _act(cur, "DDL-EDGE", closes_in_days=6, now=now)
    _act(cur, "DDL-FAR", closes_in_days=40, now=now)      # beyond the 7-day mark
    _act(cur, "DDL-GONE", closes_in_days=-1, now=now)     # already closed
    _act(cur, "DDL-NONE", deadline=None, now=now)         # no deadline at all

    rows, total, _ = dg.closing_acts(cur, {"q": "καθαριότητα"}, now,
                                     leads=(7, 1), subscription_id=sub_id)
    assert total == 2
    # Ordered by deadline: the message is a countdown, so the thing closing
    # first is the first line — and truncating drops the least urgent.
    assert [r["adam"] for r in rows] == ["DDL-SOON", "DDL-EDGE"]


def test_a_cancelled_act_is_never_chased(clean_digests):
    """Its deadline is still in the future and it is still in the corpus.
    Telling someone to hurry up and bid for it is the one thing this email
    must not do."""
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "cancelled")
    now = dt.datetime.now(UTC)
    _act(cur, "DDL-LIVE", closes_in_days=3, now=now)
    _act(cur, "DDL-DEAD", closes_in_days=3, cancelled=True, now=now)

    rows, total, _ = dg.closing_acts(cur, {"q": "καθαριότητα"}, now,
                                     leads=(7, 1), subscription_id=sub_id)
    assert total == 1 and [r["adam"] for r in rows] == ["DDL-LIVE"]


def test_the_profile_filters_still_apply(clean_digests):
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "filters")
    now = dt.datetime.now(UTC)
    _act(cur, "DDL-MATCH", closes_in_days=2, now=now)
    _act(cur, "DDL-OTHER", closes_in_days=2, title="Προμήθεια οχημάτων", now=now)

    _, total, _ = dg.closing_acts(cur, {"q": "καθαριότητα"}, now,
                                  leads=(7, 1), subscription_id=sub_id)
    assert total == 1


# --------------------------------------------------------------------------- #
# Sending, and the ledger that stops it repeating
# --------------------------------------------------------------------------- #
def test_a_reminder_is_sent_with_the_countdown_in_both_parts(clean_digests,
                                                             memory_mail):
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "send")
    _act(cur, "DDL-SEND-1", closes_in_days=3)

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent" and res["n"] == 1

    [msg] = memory_mail.outbox()
    assert msg["to"] == "send@example.com"
    assert "DDL-SEND-1" in msg["html"]
    assert "σε 3 ημέρες" in msg["html"]
    # The plain part carries the countdown too — deriving it from the mail
    # table would drop the very thing the message is about.
    assert "DDL-SEND-1" in msg["text"] and "σε 3 ημέρες" in msg["text"]


def test_the_same_act_is_not_chased_again_at_the_same_mark(clean_digests,
                                                           memory_mail):
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "once")
    _act(cur, "DDL-ONCE", closes_in_days=5)

    first = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert first["status"] == "sent"

    memory_mail.clear_outbox()
    second = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert second["status"] == "empty" and memory_mail.outbox() == []


def test_the_next_mark_fires_as_the_deadline_closes_in(clean_digests,
                                                       memory_mail):
    """The 7-day warning is spent; the 1-day one is not, and it is the reminder
    that actually matters."""
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "twice")
    _act(cur, "DDL-TWICE", closes_in_days=5)

    dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    memory_mail.clear_outbox()

    # Four days later the act is inside the 1-day mark.
    later = dt.datetime.now(UTC) + dt.timedelta(days=4, hours=12)
    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id), now=later)
    assert res["status"] == "sent" and res["n"] == 1
    assert "DDL-TWICE" in memory_mail.outbox()[0]["html"]

    # ...and not a third time.
    memory_mail.clear_outbox()
    again = dg.run_subscription(cur, dg.get_subscription(cur, sub_id),
                                now=later + dt.timedelta(hours=1))
    assert again["status"] == "empty"


def test_moving_the_deadline_re_arms_every_mark(clean_digests, memory_mail):
    """An extended closing date is news, and the whole promise of this body is
    that nothing closes unannounced."""
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "moved")
    _act(cur, "DDL-MOVED", closes_in_days=4)
    assert dg.run_subscription(cur, dg.get_subscription(cur, sub_id))["status"] == "sent"

    memory_mail.clear_outbox()
    cur.execute("""UPDATE proc.procurement_act
                      SET final_submission_date = now() + interval '6 days'
                    WHERE adam = 'DDL-MOVED'""")
    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent" and "DDL-MOVED" in memory_mail.outbox()[0]["html"]


def test_a_reminder_never_moves_the_ingest_cursor(clean_digests, memory_mail):
    """It has no cursor to move. Advancing it would mean that switching the
    subscription back to the list body later silently swallowed every act
    ingested in between."""
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "cursor")
    _act(cur, "DDL-CURSOR", closes_in_days=2)

    before = dg.get_subscription(cur, sub_id)["last_cursor"]
    assert dg.run_subscription(cur, dg.get_subscription(cur, sub_id))["status"] == "sent"
    after = dg.get_subscription(cur, sub_id)
    assert after["last_cursor"] == before
    assert after["last_sent_at"] is not None      # ...but the send is recorded


def test_a_test_send_spends_no_mark(clean_digests, memory_mail):
    """Same rule as the cursor: a message the customer never received must
    leave every reminder armed."""
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "testsend")
    _act(cur, "DDL-TEST", closes_in_days=3)

    dg.run_subscription(cur, dg.get_subscription(cur, sub_id), trigger="test",
                        advance=False, to="admin@example.com")
    cur.execute("SELECT count(*) AS n FROM proc.digest_deadline_notice "
                "WHERE subscription_id = %s", (sub_id,))
    assert cur.fetchone()["n"] == 0

    memory_mail.clear_outbox()
    real = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert real["status"] == "sent" and real["n"] == 1


def test_a_failed_send_leaves_the_marks_armed(clean_digests, memory_mail,
                                              monkeypatch):
    from app import digests as dg
    from app import mailer
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "failed")
    _act(cur, "DDL-FAIL", closes_in_days=2)

    def boom(**kw):
        raise mailer.MailError("smtp is down")
    monkeypatch.setattr(mailer, "send", boom)

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "error"
    cur.execute("SELECT count(*) AS n FROM proc.digest_deadline_notice "
                "WHERE subscription_id = %s", (sub_id,))
    assert cur.fetchone()["n"] == 0


def test_an_act_that_appears_late_spends_every_mark_it_has_already_crossed(
        clean_digests, memory_mail):
    """Published two days before it closes, it crosses the 7-day and the 1-day
    marks at once. Recording only the mark that triggered the send would fire
    the other one tomorrow — for a deadline that is closer, not further away."""
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "late", leads="7, 3")
    _act(cur, "DDL-LATE", closes_in_days=2)

    assert dg.run_subscription(cur, dg.get_subscription(cur, sub_id))["status"] == "sent"
    cur.execute("""SELECT lead_days FROM proc.digest_deadline_notice
                    WHERE subscription_id = %s ORDER BY lead_days""", (sub_id,))
    assert [r["lead_days"] for r in cur.fetchall()] == [3, 7]

    memory_mail.clear_outbox()
    assert dg.run_subscription(cur, dg.get_subscription(cur, sub_id))["status"] == "empty"


def test_the_run_records_its_acts_so_see_all_results_still_works(clean_digests,
                                                                 memory_mail):
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "items")
    for i in range(3):
        _act(cur, f"DDL-ITEM-{i}", closes_in_days=i + 1)

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent" and res["token"]
    run = dg.get_run_by_token(cur, res["token"])
    assert dg.run_item_count(cur, run["id"]) == 3
    assert f"/digests/{res['token']}" in memory_mail.outbox()[0]["html"]


# --------------------------------------------------------------------------- #
# The admin form
# --------------------------------------------------------------------------- #
def test_saving_an_alert_stores_the_marks(client, clean_digests):
    from app import digests as dg
    cur = clean_digests
    admin, cust, prof, sub_id = _subscribed(cur, "form")
    make_user("ddl_form_admin", "goodpassword1", role="admin")
    login(client, "ddl_form_admin", "goodpassword1")

    r = client.post("/admin/digests/subscriptions", data={
        "user_id": str(cust), "search_profile_id": str(prof),
        "layout": "deadline", "lang": "el", "max_results": "25",
        "lead_days": "14, 2", "is_active": "1", "include_primary": "1",
        "csrf_token": get_csrf(client)}, follow_redirects=False)
    assert r.status_code in (200, 302, 303)
    assert list(dg.get_subscription(cur, sub_id)["lead_days"]) == [14, 2]


def test_saving_without_the_field_keeps_the_marks_it_had(client, clean_digests):
    """The same form edits the language and the cadence: changing one of those
    has not asked to rewrite a deadline alert's reminder schedule."""
    from app import digests as dg
    cur = clean_digests
    admin, cust, prof, sub_id = _subscribed(cur, "keep", leads="21, 5")
    make_user("ddl_keep_admin", "goodpassword1", role="admin")
    login(client, "ddl_keep_admin", "goodpassword1")

    client.post("/admin/digests/subscriptions", data={
        "user_id": str(cust), "search_profile_id": str(prof),
        "layout": "deadline", "lang": "en", "max_results": "25",
        "is_active": "1", "include_primary": "1",
        "csrf_token": get_csrf(client)}, follow_redirects=False)
    sub = dg.get_subscription(cur, sub_id)
    assert sub["lang"] == "en" and list(sub["lead_days"]) == [21, 5]
