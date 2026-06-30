-- ============================================================================
-- reset_sequences.sql — re-sync every serial-backed sequence in schema `proc`
-- to MAX(column)+1.
--
-- Why: when a database is seeded by a bulk load / dump-restore that copies rows
-- WITH their explicit ids, the owning sequences are NOT advanced. nextval then
-- returns ids that already exist → "duplicate key value violates unique
-- constraint ..._pkey". Symptom seen on Supabase prod during diavgeia-backfill:
--   economic_operator_pkey (operator_id), act_object_detail_pkey (id), etc.
--
-- Safe + idempotent: only repositions sequences, never touches data. Run once
-- against the affected DB:
--   psql "$DATABASE_URL" -f reset_sequences.sql
-- ============================================================================

DO $$
DECLARE
    r       record;
    newval  bigint;
BEGIN
    FOR r IN
        SELECT n.nspname AS sch, t.relname AS tbl, a.attname AS col,
               pg_get_serial_sequence(n.nspname || '.' || t.relname, a.attname) AS seq
        FROM pg_class t
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname = 'proc' AND t.relkind = 'r'
          AND pg_get_serial_sequence(n.nspname || '.' || t.relname, a.attname) IS NOT NULL
    LOOP
        EXECUTE format('SELECT COALESCE(max(%I), 0) + 1 FROM %I.%I', r.col, r.sch, r.tbl)
            INTO newval;
        -- is_called = false → the next nextval() returns exactly `newval` (= max+1)
        EXECUTE format('SELECT setval(%L, %s, false)', r.seq, newval);
        RAISE NOTICE 'reset %  ->  %', r.seq, newval;
    END LOOP;
END $$;
