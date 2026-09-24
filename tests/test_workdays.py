"""Greek working days (app/workdays.py) — docs/specs/working-day-deadlines.md §7.

Every expected date below was counted BY HAND on a calendar, not produced by
running the implementation: a test that re-derives its answer from the code
under test only proves the code agrees with itself.

The owner's decisions pinned here (2026-09-24): Μεγάλη Παρασκευή is a
WORKING day; 26 December is NOT.
"""
import datetime as dt

import pytest

D = dt.date


@pytest.fixture(autouse=True)
def _no_overrides():
    from app import workdays as wd
    wd.set_overrides([])
    yield
    wd.set_overrides([])


# --------------------------------------------------------------------------- #
# §7.1 Easter, §7.2 the movable feasts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("year,easter", [
    (2023, D(2023, 4, 16)), (2024, D(2024, 5, 5)), (2025, D(2025, 4, 20)),
    (2026, D(2026, 4, 12)), (2027, D(2027, 5, 2)), (2028, D(2028, 4, 16)),
    (2029, D(2029, 4, 8)), (2030, D(2030, 4, 28)),
])
def test_orthodox_easter(year, easter):
    from app import workdays as wd
    assert wd.orthodox_easter(year) == easter


@pytest.mark.parametrize("year,clean_monday,easter_monday,whit_monday", [
    (2026, D(2026, 2, 23), D(2026, 4, 13), D(2026, 6, 1)),
    (2027, D(2027, 3, 15), D(2027, 5, 3), D(2027, 6, 21)),
])
def test_movable_feasts(year, clean_monday, easter_monday, whit_monday):
    from app import workdays as wd
    h = wd.holidays(year)
    assert h[clean_monday] == "Καθαρά Δευτέρα"
    assert h[easter_monday] == "Δευτέρα του Πάσχα"
    assert h[whit_monday] == "Αγίου Πνεύματος"


def test_good_friday_is_a_working_day():
    """The owner's decision, 2026-09-24. 10 April 2026 is Μεγάλη Παρασκευή."""
    from app import workdays as wd
    assert wd.is_working_day(D(2026, 4, 10))
    assert D(2026, 4, 10) not in wd.holidays(2026)


def test_26_december_is_not_a_working_day():
    """The owner's decision, 2026-09-24. 26 December 2025 is a Friday."""
    from app import workdays as wd
    assert not wd.is_working_day(D(2025, 12, 26))
    assert wd.holiday_name(D(2025, 12, 26)) == "Σύναξη της Θεοτόκου"


@pytest.mark.parametrize("day", [
    D(2026, 1, 1), D(2026, 1, 6), D(2026, 3, 25), D(2026, 5, 1),
    D(2026, 10, 28), D(2026, 12, 25),
])
def test_fixed_holidays_on_weekdays(day):
    from app import workdays as wd
    assert day.weekday() < 5 and not wd.is_working_day(day)


def test_weekends():
    from app import workdays as wd
    assert not wd.is_working_day(D(2026, 9, 26))           # Saturday
    assert not wd.is_working_day(D(2026, 9, 27))           # Sunday
    assert wd.is_working_day(D(2026, 9, 28))               # Monday


# --------------------------------------------------------------------------- #
# §7.3 Easter week — the cluster case
# --------------------------------------------------------------------------- #
def test_easter_week_counted_by_hand():
    """Wed 8 Apr 2026 → Fri 17 Apr 2026: Thu 9, Fri 10 (Μεγάλη Παρασκευή,
    working), [Sat 11, Sun 12, Mon 13 Easter Monday], Tue 14, Wed 15, Thu 16,
    Fri 17 = 6 working days."""
    from app import workdays as wd
    assert wd.working_days_between(D(2026, 4, 8), D(2026, 4, 17)) == 6
    assert wd.add_working_days(D(2026, 4, 17), -6) == D(2026, 4, 8)


def test_ten_working_days_back_across_easter():
    """From Mon 20 Apr 2026 back ten: 17,16,15,14, [13 hol, 12, 11], 10, 9,
    8, 7, 6, [5, 4], 3 → Fri 3 Apr 2026."""
    from app import workdays as wd
    assert wd.add_working_days(D(2026, 4, 20), -10) == D(2026, 4, 3)


def test_christmas_counted_by_hand():
    """Wed 24 Dec 2025 → Mon 29 Dec: [25 hol, 26 hol, 27, 28] then 29 = 1."""
    from app import workdays as wd
    assert wd.working_days_between(D(2025, 12, 24), D(2025, 12, 29)) == 1
    assert wd.add_working_days(D(2025, 12, 24), 1) == D(2025, 12, 29)


