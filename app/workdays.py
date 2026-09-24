"""
workdays.py — Greek public holidays and working-day arithmetic.

Spec: docs/specs/working-day-deadlines.md, slices 1–2.

Arithmetic only. No legal periods (those are content, spec §4), no app
imports, no database. The one piece of state is the OVERRIDE map, which a
caller loads from proc.public_holiday and hands in with set_overrides(); an
empty map means the computed set stands, so everything works before anyone
has touched the table.

The holiday set
---------------
Fixed: 1 Jan, 6 Jan, 25 Mar, 1 May, 15 Aug, 28 Oct, 25 Dec, 26 Dec.
Movable, from Orthodox Easter: Καθαρά Δευτέρα (−48), Δευτέρα του Πάσχα (+1),
Αγίου Πνεύματος (+50).

**Μεγάλη Παρασκευή is a WORKING day and 26 December is NOT** — both decided
by the owner on 2026-09-24 for the purpose of counting procurement deadlines
(spec §3b). Changing either is a change to this file and must be as
deliberate as that decision was; a one-year exception belongs in the
override table instead.

Dates, never datetimes
----------------------
Every function takes dt.date and REFUSES dt.datetime (TypeError). A deadline
is a timestamptz; counting from its UTC date is a day out exactly around
midnight, and always in the direction that flatters the deadline (spec §5).
Convert to Europe/Athens and take .date() first — the type check makes that
impossible to skip by accident.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

_FIXED = (
    (1, 1, "Πρωτοχρονιά"),
    (1, 6, "Θεοφάνεια"),
    (3, 25, "Εικοστή Πέμπτη Μαρτίου"),
    (5, 1, "Εργατική Πρωτομαγιά"),
    (8, 15, "Κοίμηση της Θεοτόκου"),
    (10, 28, "Επέτειος του «Όχι»"),
    (12, 25, "Χριστούγεννα"),
    (12, 26, "Σύναξη της Θεοτόκου"),        # a non-working day — decided 2026-09-24
)

# Offsets from Orthodox Easter Sunday. Μεγάλη Παρασκευή (−2) is deliberately
# ABSENT: a working day for deadline counting (decided 2026-09-24).
_MOVABLE = (
    (-48, "Καθαρά Δευτέρα"),
    (1, "Δευτέρα του Πάσχα"),
    (50, "Αγίου Πνεύματος"),
)

# {date: (is_holiday, name)} from proc.public_holiday — see set_overrides().
_OVERRIDES: dict[dt.date, tuple[bool, str]] = {}


def _date(d) -> dt.date:
    # datetime is a subclass of date, so it has to be refused explicitly.
    if isinstance(d, dt.datetime) or not isinstance(d, dt.date):
        raise TypeError("workdays takes a date (Athens local), not "
                        f"{type(d).__name__} — convert and take .date() first")
    return d


def orthodox_easter(year: int) -> dt.date:
    """Meeus' Julian algorithm, +13 days to the Gregorian calendar. Valid
    1900–2099."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = ((d + e + 114) % 31) + 1
    return dt.date(year, month, day) + dt.timedelta(days=13)


@lru_cache(maxsize=64)
def _computed(year: int) -> dict[dt.date, str]:
    out = {dt.date(year, m, d): name for m, d, name in _FIXED}
    easter = orthodox_easter(year)
    for offset, name in _MOVABLE:
        out[easter + dt.timedelta(days=offset)] = name
    return out


def holidays(year: int) -> dict[dt.date, str]:
    """{date: name} for the year: the computed set with the overrides applied.
    An override with is_holiday=False REMOVES a computed day ("1 May is
    observed on the 5th this year" = remove the 1st, add the 5th)."""
    out = dict(_computed(year))
    for day, (is_holiday, name) in _OVERRIDES.items():
        if day.year != year:
            continue
        if is_holiday:
            out[day] = name
        else:
            out.pop(day, None)
    return out


def set_overrides(rows) -> None:
    """Replace the override map. `rows` = iterables or dicts of
    (day, is_holiday, name). The caller owns loading and refreshing it."""
    new = {}
    for r in rows or ():
        if isinstance(r, dict):
            day, is_holiday, name = r["day"], r["is_holiday"], r.get("name") or ""
        else:
            day, is_holiday, name = r
        new[_date(day)] = (bool(is_holiday), name or "")
    _OVERRIDES.clear()
    _OVERRIDES.update(new)


def holiday_name(d: dt.date) -> str | None:
    d = _date(d)
    return holidays(d.year).get(d)


def is_working_day(d: dt.date) -> bool:
    """Not Saturday or Sunday, and not a holiday."""
    d = _date(d)
    return d.weekday() < 5 and d not in holidays(d.year)


def add_working_days(start: dt.date, n: int) -> dt.date:
    """The date `n` working days after `start` (before it, for negative n).

    `n == 0` returns `start` unchanged, even on a holiday. Otherwise counting
    starts from the NEXT qualifying day in the direction of travel. Both
    conventions are pinned by tests (spec §3d) — a later refactor flipping
    either would move every deadline by a day.
    """
    d = _date(start)
    step = 1 if n > 0 else -1
    left = abs(int(n))
    while left:
        d += dt.timedelta(days=step)
        if is_working_day(d):
            left -= 1
    return d


def working_days_between(a: dt.date, b: dt.date) -> int:
    """Working days after `a` up to and including `b` (negative when b < a).

    "How many working days until the deadline": a = today, b = the deadline
    day. Today itself never counts — the same convention as
    add_working_days, so add_working_days(a, working_days_between(a, b)) == b
    whenever b is a working day.
    """
    a, b = _date(a), _date(b)
    if a == b:
        return 0
    sign = 1 if b > a else -1
    lo, hi = (a, b) if b > a else (b, a)
    n, d = 0, lo
    while d < hi:
        d += dt.timedelta(days=1)
        if is_working_day(d):
            n += 1
    # Counting backwards covers (b, a]; the working days after b up to and
    # including a — the mirror of the forward case.
    return sign * n
