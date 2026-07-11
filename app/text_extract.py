"""
text_extract.py — deterministic, offline extraction of structured candidates
from an act's full text. Pure regex + validation + keyword anchoring; NO AI, no
network, no paid models. The web route (admin.py) layers DB validation on top
(CPV vocabulary, postal→NUTS, ΑΦΜ→party, authority name fuzzy match).

Design: SUGGEST, never write. Everything here just proposes candidates with a
best-guess target field; the curator accepts each one in the UI. Low-precision
guesses are deliberately omitted rather than risk polluting a structured field.
"""
from __future__ import annotations

import re
import unicodedata

# Date field name → the normalized keywords that anchor a date to it. Checked in
# this order (most specific first); the first field whose keyword appears in the
# short window before a date wins.
DATE_FIELDS = [
    ("final_submission_date", ("καταληκτικη", "προθεσμια υποβολης", "ληξη προθεσμιας",
                               "υποβολης προσφορων", "καταληκτικη ημερομηνια")),
    ("submission_date",       ("δημοσιευσης", "αναρτησης", "ημερομηνια δημοσιευσης")),
    ("signed_date",           ("υπογραφης", "υπεγραφη", "ημερομηνια υπογραφης")),
    ("start_date",            ("εναρξης", "ημερομηνια εναρξης", "εναρξη ισχυος")),
    ("end_date",              ("ληξης", "ληξη συμβασης", "ημερομηνια ληξης")),
]

# Amount field name → anchoring keywords, most specific first.
AMOUNT_FIELDS = [
    ("bid_bond_amount",        ("εγγυηση συμμετοχης", "εγγυητικη επιστολη", "εγγυηση συμμετοχης")),
    ("total_cost_without_vat", ("χωρις φπα", "ανευ φπα", "προ φπα", "μη συμπεριλαμβανομενου φπα",
                                "εκτος φπα")),
    ("total_cost_with_vat",    ("με φπα", "συμπεριλαμβανομενου φπα", "συνολικη αξια",
                                "συνολικο ποσο", "συμπεριλαμβανομενου του φπα")),
    ("budget",                 ("προυπολογισμος", "προυπολογισθεισα", "προυπολογιζομενη",
                                "εκτιμωμενη αξια", "προυπολογισθεισα δαπανη")),
]

_GREEK_MONTHS = {
    "ιανουαριου": 1, "φεβρουαριου": 2, "μαρτιου": 3, "απριλιου": 4, "μαιου": 5,
    "ιουνιου": 6, "ιουλιου": 7, "αυγουστου": 8, "σεπτεμβριου": 9, "οκτωβριου": 10,
    "νοεμβριου": 11, "δεκεμβριου": 12,
}

_NUM_DATE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")
# Written Greek dates, tolerant of an ordinal suffix on the day ("28ης", "1ης",
# "3ου", "28η") and an optional genitive article before the year ("Ιουλίου του
# 2026"). The middle token must still resolve to a real month name (below), which
# keeps false positives out. Examples matched:
#   28 Ιουλίου 2026 · 28ης Ιουλίου του 2026 · 1ης Σεπτεμβρίου 2026
_GR_DATE = re.compile(
    r"\b(\d{1,2})(?:ης|ος|ου|ής|η|ή|ῃ)?\s+([Α-Ωα-ωΆ-Ώάέίόύήώϊϋΐΰ]+)"
    r"(?:\s+του)?\s+(\d{4})\b")
# money: 1.234.567,89 | 1234,89 | 1.234 followed by a currency marker (€ / ευρω / eur)
_MONEY = re.compile(
    r"(?:€\s*)?(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:€|ευρω|eur)",
    re.IGNORECASE,
)
_AFM = re.compile(r"\b(\d{9})\b")
_POSTAL = re.compile(r"\b(\d{3})\s?(\d{2})\b")
_CPV = re.compile(r"\b(\d{8})(?:-\d)?\b")
_TITLE = re.compile(r"(?:ΘΕΜΑ|ΑΝΤΙΚΕΙΜΕΝΟ|Τίτλος)\s*[:\-]\s*(.+)")


def _norm(s: str) -> str:
    """Lowercase + strip Greek accents, for accent-insensitive keyword matching."""
    s = unicodedata.normalize("NFD", s or "").lower()
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _anchor(norm_text: str, pos: int, fields, window: int = 55) -> str | None:
    """Field whose keyword sits CLOSEST before `pos` (labels precede their value
    in these documents, and the nearest one wins over priority order)."""
    lo = max(0, pos - window)
    ctx = norm_text[lo:pos]
    best_field, best_idx = None, -1
    for field, keys in fields:
        for k in keys:
            i = ctx.rfind(k)
            if i > best_idx:
                best_idx, best_field = i, field
    return best_field


