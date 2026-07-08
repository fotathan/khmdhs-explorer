-- interconnect_migration.sql
--
-- Act Interconnection: an admin overlay that RELATES acts belonging to the same
-- tender lifecycle (and flags duplicates), guided by weighted match conditions
-- that produce a confidence score. Separate from proc.act_link (the official
-- source graph) — this captures links the source missed / cross-source acts.
--
--   act_group        — one interconnection group (a tender process).
--   act_group_member — an act's membership (at most one group per act) + an
--                      optional duplicate flag pointing at the original.
--   match_rule       — the scoring conditions (admin-editable weights / on-off).
--   match_setting    — thresholds (review_min surfaces a candidate; auto_min
--                      lets the scan auto-group).
--
-- Safe/idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.act_group (
    id         bigserial   PRIMARY KEY,
    label      text,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proc.act_group_member (
    adam         text        PRIMARY KEY REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    group_id     bigint      NOT NULL REFERENCES proc.act_group(id) ON DELETE CASCADE,
    is_duplicate boolean     NOT NULL DEFAULT false,
    duplicate_of text        REFERENCES proc.procurement_act(adam) ON DELETE SET NULL,
    added_by     text,
    added_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_act_group_member_group ON proc.act_group_member (group_id);

CREATE TABLE IF NOT EXISTS proc.match_rule (
    code      text    PRIMARY KEY,       -- stable id
    label     text    NOT NULL,
    kind      text    NOT NULL CHECK (kind IN ('identifier', 'authority')),
    field     text,                      -- procurement_act column (identifier rules)
    weight    integer NOT NULL CHECK (weight >= 0),
    is_active boolean NOT NULL DEFAULT true
);

INSERT INTO proc.match_rule (code, label, kind, field, weight) VALUES
    ('contract_number', 'Αριθμός σύμβασης',  'identifier', 'contract_number', 70),
    ('protocol_number', 'Αριθμός πρωτοκόλλου','identifier', 'protocol_number', 70),
    ('commitment_no',   'Αριθμός δέσμευσης',  'identifier', 'commitment_no',   60),
    ('authority',       'Ίδια αναθέτουσα',    'authority',  NULL,              25)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS proc.match_setting (
    key   text PRIMARY KEY,
    value integer NOT NULL
);
INSERT INTO proc.match_setting (key, value) VALUES
    ('review_min', 40),   -- min score to surface a candidate for review
    ('auto_min',   90),   -- min score for the scan to auto-group
    ('max_shared', 30)    -- an identifier value shared by MORE than this many acts
                          -- is treated as junk (placeholders like '1', '-', 'ΔΥ')
                          -- and does NOT generate candidates
ON CONFLICT (key) DO NOTHING;

-- Expression indexes on btrim(identifier) so the trimmed-equality candidate
-- lookup + count guard + scan self-join are index-backed (plain-column indexes
-- can't serve btrim(col) = ...).
CREATE INDEX IF NOT EXISTS ix_pa_contract_number_bt
    ON proc.procurement_act (btrim(contract_number)) WHERE contract_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_pa_protocol_number_bt
    ON proc.procurement_act (btrim(protocol_number)) WHERE protocol_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_pa_commitment_no_bt
    ON proc.procurement_act (btrim(commitment_no)) WHERE commitment_no IS NOT NULL;

COMMENT ON TABLE proc.act_group IS
    'Act interconnection group (one tender lifecycle). Admin overlay, separate '
    'from proc.act_link (the official source graph).';

COMMIT;
