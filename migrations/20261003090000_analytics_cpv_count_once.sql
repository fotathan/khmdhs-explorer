-- migrations/20261003090000_analytics_cpv_count_once.sql
-- /analytics "Κατηγορίες αντικειμένου (CPV)": count each value once.
--
-- Two ways the table counted the same money more than once, measured
-- 2026-10-02 on the local database:
--
-- 1. An item line carrying several CPV codes of ONE division added its cost
--    once PER CODE (the view joins items to codes, then sums). ~5% of KHMDHS
--    item lines carry more than one code; on 2026 KHMDHS notices the table
--    summed €484.0bn where each line once per division is €448.3bn (≈7%
--    double). Now: one row per (item, division) before summing. A line whose
--    codes span two divisions still shows in both. There is no honest way to
--    split its cost, and the column note already says the divisions do not add
--    up to the total.
--
-- 2. A notice and the notice that amends it both counted. KHMDHS marks the
--    amending notice (amended_adam = the notice it replaces; 5,201 locally).
--    Now a notice that a later, NOT cancelled notice amends is left out, so a
--    chain of amendments counts only its last version. A self-reference
--    (amended_adam = its own ΑΔΑΜ, 51 locally) is not an amendment. This
--    follows the amendment's value even when the amendment is the one with
--    the typo.
--
-- Also: proc.analytics_sources(), the source allowlist as one function, the
-- same definition as the Tender Service trial branch (a per-database switch
-- that is OFF unless a database sets khmdhs.single_source = 'on'; nothing in
-- production does). With it unset the list is exactly the old inline
-- ARRAY['khmdhs', 'manual']. Defined here so the trial and main share ONE
-- definition of this view.
--
-- Drop + recreate: a full scan of the act/item join, minutes on production;
-- run it on the DIRECT connection, not the pooler. No REFRESH needed: CREATE
-- MATERIALIZED VIEW populates it. Grants come back through the proc schema's
-- default privileges; a check aborts the migration if one did not. No DO
-- blocks (the Supabase editor splits them).

BEGIN;

CREATE OR REPLACE FUNCTION proc.analytics_sources()
    RETURNS text[]
    LANGUAGE sql STABLE
AS $function$
    SELECT CASE WHEN current_setting('khmdhs.single_source', true) = 'on'
                THEN ARRAY['khmdhs', 'manual', 'tsg']
                ELSE ARRAY['khmdhs', 'manual'] END;
$function$;

COMMENT ON FUNCTION proc.analytics_sources() IS
    'The data_source values /analytics counts (an allowlist; NULL counts as khmdhs). '
    'Adds tsg only where the database sets khmdhs.single_source = on (the trial).';

CREATE TEMP TABLE _mv_cpv_acl ON COMMIT DROP AS
    SELECT a.grantee, a.privilege_type
    FROM pg_class c, aclexplode(c.relacl) a
    WHERE c.oid = 'proc.mv_analytics_cpv'::regclass
      AND a.grantee <> c.relowner;

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_cpv;
CREATE MATERIALIZED VIEW proc.mv_analytics_cpv AS
    WITH items AS (
        -- DISTINCT: one row per (item line, division), however many codes of
        -- that division the line carries.
        SELECT DISTINCT a.type, a.adam, od.id AS item_id,
               substr(oc.cpv_code::text, 1, 2) AS division,
               proc.resolved_item_cost(a.adam, od.line_no, od.cost_without_vat) AS item_cost
        FROM proc.procurement_act a
        JOIN proc.act_object_detail od ON od.adam = a.adam
        JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
        WHERE (a.type = ANY (ARRAY['notice'::proc.act_type, 'contract'::proc.act_type]))
          AND NOT a.cancelled
          AND coalesce(a.data_source, 'khmdhs') = ANY (proc.analytics_sources())
          AND NOT (EXISTS (SELECT 1 FROM proc.v_act_annotation_current an
                           WHERE an.adam = a.adam AND an.flag = 'suspicious'::text))
          AND (a.type <> 'contract'::proc.act_type
               OR proc.resolved_value(a.adam, a.total_cost_with_vat) IS NULL
               OR proc.resolved_value(a.adam, a.total_cost_with_vat) <= proc.analytics_value_ceiling())
          -- A notice replaced by a later amendment counts only as that amendment.
          AND NOT (a.type = 'notice'::proc.act_type
                   AND EXISTS (SELECT 1 FROM proc.procurement_act n
                               WHERE n.amended_adam = a.adam
                                 AND n.adam <> a.adam
                                 AND n.type = 'notice'::proc.act_type
                                 AND NOT n.cancelled))
    ), agg AS (
        SELECT items.division,
               count(DISTINCT items.adam) FILTER (WHERE items.type = 'contract'::proc.act_type) AS contract_count,
               COALESCE(sum(items.item_cost) FILTER (WHERE items.type = 'contract'::proc.act_type), 0::numeric) AS contract_value,
               count(DISTINCT items.adam) FILTER (WHERE items.type = 'notice'::proc.act_type) AS notice_count,
               COALESCE(sum(items.item_cost) FILTER (WHERE items.type = 'notice'::proc.act_type), 0::numeric) AS notice_value
        FROM items
        GROUP BY items.division
    )
    SELECT division, contract_count, contract_value, notice_count, notice_value,
           (SELECT cpv_code.description FROM proc.cpv_code
            WHERE cpv_code.cpv_code::text ~~ (agg.division || '000000-_'::text)
            LIMIT 1) AS label
    FROM agg
    ORDER BY contract_value DESC;
CREATE INDEX ix_mv_cpv_cvalue ON proc.mv_analytics_cpv USING btree (contract_value DESC);

-- A grant that did not come back aborts the whole migration (division by zero).
SELECT 1 / (CASE WHEN EXISTS (
            SELECT 1 FROM _mv_cpv_acl s
            WHERE NOT EXISTS (
                SELECT 1 FROM pg_class c, aclexplode(c.relacl) a
                WHERE c.oid = 'proc.mv_analytics_cpv'::regclass
                  AND a.grantee = s.grantee AND a.privilege_type = s.privilege_type))
        THEN 0 ELSE 1 END) AS grants_restored;

COMMIT;
