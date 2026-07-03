-- data_source_backfill_migration.sql
--
-- Set data_source='khmdhs' on harvested acts that predate the ingester writing
-- it. Until now khmdhs_ingest.upsert_act never populated data_source, so acts it
-- inserted were left NULL — invisible to the data-source filter (e.g. "ΚΗΜΔΗΣ"),
-- even though they ARE KHMDHS acts. (Diavgeia acts already set data_source in
-- projection; authored acts are left untouched here.)
--
-- Pair with the khmdhs_ingest.py fix that sets data_source going forward. Safe
-- and idempotent; re-runnable. Apply to any DB that has NULL-source imports.

BEGIN;

UPDATE proc.procurement_act
   SET data_source = 'khmdhs'
 WHERE data_source IS NULL
   AND origin = 'import';

COMMIT;
