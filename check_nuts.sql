SELECT a.nuts_code AS code, coalesce(n.label, a.nuts_code) AS label, count(*) AS n
FROM proc.procurement_act a
LEFT JOIN proc.nuts_code n ON n.nuts_code = a.nuts_code
WHERE a.nuts_code IS NOT NULL
GROUP BY a.nuts_code, n.label
ORDER BY n DESC LIMIT 25;