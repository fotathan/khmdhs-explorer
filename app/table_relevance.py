"""
table_relevance.py — classify extracted tables as procurement item/service
lists vs. irrelevant grids (tables of contents, signature blocks, revision
histories, distribution lists, clause layouts).

KHMDHS-specific glue: NOT one of the byte-identical sibling modules
(extractors/exporter/ocr). Operates on the extractor's table dicts
({rows: 2-D strings, rows[0] = header, n_rows, n_cols, role?, group?})
without mutating anything the save/export paths read.

Used by BOTH surfaces:
  * interactive extract-tables preview (app/tables.py) — relevant tables are
    pre-checked, irrelevant ones render dimmed with a badge; nothing is hidden.
  * mass table-extraction jobs (db.py _table_outcome) — only relevant tables
    are persisted; the per-act log notes what was skipped.

Free/local heuristic only — weighted keyword + content-shape signals, no API.
Tuned against real KHMDHS/Diavgeia tender documents (see the scoring notes on
each signal). A wrong verdict in the interactive flow costs one checkbox click.

Env:
  TABLE_RELEVANCE=0   kill switch — classify everything as relevant (default 1)
"""

from __future__ import annotations

import os
import re
import unicodedata

# Verdict threshold: a table needs this many net points to count as an
# item/service list. Header keywords alone (e.g. ΠΕΡΙΓΡΑΦΗ + ΠΟΣΟΤΗΤΑ) clear
# it; content-only tables (headerless PDF grids) clear it via money/quantity
# shape signals.
THRESHOLD = 3


def enabled() -> bool:
    return os.environ.get("TABLE_RELEVANCE", "1") == "1"


