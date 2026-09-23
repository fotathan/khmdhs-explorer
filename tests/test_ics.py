"""Pure checks for RFC 5545 output (no database).

Written in Greek on purpose. The two bugs this module exists to avoid —
folding by characters instead of octets, and cutting a fold inside a UTF-8
sequence — are both invisible in ASCII, so an ASCII test suite would pass over
a broken implementation. See app/ics.py and docs/specs/calendar-feed.md §4.

Fixed UTC offsets rather than zoneinfo: these assertions are about arithmetic,
not about the tz database, and a test that needs tzdata installed fails for a
reason that has nothing to do with what it is checking.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app import ics

STAMP = dt.datetime(2026, 9, 23, 9, 0, tzinfo=dt.timezone.utc)
EEST = dt.timezone(dt.timedelta(hours=3))    # Athens, late March to late October
EET = dt.timezone(dt.timedelta(hours=2))     # Athens, the rest of the year

GREEK_TITLE = ("Προμήθεια ιατροτεχνολογικού εξοπλισμού για τις ανάγκες του "
               "Γενικού Νοσοκομείου Θεσσαλονίκης, σε τρία τμήματα")


def _unfold(body: str) -> str:
    """The inverse of folding: remove each CRLF followed by exactly one space."""
    return body.replace("\r\n ", "")


def _logical_lines(body: str) -> list[str]:
    assert body.endswith("\r\n")
    return _unfold(body)[:-2].split("\r\n")


def _event(**kw):
    base = dict(uid="26PROC012345678@khmdhs",
                start=dt.datetime(2026, 4, 14, 23, 59, tzinfo=EEST),
                summary=GREEK_TITLE)
    base.update(kw)
    return ics.Event(**base)


# --------------------------------------------------------------------------- #
# Folding (spec §9.1)
# --------------------------------------------------------------------------- #
def test_short_lines_are_not_folded():
    assert ics.fold("SUMMARY:σύντομο") == "SUMMARY:σύντομο"


@pytest.mark.parametrize("length", range(1, 260))
def test_folding_round_trips_for_every_greek_length(length):
    """The property that matters: folding is reversible, at every length.

    A Greek character is two octets, so this sweeps fold points that land both
    between characters and — for a byte-naive implementation — inside one.
    """
    line = "SUMMARY:" + ("α" * length)
    assert _unfold(ics.fold(line)) == line


def test_every_physical_line_fits_75_octets():
    folded = ics.fold("SUMMARY:" + GREEK_TITLE * 3)
    for physical in folded.split("\r\n"):
        assert len(physical.encode("utf-8")) <= 75, physical


def test_folding_never_splits_a_multibyte_character():
    """A naive byte cut raises UnicodeDecodeError inside fold(); a naive
    character cut produces over-long lines. This asserts against both."""
    for text in ("α" * 200,                      # 2-octet
                 "→" * 200,                      # 3-octet
                 "𝄞" * 200,                      # 4-octet, the worst back-off
                 GREEK_TITLE * 2):
        folded = ics.fold("DESCRIPTION:" + text)
        assert _unfold(folded) == "DESCRIPTION:" + text
        for physical in folded.split("\r\n"):
            assert len(physical.encode("utf-8")) <= 75


def test_continuation_lines_begin_with_one_space():
    folded = ics.fold("SUMMARY:" + GREEK_TITLE)
    physical = folded.split("\r\n")
    assert len(physical) > 1
    for line in physical[1:]:
        assert line.startswith(" ")
        assert not line.startswith("  ")


# --------------------------------------------------------------------------- #
# Escaping (spec §9.2)
# --------------------------------------------------------------------------- #
def test_escapes_comma_semicolon_backslash_and_newline():
    assert ics.escape_text("α,β") == "α\\,β"
    assert ics.escape_text("α;β") == "α\\;β"
    assert ics.escape_text("α\\β") == "α\\\\β"
    assert ics.escape_text("α\nβ") == "α\\nβ"
    assert ics.escape_text("α\r\nβ") == "α\\nβ"


def test_backslash_is_escaped_before_the_escapes_we_add():
    """If `;` were escaped first, its new backslash would then be doubled."""
    assert ics.escape_text(";") == "\\;"
    assert ics.escape_text("\\;") == "\\\\\\;"


def test_colon_is_not_escaped_in_a_text_value():
    assert ics.escape_text("Τμήμα 1: εξοπλισμός") == "Τμήμα 1: εξοπλισμός"


def test_control_characters_are_dropped():
    assert ics.escape_text("α\x00β\x07γ\x7f") == "αβγ"


def test_none_becomes_empty():
    assert ics.escape_text(None) == ""


def test_a_real_title_survives_a_round_trip_through_the_body():
    title = "Προμήθεια ειδών, 3 τμήματα; παράρτημα A\\B"
    body = ics.render([_event(summary=title)], name="δοκιμή", dtstamp=STAMP)
    line = next(ln for ln in _logical_lines(body) if ln.startswith("SUMMARY:"))
    assert line == "SUMMARY:Προμήθεια ειδών\\, 3 τμήματα\\; παράρτημα A\\\\B"


# --------------------------------------------------------------------------- #
# Calendar shape (spec §9.3)
# --------------------------------------------------------------------------- #
def test_crlf_everywhere_including_the_last_line():
    body = ics.render([_event()], name="δοκιμή", dtstamp=STAMP)
    assert body.endswith("\r\n")
    assert "\n" not in body.replace("\r\n", "")


def test_an_empty_calendar_is_still_valid():
    """What a lapsed customer's feed returns (spec §5). It must parse."""
    body = ics.render([], name="ΚΗΜΔΗΣ — Προθεσμίες", dtstamp=STAMP)
    lines = _logical_lines(body)
    assert lines[0] == "BEGIN:VCALENDAR"
    assert lines[-1] == "END:VCALENDAR"
    assert "VERSION:2.0" in lines
    assert not any(ln.startswith("BEGIN:VEVENT") for ln in lines)


