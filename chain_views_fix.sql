-- ============================================================================
-- Fix: rebuild the lifecycle chain views so their columns match what the app
-- expects (notably v_act_chain.via). Run once on any database where the act
-- detail page errors with: column v.via does not exist.
--
--   docker exec -i khmdhs-pg psql -U postgres -d procurement < chain_views_fix.sql
--   # or against Supabase:
--   psql "$REMOTE" -f chain_views_fix.sql
--
-- DROP ... CASCADE forces a clean recreate even if an older, incompatible
-- definition is already present (CREATE OR REPLACE refuses to change column
-- shape). Nothing else depends on these views, so CASCADE is safe here.
-- ============================================================================

DROP VIEW IF EXISTS proc.v_act_chain CASCADE;
DROP VIEW IF EXISTS proc.v_act_chain_any CASCADE;

CREATE VIEW proc.v_act_chain AS
WITH RECURSIVE chain AS (
    SELECT
        source_adam               AS root,
        source_adam               AS adam,
        0                         AS depth,
        ARRAY[source_adam]        AS path,
        NULL::text                AS via
    FROM proc.act_link
    UNION ALL
    SELECT
        c.root,
        l.target_adam,
        c.depth + 1,
        c.path || l.target_adam,
        l.relation::text
    FROM chain c
    JOIN proc.act_link l ON l.source_adam = c.adam
    WHERE c.depth < 12
      AND NOT (l.target_adam = ANY(c.path))      -- cycle guard
      AND l.relation IN (
            'request_to_notice','request_to_auction','request_to_contract',
            'request_to_payment',
            'notice_to_auction',
            'auction_to_contract','auction_to_payment',
            'contract_to_payment',
            'contract_next'
      )
)
SELECT DISTINCT root, adam, depth, via, path FROM chain;

CREATE VIEW proc.v_act_chain_any AS
WITH RECURSIVE chain AS (
    SELECT source_adam AS root, source_adam AS adam, 0 AS depth,
           ARRAY[source_adam] AS path
    FROM proc.act_link
    UNION ALL
    SELECT c.root, l.target_adam, c.depth + 1, c.path || l.target_adam
    FROM chain c
    JOIN proc.act_link l ON l.source_adam = c.adam
    WHERE c.depth < 12
      AND NOT (l.target_adam = ANY(c.path))
)
SELECT DISTINCT root, adam, depth, path FROM chain;
