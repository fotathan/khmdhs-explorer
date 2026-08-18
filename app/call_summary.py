"""
call_summary.py — summarise a call transcript with the Claude API.

The second half of post-call AI summarisation: given a plain-text transcript
(produced by app/transcribe.py), ask Claude for a short summary plus action
items, and return them as structured data. Mirrors the request/error style of
app/ocr.py (raw urllib, x-api-key, anthropic-version) so the app keeps a single,
consistent Claude-call convention with no SDK dependency.

Gated on ANTHROPIC_API_KEY — inert (raises SummaryError) when absent. Configure:
    ANTHROPIC_API_KEY      required
    CALL_SUMMARY_MODEL     default "claude-sonnet-4-6" (matches OCR_MODEL family)
    CALL_SUMMARY_MAXTOKENS default 1024
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover — fall back to system trust store
    _SSL_CTX = ssl.create_default_context()

API_URL = "https://api.anthropic.com/v1/messages"
SUMMARY_MODEL = os.environ.get("CALL_SUMMARY_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("CALL_SUMMARY_MAXTOKENS", "1024"))

# Keep prompt cost bounded — transcripts can be long, but a call summary needs
# only the content, not the whole thing verbatim if it is enormous.
_MAX_TRANSCRIPT_CHARS = int(os.environ.get("CALL_SUMMARY_MAX_INPUT_CHARS", "48000"))


class SummaryError(RuntimeError):
    """Raised when the transcript cannot be summarised (config or API error)."""


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_PROMPT = (
    "You are summarising a customer phone call for a CRM. Read the transcript and "
    "reply with ONLY a JSON object (no markdown, no commentary) of this shape:\n"
    '{"summary": "<2-4 sentence summary>", "action_items": ["<short item>", ...]}\n'
    "Write the summary and action items in the SAME language as the transcript "
    "(Greek transcripts → Greek summary). If there are no clear action items, use "
    "an empty list. Be concise and factual; do not invent details.\n"
)


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's reply (tolerate code fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.startswith("json") else text
        text = text.strip()
    # If the model wrapped prose around it, grab the outermost {...}.
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


def summarize_transcript(transcript: str, *, context: dict | None = None) -> dict:
    """Summarise a transcript. Returns {"summary", "action_items", "model"}.

    `context` (optional) may carry {number, name, company, direction} to give the
    model who the call was with; it is added to the prompt but never required.
    Raises SummaryError on missing key, empty transcript, or an API failure.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        raise SummaryError("empty transcript — nothing to summarise")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SummaryError(
            "ANTHROPIC_API_KEY is not set — call summarisation is disabled. "
            "Set it in the environment (see console.anthropic.com) and restart.")

    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:_MAX_TRANSCRIPT_CHARS] + "\n…[truncated]"

    header = ""
    if context:
        who = " · ".join(str(context[k]) for k in ("name", "company", "number")
                         if context.get(k))
        if who:
            header = f"Call with: {who}\n"
        if context.get("direction"):
            header += f"Direction: {context['direction']}\n"

    body = json.dumps({
        "model": SUMMARY_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [{"type": "text",
                         "text": _PROMPT + "\n" + header + "\nTranscript:\n" + transcript}],
        }],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=120, context=_SSL_CTX).read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise SummaryError("The Anthropic API rejected the key (401). "
                               "Check ANTHROPIC_API_KEY.") from e
        raise SummaryError(f"Anthropic API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SummaryError(f"Could not reach the Anthropic API: {e.reason}") from e

    text = "".join(b.get("text", "") for b in resp.get("content", [])
                   if b.get("type") == "text").strip()
    if not text:
        raise SummaryError("The model returned an empty response.")
    try:
        parsed = _extract_json(text)
    except json.JSONDecodeError as e:
        raise SummaryError(f"Model returned non-JSON output: {text[:200]}") from e

    summary = (parsed.get("summary") or "").strip()
    items = parsed.get("action_items") or []
    if not isinstance(items, list):
        items = [str(items)]
    items = [str(x).strip() for x in items if str(x).strip()]
    if not summary:
        raise SummaryError("The model did not return a summary.")
    return {"summary": summary, "action_items": items, "model": SUMMARY_MODEL}
