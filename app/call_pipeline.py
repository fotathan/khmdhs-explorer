"""
call_pipeline.py — orchestrate post-call AI summarisation.

Ties the two halves together for one call: read its recording, transcribe it
(app/transcribe.py), summarise the transcript (app/call_summary.py), and persist
the result onto the proc.customer_call row. summary_status tracks progress so the
CRM UI can show "running" / "done" / "error" without a job table.

Designed to run in a background thread (the web request returns immediately) —
it owns the row's summary_status for the duration and always leaves it in a
terminal state ('done' or 'error'). It never raises to its caller; failures are
recorded on the row.
"""
from __future__ import annotations

import logging

try:
    from app import transcribe as _transcribe
    from app import call_summary as _summary
except ImportError:  # run with --app-dir=app
    import transcribe as _transcribe          # type: ignore
    import call_summary as _summary            # type: ignore

log = logging.getLogger("telephony.summary")


def feature_configured() -> bool:
    """True when both halves are set up (STT backend + Claude key)."""
    return _transcribe.backend_configured() and _summary.api_key_present()


def _compose(summary: str, action_items: list[str]) -> str:
    if not action_items:
        return summary
    bullets = "\n".join(f"• {a}" for a in action_items)
    return f"{summary}\n\n{bullets}"


def _load_call(c, call_id: int) -> dict | None:
    c.execute("""
        SELECT k.id, k.recording_path, k.external_number, k.direction,
               p.full_name AS customer_name, p.company AS customer_company
          FROM proc.customer_call k
          LEFT JOIN proc.customer_profile p ON p.user_id = k.user_id
         WHERE k.id = %s""", (call_id,))
    return c.fetchone()


def _set_status(cursor, call_id: int, status: str, error: str | None = None) -> None:
    with cursor() as c:
        c.execute("UPDATE proc.customer_call "
                  "SET summary_status = %s, summary_error = %s WHERE id = %s",
                  (status, error, call_id))


def run_summary(cursor, call_id: int, recording_path: str | None = None) -> dict:
    """Transcribe + summarise one call, in-process. Returns a result dict
    ({"ok": True, ...} or {"ok": False, "error": ...}); never raises."""
    try:
        with cursor() as c:
            row = _load_call(c, call_id)
        if not row:
            return {"ok": False, "error": "call not found"}
        path = recording_path or row.get("recording_path")
        if not path:
            _set_status(cursor, call_id, "error", "no recording on this call")
            return {"ok": False, "error": "no recording"}

        _set_status(cursor, call_id, "running")

        # 1) speech-to-text
        transcript = _transcribe.transcribe(path)
        with cursor() as c:
            c.execute("UPDATE proc.customer_call "
                      "SET transcript = %s, transcribed_at = now() WHERE id = %s",
                      (transcript, call_id))

        # 2) summarise
        context = {"number": row.get("external_number"),
                   "name": row.get("customer_name"),
                   "company": row.get("customer_company"),
                   "direction": row.get("direction")}
        result = _summary.summarize_transcript(transcript, context=context)
        summary_text = _compose(result["summary"], result.get("action_items") or [])

        with cursor() as c:
            c.execute("""UPDATE proc.customer_call
                            SET summary = %s, summary_model = %s,
                                summarized_at = now(),
                                summary_status = 'done', summary_error = NULL
                          WHERE id = %s""",
                      (summary_text, result.get("model"), call_id))
        log.info("summarised call %s (%d chars transcript)", call_id, len(transcript))
        return {"ok": True, "call_id": call_id}
    except (_transcribe.TranscribeError, _summary.SummaryError) as e:
        log.warning("summary failed for call %s: %s", call_id, e)
        _set_status(cursor, call_id, "error", str(e))
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — background thread must not die silently
        log.exception("unexpected summary failure for call %s", call_id)
        _set_status(cursor, call_id, "error", f"unexpected error: {e}")
        return {"ok": False, "error": str(e)}
