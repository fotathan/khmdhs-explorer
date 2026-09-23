-- migrations/20260915090100_tender_service_analytics_exclusion.sql
-- Keep Tender Service acts OUT of /analytics — and every source after it.
--
-- Projected sources show in lists, search and detail, but /analytics counts
-- only the registries it is built on. Until now that was a DENYLIST
-- (data_source <> ALL ('diavgeia','ted')), so each new source was counted by
-- default until someone remembered to add it. That is how 182 local Tender
-- Service preview acts entered the local dashboards. This turns it into an
-- ALLOWLIST: KHMDHS, hand-entered acts ('manual') and the few rows with no
-- source at all. Same result for every row that exists today; a fifth source
-- is excluded until it is deliberately let in.
--
-- 1. proc.is_analytics_eligible — the shared predicate behind four matviews.
-- 2. proc.mv_analytics_cpv — carries its own inline filter, so it is dropped
--    and recreated (a full scan of the act/item join: minutes on production,
--    run it on the DIRECT connection, not the pooler). Its grants are saved and
--    re-applied, because DROP takes them with it.
--
-- No REFRESH of the other four: on a database with no 'tsg' acts they are
-- already right. On one that holds preview acts, run
--   SELECT proc.refresh_analytics();
-- afterwards.

BEGIN;

CREATE OR REPLACE FUNCTION proc.is_analytics_eligible(
        p_adam text, p_value numeric, p_cancelled boolean)
    RETURNS boolean
    LANGUAGE sql STABLE
AS $function$
    SELECT (NOT coalesce(p_cancelled, false))
       AND (proc.resolved_value(p_adam, p_value) IS NULL
            OR proc.resolved_value(p_adam, p_value) <= proc.analytics_value_ceiling())
       AND NOT EXISTS (
            SELECT 1 FROM proc.v_act_annotation_current a
            WHERE a.adam = p_adam AND a.flag = 'suspicious')
       AND NOT EXISTS (
            SELECT 1 FROM proc.procurement_act pa
            WHERE pa.adam = p_adam
              AND coalesce(pa.data_source, 'khmdhs') <> ALL (ARRAY['khmdhs', 'manual']));
$function$;

CREATE TEMP TABLE _mv_cpv_acl ON COMMIT DROP AS
    SELECT a.grantee, a.privilege_type
    FROM pg_class c, aclexplode(c.relacl) a
    WHERE c.oid = 'proc.mv_analytics_cpv'::regclass
      AND a.grantee <> c.relowner;

DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_cpv;
CREATE MATERIALIZED VIEW proc.mv_analytics_cpv AS
    WITH items AS (
        SELECT a.type, a.adam,
               substr(oc.cpv_code::text, 1, 2) AS division,
               proc.resolved_item_cost(a.adam, od.line_no, od.cost_without_vat) AS item_cost
        FROM proc.procurement_act a
        JOIN proc.act_object_detail od ON od.adam = a.adam
        JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
        WHERE (a.type = ANY (ARRAY['notice'::proc.act_type, 'contract'::proc.act_type]))
          AND NOT a.cancelled
          AND coalesce(a.data_source, 'khmdhs') = ANY (ARRAY['khmdhs', 'manual'])
          AND NOT (EXISTS (SELECT 1 FROM proc.v_act_annotation_current an
                           WHERE an.adam = a.adam AND an.flag = 'suspicious'::text))
          AND (a.type <> 'contract'::proc.act_type
               OR proc.resolved_value(a.adam, a.total_cost_with_vat) IS NULL
               OR proc.resolved_value(a.adam, a.total_cost_with_vat) <= proc.analytics_value_ceiling())
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

DO $$
DECLARE g record;
BEGIN
    FOR g IN SELECT grantee, privilege_type FROM _mv_cpv_acl LOOP
        EXECUTE format('GRANT %s ON proc.mv_analytics_cpv TO %s', g.privilege_type,
                       CASE WHEN g.grantee = 0 THEN 'PUBLIC'
                            ELSE quote_ident(pg_get_userbyid(g.grantee)) END);
    END LOOP;
END $$;

COMMIT;