def test_no_method_property():
    """METHOD:PUBLISH makes some clients treat a subscription as an iTIP
    message rather than a calendar to display."""
    body = ics.render([_event()], name="δοκιμή", dtstamp=STAMP)
    assert not any(ln.startswith("METHOD:") for ln in _logical_lines(body))


def test_begin_and_end_are_balanced():
    body = ics.render([_event(alarms=(ics.Alarm(7, "σε 7 ημέρες"),)), _event()],
                      name="δοκιμή", dtstamp=STAMP)
    lines = _logical_lines(body)
    for block in ("VCALENDAR", "VEVENT", "VALARM"):
        assert lines.count(f"BEGIN:{block}") == lines.count(f"END:{block}")


# --------------------------------------------------------------------------- #
# The event
# --------------------------------------------------------------------------- #
def test_dtend_is_omitted_for_an_instant():
    """RFC 5545 requires DTEND > DTSTART; an absent DTEND means 'ends at
    DTSTART', which is how an instant is spelled."""
    for event in (_event(), _event(end=dt.datetime(2026, 4, 14, 23, 59, tzinfo=EEST))):
        lines = _logical_lines(ics.render([event], name="δ", dtstamp=STAMP))
        assert not any(ln.startswith("DTEND:") for ln in lines)


def test_dtend_is_emitted_for_a_real_interval():
    event = _event(start=dt.datetime(2026, 4, 14, 23, 29, tzinfo=EEST),
                   end=dt.datetime(2026, 4, 14, 23, 59, tzinfo=EEST))
    lines = _logical_lines(ics.render([event], name="δ", dtstamp=STAMP))
    assert "DTSTART:20260414T202900Z" in lines
    assert "DTEND:20260414T205900Z" in lines


def test_url_is_not_escaped():
    """A URI value is not TEXT. Escaping a comma inside a URL breaks the URL."""
    url = "https://example.gr/act/26PROC012345678?a=1,2"
    lines = _logical_lines(ics.render([_event(url=url)], name="δ", dtstamp=STAMP))
    assert f"URL:{url}" in lines


def test_uid_is_stable_across_renders():
    one = ics.render([_event()], name="δ", dtstamp=STAMP)
    two = ics.render([_event()], name="δ",
                     dtstamp=STAMP + dt.timedelta(days=3))
    uid = "UID:26PROC012345678@khmdhs"
    assert uid in _logical_lines(one) and uid in _logical_lines(two)


def test_alarms_become_valarm_blocks():
    event = _event(alarms=(ics.Alarm(7, "Λήξη σε 7 ημέρες"),
                           ics.Alarm(1, "Λήξη αύριο")))
    lines = _logical_lines(ics.render([event], name="δ", dtstamp=STAMP))
    assert lines.count("BEGIN:VALARM") == 2
    assert "TRIGGER:-P7D" in lines and "TRIGGER:-P1D" in lines
    assert lines.count("ACTION:DISPLAY") == 2
    # A DISPLAY alarm without DESCRIPTION is invalid.
    assert "DESCRIPTION:Λήξη σε 7 ημέρες" in lines