# --------------------------------------------------------------------------- #
# normalization
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    """Uppercase, accent-stripped, whitespace-collapsed — Greek headers arrive
    in every casing/accent combination (ΠΟΣΌΤΗΤΑ/Ποσότητα/ΠΟΣΟΤΗΤΑ)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s).strip().upper()


# --------------------------------------------------------------------------- #
# signal vocabularies (normalized form — no accents)
# --------------------------------------------------------------------------- #
# Strong item-list header stems (2 pts each): the columns an item/service list
# is actually made of.
_HDR_STRONG = (
    "ΠΕΡΙΓΡΑΦ",        # description
    "ΠΟΣΟΤΗΤ",         # quantity
    "ΤΕΜΑΧΙ",          # pieces
    "ΤΙΜΗ",            # price
    "ΔΑΠΑΝ",           # expenditure
    "CPV",
    "ΜΟΝΑΔΑ ΜΕΤΡΗΣ",   # unit of measure (full phrase)
)
# Supporting header stems (1 pt each).
_HDR_WEAK = (
    "ΕΙΔΟΣ", "ΕΙΔΗ", "ΥΠΗΡΕΣΙ", "ΠΡΟΙΟΝ", "ΥΛΙΚ", "ΑΝΤΙΚΕΙΜΕΝ",
    "ΜΟΝΑΔΑ", "ΑΞΙΑ", "ΚΟΣΤΟΣ", "ΠΡΟΥΠΟΛΟΓΙΣΜ", "ΣΥΝΟΛ", "ΦΠΑ",
    "ΠΑΡΑΔΟΤΕ", "ΠΡΟΣΦΟΡ",
    # English fallbacks (some EU-form documents)
    "DESCRIPTION", "QUANTITY", "QTY", "UNIT", "PRICE", "TOTAL", "VAT", "AMOUNT",
)
# Short tokens that need word boundaries (substring matching would false-hit:
# ΤΕΜ in ΣΕΠΤΕΜΒΡΙΟΣ, ΩΡΕΣ in ΧΩΡΕΣ, Μ.Μ. needs the dots).
_HDR_WORD_RE = (
    re.compile(r"\bΤΕΜ\b\.?"),      # τεμ. (pieces, abbreviated)
    re.compile(r"\bΩΡΕΣ\b"),        # hours (services)
    re.compile(r"\bΜ\.?Μ\.?\b"),    # μονάδα μέτρησης abbreviation
    re.compile(r"\bΑ/Α\b"),         # line number column
)

# Hard vetoes — grids that are definitionally not item lists.
_VETO = (
    "ΠΕΡΙΕΧΟΜΕΝ",          # table of contents
    "ΠΙΝΑΚΑΣ ΑΠΟΔΕΚΤ",     # distribution list
    "ΑΠΟΔΕΚΤΕΣ",           # recipients
    "ΔΙΑΝΟΜΗ",             # distribution
)
# Soft negatives (−3 when they dominate a small grid): sign-off / approval
# blocks and revision histories. Real item tables sometimes mention ΥΠΟΓΡΑΦΗ in
# passing, so these only veto when the table is small AND has no item signals.
_NEG = (
    "ΥΠΟΓΡΑΦ",             # signature
    "ΣΦΡΑΓΙΔ",             # seal/stamp
    "ΣΥΝΤΑΧΘΗΚΕ", "ΕΓΚΡΙΘΗΚΕ", "ΘΕΩΡΗΘΗΚΕ", "Ο ΣΥΝΤΑΞΑΣ",  # sign-off row
    "ΑΝΑΘΕΩΡΗΣ",           # revision
    "ΙΣΤΟΡΙΚΟ ΕΚΔΟΣ",      # version history
)

# Content-shape regexes (run on raw, un-normalized cells).
_MONEY_RE = re.compile(r"€|\d{1,3}(?:\.\d{3})+,\d{2}\b|\b\d+,\d{2}\s*€?")
_CPV_RE = re.compile(r"\b\d{8}(?:-\d)?\b")
_NUMERIC_RE = re.compile(r"^[\s€%.,\d-]*\d[\s€%.,\d-]*$")
_DOTLEAD_RE = re.compile(r"\.{4,}")          # "1. Γενικά ........ 12" (TOC)
_INT_RE = re.compile(r"^\d{1,4}$")


# --------------------------------------------------------------------------- #
# classifier
# --------------------------------------------------------------------------- #
def classify(table: dict) -> tuple[bool, str]:
    """Score one extractor table dict. Returns (relevant, why) — `why` is a
    compact, human-readable list of the matched signals (badge tooltip and
    benchmark output)."""
    rows = table.get("rows") or []
    if not rows:
        return (False, "empty")

    # Header text: first two rows — Greek tender tables often split the header
    # across two rows (label row + unit row).
    header_cells = [c for r in rows[:2] for c in r if c]
    header = _norm(" | ".join(header_cells))
    data_rows = rows[1:]
    n_rows = len(rows)

    why: list[str] = []
    score = 0

    # -- hard vetoes ------------------------------------------------------- #
    for kw in _VETO:
        if kw in header:
            return (False, f"veto:{kw}")
    # Dotted-leader TOC lines anywhere in the grid.
    dotted = sum(1 for r in rows for c in r if c and _DOTLEAD_RE.search(c))
    if dotted >= 3 or (n_rows and dotted / n_rows >= 0.3):
        return (False, "veto:TOC-leaders")

    # -- header signals ------------------------------------------------------ #
    for kw in _HDR_STRONG:
        if kw in header:
            score += 2
            why.append(kw)
    for kw in _HDR_WEAK:
        if kw in header:
            score += 1
            why.append(kw)
    for rx in _HDR_WORD_RE:
        if rx.search(header):
            score += 1
            why.append(rx.pattern.strip("\\b"))

    # -- content signals ------------------------------------------------------ #
    # Sample the data rows (cap the work on huge tables).
    sample = data_rows[:60]
    cells = [c for r in sample for c in r if c and c.strip()]
    if cells:
        money = sum(1 for c in cells if _MONEY_RE.search(c))
        if money >= 2:
            score += 2
            why.append("χρηματικά ποσά")
        elif money == 1:
            score += 1
            why.append("ποσό")
        if any(_CPV_RE.search(c) for c in cells):
            score += 1
            why.append("κωδικοί")
        numeric = sum(1 for c in cells if _NUMERIC_RE.match(c))
        if numeric / len(cells) >= 0.25:
            score += 1
            why.append("αριθμητικές στήλες")
        # Sequential Α/Α first column: 1, 2, 3… — the signature of a line-item
        # list even when headers are missing (headerless PDF grids).
        firsts = [r[0].strip() for r in sample if r and r[0] and r[0].strip()]
        ints = [int(v) for v in firsts if _INT_RE.match(v)]
        if len(ints) >= 3 and all(b == a + 1 for a, b in zip(ints, ints[1:])):
            score += 2
            why.append("αύξων αριθμός")

        # Prose-shaped grid: long text cells, nothing countable — clause /
        # article layouts. Only counts against when no item signal exists.
        avg_len = sum(len(c) for c in cells) / len(cells)
        if avg_len > 120 and money == 0 and numeric / len(cells) < 0.05:
            score -= 2
            why.append("κείμενο-παράγραφοι")

        # Label:value info sheets — the classic 2-column «ΕΙΔΟΣ ΔΙΑΓΩΝΙΣΜΟΥ:
        # ΑΠΕΥΘΕΙΑΣ ΑΝΑΘΕΣΗ» metadata grid. Header keywords are unreliable there
        # (the "header" is just the first label/value pair), so a 2-col table
        # must earn its keep from a numeric value column; colon-suffixed labels
        # are a further tell.
        if table.get("n_cols") == 2:
            score -= 1
            col_b = [r[1].strip() for r in sample
                     if len(r) > 1 and r[1] and r[1].strip()]
            b_numeric = sum(1 for c in col_b if _NUMERIC_RE.match(c))
            if col_b and b_numeric / len(col_b) < 0.3:
                score -= 3
                why.append("δίστηλο ετικέτες:τιμές")
        firsts_all = [r[0].strip() for r in sample
                      if r and r[0] and r[0].strip()]
        if firsts_all:
            colons = sum(1 for v in firsts_all if v.endswith(":"))
            if colons / len(firsts_all) >= 0.3:
                score -= 3
                why.append("ετικέτες με άνω-κάτω τελεία")

    # -- sign-off / revision negatives ---------------------------------------- #
    # Checked over the whole (small) grid, since these blocks rarely have a
    # meaningful header row.
    if n_rows <= 8:
        all_text = _norm(" | ".join(c for r in rows for c in r if c))
        neg_hits = [kw for kw in _NEG if kw in all_text]
        if neg_hits:
            score -= 3
            why.append("υπογραφές/εκδόσεις")

    return (score >= THRESHOLD, ", ".join(why) or "κανένα σήμα")


def annotate(tables: list) -> int:
    """Classify a batch in place: sets t['relevant'] (bool) and t['rel_why']
    (str) on every table dict. PDF stitch fragments inherit their parent's
    verdict so the details/fragments UI stays consistent with the main table.
    Returns the number of relevant MAIN tables (fragments excluded).

    With TABLE_RELEVANCE=0 everything is marked relevant (today's behavior)."""
    if not enabled():
        for t in tables:
            t["relevant"], t["rel_why"] = True, ""
        return sum(1 for t in tables if t.get("role") != "fragment")

    by_id = {}
    for t in tables:
        if t.get("role") != "fragment":
            rel, why = classify(t)
            t["relevant"], t["rel_why"] = rel, why
            by_id[t["id"]] = rel
    for t in tables:
        if t.get("role") == "fragment":
            # Inherit from the stitched parent; fall back to own classification.
            parent_rel = by_id.get(t.get("group"))
            if parent_rel is None:
                parent_rel, why = classify(t)
                t["rel_why"] = why
            else:
                t["rel_why"] = ""
            t["relevant"] = parent_rel
    return sum(1 for t in tables
               if t.get("role") != "fragment" and t.get("relevant"))
