-- migrations/20261007120000_kad_cpv_map.sql
-- What a company with no award history probably sells, learned from the
-- companies that DO have one. docs/specs/crm-brief-kad.md.
--
-- 1. proc.kad_cpv_map: for a ΚΑΔ (at 8, 6 or 4 digits), the CPV prefixes that
--    firms registered under it actually win. Learned from the award ledger by
--    kad_cpv_map.py build, never typed in. Counted in FIRMS, not awards, so one
--    big supplier cannot define a ΚΑΔ, and filtered by lift, so codes every
--    firm wins (office supplies) do not dominate. ΚΑΔ 2026 is NACE Rev. 2.1
--    based; the EU CPV↔CPA tables are on the 2008 classifications, and 70% of
--    matched firms are wholesalers (46.xx) that those tables would map to
--    "wholesale trade services" rather than to the goods they sell.
--
-- 2. proc.operator_kad: one row per contractor ΑΦΜ that went into the build —
--    its PRIMARY ΚΑΔ and the NUTS-2 region of its registered seat. Prod cannot
--    hold the raw registry records the build reads (up to 22 KB each, 500 MB
--    free tier), so the build ships this slim copy instead. It is what
--    "companies with the same ΚΑΔ that already win" is read from.
--
-- 3. proc.kad_cpv_build: one row per build, so the page can say what the
--    estimate rests on ("from 24,310 contractors, built 08/10/2026").
--
-- All three are REPLACED by each build (kad_cpv_map.py build / push), never
-- edited by hand. Core migration: no tsg_* tables, no DO $$ blocks.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.kad_cpv_map (
    kad_prefix  text    NOT NULL,   -- 4, 6 or 8 digits of a ΚΑΔ code
    cpv_prefix  text    NOT NULL,   -- 2 or 4 digits, or a full code with check digit
    n_firms     integer NOT NULL,   -- firms under this ΚΑΔ that won in this CPV
    kad_firms   integer NOT NULL,   -- firms under this ΚΑΔ that won anything
    support     real    NOT NULL,   -- n_firms / kad_firms
    lift        real    NOT NULL,   -- support / share of ALL firms winning in it
    PRIMARY KEY (kad_prefix, cpv_prefix)
);

CREATE TABLE IF NOT EXISTS proc.operator_kad (
    afm     text PRIMARY KEY,       -- 9 digits, no EL prefix
    kad     text NOT NULL,          -- primary ΚΑΔ, 8 digits
    nuts2   text                    -- 'EL52' — from the registered postal code
);

-- Peers are looked up by ΚΑΔ prefix (8, then 6, then 4 digits).
CREATE INDEX IF NOT EXISTS ix_operator_kad_kad
    ON proc.operator_kad (kad text_pattern_ops);

CREATE TABLE IF NOT EXISTS proc.kad_cpv_build (
    id            serial PRIMARY KEY,
    built_at      timestamptz NOT NULL DEFAULT now(),
    n_firms       integer NOT NULL,   -- contractors with a ΚΑΔ and an award
    n_pairs       integer NOT NULL,   -- rows written to kad_cpv_map
    window_years  integer NOT NULL,
    params        jsonb   NOT NULL DEFAULT '{}'::jsonb
);

COMMIT;
