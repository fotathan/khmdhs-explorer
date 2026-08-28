# -*- coding: utf-8 -*-
"""Match explanation ("Γιατί ταιριάζει") and the occurrence navigator.

Two surfaces, one idea: a result should carry the evidence for its own
inclusion. The chips say WHICH of the user's terms matched and how often; the
occurrence navigator takes them to each hit.

WHERE THE NUMBERS COME FROM — the two paths differ on purpose:

  * DETAIL page. Everything (chip counts, the occurrence list, and the <mark>s
    rendered in the title and full text) comes from ONE scan of that act's
    text, in `scan()`. Self-consistent by construction: the count on a chip is
    literally len() of the list of marks the reader can click. That is the
    acceptance criterion, and it is the reason the detail page does not read
    the tsvector.

  * LIST page. Per-row scanning of full text is not affordable on the 2.7M-row
    path, so the chips there are read straight out of the stored `search_tsv`
    in ONE extra batched query keyed by the ids the result page already
    returned (never a per-row follow-up, and no change to the search query
    itself). Postgres stores at most 255 positions per lexeme, so a count that
    hits the cap is rendered as "255+" rather than a number we cannot stand
    behind.

Stemming is never re-implemented here. Whether a word matches is decided by
Postgres, using the SAME 'greek' configuration that builds `search_tsv` — the
app must not develop a second opinion about what the index did. Only the
character offsets are ours (app.textmatch), because the text-search parser does
not report them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    from app import textmatch as _tm
except ImportError:                      # running from inside app/
    import textmatch as _tm              # type: ignore

fold = _tm.fold

# The text-search configuration. One name, used for the query lexemes and for
# the per-token stemming, and it is the same one search_tsv is generated with.
TS_CONFIG = "greek"

# Occurrences listed in one popover. A term appearing 200 times in an OCR'd PDF
# is not worth 200 DOM nodes; the footer reports the true total.
OCC_CAP = 50

# Chips shown inline on a result row before collapsing to "+N".
LIST_CHIP_CAP = 4

# Postgres stores no more than this many positions per lexeme in a tsvector.
# A list-page count that reaches it is a floor, not a total.
TSV_POSITION_CAP = 255

# tsvector weights -> the section they came from, per the generated column in
# search_combined_tsv_migration.sql: title is A, full text is B.
WEIGHT_SECTIONS = {"A": "title", "B": "full_text"}


# --------------------------------------------------------------------------- #
# Terms
# --------------------------------------------------------------------------- #
@dataclass
class Term:
    """One thing the user asked for, and how to decide whether text matches it.

    `mode` mirrors what the search itself did for this term:
      'lexeme'    — the normal stemmed path; matching is delegated to Postgres.
      'substring' — the search box's trailing-* prefix mode, which bypasses the
                    tsvector and substring-matches raw text, so we do the same.
      'prefix'    — a CPV code, matched as a code prefix.
    """
    display: str
    mode: str = "lexeme"
    kind: str = "keyword"                 # 'keyword' | 'cpv'
    lexemes: list[str] = field(default_factory=list)
    needle: str = ""                      # folded, for substring/prefix modes
    code: str | None = None               # CPV only
    label: str | None = None              # CPV only

    @property
    def slug(self) -> str:
        return _tm.term_slug(self.display)

    def matches(self, token: _tm.Token, stemmed: set[str]) -> bool:
        if self.mode == "lexeme":
            return token.text in stemmed
        if self.mode == "prefix":
            return token.folded.startswith(self.needle)
        return self.needle in token.folded


# Words to ignore when splitting the query: websearch_to_tsquery's operators,
# not things the user is looking for.
_OPERATORS = {"or", "and", "not"}
_QUOTED = re.compile(r'"([^"]*)"|(\S+)')


def parse_terms(raw: str | None) -> list[Term]:
    """Split a raw search box value into the terms worth explaining.

    Negative (-word) terms are dropped: an act is here BECAUSE of the positive
    terms, and a chip for a word that is guaranteed absent explains nothing. A
    quoted phrase stays one term, so "καθαρισμός κτιρίων" gets one chip and not
    two.
    """
    terms: list[Term] = []
    seen: set[str] = set()
    for m in re.finditer(r'(-?)("(?:[^"]*)"|\S+)', raw or ""):
        if m.group(1) == "-":
            continue                      # exclusion: nothing to point at
        tok = m.group(2)
        phrase = tok[1:-1] if tok.startswith('"') and tok.endswith('"') else tok
        phrase = phrase.strip()
        if not phrase or phrase.lower() in _OPERATORS:
            continue
        mode = "lexeme"
        if phrase.endswith("*"):
            phrase = phrase.rstrip("*").strip()
            mode = "substring"
            if not phrase:
                continue
        key = fold(phrase)
        if key in seen:
            continue
        seen.add(key)
        terms.append(Term(display=phrase, mode=mode, needle=key))
    return terms


def resolve_lexemes(cur, terms: list[Term]) -> list[Term]:
    """Fill in `lexemes` for every stemmed term, in one query.

    A term whose lexemes come back empty is a stop word (or pure punctuation):
    the index holds nothing for it, so it never matched anything and it gets no
    chip. Dropping it here is what keeps the panel honest.
    """
    pending = [t for t in terms if t.mode == "lexeme"]
    if not pending:
        return terms
    cur.execute(f"""
        SELECT w.ord, l.lexeme
        FROM   unnest(%s::text[]) WITH ORDINALITY AS w(word, ord)
        LEFT JOIN LATERAL unnest(to_tsvector('{TS_CONFIG}', w.word)) AS l ON true
    """, ([t.display for t in pending],))
    for row in cur.fetchall():
        lex = row["lexeme"] if isinstance(row, dict) else row[1]
        ordinal = row["ord"] if isinstance(row, dict) else row[0]
        if lex:
            pending[ordinal - 1].lexemes.append(lex)
    return [t for t in terms if t.mode != "lexeme" or t.lexemes]


def cpv_terms(cur, values, lang: str = "el") -> list[Term]:
    """Chips for the CPV filter — code prefixes the user selected.

    Rendered with the label next to the code: a bare 33111000 is unreadable,
    and the classification already carries the words for it.
    """
    codes = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not codes:
        return []
    desc = "coalesce(description_en, description)" if lang == "en" else "description"
    cur.execute(f"SELECT cpv_code, {desc} AS label FROM proc.cpv_code "
                f"WHERE cpv_code = ANY(%s)", (codes,))
    labels = {r["cpv_code"]: r["label"] for r in cur.fetchall()}
    return [Term(display=code, mode="prefix", kind="cpv", needle=fold(code),
                 code=code, label=labels.get(code))
            for code in codes]


# --------------------------------------------------------------------------- #
# The scan — the single source of truth for the detail page
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Occurrence:
    slug: str
    section: str                 # 'title' | 'full_text'
    para: int | None             # paragraph number within the full text
    heading: str | None          # detected lot/section heading, if any
    anchor: str                  # element id of the <mark> this points at
    snippet_html: str
    start: int
    end: int


@dataclass
class Section:
    key: str                     # 'title' | 'full_text'
    text: str
    paragraphs: list = field(default_factory=list)


def _stemmed_hits(cur, tokens: list[_tm.Token], terms: list[Term]) -> dict[str, set[str]]:
    """Ask Postgres which surface forms stem to each term's lexemes.

    One query for the whole document: the DISTINCT surface forms go down, the
    matching ones come back. ~15ms over the largest full text in the database,
    and it means a word matches here if and only if it matched in the index.
    """
    lexemes = sorted({lex for t in terms if t.mode == "lexeme" for lex in t.lexemes})
    if not lexemes or not tokens:
        return {}
    surfaces = sorted({t.text for t in tokens})
    cur.execute(f"""
        SELECT t.tok, l.lexeme
        FROM   unnest(%s::text[]) AS t(tok)
        JOIN   LATERAL unnest(to_tsvector('{TS_CONFIG}', t.tok)) AS l ON true
        WHERE  l.lexeme = ANY(%s)
    """, (surfaces, lexemes))
    by_lexeme: dict[str, set[str]] = {}
    for row in cur.fetchall():
        by_lexeme.setdefault(row["lexeme"], set()).add(row["tok"])
    return by_lexeme


def scan(cur, sections: list[Section], terms: list[Term]
         ) -> tuple[dict[str, list[Occurrence]], dict[str, str]]:
    """Find every occurrence of every term across `sections`.

    Returns (occurrences by term slug, highlighted HTML by section key). The
    two are produced together from the same spans, which is the whole point:
    the marks in the HTML and the entries in the list cannot drift apart.
    """
    all_tokens = {s.key: _tm.tokenize(s.text) for s in sections}
    flat = [tok for toks in all_tokens.values() for tok in toks]
    by_lexeme = _stemmed_hits(cur, flat, terms)

    found: dict[str, list[Occurrence]] = {t.slug: [] for t in terms}
    spans: dict[str, list[tuple[int, int, str]]] = {s.key: [] for s in sections}

    for section in sections:
        # Paragraph lookup for labels: paragraphs are ordered and disjoint, so
        # a linear cursor over them costs nothing.
        paras = section.paragraphs
        p_idx = 0
        for token in all_tokens[section.key]:
            for term in terms:
                stemmed = set()
                if term.mode == "lexeme":
                    for lex in term.lexemes:
                        stemmed |= by_lexeme.get(lex, set())
                if not term.matches(token, stemmed):
                    continue
                while p_idx < len(paras) and paras[p_idx].end < token.start:
                    p_idx += 1
                para = paras[p_idx] if p_idx < len(paras) else None
                hits = found[term.slug]
                anchor = f"occ-{term.slug}-{len(hits) + 1}"
                hits.append(Occurrence(
                    slug=term.slug, section=section.key,
                    para=para.index if para else None,
                    heading=para.heading if para else None,
                    anchor=anchor,
                    snippet_html=_tm.snippet(section.text, token.start, token.end),
                    start=token.start, end=token.end))
                spans[section.key].append((token.start, token.end, anchor))
                break          # one mark per token, even if two terms overlap

    html_by_section = {s.key: _tm.mark(s.text, spans[s.key]) for s in sections}
    return found, html_by_section


def act_sections(notice) -> list[Section]:
    """The searchable sections of one act, in the order they are rendered.

    Exactly the fields `search_tsv` is generated from — title (weight A) and
    full text (weight B) — so a chip can never claim a hit in text the index
    never saw, and never miss one it did.
    """
    sections = [Section("title", notice.get("title") or "")]
    full_text = notice.get("full_text") or ""
    if full_text:
        sections.append(Section("full_text", full_text,
                                _tm.split_full_text(full_text)))
    return sections


# --------------------------------------------------------------------------- #
# Detail page
# --------------------------------------------------------------------------- #
@dataclass
class Chip:
    term: str
    slug: str
    kind: str                    # 'keyword' | 'cpv'
    count: int
    sections: dict               # section key -> count
    code: str | None = None
    label: str | None = None
    capped: bool = False         # count is a floor (list page only)


@dataclass
class DetailMatch:
    chips: list[Chip]
    title_html: str | None
    paragraphs: list                # [{index, id, heading, html}] or []
    has_full_text_hits: bool


def detail_match(cur, notice, q: str | None, cpv_values=None, lang: str = "el"
                 ) -> DetailMatch | None:
    """Build the "Γιατί ταιριάζει" panel and the highlighted body for one act.

    Returns None when the visitor arrived without a query — the panel is then
    ABSENT, not empty. Nothing to explain is not a thing worth a heading.
    """
    terms = parse_terms(q)
    if terms:
        terms = resolve_lexemes(cur, terms)
    terms += cpv_terms(cur, cpv_values, lang)
    if not terms:
        return None

    sections = act_sections(notice)
    found, html_by_section = scan(cur, sections, terms)

    chips: list[Chip] = []
    for term in terms:
        hits = found.get(term.slug, [])
        per_section: dict[str, int] = {}
        for occ in hits:
            per_section[occ.section] = per_section.get(occ.section, 0) + 1
        count = len(hits)
        if term.kind == "cpv" and count == 0:
            # It matched the act's classification rather than its prose. That
            # is one real hit; it just has nowhere on the page to point at.
            count = 1
        if not count:
            continue
        chips.append(Chip(term=term.display, slug=term.slug, kind=term.kind,
                          count=count, sections=per_section,
                          code=term.code, label=term.label))
    if not chips:
        return None

    # Render the full text one paragraph at a time, marking each from the SAME
    # spans the occurrence list was built from. Offsets line up because both
    # come from the one split_full_text() call in act_sections().
    para_html = []
    ft = next((s for s in sections if s.key == "full_text"), None)
    if ft:
        spans = _spans_for(found, "full_text")
        for para in ft.paragraphs:
            local = [(s - para.start, e - para.start, a) for (s, e, a) in spans
                     if para.start <= s and e <= para.end]
            para_html.append({
                "index": para.index, "id": f"ft-p-{para.index}",
                "heading": para.heading,
                "html": _tm.mark(ft.text[para.start:para.end], local),
            })

    return DetailMatch(
        chips=chips,
        title_html=html_by_section.get("title") or None,
        paragraphs=para_html,
        has_full_text_hits=any(c.sections.get("full_text") for c in chips),
    )


def _spans_for(found: dict, section: str) -> list[tuple[int, int, str]]:
    return sorted((o.start, o.end, o.anchor)
                  for hits in found.values() for o in hits if o.section == section)


def occurrences_for(cur, notice, term_display: str, lang: str = "el"
                    ) -> tuple[list[Occurrence], int]:
    """Every occurrence of ONE term in one act, capped for display.

    Re-runs the same scan the page render used, so the anchors it returns are
    the ids that exist in the DOM.
    """
    is_cpv = bool(re.fullmatch(r"\d{2,8}", term_display or ""))
    if is_cpv:
        terms = cpv_terms(cur, [term_display], lang)
    else:
        terms = resolve_lexemes(cur, parse_terms(term_display))
    if not terms:
        return [], 0
    found, _ = scan(cur, act_sections(notice), terms)
    hits = found.get(terms[0].slug, [])
    return hits[:OCC_CAP], len(hits)


# --------------------------------------------------------------------------- #
# List page — chips from the stored tsvector, one batched query
# --------------------------------------------------------------------------- #
def list_chips(cur, adams: list[str], q: str | None, cpv_values=None,
               lang: str = "el") -> dict[str, list[Chip]]:
    """Match chips for a page of results, keyed by ADAM.

    ONE query for the whole page — never one per row — reading the tsvector
    that is already stored on each row. The search query itself is untouched.
    """
    if not adams:
        return {}
    terms = resolve_lexemes(cur, parse_terms(q))
    stemmed = [t for t in terms if t.mode == "lexeme"]
    out: dict[str, list[Chip]] = {}

    if stemmed:
        lex_to_term = {lex: t for t in stemmed for lex in t.lexemes}
        cur.execute("""
            SELECT a.adam, l.lexeme, l.positions, l.weights
            FROM   proc.procurement_act a,
                   LATERAL unnest(a.search_tsv) AS l
            WHERE  a.adam = ANY(%s)
              AND  l.lexeme = ANY(%s)
        """, (adams, sorted(lex_to_term)))
        # slug -> (count, per-section counts, capped) per act
        acc: dict[str, dict[str, list]] = {}
        for row in cur.fetchall():
            term = lex_to_term.get(row["lexeme"])
            if term is None:
                continue
            positions = row["positions"] or []
            weights = row["weights"] or ""
            per_act = acc.setdefault(row["adam"], {})
            entry = per_act.setdefault(term.slug, [0, {}, False])
            entry[0] += len(positions)
            if len(positions) >= TSV_POSITION_CAP:
                entry[2] = True
            for w in weights:
                section = WEIGHT_SECTIONS.get(w)
                if section:
                    entry[1][section] = entry[1].get(section, 0) + 1
        for adam, per_term in acc.items():
            chips = []
            for term in stemmed:
                got = per_term.get(term.slug)
                if not got:
                    continue
                chips.append(Chip(term=term.display, slug=term.slug,
                                  kind="keyword", count=got[0],
                                  sections=got[1], capped=got[2]))
            if chips:
                out[adam] = chips

    # Substring (trailing-*) terms bypass the tsvector entirely, exactly as the
    # search does; the title is short enough to scan for every row on the page.
    literal = [t for t in terms if t.mode == "substring"]
    literal += cpv_terms(cur, cpv_values, lang)
    if literal:
        cur.execute("SELECT adam, title FROM proc.procurement_act WHERE adam = ANY(%s)",
                    (adams,))
        for row in cur.fetchall():
            tokens = _tm.tokenize(row["title"] or "")
            chips = out.setdefault(row["adam"], [])
            for term in literal:
                n = sum(1 for tok in tokens if term.matches(tok, set()))
                if term.kind == "cpv" and n == 0:
                    n = 1                      # matched the classification
                if n:
                    chips.append(Chip(term=term.display, slug=term.slug,
                                      kind=term.kind, count=n,
                                      sections={"title": n},
                                      code=term.code, label=term.label))
            if not chips:
                out.pop(row["adam"], None)
    return out
