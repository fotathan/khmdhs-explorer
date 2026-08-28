# -*- coding: utf-8 -*-
"""Greek-safe matching, snippets and highlighting.

ONE normaliser, shared by the match-explanation chips, the occurrence
navigator and every highlight rendered on the page. If these three ever stop
agreeing with each other the feature is worse than useless — a count that does
not equal what the reader can see is a lie about the record — so everything
below is derived from a single tokenisation pass and nothing re-implements
"does this word match" a second time.

The hard constraint is OFFSET PRESERVATION. Highlighting means slicing the
ORIGINAL text at offsets discovered in the folded text, so folding may never
change the length of a string:

    len(fold(s)) == len(s)   for every s

That rules out the usual NFD-decompose-and-strip-combining-marks trick, which
turns 'ά' (1 char) into 'α' + U+0301 (2 chars) and destroys the mapping back.
Instead we fold per CHARACTER over precomposed (NFC) text: every character maps
to exactly one character, so index i in fold(s) is always index i in s.

Word tokenisation is deliberately ours, not Postgres'. The stems come from
Postgres (same 'greek' configuration as search_tsv — see search_match.py), but
the character offsets come from the regex below, because Postgres' text-search
parser reports tokens without offsets and can emit overlapping tokens for
hyphenated words and URLs. Splitting the two keeps the offsets exact and the
stemming authoritative.
"""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass

# Greek (incl. polytonic), Latin, digits. Same character class the search
# filter's prefix mode uses in main.py — one idea of "a word", not two.
WORD_RE = re.compile(r"[0-9A-Za-zͰ-Ͽἀ-῿]+")

# Characters whose fold is not just str.lower(): accented Greek vowels, the
# dialytika forms, and final sigma. Written out rather than derived so the
# mapping is auditable at a glance.
_EXPLICIT = {
    "ά": "α", "έ": "ε", "ή": "η", "ί": "ι", "ό": "ο", "ύ": "υ", "ώ": "ω",
    "ΐ": "ι", "ΰ": "υ", "ϊ": "ι", "ϋ": "υ",
    "ς": "σ",                       # final sigma folds to medial sigma
    "Ά": "α", "Έ": "ε", "Ή": "η", "Ί": "ι", "Ό": "ο", "Ύ": "υ", "Ώ": "ω",
    "Ϊ": "ι", "Ϋ": "υ",
}


def _fold_char(ch: str) -> str:
    """Fold ONE character to ONE character. Never returns a longer string."""
    mapped = _EXPLICIT.get(ch)
    if mapped is not None:
        return mapped
    low = ch.lower()
    if len(low) != 1:
        # e.g. 'İ'.lower() is two codepoints — would break the offset mapping.
        low = ch
    mapped = _EXPLICIT.get(low)
    if mapped is not None:
        return mapped
    # Generic accent strip for anything the table above misses (polytonic
    # Greek, accented Latin): take the NFD base letter, but only when the rest
    # of the decomposition is combining marks, so we never drop a real letter.
    dec = unicodedata.normalize("NFD", low)
    if len(dec) > 1 and all(unicodedata.combining(c) for c in dec[1:]):
        return _EXPLICIT.get(dec[0], dec[0])
    return low


class _FoldTable(dict):
    """str.translate table that folds lazily and caches, per codepoint."""

    def __missing__(self, cp: int) -> str:
        folded = _fold_char(chr(cp))
        self[cp] = folded
        return folded


_FOLD = _FoldTable()


def fold(s: str) -> str:
    """Accent-, case- and final-sigma-insensitive form of `s`, same length.

    ΑΝΆΘΕΣΗ, ανάθεση and αναθεση all fold to 'αναθεση'.
    """
    if not s:
        return ""
    return s.translate(_FOLD)


# --------------------------------------------------------------------------- #
# Tokenisation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Token:
    text: str          # surface form, exactly as it appears in the source
    folded: str        # fold(text)
    start: int         # index into the ORIGINAL text
    end: int


def tokenize(text: str) -> list[Token]:
    """Words of `text` with their offsets into `text` (not into fold(text))."""
    if not text:
        return []
    folded = fold(text)          # same length, so the spans line up
    return [Token(text[m.start():m.end()], folded[m.start():m.end()],
                  m.start(), m.end())
            for m in WORD_RE.finditer(text)]


# --------------------------------------------------------------------------- #
# Paragraph split — §4.2's shared helper.
#
# The occurrence navigator's section labels ("Πλήρες κείμενο — παρ. 12") and the
# rendered full text MUST come from the same split, or a listed occurrence
# points at a paragraph that does not exist. So there is exactly one splitter
# and both callers use it.
# --------------------------------------------------------------------------- #
_PARA_SPLIT = re.compile(r"\n[ \t]*\n+")
_LINE_SPLIT = re.compile(r"\n+")

