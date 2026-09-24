"""The fit score, shown to the customer it belongs to (app/account_fit.py).

What is protected here: nobody sees fit until an admin switches the profile on
(company_profile.is_active); a customer only ever sees their OWN; the act panel
answers empty rather than erroring; every score comes with its parts, in the
customer's voice; and the module stays on its side of the isolation rule.
Needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import pathlib

import pytest

from app import account_fit, fit
from tests.helpers import get_csrf, grant, login, make_user
from tests.test_fit import _admin, _code_strings, firm  # noqa: F401 — fixture

ROOT = pathlib.Path(__file__).resolve().parent.parent
OPEN = "FITOPEN9"            # an open notice in the firm's own CPV
OTHER = "FITOPEN8"           # an open notice in a division it never touched


def _notice(cur, adam, cpv, days=10):
    cur.execute("""INSERT INTO proc.cpv_code (cpv_code, description)
                   VALUES (%s, 'δοκιμή') ON CONFLICT (cpv_code) DO NOTHING""", (cpv,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, authority_id,
                      nuts_code, total_cost_with_vat, final_submission_date)
                   VALUES (%s,'notice','Ανοικτή δοκιμής','import','khmdhs',
                           'FITAUTH','EL303', 12000,
                           now() + make_interval(days => %s))""", (adam, days))
    cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                   VALUES (%s,'είδος') RETURNING id""", (adam,))
    od = cur.fetchone()["id"]
    cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                   VALUES (%s, %s)""", (od, cpv))


@pytest.fixture()
def customer(firm):  # noqa: F811
    """The fit fixture's firm, profile derived, one open notice that fits and
    one that does not. Profile NOT switched on — that is each test's call."""
    uid, cur = firm
    grant(uid)
    fit.seed_from_ledger(cur, uid)
    _notice(cur, OPEN, "33184100")
    _notice(cur, OTHER, "90910000")
    return uid, cur


def _show(cur, uid, on=True):
    cur.execute("UPDATE proc.company_profile SET is_active=%s WHERE user_id=%s",
                (on, uid))


def _signin(client):
    login(client, "fitcust", "goodpassword1")