def valid_afm(s: str) -> bool:
    """Greek ΑΦΜ mod-11 check digit (filters phone/protocol numbers)."""
    if len(s) != 9 or not s.isdigit() or s == "000000000":
        return False
    d = [int(c) for c in s]
    total = sum(d[i] * (2 ** (8 - i)) for i in range(8))
    return total % 11 % 10 == d[8]


def find_dates(text: str, limit: int = 20) -> list[dict]:
    norm = _norm(text)
    out, seen = [], set()

    def add(iso, raw, pos):
        if iso in seen:
            return
        seen.add(iso)
        out.append({"iso": iso, "raw": raw, "target": _anchor(norm, pos, DATE_FIELDS)})

    for m in _NUM_DATE.finditer(text):
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mth <= 12 and 2000 <= y <= 2100:
            add(f"{y:04d}-{mth:02d}-{d:02d}", m.group(0), m.start())
    for m in _GR_DATE.finditer(text):
        mth = _GREEK_MONTHS.get(_norm(m.group(2)))
        d, y = int(m.group(1)), int(m.group(3))
        if mth and 1 <= d <= 31 and 2000 <= y <= 2100:
            add(f"{y:04d}-{mth:02d}-{d:02d}", m.group(0), m.start())
    return out[:limit]


def _parse_greek_money(s: str) -> float | None:
    t = s.strip()
    if "," in t:                       # comma = decimal, dots = thousands
        t = t.replace(".", "").replace(",", ".")
    else:                              # dots are thousands separators
        t = t.replace(".", "")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def find_amounts(text: str, limit: int = 20) -> list[dict]:
    norm = _norm(text)
    out, seen = [], set()
    for m in _MONEY.finditer(text):
        val = _parse_greek_money(m.group(1))
        if val is None or val <= 0:
            continue
        target = _anchor(norm, m.start(), AMOUNT_FIELDS)
        key = (val, target)
        if key in seen:
            continue
        seen.add(key)
        out.append({"value": f"{val:.2f}", "raw": m.group(0).strip(), "target": target})
    return out[:limit]


def find_afms(text: str, limit: int = 10) -> list[str]:
    out = []
    for m in _AFM.finditer(text):
        s = m.group(1)
        if valid_afm(s) and s not in out:
            out.append(s)
    return out[:limit]


def find_postals(text: str, limit: int = 10) -> list[str]:
    out = []
    for m in _POSTAL.finditer(text):
        code = m.group(1) + m.group(2)
        if code[0] != "0" and code not in out:   # Greek TK: 10000-99999
            out.append(code)
    return out[:limit]


def find_cpv_prefixes(text: str, limit: int = 30) -> list[str]:
    out = []
    for m in _CPV.finditer(text):
        p = m.group(1)
        if p not in out:
            out.append(p)
    return out[:limit]


# Words that typically start a contracting-authority name (normalized). Used to
# pick ONE candidate line from the letterhead to fuzzy-match against proc.authority.
# Deliberately excludes generic boilerplate ("ελληνικη δημοκρατια", "διευθυνση")
# that heads almost every Greek document, so the real authority line is picked.
_AUTH_STARTS = (
    "δημος", "υπουργειο", "περιφερεια", "νοσοκομειο", "οργανισμος", "ιδρυμα",
    "επιτροπη", "κεντρο", "ταμειο", "πανεπιστημιο", "δευα", "αποκεντρωμενη διοικηση",
    "γενικο νοσοκομειο", "εθνικο κεντρο", "εφορεια", "λιμεναρχειο", "σχολη",
)


def find_authority_hint(text: str) -> str | None:
    """The first letterhead line that looks like an authority name (for a fuzzy
    dictionary match). Bounded to the document head; None if nothing plausible."""
    for line in (text or "")[:1500].splitlines():
        line = line.strip(" .·—-\t")
        if 6 <= len(line) <= 90 and _norm(line).startswith(_AUTH_STARTS):
            return line
    return None


def find_title(text: str) -> str | None:
    for line in (text or "").splitlines():
        m = _TITLE.search(line)
        if m:
            t = m.group(1).strip(" .·—-\t")
            if len(t) >= 8:
                return t[:400]
    return None
