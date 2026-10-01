-- competition indicator (docs/specs/competition-indicator.md, slice 1)
--
-- How many bids does a buyer usually get? KHMDHS reports bidsSubmitted on a
-- contract (stored as procurement_act.bids_submitted). These views turn it
-- into HISTOGRAMS, one row per (group, competitive?, number of bids). Medians do
-- not add up, and the authority page merges entity groups, so the app
-- (app/competition.py) sums histograms and computes exact figures from them.
--
-- Which acts count (spec §3): contracts with 1..100 bids that pass
-- proc.is_analytics_eligible (not cancelled, under the value ceiling, not
-- flagged suspicious, source in the khmdhs/manual allowlist — which keeps TED
-- and Tender Service out, since they duplicate KHMDHS awards), and that are
-- not a hidden duplicate. 0 and >100 are typing errors (max seen: 29,970).
--
-- "competitive" = anything but a direct award. A NULL procedure_family counts
-- as competitive (older acts whose code did not map; spec §3).
--
-- Rows are also split by MONTH of signature, so the app can state the period
-- the figures really come from (the months holding the central 90% of the
-- counted contracts) rather than a min..max that one mistyped date stretches
-- to 1919. The date is coalesce(contract_signed_date, signed_date): KHMDHS
-- contracts carry contract_signed_date and leave signed_date NULL. Dates
-- before 2020 or in the future (77 rows locally) are typing errors and are
-- left out.
--
-- Freshness: refreshed CONCURRENTLY by proc.refresh_analytics(), after an
-- import, like the other analytics views. Until the views exist the app shows
-- nothing (the panels answer empty), so code and migration can land in either
-- order.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them. SELECT for
-- app_runtime comes from the schema's default privileges (postgres-owned
-- relations in proc are granted to app_runtime on creation).

BEGIN;

-- 1. Per authority -----------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS proc.mv_competition_authority AS
SELECT a.authority_id,
       (a.procedure_family IS DISTINCT FROM 'Απευθείας ανάθεση') AS competitive,
       a.bids_submitted::int                                     AS bids,
       date_trunc('month', coalesce(a.contract_signed_date, a.signed_date))::date AS month,
       count(*)::int                                             AS n
FROM proc.procurement_act a
WHERE a.type = 'contract'
  AND a.authority_id IS NOT NULL
  AND a.bids_submitted BETWEEN 1 AND 100
  AND coalesce(a.contract_signed_date, a.signed_date) >= DATE '2020-01-01'
  AND coalesce(a.contract_signed_date, a.signed_date) <= current_date
  AND proc.is_analytics_eligible(a.adam, a.total_cost_with_vat, a.cancelled)
  AND NOT EXISTS (SELECT 1 FROM proc.procurement_act hid
                  WHERE hid.duplicate_of IS NOT NULL AND hid.adam = a.adam)
GROUP BY 1, 2, 3, 4
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_competition_authority
    ON proc.mv_competition_authority (authority_id, competitive, bids, month);

COMMENT ON MATERIALIZED VIEW proc.mv_competition_authority IS
  'Bid-count histogram per authority and month (competition indicator); refreshed by proc.refresh_analytics().';

-- 2. Per CPV division ---------------------------------------------------------
-- One row per (division, contract) BEFORE counting: a contract with six line
-- items in one division counts once there; a contract spanning two divisions
-- counts once in each (the rule authority_top_cpv uses).
CREATE MATERIALIZED VIEW IF NOT EXISTS proc.mv_competition_cpv AS
WITH counted AS (
    SELECT a.adam,
           (a.procedure_family IS DISTINCT FROM 'Απευθείας ανάθεση') AS competitive,
           a.bids_submitted::int                                     AS bids,
           date_trunc('month', coalesce(a.contract_signed_date, a.signed_date))::date AS month
    FROM proc.procurement_act a
    WHERE a.type = 'contract'
      AND a.bids_submitted BETWEEN 1 AND 100
      AND coalesce(a.contract_signed_date, a.signed_date) >= DATE '2020-01-01'
      AND coalesce(a.contract_signed_date, a.signed_date) <= current_date
      AND proc.is_analytics_eligible(a.adam, a.total_cost_with_vat, a.cancelled)
      AND NOT EXISTS (SELECT 1 FROM proc.procurement_act hid
                      WHERE hid.duplicate_of IS NOT NULL AND hid.adam = a.adam)
), lines AS (
    SELECT DISTINCT substr(oc.cpv_code, 1, 2) AS division, c.adam,
           c.competitive, c.bids, c.month
    FROM counted c
    JOIN proc.act_object_detail od ON od.adam = c.adam
    JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
)
SELECT division, competitive, bids, month,
       count(*)::int AS n
FROM lines
GROUP BY 1, 2, 3, 4
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_competition_cpv
    ON proc.mv_competition_cpv (division, competitive, bids, month);

COMMENT ON MATERIALIZED VIEW proc.mv_competition_cpv IS
  'Bid-count histogram per 2-digit CPV division and month (competition indicator); refreshed by proc.refresh_analytics().';

-- 3. Source monitor (spec §8) ---------------------------------------------------
-- Per month of signature: KHMDHS contracts, how many state a bid count, and
-- how many of those were dropped as 0 / >100. KHMDHS stopped filling the field
-- in February 2026; this is how an admin sees it come back.
CREATE MATERIALIZED VIEW IF NOT EXISTS proc.mv_competition_fill AS
SELECT date_trunc('month', coalesce(a.contract_signed_date, a.signed_date))::date AS month,
       count(*)::int                                                 AS n_contracts,
       count(a.bids_submitted)::int                                  AS n_filled,
       (count(*) FILTER (WHERE a.bids_submitted = 0
                            OR a.bids_submitted > 100))::int         AS n_excluded
FROM proc.procurement_act a
WHERE a.type = 'contract'
  AND coalesce(a.data_source, 'khmdhs') = 'khmdhs'
  AND coalesce(a.contract_signed_date, a.signed_date) >= DATE '2020-01-01'
  AND coalesce(a.contract_signed_date, a.signed_date) <  current_date + 1
GROUP BY 1
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_competition_fill
    ON proc.mv_competition_fill (month);

COMMENT ON MATERIALIZED VIEW proc.mv_competition_fill IS
  'Per-month fill rate of procurement_act.bids_submitted on KHMDHS contracts (admin monitor); refreshed by proc.refresh_analytics().';

-- 4. The existing refresh_analytics(), unchanged, plus the three new views ----
CREATE OR REPLACE FUNCTION proc.refresh_analytics()
 RETURNS void
 LANGUAGE plpgsql
AS $function$
BEGIN
    BEGIN
        PERFORM proc.refresh_procedure_family();
    EXCEPTION WHEN undefined_function THEN NULL;
    END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_totals;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_authorities;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_contractors;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_monthly;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_contractor_counts;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_authority_counts;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- explore overview views
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_authority;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_authority_name;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_contractor;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_contractor_name;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- act page competition panel (20260915200000_cpv_contract_wins_rollup.sql)
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_cpv_contract_wins;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- competition indicator (20261001082952_competition_indicator.sql)
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_competition_authority;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_competition_cpv;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_competition_fill;
    EXCEPTION WHEN undefined_table THEN NULL; END;
END;
$function$;

COMMIT;
