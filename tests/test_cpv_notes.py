# -*- coding: utf-8 -*-
"""CPV notes (app/cpv_notes.py, cpv_notes_gen.py; docs/specs/public-detail-pages.md
slice 3).

HIERARCHY  a code's parent is the nearest real code above it, its children
           are only the codes directly below; supplementary codes are out.

REQUEST    what the model is given (names, ancestors, sub-codes — capped) and
           how it is asked: structured output, low effort, no forced tool and
           no fallbacks (Opus 5.5 / the Batch API reject them). The hash
           changes exactly when what was asked changes.

CHECKS     a reply is stored only in the right script, at a sane length, with
           no numbers, amounts or links; refusals and truncations are errors.

STORAGE    a hidden note stays hidden when a re-run writes the same words, and
           comes back only when the words change; the reader sees their own
           language and never a hidden note.

ISOLATION  no customer data, no AI act summaries.
"""
import pathlib
import re

import pytest

from app import cpv_notes as cn

CODES = {
    "50000000-5": {"el": "Υπηρεσίες επισκευής και συντήρησης", "en": "Repair and maintenance services"},
    "50200000-7": {"el": "Επισκευή οχημάτων", "en": "Repair of vehicles"},
    "50220000-3": {"el": "Επισκευή σιδηροδρομικού υλικού", "en": "Repair related to railways"},
    "50221000-0": {"el": "Επισκευή μηχανών έλξης", "en": "Repair of locomotives"},
    "50221100-1": {"el": "Επισκευή κιβωτίων ταχυτήτων", "en": "Repair of locomotive gearboxes"},
    "50222000-7": {"el": "Επισκευή τροχαίου υλικού", "en": "Repair of rolling stock"},
}

GOOD_EL = ("Η κατηγορία καλύπτει την επισκευή και τη συντήρηση σιδηροδρομικού υλικού, "
           "όπως μηχανών έλξης και τροχαίου υλικού, καθώς και συναφείς εργασίες "
           "αποκατάστασης για την ασφαλή λειτουργία των συρμών.")
GOOD_EN = ("This category covers the repair and maintenance of railway equipment, "
           "such as locomotives and rolling stock, together with related "
           "reconditioning work that keeps trains running safely.")


@pytest.fixture()
def tree():
    parent, children = cn.build_tree(CODES)
    return parent, children


# --------------------------------------------------------------------------- #
# Hierarchy
# --------------------------------------------------------------------------- #
def test_parents_skip_to_the_nearest_real_code(tree):
    parent, children = tree
    assert parent["50000000-5"] is None
    assert parent["50220000-3"] == "50200000-7"
    assert parent["50221100-1"] == "50221000-0"
    assert children["50220000-3"] == ["50221000-0", "50222000-7"]   # not the grandchild
    assert cn.ancestors("50221100-1", parent) == ["50000000-5", "50200000-7",
                                                 "50220000-3", "50221000-0"]


def test_levels():
    assert [cn.level(c) for c in ("50000000-5", "50200000-7", "50220000-3",
                                  "50221000-0", "50221100-1")] == [1, 2, 3, 4, 5]


def test_supplementary_codes_are_not_cpv_subjects():
    assert cn.CODE_RE.match("50220000-3")
    assert not cn.CODE_RE.match("DA03-0")


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #
def test_the_model_is_given_names_ancestors_and_subcodes(tree):
    text = cn.user_text("50220000-3", CODES, *tree)
    assert "Επισκευή σιδηροδρομικού υλικού" in text and "Repair related to railways" in text
    assert "Επισκευή οχημάτων" in text                       # ancestor
    assert "Επισκευή τροχαίου υλικού" in text                # child
    assert "κιβωτίων" not in text                            # grandchild is not given
    leaf = cn.user_text("50221100-1", CODES, *tree)
    assert "none (this is a specific code)" in leaf


def test_subcodes_are_capped(monkeypatch, tree):
    monkeypatch.setattr(cn, "MAX_CHILDREN", 1)
    assert "(+1 more)" in cn.user_text("50220000-3", CODES, *tree)


