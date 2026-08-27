"""Scheduled result-digest emails: cadence maths, the schedule-resolution
fallback, the ingest window, sending (through the in-memory mail backend), and
the /admin/digests routes.

The pure-unit half needs no database — cadence maths and app/mailer.py are
deliberately free of both DB and FastAPI.
"""
import datetime as dt
import zoneinfo

import pytest

from tests.helpers import get_csrf, login, make_user

ATHENS = zoneinfo.ZoneInfo("Europe/Athens")
UTC = dt.timezone.utc


def _sched(**kw):
    """A schedule dict shaped like the table row, with sane defaults."""
    base = {"cadence": "daily", "hour": 8, "minute": 0, "weekday": None,
            "day_of_month": None, "tz": "Europe/Athens", "is_active": True}
    base.update(kw)
    return base


def _at(y, m, d, hh, mm=0, tz=ATHENS):
    return dt.datetime(y, m, d, hh, mm, tzinfo=tz)


# --------------------------------------------------------------------------- #
# Cadence maths (no DB)
# --------------------------------------------------------------------------- #
def test_daily_last_occurrence_is_today_once_the_hour_has_passed():
    from app.digests import last_occurrence
    occ = last_occurrence(_sched(hour=8), _at(2026, 6, 10, 9, 30))
    assert occ.astimezone(ATHENS) == _at(2026, 6, 10, 8, 0)


def test_daily_last_occurrence_falls_back_to_yesterday_before_the_hour():
    from app.digests import last_occurrence
    occ = last_occurrence(_sched(hour=8), _at(2026, 6, 10, 7, 59))
    assert occ.astimezone(ATHENS) == _at(2026, 6, 9, 8, 0)


def test_local_hour_is_converted_to_utc():
    """08:00 Athens in June is 05:00 UTC — the window must not drift by the
    offset, or a 'daily 08:00' digest fires at 11:00 local."""
    from app.digests import last_occurrence
    occ = last_occurrence(_sched(hour=8), _at(2026, 6, 10, 12, 0))
    assert occ == dt.datetime(2026, 6, 10, 5, 0, tzinfo=UTC)


def test_weekdays_cadence_skips_the_weekend():
    from app.digests import last_occurrence
    # Sunday 2026-06-14 → the last firing was Friday the 12th.
    occ = last_occurrence(_sched(cadence="weekdays", hour=8),
                          _at(2026, 6, 14, 20, 0))
    assert occ.astimezone(ATHENS) == _at(2026, 6, 12, 8, 0)


def test_weekly_cadence_uses_its_weekday():
    from app.digests import last_occurrence
    # weekday=0 is Monday; from Thursday the 11th the last one was Monday the 8th.
    occ = last_occurrence(_sched(cadence="weekly", weekday=0, hour=9),
                          _at(2026, 6, 11, 12, 0))
    assert occ.astimezone(ATHENS) == _at(2026, 6, 8, 9, 0)


def test_monthly_cadence_uses_its_day_of_month():
    from app.digests import last_occurrence
    occ = last_occurrence(_sched(cadence="monthly", day_of_month=5, hour=7),
                          _at(2026, 6, 3, 12, 0))
    assert occ.astimezone(ATHENS) == _at(2026, 5, 5, 7, 0)


def test_next_occurrence_is_strictly_in_the_future():
    from app.digests import next_occurrence
    now = _at(2026, 6, 10, 8, 0)          # exactly on the hour
    nxt = next_occurrence(_sched(hour=8), now)
    assert nxt.astimezone(ATHENS) == _at(2026, 6, 11, 8, 0)


def test_unknown_cadence_never_fires():
    from app.digests import last_occurrence
    assert last_occurrence(_sched(cadence="hourly")) is None


