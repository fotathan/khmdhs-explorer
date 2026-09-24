"""The checklist's dated deadlines in the calendar (tender-checklist spec, slice 2).

What is defended here:

  WHAT        only the summary's DATED timeline items become events; the
              closing date stays the act's one main event, never twice; a
              relative deadline ("within three days") never reaches a calendar.
  SHAPE       a stated time is a timed event in Athens; no time is an all-day
              event; the description says the date is an AI reading.
  IDENTITY    the UID does not contain the date, so a regenerated summary
              that moves a date moves the SAME event, with a higher SEQUENCE.
  WHERE       favourites in the subscribed feed, and the one-off download for
              an entitled reader. Not for a lapsed reader, not from a stale
              summary, not with the AI summary switched off, not for "no bid".
  CANCELLED   a cancelled act cancels its milestones too.
"""
import datetime as dt
import re

import pytest

from tests.helpers import connect, get_csrf, grant, login, make_user

ADAM = "TEST-CLCAL-0001"
ATHENS_TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date()
Q_DAY = ATHENS_TODAY + dt.timedelta(days=5)          # questions, with a time
V_DAY = ATHENS_TODAY + dt.timedelta(days=7)          # site visit, no time
CLOSE = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=14)


def _text(q_day=Q_DAY):
    return (
        "ΔΙΑΚΗΡΥΞΗ\n\n"
        f"Τα ερωτήματα υποβάλλονται έως {q_day:%d/%m/%Y} και ώρα 15:00.\n\n"
        f"Η επιτόπια επίσκεψη θα γίνει στις {V_DAY:%d/%m/%Y}.\n\n"
        "Οι απαντήσεις δίνονται εντός τριών ημερών από την υποβολή.\n"
    )


def _raw(q_day=Q_DAY):
    return {
        "timeline": [
            {"label": "Προθεσμία ερωτημάτων", "value": f"{q_day:%d/%m/%Y}, 15:00",
             "obligation": "mandatory", "confidence": "high", "source": "full_text",
             "quote": f"Τα ερωτήματα υποβάλλονται έως {q_day:%d/%m/%Y} και ώρα 15:00"},
            {"label": "Επιτόπια επίσκεψη", "value": f"{V_DAY:%d/%m/%Y}",
             "obligation": "mandatory", "confidence": "high", "source": "full_text",
             "quote": f"Η επιτόπια επίσκεψη θα γίνει στις {V_DAY:%d/%m/%Y}"},
            {"label": "Απαντήσεις", "value": "εντός τριών ημερών",
             "obligation": "unspecified", "confidence": "high", "source": "full_text",
             "quote": "Οι απαντήσεις δίνονται εντός τριών ημερών από την υποβολή"},
        ],
        "not_found": [],
    }


