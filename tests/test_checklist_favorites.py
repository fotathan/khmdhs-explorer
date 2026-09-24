"""Checklist progress on /account/favorites (tender-checklist spec, slice 3).

What is defended here:

  NUMBERS     "n_done / n_tasks" counts only ticks on items the CURRENT
              checklist still has — the same rule as the act-page panel, so
              the two can never show different numbers.
  NEXT        the soonest UPCOMING dated deadline the summary found; the
              closing date (already on the card) and past dates never.
  WHEN        only for an act with a current summary, an entitled customer,
              AI summaries on, and a favourite still in play (no stage,
              bidding, submitted) — never won / lost / no_bid.
  PRIVACY     another customer's ticks never count.
"""
import datetime as dt

import pytest

from tests.helpers import connect, grant, login, make_user

ADAM = "TEST-CLFAV-0001"
ATHENS_TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date()
PAST = ATHENS_TODAY - dt.timedelta(days=3)
SOON = ATHENS_TODAY + dt.timedelta(days=4)
LATER = ATHENS_TODAY + dt.timedelta(days=9)

TEXT = (
    "ΔΙΑΚΗΡΥΞΗ\n\n"
    f"Η επίσκεψη έγινε στις {PAST:%d/%m/%Y}.\n\n"
    f"Τα ερωτήματα υποβάλλονται έως {SOON:%d/%m/%Y} και ώρα 12:00.\n\n"
    f"Η αποσφράγιση θα γίνει στις {LATER:%d/%m/%Y}.\n\n"
    "Απαιτείται πιστοποίηση ISO 9001:2015 σε ισχύ.\n\n"
    "Η προσφορά συνοδεύεται από το ΕΕΕΣ.\n"
)


def _item(label, value, quote, obligation="mandatory"):
    return {"label": label, "value": value, "obligation": obligation,
            "confidence": "high", "source": "full_text", "quote": quote}


RAW = {
    "timeline": [
        _item("Επίσκεψη", f"{PAST:%d/%m/%Y}", f"Η επίσκεψη έγινε στις {PAST:%d/%m/%Y}"),
        _item("Αποσφράγιση", f"{LATER:%d/%m/%Y}", f"Η αποσφράγιση θα γίνει στις {LATER:%d/%m/%Y}"),
        _item("Ερωτήματα", f"{SOON:%d/%m/%Y}, 12:00",
              f"Τα ερωτήματα υποβάλλονται έως {SOON:%d/%m/%Y} και ώρα 12:00"),
    ],
    "eligibility": [_item("ISO", "ISO 9001:2015", "πιστοποίηση ISO 9001:2015 σε ισχύ")],
    "submission": [_item("ΕΕΕΣ", "Με την προσφορά", "συνοδεύεται από το ΕΕΕΣ",
                         obligation="unspecified")],
    "not_found": [],
}


