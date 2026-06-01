-- ============================================================================
-- Migration: normalized procedure-type "family".
-- Run once:  psql "$REMOTE" -f procedure_family_migration.sql
--            psql "$LOCAL"  -f procedure_family_migration.sql
--
-- PROBLEM: proc.procurement_act.procedure_type_code is inconsistent at the
-- source — some rows hold a full Greek description ("Απευθείας ανάθεση
-- (αρ.118/αρ. 328)"), others hold a bare numeric code ("6"). The same real
-- procedure appears under several spellings + a code. This makes the column
-- useless as a filter facet (duplicates, bare numbers).
--
-- APPROACH (overlay-style, harvested data preserved):
--   * procedure_type_code is NEVER modified — it keeps exactly what KHMDHS sent,
--     so the detail page can show the verbatim value and re-imports are fine.
--   * a DERIVED column procedure_family holds a clean, grouped label computed by
--     proc.compute_procedure_family(). The filter/dropdown use this.
--   * the function is the single source of the mapping, so re-normalising new
--     rows after a backfill is just one UPDATE (folded into refresh_analytics).
--
-- Numeric codes are mapped to their family via the known KHMDHS code meanings;
-- text variants are grouped by their procedure family. Codes 9 and 16 have no
-- known meaning and are deliberately bucketed as 'Άλλο / Άγνωστο' rather than
-- guessed.
-- ============================================================================

CREATE OR REPLACE FUNCTION proc.compute_procedure_family(raw text)
RETURNS text AS $$
DECLARE
    s text := lower(coalesce(raw, ''));
BEGIN
    IF raw IS NULL OR btrim(raw) = '' THEN
        RETURN NULL;
    END IF;

    -- ---- numeric codes (map to the same families as the text variants) ----
    CASE btrim(raw)
        WHEN '1'  THEN RETURN 'Ανοιχτή διαδικασία';
        WHEN '2'  THEN RETURN 'Κλειστή διαδικασία';
        WHEN '4'  THEN RETURN 'Ανταγωνιστικός διάλογος';
        WHEN '6'  THEN RETURN 'Απευθείας ανάθεση';
        WHEN '7'  THEN RETURN 'Ανταγωνιστική διαδικασία με διαπραγμάτευση';
        WHEN '11' THEN RETURN 'Σύμπραξη καινοτομίας';
        WHEN '12' THEN RETURN 'Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση';
        WHEN '13' THEN RETURN 'Διαπραγμάτευση με προηγούμενη προκήρυξη';
        WHEN '18' THEN RETURN 'Διαδικασία άρθρου 128';
        WHEN '9'  THEN RETURN 'Άλλο / Άγνωστο';
        WHEN '16' THEN RETURN 'Άλλο / Άγνωστο';
        ELSE
            -- not a bare code we know; fall through to text matching
    END CASE;

    -- ---- text variants: group by distinctive substring ----
    -- order matters: check more specific families before generic ones.
    IF s LIKE '%απευθείας%' THEN
        RETURN 'Απευθείας ανάθεση';
    ELSIF s LIKE '%συνοπτικ%' THEN
        RETURN 'Συνοπτικός διαγωνισμός';
    ELSIF s LIKE '%ανοιχτή%' OR s LIKE '%ανοικτή%' THEN
        RETURN 'Ανοιχτή διαδικασία';
    ELSIF s LIKE '%κλειστή%' THEN
        RETURN 'Κλειστή διαδικασία';
    ELSIF s LIKE '%ανταγωνιστικός διάλογος%' THEN
        RETURN 'Ανταγωνιστικός διάλογος';
    ELSIF s LIKE '%ανταγωνιστική%διαπραγμάτευση%' THEN
        RETURN 'Ανταγωνιστική διαδικασία με διαπραγμάτευση';
    ELSIF s LIKE '%με προηγούμενη προκήρυξη%' OR s LIKE '%αρ.266%' OR s LIKE '%αρ. 266%' THEN
        RETURN 'Διαπραγμάτευση με προηγούμενη προκήρυξη';
    ELSIF s LIKE '%χωρίς προηγούμενη δημοσίευση%' THEN
        RETURN 'Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση';
    ELSIF s LIKE '%σύμπραξη καινοτομίας%' THEN
        RETURN 'Σύμπραξη καινοτομίας';
    ELSIF s LIKE '%άρθρου 128%' THEN
        RETURN 'Διαδικασία άρθρου 128';
    ELSIF s LIKE '%κάτω των ορίων%' THEN
        RETURN 'Διαδικασία κάτω των ορίων εκτός ν.4412/2016';
    ELSE
        RETURN 'Άλλο / Άγνωστο';
    END IF;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Add the derived column (idempotent).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS procedure_family text;

-- Populate / refresh it for all rows.
UPDATE proc.procurement_act
SET procedure_family = proc.compute_procedure_family(procedure_type_code)
WHERE procedure_family IS DISTINCT FROM
      proc.compute_procedure_family(procedure_type_code);

-- Index for fast filtering.
CREATE INDEX IF NOT EXISTS ix_act_procedure_family
    ON proc.procurement_act (procedure_family);

-- Helper to re-normalise after a backfill (call from refresh_analytics or
-- manually). Only touches rows whose family is stale.
CREATE OR REPLACE FUNCTION proc.refresh_procedure_family() RETURNS void AS $$
    UPDATE proc.procurement_act
    SET procedure_family = proc.compute_procedure_family(procedure_type_code)
    WHERE procedure_family IS DISTINCT FROM
          proc.compute_procedure_family(procedure_type_code);
$$ LANGUAGE sql;

-- If the analytics refresh function exists, extend it to also re-normalise the
-- procedure family after each backfill, so the one command keeps everything
-- current. (Recreated defensively; lists all five MVs + the family refresh.)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
               WHERE n.nspname='proc' AND p.proname='refresh_analytics') THEN
        CREATE OR REPLACE FUNCTION proc.refresh_analytics() RETURNS void AS $f$
        BEGIN
            PERFORM proc.refresh_procedure_family();
            REFRESH MATERIALIZED VIEW proc.mv_analytics_totals;
            REFRESH MATERIALIZED VIEW proc.mv_analytics_authorities;
            REFRESH MATERIALIZED VIEW proc.mv_analytics_contractors;
            REFRESH MATERIALIZED VIEW proc.mv_analytics_monthly;
            REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv;
        END;
        $f$ LANGUAGE plpgsql;
    END IF;
END $$;
