"""rich_text.py — one standard for an act's rich full text (full_text_html).

Two very different kinds of HTML end up in that column:

* **What a curator types** in the Quill editor (act form, /tables full-text
  editor). Small, clean, and in Quill's own dialect: `<ol><li data-list=
  "bullet">` for BOTH list kinds, and `<td data-row="…">` for tables.
* **What an importer brings in.** Tender Service's `tenderText` is a copied web
  page, not a document: eProcurement notices average 83k characters of nested
  `<div>`s, ~14k inline `style` attributes, form `<input>`/`<button>` widgets
  and `xsl:value-of` leftovers from the upstream converter. Sanitising that
  alone keeps 87% of it — every empty div survives, because a div is allowed.

So there are two functions, and they are not interchangeable:

`sanitize(html)` — the SECURITY step. Runs on every write, whatever the source.
    An explicit allow-list (not nh3's default): the tags the act page can style
    and the editor can round-trip, plus exactly the three attributes Quill needs
    (a[href], li[data-list], td[data-row]). nh3's default list dropped
    `data-list`, so a bullet list saved from the editor came back numbered, and
    it dropped `data-row`, which is how Quill knows which cells share a row.

`normalise_imported_html(raw)` — the SHAPE step, for importers. Turns page
    markup into paragraphs, headings, lists and tables that look the same on
    every act and load cleanly into Quill, then sanitises.

Quill's table module has two limits the normaliser is written around, and
both are worth knowing before "fixing" it:
  1. A cell holds ONE line. Quill models each line as a cell, so a cell with
     two paragraphs becomes two cells and shifts the row. Cells are flattened
     to a single line of text.
  2. No header cells and no colspan/rowspan. `th` becomes a bold `td`; a
     spanning cell is padded with empty cells so columns still line up.
A table nested in a cell is flattened to text; a one-column table (page
layout, not data) becomes paragraphs.

`html_to_text(html)` derives the plain `full_text` (the search and paragraph
source) from normalised HTML, so the two columns cannot disagree.
"""
from __future__ import annotations

import copy
import re

ALLOWED_TAGS = {
    "p", "br", "h1", "h2", "h3", "h4", "strong", "em", "u", "s",
    "ul", "ol", "li", "a", "blockquote", "pre", "code",
    "table", "tbody", "tr", "td",
}
_ALLOWED_ATTRS = {"a": {"href"}, "li": {"data-list"}, "td": {"data-row"}}
_LIST_KINDS = {"bullet", "ordered", "checked", "unchecked"}
_ROW_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_WS = re.compile(r"[\s ​﻿]+")
_TAGS = re.compile(r"<[^>]+>")

# Removed WITH their content.
_DROP = {
    "script", "style", "noscript", "head", "title", "meta", "link", "input",
    "button", "select", "option", "optgroup", "textarea", "img", "svg",
    "iframe", "object", "embed", "canvas", "video", "audio", "map", "area",
    "template",
}
_RENAME = {
    "b": "strong", "i": "em", "h1": "h2", "h5": "h4", "h6": "h4",
    "strike": "s", "del": "s", "ins": "u", "th": "td",
    "tt": "code", "kbd": "code", "samp": "code",
}
# Structural: start a new paragraph.
_BLOCK = {
    "div", "p", "blockquote", "pre", "dl", "dt", "dd", "section", "article",
    "header", "footer", "main", "nav", "aside", "form", "fieldset", "center",
    "address", "figure", "figcaption", "details", "summary", "caption",
    "thead", "tbody", "tfoot", "tr", "td", "li", "body", "html",
}
_HEADINGS = {"h2", "h3", "h4"}
_BREAKS = _BLOCK | _HEADINGS | {"br", "hr", "table", "ul", "ol"}
_INLINE_KEEP = {"strong", "em", "u", "s", "a", "code"}
_MAX_COLSPAN = 20


def _squash(s: str | None) -> str:
    return _WS.sub(" ", s or "").strip()


def _attr_filter(tag: str, attr: str, value: str):
    if tag == "li" and attr == "data-list":
        return value if value in _LIST_KINDS else None
    if tag == "td" and attr == "data-row":
        return value if _ROW_ID.match(value or "") else None
    return value


