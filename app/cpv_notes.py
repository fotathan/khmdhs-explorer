"""One generated paragraph per CPV code, in Greek and English.

Slice 3 of docs/specs/public-detail-pages.md. The official CPV name stays
exact; the paragraph next to it explains in plain words what the code covers,
using the everyday terms a buyer or supplier would search for. Owner,
2026-10-09: written by AI (Claude, through the Batch API), for EVERY code in
the official list (9,454), both languages, shown on public CPV pages (slice 4).

What keeps the text honest:
  - the model is given only the code's official names, its place in the
    hierarchy and its sub-codes' official names, and told to say nothing else
    (no laws, amounts, dates, authorities, brands, this site);
  - every reply is checked (validate) before it is stored: both languages
    present and in the right script, a sane length, no numbers, no links;
  - a note can be hidden (hidden_at) without regenerating anything;
  - input_hash covers model + prompt + the code's context, so a changed prompt
    or a renamed sub-code marks exactly the affected notes as stale.

Generation happens LOCALLY (cpv_notes_gen.py at the repo root) and the finished
rows are pushed to production; the app only ever reads proc.cpv_note. Nothing
here reads customer data or act_ai_summary (test-enforced).

Transport: the project's own Anthropic HTTP helpers (app/ai_summary.py), the
same ones the summary batch path uses, so there is one place that talks to
the API.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.request

try:
    from app import ai_summary as _ai
except ImportError:  # flat layout
    import ai_summary as _ai

MODEL = "claude-opus-5-5"
PROMPT_VERSION = 1
EFFORT = "low"            # a reference paragraph, not a reasoning task
MAX_TOKENS = 6000         # thinking counts against it; replies are ~300-600
MAX_CHILDREN = 40         # sub-code names given as context; the rest are counted

# Codes in the official list are 8 digits + check digit. proc.cpv_code also
# holds 11 supplementary codes (DA03-0, FB02-0 …) — not CPV subjects, no note.
CODE_RE = re.compile(r"^\d{8}-\d$")

SCHEMA = {
    "type": "object",
    "properties": {
        "el": {"type": "string", "description": "The paragraph in Greek."},
        "en": {"type": "string", "description": "The same paragraph in English."},
    },
    "required": ["el", "en"],
    "additionalProperties": False,
}

SYSTEM = (
    "You write short reference descriptions of Common Procurement Vocabulary "
    "(CPV) codes for a Greek public-procurement website. For each code you "
    "receive its official Greek and English names, its place in the CPV "
    "hierarchy, and the official names of its sub-codes.\n\n"
    "Write one paragraph in Greek and one in English that say the same thing:\n"
    "- 2 to 3 sentences, about 50-90 words each.\n"
    "- Explain in plain language what goods, services or works the code covers "
    "and what kind of purchase it is used for. Where the sub-codes show what it "
    "includes, name the main ones naturally, in words.\n"
    "- Use the everyday words and synonyms a buyer or a supplier would search "
    "for, alongside the official terminology.\n"
    "- Say only what follows from the official names and the hierarchy. Do not "
    "invent examples the names do not support. If the code is narrow, keep the "
    "paragraph narrow.\n"
    "- Never mention laws, thresholds, procedures, prices, amounts, "
    "percentages, dates, statistics, specific authorities, companies, brands, "
    "or this website.\n"
    "- Do not write any code number, and do not open with the official name "
    "word for word: it is printed directly above the paragraph.\n"
    "- Neutral, factual, encyclopaedic tone. No marketing language, no second "
    "person, no lists, no headings.\n"
    "- Greek: natural modern Greek with correct accents, written as Greek, not "
    "translated from the English. English: plain international English.\n"
    "Return JSON with the keys \"el\" and \"en\"."
)

# Published Anthropic prices, USD per million tokens (input, output). Batch
# bills both at half. Check the pricing page when changing MODEL.
PRICE_USD_PER_MTOK = {"claude-opus-5-5": (4.00, 20.00)}

_GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
_LETTER = re.compile(r"[^\W\d_]")


# --------------------------------------------------------------------------- #
# The CPV hierarchy
# --------------------------------------------------------------------------- #
def load_codes(c) -> dict[str, dict]:
    """{code: {"el", "en"}} for every official code (supplementary ones out)."""
    c.execute("SELECT cpv_code, description, description_en FROM proc.cpv_code")
    return {r["cpv_code"]: {"el": (r["description"] or "").strip(),
                            "en": (r["description_en"] or "").strip()}
            for r in c.fetchall() if CODE_RE.match(r["cpv_code"] or "")}


def _stem(code: str) -> str:
    """The significant prefix: 50220000-3 → '5022'. Never shorter than the
    2-digit division."""
    base = code[:8].rstrip("0")
    return base if len(base) >= 2 else code[:2]


def build_tree(codes: dict) -> tuple[dict[str, str | None], dict[str, list[str]]]:
    """(parent_of, children_of). A code's parent is the nearest code whose
    8-digit base is its stem cut short and padded with zeros."""
    by_base = {code[:8]: code for code in codes}
    parent: dict[str, str | None] = {}
    children: dict[str, list[str]] = {code: [] for code in codes}
    for code in codes:
        stem = _stem(code)
        p = None
        for k in range(len(stem) - 1, 1, -1):
            cand = by_base.get(stem[:k].ljust(8, "0"))
            if cand and cand != code:
                p = cand
                break
        parent[code] = p
        if p:
            children[p].append(code)
    for kids in children.values():
        kids.sort()
    return parent, children


def ancestors(code: str, parent: dict) -> list[str]:
    out, p = [], parent.get(code)
    while p:
        out.append(p)
        p = parent.get(p)
    return list(reversed(out))


def level(code: str) -> int:
    """1 division … 5 the most specific (6-8 significant digits)."""
    return min(len(_stem(code)) - 1, 5)


# --------------------------------------------------------------------------- #
# The request
# --------------------------------------------------------------------------- #
def user_text(code: str, codes: dict, parent: dict, children: dict) -> str:
    names = codes[code]
    lines = [f"Κωδικός / Code: {code}",
             f"Επίσημη ονομασία (EL): {names['el']}",
             f"Official name (EN): {names['en'] or '—'}"]
    chain = ancestors(code, parent)
    if chain:
        lines.append("Ανήκει σε / Belongs to:")
        lines += [f"  - {codes[a]['el']} / {codes[a]['en']}" for a in chain]
    kids = children.get(code) or []
    if kids:
        lines.append("Υποκατηγορίες / Sub-codes:")
        lines += [f"  - {codes[k]['el']} / {codes[k]['en']}" for k in kids[:MAX_CHILDREN]]
        if len(kids) > MAX_CHILDREN:
            lines.append(f"  - (+{len(kids) - MAX_CHILDREN} more)")
    else:
        lines.append("Υποκατηγορίες / Sub-codes: none (this is a specific code)")
    return "\n".join(lines)


def request_params(code: str, codes: dict, parent: dict, children: dict,
                   *, model: str = MODEL) -> dict:
    """The Messages request for one code — the same object for an immediate
    call and for a batch entry. No forced tool_choice (Opus 5.5 rejects it)
    and no server-side fallbacks (the Batch API rejects them): a refusal is
    recorded and the code simply has no note."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM,
        "output_config": {"effort": EFFORT,
                          "format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user",
                      "content": user_text(code, codes, parent, children)}],
    }


