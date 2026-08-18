"""
transcribe.py — speech-to-text for call recordings.

The first half of post-call AI summarisation: turn a recorded audio file into a
plain-text transcript. Asterisk has no built-in STT, so this bridges to an
external engine. It talks the OpenAI "audio/transcriptions" (Whisper) HTTP shape
with raw urllib — which means it works against BOTH the hosted OpenAI Whisper API
and any self-hosted OpenAI-compatible server (whisper.cpp's server, faster-whisper,
etc.) by pointing TRANSCRIBE_BASE_URL at it. No SDK dependency, no provider lock-in.

Gated on configuration — inert (raises TranscribeError) until a backend is set up.
Config (env):
    TRANSCRIBE_BACKEND    "openai" (default) | "none" (disabled)
    TRANSCRIBE_BASE_URL   default "https://api.openai.com/v1"
                          point at a self-hosted Whisper server to avoid OpenAI
    TRANSCRIBE_API_KEY    API key/token (falls back to OPENAI_API_KEY); may be
                          empty for a self-hosted server that needs no auth
    TRANSCRIBE_MODEL      default "whisper-1"
    TRANSCRIBE_LANGUAGE   default "el" (Greek); "" lets the engine auto-detect
"""
from __future__ import annotations

import mimetypes
import os
import ssl
import urllib.error
import urllib.request
import uuid

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover — fall back to system trust store
    _SSL_CTX = ssl.create_default_context()

BACKEND = os.environ.get("TRANSCRIBE_BACKEND", "openai").strip().lower()
BASE_URL = os.environ.get("TRANSCRIBE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
MODEL = os.environ.get("TRANSCRIBE_MODEL", "whisper-1")
LANGUAGE = os.environ.get("TRANSCRIBE_LANGUAGE", "el").strip()
# Whisper's own hard limit is 25 MB on the hosted API; guard before uploading.
_MAX_BYTES = int(os.environ.get("TRANSCRIBE_MAX_BYTES", str(25 * 1024 * 1024)))


class TranscribeError(RuntimeError):
    """Raised when a recording cannot be transcribed (config or engine error)."""


def _api_key() -> str:
    return os.environ.get("TRANSCRIBE_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def backend_configured() -> bool:
    """True when transcription can run. A self-hosted server (custom BASE_URL)
    needs no key; the hosted OpenAI endpoint does."""
    if BACKEND == "none":
        return False
    hosted = BASE_URL == "https://api.openai.com/v1"
    return bool(_api_key()) or not hosted


def _multipart(fields: dict[str, str], file_field: str, filename: str,
               file_bytes: bytes, file_ct: str) -> tuple[bytes, str]:
    """Build a minimal multipart/form-data body (stdlib only)."""
    boundary = "----khmdhs" + uuid.uuid4().hex
    crlf = b"\r\n"
    parts: list[bytes] = []
    for k, v in fields.items():
        if v is None or v == "":
            continue
        parts += [b"--" + boundary.encode(),
                  f'Content-Disposition: form-data; name="{k}"'.encode(),
                  b"", str(v).encode()]
    parts += [b"--" + boundary.encode(),
              (f'Content-Disposition: form-data; name="{file_field}"; '
               f'filename="{filename}"').encode(),
              f"Content-Type: {file_ct}".encode(), b"", file_bytes]
    parts += [b"--" + boundary.encode() + b"--", b""]
    return crlf.join(parts), boundary


def transcribe(audio_path: str, *, language: str | None = None) -> str:
    """Transcribe an audio file to plain text. Raises TranscribeError on any
    misconfiguration or engine failure."""
    if BACKEND == "none":
        raise TranscribeError("transcription disabled (TRANSCRIBE_BACKEND=none)")
    if not audio_path or not os.path.isfile(audio_path):
        raise TranscribeError(f"recording not found: {audio_path}")
    size = os.path.getsize(audio_path)
    if size == 0:
        raise TranscribeError(f"recording is empty: {audio_path}")
    if size > _MAX_BYTES:
        raise TranscribeError(
            f"recording is {size} bytes, over the {_MAX_BYTES}-byte transcription "
            "limit — split or downsample it first.")
    with open(audio_path, "rb") as fh:
        audio = fh.read()
    ct = mimetypes.guess_type(audio_path)[0] or "application/octet-stream"

    lang = LANGUAGE if language is None else language
    body, boundary = _multipart(
        {"model": MODEL, "language": lang, "response_format": "text"},
        "file", os.path.basename(audio_path), audio, ct)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(f"{BASE_URL}/audio/transcriptions",
                                 data=body, headers=headers)
    try:
        raw = urllib.request.urlopen(req, timeout=300, context=_SSL_CTX).read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code in (401, 403):
            raise TranscribeError(
                f"transcription endpoint rejected the credentials ({e.code}). "
                "Check TRANSCRIBE_API_KEY / TRANSCRIBE_BASE_URL.") from e
        raise TranscribeError(f"transcription API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise TranscribeError(f"could not reach the transcription endpoint: {e.reason}") from e

    text = raw.decode("utf-8", "replace").strip()
    # response_format=text returns plain text; some servers still wrap JSON.
    if text.startswith("{"):
        import json
        try:
            text = (json.loads(text).get("text") or "").strip()
        except Exception:  # noqa: BLE001 — keep whatever we got
            pass
    if not text:
        raise TranscribeError("transcription returned no text")
    return text
