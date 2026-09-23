#!/usr/bin/env python3
"""Purge expired native-auth/notification records and print aggregate counts.

Run this once daily after the mobile-auth migration has been applied. The
operation is idempotent and follows the Stage-2 retention contract: terminal
auth/delivery records remain available for 30 days, revoked devices and inbox
events for 90 days, and expired idempotency keys are removed after 24 hours.
"""
from __future__ import annotations

import os


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(
        os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None,
        row_factory=dict_row,
    )


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("cleanup_mobile_auth: DATABASE_URL is not set")

    from app.mobile_auth import cleanup_expired
    from app.mobile_notifications import cleanup_expired as cleanup_notifications

    with _connect() as conn:
        counts = cleanup_expired(conn.cursor())
        counts.update(cleanup_notifications(conn.cursor()))
    summary = " · ".join(f"{name} {count}" for name, count in counts.items())
    print(f"cleanup_mobile_auth: {summary}", flush=True)


if __name__ == "__main__":
    main()