def test_is_due_when_never_run_and_not_again_after_running():
    from app.digests import is_due
    now = _at(2026, 6, 10, 9, 0)
    sched = _sched(hour=8)
    fresh = {"is_active": True, "last_run_at": None}
    assert is_due(fresh, sched, now) is True

    ran = {"is_active": True,
           "last_run_at": _at(2026, 6, 10, 8, 30).astimezone(UTC)}
    assert is_due(ran, sched, now) is False

    # ... but the next day's firing makes it due again.
    assert is_due(ran, sched, _at(2026, 6, 11, 8, 1)) is True


def test_inactive_subscription_or_schedule_is_never_due():
    from app.digests import is_due
    now = _at(2026, 6, 10, 9, 0)
    assert is_due({"is_active": False, "last_run_at": None}, _sched(), now) is False
    assert is_due({"is_active": True, "last_run_at": None},
                  _sched(is_active=False), now) is False
    assert is_due({"is_active": True, "last_run_at": None}, None, now) is False


def test_window_start_prefers_the_cursor_then_creation():
    from app.digests import window_start
    created = dt.datetime(2026, 6, 1, tzinfo=UTC)
    cursor = dt.datetime(2026, 6, 9, tzinfo=UTC)
    assert window_start({"last_cursor": cursor, "created_at": created}) == cursor
    assert window_start({"last_cursor": None, "created_at": created}) == created


def test_describe_schedule_reads_as_a_sentence():
    from app.digests import describe_schedule
    assert describe_schedule(_sched(hour=8, minute=30)).startswith("Καθημερινά 08:30")
    assert "Δευτέρα" in describe_schedule(_sched(cadence="weekly", weekday=0))
    assert describe_schedule(None) == "—"


def test_a_broken_timezone_falls_back_instead_of_raising():
    from app.digests import last_occurrence
    occ = last_occurrence(_sched(tz="Mars/Olympus", hour=8),
                          _at(2026, 6, 10, 9, 0))
    assert occ is not None


# --------------------------------------------------------------------------- #
# mailer (no DB)
# --------------------------------------------------------------------------- #
def test_memory_backend_captures_both_body_parts(monkeypatch):
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    monkeypatch.delenv("EMAIL_REDIRECT_TO", raising=False)
    mailer.clear_outbox()

    res = mailer.send(to="a@example.com", subject="Θέμα",
                      html="<p>Γεια</p>", text="Γεια")
    assert res["backend"] == "memory"
    [msg] = mailer.outbox()
    assert msg["subject"] == "Θέμα"
    assert "text/plain" in msg["raw"] and "text/html" in msg["raw"]
    mailer.clear_outbox()


def test_invalid_recipient_is_refused_before_any_connection(monkeypatch):
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    for bad in ("", "   ", "not-an-address", "two@addresses,x@y.gr"):
        with pytest.raises(mailer.MailError):
            mailer.send(to=bad, subject="s", html="<p>h</p>", text="h")


def test_redirect_keeps_the_intended_recipient_visible(monkeypatch):
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    monkeypatch.setenv("EMAIL_REDIRECT_TO", "trap@example.com")
    mailer.clear_outbox()

    res = mailer.send(to="customer@example.com", subject="s",
                      html="<p>h</p>", text="h")
    assert res["to"] == "trap@example.com"
    assert "X-Original-To: customer@example.com" in mailer.outbox()[0]["raw"]
    mailer.clear_outbox()


def test_smtp_backend_without_a_host_fails_loudly(monkeypatch):
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(mailer.MailError):
        mailer.send(to="a@example.com", subject="s", html="<p>h</p>", text="h")


def test_file_backend_writes_an_eml(monkeypatch, tmp_path):
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "file")
    monkeypatch.setenv("EMAIL_FILE_DIR", str(tmp_path))
    monkeypatch.delenv("EMAIL_REDIRECT_TO", raising=False)
    res = mailer.send(to="a@example.com", subject="s", html="<p>h</p>", text="h")
    written = list(tmp_path.glob("*.eml"))
    assert len(written) == 1 and written[0].name in res["detail"]


