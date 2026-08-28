# -*- coding: utf-8 -*-
"""Match explanation chips + the occurrence navigator, end to end.

The load-bearing assertion is `test_chip_count_equals_rendered_marks`: a chip
that claims 7 hits and a page that shows 6 <mark>s is a lie about the record,
and the spec says such a panel must not ship.
"""
import re

import pytest

from tests.helpers import login, make_user

ADAM = "MATCH-TEST-1"

# Greek that exercises the normaliser: the same stem in several surface forms,
# with accents, capitals and a final sigma, plus a heading to label against.
FULL_TEXT = """ΔΙΑΚΗΡΥΞΗ καθαρισμού κτιρίων.

ΤΜΗΜΑ 2

Ο ΚΑΘΑΡΙΣΜΟΣ των χώρων γίνεται καθημερινά.

Η ανάθεση του καθαρισμού αφορά τα κτίρια της υπηρεσίας.
"""


@pytest.fixture()
def act(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text)
                   VALUES (%s, 'notice', %s, 'import', 'khmdhs', %s)""",
                (ADAM, "Παροχή υπηρεσιών καθαρισμού κτιρίων", FULL_TEXT))
    yield ADAM
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (ADAM,))


@pytest.fixture()
def admin(client):
    make_user("matchadmin", "goodpassword1", role="admin")
    login(client, "matchadmin", "goodpassword1")
    return client


def _row(db, adam):
    cur = db.cursor()
    cur.execute("SELECT adam, title, full_text FROM proc.procurement_act WHERE adam=%s",
                (adam,))
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# Feature B — the chips
# --------------------------------------------------------------------------- #
def test_chip_count_equals_rendered_marks(db, act):
    """§3.4: the number on a chip IS the number of highlights on the page."""
    from app import search_match as sm
    m = sm.detail_match(db.cursor(), _row(db, act), "καθαρισμός")
    assert m is not None and len(m.chips) == 1
    rendered = sum(p["html"].count("<mark") for p in m.paragraphs)
    rendered += (m.title_html or "").count("<mark")
    assert m.chips[0].count == rendered


def test_chips_match_across_accents_case_and_final_sigma(db, act):
    from app import search_match as sm
    cur = db.cursor()
    counts = {q: sm.detail_match(cur, _row(db, act), q).chips[0].count
              for q in ("καθαρισμός", "ΚΑΘΑΡΙΣΜΌΣ", "καθαρισμος", "Καθαρισμού")}
    assert len(set(counts.values())) == 1, counts


def test_panel_is_absent_without_a_query(db, act):
    from app import search_match as sm
    cur = db.cursor()
    assert sm.detail_match(cur, _row(db, act), "") is None
    assert sm.detail_match(cur, _row(db, act), None) is None


def test_a_stop_word_gets_no_chip(db, act):
    """The index holds nothing for it, so it explains nothing."""
    from app import search_match as sm
    assert sm.detail_match(db.cursor(), _row(db, act), "και") is None


def test_negative_terms_get_no_chip(db, act):
    from app import search_match as sm
    m = sm.detail_match(db.cursor(), _row(db, act), "καθαρισμός -νοσοκομεία")
    assert [c.term for c in m.chips] == ["καθαρισμός"]


def test_list_chips_are_one_query_for_the_whole_page(db, act):
    """§3.3: chips for a result page must never be a per-row follow-up."""
    from app import search_match as sm

    class CountingCursor:
        """Cursor proxy that records every statement it is asked to run."""

        def __init__(self, inner):
            self._inner, self.seen = inner, []

        def execute(self, sql, params=None, *a, **kw):
            self.seen.append(sql)
            return self._inner.execute(sql, params, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    cur = CountingCursor(db.cursor())
    chips = sm.list_chips(cur, [act], "καθαρισμός κτιρίων")
    # one to resolve the query's lexemes, one to read the stored tsvectors —
    # and crucially not one per row, however many rows the page holds
    assert len(cur.seen) == 2, cur.seen
    assert {c.term for c in chips[act]} == {"καθαρισμός", "κτιρίων"}


def test_cpv_chip_carries_code_and_label(db, act):
    from app import search_match as sm
    cur = db.cursor()
    cur.execute("""INSERT INTO proc.cpv_code (cpv_code, description)
                   VALUES ('90911200-8', 'Υπηρεσίες καθαρισμού κτιρίων')
                   ON CONFLICT (cpv_code) DO NOTHING""")
    m = sm.detail_match(cur, _row(db, act), None, ["90911200-8"])
    chip = m.chips[0]
    assert chip.kind == "cpv"
    assert chip.code == "90911200-8"
    assert chip.label == "Υπηρεσίες καθαρισμού κτιρίων"
    assert chip.count == 1          # matched the classification, not the prose


# --------------------------------------------------------------------------- #
# Feature C — the occurrence navigator
# --------------------------------------------------------------------------- #
def test_every_listed_occurrence_has_an_anchor_on_the_page(db, act):
    """§4's whole promise: clicking an entry lands on something visible."""
    from app import search_match as sm
    cur = db.cursor()
    row = _row(db, act)
    occurrences, total = sm.occurrences_for(cur, row, "καθαρισμός")
    rendered = sm.detail_match(cur, row, "καθαρισμός")
    ids = set()
    for para in rendered.paragraphs:
        ids |= set(re.findall(r'id="(occ-[^"]+)"', para["html"]))
    ids |= set(re.findall(r'id="(occ-[^"]+)"', rendered.title_html or ""))
    assert occurrences
    assert all(o.anchor in ids for o in occurrences)
    assert total == rendered.chips[0].count


