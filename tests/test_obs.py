"""Structured logging: JSON formatter + request-id header."""
import json
import logging

from app import obs


def test_json_formatter_emits_fields():
    rec = logging.LogRecord("khmdhs", logging.INFO, __file__, 0, "request", None, None)
    rec.extra_fields = {"request_id": "abc", "status": 200, "dur_ms": 3.1}
    out = obs._JsonFormatter().format(rec)
    d = json.loads(out)
    assert d["msg"] == "request"
    assert d["level"] == "INFO"
    assert d["logger"] == "khmdhs"
    assert d["request_id"] == "abc"
    assert d["status"] == 200
    assert "ts" in d


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = logging.LogRecord("khmdhs", logging.ERROR, __file__, 0, "oops",
                                None, sys.exc_info())
    d = json.loads(obs._JsonFormatter().format(rec))
    assert "exc" in d and "ValueError" in d["exc"]


def test_response_has_request_id(client):
    r = client.get("/healthz")
    assert r.headers.get("x-request-id")


def test_request_id_is_echoed(client):
    r = client.get("/healthz", headers={"X-Request-ID": "trace-123"})
    assert r.headers.get("x-request-id") == "trace-123"
