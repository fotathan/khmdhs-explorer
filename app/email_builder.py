"""
email_builder.py — merge pasted text into a stored email template.

A port of the standalone Multilingual-HTML-Template-Builder (React + one Vercel
function): `shared/splitBlocks.ts` and `api/_lib/htmlMerge.ts`. The behaviour is
meant to match that tool's, so a body composed there and one composed here come
out the same. Where it deliberately does not, the comment says DIVERGENCE.

Pure logic — no DB, no FastAPI, no network, no disk. app/crm.py owns the HTTP
surface and app/auth.py the storage, so this module is testable without a
database and the merge rules can be exercised in isolation.

The pipeline:

    segment_text()         pasted text  -> prose / list segments
    merge_into_template()  segments     -> the template's <p> / <ul> slots
    resolve_fields()       [[field]]    -> the customer's own values
    to_plain_text()        merged HTML  -> the text/plain alternative

Two markup conventions travel with a template body:

  @@token    protects a paragraph. It is never filled, never removed, and
             consumes no pasted block — this is how a template's salutation and
             sign-off survive without the merge needing to special-case them.
  [[field]]  a merge field, replaced with a value from the customer's profile.
             It protects its paragraph as well, for the same reason @@token
             does. Deliberately not {{ }}, which would collide with Jinja2 if a
             stored body ever passed through a template render.

`to_plain_text` re-parses the MERGED html rather than the input, so protected
content appears in the plain-text alternative exactly as it will send.
"""
from __future__ import annotations

import copy
import html as _html
import re
from dataclasses import dataclass, replace

import lxml.html
import nh3

# Hard cap on pasted length, mirroring the original tool's MAX_SOURCE_CHARS.
MAX_SOURCE_CHARS = 50_000

# Inline tags an author may type into the paste box.
ALLOWED_TAGS = {"strong", "b", "em", "i", "u", "br", "a", "span"}
ALLOWED_ATTRIBUTES = {"a": {"href"}}
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

# Protects a paragraph: @@SalutationFixSub, @@ts4_footer, ...
MERGE_TOKEN = re.compile(r"@@[A-Za-z0-9_]+")

# A merge field: [[full_name]], [[company]], ...
FIELD_TOKEN = re.compile(r"\[\[([a-z_][a-z0-9_]*)\]\]")

_BULLET_LINE = re.compile(r"^\s*[-*]\s+\S")
_BULLET_MARKER = re.compile(r"^\s*[-*]\s+")
# Runs of horizontal space, including the non-breaking space templates are full of.
_HSPACE = re.compile(r"[ \t ]+")


class UnresolvedFieldsError(ValueError):
    """A [[field]] had no value. Raised rather than substituting an empty string:
    a half-filled greeting is worse than a refusal, and the CRM panel surfaces
    `fields` so the admin can fill the profile in and retry."""

    def __init__(self, fields):
        self.fields = list(fields)
        super().__init__("unresolved merge fields: " + ", ".join(self.fields))


@dataclass(frozen=True)
class Segment:
    """One piece of pasted text that the template has a slot for. `kind` is
    'prose' (then `text` holds it) or 'list' (then `items` does)."""

    kind: str
    text: str = ""
    items: tuple[str, ...] = ()


@dataclass(frozen=True)
class MergeResult:
    html: str
    text: str
    filled: int
    created: int
    removed: int


# --------------------------------------------------------------------------- #
# Segmentation — the single source of truth for how text splits into blocks.
# The CRM panel's counters call this too, so the count shown before generating
# is the count generation actually uses.
# --------------------------------------------------------------------------- #

def split_blocks(text: str) -> list[str]:
    """Blank-line-separated blocks, trimmed, empties dropped."""
    normalised = (text or "").replace("\r\n", "\n")
    return [b.strip() for b in re.split(r"\n\s*\n", normalised) if b.strip()]


def _strip_marker(line: str) -> str:
    return _BULLET_MARKER.sub("", line).strip()


def segment_text(text: str) -> list[Segment]:
    """Split into prose paragraphs and bullet lists.

    A blank line always starts a new segment, but so does the boundary between
    bullet and non-bullet lines *inside* a block: writers routinely end a list
    and carry straight on to the next sentence with no blank line, and treating
    that as one paragraph loses the list entirely.
    """
    segments: list[Segment] = []

    for block in split_blocks(text):
        prose: list[str] = []
        items: list[str] = []

        def flush_prose() -> None:
            joined = "\n".join(prose).strip()
            if joined:
                segments.append(Segment("prose", text=joined))
            prose.clear()

        def flush_list() -> None:
            if items:
                segments.append(Segment("list", items=tuple(items)))
            items.clear()

        for line in block.split("\n"):
            if not line.strip():
                continue
            if _BULLET_LINE.match(line):
                flush_prose()
                items.append(_strip_marker(line))
            else:
                flush_list()
                prose.append(line)

        # Only one of these can be non-empty; switching mode flushed the other.
        flush_prose()
        flush_list()

    return segments


