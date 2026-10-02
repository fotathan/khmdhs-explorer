-- migrations/20261002220000_analytics_sources_switch.sql
-- The /analytics source ALLOWLIST becomes one function, with a per-DATABASE
-- switch that adds Tender Service for the single-source trial
-- (docs/specs/tender-service-single-source.md §7).
--
-- Still an allowlist (CLAUDE.md: never a list of excluded sources):
--   proc.analytics_sources() = {khmdhs, manual}
--                              + {tsg} only where khmdhs.single_source = 'on'.
-- The setting is NOT set by this migration and defaults to off, so on every
-- database that does not set it — local `procurement`, Supabase — every
-- result is identical to before. There Tender Service still duplicates the
-- KHMDHS awards and must stay out. The trial database alone turns it on:
--   ALTER DATABASE procurement_tsg SET khmdhs.single_source = 'on';
-- then, in a NEW session (the setting applies at connect):
--   SELECT proc.refresh_analytics();
--
-- 1. proc.analytics_sources() — the list.
-- 2. proc.is_analytics_eligible — reads it (behind five matviews).
-- 3. proc.mv_analytics_cpv — carries its own inline filter, so it is dropped
--    and recreated reading the list. Grants are saved and re-applied, because
--    DROP takes them with it (same pattern as 20260915090100). A full scan of
--    the act/item join: minutes on production, use the DIRECT connection.
--
-- The competition matviews keep their own 'khmdhs' filter on purpose: they
-- count bids_submitted, which Tender Service does not fill (spec §1c).
-- No DO blocks (the Supabase editor splits them).

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
              AND coalesce(pa.data_source, 'khmdhs') <> ALL (proc.analytics_sources()));
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
          AND coalesce(a.data_source, 'khmdhs') = ANY (proc.analytics_sources())
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

-- Grants: the proc schema's default privileges re-grant a new matview to
-- app_runtime by themselves. Checked rather than re-granted in a DO block: a
-- grant that did not come back aborts the whole migration (division by zero).
SELECT 1 / (CASE WHEN EXISTS (
            SELECT 1 FROM _mv_cpv_acl s
            WHERE NOT EXISTS (
                SELECT 1 FROM pg_class c, aclexplode(c.relacl) a
                WHERE c.oid = 'proc.mv_analytics_cpv'::regclass
                  AND a.grantee = s.grantee AND a.privilege_type = s.privilege_type))
        THEN 0 ELSE 1 END) AS grants_restored;

COMMIT;
