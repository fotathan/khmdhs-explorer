"""Result emails with more than one reader, and the summary body.

Two features that share one send path, so they share one test module:

  * proc.digest_recipient — a subscription mails the account address (unless
    include_primary is off) plus every named reader. One send is N messages,
    each greeting the person who receives it.
  * subscription.layout — 'list' prints the acts (tests/test_digests.py covers
    that shape), 'summary' prints the statistics and links out.

The sending machinery itself (windows, cursors, entitlement) is tested in
tests/test_digests.py; here we care about who gets a copy, what their copy
says, and what the summary counts.
"""
import datetime as dt

import pytest

from tests.helpers import get_csrf, grant, login, make_user

UTC = dt.timezone.utc


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def clean(db):
    """app_user is truncated per test (cascading subscriptions, and with them
    their recipients, away); schedules and acts are not."""
    cur = db.cursor()
    cur.execute("DELETE FROM proc.digest_schedule")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'DGR%'")
    cur.execute("DELETE FROM proc.authority WHERE org_id LIKE 'DGR%'")
    yield cur
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'DGR%'")
    cur.execute("DELETE FROM proc.authority WHERE org_id LIKE 'DGR%'")


@pytest.fixture()
def memory_mail(monkeypatch):
    from app import mailer
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    monkeypatch.delenv("EMAIL_REDIRECT_TO", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    mailer.clear_outbox()
    yield mailer
    mailer.clear_outbox()


def _authority(cur, org_id, name):
    """procurement_act.authority_id is a FK, so the summary's "how many
    authorities" needs real rows to point at."""
    cur.execute("""INSERT INTO proc.authority (org_id, name)
                   VALUES (%s, %s) ON CONFLICT (org_id) DO NOTHING""",
                (org_id, name))


def _act(cur, adam, *, title="Καθαριότητα κτιρίων", ingested_at=None,
         type_="notice", value=None, authority=None, deadline=None,
         cancelled=False):
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, submission_date,
                      ingested_at, total_cost_with_vat, authority_id,
                      final_submission_date, cancelled)
                   VALUES (%s, %s, %s, 'import', 'khmdhs', now(),
                           coalesce(%s, now()), %s, %s, %s, %s)""",
                (adam, type_, title, ingested_at, value, authority, deadline,
                 cancelled))


def _subscribed(cur, tag, *, layout="list", params=None, email=None,
                include_primary=True, max_results=25):
    """An entitled customer subscribed to one portal profile, with a window
    wide enough to cover acts the test inserts."""
    from app import auth as _auth
    from app import digests as dg
    admin = make_user(f"dgr_admin_{tag}", "goodpassword1", role="admin")
    cust = _auth.create_user(cur, f"dgr_cust_{tag}", "goodpassword1",
                             role="customer",
                             email=(email if email is not None
                                    else f"{tag}@example.com"))["id"]
    grant(cust)
    prof = _auth.create_search_profile(
        cur, name="Καθαριότητα", scope="portal", owner_id=None,
        params=params if params is not None else {"q": "καθαριότητα"},
        based_on_id=None, created_by=admin)
    dg.create_schedule(cur, name="Προεπιλογή", cadence="daily", hour=8,
                       minute=0, is_default=True)
    sub_id = dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                                    layout=layout, max_results=max_results,
                                    include_primary=include_primary)
    cur.execute("UPDATE proc.digest_subscription SET created_at = now() - "
                "interval '2 days' WHERE id = %s", (sub_id,))
    return admin, cust, prof, sub_id


def _addresses(mail):
    return sorted(m["to"] for m in mail.outbox())


# --------------------------------------------------------------------------- #
# Who a send reaches
# --------------------------------------------------------------------------- #
def test_with_no_extra_readers_only_the_account_is_mailed(clean, memory_mail):
    """The behaviour every existing subscription had, unchanged."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "solo")
    _act(cur, "DGR-SOLO-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent"
    assert _addresses(memory_mail) == ["solo@example.com"]


def test_every_named_reader_gets_their_own_copy(clean, memory_mail):
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "many")
    dg.add_recipient(cur, sub_id, email="anna@example.com",
                     salutation="Αξιότιμη κυρία", first_name="Άννα",
                     last_name="Παπαδάκη")
    dg.add_recipient(cur, sub_id, email="nikos@example.com", first_name="Νίκος")
    _act(cur, "DGR-MANY-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent"
    assert _addresses(memory_mail) == ["anna@example.com", "many@example.com",
                                       "nikos@example.com"]
    # One run, three messages — the history says how many mailboxes it reached.
    [run] = dg.list_runs(cur, subscription_id=sub_id)
    assert run["n_recipients"] == 3
    assert "anna@example.com" in run["recipient"]


def test_a_readers_own_copy_greets_them_and_not_the_account_holder(clean,
                                                                   memory_mail):
    """The point of storing a name per recipient: [[full_name]] must resolve to
    whoever is reading, or a colleague's copy opens with the customer's name."""
    from app import auth as _auth
    from app import digests as dg
    cur = clean
    _, cust, _, sub_id = _subscribed(cur, "greet")
    _auth.upsert_profile(cur, cust, {"full_name": "Γιώργος Οικονόμου"})
    _auth.upsert_email_template(
        cur, slug="digest", lang="el", name="Ειδοποίηση",
        subject="Νέα: [[profile_name]]",
        body_html="<p>[[salutation]] [[full_name]],</p>")
    dg.add_recipient(cur, sub_id, email="anna@example.com",
                     salutation="Αξιότιμη κυρία", first_name="Άννα",
                     last_name="Παπαδάκη")
    _act(cur, "DGR-GREET-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    by_to = {m["to"]: m for m in memory_mail.outbox()}

    assert "Γιώργος Οικονόμου" in by_to["greet@example.com"]["html"]
    assert "Άννα Παπαδάκη" not in by_to["greet@example.com"]["html"]

    assert "Αξιότιμη κυρία Άννα Παπαδάκη" in by_to["anna@example.com"]["html"]
    assert "Γιώργος Οικονόμου" not in by_to["anna@example.com"]["html"]


def test_the_account_address_can_be_left_out(clean, memory_mail):
    """An agency account whose staff read the results, not the account holder."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "noacct", include_primary=False)
    dg.add_recipient(cur, sub_id, email="staff@example.com")
    _act(cur, "DGR-NOACCT-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    assert dg.run_subscription(cur, dg.get_subscription(cur, sub_id))["status"] == "sent"
    assert _addresses(memory_mail) == ["staff@example.com"]


def test_the_same_mailbox_is_never_mailed_twice(clean, memory_mail):
    """The account address listed again as a named reader — one person, one
    copy, whatever the capitalisation."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "dupe")
    dg.add_recipient(cur, sub_id, email="DUPE@Example.com", first_name="Ίδιος")
    _act(cur, "DGR-DUPE-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert _addresses(memory_mail) == ["dupe@example.com"]


def test_the_same_address_cannot_be_listed_twice_on_one_subscription(clean):
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "uniq")
    first = dg.add_recipient(cur, sub_id, email="a@example.com", first_name="Α")
    again = dg.add_recipient(cur, sub_id, email="A@Example.com", first_name="Β")
    assert first == again                       # the row was updated, not added
    [row] = dg.list_recipients(cur, sub_id)
    assert row["first_name"] == "Β"


def test_a_switched_off_reader_is_kept_but_not_mailed(clean, memory_mail):
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "off")
    dg.add_recipient(cur, sub_id, email="paused@example.com", is_active=False)
    _act(cur, "DGR-OFF-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert _addresses(memory_mail) == ["off@example.com"]
    # Still on the list, so the admin can see it and turn it back on.
    assert len(dg.list_recipients(cur, sub_id)) == 1


def test_a_bad_address_is_refused_when_it_is_typed_in(clean):
    """Not at send time: a typo discovered three days later, in a run history
    nobody reads, is a digest silently not delivered."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "typo")
    with pytest.raises(ValueError):
        dg.add_recipient(cur, sub_id, email="anna(at)example.com")


def test_deleting_a_reader_is_scoped_to_its_own_subscription(clean):
    from app import digests as dg
    cur = clean
    _, cust, prof, sub_id = _subscribed(cur, "scope")
    rid = dg.add_recipient(cur, sub_id, email="anna@example.com")

    dg.delete_recipient(cur, rid, subscription_id=sub_id + 999)
    assert len(dg.list_recipients(cur, sub_id)) == 1     # untouched

    dg.delete_recipient(cur, rid, subscription_id=sub_id)
    assert dg.list_recipients(cur, sub_id) == []


def test_removing_a_subscription_takes_its_readers_with_it(clean):
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "cascade")
    dg.add_recipient(cur, sub_id, email="anna@example.com")
    dg.delete_subscription(cur, sub_id)
    cur.execute("SELECT count(*) AS n FROM proc.digest_recipient "
                "WHERE subscription_id = %s", (sub_id,))
    assert cur.fetchone()["n"] == 0


def test_an_account_with_no_address_but_a_named_reader_is_still_a_candidate(clean):
    """Used to be filtered out by the sweep's "has an email" test, which asked
    about the account rather than about the send."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "agency", email=None,
                                  include_primary=False)
    assert dg.active_subscriptions(cur) == []
    dg.add_recipient(cur, sub_id, email="staff@example.com")
    assert [s["id"] for s in dg.active_subscriptions(cur)] == [sub_id]


def test_a_subscription_with_nobody_on_it_records_an_error(clean, memory_mail):
    """Silence would read as 'sent'. It is a misconfiguration, and the run has
    to say so."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "nobody", include_primary=False)
    _act(cur, "DGR-NOBODY-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "error" and "no recipient" in res["error"]
    assert memory_mail.outbox() == []
    assert dg.get_subscription(cur, sub_id)["last_cursor"] is None


def test_one_bad_address_does_not_stop_the_others(clean, memory_mail,
                                                  monkeypatch):
    """The window WAS mailed, so it must not be re-sent: everyone else would
    get the whole set a second time. The failure is recorded on the run."""
    from app import digests as dg
    from app import mailer
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "partial")
    dg.add_recipient(cur, sub_id, email="broken@example.com")
    _act(cur, "DGR-PARTIAL-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    real_send = mailer.send

    def flaky(**kw):
        if kw.get("to") == "broken@example.com":
            raise mailer.MailError("mailbox full")
        return real_send(**kw)

    monkeypatch.setattr(mailer, "send", flaky)
    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))

    assert res["status"] == "sent"
    assert _addresses(memory_mail) == ["partial@example.com"]
    [run] = dg.list_runs(cur, subscription_id=sub_id)
    assert run["status"] == "sent" and run["n_recipients"] == 1
    assert "broken@example.com" in run["error"]
    assert dg.get_subscription(cur, sub_id)["last_cursor"] is not None


def test_when_every_address_fails_the_window_is_kept(clean, memory_mail,
                                                     monkeypatch):
    from app import digests as dg
    from app import mailer
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "allfail")
    dg.add_recipient(cur, sub_id, email="also@example.com")
    _act(cur, "DGR-ALLFAIL-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    monkeypatch.setattr(mailer, "send",
                        lambda **kw: (_ for _ in ()).throw(mailer.MailError("down")))
    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))

    assert res["status"] == "error"
    assert dg.get_subscription(cur, sub_id)["last_cursor"] is None
    [run] = dg.list_runs(cur, subscription_id=sub_id)
    assert run["error"].count("down") == 2      # both addresses named


def test_a_test_send_goes_only_where_it_is_told(clean, memory_mail):
    """The admin's own mailbox — not the customer's readers, who must not get a
    message the admin is only checking."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "testonly")
    dg.add_recipient(cur, sub_id, email="anna@example.com")
    _act(cur, "DGR-TESTONLY-1", ingested_at=dt.datetime.now(UTC) - dt.timedelta(hours=2))

    dg.run_subscription(cur, dg.get_subscription(cur, sub_id), trigger="test",
                        advance=False, to="admin@example.com")
    assert _addresses(memory_mail) == ["admin@example.com"]


# --------------------------------------------------------------------------- #
# The summary body
# --------------------------------------------------------------------------- #
def _stocked(cur, tag, **kw):
    """A subscription plus a window holding a known mix of acts."""
    out = _subscribed(cur, tag, **kw)
    now = dt.datetime.now(UTC)
    ago = now - dt.timedelta(hours=2)
    soon = now + dt.timedelta(days=10)
    _authority(cur, "DGR-A1", "Δήμος Αθηναίων")
    _authority(cur, "DGR-A2", "Περιφέρεια Αττικής")
    _act(cur, f"DGR-{tag}-N1", ingested_at=ago, type_="notice", value=1000,
         authority="DGR-A1", deadline=soon)
    _act(cur, f"DGR-{tag}-N2", ingested_at=ago, type_="notice", value=2000,
         authority="DGR-A1")
    _act(cur, f"DGR-{tag}-C1", ingested_at=ago, type_="contract", value=500,
         authority="DGR-A2", cancelled=True)
    return out


def test_the_summary_counts_by_type_instead_of_listing_the_acts(clean,
                                                                memory_mail):
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _stocked(cur, "sum", layout="summary")

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["status"] == "sent" and res["n"] == 3
    [msg] = memory_mail.outbox()

    # No act is listed — that is the whole point of this shape.
    assert "DGR-sum-N1" not in msg["html"]
    # The figures are.
    assert "Προκήρυξη" in msg["html"] and "Σύμβαση" in msg["html"]
    assert "3" in msg["html"]
    # ... and in the plain-text alternative too, not only in the HTML.
    assert "Προκήρυξη: 2" in msg["text"]
    assert "Σύμβαση: 1" in msg["text"]


def test_the_summary_reports_value_authorities_and_deadlines(clean, memory_mail):
    from app import digests as dg
    cur = clean
    _, _, prof, sub_id = _stocked(cur, "figs", layout="summary")
    sub = dg.get_subscription(cur, sub_id)
    params = {"q": "καθαριότητα"}
    now = dt.datetime.now(UTC)

    stats = dg.window_stats(cur, params, now - dt.timedelta(days=2), now)
    assert stats["total"] == 3
    assert float(stats["value"]) == 3500.0
    assert stats["authorities"] == 2
    assert stats["cancelled"] == 1
    assert stats["open_deadlines"] == 1
    assert [r["type"] for r in stats["by_type"]] == ["notice", "contract"]
    assert stats["top_authorities"][0]["n"] == 2

    dg.run_subscription(cur, sub)
    [msg] = memory_mail.outbox()
    assert "Αναθέτουσες αρχές" in msg["text"]
    assert "Ανοιχτές προθεσμίες" in msg["text"]


def test_the_summary_counts_the_whole_window_not_the_truncated_list(clean,
                                                                    memory_mail):
    """max_results caps what a LIST prints; it must not cap what a summary
    reports, or the headline number quietly becomes a lie."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _stocked(cur, "cap", layout="summary", max_results=1)

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    assert res["n"] == 3
    [msg] = memory_mail.outbox()
    assert ">3<" in msg["html"].replace("\n", "").replace(" ", "")


def test_the_summary_links_to_its_own_result_set(clean, memory_mail):
    """The detail is deliberately not in the message, so the link is the whole
    way through to it — and it must point at THIS run, not a live search."""
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _stocked(cur, "link", layout="summary")

    res = dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    [msg] = memory_mail.outbox()
    assert f"https://example.test/digests/{res['token']}" in msg["html"]
    assert f"https://example.test/digests/{res['token']}" in msg["text"]
    # Every act in the window is recorded behind that link.
    cur.execute("SELECT count(*) AS n FROM proc.digest_run_item WHERE run_id = %s",
                (res["run_id"],))
    assert cur.fetchone()["n"] == 3


def test_the_summary_takes_its_wording_from_its_own_template(clean, memory_mail):
    """A separate slug, so rewording the summary cannot change the list digest."""
    from app import auth as _auth
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _stocked(cur, "words", layout="summary")
    _auth.upsert_email_template(
        cur, slug="digest_summary", lang="el", name="Σύνοψη",
        subject="Η σύνοψή σας: [[profile_name]]",
        body_html="<p>Συνοπτικά για [[profile_name]]:</p>")

    dg.run_subscription(cur, dg.get_subscription(cur, sub_id))
    [msg] = memory_mail.outbox()
    assert msg["subject"] == "Η σύνοψή σας: Καθαριότητα"
    assert "Συνοπτικά για Καθαριότητα" in msg["html"]


def test_an_empty_window_is_still_empty_for_a_summary(clean, memory_mail):
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "sumempty", layout="summary")
    assert dg.run_subscription(cur, dg.get_subscription(cur, sub_id))["status"] == "empty"
    assert memory_mail.outbox() == []


def test_an_unknown_layout_is_refused(clean):
    from app import digests as dg
    cur = clean
    _, cust, prof, _ = _subscribed(cur, "badlayout")
    with pytest.raises(ValueError):
        dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                               layout="postcard")


def test_switching_layout_changes_the_body_and_nothing_else(clean, memory_mail):
    from app import digests as dg
    cur = clean
    _, cust, prof, sub_id = _stocked(cur, "switch")

    built_list = dg.build(cur, dg.get_subscription(cur, sub_id))
    dg.upsert_subscription(cur, user_id=cust, search_profile_id=prof,
                           layout="summary")
    built_sum = dg.build(cur, dg.get_subscription(cur, sub_id))

    assert built_list["total"] == built_sum["total"] == 3
    assert "DGR-switch-N1" in built_list["html"]
    assert "DGR-switch-N1" not in built_sum["html"]


# --------------------------------------------------------------------------- #
# The admin surface
# --------------------------------------------------------------------------- #
def _admin_client(client, cur, tag):
    """Log in as an admin and return (admin_id, customer_id, subscription_id)."""
    admin, cust, prof, sub_id = _subscribed(cur, tag)
    login(client, f"dgr_admin_{tag}", "goodpassword1")
    return admin, cust, sub_id


def test_an_admin_adds_a_reader_from_the_customer_card(client, clean):
    from app import digests as dg
    cur = clean
    _, cust, sub_id = _admin_client(client, cur, "uiadd")

    r = client.post(f"/admin/digests/subscriptions/{sub_id}/recipients",
                    data={"email": "anna@example.com",
                          "salutation": "Αξιότιμη κυρία",
                          "first_name": "Άννα", "last_name": "Παπαδάκη",
                          "back": f"/admin/crm/{cust}?tab=alerts"},
                    headers={"X-CSRF-Token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/admin/crm/{cust}")

    [row] = dg.list_recipients(cur, sub_id)
    assert row["email"] == "anna@example.com" and row["first_name"] == "Άννα"

    page = client.get(f"/admin/crm/{cust}").text
    assert "anna@example.com" in page and "Άννα" in page


def test_a_typo_comes_back_as_a_message_not_a_crash(client, clean):
    cur = clean
    _, cust, sub_id = _admin_client(client, cur, "uitypo")
    r = client.post(f"/admin/digests/subscriptions/{sub_id}/recipients",
                    data={"email": "not-an-address",
                          "back": f"/admin/crm/{cust}?tab=alerts"},
                    headers={"X-CSRF-Token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "flash=" in r.headers["location"]


def test_an_admin_removes_a_reader_and_returns_to_the_card(client, clean):
    from app import digests as dg
    cur = clean
    _, cust, sub_id = _admin_client(client, cur, "uidel")
    rid = dg.add_recipient(cur, sub_id, email="anna@example.com")

    r = client.post(
        f"/admin/digests/subscriptions/{sub_id}/recipients/{rid}/delete",
        data={"back": f"/admin/crm/{cust}?tab=alerts"},
        headers={"X-CSRF-Token": get_csrf(client)}, follow_redirects=False)
    assert r.status_code == 303
    assert dg.list_recipients(cur, sub_id) == []


def test_a_customer_cannot_touch_the_recipient_list(client, clean):
    from app import digests as dg
    cur = clean
    _, _, _, sub_id = _subscribed(cur, "uiperm")
    make_user("dgr_intruder", "goodpassword1", role="customer")
    login(client, "dgr_intruder", "goodpassword1")

    r = client.post(f"/admin/digests/subscriptions/{sub_id}/recipients",
                    data={"email": "attacker@example.com"},
                    headers={"X-CSRF-Token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code in (302, 303, 403, 404)
    assert dg.list_recipients(cur, sub_id) == []


def test_the_card_saves_the_email_format(client, clean):
    from app import digests as dg
    cur = clean
    _, cust, sub_id = _admin_client(client, cur, "uilayout")
    sub = dg.get_subscription(cur, sub_id)
    assert sub["layout"] == "list"

    r = client.post("/admin/digests/subscriptions",
                    data={"user_id": str(cust),
                          "search_profile_id": str(sub["search_profile_id"]),
                          "layout": "summary", "lang": "el",
                          "max_results": "25", "is_active": "on",
                          "back": f"/admin/crm/{cust}?tab=alerts"},
                    headers={"X-CSRF-Token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    updated = dg.get_subscription(cur, sub_id)
    assert updated["layout"] == "summary"
    # The unchecked box means "do not mail the account address" — the form
    # posts nothing for an unchecked checkbox, and that has to be honoured.
    assert updated["include_primary"] is False


def test_the_customer_card_is_split_into_tabs(client, clean):
    """The panels are all rendered; the tab bar is what gates them, and it has
    to name every one of them or a panel becomes unreachable."""
    cur = clean
    _, cust, _ = _admin_client(client, cur, "uitabs")
    page = client.get(f"/admin/crm/{cust}").text
    for key in ("ctab-details", "ctab-alerts", "ctab-activity", "ctab-email"):
        assert f'id="{key}"' in page
        assert f'data-tab="{key.removeprefix("ctab-")}"' in page