def segment_counts(text: str) -> tuple[int, int]:
    """(paragraphs, lists) for the panel's live counters."""
    segments = segment_text(text)
    return (sum(1 for s in segments if s.kind == "prose"),
            sum(1 for s in segments if s.kind == "list"))


# --------------------------------------------------------------------------- #
# Sanitising
# --------------------------------------------------------------------------- #

def render_block(block: str) -> str:
    """Sanitise a pasted block and turn its newlines into <br />.

    DIVERGENCE from the TS original, which escaped everything and then re-enabled
    the allowed tags with a regex, leaving anything else visible as literal text.
    Here nh3 (the Rust `ammonia` binding, already used by app/tables.py) does the
    sanitising, so a disallowed tag is dropped while its text is kept. Trading a
    hand-rolled unescaper for an audited sanitiser is worth the cosmetic
    difference, and it gives us scheme restriction on href for free.
    """
    cleaned = nh3.clean(
        block or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        # Templates are email HTML; injecting rel="noopener noreferrer" into
        # every link would be noise no mail client acts on.
        link_rel=None,
    )
    return cleaned.replace("\n", "<br />")


def render_inline(text: str) -> str:
    """Same, for one list item — an item never spans lines."""
    return nh3.clean(
        text or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel=None,
    )


# --------------------------------------------------------------------------- #
# Fragment plumbing
#
# Templates are HTML *fragments*. lxml.html.document_fromstring would wrap them
# in <html><body>, corrupting every generated body — the same trap the original
# avoided with cheerio's isDocument=false. Everything here parses into a throwaway
# wrapper element and serialises the wrapper's children back out.
# --------------------------------------------------------------------------- #

def _parse_fragment(html: str):
    return lxml.html.fragment_fromstring(html or "", create_parent="div")


def _inner_html(root) -> str:
    parts = [root.text or ""]
    for child in root:
        parts.append(lxml.html.tostring(child, encoding="unicode"))
    return "".join(parts)


def _set_inner_html(el, html_fragment: str) -> None:
    """Replace an element's children while leaving the element itself — and so
    its class / style / align / id — untouched."""
    holder = _parse_fragment(html_fragment)
    for child in list(el):
        el.remove(child)
    el.text = holder.text
    for child in list(holder):
        el.append(child)


def _remove_keeping_tail(el) -> None:
    """Remove an element but not the whitespace that followed it: in lxml a
    node's tail text belongs to the node, so a plain remove() would also eat the
    newline/indent separating it from its next sibling."""
    parent = el.getparent()
    if parent is None:
        return
    if el.tail:
        previous = el.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _is_protected(el) -> bool:
    """A protected element is never filled, never removed, and consumes no
    pasted block.

    DIVERGENCE: the original tested `data-no-translate` on ancestors only, so a
    <p data-no-translate> did not protect itself. Here it does
    (ancestor-or-self), which is what marking that attribute plainly means.
    """
    if el.get("data-keep") is not None:
        return True
    if el.xpath("ancestor-or-self::*[@data-no-translate]"):
        return True
    text = el.text_content()
    # A [[field]] protects its paragraph too. A paragraph that greets the reader
    # by name is a salutation, not a slot: filling it would overwrite the field
    # before resolve_fields ever saw it, silently dropping the personalisation.
    return bool(MERGE_TOKEN.search(text) or FIELD_TOKEN.search(text))


# --------------------------------------------------------------------------- #
# The merge
# --------------------------------------------------------------------------- #

def _fill_list(list_el, items) -> None:
    """Rewrite a list's items, cloning the first <li> so its inline styles carry
    over to every new row."""
    existing = list_el.xpath("./li")
    prototype = existing[0] if existing else lxml.html.fragment_fromstring("<li></li>")

    for child in list(list_el):
        list_el.remove(child)
    list_el.text = None

    for item in items:
        li = copy.deepcopy(prototype)
        _set_inner_html(li, render_inline(item))
        list_el.append(li)


