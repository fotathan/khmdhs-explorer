-- ============================================================================
-- diavgeia_analytics_exclusion.sql — keep Diavgeia acts OUT of /analytics.
--
-- Diavgeia decisions are projected into proc.procurement_act (data_source =
-- 'diavgeia') so they show in lists / search / detail, but the same real-world
-- procurement is often also in KHMDHS — counting both would double-count the
-- analytics dashboard. So we exclude data_source='diavgeia' from every
-- proc.mv_analytics_* aggregate.
--
-- Four of the five MVs gate on proc.is_analytics_eligible(adam, value,
-- cancelled); adding the Diavgeia check there covers them all at once (the
-- function already does an adam-keyed lookup, so the cost shape is unchanged).
-- mv_analytics_cpv is item-based and doesn't use that function, so it is
-- recreated with an explicit filter.
--
-- Idempotent. Apply by hand, then it REFRESHes all five MVs at the end:
--   psql "$DATABASE_URL" -f diavgeia_analytics_exclusion.sql
-- ============================================================================

BEGIN;
SET search_path TO proc, public;

-- 1. Shared eligibility predicate (used by totals / authorities / contractors /
--    monthly) — add the Diavgeia exclusion.
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
            WHERE pa.adam = p_adam AND pa.data_source = 'diavgeia');
$function$;

-- 2. By-CPV MV (item-based; doesn't use the function) — recreate with the
--    explicit data_source filter in the items CTE. Definition mirrors the live
--    one plus `AND a.data_source IS DISTINCT FROM 'diavgeia'`.
DROP MATERIALIZED VIEW IF EXISTS proc.mv_analytics_cpv CASCADE;
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
          AND a.data_source IS DISTINCT FROM 'diavgeia'
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

COMMIT;

-- 3. Refresh all five so the dashboard reflects the exclusion immediately.
REFRESH MATERIALIZED VIEW proc.mv_analytics_totals;
REFRESH MATERIALIZED VIEW proc.mv_analytics_authorities;
REFRESH MATERIALIZED VIEW proc.mv_analytics_contractors;
REFRESH MATERIALIZED VIEW proc.mv_analytics_monthly;
REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv;