@pytest.fixture()
def act(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text,
                      final_submission_date)
                   VALUES (%s, 'notice', 'Προμήθεια αγαπημένου', 'import',
                           'khmdhs', %s, now() + interval '20 days')""",
                (ADAM, TEXT))
    yield ADAM
    cur.execute("DELETE FROM proc.act_ai_summary_history WHERE adam=%s", (ADAM,))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))


@pytest.fixture()
def ai_on(monkeypatch):
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    from app import ai_summary as _ai
    monkeypatch.setenv(_ai.key_var(), "test-key-not-used")


def _store(raw=None):
    from app import ai_summary as ai
    with connect() as conn:
        cur = conn.cursor()
        act_row, sources = ai.load_inputs(cur, ADAM)
        payload = ai.verify(raw or RAW, sources, act_row)
        ai.store(cur, ADAM, payload=payload, hash_=ai.input_hash(sources),
                 usage={}, by="test")


def _member(client, name="clfav", *, entitled=True, stage=None):
    uid = make_user(name, "goodpassword1")
    if entitled:
        grant(uid)
    login(client, name, "goodpassword1")
    with connect() as conn:
        conn.cursor().execute(
            "INSERT INTO proc.user_favorite_act (user_id, adam, bid_stage) "
            "VALUES (%s, %s, %s)", (uid, ADAM, stage))
    return uid


def _tick(uid, label, quote, section="eligibility"):
    from app import tender_checklist as tc
    with connect() as conn:
        conn.cursor().execute(
            "INSERT INTO proc.act_checklist_tick (user_id, adam, item_key) "
            "VALUES (%s, %s, %s)", (uid, ADAM, tc.item_key(section, label, quote)))


# --------------------------------------------------------------------------- #
def test_progress_and_next_deadline(db, act, ai_on):
    from app import tender_checklist as tc
    _store()
    uid = make_user("clfav_unit")
    _tick(uid, "ISO", "πιστοποίηση ISO 9001:2015 σε ισχύ")
    got = tc.progress_for(db.cursor(), uid, [ADAM, "NOT-THERE"])
    assert list(got) == [ADAM]
    p = got[ADAM]
    assert (p["n_done"], p["n_tasks"]) == (1, 2)
    # The soonest UPCOMING one: not the past visit, not the later opening,
    # and never the record's closing date.
    assert (p["next"]["label"], p["next"]["date"], p["next"]["time"]) == \
           ("Ερωτήματα", SOON, "12:00")


def test_only_current_items_are_counted(db, act, ai_on):
    """A tick on an item the summary no longer has does not count — the
    panel's rule — and neither does another customer's tick."""
    from app import tender_checklist as tc
    _store()
    uid = make_user("clfav_mine")
    other = make_user("clfav_other")
    _tick(uid, "ISO παλιό", "κάτι που δεν υπάρχει πια")
    _tick(other, "ISO", "πιστοποίηση ISO 9001:2015 σε ισχύ")
    assert tc.progress_for(db.cursor(), uid, [ADAM])[ADAM]["n_done"] == 0


def test_nothing_without_a_current_summary(db, act, ai_on):
    from app import tender_checklist as tc
    assert tc.progress_for(db.cursor(), 1, [ADAM]) == {}
    _store()
    db.cursor().execute("UPDATE proc.procurement_act SET full_text = full_text || 'x' "
                        "WHERE adam=%s", (ADAM,))
    assert tc.progress_for(db.cursor(), 1, [ADAM]) == {}


def test_nothing_when_the_ai_summary_is_off(db, act, monkeypatch):
    from app import tender_checklist as tc
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    _store()
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "false")
    assert tc.progress_for(db.cursor(), 1, [ADAM]) == {}


def test_the_favourites_page_shows_the_line(client, act, ai_on):
    _store()
    uid = _member(client, stage="bidding")
    _tick(uid, "ΕΕΕΣ", "συνοδεύεται από το ΕΕΕΣ", section="submission")
    body = client.get("/account/favorites").text
    assert 'class="fav-cl' in body
    assert f'href="/act/{ADAM}#tab-checklist"' in body
    assert "1 / 2" in body
    assert "Ερωτήματα" in body and f"{SOON:%d/%m}" in body


@pytest.mark.parametrize("stage", [None, "submitted"])
def test_shown_while_the_bid_is_open(client, act, ai_on, stage):
    _store()
    _member(client, stage=stage)
    assert 'class="fav-cl' in client.get("/account/favorites").text


@pytest.mark.parametrize("stage", ["won", "lost", "no_bid"])
def test_hidden_once_the_bid_is_decided(client, act, ai_on, stage):
    _store()
    _member(client, stage=stage)
    assert 'class="fav-cl' not in client.get("/account/favorites").text


def test_hidden_for_a_lapsed_customer(client, act, ai_on):
    _store()
    _member(client, entitled=False)
    body = client.get("/account/favorites").text
    assert "Προμήθεια αγαπημένου" in body            # the favourite itself is there
    assert 'class="fav-cl' not in body
