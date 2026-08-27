#!/usr/bin/env python3
"""Send the digest emails that have come due, then exit.

The scheduling entry point for anything that can run a command on a timer: a
Render Cron Job, a system crontab, a GitHub Actions schedule, or you at a
terminal. Run it every few minutes — it only sends what is actually due (see
app/digests.is_due), so re-running is safe and a missed run is absorbed by the
next one.

The alternative is DIGEST_SCHEDULER=1, which runs the same sweep in a thread
inside the web process. Use one or the other, not both: two runners would race
for the same subscriptions.

Env:
  DATABASE_URL     required
  EMAIL_BACKEND    console (default) | memory | file | smtp — see app/mailer.py
  APP_BASE_URL     absolute base for the links in the email
  DIGEST_FORCE     "1" to ignore the schedules and send every active subscription
                   (the CLI equivalent of the admin's "force all" button)
  DIGEST_LIMIT     stop after N subscriptions (a safety valve while testing)
  DIGEST_DRY_RUN   "1" to print what WOULD be sent and send nothing

Exits non-zero if any subscription errored, so a cron runner marks the run
failed instead of failing quietly.
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _connect():
    import psycopg
    from psycopg.rows import dict_row
    # prepare_threshold=None for the same reason app/main.py disables prepared
    # statements: a pooler can route consecutive queries to different backends.
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                           prepare_threshold=None, row_factory=dict_row)


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("cron_digests: DATABASE_URL is not set", flush=True)
        raise SystemExit(2)

    from app import digests as _digests

    force = os.environ.get("DIGEST_FORCE") == "1"
    dry = os.environ.get("DIGEST_DRY_RUN") == "1"
    limit = int(os.environ.get("DIGEST_LIMIT") or 0) or None

    t0 = time.time()
    with _connect() as conn:
        c = conn.cursor()
        if dry:
            # Report the decision without sending: which subscriptions are due,
            # on which schedule, and how big the window is.
            import datetime as dt
            now = dt.datetime.now(dt.timezone.utc)
            n_due = 0
            for sub in _digests.active_subscriptions(c):
                sched = _digests.resolve_schedule(c, sub)
                due = force or _digests.is_due(sub, sched, now)
                n_due += bool(due)
                print(f"{'DUE ' if due else '    '} {sub['username']} / "
                      f"{sub['profile_name']} — {_digests.describe_schedule(sched)} "
                      f"— since {_digests.window_start(sub):%Y-%m-%d %H:%M}", flush=True)
            print(f"cron_digests: dry run, {n_due} due", flush=True)
            _close_app_pool()
            return
        out = _digests.run_due(c, force=force, limit=limit)

    print(f"cron_digests: checked {out['checked']} · sent {out['sent']} · "
          f"empty {out['empty']} · errors {out['errors']} · "
          f"skipped {out['skipped']} in {time.time() - t0:.1f}s", flush=True)
    for r in out["results"]:
        if r["status"] == "error":
            print(f"  ERROR {r.get('username')}/{r.get('profile')}: "
                  f"{r.get('error')}", flush=True)
    _close_app_pool()
    if out["errors"]:
        raise SystemExit(1)


def _close_app_pool() -> None:
    """app.digests reaches into app.main for build_where, and importing that
    opens its connection pool. Close it explicitly, or the pool's maintenance
    thread is still alive at interpreter shutdown and psycopg's __del__ prints a
    PythonFinalizationError traceback over an otherwise successful run."""
    import sys
    m = sys.modules.get("app.main")
    if m is not None:
        try:
            m._pool.close()
        except Exception:      # noqa: BLE001 — best-effort on the way out
            pass


if __name__ == "__main__":
    main()
