"""The per-tender checklist and deadline set (app/tender_checklist.py).

docs/specs/tender-checklist.md. What is defended here:

  DERIVED     the checklist is built from a CURRENT summary payload and the
              record, on every request. No summary, a stale summary, or a
              non-notice → no panel. Nothing is generated here.
  TASKS       eligibility + submission give every item; pricing + requirements
              only the mandatory ones; award / attention / timeline never.
  DATES       a timeline item is dated only when it names exactly ONE date;
              the record's closing date is counted on the Athens calendar.
  TICKS       per user, idempotent, only for keys in the current checklist,
              and invisible to every other user.
  ISOLATION   the summary is one row per act served to everyone. This module
              reads it and never writes it; ai_summary.py never reads ticks.
  GATING      entitled readers only; the GET is empty (never an error) for
              everyone else, and the tab removes itself.

Payloads are always built through ai_summary.verify(), so a test can only
assert on shapes the real pipeline produces.
"""
import datetime as dt
import pathlib
import re

import pytest

from tests.helpers import connect, get_csrf, grant, login, make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADAM = "TEST-CHECKLIST-0001"

FULL_TEXT = (
    "ΔΙΑΚΗΡΥΞΗ ΑΝΟΙΚΤΟΥ ΔΙΑΓΩΝΙΣΜΟΥ\n"
    "Αντικείμενο είναι η προμήθεια ιατροτεχνολογικού εξοπλισμού.\n"
    "\n"
    "Η προθεσμία υποβολής ερωτημάτων λήγει στις 12/06/2026 και ώρα 15:00.\n"
    "\n"
    "Η εγγύηση συμμετοχής ορίζεται σε ποσοστό 2% της εκτιμώμενης αξίας, ήτοι "
    "ποσού 20.000 ευρώ, και κατατίθεται με την προσφορά.\n"
    "\n"
    "Η πληρωμή γίνεται εντός 60 ημερών από την παραλαβή.\n"
    "\n"
    "Οι συμμετέχοντες οφείλουν να διαθέτουν πιστοποίηση ISO 9001:2015 σε ισχύ.\n"
    "\n"
    "Η προσφορά υποβάλλεται ηλεκτρονικά μέσω ΕΣΗΔΗΣ μαζί με το ΕΕΕΣ.\n"
    "\n"
    "Βαθμολογία τεχνικής προσφοράς 70% και οικονομικής 30%.\n"
)


def _item(label, value, quote, obligation="mandatory", confidence="high"):
    return {"label": label, "value": value, "obligation": obligation,
            "confidence": confidence, "source": "full_text", "quote": quote}


