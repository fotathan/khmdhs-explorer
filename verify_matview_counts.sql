-- verify_matview_counts.sql
-- Run AFTER applying entity_counts_matview_migration.sql. Confirms the
-- precomputed counts match what the old live aggregation produced, and shows
-- the new query is fast.

\timing on

-- 1. Spot-check: do matview counts match a direct live count for a few
--    high-activity contractors? (Pick canonical/unmerged ones.)
SELECT mc.vat_number, mc.n_acts AS matview_acts,
       (SELECT count(*) FROM proc.act_operator ao
        JOIN proc.economic_operator eo ON eo.operator_id = ao.operator_id
        WHERE eo.vat_number = mc.vat_number) AS live_acts_unmerged
FROM proc.mv_contractor_counts mc
ORDER BY mc.n_acts DESC
LIMIT 5;
-- For UNMERGED contractors matview_acts should equal live_acts_unmerged.
-- (Merged ones legitimately differ: matview sums across members.)

-- 2. Row counts present.
SELECT 'contractor_counts' AS view, count(*) FROM proc.mv_contractor_counts
UNION ALL
SELECT 'authority_counts', count(*) FROM proc.mv_authority_counts;

-- 3. The NEW list query (default view) — should now be milliseconds.
EXPLAIN (ANALYZE, BUFFERS)
WITH canon AS (
  SELECT eo.vat_number, eo.name, eo.is_greek_vat, eo.country,
         EXISTS (SELECT 1 FROM proc.entity_member m
                 WHERE m.kind='contractor' AND m.member_key=eo.vat_number) AS is_merged
  FROM proc.economic_operator eo
  WHERE TRUE AND NOT EXISTS (
        SELECT 1 FROM proc.entity_member m
        JOIN proc.entity_group g ON g.id = m.group_id
        WHERE m.kind='contractor' AND m.member_key = eo.vat_number
          AND g.canonical_key <> eo.vat_number)
)
SELECT c.vat_number, c.name, c.is_greek_vat, c.country, c.is_merged,
       COALESCE(mc.n_acts,0) AS n_acts, COALESCE(mc.n_buyers,0) AS n_buyers
FROM canon c
LEFT JOIN proc.mv_contractor_counts mc ON mc.vat_number = c.vat_number
ORDER BY n_acts DESC NULLS LAST
LIMIT 50 OFFSET 0;
