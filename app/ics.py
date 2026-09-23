"""
ics.py — RFC 5545 output. Turns deadlines into a calendar file.

Spec: docs/specs/calendar-feed.md (slice 1).

Why this module exists
----------------------
A submission deadline belongs in the bidder's own calendar, not only in our
page and our mail. Getting it there is a text format, nothing more: no OAuth
scope, no sync protocol, no vendor. This module is that text format, and
nothing else — rows in, string out. No database, no request, no app imports.
That is what makes it testable to the byte, which matters more here than
usual: the failure mode of malformed iCalendar is a client that imports
NOTHING and says nothing about why.

Why not the `icalendar` package
-------------------------------
It would be a dependency, a version to track and a supply-chain surface for
what is ~150 lines of string handling. The format's difficulty is not volume,
it is two specific rules (below) that a library would also have to get right
and that we have to understand anyway to test.

The two rules that actually bite, both in Greek
-----------------------------------------------
1. **Folding is measured in OCTETS, not characters** (RFC 5545 §3.1). Greek is
   two bytes per character in UTF-8, so folding by `len(str)` produces lines up
   to 150 octets — and cutting at an arbitrary byte splits a UTF-8 sequence and
   corrupts the text. `_fold` counts bytes and backs off a cut that would land
   inside a character. Both bugs are invisible in any ASCII test, which is why
   test_ics.py is written in Greek.

2. **TEXT values escape `\\ ; ,` and newlines** (§3.3.11). Tender titles carry
   commas and semicolons constantly ("Προμήθεια ειδών, 3 τμήματα"), and an
   unescaped comma does not error — it silently truncates the field, because a
   comma separates list values. Backslash is escaped FIRST or the escapes we
   add get escaped again.

   A colon is NOT escaped in a TEXT value. URI values (URL) are not escaped at
   all: `\\,` in a URL is simply a broken URL. Hence `escape=` on `_prop`.

Instants, not intervals
-----------------------
A deadline is a point in time. RFC 5545 requires DTEND to be strictly after
DTSTART, so `DTEND == DTSTART` is invalid — the correct way to write an instant
is to OMIT DTEND, which the spec defines as ending at DTSTART. `event()` does
that when no end is given; pass one only for a real interval (e.g. a 30-minute
block leading up to the cut-off, if that reads better in a day view).

Times are emitted in UTC. The client renders them in the viewer's own zone,
which for these customers is Athens, so this is both correct and avoids
shipping a VTIMEZONE block. Naive datetimes are REFUSED rather than assumed —
the same guard `docs/specs/working-day-deadlines.md` §5 puts on date
arithmetic, for the same reason: the assumption is wrong exactly at midnight,
and always in the direction that flatters the deadline.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

# Identifies the product that wrote the file. Purely informational, but clients
# log it and it is what makes a support question answerable.
PRODID = "-//KHMDHS Explorer//Calendar Feed//EL"

# RFC 5545 §3.1: a content line is at most 75 octets, excluding the CRLF. A
# folded continuation begins with one space, and that space counts towards the
# 75 — so continuations carry one octet less of content than the first line.
_LINE_OCTETS = 75
_CONT_OCTETS = _LINE_OCTETS - 1

# SEQUENCE has to fit a 32-bit integer for the clients that store it as one.
# Seconds since this epoch stay well inside that until well past 2080, and are
# monotonic by construction — see sequence_from().
_SEQ_EPOCH = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def escape_text(value) -> str:
    """Escape one TEXT value (RFC 5545 §3.3.11).

    Backslash first: doing it later would escape the backslashes this function
    itself introduces, so a semicolon would come out double-escaped.
    """
    s = "" if value is None else str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;").replace(",", "\\,")
    # Every flavour of line break becomes the literal two-character escape.
    s = s.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    # Control characters are not permitted in a TEXT value. The line breaks are
    # already gone, so anything left here is junk from an upstream extractor
    # (OCR and PDF text both produce stray control bytes) and is dropped rather
    # than passed on to trip a parser.
    return "".join(ch for ch in s if ord(ch) >= 0x20 and ord(ch) != 0x7F)


def fold(line: str) -> str:
    """Fold one content line to 75 octets, never splitting a UTF-8 character.

    Returns the line with CRLF + space inserted at each fold point. Unfolding
    (removing every CRLF followed by one space) reproduces the input exactly,
    which is the property test_ics.py asserts.
    """
    raw = line.encode("utf-8")
    if len(raw) <= _LINE_OCTETS:
        return line

    chunks, start, limit = [], 0, _LINE_OCTETS
    while start < len(raw):
        end = min(start + limit, len(raw))
        if end < len(raw):
            # raw[end] is the first byte of the NEXT chunk. While it is a UTF-8
            # continuation byte (0b10xxxxxx) the cut sits inside a character,
            # so walk back to the character's lead byte. A character is at most
            # four bytes and `limit` is at least 74, so this always leaves
            # progress to make and the loop always terminates.
            while end > start and (raw[end] & 0xC0) == 0x80:
                end -= 1
        chunks.append(raw[start:end])
        start = end
        limit = _CONT_OCTETS
    return "\r\n ".join(chunk.decode("utf-8") for chunk in chunks)


def utc(value: dt.datetime) -> str:
    """Format an aware datetime as an RFC 5545 UTC stamp.

    A naive datetime is an error, not an assumption. `final_submission_date` is
    a timestamptz and every caller has a real instant; anything naive got that
    way by losing information, and guessing a zone here would hide it.
    """
    if not isinstance(value, dt.datetime):
        raise TypeError(f"expected datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("naive datetime: an ICS timestamp needs a timezone")
    return value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sequence_from(last_update) -> int:
    """Derive SEQUENCE from an act's last_update_date.

    SEQUENCE is the property that decides whether a moved deadline actually
    MOVES in the customer's calendar: a client that already holds this UID
    ignores a changed DTSTART unless SEQUENCE is higher than the one it stored.
    A deadline that silently fails to update is worse than no feed at all,
    because by then the customer is relying on it.

    Deriving it from the source timestamp rather than keeping a counter of our
    own means there is no column to migrate, nothing to increment, and no way
    for a re-render of unchanged data to produce a different number.
    """
    if last_update is None:
        return 0
    if isinstance(last_update, dt.datetime):
        moment = last_update
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
    elif isinstance(last_update, dt.date):
        moment = dt.datetime(last_update.year, last_update.month,
                             last_update.day, tzinfo=dt.timezone.utc)
    else:
        raise TypeError(f"expected date/datetime, got {type(last_update).__name__}")
    return max(0, int((moment - _SEQ_EPOCH).total_seconds()))


# --------------------------------------------------------------------------- #
# The pieces of a calendar
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class Alarm:
    """A client-side reminder. The customer's own calendar does the nagging, on
    the device they already trust for it — which is why this feed can replace a
    reminder mail rather than duplicating one."""
    days_before: int
    description: str


@dataclasses.dataclass(frozen=True)
class Event:
    """One VEVENT. `start` is an aware datetime for a moment (a deadline) or a
    plain date for an all-day entry (the lapsed-subscription notice the feed
    shows instead of deadlines)."""
    uid: str
    start: dt.datetime | dt.date
    summary: str
    end: dt.datetime | None = None
    description: str = ""
    url: str = ""
    sequence: int = 0
    cancelled: bool = False
    alarms: tuple[Alarm, ...] = ()


def _prop(name: str, value, *, escape: bool = True) -> str:
    """One content line, escaped if it is a TEXT value, then folded."""
    body = escape_text(value) if escape else str(value)
    return fold(f"{name}:{body}")


def _alarm_lines(alarm: Alarm) -> list[str]:
    # A DISPLAY alarm must carry a DESCRIPTION (RFC 5545 §3.6.6).
    days = int(alarm.days_before)
    trigger = f"-P{days}D" if days > 0 else "PT0S"
    return ["BEGIN:VALARM",
            _prop("ACTION", "DISPLAY", escape=False),
            _prop("TRIGGER", trigger, escape=False),
            _prop("DESCRIPTION", alarm.description),
            "END:VALARM"]


def _event_lines(event: Event, dtstamp: dt.datetime) -> list[str]:
    lines = ["BEGIN:VEVENT",
             _prop("UID", event.uid, escape=False),
             _prop("DTSTAMP", utc(dtstamp), escape=False)]
    # datetime is a subclass of date, so the order of these tests matters.
    all_day = not isinstance(event.start, dt.datetime)
    if all_day:
        # RFC 5545 §3.6.1: a DATE start with no DTEND lasts exactly that day.
        # No UTC conversion — an all-day entry has no time to convert, and
        # shifting it would move it to the wrong day for half the world.
        lines.append(_prop("DTSTART;VALUE=DATE",
                           event.start.strftime("%Y%m%d"), escape=False))
    else:
        lines.append(_prop("DTSTART", utc(event.start), escape=False))
    # Omit DTEND for an instant: RFC 5545 §3.6.1 requires DTEND to be strictly
    # after DTSTART, and defines an absent DTEND as ending at DTSTART. Emitting
    # DTEND == DTSTART is the invalid spelling of the same thing.
    if not all_day and event.end is not None and event.end > event.start:
        lines.append(_prop("DTEND", utc(event.end), escape=False))
    lines.append(_prop("SUMMARY", event.summary))
    if event.description:
        lines.append(_prop("DESCRIPTION", event.description))
    if event.url:
        # URI value: escaping it would corrupt any URL containing a comma.
        lines.append(_prop("URL", event.url, escape=False))
    lines.append(_prop("SEQUENCE", int(event.sequence), escape=False))
    # A cancelled act is stated, never merely dropped. Several clients keep an
    # event they have already imported when it vanishes from the feed, which
    # would leave a dead deadline in the customer's calendar permanently.
    lines.append(_prop("STATUS", "CANCELLED" if event.cancelled else "CONFIRMED",
                       escape=False))
    if not event.cancelled:
        for alarm in event.alarms:
            lines.extend(_alarm_lines(alarm))
    lines.append("END:VEVENT")
    return lines


def render(events, *, name: str, timezone: str = "Europe/Athens",
           refresh_hours: int = 6, dtstamp: dt.datetime | None = None,
           prodid: str = PRODID) -> str:
    """Render a complete calendar. An empty `events` is valid and expected —
    it is what a lapsed customer's feed returns (spec §5), and it must parse.

    `dtstamp` is injectable so a test can assert an exact body; it defaults to
    now, which is what every caller in the app wants.
    """
    stamp = dtstamp or dt.datetime.now(dt.timezone.utc)
    lines = ["BEGIN:VCALENDAR",
             _prop("VERSION", "2.0", escape=False),
             _prop("PRODID", prodid, escape=False),
             _prop("CALSCALE", "GREGORIAN", escape=False),
             _prop("X-WR-CALNAME", name),
             _prop("X-WR-TIMEZONE", timezone, escape=False),
             # Hints only; clients poll on their own schedule and several
             # ignore both. Sent because the ones that honour them poll less.
             _prop("REFRESH-INTERVAL;VALUE=DURATION", f"PT{int(refresh_hours)}H",
                   escape=False),
             _prop("X-PUBLISHED-TTL", f"PT{int(refresh_hours)}H", escape=False)]
    # No METHOD. A subscription feed carrying METHOD:PUBLISH is read by some
    # clients as an iTIP message — an invitation to act on — rather than as a
    # calendar to display.
    for event in events:
        lines.extend(_event_lines(event, stamp))
    lines.append("END:VCALENDAR")
    # CRLF everywhere, including after the final line.
    return "\r\n".join(lines) + "\r\n"