RAW = {
    "timeline": [_item("Προθεσμία ερωτημάτων", "12/06/2026, 15:00",
                       "Η προθεσμία υποβολής ερωτημάτων λήγει στις 12/06/2026")],
    "award": [_item("Στάθμιση", "70% τεχνική, 30% οικονομική",
                    "Βαθμολογία τεχνικής προσφοράς 70% και οικονομικής 30%")],
    "pricing": [
        _item("Εγγύηση συμμετοχής", "2%, 20.000 €",
              "Η εγγύηση συμμετοχής ορίζεται σε ποσοστό 2%"),
        _item("Όροι πληρωμής", "60 ημέρες",
              "Η πληρωμή γίνεται εντός 60 ημερών από την παραλαβή",
              obligation="unspecified"),
    ],
    "eligibility": [_item("Πιστοποίηση ποιότητας", "ISO 9001:2015",
                          "πιστοποίηση ISO 9001:2015 σε ισχύ")],
    "submission": [_item("ΕΕΕΣ", "Ηλεκτρονικά μέσω ΕΣΗΔΗΣ, με ΕΕΕΣ",
                         "υποβάλλεται ηλεκτρονικά μέσω ΕΣΗΔΗΣ μαζί με το ΕΕΕΣ",
                         obligation="unspecified")],
    "not_found": [],
}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def act(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text,
                      final_submission_date)
                   VALUES (%s, 'notice', 'Προμήθεια εξοπλισμού', 'import',
                           'khmdhs', %s, '2026-06-20 10:00+03')""",
                (ADAM, FULL_TEXT))
    yield ADAM
    cur.execute("DELETE FROM proc.act_ai_summary_history WHERE adam=%s", (ADAM,))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))


@pytest.fixture()
def ai_on(monkeypatch):
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    from app import ai_summary as _ai
    monkeypatch.setenv(_ai.key_var(), "test-key-not-used")
    from app.main import templates
    monkeypatch.setitem(templates.env.globals, "ai_summary_enabled", True)


def _store(adam=ADAM, raw=None):
    from app import ai_summary as ai
    with connect() as conn:
        cur = conn.cursor()
        act_row, sources = ai.load_inputs(cur, adam)
        payload = ai.verify(raw if raw is not None else RAW, sources, act_row)
        ai.store(cur, adam, payload=payload, hash_=ai.input_hash(sources),
                 usage={"input_tokens": 1, "output_tokens": 1}, by="test")
    return payload


def _customer(client, name="cl_cust"):
    uid = make_user(name, "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, name, "goodpassword1")
    return uid


def _built(adam=ADAM, today=dt.date(2026, 6, 1)):
    from app import tender_checklist as tc
    with connect() as conn:
        act_row, payload = tc.current(conn.cursor(), adam)
    return tc.build(payload, act_row, today=today)


def _post(client, adam, key, done):
    return client.post(f"/act/{adam}/checklist/{key}",
                       data={"done": "1"} if done else {},
                       headers={"X-CSRF-Token": get_csrf(client)})


# --------------------------------------------------------------------------- #
# Pure: dates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,quote,want", [
    ("12/06/2026, 15:00", "", (dt.date(2026, 6, 12), "15:00")),
    ("12.06.2026", "", (dt.date(2026, 6, 12), None)),
    ("2026-06-12", "", (dt.date(2026, 6, 12), None)),
    ("Δευτέρα 12 Ιουνίου 2026 ώρα 10:30", "", (dt.date(2026, 6, 12), "10:30")),
    ("έως 3 ΜΑΪΟΥ 2027", "", (dt.date(2027, 5, 3), None)),
    # Nothing in the value — the quote is the source's own words.
    ("δέκα ημέρες πριν", "λήγει στις 05/06/2026", (dt.date(2026, 6, 5), None)),
    # Two different dates is two milestones in one item: never guess.
    ("ερωτήματα έως 12/06/2026, απαντήσεις έως 16/06/2026", "", (None, None)),
    ("31/02/2026", "", (None, None)),                   # not a real date
    ("δέκα ημέρες πριν την υποβολή", "", (None, None)),
])
def test_parse_when(value, quote, want):
    from app import tender_checklist as tc
    assert tc.parse_when(value, quote) == want


def test_an_ambiguous_value_does_not_fall_through_to_the_quote():
    from app import tender_checklist as tc
    assert tc.parse_when("12/06/2026 ή 16/06/2026", "λήγει 12/06/2026") == (None, None)


def test_the_item_key_ignores_accents_and_case_but_not_the_section():
    from app import tender_checklist as tc
    a = tc.item_key("eligibility", "Πιστοποίηση", "ISO 9001 σε ισχύ")
    assert a == tc.item_key("eligibility", "ΠΙΣΤΟΠΟΙΗΣΗ", "iso 9001 σε ισχυ")
    assert a != tc.item_key("submission", "Πιστοποίηση", "ISO 9001 σε ισχύ")
    assert tc.KEY_RE.match(a)


# --------------------------------------------------------------------------- #
# Derived from the summary
# --------------------------------------------------------------------------- #
def test_which_extracted_items_become_tasks(db, act):
    _store()
    cl = _built()
    labels = {g["key"]: [i["label"] for i in g["items"]] for g in cl["groups"]}
    assert labels == {
        "eligibility": ["Πιστοποίηση ποιότητας"],
        "pricing": ["Εγγύηση συμμετοχής"],        # not the payment terms
        "submission": ["ΕΕΕΣ"],                   # unspecified still counts here
    }
    assert cl["n_tasks"] == 3
    every = str(cl)
    assert "Στάθμιση" not in every                # award weights: read, not do


def test_the_deadline_set_is_the_record_plus_the_timeline_sorted(db, act):
    _store()
    cl = _built(today=dt.date(2026, 6, 10))
    got = [(d["label"], d["date"], d["time"], d["source"]) for d in cl["deadlines"]]
    assert got == [
        ("Προθεσμία ερωτημάτων", dt.date(2026, 6, 12), "15:00", "ai"),
        ("Υποβολή προσφορών", dt.date(2026, 6, 20), "10:00", "record"),
    ]
    assert [d["days_left"] for d in cl["deadlines"]] == [2, 10]
    assert cl["deadlines"][0]["anchor"].startswith("ft-p-")


def test_the_closing_date_is_counted_on_the_athens_calendar(db, act):
    """00:30 in Athens is still the previous day in UTC. Counting from the UTC
    date would give the bidder one day more than they have."""
    db.cursor().execute("""UPDATE proc.procurement_act
                              SET final_submission_date = '2026-06-20 00:30+03'
                            WHERE adam = %s""", (act,))
    _store()
    rec = [d for d in _built()["deadlines"] if d["source"] == "record"][0]
    assert (rec["date"], rec["time"]) == (dt.date(2026, 6, 20), "00:30")


def test_no_summary_means_no_checklist(db, act):
    from app import tender_checklist as tc
    assert tc.current(db.cursor(), act) is None


def test_a_stale_summary_means_no_checklist(db, act):
    from app import tender_checklist as tc
    _store()
    db.cursor().execute("UPDATE proc.procurement_act SET full_text = full_text || 'x' "
                        "WHERE adam = %s", (act,))
    assert tc.current(db.cursor(), act) is None


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def test_the_panel_renders_for_an_entitled_reader(client, act, ai_on):
    _store()
    _customer(client)
    r = client.get(f"/act/{act}/checklist")
    assert r.status_code == 200
    assert 'id="checklist-mount"' in r.text
    assert "Πιστοποίηση ποιότητας" in r.text
    assert "0 / 3" in r.text
    assert "Εργαλείο πρώτης αξιολόγησης" in r.text   # the permanent warning


def test_ticking_is_per_user_idempotent_and_reversible(client, act, ai_on):
    _store()
    uid = _customer(client)
    key = _built()["groups"][0]["items"][0]["key"]
    for _ in range(2):
        r = _post(client, act, key, True)
        assert r.status_code == 200 and "1 / 3" in r.text
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) AS n FROM proc.act_checklist_tick "
                    "WHERE user_id=%s AND adam=%s", (uid, act))
        assert cur.fetchone()["n"] == 1
    r = _post(client, act, key, False)
    assert "0 / 3" in r.text


def test_another_customer_does_not_see_my_ticks(client, act, ai_on):
    _store()
    _customer(client, "cl_one")
    key = _built()["groups"][0]["items"][0]["key"]
    _post(client, act, key, True)
    client.cookies.clear()
    _customer(client, "cl_two")
    assert "0 / 3" in client.get(f"/act/{act}/checklist").text


def test_only_a_key_from_the_current_checklist_is_accepted(client, act, ai_on):
    _store()
    _customer(client)
    from app import tender_checklist as tc
    award_key = tc.item_key("award", "Στάθμιση",
                            "Βαθμολογία τεχνικής προσφοράς 70% και οικονομικής 30%")
    assert _post(client, act, award_key, True).status_code == 404   # not a task
    assert _post(client, act, "0" * 20, True).status_code == 404
    assert _post(client, act, "not-a-key", True).status_code == 404
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) AS n FROM proc.act_checklist_tick")
        assert cur.fetchone()["n"] == 0


def test_ticks_for_reworded_items_are_kept_and_counted(client, act, ai_on):
    _store()
    _customer(client)
    key = _built()["groups"][0]["items"][0]["key"]
    _post(client, act, key, True)
    raw = dict(RAW)
    raw["eligibility"] = [_item("Πιστοποιητικό ISO", "ISO 9001:2015",
                                "πιστοποίηση ISO 9001:2015 σε ισχύ")]
    _store(raw=raw)
    text = client.get(f"/act/{act}/checklist").text
    assert "0 / 3" in text
    assert "1 σημείωση" in text


def test_gated_and_anonymous_readers_get_nothing(client, act, ai_on):
    _store()
    assert client.get(f"/act/{act}/checklist").text == ""
    make_user("cl_lapsed", "goodpassword1")            # signed in, no grant
    login(client, "cl_lapsed", "goodpassword1")
    assert client.get(f"/act/{act}/checklist").text == ""
    key = _built()["groups"][0]["items"][0]["key"]
    assert _post(client, act, key, True).status_code == 403


def test_off_when_the_ai_summary_is_off(client, act, monkeypatch):
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "false")
    _store()
    _customer(client)
    assert client.get(f"/act/{act}/checklist").text == ""


def test_the_act_page_mounts_the_tab_for_an_entitled_reader(client, act, ai_on):
    _customer(client)
    body = client.get(f"/act/{act}").text
    section = body[body.rindex("<section", 0, body.index('id="tab-checklist"')):]
    assert "data-autohide" in section[: section.index(">")]
    assert f'/act/{act}/checklist' in section[: section.index("</section>")]


# --------------------------------------------------------------------------- #
# Isolation — the summary never carries customer data
# --------------------------------------------------------------------------- #
def test_this_module_never_writes_the_shared_summary():
    src = (ROOT / "app" / "tender_checklist.py").read_text()
    code = re.sub(r'"""[\s\S]*?"""', "", src)          # docstrings may say the name
    assert not re.search(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+proc\.act_ai_summary",
                         code, re.I)
    assert "_ai.store" not in code and "_ai.generate" not in code
    # …and reads no company profile either: this is not fit scoring.
    for forbidden in ("company_profile", "customer_profile", "import fit",
                      "fit as _fit"):
        assert forbidden not in code


def test_the_summary_never_reads_the_checklist():
    src = (ROOT / "app" / "ai_summary.py").read_text()
    assert "act_checklist" not in src
    assert "tender_checklist" not in src