# --------------------------------------------------------------------------- #
# Nobody sees it until an admin switches it on
# --------------------------------------------------------------------------- #
def test_the_page_sends_a_signed_out_visitor_to_login(client):
    r = client.get("/account/fit", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/account/fit"


def test_a_profile_that_is_not_switched_on_shows_nothing(client, customer):
    _signin(client)
    r = client.get("/account/fit")
    assert r.status_code == 200
    assert OPEN not in r.text
    assert "δεν έχει ενεργοποιηθεί ακόμη" in r.text
    assert client.get(f"/act/{OPEN}/fit").text == ""


def test_switched_on_the_page_ranks_and_explains(client, customer):
    uid, cur = customer
    _show(cur, uid)
    _signin(client)
    r = client.get("/account/fit")
    assert OPEN in r.text
    assert OTHER not in r.text            # never a candidate: other division
    # The parts, in the customer's voice — never the admin's third person.
    assert "ίδιος κωδικός CPV με συμβάσεις που έχετε κερδίσει" in r.text
    assert "έχετε ξανά σύμβαση με αυτή την αναθέτουσα" in r.text
    assert "δραστηριοποιούνται" not in r.text
    assert "fitc-score" in r.text


def test_the_act_panel_scores_a_notice_for_its_owner(client, customer):
    uid, cur = customer
    _show(cur, uid)
    _signin(client)
    r = client.get(f"/act/{OPEN}/fit")
    assert r.status_code == 200 and "act-fit" in r.text
    assert "fitc-hi" in r.text


def test_the_act_panel_is_empty_for_anything_but_a_notice(client, customer):
    uid, cur = customer
    _show(cur, uid)
    _signin(client)
    assert client.get("/act/FIT0000/fit").text == ""       # a contract
    assert client.get("/act/NOSUCHACT/fit").text == ""


def test_a_capped_score_says_why(client, customer):
    uid, cur = customer
    _show(cur, uid)
    _signin(client)
    r = client.get(f"/act/{OTHER}/fit")
    assert "η βαθμολογία περιορίζεται" in r.text


def test_a_lapsed_customer_sees_no_fit(client, customer):
    uid, cur = customer
    _show(cur, uid)
    cur.execute("""UPDATE proc.user_subscription
                      SET expires_at = now() - interval '1 day'
                    WHERE user_id = %s""", (uid,))
    _signin(client)
    r = client.get("/account/fit")
    assert OPEN not in r.text
    assert "ενεργή συνδρομή" in r.text
    assert client.get(f"/act/{OPEN}/fit").text == ""


def test_anonymous_gets_an_empty_panel(client, customer):
    uid, cur = customer
    _show(cur, uid)
    assert client.get(f"/act/{OPEN}/fit").text == ""


def test_another_customer_never_sees_this_firms_fit(client, customer):
    """No user id travels in any URL; a second entitled customer with no
    profile of their own gets the empty state, not the first one's ranking."""
    uid, cur = customer
    _show(cur, uid)
    other = make_user("fitneighbour", "goodpassword1")
    grant(other)
    login(client, "fitneighbour", "goodpassword1")
    assert OPEN not in client.get("/account/fit").text
    assert client.get(f"/act/{OPEN}/fit").text == ""


def test_the_account_tab_is_offered_to_entitled_customers(client, customer):
    _signin(client)
    assert 'href="/account/fit"' in client.get("/account/favorites").text


def test_the_act_page_asks_for_the_panel_only_when_signed_in(client, customer):
    assert f'hx-get="/act/{OPEN}/fit"' not in client.get(f"/act/{OPEN}").text
    _signin(client)
    assert f'hx-get="/act/{OPEN}/fit"' in client.get(f"/act/{OPEN}").text


# --------------------------------------------------------------------------- #
# The admin switch
# --------------------------------------------------------------------------- #
def _row(cur, uid):
    cur.execute("SELECT is_active FROM proc.company_profile WHERE user_id=%s", (uid,))
    r = cur.fetchone()
    return r and r["is_active"]


def test_an_admin_switches_it_on_and_off(client, customer):
    uid, cur = customer
    token = _admin(client)
    r = client.post(f"/admin/crm/{uid}/fit/visible", data={"on": "1"},
                    headers={"X-CSRF-Token": token}, follow_redirects=False)
    assert r.status_code == 303 and "tab=fit" in r.headers["location"]
    assert _row(cur, uid) is True
    card = client.get(f"/admin/crm/{uid}").text
    assert "Απόκρυψη από τον πελάτη" in card
    client.post(f"/admin/crm/{uid}/fit/visible", data={"on": ""},
                headers={"X-CSRF-Token": token})
    assert _row(cur, uid) is False


def test_a_customer_cannot_switch_it_on(client, customer):
    uid, cur = customer
    _signin(client)
    r = client.post(f"/admin/crm/{uid}/fit/visible", data={"on": "1"},
                    headers={"X-CSRF-Token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code in (303, 403)
    assert _row(cur, uid) is False


def test_a_profile_without_cpv_history_cannot_be_shown(client, db):
    cur = db.cursor()
    uid = make_user("fitempty", "goodpassword1")
    cur.execute("""INSERT INTO proc.company_profile (user_id, derived_at)
                   VALUES (%s, now())""", (uid,))
    token = _admin(client)
    client.post(f"/admin/crm/{uid}/fit/visible", data={"on": "1"},
                headers={"X-CSRF-Token": token})
    assert _row(cur, uid) is False


# --------------------------------------------------------------------------- #
# Ranking and wording
# --------------------------------------------------------------------------- #
def test_every_open_candidate_is_scored_not_just_the_soonest():
    """The ranking used to score only the 200 notices closing soonest — about
    12% of a real profile's candidates — so the best fits could be missed."""
    import inspect
    default = inspect.signature(fit.open_tenders).parameters["limit"].default
    assert default == fit.CANDIDATE_CAP >= 5000


def test_every_scorer_phrase_has_a_customer_version():
    """Run every branch of every component; each `why` must be addressed to
    the customer. A new phrase in fit.py without one fails here."""
    full = fit.Profile(user_id=1, cpv={"33": 1.0, "3318": 1.0, "33184100": 1.0},
                       nuts={"EL30"}, buyers={"A"}, value_min=10, value_max=100)
    empty = fit.Profile(user_id=1)
    phrases = set()
    for p in (full, empty):
        for cpvs in ([], ["33184100-4"], ["33180000"], ["33700000"], ["90910000"]):
            phrases.add(fit.score_cpv(p, cpvs)[1])
        for v in (None, 0, 50, 150, 1000, 1):
            phrases.add(fit.score_value(p, v)[1])
        for n in (None, "EL303", "EL521", "CY000"):
            phrases.add(fit.score_geo(p, n)[1])
        for a in (None, "A", "B"):
            phrases.add(fit.score_buyer(p, a)[1])
    missing = sorted(ph for ph in phrases if ph not in account_fit.CUSTOMER_WHY)
    assert not missing, missing


def test_the_customer_side_never_touches_the_shared_summary():
    code = _code_strings(str(ROOT / "app" / "account_fit.py"))
    for forbidden in ("act_ai_summary", "ai_summary_job", "ai_summary"):
        assert forbidden not in code, forbidden


def test_a_hidden_duplicate_is_not_a_candidate(customer):
    """A Tender Service copy of an act we already show (duplicate_of set) must
    not appear as a second, separate tender in the ranking."""
    uid, cur = customer
    _notice(cur, "FITDUP1", "33184100")
    cur.execute("UPDATE proc.procurement_act SET duplicate_of=%s WHERE adam='FITDUP1'",
                (OPEN,))
    adams = [r["adam"] for r in fit.rank(cur, uid, limit=50)["rows"]]
    assert OPEN in adams and "FITDUP1" not in adams
