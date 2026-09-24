"""Customers' own certificates and the expiry reminder
(docs/specs/evaluation-layer.md §10).

What is defended here:

  SELF-SERVICE  an entitled customer adds, renews and deletes their own rows
                on /account/certificates (source='customer'); a lapsed one
                gets no form and cannot write; nobody can touch another
                account's row.
  OPT-IN        no reminder without the switch — deliverability is not done.
  ONCE          each mark (60, 14 days) fires once per valid_until; a
                certificate already inside both marks gets ONE line and
                spends both; a renewal re-arms.
  ONLY SENT     the ledger is written only after the message left: a failed
                send retries on the next run.
  NEVER         expired or undated certificates, lapsed or deactivated
                accounts, accounts without an address.
  ONE MESSAGE   per customer per run, whatever the number of certificates.
"""
import datetime as dt

import pytest

from app import auth, cert_reminders as rem, eligibility_eval as ev, mailer
from tests.helpers import connect, get_csrf, grant, login, make_user

TODAY = dt.date(2030, 3, 1)


@pytest.fixture()
def memory_mail(monkeypatch):
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    monkeypatch.setenv("APP_BASE_URL", "https://example.test")
    mailer.clear_outbox()
    yield mailer
    mailer.clear_outbox()


def _cust(name, *, entitled=True, email=True):
    uid = make_user(name, "goodpassword1")
    with connect() as conn:
        if email:
            auth.set_email(conn.cursor(), uid, f"{name}@example.com")
    if entitled:
        grant(uid)
    return uid


def _declare(uid, scheme, until, **kw):
    with connect() as conn:
        return ev.save(conn.cursor(), uid, scheme=scheme,
                       valid_until=until.isoformat() if until else "", **kw)


def _opt_in(uid, lang="el"):
    with connect() as conn:
        rem.set_enabled(conn.cursor(), uid, True, lang=lang)


def _run(today=TODAY):
    with connect() as conn:
        return rem.run_due(conn.cursor(), today=today)


# --------------------------------------------------------------------------- #
# The marks — pure
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("left, spent, want", [
    (90, set(), []),
    (60, set(), [60]),
    (30, {60}, []),
    (14, {60}, [14]),
    (10, set(), [60, 14]),         # declared late: both at once
    (0, set(), [60, 14]),          # the last day still counts
    (-1, set(), []),               # expired: never chased
])
def test_due_marks(left, spent, want):
    assert rem.due_marks(TODAY + dt.timedelta(days=left), TODAY, spent) == want


def test_an_undated_certificate_is_never_due():
    assert rem.due_marks(None, TODAY, set()) == []


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #
def test_nothing_without_the_opt_in(db, memory_mail):
    uid = _cust("remoff")
    _declare(uid, "iso9001", TODAY + dt.timedelta(days=10))
    out = _run()
    assert out["sent"] == 0 and memory_mail.outbox() == []


def test_one_message_listing_what_is_due(db, memory_mail):
    uid = _cust("remone")
    _declare(uid, "iso9001", TODAY + dt.timedelta(days=10), edition="2015")
    _declare(uid, "iso13485", TODAY + dt.timedelta(days=45),
             holder="manufacturer", manufacturer="Medtek GmbH")
    _declare(uid, "iso14001", TODAY + dt.timedelta(days=200))   # not near
    _opt_in(uid)
    out = _run()
    assert out["sent"] == 1
    [msg] = memory_mail.outbox()
    assert msg["intended"] == "remone@example.com"
    assert "ISO 9001:2015" in msg["text"] and "σε 10 ημέρες" in msg["text"]
    assert "Κατασκευαστής Medtek GmbH: ISO 13485" in msg["text"]
    assert "ISO 14001" not in msg["text"]
    assert "https://example.test/account/certificates" in msg["html"]
    # Both marks spent for the 10-day one, the 60 for the other.
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) AS n FROM proc.certificate_expiry_notice")
        assert cur.fetchone()["n"] == 3


def test_each_mark_fires_once_and_a_renewal_rearms(db, memory_mail):
    uid = _cust("remonce")
    _declare(uid, "iso9001", TODAY + dt.timedelta(days=50))
    _opt_in(uid)
    assert _run()["sent"] == 1                                   # the 60 mark
    assert _run()["sent"] == 0                                   # not twice
    assert _run(TODAY + dt.timedelta(days=20))["sent"] == 0      # 30 left: nothing new
    assert _run(TODAY + dt.timedelta(days=40))["sent"] == 1      # 10 left: the 14 mark
    assert _run(TODAY + dt.timedelta(days=41))["sent"] == 0
    # Renewed: a new valid_until re-arms both marks.
    _declare(uid, "iso9001", TODAY + dt.timedelta(days=41 + 12))
    assert _run(TODAY + dt.timedelta(days=41))["sent"] == 1
    assert len(memory_mail.outbox()) == 3


def test_a_failed_send_is_retried(db, memory_mail, monkeypatch):
    uid = _cust("remfail")
    _declare(uid, "iso9001", TODAY + dt.timedelta(days=5))
    _opt_in(uid)

    def boom(**_kw):
        raise mailer.MailError("smtp down")
    monkeypatch.setattr(rem._mailer, "send", boom)
    out = _run()
    assert out["errors"] == 1 and out["sent"] == 0
    monkeypatch.undo()
    monkeypatch.setenv("EMAIL_BACKEND", "memory")
    assert _run()["sent"] == 1                   # the ledger was not written