def input_hash(params: dict) -> str:
    blob = json.dumps({"v": PROMPT_VERSION, "p": params}, sort_keys=True,
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def custom_id(code: str) -> str:
    """Batch ids must match ^[a-zA-Z0-9_-]{1,64}$; a CPV code does, prefixed."""
    return "c" + code


def code_of(cid: str) -> str:
    return cid[1:]


# --------------------------------------------------------------------------- #
# Checking a reply
# --------------------------------------------------------------------------- #
def _greek_share(s: str) -> float:
    letters = _LETTER.findall(s)
    return (len(_GREEK.findall(s)) / len(letters)) if letters else 0.0


def validate(code: str, el: str, en: str) -> list[str]:
    """Reasons to refuse a reply; empty means store it."""
    problems = []
    for lang, text in (("el", el), ("en", en)):
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{lang}: empty")
            continue
        if not 120 <= len(text) <= 900:
            problems.append(f"{lang}: length {len(text)}")
        if re.search(r"\d{3,}", text):
            problems.append(f"{lang}: contains a number")
        if any(s in text for s in ("€", "%", "http", "www.")):
            problems.append(f"{lang}: amount, percentage or link")
        if "\n" in text.strip():
            problems.append(f"{lang}: more than one paragraph")
    if isinstance(el, str) and el.strip() and _greek_share(el) < 0.8:
        problems.append("el: not Greek")
    if isinstance(en, str) and _GREEK.search(en or ""):
        problems.append("en: Greek letters in English")
    if code[:8] in f"{el}{en}":
        problems.append("the code number is in the text")
    return problems


def parse_message(message: dict) -> tuple[dict | None, str | None]:
    """(reply, error) from a completed Messages response."""
    stop = message.get("stop_reason")
    if stop == "refusal":
        return None, "refusal"
    if stop == "max_tokens":
        return None, "max_tokens"
    text = next((b.get("text") for b in message.get("content") or []
                 if b.get("type") == "text"), None)
    if not text:
        return None, "no text block"
    try:
        reply = json.loads(text)
    except json.JSONDecodeError:
        return None, "not JSON"
    if not isinstance(reply, dict):
        return None, "not an object"
    return {"el": (reply.get("el") or "").strip(),
            "en": (reply.get("en") or "").strip()}, None


def cost_micro_usd(usage: dict, *, model: str = MODEL, batch: bool) -> int:
    pin, pout = PRICE_USD_PER_MTOK[model]
    inp = (int(usage.get("input_tokens") or 0)
           + int(usage.get("cache_read_input_tokens") or 0)
           + int(usage.get("cache_creation_input_tokens") or 0))
    out = int(usage.get("output_tokens") or 0)
    micro = inp * pin + out * pout
    return round(micro / 2) if batch else round(micro)


# --------------------------------------------------------------------------- #
# Talking to the API (the project's transport — app/ai_summary.py)
# --------------------------------------------------------------------------- #
def _key() -> str:
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise _ai.SummaryError("ANTHROPIC_API_KEY is not set.")
    return key


def _post(url: str, body: dict, timeout: float = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=_ai._anthropic_headers(_key()))
    try:
        return json.loads(urllib.request.urlopen(
            req, timeout=timeout or _ai.TIMEOUT, context=_ai._SSL_CTX).read())
    except urllib.error.HTTPError as e:
        raise _ai.SummaryError(f"Anthropic API error {e.code}: "
                               f"{e.read().decode(errors='replace')[:300]}") from e


def call_one(params: dict, *, retries: int = 3) -> dict:
    """One immediate request (the review sample). Retries 429/5xx."""
    for attempt in range(retries + 1):
        try:
            return _post(_ai.API_URL, params)
        except _ai.SummaryError as e:
            msg = str(e)
            if attempt < retries and any(f"error {s}" in msg for s in ("429", "500", "502", "503", "529")):
                time.sleep(2 ** attempt + random.random())
                continue
            raise


def submit_batch(requests_: list[dict]) -> str:
    """requests_: [{"custom_id", "params"}]. Returns the batch id."""
    if not requests_:
        raise _ai.SummaryError("nothing to submit")
    if len(requests_) > _ai.BATCH_MAX_REQUESTS:
        raise _ai.SummaryError("too many requests for one batch — chunk them")
    body = {"requests": requests_}
    if len(json.dumps(body).encode()) > _ai.BATCH_MAX_BYTES:
        raise _ai.SummaryError("batch body over the size limit — chunk it")
    return _post(_ai.BATCH_URL, body)["id"]


def batch_results(batch_id: str):
    """Yield (custom_id, kind, message-or-error) for an ENDED batch. Results
    arrive in any order — key by custom_id."""
    info = _ai.batch_status(batch_id)
    if info.get("processing_status") != "ended":
        raise _ai.SummaryError(f"batch {batch_id} is {info.get('processing_status')!r}")
    req = urllib.request.Request(info["results_url"],
                                 headers=_ai._anthropic_headers(_key()))
    with urllib.request.urlopen(req, timeout=_ai.TIMEOUT, context=_ai._SSL_CTX) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            row = json.loads(line)
            result = row.get("result") or {}
            kind = result.get("type")
            yield (row.get("custom_id"), kind,
                   result.get("message") if kind == "succeeded" else result.get("error"))


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def current_hashes(c) -> dict[str, str]:
    c.execute("SELECT cpv_code, input_hash FROM proc.cpv_note")
    return {r["cpv_code"]: r["input_hash"] for r in c.fetchall()}


def store(c, code: str, reply: dict, *, ihash: str, usage: dict,
          batch_id: str | None, model: str = MODEL) -> None:
    """Upsert one note. A regenerated note clears an earlier hide only when the
    text actually changed — a hidden note is never resurrected by a re-run
    that produced the same words."""
    c.execute("""
        INSERT INTO proc.cpv_note
          (cpv_code, text_el, text_en, model, prompt_version, input_hash,
           input_tokens, output_tokens, cost_micro_usd, batch_id, generated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (cpv_code) DO UPDATE SET
          hidden_at = CASE WHEN proc.cpv_note.text_el = EXCLUDED.text_el
                            AND proc.cpv_note.text_en = EXCLUDED.text_en
                           THEN proc.cpv_note.hidden_at END,
          hidden_reason = CASE WHEN proc.cpv_note.text_el = EXCLUDED.text_el
                                AND proc.cpv_note.text_en = EXCLUDED.text_en
                               THEN proc.cpv_note.hidden_reason END,
          text_el = EXCLUDED.text_el, text_en = EXCLUDED.text_en,
          model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version,
          input_hash = EXCLUDED.input_hash, input_tokens = EXCLUDED.input_tokens,
          output_tokens = EXCLUDED.output_tokens,
          cost_micro_usd = EXCLUDED.cost_micro_usd, batch_id = EXCLUDED.batch_id,
          generated_at = now()""",
        (code, reply["el"], reply["en"], model, PROMPT_VERSION, ihash,
         int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
         cost_micro_usd(usage, model=model, batch=batch_id is not None), batch_id))


def note_for(c, code: str, lang: str = "el") -> str | None:
    """The visible note for one code in the reader's language, or None."""
    c.execute("""SELECT text_el, text_en FROM proc.cpv_note
                 WHERE cpv_code = %s AND hidden_at IS NULL""", (code,))
    r = c.fetchone()
    if not r:
        return None
    return r["text_en"] if lang == "en" else r["text_el"]
