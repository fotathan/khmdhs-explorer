# -*- coding: utf-8 -*-
"""ai_summary.py — structured, cached comprehension of a `notice`.

Spec: docs/specs/ai-summary.md. Read §3, §4 and §8 before changing anything
here; the three rules they set are what make this shippable rather than a
plausible-sounding guess machine bolted to an act page.

    §3  EXTRACTION, NOT EVALUATION. This module answers "what does this notice
        require", never "can THIS customer meet it". The first is a property of
        the act, identical for every reader, and therefore cacheable forever.
        The second is a property of the reader and belongs nowhere near a shared
        cache. Nothing here may ever read a customer profile.

    §4  THE RECORD BEATS THE MODEL. Fields the feed already carries — the
        submission deadline, the budget, the award-criteria type, the lots, the
        CPVs — are given to the model as reading context and are NOT in the
        output schema. When a value comes back that restates one, it is dropped;
        when it contradicts one, it is dropped and recorded in `conflicts` for an
        admin. A bidder is never shown a model's opinion about a column.

    §8  NOTHING IS TRUSTED ON THE MODEL'S AUTHORITY. Every extracted item must
        carry a `quote` that is verbatim in the source it names. The quote is
        located here, in Python, through the same folding search uses
        (app/textmatch.py); an item whose quote cannot be found is DROPPED, not
        flagged, not shown greyed out. That single gate is the hallucination
        filter, the source of the character offset, and what lets the panel link
        each item to the sentence that produced it.

Transport is raw urllib with x-api-key / anthropic-version, matching app/ocr.py
and app/call_summary.py — one house convention for calling Claude, no SDK
dependency. Configuration (env):

    ANTHROPIC_API_KEY           required; absent → generation impossible
    AI_SUMMARY_ENABLED          default OFF (this one spends money)
    AI_SUMMARY_MODEL            default "claude-opus-5"
    AI_SUMMARY_EFFORT           default "medium" (thinking tokens bill at the
                                OUTPUT rate; see the note on EFFORT below)
    AI_SUMMARY_MAX_TOKENS       default 16000
    AI_SUMMARY_MAX_INPUT_CHARS  default 120000
    AI_SUMMARY_TIMEOUT          default 180 (seconds BETWEEN stream events,
                                not for the whole call — see _stream)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.request

try:
    from app import textmatch as _tm
except ImportError:                       # flat layout (run with --app-dir=app)
    import textmatch as _tm               # type: ignore

# macOS Pythons often have no CA bundle wired into the default SSL context.
# Same belt-and-braces as ocr.py: system defaults, plus certifi on top.
_SSL_CTX = ssl.create_default_context()
try:
    import certifi
    _SSL_CTX.load_verify_locations(cafile=certifi.where())
except Exception:                         # pragma: no cover — certifi missing
    pass

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

MODEL = os.environ.get("AI_SUMMARY_MODEL", "claude-opus-5")
# Thinking tokens are billed as OUTPUT ($25/MTok on Opus 5), and a median Greek
# notice is only ~5,200 input tokens — so on a typical act the reasoning costs
# more than the document. This is "find the clause and copy it out" against a
# strict schema, not open-ended reasoning, so "high" is hard to justify. Start at
# medium and let the measurements argue for a change: act_ai_summary records
# output_tokens per act, and rejected_n says whether quality moved with it.
EFFORT = os.environ.get("AI_SUMMARY_EFFORT", "medium")
# The OUTPUT cap, and it is not a spend control: you are billed for the tokens
# generated, never for the ceiling. A low cap therefore buys nothing and can
# lose everything — when a reply runs past it the tool call is cut off
# mid-JSON, the whole generation is discarded, and the tokens are still
# charged. 16000 did exactly that on the largest notices in the corpus (a
# 68k-character act with 53 items needed ~14k, and one act exceeded it
# outright). Streaming is already on, which is what makes a large ceiling safe.
MAX_TOKENS = int(os.environ.get("AI_SUMMARY_MAX_TOKENS", "32000"))
MAX_INPUT_CHARS = int(os.environ.get("AI_SUMMARY_MAX_INPUT_CHARS", "120000"))
TIMEOUT = float(os.environ.get("AI_SUMMARY_TIMEOUT", "180"))
DAILY_CAP = int(os.environ.get("AI_SUMMARY_DAILY_CAP", "50"))

# Bump PROMPT_VERSION when the wording below changes, SCHEMA_VERSION when the
# catalogue or the item envelope changes. BOTH are in the cache key, so a bump
# invalidates every stored payload — which is the point: a payload produced by
# different instructions is a different answer, and serving it as though nothing
# changed is how a prompt regression goes unnoticed for a month.
PROMPT_VERSION = 1
SCHEMA_VERSION = 1

# Published API prices, USD per million tokens. A local cache, like every other
# price list in this repo — check console.anthropic.com/pricing when adding a
# model. An unknown model records no cost rather than guessing one.
PRICES_USD_PER_MTOK = {
    "claude-opus-5":   (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


class SummaryError(RuntimeError):
    """Raised when a summary cannot be produced (config, input or API error)."""


# --------------------------------------------------------------------------- #
# Switches
# --------------------------------------------------------------------------- #
# An AFFIRMATIVE vocabulary, unlike login_links._OFF_VALUES. That switch defaults
# to ON and therefore needs every spelling of "no" to be recognised, or a
# dashboard-typed "false" silently leaves the feature running. This one defaults
# to OFF and spends money when on, so the safe direction is already the default:
# anything not recognisably "yes" — including a typo — is off.
_ON_VALUES = frozenset({"1", "true", "yes", "on", "y", "t", "enabled"})


def enabled() -> bool:
    """Is the AI summary offered at all? Default OFF; unrecognised values OFF."""
    return (os.environ.get("AI_SUMMARY_ENABLED") or "").strip().lower() in _ON_VALUES


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def can_generate() -> bool:
    """Cached summaries still render without a key — only generation stops."""
    return enabled() and api_key_present()


# --------------------------------------------------------------------------- #
# §5 — the section catalogue
#
# A CLOSED catalogue with a fixed item envelope. "Dynamic structure" means the
# model emits only the sections it found evidence for; it does not mean the model
# invents section names, because a name the template has never seen cannot be
# rendered and cannot be tested. Adding a section is a change here plus a heading
# string, and a SCHEMA_VERSION bump.
# --------------------------------------------------------------------------- #
SECTIONS = (
    ("timeline",     "Χρονοδιάγραμμα",      "Πότε πρέπει να ενεργήσω;",
     "Dates and deadlines OTHER THAN the submission deadline: deadline for "
     "questions, clarification rounds, site visits, opening of offers, and any "
     "milestone the bidder must diarise."),
    ("award",        "Κριτήρια ανάθεσης",   "Πώς βαθμολογείται;",
     "The sub-criteria behind the award and their WEIGHTS (percentages or "
     "points), scoring formulas, and how price and technical merit combine."),
    ("pricing",      "Οικονομικοί όροι",    "Τι κοστίζει να συμμετάσχω;",
     "Bid security (εγγύηση συμμετοχής), performance guarantee (εγγύηση καλής "
     "εκτέλεσης), payment terms, retentions, price adjustment, per-lot values "
     "the record does not already carry."),
    ("requirements", "Τεχνικές απαιτήσεις", "Μπορώ να τις καλύψω;",
     "Technical specifications, equipment and quantities, materials, delivery "
     "and response times, warranty terms, standards and certificates."),
    ("eligibility",  "Κριτήρια συμμετοχής", "Ποιος μπορεί να συμμετάσχει;",
     "What a bidder must BE or HOLD: registrations, licences, ISO and other "
     "certificates, minimum turnover, minimum years of experience, reference "
     "projects, personnel qualifications."),
    ("submission",   "Υποβολή προσφοράς",   "Πώς υποβάλλω;",
     "The mechanics: platform and ΕΣΗΔΗΣ system number, electronic or sealed, "
     "language, required forms such as the ΕΕΕΣ/ESPD, signing and stamping "
     "requirements, how offers must be structured into files."),
    ("attention",    "Σημεία προσοχής",     "Τι πρέπει να προσέξω;",
     "Clauses a bidder would regret missing: penalties, liquidated damages, "
     "subcontracting limits, exclusivity, unusual liabilities, options and "
     "extensions."),
)

SECTION_KEYS = tuple(k for k, _h, _q, _d in SECTIONS)
SECTION_HEADINGS = {k: (h, q) for k, h, q, _d in SECTIONS}

OBLIGATIONS = ("mandatory", "desirable")
CONFIDENCES = ("high", "medium", "low")


# --------------------------------------------------------------------------- #
# §4 — fields the record owns
#
# Two jobs. As `_CONTEXT_FIELDS` they are handed to the model so it reads the
# document with the budget and the deadline already in hand. As `_OWNED` they
# are what an emitted item is checked against: a restatement is noise, and a
# contradiction is an ingestion signal for an admin — neither belongs on a
# bidder's screen.
# --------------------------------------------------------------------------- #
_CONTEXT_FIELDS = (
    "adam", "title", "type", "notice_type_code", "contract_type_code",
    "procedure_type_code", "criteria_code", "final_submission_date",
    "signed_date", "published_eu_date", "budget", "total_cost_without_vat",
    "total_cost_with_vat", "currency_code", "number_of_sections",
    "contract_duration", "contract_duration_unit", "offers_valid_time",
    "offers_valid_time_unit", "bidding_website",
)

# folded label fragments → the record field an item would be restating.
_OWNED_DATE_LABELS = {
    "final_submission_date": (
        "καταληκτικη ημερομηνια", "προθεσμια υποβολ", "ημερομηνια υποβολ",
        "λητη προθεσμια", "submission deadline", "closing date",
    ),
}
_OWNED_AMOUNT_LABELS = {
    "budget": ("προυπολογισμ", "budget"),
    "total_cost_without_vat": (
        "εκτιμωμενη αξια", "εκτιμωμενης αξιας", "estimated value",
        "συνολικη αξια", "contract value",
    ),
}

_DATE_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
# Greek and English amount forms: 1.234.567,89 / 1,234,567.89 / 50000
_AMOUNT_RE = re.compile(r"\d[\d.,\s]{2,}\d|\b\d+\b")


# --------------------------------------------------------------------------- #
# Source assembly and the cache key
# --------------------------------------------------------------------------- #
def build_sources(act: dict, tables: list[dict] | None = None) -> dict[str, str]:
    """The texts the model may quote, keyed by the label it must cite.

    Keys are the `source` discriminator the model echoes back: "full_text" and
    "table:<id>". Attachments would be "attachment:<id>" — the shape is already
    here, but proc.act_attachment is local-only (ATTACHMENTS_ENABLED), so
    nothing populates it in production. See spec §6.
    """
    sources: dict[str, str] = {}
    full_text = (act.get("full_text") or "").strip()
    if full_text:
        sources["full_text"] = full_text
    for t in tables or []:
        rows = t.get("rows") or []
        if not rows:
            continue
        body = "\n".join("\t".join("" if c is None else str(c) for c in row)
                         for row in rows)
        locator = t.get("locator") or ""
        sources[f"table:{t['id']}"] = f"{locator}\n{body}".strip()
    return sources


def input_hash(sources: dict[str, str], *, model: str = None, lang: str = "el") -> str:
    """The whole cache design in one value — see the migration's header.

    Covers every byte the model will read, plus the prompt, the schema, the model
    and the language. Equal hash ⇒ the stored payload answers exactly this
    question, including the character offsets, which point into this text.
    """
    h = hashlib.sha256()
    for key in sorted(sources):
        h.update(key.encode("utf-8"))
        h.update(b"\x00")
        h.update(sources[key].encode("utf-8"))
        h.update(b"\x00")
    h.update(f"p{PROMPT_VERSION}|s{SCHEMA_VERSION}|{model or MODEL}|{lang}".encode())
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# §8 — the quote gate
#
# textmatch.fold is length-preserving by construction, so folding alone never
# moves an offset. Whitespace is the one thing that must collapse — a model
# copying a quote across a line break writes a single space where the document
# has "\n      " — and collapsing DOES move offsets, so the projection carries
# its own inverse map rather than losing them.
# --------------------------------------------------------------------------- #
# Typographic characters a model normalises without being asked. Every mapping is
# one character to one character, so folding stays length-preserving.
_PUNCT = str.maketrans({
    "«": '"', "»": '"',                    # « »
    "“": '"', "”": '"', "„": '"',     # “ ” „
    "‘": "'", "’": "'", "‚": "'",     # ‘ ’ ‚
    "–": "-", "—": "-", "−": "-",     # – — −
    " ": " ",                                   # nbsp
})


def _project(text: str) -> tuple[str, list[int]]:
    """Folded, whitespace-collapsed form of `text`, plus a map back to it.

    Returns (projected, index_map) where index_map[i] is the offset in `text`
    of projected[i]. Runs of whitespace become one space mapped to the run's
    first character.
    """
    folded = _tm.fold(text).translate(_PUNCT)   # same length as `text`
    out: list[str] = []
    idx: list[int] = []
    i, n = 0, len(folded)
    while i < n:
        ch = folded[i]
        if ch.isspace():
            out.append(" ")
            idx.append(i)
            while i < n and folded[i].isspace():
                i += 1
        else:
            out.append(ch)
            idx.append(i)
            i += 1
    return "".join(out), idx


def locate(haystack: str, needle: str) -> tuple[int, int] | None:
    """Offsets of `needle` in `haystack`, ignoring case, accents and whitespace.

    Returns (start, end) into the ORIGINAL `haystack`, or None when the quote is
    not there — which is the whole point of the gate: None means the model
    produced words the document does not contain, and the item goes away.
    """
    if not haystack or not needle:
        return None
    proj_h, idx_h = _project(haystack)
    proj_n, _ = _project(needle)
    proj_n = proj_n.strip()
    if not proj_n:
        return None
    at = proj_h.find(proj_n)
    if at < 0:
        return None
    start = idx_h[at]
    last = idx_h[at + len(proj_n) - 1]
    # `last` is the first character of the final matched run; the run is a single
    # non-space character because the needle was stripped.
    return start, last + 1


# --------------------------------------------------------------------------- #
# §4 — reconciliation against the record
# --------------------------------------------------------------------------- #
def _dates_in(text: str) -> set[str]:
    """ISO dates mentioned in `text`, day-first (Greek documents are dd/mm/yyyy)."""
    found = set()
    for m in _DATE_RE.finditer(text or ""):
        if m.group(1):
            d, mo, y = m.group(1), m.group(2), m.group(3)
        else:
            y, mo, d = m.group(4), m.group(5), m.group(6)
        try:
            found.add(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}")
        except (TypeError, ValueError):    # pragma: no cover — regex-guarded
            continue
    return found


def _amounts_in(text: str) -> set[int]:
    """Whole-euro amounts mentioned in `text`, cents discarded.

    Both 1.234.567,89 and 1,234,567.89 appear in these documents. The rule that
    resolves them: the LAST separator is a decimal point only when exactly two
    digits follow it; everything else is a thousands separator.
    """
    out = set()
    for m in _AMOUNT_RE.finditer(text or ""):
        raw = m.group(0).replace(" ", "")
        if not raw or not raw[0].isdigit():
            continue
        whole = raw
        for sep in (",", "."):
            tail = raw.rsplit(sep, 1)
            if len(tail) == 2 and len(tail[1]) == 2 and tail[1].isdigit():
                whole = tail[0]
                break
        digits = re.sub(r"\D", "", whole)
        if digits:
            out.add(int(digits))
    return out


def _record_dates(record: dict) -> dict[str, str]:
    out = {}
    for f in ("final_submission_date",):
        v = record.get(f)
        if v is not None:
            out[f] = str(v)[:10]
    return out


def _record_amounts(record: dict) -> dict[str, int]:
    out = {}
    for f in ("budget", "total_cost_without_vat", "total_cost_with_vat"):
        v = record.get(f)
        if v is not None:
            try:
                out[f] = int(float(v))
            except (TypeError, ValueError):     # pragma: no cover
                continue
    return out


def _reconcile(item: dict, record: dict) -> tuple[str | None, dict | None]:
    """Classify one item against the record. Returns (verdict, conflict).

    verdict is None to keep the item, "duplicate" when it restates a value the
    record already shows the reader, or "conflict" when it contradicts one. A
    conflict also returns the row to record for an admin; a duplicate does not,
    because agreeing with the record is not news.
    """
    label = _tm.fold(item.get("label") or "")
    value = item.get("value") or ""
    rec_dates, rec_amounts = _record_dates(record), _record_amounts(record)

    item_dates = _dates_in(value)
    if item_dates:
        for field, patterns in _OWNED_DATE_LABELS.items():
            if field not in rec_dates or not any(p in label for p in patterns):
                continue
            if rec_dates[field] in item_dates:
                return "duplicate", None
            return "conflict", {"field": field, "record": rec_dates[field],
                                "model": sorted(item_dates)[0],
                                "label": item.get("label"), "quote": item.get("quote")}
        # An unlabelled restatement of the deadline is still a restatement.
        if any(d in item_dates for d in rec_dates.values()):
            return "duplicate", None

    item_amounts = _amounts_in(value)
    if item_amounts:
        for field, patterns in _OWNED_AMOUNT_LABELS.items():
            if field not in rec_amounts or not any(p in label for p in patterns):
                continue
            if rec_amounts[field] in item_amounts:
                return "duplicate", None
            return "conflict", {"field": field, "record": rec_amounts[field],
                                "model": sorted(item_amounts)[-1],
                                "label": item.get("label"), "quote": item.get("quote")}
    return None, None


# --------------------------------------------------------------------------- #
# Validation — the model's output has to survive this before anyone sees it
# --------------------------------------------------------------------------- #
def verify(raw: dict, sources: dict[str, str], record: dict) -> dict:
    """Turn a model payload into a payload the panel may render.

    Every item is dropped unless it (a) has the required fields, (b) names a
    source that exists, (c) quotes that source verbatim, and (d) does not
    restate or contradict the record. Survivors gain `start`/`end` offsets into
    their source. Nothing is repaired: an item that fails is gone, and the count
    of dropped items is part of the payload, because a rising rejection rate is
    how a prompt regression announces itself.
    """
    if not isinstance(raw, dict):        # a reply shaped like nothing we asked for
        raw = {}
    sections, conflicts = [], []
    rejected = duplicates = 0

    for key in SECTION_KEYS:                       # catalogue order, not model order
        items_in = raw.get(key)
        if not isinstance(items_in, list):
            continue
        kept = []
        for item in items_in:
            if not isinstance(item, dict):
                rejected += 1
                continue
            label = (item.get("label") or "").strip()
            value = (item.get("value") or "").strip()
            quote = (item.get("quote") or "").strip()
            source = (item.get("source") or "").strip()
            if not (label and value and quote):
                rejected += 1
                continue
            text = sources.get(source)
            if text is None:
                rejected += 1                      # cited a source that isn't there
                continue
            span = locate(text, quote)
            if span is None:
                rejected += 1                      # §8: the gate
                continue
            verdict, conflict = _reconcile(item, record)
            if conflict:
                conflicts.append(conflict)
            if verdict == "conflict":
                rejected += 1
                continue
            if verdict == "duplicate":
                duplicates += 1
                continue
            obligation = item.get("obligation")
            confidence = item.get("confidence")
            kept.append({
                "label": label,
                "value": value,
                "obligation": obligation if obligation in OBLIGATIONS else None,
                "confidence": confidence if confidence in CONFIDENCES else "medium",
                "quote": quote,
                "source": source,
                "start": span[0],
                "end": span[1],
            })
        if kept:
            heading, question = SECTION_HEADINGS[key]
            sections.append({"key": key, "heading": heading,
                             "question": question, "items": kept})

    not_found = [str(x).strip() for x in (raw.get("not_found") or [])
                 if str(x).strip()][:20]

    return {
        "sections": sections,
        "conflicts": conflicts,
        "not_found": not_found,
        "rejected_n": rejected,
        "duplicate_n": duplicates,
        "n_sections": len(sections),
        "n_items": sum(len(s["items"]) for s in sections),
    }


# --------------------------------------------------------------------------- #
# The request
# --------------------------------------------------------------------------- #
_SYSTEM = (
    "You extract structured facts from Greek public procurement notices for a "
    "screening panel. You are not writing a summary and not giving advice: you "
    "are locating obligations, deadlines, weights and requirements that a bidder "
    "must not miss, and pointing at the exact words that state them.\n"
    "\n"
    "Absolute rules:\n"
    "1. Every item MUST carry a `quote` copied CHARACTER FOR CHARACTER from one "
    "of the numbered sources below, and `source` MUST be that source's exact "
    "label. Do not translate, tidy, abbreviate or re-punctuate a quote. An item "
    "whose quote is not found verbatim in the source is discarded by the caller, "
    "so a paraphrased quote loses the whole item.\n"
    "2. Never state something the documents do not. If a section's subject is not "
    "covered, omit the section and name the missing subject in `not_found`. An "
    "empty answer is a correct answer; an invented one is not.\n"
    "3. Do NOT repeat the values listed under RECORD BELOW — the submission "
    "deadline, the budget and estimated value, the award-criteria type, the "
    "number of lots, the durations. The reader already sees those from the "
    "database, and they are given to you only so you can read the document in "
    "context. Extract what the record does NOT carry.\n"
    "4. `label` and `value` are read by Greek-speaking bidders: write them in "
    "Greek, short and concrete. `value` carries the substance — an amount, a "
    "percentage, a date, a specification — not a sentence about it.\n"
    "5. `obligation` is \"mandatory\" for anything required on pain of exclusion, "
    "\"desirable\" for anything scored but optional, and \"unspecified\" when the "
    "document does not make the distinction. Set `confidence` to \"low\" when the "
    "document is ambiguous and you are reading between the lines.\n"
    "6. A section with nothing to report is an EMPTY ARRAY, never a guess.\n"
    "\n"
    "Call the record_summary tool exactly once. Do not write prose."
)


def _item_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "value", "obligation", "quote", "source", "confidence"],
        "properties": {
            "label": {"type": "string",
                      "description": "Short Greek label, e.g. «Εγγύηση συμμετοχής»."},
            "value": {"type": "string",
                      "description": "The substance in Greek: amount, percentage, "
                                     "date, specification, requirement."},
            "obligation": {"type": "string", "enum": [*OBLIGATIONS, "unspecified"],
                           "description": "\"unspecified\" when the document "
                                          "does not make the distinction."},
            "quote": {"type": "string",
                      "description": "Verbatim substring of the cited source. "
                                     "Copied exactly, never paraphrased."},
            "source": {"type": "string",
                       "description": "Exact label of the source quoted, e.g. "
                                      "\"full_text\" or \"table:12\"."},
            "confidence": {"type": "string", "enum": [*CONFIDENCES]},
        },
    }


def build_tool() -> dict:
    """One strict tool. Sections are always present, and may be empty.

    Strict mode requires every property to appear in `required`, so "the model
    emits only the sections it found" is expressed as an EMPTY ARRAY rather than
    absence. Deliberately no union types and no null in an enum anywhere in this
    schema: they are the shapes most likely to come back as a 400, and an empty
    array says the same thing. verify() drops the empty sections.
    """
    item = _item_schema()
    props = {
        key: {"type": "array", "items": item, "description": desc}
        for key, _h, _q, desc in SECTIONS
    }
    props["not_found"] = {
        "type": "array", "items": {"type": "string"},
        "description": "Subjects you looked for and the documents do not state, "
                       "in Greek. Silence is not information; this is.",
    }
    return {
        "name": "record_summary",
        "description": "Record the facts extracted from this notice.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [*SECTION_KEYS, "not_found"],
            "properties": props,
        },
    }


def _render_record(record: dict) -> str:
    lines = [f"{f}: {record[f]}" for f in _CONTEXT_FIELDS
             if record.get(f) not in (None, "")]
    return "\n".join(lines)


def _render_sources(sources: dict[str, str]) -> tuple[str, dict | None]:
    """Sources as labelled blocks, capped. Truncation is reported, never silent."""
    budget = MAX_INPUT_CHARS
    blocks, truncated = [], None
    # full_text first: it is the substance, and the cap must not be eaten by
    # tables before the document has been read.
    for key in sorted(sources, key=lambda k: (k != "full_text", k)):
        text = sources[key]
        if budget <= 0:
            truncated = truncated or {"source": key, "kept": 0,
                                      "total": len(text)}
            break
        if len(text) > budget:
            truncated = {"source": key, "kept": budget, "total": len(text)}
            text = text[:budget]
        budget -= len(text)
        blocks.append(f"--- SOURCE: {key} ---\n{text}")
    return "\n\n".join(blocks), truncated


def _headers(key: str) -> dict:
    return {"content-type": "application/json", "x-api-key": key,
            "anthropic-version": API_VERSION}


def request_params(sources: dict[str, str], record: dict, *,
                   model: str = None, stream: bool = False) -> dict:
    """The Messages request for one act — identical for both paths.

    The immediate path streams it; the batch path posts the very same object as
    a `params` entry. Sharing the builder is the point: a prompt or schema that
    only half the acts were summarised with would poison the cache, since
    input_hash covers PROMPT_VERSION but not "which code path produced this".
    """
    source_text, _truncated = _render_sources(sources)
    prompt = (
        "RECORD (already shown to the reader — do NOT repeat these values):\n"
        f"{_render_record(record)}\n\n"
        f"SOURCES (quote from these, using the label after 'SOURCE:'):\n\n{source_text}"
    )
    params = {
        "model": model or MODEL,
        "max_tokens": MAX_TOKENS,
        "output_config": {"effort": EFFORT},
        "system": _SYSTEM,
        "tools": [build_tool()],
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    if stream:
        params["stream"] = True          # batch requests must NOT carry this
    return params


def _stream(req) -> tuple[str, dict, str | None]:
    """Read the SSE stream; return (tool input JSON, usage, stop_reason).

    Streaming is not a nicety here. ~30k tokens of Greek legal text with
    adaptive thinking runs for MINUTES, and a non-streaming socket sits idle
    that whole time — long enough for the read timeout (or any proxy between
    here and the API) to kill a request the API is still generating and still
    billing. Streaming keeps events flowing, so TIMEOUT becomes the gap between
    events rather than a budget for the entire call.

    Only the record_summary tool call is accumulated. Adaptive thinking is on,
    so thinking blocks stream too — they are skipped, not parsed.
    """
    parts: list[str] = []
    usage: dict = {}
    stop_reason = None
    in_tool = False
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue                      # event: / ping / blank framing
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:      # pragma: no cover — framing noise
                continue
            kind = ev.get("type")
            if kind == "message_start":
                usage.update(((ev.get("message") or {}).get("usage")) or {})
            elif kind == "content_block_start":
                block = ev.get("content_block") or {}
                in_tool = (block.get("type") == "tool_use"
                           and block.get("name") == "record_summary")
            elif kind == "content_block_delta":
                delta = ev.get("delta") or {}
                if in_tool and delta.get("type") == "input_json_delta":
                    parts.append(delta.get("partial_json") or "")
            elif kind == "content_block_stop":
                in_tool = False
            elif kind == "message_delta":
                stop_reason = (ev.get("delta") or {}).get("stop_reason") or stop_reason
                usage.update(ev.get("usage") or {})
            elif kind == "error":
                detail = (ev.get("error") or {}).get("message") or "unknown"
                raise SummaryError(f"Anthropic stream error: {detail}")
    return "".join(parts), usage, stop_reason


def call_model(sources: dict[str, str], record: dict, *,
               model: str = None) -> tuple[dict, dict, dict | None]:
    """Ask the model. Returns (raw tool input, usage, truncation or None).

    Raises SummaryError for everything a caller can act on: no key, no text, an
    API failure, or a reply with no tool call in it.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SummaryError(
            "ANTHROPIC_API_KEY is not set — AI summaries are disabled. "
            "Set it in the environment (see console.anthropic.com) and restart.")
    if not sources:
        raise SummaryError("no full text or published tables to read")

    _text, truncated = _render_sources(sources)
    if not _text.strip():
        raise SummaryError("no full text or published tables to read")

    params = request_params(sources, record, model=model, stream=True)
    req = urllib.request.Request(API_URL, data=json.dumps(params).encode(),
                                 headers=_headers(key))
    try:
        tool_json, usage, stop_reason = _stream(req)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise SummaryError("The Anthropic API rejected the key (401). "
                               "Check ANTHROPIC_API_KEY.") from e
        raise SummaryError(f"Anthropic API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SummaryError(f"Could not reach the Anthropic API: {e.reason}") from e

    if stop_reason == "refusal":
        raise SummaryError("The model declined to process this document.")
    if stop_reason == "max_tokens":
        # Diagnosed here rather than left to the JSON parser below: the reply
        # is valid up to the cut, so it fails as a syntax error several layers
        # away from the cause, and the admin reads "did not parse" for what is
        # really "the answer did not fit".
        raise SummaryError(
            f"The reply reached the {MAX_TOKENS:,}-token output cap before the "
            f"tool call was complete, so nothing could be saved — and those "
            f"tokens are still billed. Raise AI_SUMMARY_MAX_TOKENS.")
    if not tool_json.strip():
        raise SummaryError("The model returned no structured output "
                           f"(stop_reason={stop_reason!r}).")
    try:
        raw = json.loads(tool_json)         # accumulated, never string-matched
    except json.JSONDecodeError as e:
        raise SummaryError(
            f"Tool arguments did not parse (stop_reason={stop_reason!r}); "
            "a truncated stream usually means max_tokens was hit."
        ) from e
    return raw or {}, usage, truncated


# --------------------------------------------------------------------------- #
# The batch path — half price, asynchronous
#
# Same prompt, same schema, same quote gate; only the transport differs. Batch
# requests are NOT streamed (there is no socket to keep alive) and results land
# whenever the batch ends — most inside an hour, guaranteed within 24. That is
# useless for "a reader clicked Generate and is watching a spinner", and ideal
# for pre-warming the notices a digest is about to mail. Both paths exist for
# that reason; batch is not a replacement for call_model.
# --------------------------------------------------------------------------- #
BATCH_URL = "https://api.anthropic.com/v1/messages/batches"
BATCH_MAX_REQUESTS = 100_000          # documented ceiling; whichever comes first
BATCH_MAX_BYTES = 256 * 1024 * 1024


def batch_custom_id(adam: str) -> str:
    """A custom_id the API will accept for a Greek ΑΔΑΜ.

    custom_id must match ^[a-zA-Z0-9_-]{1,64}$ — every ΑΔΑΜ in this database
    fails that ("ΨΔΞΞΟΡΛΟ-Χ5Δ"), and a ΑΔΑ is no better. So the id is a hash and
    the caller keeps the map back. Deterministic, so a resubmitted act keeps the
    same id and a stale result can still be recognised.
    """
    return "a" + hashlib.sha256(adam.encode("utf-8")).hexdigest()[:40]


def submit_batch(items, *, model: str = None) -> tuple[str, dict[str, str]]:
    """Send one batch. `items` is an iterable of (adam, sources, record).

    Returns (batch_id, {custom_id: adam}). Raises SummaryError rather than
    silently truncating if the caller hands over more than a batch can hold —
    chunking is the caller's decision, not something to guess at here.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SummaryError("ANTHROPIC_API_KEY is not set — cannot submit a batch.")

    requests_, ids = [], {}
    for adam, sources, record in items:
        if not sources:
            continue                      # nothing to read; not an error
        cid = batch_custom_id(adam)
        ids[cid] = adam
        requests_.append({"custom_id": cid,
                          "params": request_params(sources, record, model=model)})
    if not requests_:
        raise SummaryError("no acts with anything to read")
    if len(requests_) > BATCH_MAX_REQUESTS:
        raise SummaryError(f"{len(requests_)} requests exceeds the "
                           f"{BATCH_MAX_REQUESTS} per-batch limit — chunk the work")

    body = json.dumps({"requests": requests_}).encode()
    if len(body) > BATCH_MAX_BYTES:
        raise SummaryError(f"batch body is {len(body)/1e6:.0f} MB, over the "
                           f"{BATCH_MAX_BYTES/1e6:.0f} MB limit — chunk the work")

    req = urllib.request.Request(BATCH_URL, data=body, headers=_headers(key))
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT,
                                                 context=_SSL_CTX).read())
    except urllib.error.HTTPError as e:
        raise SummaryError(f"Anthropic API error {e.code}: "
                           f"{e.read().decode(errors='replace')[:300]}") from e
    except urllib.error.URLError as e:
        raise SummaryError(f"Could not reach the Anthropic API: {e.reason}") from e
    return resp["id"], ids


def batch_status(batch_id: str) -> dict:
    """processing_status is 'in_progress', 'canceling' or 'ended'."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SummaryError("ANTHROPIC_API_KEY is not set.")
    req = urllib.request.Request(f"{BATCH_URL}/{batch_id}", headers=_headers(key))
    try:
        return json.loads(urllib.request.urlopen(req, timeout=TIMEOUT,
                                                 context=_SSL_CTX).read())
    except urllib.error.HTTPError as e:
        raise SummaryError(f"Anthropic API error {e.code}: "
                           f"{e.read().decode(errors='replace')[:300]}") from e


def _message_tool_input(message: dict) -> dict | None:
    """The record_summary arguments out of a completed (non-streamed) message.

    Adaptive thinking is on, so the reply carries thinking blocks as well — take
    the tool call, never "the first block".
    """
    for block in (message or {}).get("content") or []:
        if block.get("type") == "tool_use" and block.get("name") == "record_summary":
            raw = block.get("input")
            return json.loads(raw) if isinstance(raw, str) else (raw or {})
    return None


def batch_results(batch_id: str):
    """Yield (custom_id, outcome, payload) for every request in an ended batch.

    outcome is 'succeeded' | 'errored' | 'canceled' | 'expired'. For 'succeeded'
    payload is (raw tool input, usage, stop_reason); otherwise it is the API's
    error object, kept verbatim so a failure can be read after the fact.

    Results are JSONL and arrive in ANY order — always key by custom_id, never by
    position. They stay available for 29 days.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SummaryError("ANTHROPIC_API_KEY is not set.")
    info = batch_status(batch_id)
    if info.get("processing_status") != "ended":
        raise SummaryError(f"batch {batch_id} is {info.get('processing_status')!r}, "
                           "not ended — nothing to read yet")
    url = info.get("results_url")
    if not url:
        raise SummaryError(f"batch {batch_id} ended with no results_url")

    req = urllib.request.Request(url, headers=_headers(key))
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line:
                continue
            row = json.loads(line)
            cid = row.get("custom_id")
            result = row.get("result") or {}
            kind = result.get("type")
            if kind != "succeeded":
                yield cid, kind, result.get("error")
                continue
            message = result.get("message") or {}
            tool_input = _message_tool_input(message)
            if tool_input is None:
                yield cid, "errored", {"message": "no record_summary tool call"}
                continue
            yield cid, "succeeded", (tool_input,
                                     message.get("usage") or {},
                                     message.get("stop_reason"))


def cost_micro_usd(usage: dict, model: str = None, *, batch: bool = False) -> int | None:
    """Exact integer micro-dollars, or None for a model with no published price.

    Batch tokens bill at half rate, so a batch-generated row that recorded the
    immediate price would overstate what the feature actually cost.
    """
    price = PRICES_USD_PER_MTOK.get(model or MODEL)
    if not price:
        return None
    inp = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    micro = inp * price[0] + out * price[1]            # $/MTok × tokens = µ$
    return round(micro / 2) if batch else round(micro)


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def cached(c, adam: str, expect_hash: str) -> dict | None:
    """The stored payload for `adam`, but only if it answers the current inputs.

    A row whose input_hash differs is not "slightly stale" — its offsets point
    into text that no longer exists. It is not returned, and the caller offers
    regeneration instead.
    """
    c.execute("""SELECT adam, input_hash, model, prompt_version, schema_version,
                        lang, payload, n_sections, n_items, rejected_n,
                        input_tokens, output_tokens, cost_micro_usd,
                        generated_by, generated_at
                   FROM proc.act_ai_summary WHERE adam = %s""", (adam,))
    row = c.fetchone()
    if not row:
        return None
    return row if row["input_hash"] == expect_hash else None


def store(c, adam: str, *, payload: dict, hash_: str, usage: dict,
          model: str = None, lang: str = "el", by: str = None,
          batch: bool = False) -> None:
    """Write the payload, keeping the one it replaces.

    History first, then upsert: a superseded payload is what a reader was shown
    last week, and "your summary said X" needs an answer.
    """
    model = model or MODEL
    cost = cost_micro_usd(usage, model, batch=batch)
    in_tok = usage.get("input_tokens")
    out_tok = usage.get("output_tokens")
    args = (adam, hash_, model, PROMPT_VERSION, SCHEMA_VERSION, lang,
            json.dumps(payload, ensure_ascii=False),
            payload.get("n_sections", 0), payload.get("n_items", 0),
            payload.get("rejected_n", 0), in_tok, out_tok, cost, by)
    c.execute("""INSERT INTO proc.act_ai_summary_history
                   (adam, input_hash, model, prompt_version, schema_version, lang,
                    payload, rejected_n, input_tokens, output_tokens,
                    cost_micro_usd, generated_by)
                 SELECT adam, input_hash, model, prompt_version, schema_version,
                        lang, payload, rejected_n, input_tokens, output_tokens,
                        cost_micro_usd, generated_by
                   FROM proc.act_ai_summary WHERE adam = %s""", (adam,))
    c.execute("""INSERT INTO proc.act_ai_summary
                   (adam, input_hash, model, prompt_version, schema_version, lang,
                    payload, n_sections, n_items, rejected_n,
                    input_tokens, output_tokens, cost_micro_usd, generated_by)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT (adam) DO UPDATE SET
                   input_hash = EXCLUDED.input_hash,
                   model = EXCLUDED.model,
                   prompt_version = EXCLUDED.prompt_version,
                   schema_version = EXCLUDED.schema_version,
                   lang = EXCLUDED.lang,
                   payload = EXCLUDED.payload,
                   n_sections = EXCLUDED.n_sections,
                   n_items = EXCLUDED.n_items,
                   rejected_n = EXCLUDED.rejected_n,
                   input_tokens = EXCLUDED.input_tokens,
                   output_tokens = EXCLUDED.output_tokens,
                   cost_micro_usd = EXCLUDED.cost_micro_usd,
                   generated_by = EXCLUDED.generated_by,
                   generated_at = now()""", args)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def load_inputs(c, adam: str) -> tuple[dict, dict[str, str]] | None:
    """The act row and its quotable sources, or None when the act is not there.

    Notices only. Everything in the catalogue — deadlines to diarise, award
    weights, participation criteria — is a thing a notice has and an award or a
    payment does not; running this over them would spend money to produce empty
    sections.
    """
    c.execute("""SELECT * FROM proc.procurement_act WHERE adam = %s""", (adam,))
    act = c.fetchone()
    if not act or act.get("type") != "notice":
        return None
    tables = []
    if os.environ.get("TABLES_ENABLED", "1") == "1":
        c.execute("""SELECT id, locator, rows FROM proc.extracted_table
                      WHERE adam = %s AND is_published ORDER BY id""", (adam,))
        tables = c.fetchall() or []
    return act, build_sources(act, tables)


def generate(c, adam: str, *, by: str = None, model: str = None,
             lang: str = "el") -> dict:
    """Full pass for one act: read, ask, verify, store. Returns the payload.

    The caller is worker.py's runner, never a web request — see spec §10.
    """
    loaded = load_inputs(c, adam)
    if loaded is None:
        raise SummaryError(f"{adam} is not a notice, or does not exist")
    act, sources = loaded
    if not sources:
        raise SummaryError(f"{adam} has no full text or published tables")

    hash_ = input_hash(sources, model=model, lang=lang)
    raw, usage, truncated = call_model(sources, act, model=model)
    payload = verify(raw, sources, act)
    payload["truncated"] = truncated
    payload["model"] = model or MODEL
    store(c, adam, payload=payload, hash_=hash_, usage=usage,
          model=model, lang=lang, by=by)
    return payload