# --------------------------------------------------------------------------- #
# Cancellation (spec §9.5)
# --------------------------------------------------------------------------- #
def test_a_cancelled_act_is_stated_not_dropped():
    """Dropping the VEVENT leaves the event in clients that already imported
    it — a dead deadline in the customer's calendar, permanently."""
    lines = _logical_lines(ics.render([_event(cancelled=True)], name="δ",
                                      dtstamp=STAMP))
    assert "BEGIN:VEVENT" in lines
    assert "STATUS:CANCELLED" in lines
    assert "STATUS:CONFIRMED" not in lines


def test_a_cancelled_act_carries_no_alarms():
    event = _event(cancelled=True, alarms=(ics.Alarm(7, "μην το κάνεις"),))
    lines = _logical_lines(ics.render([event], name="δ", dtstamp=STAMP))
    assert "BEGIN:VALARM" not in lines


def test_a_live_act_is_confirmed():
    lines = _logical_lines(ics.render([_event()], name="δ", dtstamp=STAMP))
    assert "STATUS:CONFIRMED" in lines


# --------------------------------------------------------------------------- #
# SEQUENCE (spec §9.4) — the property that makes a moved deadline move
# --------------------------------------------------------------------------- #
def test_sequence_rises_with_last_update_and_is_stable_without_it():
    earlier = dt.datetime(2026, 4, 1, 10, 0, tzinfo=dt.timezone.utc)
    later = dt.datetime(2026, 4, 2, 10, 0, tzinfo=dt.timezone.utc)
    assert ics.sequence_from(later) > ics.sequence_from(earlier)
    assert ics.sequence_from(earlier) == ics.sequence_from(earlier)


def test_sequence_is_zero_without_a_last_update():
    assert ics.sequence_from(None) == 0


def test_sequence_accepts_a_plain_date():
    assert ics.sequence_from(dt.date(2026, 4, 1)) > 0


def test_sequence_fits_a_32_bit_client():
    """Several clients store SEQUENCE as int32. Check the far end of any
    horizon this product has."""
    far = dt.datetime(2075, 1, 1, tzinfo=dt.timezone.utc)
    assert ics.sequence_from(far) < 2_147_483_647


def test_sequence_reaches_the_event_body():
    event = _event(sequence=ics.sequence_from(
        dt.datetime(2026, 4, 1, 10, 0, tzinfo=dt.timezone.utc)))
    lines = _logical_lines(ics.render([event], name="δ", dtstamp=STAMP))
    assert f"SEQUENCE:{event.sequence}" in lines


# --------------------------------------------------------------------------- #
# Timestamps — the off-by-one this guards (calendar-feed §4c, deadlines §5)
# --------------------------------------------------------------------------- #
def test_a_naive_datetime_is_refused():
    with pytest.raises(ValueError):
        ics.utc(dt.datetime(2026, 4, 14, 23, 59))


def test_utc_conversion_crosses_the_athens_midnight_correctly():
    """01:00 Athens on the 15th is 22:00 UTC on the 14th. A formatter that
    took the local date would put this event on the wrong day."""
    assert ics.utc(dt.datetime(2026, 4, 15, 1, 0, tzinfo=EEST)) == "20260414T220000Z"
    assert ics.utc(dt.datetime(2026, 4, 14, 23, 59, tzinfo=EEST)) == "20260414T205900Z"


def test_utc_handles_the_winter_offset():
    assert ics.utc(dt.datetime(2026, 1, 15, 12, 0, tzinfo=EET)) == "20260115T100000Z"


def test_a_non_datetime_is_refused():
    with pytest.raises(TypeError):
        ics.utc(dt.date(2026, 4, 14))


# --------------------------------------------------------------------------- #
# All-day entries (the feed's lapsed-subscription notice, calendar-feed §5)
# --------------------------------------------------------------------------- #
def test_a_plain_date_becomes_an_all_day_event():
    event = _event(start=dt.date(2026, 9, 23))
    lines = _logical_lines(ics.render([event], name="δ", dtstamp=STAMP))
    assert "DTSTART;VALUE=DATE:20260923" in lines
    assert not any(ln.startswith("DTSTART:") for ln in lines)


def test_an_all_day_event_never_carries_dtend():
    """A DATE start with no DTEND is one whole day. An `end` passed by mistake
    must not turn it into something invalid."""
    event = _event(start=dt.date(2026, 9, 23),
                   end=dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc))
    lines = _logical_lines(ics.render([event], name="δ", dtstamp=STAMP))
    assert not any(ln.startswith("DTEND") for ln in lines)


def test_an_all_day_date_is_not_shifted_by_a_timezone():
    """No UTC conversion for a date — that would move it a day for anyone
    east or west of UTC."""
    event = _event(start=dt.date(2026, 1, 1))
    lines = _logical_lines(ics.render([event], name="δ", dtstamp=STAMP))
    assert "DTSTART;VALUE=DATE:20260101" in lines
