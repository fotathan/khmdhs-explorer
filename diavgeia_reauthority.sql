-- ============================================================================
-- diavgeia_reauthority.sql — clear all Diavgeia↔authority links so they can be
-- rebuilt with the improved matching (unambiguous ΑΦΜ → authority NAME).
--
-- Why: the first resolve matched only by ΑΦΜ, which is wrong/ambiguous for the
-- many organizations that share placeholder VATs (e.g. several ministries all
-- use 011111111), and catchup never resolved its new orgs at all — so most
-- Diavgeia acts showed no awarding authority. After this reset, run:
--     python3 db.py diavgeia-resolve     # re-resolve every org (API lookups)
--     python3 db.py diavgeia-project     # copy authority_id into procurement_act
--
-- Idempotent. Apply:  psql "$DATABASE_URL" -f diavgeia_reauthority.sql
-- ============================================================================

BEGIN;
SET search_path TO proc, public;

-- 1. Detach Diavgeia acts from their authorities and clear the decision links,
--    so the synthetic authorities below become unreferenced.
UPDATE proc.procurement_act SET authority_id = NULL WHERE data_source = 'diavgeia';
UPDATE proc.diavgeia_decision SET authority_id = NULL;

-- 2. Un-merge real (KHMDHS) authorities that were linked to a Diavgeia org.
UPDATE proc.authority
   SET diavgeia_org_uid = NULL,
       source = CASE WHEN source = 'merged' THEN 'khmdhs' ELSE source END
 WHERE diavgeia_org_uid IS NOT NULL AND source <> 'diavgeia';

-- 3. Drop the synthetic Diavgeia-only authorities (now unreferenced); resolve
--    will recreate only the ones still needed after name matching.
DELETE FROM proc.authority WHERE source = 'diavgeia';

COMMIT;