# --------------------------------------------------------------------------- #
# §7.4 symmetry, §7.5 zero and boundaries
# --------------------------------------------------------------------------- #
def test_direction_symmetry():
    from app import workdays as wd
    start = D(2026, 3, 1)
    for i in range(0, 120, 7):
        d = start + dt.timedelta(days=i)
        for n in (1, 3, 10, 25):
            there = wd.add_working_days(d, -n)
            back = wd.add_working_days(there, n)
            assert back >= d and wd.is_working_day(back)
            assert wd.working_days_between(there, back) == n


def test_between_is_the_inverse_of_add_on_a_working_day():
    from app import workdays as wd
    a = D(2026, 4, 1)
    for n in range(1, 40):
        b = wd.add_working_days(a, n)
        assert wd.working_days_between(a, b) == n
        assert wd.working_days_between(b, a) == -n


def test_zero_returns_the_start_even_on_a_holiday():
    from app import workdays as wd
    assert wd.add_working_days(D(2026, 9, 27), 0) == D(2026, 9, 27)   # Sunday
    assert wd.add_working_days(D(2026, 3, 25), 0) == D(2026, 3, 25)   # 25 March
    assert wd.working_days_between(D(2026, 3, 25), D(2026, 3, 25)) == 0


def test_a_datetime_is_refused():
    """§5: the conversion to an Athens date cannot be skipped by accident."""
    from app import workdays as wd
    now = dt.datetime(2026, 4, 14, 23, 59, tzinfo=dt.timezone.utc)
    for call in (lambda: wd.is_working_day(now),
                 lambda: wd.add_working_days(now, 1),
                 lambda: wd.working_days_between(D(2026, 4, 1), now)):
        with pytest.raises(TypeError):
            call()


# --------------------------------------------------------------------------- #
# §7.6 overrides
# --------------------------------------------------------------------------- #
def test_overrides_move_a_holiday():
    """"1 May is observed on the 5th": remove the 1st, add the 5th."""
    from app import workdays as wd
    wd.set_overrides([(D(2026, 5, 1), False, ""),
                      (D(2026, 5, 5), True, "Πρωτομαγιά (μετάθεση)")])
    assert wd.is_working_day(D(2026, 5, 1))
    assert not wd.is_working_day(D(2026, 5, 5))
    assert wd.holiday_name(D(2026, 5, 5)) == "Πρωτομαγιά (μετάθεση)"
    assert not wd.is_working_day(D(2027, 5, 1))            # other years untouched
    wd.set_overrides([])
    assert not wd.is_working_day(D(2026, 5, 1))


def test_overrides_load_from_the_table(db):
    from app import tender_checklist as tc
    from app import workdays as wd
    cur = db.cursor()
    cur.execute("DELETE FROM proc.public_holiday")
    cur.execute("INSERT INTO proc.public_holiday (day, name, is_holiday) VALUES "
                "('2026-11-17', 'Πολυτεχνείο (δοκιμή)', true), "
                "('2026-10-28', '', false)")
    try:
        tc.refresh_holidays(cur, force=True)
        assert not wd.is_working_day(D(2026, 11, 17))
        assert wd.is_working_day(D(2026, 10, 28))
    finally:
        cur.execute("DELETE FROM proc.public_holiday")
        tc.refresh_holidays(cur, force=True)
    assert wd.is_working_day(D(2026, 11, 17))


# --------------------------------------------------------------------------- #
# In the checklist
# --------------------------------------------------------------------------- #
def test_the_checklist_carries_working_days_and_marks_a_holiday():
    from app import tender_checklist as tc
    act = {"full_text": "",
           "final_submission_date": dt.datetime(2026, 4, 17, 12, 0,
                                                tzinfo=tc.ATHENS)}
    payload = {"sections": [{"key": "timeline", "items": [
        {"label": "Αποσφράγιση", "value": "13/04/2026", "quote": "x",
         "source": "full_text", "confidence": "high"}]}]}
    cl = tc.build(payload, act, today=D(2026, 4, 8))
    by = {d["label"]: d for d in cl["deadlines"]}
    assert (by["Υποβολή προσφορών"]["days_left"],
            by["Υποβολή προσφορών"]["workdays_left"]) == (9, 6)
    assert by["Αποσφράγιση"]["holiday"] == "Δευτέρα του Πάσχα"
    assert by["Υποβολή προσφορών"]["holiday"] is None