@pytest.mark.parametrize("case", ["lapsed", "no_email", "deactivated", "expired"])
def test_who_is_never_mailed(db, memory_mail, case):
    uid = _cust(f"remno{case[:4]}", entitled=case != "lapsed",
                email=case != "no_email")
    until = TODAY - dt.timedelta(days=1) if case == "expired" else TODAY + dt.timedelta(days=5)
    _declare(uid, "iso9001", until)
    _opt_in(uid)
    if case == "deactivated":
        with connect() as conn:
            auth.set_active(conn.cursor(), uid, False)
    _run()
    assert memory_mail.outbox() == []


def test_the_reminder_follows_the_language_it_was_switched_on_in(db, memory_mail):
    uid = _cust("remen")
    _declare(uid, "iso9001", TODAY + dt.timedelta(days=5))
    _opt_in(uid, lang="en")
    _run()
    [msg] = memory_mail.outbox()
    assert "expires" in msg["text"] and "in 5 days" in msg["text"]
    assert "λήγει" not in msg["text"]


# --------------------------------------------------------------------------- #
# /account/certificates
# --------------------------------------------------------------------------- #
def _signin(client, name, **kw):
    uid = _cust(name, **kw)
    login(client, name, "goodpassword1")
    return uid, get_csrf(client)


def test_the_page_asks_a_visitor_to_sign_in(client):
    r = client.get("/account/certificates", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_a_customer_adds_renews_and_deletes(client, db):
    uid, tok = _signin(client, "selfcert")
    r = client.post("/account/certificates",
                    data={"scheme": "iso9001", "valid_until": "2031-05-01",
                          "edition": "2015", "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "Το πιστοποιητικό αποθηκεύτηκε." in page
    assert "ISO 9001" in page and "01/05/2031" in page
    client.post("/account/certificates",
                data={"scheme": "iso9001", "valid_until": "2034-05-01",
                      "csrf_token": tok})
    rows = ev.certificates(db.cursor(), uid)
    assert len(rows) == 1 and rows[0]["valid_until"] == dt.date(2034, 5, 1)
    assert rows[0]["source"] == "customer"
    client.post(f"/account/certificates/{rows[0]['id']}/delete",
                data={"csrf_token": tok})
    assert ev.certificates(db.cursor(), uid) == []


def test_a_bad_value_is_explained_not_stored(client, db):
    uid, tok = _signin(client, "selfbad")
    r = client.post("/account/certificates",
                    data={"scheme": "iso9001", "holder": "manufacturer",
                          "csrf_token": tok})
    assert "Συμπληρώστε το όνομα του κατασκευαστή." in r.text
    assert ev.certificates(db.cursor(), uid) == []


def test_a_crafted_message_is_never_shown(client):
    _signin(client, "selfmsg")
    body = client.get("/account/certificates?err=phish-text&ok=phish-text").text
    # (The language switcher echoes the URL, escaped — that is not a message.)
    assert 'class="acct-error">phish' not in body
    assert 'class="acct-ok">phish' not in body


def test_nobody_deletes_another_accounts_certificate(client, db):
    other = _cust("certowner")
    cid = _declare(other, "iso9001", TODAY)
    _uid, tok = _signin(client, "certthief")
    r = client.post(f"/account/certificates/{cid}/delete",
                    data={"csrf_token": tok}, follow_redirects=False)
    assert r.status_code == 404
    assert len(ev.certificates(db.cursor(), other)) == 1


def test_a_lapsed_customer_gets_no_form_and_cannot_write(client, db):
    uid, tok = _signin(client, "selflapsed", entitled=False)
    body = client.get("/account/certificates").text
    assert "Η συνδρομή σας δεν είναι ενεργή" in body
    assert 'action="/account/certificates"' not in body
    r = client.post("/account/certificates",
                    data={"scheme": "iso9001", "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 403
    assert ev.certificates(db.cursor(), uid) == []


def test_the_reminder_switch(client, db):
    uid, tok = _signin(client, "selfswitch")
    assert "Ενεργοποίηση υπενθυμίσεων" in client.get("/account/certificates").text
    client.post("/account/certificates/reminders", data={"on": "1", "csrf_token": tok})
    assert rem.is_enabled(db.cursor(), uid)
    assert "Απενεργοποίηση υπενθυμίσεων" in client.get("/account/certificates").text
    client.post("/account/certificates/reminders", data={"on": "", "csrf_token": tok})
    assert not rem.is_enabled(db.cursor(), uid)


def test_the_page_states_the_marks_the_code_uses(client):
    _signin(client, "selfmarks")
    body = client.get("/account/certificates").text
    assert rem.REMIND_DAYS == (60, 14)
    assert "60 και 14 ημέρες" in body


def test_the_menu_offers_it_to_entitled_customers_only(client):
    _signin(client, "selfmenu")
    assert 'href="/account/certificates"' in client.get("/account").text
    client.cookies.clear()
    _signin(client, "selfmenulapsed", entitled=False)
    assert 'href="/account/certificates"' not in client.get("/account").text


def test_the_crm_card_says_who_entered_it(client, db):
    uid = _cust("crmsrc")
    _declare(uid, "iso9001", TODAY, source="customer")
    make_user("crmsrcadmin", "goodpassword1", role="admin")
    login(client, "crmsrcadmin", "goodpassword1")
    card = client.get(f"/admin/crm/{uid}?tab=fit").text
    assert "ο πελάτης" in card
