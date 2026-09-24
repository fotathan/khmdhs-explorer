"""The customer's own checklist items (tender-checklist spec, slice 4).

What is defended here:

  PRIVATE     every read and write is keyed on the signed-in user AND the act
              in the URL: another customer's item id is a 404, never an edit.
  BOUNDED     text is trimmed and 1..200 chars; at most OWN_MAX_PER_ACT per
              act. A rejection re-renders the panel with the reason.
  COUNTED     own items count in the panel headline AND on /account/favorites
              — one arithmetic for both pages.
  ROUTING     /checklist/own is not swallowed by /checklist/{key}.
  GATING      entitled readers only; only on an act that has a checklist.
  ISOLATION   the shared summary never sees them.
"""
import pytest

from tests.helpers import connect, get_csrf, grant, login, make_user

ADAM = "TEST-CLOWN-0001"
TEXT = ("ΔΙΑΚΗΡΥΞΗ\n\n"
        "Απαιτείται πιστοποίηση ISO 9001:2015 σε ισχύ.\n")
RAW = {"eligibility": [{"label": "ISO", "value": "ISO 9001:2015",
                        "obligation": "mandatory", "confidence": "high",
                        "source": "full_text",
                        "quote": "πιστοποίηση ISO 9001:2015 σε ισχύ"}],
       "not_found": []}


@pytest.fixture()
def act(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text,
                      final_submission_date)
                   VALUES (%s, 'notice', 'Προμήθεια δικών', 'import', 'khmdhs',
                           %s, now() + interval '10 days')""", (ADAM, TEXT))
    yield ADAM
    cur.execute("DELETE FROM proc.act_ai_summary_history WHERE adam=%s", (ADAM,))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))


@pytest.fixture()
def ai_on(monkeypatch):
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    from app import ai_summary as _ai
    monkeypatch.setenv(_ai.key_var(), "test-key-not-used")


def _store():
    from app import ai_summary as ai
    with connect() as conn:
        cur = conn.cursor()
        act_row, sources = ai.load_inputs(cur, ADAM)
        ai.store(cur, ADAM, payload=ai.verify(RAW, sources, act_row),
                 hash_=ai.input_hash(sources), usage={}, by="test")


def _customer(client, name="clown", *, entitled=True):
    uid = make_user(name, "goodpassword1")
    if entitled:
        grant(uid)
    login(client, name, "goodpassword1")
    return uid


def _post(client, path, data=None):
    return client.post(f"/act/{ADAM}/checklist/{path}", data=data or {},
                       headers={"X-CSRF-Token": get_csrf(client)})


def _rows(uid):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, text, done_at FROM proc.act_checklist_own_item "
                    "WHERE user_id=%s ORDER BY id", (uid,))
        return cur.fetchall()


# --------------------------------------------------------------------------- #
def test_add_tick_untick_delete(client, act, ai_on):
    _store()
    uid = _customer(client)
    r = _post(client, "own", {"text": "  Αίτημα   εγγυητικής\nστην τράπεζα "})
    assert r.status_code == 200
    assert "Αίτημα εγγυητικής στην τράπεζα" in r.text     # whitespace collapsed
    assert "0 / 2" in r.text                               # 1 AI item + 1 own
    (row,) = _rows(uid)
    r = _post(client, f"own/{row['id']}", {"done": "1"})
    assert "1 / 2" in r.text and "is-done" in r.text
    first_done = _rows(uid)[0]["done_at"]
    _post(client, f"own/{row['id']}", {"done": "1"})        # tick again
    assert _rows(uid)[0]["done_at"] == first_done           # keeps the first time
    r = _post(client, f"own/{row['id']}")                   # untick
    assert "0 / 2" in r.text
    r = _post(client, f"own/{row['id']}/delete")
    assert r.status_code == 200 and _rows(uid) == [] and "0 / 1" in r.text


@pytest.mark.parametrize("text,msg", [
    ("   ", "Γράψτε τι θέλετε να προσθέσετε."),
    ("χ" * 201, "πολύ μεγάλο"),
])
def test_bad_text_is_refused_with_a_reason(client, act, ai_on, text, msg):
    _store()
    uid = _customer(client)
    r = _post(client, "own", {"text": text})
    assert r.status_code == 200 and msg in r.text
    assert _rows(uid) == []


def test_the_per_act_cap(client, act, ai_on, monkeypatch):
    from app import tender_checklist as tc
    monkeypatch.setattr(tc, "OWN_MAX_PER_ACT", 2)
    _store()
    uid = _customer(client)
    _post(client, "own", {"text": "ένα"})
    r = _post(client, "own", {"text": "δύο"})
    assert 'class="cl-add"' not in r.text                  # form hidden when full
    r = _post(client, "own", {"text": "τρία"})
    assert "όριο" in r.text
    assert [x["text"] for x in _rows(uid)] == ["ένα", "δύο"]


def test_another_customers_item_cannot_be_touched(client, act, ai_on):
    _store()
    mine = _customer(client, "clown_a")
    _post(client, "own", {"text": "δικό μου"})
    (row,) = _rows(mine)
    client.cookies.clear()
    _customer(client, "clown_b")
    assert "δικό μου" not in client.get(f"/act/{ADAM}/checklist").text
    assert _post(client, f"own/{row['id']}", {"done": "1"}).status_code == 404
    assert _post(client, f"own/{row['id']}/delete").status_code == 404
    assert _rows(mine)[0]["done_at"] is None and len(_rows(mine)) == 1


def test_an_item_id_is_bound_to_its_act(client, act, ai_on, db):
    _store()
    uid = _customer(client)
    _post(client, "own", {"text": "δικό μου"})
    (row,) = _rows(uid)
    r = client.post(f"/act/OTHER-ACT/checklist/own/{row['id']}", data={"done": "1"},
                    headers={"X-CSRF-Token": get_csrf(client)})
    assert r.status_code == 404 and _rows(uid)[0]["done_at"] is None


def test_only_on_an_act_with_a_checklist(client, act, ai_on):
    _customer(client)                                      # no summary stored
    assert _post(client, "own", {"text": "κάτι"}).status_code == 404


def test_lapsed_and_anonymous_cannot_write(client, act, ai_on):
    _store()
    assert _post(client, "own", {"text": "κάτι"}).status_code in (403, 303)
    uid = _customer(client, "clown_lapsed", entitled=False)
    assert _post(client, "own", {"text": "κάτι"}).status_code == 403
    assert _rows(uid) == []


def test_own_items_count_on_the_favourites_page(client, act, ai_on):
    _store()
    uid = _customer(client)
    with connect() as conn:
        conn.cursor().execute("INSERT INTO proc.user_favorite_act (user_id, adam) "
                              "VALUES (%s, %s)", (uid, ADAM))
    _post(client, "own", {"text": "ένα"})
    (row,) = _rows(uid)
    _post(client, f"own/{row['id']}", {"done": "1"})
    assert "1 / 2" in client.get("/account/favorites").text


def test_the_summary_never_sees_own_items():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app" / "ai_summary.py").read_text()
    assert "act_checklist_own_item" not in src