def merge_into_template(text: str, template_html: str) -> MergeResult:
    """Fill the template's paragraphs and lists from the pasted text, in
    document order.

    Surplus blocks clone the last usable element so the new ones inherit its
    styling; surplus template slots are removed outright rather than left as
    empty gaps or stray bullets.
    """
    root = _parse_fragment(template_html)

    paragraphs = [el for el in root.xpath(".//p") if not _is_protected(el)]
    lists = [el for el in root.xpath(".//ul | .//ol") if not _is_protected(el)]

    # With no list in the template, bullet segments fall back to prose so their
    # text is never silently dropped.
    has_lists = bool(lists)
    prose_blocks: list[str] = []
    list_blocks: list[tuple[str, ...]] = []
    for segment in segment_text(text):
        if segment.kind == "prose":
            prose_blocks.append(segment.text)
        elif has_lists:
            list_blocks.append(segment.items)
        else:
            prose_blocks.append("\n".join(f"- {item}" for item in segment.items))

    filled = created = removed = 0

    # ---- paragraphs ---- #
    for block, el in zip(prose_blocks, paragraphs):
        _set_inner_html(el, render_block(block))
        filled += 1

    if len(prose_blocks) > len(paragraphs):
        prototype = paragraphs[-1] if paragraphs else None
        anchor = prototype
        for block in prose_blocks[len(paragraphs):]:
            if prototype is None:
                # No usable paragraph in the template at all — append a bare one.
                bare = lxml.html.fragment_fromstring("<p></p>")
                _set_inner_html(bare, render_block(block))
                root.append(bare)
                created += 1
                continue
            clone = copy.deepcopy(prototype)
            _set_inner_html(clone, render_block(block))
            anchor.addnext(clone)
            anchor = clone
            created += 1
    elif len(prose_blocks) < len(paragraphs):
        for el in paragraphs[len(prose_blocks):]:
            _remove_keeping_tail(el)
            removed += 1

    # ---- lists ---- #
    for items, el in zip(list_blocks, lists):
        _fill_list(el, items)
        filled += 1

    if len(list_blocks) > len(lists):
        prototype = lists[-1]
        anchor = prototype
        for items in list_blocks[len(lists):]:
            clone = copy.deepcopy(prototype)
            _fill_list(clone, items)
            anchor.addnext(clone)
            anchor = clone
            created += 1
    elif len(list_blocks) < len(lists):
        for el in lists[len(list_blocks):]:
            _remove_keeping_tail(el)
            removed += 1

    merged = _inner_html(root)
    return MergeResult(html=merged, text=to_plain_text(merged),
                       filled=filled, created=created, removed=removed)


# --------------------------------------------------------------------------- #
# Merge fields
# --------------------------------------------------------------------------- #

def resolve_fields(html: str, values: dict) -> str:
    """Replace every [[field]] with its value, HTML-escaped.

    Raises UnresolvedFieldsError listing every field that had no usable value,
    so nothing containing a raw [[token]] can reach an outbox.
    """
    missing: list[str] = []

    def substitute(match):
        key = match.group(1)
        value = (values or {}).get(key)
        if value is None or not str(value).strip():
            missing.append(key)
            return match.group(0)
        return _html.escape(str(value), quote=True)

    resolved = FIELD_TOKEN.sub(substitute, html or "")
    if missing:
        raise UnresolvedFieldsError(sorted(set(missing)))
    return resolved


def field_names(html: str) -> list[str]:
    """Every [[field]] a template asks for, in first-seen order — lets the panel
    show what a template needs before anything is generated."""
    seen: list[str] = []
    for match in FIELD_TOKEN.finditer(html or ""):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


# --------------------------------------------------------------------------- #
# Plain-text alternative
# --------------------------------------------------------------------------- #

def _normalise_lines(text: str) -> str:
    lines = [_HSPACE.sub(" ", line).strip() for line in (text or "").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def to_plain_text(html: str) -> str:
    """The text/plain alternative: paragraphs separated by blank lines, list
    items back to '- ' bullets, markup gone.

    Parsed separately from the merge because extracting text mutates the tree,
    and the HTML output must not be affected by it.
    """
    root = _parse_fragment(html)

    for br in root.xpath(".//br"):
        parent = br.getparent()
        tail = br.tail or ""
        previous = br.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + "\n" + tail
        else:
            parent.text = (parent.text or "") + "\n" + tail
        parent.remove(br)

    blocks: list[str] = []
    for el in root.xpath(".//p | .//ul | .//ol"):
        # A list inside a paragraph, or a paragraph inside a list item, would
        # otherwise be emitted twice.
        if el.xpath("ancestor::p | ancestor::li"):
            continue

        if el.tag in ("ul", "ol"):
            items = [_normalise_lines(li.text_content()) for li in el.xpath("./li")]
            items = [item for item in items if item]
            if items:
                blocks.append("\n".join(f"- {item}" for item in items))
            continue

        text = _normalise_lines(el.text_content())
        if text:
            blocks.append(text)

    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# The one call the CRM route makes
# --------------------------------------------------------------------------- #

def build_email(text: str, template_html: str, values: dict = None) -> MergeResult:
    """Merge, resolve the merge fields, and derive the plain-text alternative.

    Raises ValueError if the pasted text is over MAX_SOURCE_CHARS, and
    UnresolvedFieldsError if the result would still contain a [[field]].
    """
    if len(text or "") > MAX_SOURCE_CHARS:
        raise ValueError(f"text exceeds {MAX_SOURCE_CHARS} characters")

    merged = merge_into_template(text, template_html)
    resolved = resolve_fields(merged.html, values or {})
    return replace(merged, html=resolved, text=to_plain_text(resolved))
