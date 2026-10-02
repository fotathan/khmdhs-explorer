"""tsg_coverage.py: the trial's coverage/identity report (pure part)."""
from __future__ import annotations

import datetime as dt

import tsg_coverage as cov

D = dt.date
START, END = D(2026, 9, 1), D(2026, 9, 30)
LOADED = {D(2026, 9, d) for d in range(1, 31)}


def test_a_day_counts_only_when_both_sides_hold_it_completely():
    days = cov.comparable_days(START, END, D(2026, 9, 17), {D(2026, 9, 15), D(2026, 9, 16), D(2026, 9, 17)})
    assert days == {D(2026, 9, 15), D(2026, 9, 16)}     # the last ingest day may be partial
    assert cov.comparable_days(START, END, D(2026, 8, 5), LOADED) == set()
    assert cov.comparable_days(START, END, None, LOADED) == set()


def _tsg(internal, key, kind, label, day, ds="eprocurement-gov-gr"):
    return (internal, key, kind, label, ds, day, "t")


def test_found_missing_extra_and_dates_are_counted_per_source_and_type():
    ours = [("26PROC1000001", "khmdhs", "notice", D(2026, 9, 2), "a"),
            ("26PROC1000002", "khmdhs", "notice", D(2026, 9, 2), "b"),
            ("26PROC1000003", "khmdhs", "notice", D(2026, 9, 20), "out of our window"),
            ("26SYMV1000001", "khmdhs", "contract", D(2026, 9, 2), "no overlap")]
    our_last = {("khmdhs", "notice"): D(2026, 9, 17), ("khmdhs", "contract"): D(2026, 8, 5)}
    tsg = [_tsg("1", "26PROC1000001", "adam", "Προκήρυξη", D(2026, 9, 2)),
           _tsg("2", "26PROC1000009", "adam", "Προκήρυξη", D(2026, 9, 3)),       # extra
           _tsg("3", "26PROC1000010", "adam", "Προκήρυξη", D(2026, 9, 25)),      # outside our window
           _tsg("4", "26SYMV1000002", "adam", "Σύμβαση", D(2026, 9, 3)),         # contract: no overlap
           _tsg("5", "TSG:5", "tsg", "Προκήρυξη", D(2026, 9, 3), ds="isupplies.gr")]
    r = cov.compare(ours, our_last, tsg, LOADED, START, END)
    n = r["coverage"][("khmdhs", "notice")]
    assert (n["n"], n["found"], n["same_date"]) == (2, 1, 1)
    assert n["missing"] == [("26PROC1000002", D(2026, 9, 2), "b")]
    c = r["coverage"][("khmdhs", "contract")]
    assert c["window"] == set() and c["n"] == 0             # reported as no overlap, not 100% missing
    assert r["extra"] == {("khmdhs", "notice"): {"n": 1, "samples": [("26PROC1000009", "2", D(2026, 9, 3), "t")]}}
    assert r["tsg_side"][("isupplies.gr", "Προκήρυξη")] == {"tsg": 1}
    assert r["tsg_total"] == 5


def test_found_ignores_the_publication_date_and_reports_it_separately():
    ours = [("26PROC1000001", "khmdhs", "notice", D(2026, 9, 2), "a")]
    tsg = [_tsg("1", "26PROC1000001", "adam", "Προκήρυξη", D(2026, 8, 30))]
    r = cov.compare(ours, {("khmdhs", "notice"): D(2026, 9, 17)}, tsg, LOADED, START, END)
    n = r["coverage"][("khmdhs", "notice")]
    assert (n["found"], n["same_date"]) == (1, 0)


def test_the_report_renders_no_overlap_in_words():
    r = cov.compare([], {("khmdhs", "payment"): D(2026, 8, 5)}, [], LOADED, START, END)
    text = cov.render(r, START, END, LOADED, "now")
    assert "| khmdhs | payment | no overlap | 0 | 0 | – | – |" in text
