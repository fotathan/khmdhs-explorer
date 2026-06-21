\timing on

\set vat '''082525697'''

-- by_type
EXPLAIN (ANALYZE, BUFFERS)
SELECT a.type, count(*) AS n,
       coalesce(sum(coalesce(ao.awarded_value_with_vat,
              proc.resolved_value(a.adam, a.total_cost_with_vat))),0) AS total_value
FROM proc.act_operator ao
JOIN proc.procurement_act a ON a.adam = ao.adam
WHERE ao.operator_id = (SELECT operator_id FROM proc.economic_operator WHERE vat_number=:vat)
GROUP BY a.type ORDER BY a.type;

-- top_buyers
EXPLAIN (ANALYZE, BUFFERS)
SELECT auth.org_id, auth.name, count(DISTINCT a.adam) AS n_acts,
       coalesce(sum(coalesce(ao.awarded_value_with_vat,
              proc.resolved_value(a.adam, a.total_cost_with_vat))),0) AS total_value
FROM proc.act_operator ao
JOIN proc.procurement_act a ON a.adam = ao.adam
LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
WHERE ao.operator_id = (SELECT operator_id FROM proc.economic_operator WHERE vat_number=:vat)
  AND a.type='contract'
GROUP BY auth.org_id, auth.name
ORDER BY total_value DESC NULLS LAST LIMIT 10;

-- top_cpv (usually the heaviest)
EXPLAIN (ANALYZE, BUFFERS)
WITH agg AS (
  SELECT substr(oc.cpv_code,1,2) AS division, count(DISTINCT a.adam) AS n_acts,
         coalesce(sum(coalesce(ao.awarded_value_with_vat,
                proc.resolved_value(a.adam, a.total_cost_with_vat))),0) AS total_value
  FROM proc.act_operator ao
  JOIN proc.procurement_act a ON a.adam = ao.adam
  JOIN proc.act_object_detail od ON od.adam = a.adam
  JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
  WHERE ao.operator_id = (SELECT operator_id FROM proc.economic_operator WHERE vat_number=:vat)
    AND a.type='contract'
  GROUP BY substr(oc.cpv_code,1,2)
)
SELECT agg.division, agg.n_acts, agg.total_value,
  (SELECT description FROM proc.cpv_code WHERE cpv_code LIKE agg.division||'000000-_' LIMIT 1) AS label
FROM agg ORDER BY agg.total_value DESC NULLS LAST LIMIT 8;

-- paginated page (first page)
EXPLAIN (ANALYZE, BUFFERS)
SELECT a.adam, a.type, a.title, a.signed_date, a.submission_date, a.total_cost_with_vat
FROM proc.act_operator ao
JOIN proc.procurement_act a ON a.adam = ao.adam
WHERE ao.operator_id = (SELECT operator_id FROM proc.economic_operator WHERE vat_number=:vat)
ORDER BY a.signed_date DESC NULLS LAST
LIMIT 50 OFFSET 0;