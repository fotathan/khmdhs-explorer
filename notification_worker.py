#!/usr/bin/env python3
"""Evaluate mobile alerts and, when explicitly enabled, deliver them."""
from __future__ import annotations

import os
import sys
import time


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(
        os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None,
        row_factory=dict_row)


def _run() -> dict:
    from app.notification_worker import run_once

    with _connect() as connection:
        return run_once(connection.cursor())


def _summary(result: dict) -> str:
    evaluation = result["evaluation"]
    submission = result["submission"]
    receipts = result["receipts"]
    return (
        f"users {evaluation['users']} · subscriptions {evaluation['subscriptions']} · "
        f"events {evaluation['events']} · queued {evaluation['deliveries']} · "
        f"expired deliveries {result['expired']} · "
        f"evaluation errors {evaluation['errors']} · submitted {submission['accepted']} · "
        f"submission retries {submission['retry']} · receipts accepted {receipts['accepted']} · "
        f"receipt pending {receipts['pending']} · rejected "
        f"{submission['rejected'] + receipts['rejected']}"
    )


def _close_app_pool() -> None:
    """Close the web pool if deadline matching imported app.main."""
    module = sys.modules.get("app.main")
    if module is not None:
        try:
            module._pool.close()
        except Exception:  # best effort during process shutdown
            pass


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("notification_worker: DATABASE_URL is not set")
    once = (os.environ.get("PUSH_WORKER_ONCE") or "0").lower() in {
        "1", "true", "yes", "on"}
    poll = max(10, int(os.environ.get("PUSH_WORKER_POLL_SECONDS", "60")))
    while True:
        started = time.monotonic()
        try:
            result = _run()
            print(f"notification_worker: {_summary(result)}", flush=True)
        except Exception as exc:  # no record or payload data in process logs
            print(f"notification_worker: failed: {type(exc).__name__}", flush=True)
            if once:
                _close_app_pool()
                raise SystemExit(1) from exc
        if once:
            _close_app_pool()
            return
        time.sleep(max(1, poll - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
