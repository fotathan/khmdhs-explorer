# Performance fixes — round 1

Targets the two biggest costs found by EXPLAIN ANALYZE on your 2.67M-row
`procurement_act` table. Both are on the hottest page (the main search/list).

## What the diagnosis showed

| Operation (every list-page load)        | Before        | Cause |
|-----------------------------------------|---------------|-------|
| Default sort by `submission_date DESC`  | **~1.5 s**    | No index → full parallel seq scan + top-N sort of 2.67M rows |
| Results counter (`count + sum value`)   | **~7.9 s**    | `sum(resolved_value(...))` fired a subquery per row × 2.67M |
| Filtered search (type=notice)           | ~105 ms       | Tolerable, improves with the new index too |

The 7.9 s counter was the dominant cost — and almost nobody reads that number.

## The two fixes

### 1. Index migration — `perf_submission_sort_migration.sql`

Adds the missing `submission_date` indexes (plain + composite with `type` and
`authority_id`) plus the deadline-sort index. Turns the default-sort full scan
into an index read.

Run it (local AND Supabase). Uses `CREATE INDEX CONCURRENTLY` so it won't lock
the table while building — but that means **run it directly with psql, not
inside a transaction**:

    psql "$DATABASE_URL" -f perf_submission_sort_migration.sql

On Supabase use the **direct** connection (port 5432), not the pooler (6543) —
CONCURRENTLY + pooler can misbehave. It'll take a little while on 2.67M rows;
that's normal, and reads/writes keep working during the build.

### 2. Counter rework — `main.py` (run_search)

Replaces the per-row `resolved_value()` sum with arithmetic that's provably
identical to the cent:

    corrected_total = sum(total_cost_with_vat)                  -- cheap column scan
                    + sum(corrected_value - total_cost_with_vat) -- only the few overrides

and, when there are NO filters, uses Postgres' instant row estimate
(`pg_class.reltuples`) instead of an exact `count(*)` over 2.67M rows. Filtered
searches still get exact counts (smaller sets, fast). Verified algebraically
equal to the old figure; the callers already read `agg["n"]` / `agg["total_value"]`
so nothing else changes.

Replace `app/main.py` with the supplied `main_perf.py` (renamed), or apply just
the new `run_search`.

## Expected result

- Unfiltered default page: ~9.4 s of DB work (1.5 + 7.9) → well under 1 s
  (index read for the sort; reltuples for the count; ~0.4 s base-sum scan).
- Filtered pages: snappier, and the counter no longer does per-row subqueries.

## After installing

Run ANALYZE if the migration's own ANALYZE didn't (it's included):

    psql "$DATABASE_URL" -c "ANALYZE proc.procurement_act;"

Then re-run the default page and a filtered page and confirm the feel. If you
want hard numbers, re-run the two EXPLAINs from perf_diagnose.sql (#2 and #3) —
#2 should now show an Index Scan, #3 should drop from ~7.9 s to sub-second.

## Not done yet (next round, if you want)

- **`/explore` and `/analytics` aggregations** also call `resolved_value()`
  per row (main.py lines ~1188, 1322, 1338). Same fix applies; they're less hot
  than the main list, so deferred until you confirm round 1.
- **Unfiltered base-sum caching**: the ~0.4 s base-value scan repeats every
  unfiltered load. A short-TTL cache (or a stored running total) would make it
  near-zero — worth it only at real concurrency.
- **Index bloat**: `ix_act_raw_gin` (GIN on raw_json) is a big share of the
  3.4 GB of indexes. If you never do full-text search on raw_json, dropping it
  reclaims space and speeds writes. Confirm before dropping.
- **Infra for concurrency**: Supabase compute tier and a real connection pooler
  (PgBouncer/Supavisor) matter more than query tuning once you have many
  simultaneous users. Separable from this code work.