def sanitize(raw: str | None) -> str | None:
    """Security allow-list for anything written to full_text_html.

    Returns None for empty input, for markup with no visible content (Quill
    serialises an empty editor as '<p><br></p>'), and when nh3 is missing —
    unsanitised HTML is never stored; the plain text still is."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        import nh3
    except ImportError:
        return None
    cleaned = nh3.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        attribute_filter=_attr_filter,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    ).strip()
    if "<table" not in cleaned and not _squash(_TAGS.sub(" ", cleaned).replace("&nbsp;", " ")):
        return None
    return cleaned or None


# --------------------------------------------------------------------------- #
# import normalisation
# --------------------------------------------------------------------------- #
def _remove_keep_tail(el) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    prev = el.getprevious()
    parent.remove(el)
    if tail:
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


def _replace_with_text(el, text: str) -> None:
    el.tail = (" " + text + " " + (el.tail or "")) if text else el.tail
    _remove_keep_tail(el)


def _text(el) -> str:
    """text_content() with a space wherever a block or <br> separates words.

    lxml's text_content() simply concatenates, so '<p>Γραμμή α</p><p>συνέχεια</p>'
    reads 'Γραμμή ασυνέχεια' — found on real cells. Inline tags add nothing, so
    'Γρα<b>μμή</b>' stays one word."""
    parts: list[str] = []

    def walk(node):
        brk = isinstance(node.tag, str) and node.tag in _BREAKS
        if brk:
            parts.append(" ")
        if node.text:
            parts.append(node.text)
        for ch in node:
            walk(ch)
            if ch.tail:
                parts.append(ch.tail)
        if brk:
            parts.append(" ")

    walk(el)
    return _squash("".join(parts))


def _normalise_table(tbl, make) -> None:
    rows = [tr for tr in tbl.iter("tr") if next(tr.iterancestors("table"), None) is tbl]
    grid: list[list[tuple[str, bool]]] = []
    for tr in rows:
        cells: list[tuple[str, bool]] = []
        for td in tr:
            if not isinstance(td.tag, str) or td.tag != "td":
                continue
            try:
                span = max(1, min(_MAX_COLSPAN, int(td.get("colspan") or 1)))
            except ValueError:
                span = 1
            cells.append((_text(td), td.get("data-th") == "1"))
            cells.extend([("", False)] * (span - 1))
        if any(text for text, _ in cells):
            grid.append(cells)

    nested = any(a.tag == "td" for a in tbl.iterancestors())
    if nested or not grid:
        _replace_with_text(tbl, " ; ".join(" · ".join(t for t, _ in r if t) for r in grid))
        return

    cols = max(len(r) for r in grid)
    parent = tbl.getparent()
    index = parent.index(tbl)
    if cols == 1:
        # Page layout, not data: one paragraph per row.
        for i, r in enumerate(grid):
            p = make("p")
            p.text = r[0][0]
            parent.insert(index + i, p)
        last = parent[index + len(grid) - 1]
        last.tail = tbl.tail
        parent.remove(tbl)
        return

    new = make("table")
    body = make("tbody")
    new.append(body)
    for i, r in enumerate(grid, 1):
        tr = make("tr")
        body.append(tr)
        for text, header in r + [("", False)] * (cols - len(r)):
            td = make("td")
            td.set("data-row", f"r{i}")
            if header and text:
                strong = make("strong")
                strong.text = text
                td.append(strong)
            else:
                td.text = text or None
            tr.append(td)
    new.tail = tbl.tail
    parent.replace(tbl, new)


class _Flow:
    """Collects inline content into paragraphs and emits finished blocks."""

    def __init__(self, make):
        self.make = make
        self.blocks = []
        self.cur = None

    def text(self, s: str | None) -> None:
        s = _WS.sub(" ", s or "")
        if not s or (self.cur is None and not s.strip()):
            return
        p = self._para()
        if len(p):
            p[-1].tail = (p[-1].tail or "") + s
        else:
            p.text = (p.text or "") + s

    def inline(self, el) -> None:
        if _squash(el.text_content()):
            self._para().append(el)
        elif el.tail:
            self.text(el.tail)

    def close(self) -> None:
        p, self.cur = self.cur, None
        if p is None or not _squash(p.text_content()):
            return
        if p.text:
            p.text = p.text.lstrip()
        if len(p):
            if p[-1].tail:
                p[-1].tail = p[-1].tail.rstrip()
        elif p.text:
            p.text = p.text.rstrip()
        self.blocks.append(p)

    def block(self, el) -> None:
        self.close()
        self.blocks.append(el)

    def _para(self):
        if self.cur is None:
            self.cur = self.make("p")
        return self.cur


def _has_block(el) -> bool:
    return any(isinstance(d.tag, str) and (d.tag in _BLOCK or d.tag in _HEADINGS
                                            or d.tag in ("table", "ul", "ol", "br"))
               for d in el.iterdescendants())


def _clean_inline(el):
    out = copy.deepcopy(el)
    out.tail = None
    for d in list(out.iterdescendants()):
        if isinstance(d.tag, str) and d.tag not in _INLINE_KEEP:
            d.drop_tag()
    return out


def _walk(el, flow: _Flow) -> None:
    flow.text(el.text)
    for child in list(el):
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag in ("br", "hr"):
            flow.close()
        elif tag == "table":
            t = copy.deepcopy(child)
            t.tail = None
            flow.block(t)
        elif tag in _HEADINGS:
            text = _text(child)
            if text:
                h = flow.make(tag)
                h.text = text
                flow.block(h)
        elif tag in ("ul", "ol"):
            items = [_text(li) for li in child.iter("li")]
            items = [x for x in items if x]
            if items:
                lst = flow.make(tag)
                for x in items:
                    li = flow.make("li")
                    li.text = x
                    lst.append(li)
                flow.block(lst)
        elif tag in _INLINE_KEEP and not _has_block(child):
            flow.inline(_clean_inline(child))
        elif tag in _BLOCK:
            flow.close()
            _walk(child, flow)
            flow.close()
        elif tag:
            _walk(child, flow)          # span, font, label, small… — unwrap
        flow.text(child.tail)


def normalise_imported_html(raw: str | None) -> str | None:
    """Imported page markup → standard, sanitised, Quill-loadable HTML."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        import lxml.html
        from lxml import etree
    except ImportError:
        return sanitize(raw)
    try:
        root = lxml.html.fragment_fromstring(raw, create_parent="div")
    except (etree.ParserError, ValueError):
        return sanitize(raw)
    make = root.makeelement

    for el in list(root.iter()):
        if el is root:
            continue
        if not isinstance(el.tag, str):                  # comments, PIs
            _remove_keep_tail(el)
            continue
        tag = el.tag.lower()
        if ":" in tag or tag in _DROP:
            _remove_keep_tail(el)
            continue
        href = el.get("href") if tag == "a" else None
        colspan = el.get("colspan") if tag in ("td", "th") else None
        el.attrib.clear()
        if href:
            el.set("href", href)
        if colspan:
            el.set("colspan", colspan)
        if tag == "th":
            el.set("data-th", "1")
        el.tag = _RENAME.get(tag, tag)

    for tbl in reversed(list(root.iter("table"))):       # innermost first
        if tbl.getparent() is not None:
            _normalise_table(tbl, make)

    flow = _Flow(make)
    _walk(root, flow)
    flow.close()
    html = "".join(lxml.html.tostring(b, encoding="unicode") for b in flow.blocks)
    return sanitize(html)


