-- act_cpv_migration.sql
-- ===========================================================================
-- Act-level CPV codes (editable in the authored-act form). Separate from the
-- per-line-item CPVs ΚΗΜΔΗΣ provides (proc.object_detail_cpv); this is a
-- curator-set, act-level list. Never written by the importer, so it survives
-- re-imports. cpv_code is validated against proc.cpv_code by the picker (not a
-- hard FK, to tolerate any future code-list gaps).
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f act_cpv_migration.sql
--   psql "<supabase-direct-url>" -f act_cpv_migration.sql
-- ===========================================================================

CREATE TABLE IF NOT EXISTS proc.act_cpv (
    adam     text    NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    cpv_code text    NOT NULL,
    ord      integer NOT NULL DEFAULT 0,   -- display order (insertion order)
    PRIMARY KEY (adam, cpv_code)
);

CREATE INDEX IF NOT EXISTS ix_act_cpv_code ON proc.act_cpv (cpv_code);

ANALYZE proc.act_cpv;