def test_occurrence_labels_use_the_shared_paragraph_split(db, act):
    """§4.2: labels must be numbered from the split that renders the text, or
    'παρ. 12' points at a paragraph that is not there."""
    from app import search_match as sm
    cur = db.cursor()
    row = _row(db, act)
    occurrences, _ = sm.occurrences_for(cur, row, "καθαρισμός")
    rendered = sm.detail_match(cur, row, "καθαρισμός")
    numbers = {p["index"] for p in rendered.paragraphs}
    body = [o for o in occurrences if o.section == "full_text"]
    assert body
    assert all(o.para in numbers for o in body)
    # the heading detected upstream is carried onto the occurrences under it
    assert any(o.heading == "ΤΜΗΜΑ 2" for o in body)


def test_occurrences_are_capped_but_the_total_is_honest(db, act):
    from app import search_match as sm
    cur = db.cursor()
    cur.execute("UPDATE proc.procurement_act SET full_text=%s WHERE adam=%s",
                ("καθαρισμός κτιρίων. " * 120, act))
    occurrences, total = sm.occurrences_for(cur, _row(db, act), "καθαρισμός")
    assert len(occurrences) == sm.OCC_CAP
    # 120 in the body plus the one in the title — the cap limits what is
    # LISTED, never what is counted
    assert total == 121
    assert sum(1 for o in occurrences if o.section == "title") == 1


def test_title_hits_are_labelled_as_the_title(db, act):
    from app import search_match as sm
    occurrences, _ = sm.occurrences_for(db.cursor(), _row(db, act), "υπηρεσιών")
    assert [o.section for o in occurrences] == ["title"]


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def test_detail_page_shows_the_panel_only_with_a_query(admin, act):
    plain = admin.get(f"/act/{act}")
    assert plain.status_code == 200
    assert "Γιατί ταιριάζει" not in plain.text

    matched = admin.get(f"/act/{act}?q=καθαρισμός")
    assert matched.status_code == 200
    assert "Γιατί ταιριάζει" in matched.text
    assert 'data-occ-term="καθαρισμός"' in matched.text
    assert '<mark class="hl"' in matched.text


def test_occurrences_endpoint_returns_clickable_entries(admin, act):
    r = admin.get(f"/act/{act}/occurrences", params={"term": "καθαρισμός"})
    assert r.status_code == 200
    assert "data-occ-anchor=\"occ-" in r.text
    assert "Πλήρες κείμενο" in r.text


def test_occurrences_endpoint_is_behind_the_paywall(client, act):
    """The full text is gated; so is an index into it."""
    assert client.get(f"/act/{act}/occurrences",
                      params={"term": "καθαρισμός"}).status_code == 404


def test_results_list_carries_the_query_onto_act_links(admin, act):
    r = admin.get("/", params={"q": "καθαρισμός"})
    assert r.status_code == 200
    assert "Ταιριάζει:" in r.text
    assert "?q=" in r.text                    # the detail link keeps the query


def test_act_page_without_a_query_renders_the_plain_full_text(admin, act):
    """The verbatim official text stays reachable — the searched view is an
    addition, never a replacement."""
    r = admin.get(f"/act/{act}")
    # the element, not the stylesheet rule of the same name
    assert '<pre class="full-text-pre">' in r.text
    assert '<div class="full-text-marked">' not in r.text