@pytest.fixture()
def act(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text,
                      final_submission_date, last_update_date)
                   VALUES (%s, 'notice', 'Προμήθεια δοκιμής', 'import', 'khmdhs',
                           %s, %s, now() - interval '30 days')""",
                (ADAM, _text(), CLOSE))
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
        payload = ai.verify(raw or _raw(), sources, act_row)
        ai.store(cur, ADAM, payload=payload, hash_=ai.input_hash(sources),
                 usage={}, by="test")


def _member(client, name="clcal", *, entitled=True, fav=True, stage=None):
    uid = make_user(name, "goodpassword1")
    if entitled:
        grant(uid)
    login(client, name, "goodpassword1")
    if fav:
        with connect() as conn:
            conn.cursor().execute(
                "INSERT INTO proc.user_favorite_act (user_id, adam, bid_stage) "
                "VALUES (%s, %s, %s)", (uid, ADAM, stage))
    return uid


def _feed(client):
    r = client.post("/account/calendar/new", data={"csrf_token": get_csrf(client)})
    token = re.search(r"/calendar/([A-Za-z0-9_-]+)\.ics", r.text).group(1)
    client.cookies.clear()
    body = client.get(f"/calendar/{token}.ics").text
    return body.replace("\r\n ", "")


def _events(body):
    return re.findall(r"BEGIN:VEVENT\r\n(.*?)END:VEVENT", body, re.S)


def _prop(ev, name):
    m = re.search(rf"^{name}[;:](.*)$", ev, re.M)
    return m.group(1).strip() if m else None


def _milestones(body):
    return [e for e in _events(body) if f"UID:{ADAM}-m-" in e]


# --------------------------------------------------------------------------- #
def test_the_feed_carries_the_dated_milestones_of_a_favourite(client, act, ai_on):
    _store()
    _member(client)
    body = _feed(client)
    main = [e for e in _events(body) if f"UID:{ADAM}@khmdhs" in e]
    assert len(main) == 1                                  # closing date once
    miles = _milestones(body)
    assert len(miles) == 2                                 # not the relative one
    by_summary = {_prop(e, "SUMMARY").split(" — ")[0]: e for e in miles}
    q = by_summary["Προθεσμία ερωτημάτων"]
    # 15:00 Athens (UTC+3 in summer, +2 in winter) written in UTC.
    athens = dt.datetime.combine(Q_DAY, dt.time(15, 0),
                                 tzinfo=__import__("zoneinfo").ZoneInfo("Europe/Athens"))
    assert _prop(q, "DTSTART") == athens.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    v = by_summary["Επιτόπια επίσκεψη"]
    assert _prop(v, "DTSTART") == f"VALUE=DATE:{V_DAY:%Y%m%d}"
    for e in miles:
        assert "σύνοψη AI" in _prop(e, "DESCRIPTION")
        assert _prop(e, "URL").endswith("#tab-checklist")
        assert _prop(e, "STATUS") == "CONFIRMED"
    assert "Απαντήσεις" not in body


def test_a_moved_date_moves_the_same_event(client, act, ai_on, db):
    _store()
    # The first summary is a day old, so the regeneration is visibly later
    # (SEQUENCE counts seconds).
    db.cursor().execute("UPDATE proc.act_ai_summary SET generated_at = now() - "
                        "interval '1 day' WHERE adam=%s", (ADAM,))
    _member(client)
    before = {_prop(e, "UID"): e for e in _milestones(_feed(client))}
    new_q = Q_DAY + dt.timedelta(days=2)
    db.cursor().execute("UPDATE proc.procurement_act SET full_text=%s WHERE adam=%s",
                        (_text(new_q), ADAM))
    _store(_raw(new_q))
    login(client, "clcal", "goodpassword1")
    after = {_prop(e, "UID"): e for e in _milestones(_feed(client))}
    assert set(before) == set(after)                       # same UIDs
    uid = next(u for u, e in after.items() if "ερωτημάτων" in e)
    assert _prop(after[uid], "DTSTART") != _prop(before[uid], "DTSTART")
    assert int(_prop(after[uid], "SEQUENCE")) > int(_prop(before[uid], "SEQUENCE"))


def test_a_stale_summary_puts_nothing_in_the_calendar(client, act, ai_on, db):
    _store()
    db.cursor().execute("UPDATE proc.procurement_act SET full_text = full_text || 'x' "
                        "WHERE adam=%s", (ADAM,))
    _member(client)
    assert _milestones(_feed(client)) == []


def test_nothing_when_the_ai_summary_is_off(client, act, monkeypatch):
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    _store()
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "0")
    _member(client)
    body = _feed(client)
    assert _milestones(body) == [] and f"UID:{ADAM}@khmdhs" in body


def test_a_no_bid_favourite_brings_no_milestones(client, act, ai_on):
    _store()
    _member(client, stage="no_bid")
    assert _milestones(_feed(client)) == []


def test_a_cancelled_act_cancels_its_milestones(client, act, ai_on, db):
    _store()
    db.cursor().execute("UPDATE proc.procurement_act SET cancelled = true "
                        "WHERE adam=%s", (ADAM,))
    _member(client)
    miles = _milestones(_feed(client))
    assert miles and all(_prop(e, "STATUS") == "CANCELLED" for e in miles)
    assert all("BEGIN:VALARM" not in e for e in miles)


def test_the_download_carries_them_for_an_entitled_reader_only(client, act, ai_on):
    _store()
    _member(client, fav=False)                            # no star needed here
    assert len(_milestones(client.get(f"/act/{ADAM}/calendar.ics").text
                           .replace("\r\n ", ""))) == 2
    client.cookies.clear()
    _member(client, "clcal_lapsed", entitled=False, fav=False)
    body = client.get(f"/act/{ADAM}/calendar.ics").text
    assert f"UID:{ADAM}@khmdhs" in body and _milestones(body) == []


def test_two_items_with_the_same_label_get_different_uids():
    from app import tender_checklist as tc
    act = {"full_text": "", "final_submission_date": None}
    item = lambda d: {"label": "Επίσκεψη", "value": f"{d:%d/%m/%Y}",   # noqa: E731
                      "quote": "x", "source": "full_text", "confidence": "high"}
    payload = {"sections": [{"key": "timeline",
                             "items": [item(Q_DAY), item(V_DAY)]}]}

    class _C:                                             # current_row stand-in
        pass
    orig = tc.current_row
    tc.current_row = lambda c, adam: (act, payload, None)
    try:
        keys = [m["uid_key"] for m in tc.milestones(_C(), "X")]
    finally:
        tc.current_row = orig
    assert len(keys) == 2 and len(set(keys)) == 2 and keys[1].endswith("-2")