# --------------------------------------------------------------------------- #
# DB fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def clean_digests(db):
    """app_user is truncated per test (cascading subscriptions away), but the
    schedules are reference data the snapshot does not clear."""
    cur = db.cursor()
    cur.execute("DELETE FROM proc.digest_schedule")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'DGST%'")
    yield cur
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'DGST%'")


def _customer(cur, username, email="cust@example.com"):
    from app import auth as _auth
    return _auth.create_user(cur, username, "goodpassword1",
                             role="customer", email=email)["id"]


def _profile(cur, admin_id, owner_id=None, name="Καθαριότητα",
             params=None, scope=None):
    from app import auth as _auth
    scope = scope or ("customer" if owner_id else "portal")
    return _auth.create_search_profile(
        cur, name=name, scope=scope, owner_id=owner_id,
        params=params if params is not None else {"q": "καθαριότητα"},
        based_on_id=None, created_by=admin_id)


def _act(cur, adam, *, title="Καθαριότητα κτιρίων", ingested_at=None,
         type_="notice"):
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, submission_date,
                      ingested_at)
                   VALUES (%s, %s, %s, 'import', 'khmdhs', now(),
                           coalesce(%s, now()))""",
                (adam, type_, title, ingested_at))


# --------------------------------------------------------------------------- #
# Schedules + the resolution fallback
# --------------------------------------------------------------------------- #
def test_only_one_schedule_can_be_the_portal_default(clean_digests):
    from app import digests as dg
    cur = clean_digests
    first = dg.create_schedule(cur, name="Α", cadence="daily", hour=8,
                               minute=0, is_default=True)
    second = dg.create_schedule(cur, name="Β", cadence="weekly", weekday=2,
                                hour=9, minute=0, is_default=True)
    assert dg.default_schedule(cur)["id"] == second
    assert dg.get_schedule(cur, first)["is_default"] is False


def test_cadence_is_validated(clean_digests):
    from app import digests as dg
    with pytest.raises(ValueError):
        dg.create_schedule(clean_digests, name="x", cadence="hourly",
                           hour=1, minute=0)


def test_subscription_inherits_the_default_and_an_override_wins(clean_digests):
    from app import digests as dg
    cur = clean_digests
    admin = make_user("dg_admin_res", "goodpassword1", role="admin")
    cust = _customer(cur, "dg_cust_res")
    prof = _profile(cur, admin)
    default = dg.create_schedule(cur, name="Προεπιλογή", cadence="daily",
                                 hour=8, minute=0, is_default=True)
    special = dg.create_schedule(cur, name="Εβδομαδιαία", cadence="weekly",
                                 weekday=0, hour=9, minute=0)

    sub_id = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof)
    sub = dg.get_subscription(cur, sub_id)
    assert dg.resolve_schedule(cur, sub)["id"] == default

    dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                           schedule_id=special)
    sub = dg.get_subscription(cur, sub_id)
    assert dg.resolve_schedule(cur, sub)["id"] == special


def test_upsert_edits_rather_than_duplicates(clean_digests):
    from app import digests as dg
    cur = clean_digests
    admin = make_user("dg_admin_up", "goodpassword1", role="admin")
    cust = _customer(cur, "dg_cust_up")
    prof = _profile(cur, admin)
    a = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                               max_results=10)
    b = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                               max_results=50, lang="en")
    assert a == b
    sub = dg.get_subscription(cur, a)
    assert sub["max_results"] == 50 and sub["lang"] == "en"
    assert len(dg.list_subscriptions(cur, user_id=cust)) == 1


def test_deleting_a_schedule_drops_subscriptions_back_to_the_default(clean_digests):
    from app import digests as dg
    cur = clean_digests
    admin = make_user("dg_admin_del", "goodpassword1", role="admin")
    cust = _customer(cur, "dg_cust_del")
    prof = _profile(cur, admin)
    default = dg.create_schedule(cur, name="Προεπιλογή", cadence="daily",
                                 hour=8, minute=0, is_default=True)
    special = dg.create_schedule(cur, name="Ειδικό", cadence="daily",
                                 hour=18, minute=0)
    sub_id = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                                    schedule_id=special)
    dg.delete_schedule(cur, special)

    sub = dg.get_subscription(cur, sub_id)
    assert sub is not None and sub["schedule_id"] is None
    assert dg.resolve_schedule(cur, sub)["id"] == default


# --------------------------------------------------------------------------- #
# The ingest window
# --------------------------------------------------------------------------- #
def test_new_acts_honours_both_the_filters_and_the_window(clean_digests):
    from app import digests as dg
    cur = clean_digests
    now = dt.datetime.now(UTC)
    _act(cur, "DGST-IN-1", title="Καθαριότητα κτιρίων",
         ingested_at=now - dt.timedelta(hours=1))
    _act(cur, "DGST-OLD-1", title="Καθαριότητα οδών",
         ingested_at=now - dt.timedelta(days=5))       # before the window
    _act(cur, "DGST-OTHER", title="Προμήθεια οχημάτων",
         ingested_at=now - dt.timedelta(hours=1))      # does not match the filter

    rows, total = dg.new_acts(cur, {"q": "καθαριότητα"},
                              now - dt.timedelta(hours=6), now)
    assert total == 1
    assert [r["adam"] for r in rows] == ["DGST-IN-1"]


def test_new_acts_reports_the_full_count_but_returns_at_most_the_limit(clean_digests):
    from app import digests as dg
    cur = clean_digests
    now = dt.datetime.now(UTC)
    for i in range(5):
        _act(cur, f"DGST-N{i}", ingested_at=now - dt.timedelta(minutes=i + 1))
    rows, total = dg.new_acts(cur, {"q": "καθαριότητα"},
                              now - dt.timedelta(hours=1), now, limit=2)
    assert total == 5 and len(rows) == 2


def test_the_window_is_half_open_so_runs_never_double_send(clean_digests):
    """Upper bound inclusive, lower bound exclusive: an act ingested exactly at
    the previous cursor belongs to the previous digest, not this one."""
    from app import digests as dg
    cur = clean_digests
    now = dt.datetime.now(UTC)
    boundary = now - dt.timedelta(hours=1)
    _act(cur, "DGST-EDGE", ingested_at=boundary)

    _, before = dg.new_acts(cur, {"q": "καθαριότητα"},
                            now - dt.timedelta(hours=2), boundary)
    _, after = dg.new_acts(cur, {"q": "καθαριότητα"}, boundary, now)
    assert (before, after) == (1, 0)


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
@pytest.fixture()
def memory_mail(monkeypatch):
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    monkeypatch.delenv("EMAIL_REDIRECT_TO", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    mailer.clear_outbox()
    yield mailer
    mailer.clear_outbox()


def _subscribed(cur, tag, params=None):
    """An admin, a customer with an address, a profile and a due subscription."""
    from app import digests as dg
    admin = make_user(f"dg_admin_{tag}", "goodpassword1", role="admin")
    cust = _customer(cur, f"dg_cust_{tag}", email=f"{tag}@example.com")
    prof = _profile(cur, admin, params=params)
    dg.create_schedule(cur, name="Προεπιλογή", cadence="daily", hour=8,
                       minute=0, is_default=True)
    sub_id = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof)
    # Backdate creation so the first window covers the acts the test inserts.
    cur.execute("UPDATE proc.digest_subscription SET created_at = now() - "
                "interval '2 days' WHERE id = %s", (sub_id,))
    return admin, cust, prof, sub_id


def test_a_digest_is_sent_and_the_cursor_advances(clean_digests, memory_mail):
    from app import digests as dg
    cur = clean_digests
    _, _, _, sub_id = _subscribed(cur, "send")
    _act(cur, "DGST-SEND-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent" and res["n"] == 1

    [msg] = memory_mail.outbox()
    assert msg["to"] == "send@example.com"
    assert "DGST-SEND-1" in msg["html"]
    assert "https://example.test/act/DGST-SEND-1" in msg["html"]
    assert msg["text"].strip()                      # the plain part is not empty

    # The text/plain alternative carries the results too, not just the intro —
    # deriving it from the mail-table HTML would silently drop every act.
    assert "DGST-SEND-1" in msg["text"]
    assert "https://example.test/act/DGST-SEND-1" in msg["text"]

    sub = dg.get_subscription(cur, sub_id)
    assert sub["last_cursor"] is not None and sub["last_sent_at"] is not None

    # The same act is not sent twice: the second run's window starts at the cursor.
    memory_mail.clear_outbox()
    res2 = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res2["status"] == "empty" and memory_mail.outbox() == []


def test_an_empty_window_sends_nothing_unless_asked(clean_digests, memory_mail):
    from app import digests as dg
    cur = clean_digests
    _, cust, prof, sub_id = _subscribed(cur, "empty")

    assert dg.run_subscription(cur, dg.get_subscription(cur, sub_id))["status"] == "empty"
    assert memory_mail.outbox() == []

    dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                           send_empty=True)
    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent" and len(memory_mail.outbox()) == 1


def test_a_send_failure_records_an_error_and_keeps_the_window(clean_digests,
                                                              memory_mail,
                                                              monkeypatch):
    from app import digests as dg
    cur = clean_digests
    _, cust, _, sub_id = _subscribed(cur, "fail")
    _act(cur, "DGST-FAIL-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))
    cur.execute("UPDATE proc.app_user SET email = NULL WHERE id = %s", (cust,))

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "error"
    sub = dg.get_subscription(cur, sub_id)
    # last_run_at moved (we did look), last_cursor did NOT (nothing was delivered).
    assert sub["last_run_at"] is not None and sub["last_cursor"] is None
    [run] = dg.list_runs(cur, subscription_id=sub_id)
    assert run["status"] == "error" and run["error"]


def test_a_test_send_leaves_the_customer_window_untouched(clean_digests, memory_mail):
    from app import digests as dg
    cur = clean_digests
    _subscribed(cur, "testmode")
    sub_id = dg.list_subscriptions(cur)[0]["id"]
    _act(cur, "DGST-TEST-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id),
                              trigger="test", advance=False,
                              to="admin@example.com")
    assert res["status"] == "sent"
    assert memory_mail.outbox()[0]["to"] == "admin@example.com"
    assert dg.get_subscription(cur, sub_id)["last_cursor"] is None


def test_run_due_only_sends_what_the_schedule_says(clean_digests, memory_mail):
    from app import digests as dg
    cur = clean_digests
    _subscribed(cur, "due")
    _act(cur, "DGST-DUE-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    first = dg.run_due(cur)
    assert first["sent"] == 1

    # Immediately after, the same daily schedule has not fired again.
    second = dg.run_due(cur)
    assert second["sent"] == 0 and second["skipped"] == 1

    # ...unless the admin forces it.
    _act(cur, "DGST-DUE-2", ingested_at=dt.datetime.now(UTC))
    forced = dg.run_due(cur, force=True)
    assert forced["sent"] == 1


def test_a_customer_without_an_address_is_not_even_a_candidate(clean_digests):
    from app import digests as dg
    cur = clean_digests
    admin = make_user("dg_admin_noaddr", "goodpassword1", role="admin")
    cust = _customer(cur, "dg_cust_noaddr", email=None)
    prof = _profile(cur, admin)
    dg.create_schedule(cur, name="Π", cadence="daily", hour=8, minute=0,
                       is_default=True)
    dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof)
    assert dg.active_subscriptions(cur) == []


def test_the_digest_wording_comes_from_the_editable_template(clean_digests,
                                                             memory_mail):
    from app import auth as _auth
    from app import digests as dg
    cur = clean_digests
    _subscribed(cur, "tpl")
    sub_id = dg.list_subscriptions(cur)[0]["id"]
    _act(cur, "DGST-TPL-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))
    _auth.upsert_email_template(
        cur, slug="digest", lang="el", name="Ειδοποίηση",
        subject="Νέα για [[profile_name]]",
        body_html="<p>@@x Γεια σου [[full_name]] από το [[profile_name]].</p>")

    built = dg.build(cur, dg.get_subscription(cur, sub_id))
    assert built["subject"] == "Νέα για Καθαριότητα"
    assert "Γεια σου" in built["html"] and "[[" not in built["html"]


def test_an_ampersand_in_the_subject_is_not_html_escaped(clean_digests, memory_mail):
    """The subject is a plain-text header, not markup: a profile called
    "Καύσιμα & πετρελαιοειδή" must not arrive as "... &amp; ..."."""
    from app import auth as _auth
    from app import digests as dg
    cur = clean_digests
    _, _, prof, sub_id = _subscribed(cur, "amp")
    _auth.update_search_profile(cur, prof, name="Καύσιμα & πετρελαιοειδή",
                                params={"q": "καθαριότητα"}, based_on_id=None,
                                is_published=False)
    _auth.upsert_email_template(cur, slug="digest", lang="el", name="Ειδ.",
                                subject="Νέα: [[profile_name]]",
                                body_html="<p>[[profile_name]]</p>")

    built = dg.build(cur, dg.get_subscription(cur, sub_id))
    assert built["subject"] == "Νέα: Καύσιμα & πετρελαιοειδή"
    # the BODY is markup, so there the escaping must stay
    assert "&amp;" in built["html"]


def test_a_crm_style_marker_never_reaches_the_customer(clean_digests, memory_mail):
    """@@token survives the CRM merge on purpose (a human replaces it before
    sending). A digest sends unattended, so it must be stripped."""
    from app import auth as _auth
    from app import digests as dg
    cur = clean_digests
    _subscribed(cur, "marker")
    sub_id = dg.list_subscriptions(cur)[0]["id"]
    _auth.upsert_email_template(
        cur, slug="digest", lang="el", name="Ειδοποίηση",
        subject="@@subj Νέα για [[profile_name]]",
        body_html="<p>@@greeting Καλημέρα [[full_name]],</p>")

    built = dg.build(cur, dg.get_subscription(cur, sub_id))
    assert built["subject"] == "Νέα για Καθαριότητα"
    assert "@@" not in built["html"] and "Καλημέρα" in built["html"]


def test_a_missing_template_still_produces_an_email(clean_digests, memory_mail):
    """An admin deleting the 'digest' template must not stop the send."""
    from app import digests as dg
    cur = clean_digests
    cur.execute("DELETE FROM proc.email_template WHERE slug = 'digest'")
    _subscribed(cur, "notpl")
    sub_id = dg.list_subscriptions(cur)[0]["id"]
    _act(cur, "DGST-NOTPL-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent"
    assert "DGST-NOTPL-1" in memory_mail.outbox()[0]["html"]


def test_deleting_a_search_profile_takes_its_subscriptions_with_it(clean_digests):
    from app import digests as dg
    cur = clean_digests
    _, _, prof, sub_id = _subscribed(cur, "gone")
    cur.execute("DELETE FROM proc.search_profile WHERE id = %s", (prof,))
    assert dg.get_subscription(cur, sub_id) is None


def test_a_failure_while_building_is_recorded_not_raised(clean_digests,
                                                         memory_mail,
                                                         monkeypatch):
    """One broken subscription must not abort the sweep for everyone else."""
    from app import digests as dg
    cur = clean_digests
    _subscribed(cur, "boom")
    sub_id = dg.list_subscriptions(cur)[0]["id"]

    def _explode(*a, **kw):
        raise RuntimeError("query blew up")
    monkeypatch.setattr(dg, "new_acts", _explode)

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "error"
    [run] = dg.list_runs(cur, subscription_id=sub_id)
    assert run["status"] == "error" and "query blew up" in run["error"]
    # nothing was consumed, so the next run retries the same window
    assert dg.get_subscription(cur, sub_id)["last_cursor"] is None


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def test_admin_pages_are_closed_to_customers(client, clean_digests):
    cur = clean_digests
    _customer(cur, "dg_rbac_cust")
    login(client, "dg_rbac_cust", "goodpassword1")
    assert client.get("/admin/digests", follow_redirects=False).status_code == 403


def test_admin_can_create_a_schedule_and_a_subscription(client, clean_digests):
    from app import digests as dg
    cur = clean_digests
    admin = make_user("dg_route_admin", "goodpassword1", role="admin")
    cust = _customer(cur, "dg_route_cust")
    prof = _profile(cur, admin)
    login(client, "dg_route_admin", "goodpassword1")

    tok = get_csrf(client)
    r = client.post("/admin/digests/schedules",
                    data={"name": "Εβδομαδιαία", "cadence": "weekly",
                          "hour": "9", "minute": "15", "weekday": "1",
                          "tz": "Europe/Athens", "is_default": "1",
                          "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 303
    sched = dg.default_schedule(cur)
    assert sched["name"] == "Εβδομαδιαία" and sched["weekday"] == 1

    r = client.post("/admin/digests/subscriptions",
                    data={"user_id": str(cust), "search_profile_id": str(prof),
                          "schedule_id": "", "lang": "en", "max_results": "40",
                          "is_active": "1", "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 303
    [sub] = dg.list_subscriptions(cur)
    assert sub["schedule_id"] is None and sub["lang"] == "en"

    page = client.get("/admin/digests?tab=subscriptions")
    assert page.status_code == 200 and "dg_route_cust" in page.text


def test_a_customers_private_profile_cannot_be_given_to_someone_else(client,
                                                                     clean_digests):
    cur = clean_digests
    admin = make_user("dg_x_admin", "goodpassword1", role="admin")
    owner = _customer(cur, "dg_x_owner", email="owner@example.com")
    other = _customer(cur, "dg_x_other", email="other@example.com")
    private = _profile(cur, admin, owner_id=owner, name="Ιδιωτικό")
    login(client, "dg_x_admin", "goodpassword1")

    tok = get_csrf(client)
    r = client.post("/admin/digests/subscriptions",
                    data={"user_id": str(other),
                          "search_profile_id": str(private),
                          "max_results": "25", "is_active": "1",
                          "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 400

    r = client.post("/admin/digests/subscriptions",
                    data={"user_id": str(owner),
                          "search_profile_id": str(private),
                          "max_results": "25", "is_active": "1",
                          "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 303


def test_preview_renders_the_real_email(client, clean_digests, memory_mail):
    from app import digests as dg
    cur = clean_digests
    _subscribed(cur, "prev")
    sub_id = dg.list_subscriptions(cur)[0]["id"]
    _act(cur, "DGST-PREV-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))
    make_user("dg_prev_admin", "goodpassword1", role="admin")
    login(client, "dg_prev_admin", "goodpassword1")

    r = client.get(f"/admin/digests/subscriptions/{sub_id}/preview?days=3")
    assert r.status_code == 200
    assert "DGST-PREV-1" in r.text
    assert memory_mail.outbox() == []            # preview sends nothing


def test_the_admin_run_button_sweeps_what_is_due(client, clean_digests,
                                                 memory_mail):
    from app import digests as dg
    cur = clean_digests
    _subscribed(cur, "sweep")
    _act(cur, "DGST-SWEEP-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))
    make_user("dg_sweep_admin", "goodpassword1", role="admin")
    login(client, "dg_sweep_admin", "goodpassword1")

    r = client.post("/admin/digests/run",
                    data={"csrf_token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert len(memory_mail.outbox()) == 1
    assert dg.list_runs(cur)[0]["status"] == "sent"
