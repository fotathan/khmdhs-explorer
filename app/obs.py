"""
obs.py — structured (JSON) logging for the web app.

One dedicated "khmdhs" logger with its own handler and propagate=False, so it's
independent of uvicorn's logging config. In prod (RENDER set) it emits one JSON
line per event to stdout — parseable by Render's log viewer or any aggregator
you point at it later (the "structured logging" pre-pilot item). Locally it's
plain text for readability.

  LOG_LEVEL   default INFO
  LOG_FORMAT  json | text   (default: json on Render, text locally)
  LOG_SLOW_MS request latency (ms) at/above which a request logs at WARNING
"""
from __future__ import annotations

import json
import logging
import os
import sys

_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_FORMAT = os.environ.get("LOG_FORMAT", "json" if os.environ.get("RENDER") else "text")
SLOW_MS = float(os.environ.get("LOG_SLOW_MS", "1000"))

_LOGGER_NAME = "khmdhs"
_configured = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "extra_fields", None)
        if fields:
            for k, v in fields.items():
                payload.setdefault(k, v)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    """Readable dev format — message, then the structured fields as key=value
    pairs, then any traceback."""
    def format(self, record: logging.LogRecord) -> str:
        exc = record.exc_info
        record.exc_info = None          # format the base line WITHOUT the traceback
        try:
            line = super().format(record)
        finally:
            record.exc_info = exc
        fields = getattr(record, "extra_fields", None)
        if fields:
            line += "  " + " ".join(f"{k}={v}" for k, v in fields.items())
        if exc:
            line += "\n" + self.formatException(exc)
        return line


def configure() -> None:
    """Idempotently attach a stdout handler to the khmdhs logger."""
    global _configured
    if _configured:
        return
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(_LEVEL)
    logger.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    if _FORMAT == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(_TextFormatter(
            "%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    logger.handlers = [handler]
    _configured = True


def logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def log_event(level: int, msg: str, *, exc_info: bool = False, **fields) -> None:
    """Emit a structured event: the free-text msg plus arbitrary keyword fields
    (request_id, method, path, status, dur_ms, uid, ip, ...)."""
    logger().log(level, msg, exc_info=exc_info, extra={"extra_fields": fields})