def html_to_text(html: str | None) -> str:
    """Plain text for full_text: one block per paragraph, table rows as
    'cell | cell' lines, list items as '• item'."""
    if not html:
        return ""
    import lxml.html
    root = lxml.html.fragment_fromstring(html, create_parent="div")
    parts = []
    if _squash(root.text):
        parts.append(_squash(root.text))
    for el in root:
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag == "table":
            rows = []
            for tr in el.iter("tr"):
                cells = [_squash(td.text_content()) for td in tr if td.tag in ("td", "th")]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                parts.append("\n".join(rows))
        elif tag in ("ul", "ol"):
            items = [_squash(li.text_content()) for li in el.iter("li")]
            if any(items):
                parts.append("\n".join(f"• {x}" for x in items if x))
        else:
            text = _squash(el.text_content())
            if text:
                parts.append(text)
        if _squash(el.tail):
            parts.append(_squash(el.tail))
    return "\n\n".join(parts)


def import_full_text(raw: str | None) -> tuple[str | None, str | None]:
    """(full_text_html, full_text) for an imported document's HTML."""
    html = normalise_imported_html(raw)
    if html:
        return html, (html_to_text(html) or None)
    if not (raw or "").strip():
        return None, None
    return None, (_squash(_TAGS.sub(" ", raw)) or None)
