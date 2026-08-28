# -*- coding: utf-8 -*-
"""The Greek normaliser, offsets, paragraph split and highlighting.

No database: this is the layer everything else is built on, so it is worth
being able to run it on its own. If these fail, the chip counts, the snippets
and the <mark>s on the page will disagree with each other.
"""
from app import textmatch as tm


# --------------------------------------------------------------------------- #
# fold()
# --------------------------------------------------------------------------- #
def test_fold_is_accent_case_and_final_sigma_insensitive():
    for variant in ("ΑΝΆΘΕΣΗ", "ανάθεση", "αναθεση", "Ανάθεση", "ΑΝΑΘΕΣΗ"):
        assert tm.fold(variant) == "αναθεση", variant
    # final sigma folds to medial sigma, in both the middle and at the end
    assert tm.fold("Καθαρισμός") == tm.fold("ΚΑΘΑΡΙΣΜΟΣ") == "καθαρισμοσ"


def test_fold_handles_dialytika_and_polytonic():
    assert tm.fold("ΐ") == tm.fold("ϊ") == "ι"
    assert tm.fold("ΰ") == tm.fold("ϋ") == "υ"
    assert tm.fold("ἀνάθεσις") == "αναθεσισ"        # polytonic breathing marks


def test_fold_preserves_length_exactly():
    """The constraint the whole feature rests on: offsets found in the folded
    text must be valid in the original, so folding may never resize a string."""
    samples = [
        "ΑΝΆΘΕΣΗ ΚΑΘΑΡΙΣΜΟΎ", "Προμήθεια ειδών καθαριότητας ΑΔΑΜ 25SYMV016143474",
        "ΐΰϊϋςΆΈΉΊΌΎΏ", "ἀνάθεσις", "Straße İstanbul ﬁle", "33111000-1 · CPV",
        "", "   ", "Ω" * 500,
    ]
    for s in samples:
        assert len(tm.fold(s)) == len(s), repr(s)


def test_fold_leaves_digits_and_latin_usable():
    assert tm.fold("CPV 33111000-1") == "cpv 33111000-1"


# --------------------------------------------------------------------------- #
# tokenize()
# --------------------------------------------------------------------------- #
def test_tokenize_offsets_point_into_the_original_text():
    text = "Προμήθεια  ΚΑΘΑΡΙΣΤΙΚΩΝ, ΑΔΑΜ 25SYMV1."
    for tok in tm.tokenize(text):
        assert text[tok.start:tok.end] == tok.text
        assert tok.folded == tm.fold(tok.text)


def test_tokenize_splits_on_punctuation_not_accents():
    words = [t.text for t in tm.tokenize("καθαρισμός, κτιρίων· Δ.Π.Θ.")]
    assert words == ["καθαρισμός", "κτιρίων", "Δ", "Π", "Θ"]


# --------------------------------------------------------------------------- #
# split_full_text()
# --------------------------------------------------------------------------- #
def test_split_numbers_paragraphs_and_keeps_offsets():
    text = "Πρώτη παράγραφος.\n\nΔεύτερη παράγραφος.\n\nΤρίτη."
    paras = tm.split_full_text(text)
    assert [p.index for p in paras] == [1, 2, 3]
    for p in paras:
        assert text[p.start:p.end] == p.text
        assert p.text == p.text.strip()          # no whitespace inside the span


def test_split_carries_a_detected_heading_forward():
    text = "Εισαγωγή.\n\nΤΜΗΜΑ 3\n\nΤο αντικείμενο.\n\nΆλλο."
    paras = tm.split_full_text(text)
    assert paras[0].heading is None
    assert [p.heading for p in paras[1:]] == ["ΤΜΗΜΑ 3"] * 3


def test_split_falls_back_to_single_newlines_then_chunks():
    assert len(tm.split_full_text("α\nβ\nγ")) == 3
    # one unbroken OCR blob still gets usable paragraph numbers
    blob = ("λέξη " * 2000).strip()
    paras = tm.split_full_text(blob)
    assert len(paras) > 1
    assert all(p.end - p.start <= tm._CHUNK for p in paras)


def test_split_of_empty_text_is_empty():
    assert tm.split_full_text(None) == []
    assert tm.split_full_text("   \n\n ") == []


# --------------------------------------------------------------------------- #
# mark() / snippet()
# --------------------------------------------------------------------------- #
def test_mark_escapes_before_inserting_markup():
    out = tm.mark("<script>καθαρισμός</script>", [(8, 18, "occ-x-1")])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert '<mark class="hl" id="occ-x-1">καθαρισμός</mark>' in out


def test_mark_without_spans_is_just_escaped_text():
    assert tm.mark("a & b", []) == "a &amp; b"


def test_mark_ignores_overlapping_spans():
    out = tm.mark("καθαρισμός", [(0, 5, "a"), (2, 7, "b")])
    assert out.count("<mark") == 1


def test_snippet_marks_the_term_and_trims_at_word_boundaries():
    text = "άλφα " * 30 + "καθαρισμός " + "βήτα " * 30
    start = text.index("καθαρισμός")
    out = tm.snippet(text, start, start + len("καθαρισμός"))
    assert '<mark class="hl">καθαρισμός</mark>' in out
    assert out.startswith("… ") and out.endswith(" …")
    # every word at the edges is whole — the trim moved out to whitespace
    import re
    words = re.sub(r"<[^>]+>", "", out).replace("…", "").split()
    assert set(words) <= {"άλφα", "βήτα", "καθαρισμός"}


def test_snippet_at_the_very_start_has_no_leading_ellipsis():
    text = "καθαρισμός κτιρίων και λοιπών χώρων"
    out = tm.snippet(text, 0, 10)
    assert not out.startswith("…")


# --------------------------------------------------------------------------- #
# term_slug()
# --------------------------------------------------------------------------- #
def test_term_slug_is_ascii_stable_and_fold_insensitive():
    assert tm.term_slug("Καθαρισμός") == tm.term_slug("ΚΑΘΑΡΙΣΜΟΣ")
    slug = tm.term_slug("Καθαρισμός")
    assert slug.isascii() and slug.isalnum()
    assert tm.term_slug("κτίρια") != slug