# A lot / section heading we can name instead of numbering. Kept deliberately
# narrow: a false positive mislabels an occurrence, which is worse than a
# plain "παρ. 12".
_HEADING_RE = re.compile(
    r"^\s*("
    r"ΤΜΗΜΑ|ΜΕΡΟΣ|ΑΡΘΡΟ|ΠΑΡΑΡΤΗΜΑ|ΚΕΦΑΛΑΙΟ|"
    r"Τμήμα|Μέρος|Άρθρο|Παράρτημα|Κεφάλαιο"
    r")\s+([0-9]{1,3}|[Α-Ω]{1,3})\b"
)

# Very long unbroken blobs (OCR often produces one) get chunked so a label
# still means something. Chunks break on a word boundary.
_CHUNK = 1500


@dataclass(frozen=True)
class Paragraph:
    index: int         # 1-based, what the label shows
    start: int         # offsets into the full text
    end: int
    text: str
    heading: str | None


def _chunk_span(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Break one over-long span into word-boundary chunks of ~_CHUNK chars."""
    if end - start <= _CHUNK:
        return [(start, end)]
    out, pos = [], start
    while end - pos > _CHUNK:
        cut = text.rfind(" ", pos + _CHUNK // 2, pos + _CHUNK)
        if cut <= pos:
            cut = pos + _CHUNK
        out.append((pos, cut))
        pos = cut
    if pos < end:
        out.append((pos, end))
    return out


def split_full_text(text: str | None) -> list[Paragraph]:
    """Split a document into numbered paragraphs, offsets preserved.

    Blank lines first (the normal case); a document with none falls back to
    single newlines, then to fixed-size word-boundary chunks, so an OCR'd wall
    of text still gets usable paragraph numbers.
    """
    if not text or not text.strip():
        return []

    def spans(pattern) -> list[tuple[int, int]]:
        out, pos = [], 0
        for m in pattern.finditer(text):
            if m.start() > pos:
                out.append((pos, m.start()))
            pos = m.end()
        if pos < len(text):
            out.append((pos, len(text)))
        return [(s, e) for s, e in out if text[s:e].strip()]

    raw = spans(_PARA_SPLIT)
    if len(raw) <= 1:
        raw = spans(_LINE_SPLIT) or raw

    paragraphs: list[Paragraph] = []
    heading: str | None = None
    for s, e in raw:
        m = _HEADING_RE.match(text[s:e])
        if m:
            heading = f"{m.group(1)} {m.group(2)}"
        for cs, ce in _chunk_span(text, s, e):
            # Trim leading/trailing whitespace out of the stored span so the
            # rendered paragraph and its offsets agree exactly.
            body = text[cs:ce]
            lead = len(body) - len(body.lstrip())
            trail = len(body) - len(body.rstrip())
            cs, ce = cs + lead, ce - trail
            if ce <= cs:
                continue
            paragraphs.append(Paragraph(len(paragraphs) + 1, cs, ce,
                                        text[cs:ce], heading))
    return paragraphs


# --------------------------------------------------------------------------- #
# Highlighting
# --------------------------------------------------------------------------- #
def term_slug(term: str) -> str:
    """Stable ASCII id fragment for a term.

    Greek in an HTML id is legal but a nuisance in fragments and selectors, so
    the folded term is hashed. Same term -> same slug on every request, which
    is what lets the occurrence list and the rendered marks agree.
    """
    return hashlib.sha1(fold(term).encode("utf-8")).hexdigest()[:10]


def mark(text: str, spans: list[tuple[int, int, str | None]]) -> str:
    """Escape `text` and wrap each (start, end, anchor_id) span in <mark>.

    Escape FIRST, insert the markup after — never the other way round, and the
    act's own text is never trusted as HTML.
    """
    if not text:
        return ""
    out: list[str] = []
    pos = 0
    for start, end, anchor in sorted(spans):
        if start < pos or end > len(text) or end <= start:
            continue                       # overlapping / stale span: skip it
        out.append(html.escape(text[pos:start]))
        aid = f' id="{html.escape(anchor, quote=True)}"' if anchor else ""
        out.append(f'<mark class="hl"{aid}>{html.escape(text[start:end])}</mark>')
        pos = end
    out.append(html.escape(text[pos:]))
    return "".join(out)


_SNIPPET_RADIUS = 60


def snippet(text: str, start: int, end: int, radius: int = _SNIPPET_RADIUS) -> str:
    """±`radius` characters around [start, end), escaped, term marked.

    Trimmed outward to whitespace so a word is never cut in half (and, since
    Python strings are codepoints, never a character either).
    """
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    if lo > 0:
        cut = text.find(" ", lo, start)
        lo = cut + 1 if cut != -1 else lo
    if hi < len(text):
        cut = text.rfind(" ", end, hi)
        hi = cut if cut != -1 else hi
    body = mark(text[lo:hi], [(start - lo, end - lo, None)])
    return ("… " if lo > 0 else "") + body.strip() + (" …" if hi < len(text) else "")