def test_request_shape(tree):
    p = cn.request_params("50220000-3", CODES, *tree)
    assert p["model"] == cn.MODEL == "claude-opus-5-5"
    assert p["output_config"]["effort"] == "low"
    fmt = p["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["required"] == ["el", "en"]
    assert fmt["schema"]["additionalProperties"] is False
    for banned in ("tool_choice", "fallbacks", "thinking", "temperature"):
        assert banned not in p, banned


def test_the_hash_follows_what_was_asked(tree, monkeypatch):
    a = cn.input_hash(cn.request_params("50220000-3", CODES, *tree))
    assert a == cn.input_hash(cn.request_params("50220000-3", CODES, *tree))
    renamed = dict(CODES, **{"50222000-7": {"el": "Άλλο όνομα", "en": "Other"}})
    b = cn.input_hash(cn.request_params("50220000-3", renamed, *cn.build_tree(renamed)))
    assert a != b, "a renamed sub-code must make the note stale"
    monkeypatch.setattr(cn, "PROMPT_VERSION", cn.PROMPT_VERSION + 1)
    assert a != cn.input_hash(cn.request_params("50220000-3", CODES, *tree))


def test_custom_ids_are_valid_and_reversible():
    cid = cn.custom_id("50220000-3")
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", cid)
    assert cn.code_of(cid) == "50220000-3"


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def test_a_good_reply_passes():
    assert cn.validate("50220000-3", GOOD_EL, GOOD_EN) == []


@pytest.mark.parametrize("el, en, problem", [
    ("", GOOD_EN, "el: empty"),
    (GOOD_EL, "Σύντομο", "en: length"),
    (GOOD_EL * 5, GOOD_EN, "el: length"),
    (GOOD_EL + " Κόστος 1500.", GOOD_EN, "el: contains a number"),
    (GOOD_EL, GOOD_EN + " Prices from €5.", "en: amount"),
    (GOOD_EL, GOOD_EN + " See www.example.com.", "en: amount, percentage or link"),
    (GOOD_EN, GOOD_EN, "el: not Greek"),
    (GOOD_EL, GOOD_EN + " Λέξη.", "en: Greek letters in English"),
    (GOOD_EL, GOOD_EN + "\n\nA second paragraph here.", "en: more than one paragraph"),
])
def test_bad_replies_are_refused(el, en, problem):
    assert any(p.startswith(problem) for p in cn.validate("50220000-3", el, en))


def test_the_code_number_is_never_in_the_text():
    probs = cn.validate("50220000-3", GOOD_EL, GOOD_EN.replace("This", "Code 50220000 -"))
    assert any("number" in p for p in probs)


def test_parse_message():
    ok = {"stop_reason": "end_turn",
          "content": [{"type": "thinking", "thinking": ""},
                      {"type": "text", "text": '{"el": " α ", "en": " b "}'}]}
    assert cn.parse_message(ok) == ({"el": "α", "en": "b"}, None)
    assert cn.parse_message({"stop_reason": "refusal"}) == (None, "refusal")
    assert cn.parse_message({"stop_reason": "max_tokens"}) == (None, "max_tokens")
    assert cn.parse_message({"stop_reason": "end_turn",
                             "content": [{"type": "text", "text": "nope"}]}) == (None, "not JSON")
    assert cn.parse_message({"stop_reason": "end_turn", "content": []}) == (None, "no text block")


def test_batch_costs_half():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert cn.cost_micro_usd(usage, batch=False) == 24_000_000
    assert cn.cost_micro_usd(usage, batch=True) == 12_000_000


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
CODE = "50220000-3"


@pytest.fixture()
def note_db(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.cpv_code WHERE cpv_code = %s", (CODE,))
    cur.execute("INSERT INTO proc.cpv_code (cpv_code, description, description_en) "
                "VALUES (%s, 'Επισκευή', 'Repair')", (CODE,))
    yield cur
    cur.execute("DELETE FROM proc.cpv_code WHERE cpv_code = %s", (CODE,))   # cascades


def _store(cur, el=GOOD_EL, en=GOOD_EN, h="h1"):
    cn.store(cur, CODE, {"el": el, "en": en}, ihash=h,
             usage={"input_tokens": 1000, "output_tokens": 400}, batch_id="b1")


def test_the_reader_gets_their_language(note_db):
    _store(note_db)
    assert cn.note_for(note_db, CODE) == GOOD_EL
    assert cn.note_for(note_db, CODE, "en") == GOOD_EN
    note_db.execute("SELECT cost_micro_usd FROM proc.cpv_note WHERE cpv_code = %s", (CODE,))
    assert note_db.fetchone()["cost_micro_usd"] == (1000 * 4 + 400 * 20) // 2


def test_a_hidden_note_stays_hidden_unless_the_words_change(note_db):
    _store(note_db)
    note_db.execute("UPDATE proc.cpv_note SET hidden_at = now(), hidden_reason = 'x' "
                    "WHERE cpv_code = %s", (CODE,))
    assert cn.note_for(note_db, CODE) is None
    _store(note_db, h="h2")                                   # same words, new hash
    assert cn.note_for(note_db, CODE) is None
    _store(note_db, el=GOOD_EL.replace("ασφαλή", "αξιόπιστη"), h="h3")
    assert cn.note_for(note_db, CODE) is not None
    assert cn.current_hashes(note_db)[CODE] == "h3"


# --------------------------------------------------------------------------- #
# Isolation + migration hygiene
# --------------------------------------------------------------------------- #
def test_notes_read_no_customer_data():
    for path in ("app/cpv_notes.py", "cpv_notes_gen.py"):
        src = pathlib.Path(path).read_text(encoding="utf-8")
        code = re.sub(r'"""[\s\S]*?"""', "", src)
        for forbidden in ("act_ai_summary", "customer_profile", "company_profile",
                          "app_user", "procurement_act"):
            assert forbidden not in code, f"{path}: {forbidden}"


def test_the_migration_has_no_do_blocks():
    mig = pathlib.Path("migrations/20261009150000_cpv_note.sql")
    sql = "\n".join(line for line in mig.read_text(encoding="utf-8").splitlines()
                    if not line.lstrip().startswith("--"))
    assert "DO $$" not in sql
