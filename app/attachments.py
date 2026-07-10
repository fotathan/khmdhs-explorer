"""
attachments.py — store uploaded act attachments and read them back.

Binaries live OUTSIDE the database (never in Postgres — the prod DB is a free
tier). This is a thin storage abstraction so the object-storage backend is a
config change, not a rewrite: `local_fs` (dev, under ATTACHMENTS_DIR) or `s3`
(any S3-compatible object store — AWS S3, Cloudflare R2, or Supabase Storage's
S3 endpoint). Render disks are ephemeral, so prod should use `s3`.

The whole feature is gated on ATTACHMENTS_ENABLED (default off), so prod — which
never sets it — stores nothing and never grows.

KHMDHS-specific glue: NOT one of the byte-identical sibling modules
(extractors/exporter/ocr). Text extraction for search reuses
app.extractors.extract_text_from_upload (which also unpacks zips).

Env:
  ATTACHMENTS_ENABLED=1            turn the feature on (default 0)
  ATTACHMENTS_BACKEND=local_fs|s3  storage backend (default local_fs)
  ATTACH_MAX_MB=80                 per-file upload cap

  local_fs:
  ATTACHMENTS_DIR=<path>           where local_fs writes (default <repo>/attachment_store)

  s3 (needs boto3; keeps storage_ref backend-agnostic so you can migrate):
  ATTACH_S3_BUCKET=<bucket>            (required)
  ATTACH_S3_ACCESS_KEY_ID=<key>        (required)
  ATTACH_S3_SECRET_ACCESS_KEY=<secret> (required)
  ATTACH_S3_ENDPOINT=<url>            S3 endpoint — set for R2/Supabase; omit for AWS S3
  ATTACH_S3_REGION=<region>           default "auto" (fine for R2/Supabase; use e.g. eu-west-3 on AWS)
  ATTACH_S3_PREFIX=<prefix>           optional key prefix within the bucket
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import uuid
from urllib.parse import quote

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.environ.get("ATTACHMENTS_DIR", os.path.join(_REPO_ROOT, "attachment_store"))
BACKEND = os.environ.get("ATTACHMENTS_BACKEND", "local_fs")
MAX_BYTES = int(os.environ.get("ATTACH_MAX_MB", "80")) * 1024 * 1024

# S3-compatible object storage (AWS S3 / Cloudflare R2 / Supabase Storage).
_S3_BUCKET = os.environ.get("ATTACH_S3_BUCKET")
_S3_ENDPOINT = os.environ.get("ATTACH_S3_ENDPOINT") or None   # None → real AWS S3
_S3_REGION = os.environ.get("ATTACH_S3_REGION", "auto")
_S3_PREFIX = os.environ.get("ATTACH_S3_PREFIX", "").strip("/")
_s3_client_cache = None


def enabled() -> bool:
    return os.environ.get("ATTACHMENTS_ENABLED", "0") == "1"


def content_disposition(filename: str) -> str:
    """A latin-1-safe Content-Disposition for a download. HTTP header values must
    be latin-1, so a Greek filename in a bare filename="…" 500s the server. Emit
    an ASCII fallback plus an RFC 5987 filename* with the real UTF-8 name."""
    name = (filename or "attachment").replace('"', "").replace("\\", "")
    ascii_fallback = name.encode("ascii", "ignore").decode("ascii").strip() or "attachment"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(name)}"


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
# storage backend: s3-compatible (boto3, lazily imported)
# --------------------------------------------------------------------------- #
def _s3():
    global _s3_client_cache
    if _s3_client_cache is None:
        try:
            import boto3
        except ImportError as e:      # noqa: BLE001
            raise AttachmentError("S3 backend needs boto3 (pip install boto3)") from e
        if not _S3_BUCKET:
            raise AttachmentError("ATTACH_S3_BUCKET is not set")
        _s3_client_cache = boto3.client(
            "s3", endpoint_url=_S3_ENDPOINT, region_name=_S3_REGION,
            aws_access_key_id=os.environ.get("ATTACH_S3_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("ATTACH_S3_SECRET_ACCESS_KEY"))
    return _s3_client_cache


def _s3_key(storage_ref: str) -> str:
    return f"{_S3_PREFIX}/{storage_ref}" if _S3_PREFIX else storage_ref


# --------------------------------------------------------------------------- #
# storage API — dispatches on BACKEND (local_fs | s3). storage_ref is backend-
# agnostic ("<adam>/<uuid>__<name>"), so switching backends only changes where
# bytes live, not what's recorded in the DB.
# --------------------------------------------------------------------------- #
def store(adam: str, filename: str, data: bytes) -> dict:
    """Persist bytes and return {storage_ref, checksum, size, mimetype}."""
    if not enabled():
        raise AttachmentError("attachments are disabled on this environment")
    if BACKEND not in ("local_fs", "s3"):
        raise AttachmentError(f"unknown ATTACHMENTS_BACKEND: {BACKEND!r}")
    if not data:
        raise AttachmentError("empty file")
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"file exceeds {MAX_BYTES // (1024 * 1024)} MB")

    storage_ref = f"{_safe_seg(adam)}/{uuid.uuid4().hex}__{_safe_name(filename)}"
    mimetype = mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
    if BACKEND == "local_fs":
        path = _resolve(storage_ref)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    else:  # s3
        try:
            _s3().put_object(Bucket=_S3_BUCKET, Key=_s3_key(storage_ref),
                             Body=data, ContentType=mimetype)
        except AttachmentError:
            raise
        except Exception as e:      # noqa: BLE001 — boto/network errors → user-facing
            raise AttachmentError(f"upload to object storage failed: {e}") from e
    return {
        "storage_ref": storage_ref,
        "checksum": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mimetype": mimetype,
    }


def load(storage_ref: str) -> bytes:
    """Read a stored file's bytes."""
    if not storage_ref:
        raise AttachmentError("missing storage reference")
    if BACKEND == "s3":
        try:
            return _s3().get_object(Bucket=_S3_BUCKET,
                                    Key=_s3_key(storage_ref))["Body"].read()
        except AttachmentError:
            raise
        except Exception as e:      # noqa: BLE001
            raise AttachmentError(f"download from object storage failed: {e}") from e
    with open(_resolve(storage_ref), "rb") as f:
        return f.read()


def remove(storage_ref: str) -> None:
    """Delete a stored file (fail-soft — a missing file is fine)."""
    if not storage_ref:
        return
    if BACKEND == "s3":
        try:
            _s3().delete_object(Bucket=_S3_BUCKET, Key=_s3_key(storage_ref))
        except Exception:      # noqa: BLE001 — best-effort delete
            pass
        return
    try:
        os.remove(_resolve(storage_ref))
    except (OSError, AttachmentError):
        pass
