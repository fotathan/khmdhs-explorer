#!/usr/bin/env python3
"""CI guard: the migration manifest and the migration files must agree.

- Every path listed in migrations/manifest.txt exists on disk.
- Every migrations/*.sql file is listed in the manifest — so a new framework
  migration can't be silently forgotten (which is how the act_attachment drift
  slipped through). Root-level *_migration.sql are legacy/baseline and not
  required to be present as files, so only their listing→existence is checked.

Exits non-zero (fails CI) on any inconsistency.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "migrations" / "manifest.txt"

listed = []
for line in MANIFEST.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#"):
        listed.append(line)

problems = []

# 1) every listed path exists
for rel in listed:
    if not (ROOT / rel).is_file():
        problems.append(f"manifest lists a missing file: {rel}")

# 2) every migrations/*.sql is listed
listed_set = set(listed)
for f in sorted((ROOT / "migrations").glob("*.sql")):
    rel = f"migrations/{f.name}"
    if rel not in listed_set:
        problems.append(f"migration file not in manifest: {rel} "
                        f"(add it, or use migrate.py new which appends automatically)")

if problems:
    print("Migration manifest is inconsistent:")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)

print(f"Migration manifest OK: {len(listed)} entries, all present; "
      f"all migrations/*.sql listed.")
