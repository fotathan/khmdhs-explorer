#!/usr/bin/env python3
"""Scheduled incremental ingestion — meant to run as a Render Cron Job, OFF the
web process (long-running jobs shouldn't live in the web dyno).

Runs each source's watermark-based `catchup` (safe to repeat), isolated so one
source failing doesn't block the others, then optionally refreshes the analytics
materialized views. Exits non-zero if any step failed, so Render marks the run
failed.

Env:
  DATABASE_URL             required (use the OWNER url if CRON_REFRESH_ANALYTICS=1,
                           since REFRESH MATERIALIZED VIEW needs matview ownership)
  CRON_STEPS               override the default steps, ';'-separated db.py commands
                           e.g. "catchup --types notice contract;ted-catchup"
  CRON_DISABLE             comma-separated step names to skip (e.g. "diavgeia-catchup")
  CRON_REFRESH_ANALYTICS   "1" to run SELECT proc.refresh_analytics() at the end
  CRON_DRY_RUN             "1" to print the plan without running anything
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STEPS = ["catchup", "diavgeia-catchup", "ted-catchup --country GRC"]


def _steps() -> list[str]:
    raw = os.environ.get("CRON_STEPS")
    steps = [s.strip() for s in raw.split(";")] if raw else list(DEFAULT_STEPS)
    disable = {d.strip() for d in os.environ.get("CRON_DISABLE", "").split(",") if d.strip()}
    return [s for s in steps if s and s.split()[0] not in disable]


def _run(cmd_str: str) -> int:
    argv = [sys.executable, os.path.join(HERE, "db.py")] + shlex.split(cmd_str)
    t0 = time.time()
    print(f"\n=== cron: db.py {cmd_str} ===", flush=True)
    rc = subprocess.run(argv, cwd=HERE).returncode
    print(f"=== db.py {cmd_str} -> exit {rc} in {time.time() - t0:.0f}s ===", flush=True)
    return rc


def _refresh_analytics() -> int:
    try:
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                             prepare_threshold=None) as c:
            c.execute("SELECT proc.refresh_analytics()")
        print("cron: refreshed analytics matviews", flush=True)
        return 0
    except Exception as e:      # noqa: BLE001 — report, count as a failure
        print(f"cron: analytics refresh FAILED: {e}", flush=True)
        return 1


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        sys.exit("cron: DATABASE_URL not set")
    steps = _steps()
    dry = os.environ.get("CRON_DRY_RUN") == "1"
    refresh = os.environ.get("CRON_REFRESH_ANALYTICS") == "1"
    print(f"cron_catchup: {len(steps)} step(s): {steps}"
          + (" + analytics refresh" if refresh else "")
          + (" [DRY RUN]" if dry else ""), flush=True)
    if dry:
        return
    failures = sum(1 for s in steps if _run(s) != 0)
    if refresh and _refresh_analytics() != 0:
        failures += 1
    print(f"\ncron_catchup done: {len(steps)} step(s), {failures} failure(s)", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
