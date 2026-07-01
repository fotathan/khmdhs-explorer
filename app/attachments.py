"""
attachments.py — store uploaded act attachments and read them back.

Binaries live OUTSIDE the database (never in Postgres — the prod DB is a free
tier). This is a thin storage abstraction so a future object-storage backend
(S3 / Supabase Storage / R2) is a config change, not a rewrite. The only backend
today is `local_fs`, which writes under ATTACHMENTS_DIR on the local disk.

The whole feature is gated on ATTACHMENTS_ENABLED (default off), so prod — which
never sets it — stores nothing and never grows.

KHMDHS-specific glue: NOT one of the byte-identical sibling modules
(extractors/exporter/ocr). Text extraction for search reuses
app.extractors.extract_text_from_upload (which also unpacks zips).

Env:
  ATTACHMENTS_ENABLED=1        turn the feature on (default 0)
  ATTACHMENTS_BACKEND=local_fs storage backend (only one for now)
  ATTACHMENTS_DIR=<path>       where local_fs writes (default <repo>/attachment_store)
  ATTACH_MAX_MB=80             per-file upload cap
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import uuid

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.environ.get("ATTACHMENTS_DIR", os.path.join(_REPO_ROOT, "attachment_store"))
BACKEND = os.environ.get("ATTACHMENTS_BACKEND", "local_fs")
MAX_BYTES = int(os.environ.get("ATTACH_MAX_MB", "80")) * 1024 * 1024


def enabled() -> bool:
    return os.environ.get("ATTACHMENTS_ENABLED", "0") == "1"


class AttachmentError(Exception):
    """Human-facing storage error (surfaced to the curator)."""


# --------------------------------------------------------------------------- #
# name / path safety
# --------------------------------------------------------------------------- #
def _safe_name(filename: str) -> str:
    base = os.path.basename(filename or "file")
    base = re.sub(r"[^\w\-. ]", "_", base, flags=re.UNICODE).strip() or "file"
    return base[:150]


def _safe_seg(adam: str) -> str:
    return re.sub(r"[^\w\-.]", "_", adam or "unknown", flags=re.UNICODE)[:120] or "unknown"


def _resolve(storage_ref: str) -> str:
    """Absolute path for a stored ref, guarded against path traversal."""
    root = os.path.realpath(DIR)
    p = os.path.realpath(os.path.join(DIR, storage_ref))
    if p != root and not p.startswith(root + os.sep):
        raise AttachmentError("invalid storage reference")
    return p


# --------------------------------------------------------------------------- #
# storage backend: local_fs
# --------------------------------------------------------------------------- #
def store(adam: str, filename: str, data: bytes) -> dict:
    """Persist bytes and return {storage_ref, checksum, size, mimetype}."""
    if not enabled():
        raise AttachmentError("attachments are disabled on this environment")
    if BACKEND != "local_fs":
        raise AttachmentError(f"unknown ATTACHMENTS_BACKEND: {BACKEND!r}")
    if not data:
        raise AttachmentError("empty file")
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"file exceeds {MAX_BYTES // (1024 * 1024)} MB")

    storage_ref = f"{_safe_seg(adam)}/{uuid.uuid4().hex}__{_safe_name(filename)}"
    path = _resolve(storage_ref)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return {
        "storage_ref": storage_ref,
        "checksum": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mimetype": mimetypes.guess_type(filename or "")[0] or "application/octet-stream",
    }


def load(storage_ref: str) -> bytes:
    """Read a stored file's bytes."""
    if not storage_ref:
        raise AttachmentError("missing storage reference")
    with open(_resolve(storage_ref), "rb") as f:
        return f.read()


def remove(storage_ref: str) -> None:
    """Delete a stored file (fail-soft — a missing file is fine)."""
    if not storage_ref:
        return
    try:
        os.remove(_resolve(storage_ref))
    except (OSError, AttachmentError):
        pass
