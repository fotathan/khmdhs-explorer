"""The Tender Service probe (tsg_probe.py).

The probe's output decides whether and how a fourth source is built, so the
things worth testing are the ones that would make it lie: a key read as empty,
a request date in the wrong format (a silently wrong day), a rate-limit header
misread into sprinting past the budget, and an identifier check that misses
our ΑΔΑΜ/ΑΔΑ — which is what cross-source de-duplication will join on.
"""
from __future__ import annotations

import datetime as dt

import pytest

import tsg_probe as tp


# --------------------------------------------------------------------------- #
# the key
# --------------------------------------------------------------------------- #
def test_key_from_environment_is_stripped():
    assert tp.load_key({"TSG_API_KEY": "  abc123 \n"}) == "abc123"


def test_key_from_file_survives_a_space_after_the_equals(tmp_path):
    # The exact mistake that made every shell read the key as empty.
    f = tmp_path / "env"
    f.write_text("GEMI_API_KEY=x\nTSG_API_KEY= abc123\n", encoding="utf-8")
    assert tp.load_key({}, f) == "abc123"


def test_key_from_file_quoted_and_exported(tmp_path):
    f = tmp_path / "env"
    f.write_text('export TSG_API_KEY="abc123"\n', encoding="utf-8")
    assert tp.load_key({}, f) == "abc123"


def test_missing_key_is_none(tmp_path):
    f = tmp_path / "env"
    f.write_text("TSG_API_KEY=\n", encoding="utf-8")
    assert tp.load_key({}, f) is None
    assert tp.load_key({}, tmp_path / "absent") is None


# --------------------------------------------------------------------------- #
# the server's prose
# --------------------------------------------------------------------------- #
def test_rate_limit_remaining():
    assert tp.parse_rate_limit("299 requests remaining for the next 579 seconds") == (299, 579)


def test_rate_limit_consumed():
    assert tp.parse_rate_limit("All requests consumed, so it will take 17 seconds to reset") == (0, 17)


def test_rate_limit_absent():
    assert tp.parse_rate_limit(None) == (None, None)


def test_page_size_from_rejection():
    assert tp.parse_max_from_error("400 ... Requested tender amount is greater than limit: 10") == 10
    assert tp.parse_max_from_error("Invalid request content") is None


# --------------------------------------------------------------------------- #
# request dates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pattern,expected", [
    ("dd.MM.yy", "05.03.26"),
    ("dd.MM.yyyy", "05.03.2026"),
    ("yyyy-MM-dd", "2026-03-05"),
    ("dd/MM/yyyy", "05/03/2026"),
])
def test_java_pattern_formats_the_right_day(pattern, expected):
    assert dt.date(2026, 3, 5).strftime(tp.java_to_strftime(pattern)) == expected


def test_unknown_java_token_refuses():
    with pytest.raises(ValueError):
        tp.java_to_strftime("EEE, dd MMM yyyy")


def test_pick_day_skips_weekends():
    # Monday 2026-09-14 − 3 days = Friday 09-11.
    assert tp.pick_day(dt.date(2026, 9, 14)) == dt.date(2026, 9, 11)
    # Tuesday 09-15 − 3 = Saturday 09-12 → back to Friday 09-11.
    assert tp.pick_day(dt.date(2026, 9, 15)) == dt.date(2026, 9, 11)


# --------------------------------------------------------------------------- #
# response shapes
# --------------------------------------------------------------------------- #
def test_records_from_bare_array_and_wrapper():
    assert tp.extract_records([{"a": 1}]) == [{"a": 1}]
    body = {"totalCount": "123", "tenders": [{"a": 1}, {"a": 2}]}
    assert tp.extract_records(body) == [{"a": 1}, {"a": 2}]
    assert tp.find_total(tp.extract_meta(body)) == 123


def test_no_total_is_none_not_zero():
    assert tp.find_total({"format": "json"}) is None


# --------------------------------------------------------------------------- #
# identifiers and shapes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,kinds", [
    ("24PROC015123456", {"adam"}),
    ("ref 25SYMV017000001 (lot 2)", {"adam"}),
    ("9ΛΥΠ46ΜΤΛΡ-8ΘΡ", {"ada"}),
    ("9ΥΠΓΩ6Ζ-ΓΝ8-09-11084123", {"ada"}),       # short ΑΔΑ + TSG's timestamp suffix
    ("94ΩΕ46907Τ-ΚΜΗ-09-11143505", {"ada"}),
    ("https://ted.europa.eu/el/notice/-/detail/612345-2026", {"ted"}),
    ("ABCDEFGH-XYZ", set()),          # Latin-only: not an ΑΔΑ
    ("", set()),
    (None, set()),
])
def test_classify_ids(text, kinds):
    assert tp.classify_ids(text) == kinds


def test_value_shape_hides_values_keeps_format():
    assert tp.value_shape("13.08.27 16:00") == "99.99.99 99:99"
    assert tp.value_shape("41.109,00 EUR") == "99.999,99 AAA"


def test_summary_counts_coverage_and_labels():
    recs = [
        {"typeOfDocument": "Προκήρυξη", "externalId": "24PROC015123456", "title": "x"},
        {"typeOfDocument": "Προκήρυξη", "externalId": "", "title": "y"},
    ]
    s = tp.summarise(recs)
    cov = {f: pct for f, _, pct in s["coverage"]}
    assert cov["title"] == 100 and cov["externalId"] == 50
    assert s["vocab"]["typeOfDocument"] == [("Προκήρυξη", 2)]
    assert s["ids"]["externalId"]["kinds"] == {"adam": 1}
