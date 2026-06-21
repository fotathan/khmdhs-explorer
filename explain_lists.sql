\timing on

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
),
keymap AS (
  SELECT eo.vat_number AS member_vat, eo.operator_id,
         COALESCE(g.canonical_key, eo.vat_number) AS canon_vat
  FROM proc.economic_operator eo
  LEFT JOIN proc.entity_member m ON m.kind='contractor' AND m.member_key = eo.vat_number
  LEFT JOIN proc.entity_group g ON g.id = m.group_id
),
counts AS (
  SELECT k.canon_vat, count(ao.adam) AS n_acts,
         count(DISTINCT a.authority_id) AS n_buyers
  FROM keymap k
  JOIN proc.act_operator ao ON ao.operator_id = k.operator_id
  LEFT JOIN proc.procurement_act a ON a.adam = ao.adam
  WHERE k.canon_vat IN (SELECT vat_number FROM canon)
  GROUP BY k.canon_vat
)
SELECT c.vat_number, c.name, c.is_greek_vat, c.country, c.is_merged,
       COALESCE(ct.n_acts,0) AS n_acts, COALESCE(ct.n_buyers,0) AS n_buyers
FROM canon c LEFT JOIN counts ct ON ct.canon_vat = c.vat_number
ORDER BY n_acts DESC NULLS LAST
LIMIT 50 OFFSET 0;