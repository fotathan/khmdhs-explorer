-- migrations/20260713135342_structured_tender_lots_act_scope_and_ted_lot_snapshots.sql
-- structured tender lots + act scope + TED source-native lot snapshots
--
-- Adds a first-class LOT model and a per-act SCOPE model on top of the existing
-- curated tender-lifecycle overlay (proc.act_group):
--
--   * proc.tender_lot (+ cpv/nuts children) — canonical lifecycle lots, owned by
--     an act_group, not by any act. Imported from a source (TED) or authored by
--     an admin.
--   * proc.act_scope / proc.act_lot_scope — which part of the tender an act
--     applies to: unknown (default / no row), whole_tender, or specific_lots
--     (one or more act_lot_scope bridge rows).
--   * proc.act_group_identifier — a stable, source-native key (e.g. a TED
--     procedure id) so ingestion can find the same lifecycle group across
--     publications without relying on a display label.
--   * proc.ted_notice_lot (+ cpv/nuts children) / proc.ted_lot_result —
--     source-native TED lot + lot-result snapshots (authoritative; no catalog FK
--     so valid source codes are never dropped when the local catalog is behind).
--
-- Invariants: a specific_lots scope must reference lots in the SAME act_group as
-- the act (enforced by trigger); whole_tender/unknown carry no lot links
-- (enforced by trigger on the inverse edit). "specific_lots has >=1 lot" is
-- enforced in the service layer (a per-row trigger cannot see a parentless
-- scope row). Absence of an act_scope row == unknown, for backward compatibility.
--
-- Idempotent and transactional.

BEGIN;

-- --------------------------------------------------------------------------- --
-- 0. act_group gets an `auto` flag so machine-created singleton groups (one per
--    TED publication/procedure) can be hidden from the curated group listing.
-- --------------------------------------------------------------------------- --
ALTER TABLE proc.act_group
  ADD COLUMN IF NOT EXISTS auto boolean NOT NULL DEFAULT false;

-- --------------------------------------------------------------------------- --
-- 1. Stable lifecycle-group identifiers (source-native keys).
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS proc.act_group_identifier (
    group_id   bigint      NOT NULL REFERENCES proc.act_group(id) ON DELETE CASCADE,
    scheme     text        NOT NULL CHECK (btrim(scheme) <> ''),
    value      text        NOT NULL CHECK (btrim(value)  <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scheme, value)
);
CREATE INDEX IF NOT EXISTS ix_act_group_identifier_group
  ON proc.act_group_identifier (group_id);

-- --------------------------------------------------------------------------- --
-- 2. Canonical lifecycle lots (+ normalized CPV / NUTS children).
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS proc.tender_lot (
    id              bigserial   PRIMARY KEY,
    group_id        bigint      NOT NULL REFERENCES proc.act_group(id) ON DELETE CASCADE,
    source          text        NOT NULL CHECK (btrim(source)     <> ''),
    source_key      text        NOT NULL CHECK (btrim(source_key) <> ''),
    lot_number      text,
    title           text,
    description     text,
    status          text,
    estimated_value numeric(18,2),
    awarded_value   numeric(18,2),
    currency_code   text,
    raw_json        jsonb,
    origin          text        NOT NULL DEFAULT 'import' CHECK (origin IN ('import', 'authored')),
    created_by      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (group_id, source, source_key)
);
CREATE INDEX IF NOT EXISTS ix_tender_lot_group  ON proc.tender_lot (group_id);
CREATE INDEX IF NOT EXISTS ix_tender_lot_source ON proc.tender_lot (source, source_key);

CREATE TABLE IF NOT EXISTS proc.tender_lot_cpv (
    lot_id   bigint             NOT NULL REFERENCES proc.tender_lot(id) ON DELETE CASCADE,
    cpv_code character varying(10) NOT NULL REFERENCES proc.cpv_code(cpv_code),
    PRIMARY KEY (lot_id, cpv_code)
);

CREATE TABLE IF NOT EXISTS proc.tender_lot_nuts (
    lot_id    bigint            NOT NULL REFERENCES proc.tender_lot(id) ON DELETE CASCADE,
    nuts_code character varying(8) NOT NULL REFERENCES proc.nuts_code(nuts_code),
    PRIMARY KEY (lot_id, nuts_code)
);

-- keep tender_lot.updated_at fresh on edit
CREATE OR REPLACE FUNCTION proc.tg_tender_lot_touch() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tender_lot_touch ON proc.tender_lot;
CREATE TRIGGER trg_tender_lot_touch
    BEFORE UPDATE ON proc.tender_lot
    FOR EACH ROW EXECUTE FUNCTION proc.tg_tender_lot_touch();

-- --------------------------------------------------------------------------- --
-- 3. Act scope (which part of the tender an act applies to).
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS proc.act_scope (
    adam         text        PRIMARY KEY REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    scope_kind   text        NOT NULL CHECK (scope_kind   IN ('unknown', 'whole_tender', 'specific_lots')),
    scope_source text        NOT NULL DEFAULT 'curator' CHECK (scope_source IN ('import', 'curator')),
    note         text,
    updated_by   text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proc.act_lot_scope (
    adam             text          NOT NULL REFERENCES proc.act_scope(adam) ON DELETE CASCADE,
    lot_id           bigint        NOT NULL REFERENCES proc.tender_lot(id)  ON DELETE CASCADE,
    coverage_amount  numeric(18,2),
    coverage_percent numeric(7,4),
    note             text,
    PRIMARY KEY (adam, lot_id)
);
CREATE INDEX IF NOT EXISTS ix_act_lot_scope_lot ON proc.act_lot_scope (lot_id);

-- Same-group integrity: an act may only be linked to a lot when the act is a
-- member of a group, the lot belongs to that same group, and the act's scope is
-- 'specific_lots'. Enforced on every insert/update of a bridge row.
CREATE OR REPLACE FUNCTION proc.tg_act_lot_scope_same_group() RETURNS trigger AS $$
DECLARE
    act_group  bigint;
    lot_group  bigint;
    kind       text;
BEGIN
    SELECT group_id INTO act_group FROM proc.act_group_member WHERE adam = NEW.adam;
    IF act_group IS NULL THEN
        RAISE EXCEPTION 'act % is not a member of any group', NEW.adam
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    SELECT group_id INTO lot_group FROM proc.tender_lot WHERE id = NEW.lot_id;
    IF lot_group IS DISTINCT FROM act_group THEN
        RAISE EXCEPTION 'lot % (group %) is not in act %''s group %',
            NEW.lot_id, lot_group, NEW.adam, act_group
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    SELECT scope_kind INTO kind FROM proc.act_scope WHERE adam = NEW.adam;
    IF kind IS DISTINCT FROM 'specific_lots' THEN
        RAISE EXCEPTION 'act % scope is % (must be specific_lots to link lots)',
            NEW.adam, COALESCE(kind, 'unknown')
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_act_lot_scope_same_group ON proc.act_lot_scope;
CREATE TRIGGER trg_act_lot_scope_same_group
    BEFORE INSERT OR UPDATE ON proc.act_lot_scope
    FOR EACH ROW EXECUTE FUNCTION proc.tg_act_lot_scope_same_group();

-- Inverse invariant: cannot flip an act to whole_tender / unknown while it still
-- owns lot links. Services delete bridge rows first; this catches the mistake.
CREATE OR REPLACE FUNCTION proc.tg_act_scope_no_orphan_links() RETURNS trigger AS $$
BEGIN
    IF NEW.scope_kind IN ('whole_tender', 'unknown')
       AND EXISTS (SELECT 1 FROM proc.act_lot_scope WHERE adam = NEW.adam) THEN
        RAISE EXCEPTION 'act % has lot links; clear them before setting scope %',
            NEW.adam, NEW.scope_kind
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_act_scope_no_orphan_links ON proc.act_scope;
CREATE TRIGGER trg_act_scope_no_orphan_links
    BEFORE UPDATE ON proc.act_scope
    FOR EACH ROW EXECUTE FUNCTION proc.tg_act_scope_no_orphan_links();

-- --------------------------------------------------------------------------- --
-- 4. TED source-native lot + result snapshots (authoritative; no catalog FK).
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS proc.ted_notice_lot (
    publication_number text NOT NULL REFERENCES proc.ted_notice(publication_number) ON DELETE CASCADE,
    lot_identifier     text NOT NULL,
    lot_number         text,
    title              text,
    description        text,
    status             text,
    estimated_value    numeric(18,2),
    currency           text,
    raw_json           jsonb,
    PRIMARY KEY (publication_number, lot_identifier)
);

CREATE TABLE IF NOT EXISTS proc.ted_notice_lot_cpv (
    publication_number text NOT NULL,
    lot_identifier     text NOT NULL,
    cpv_code           text NOT NULL,
    PRIMARY KEY (publication_number, lot_identifier, cpv_code),
    FOREIGN KEY (publication_number, lot_identifier)
        REFERENCES proc.ted_notice_lot(publication_number, lot_identifier) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS proc.ted_notice_lot_nuts (
    publication_number text NOT NULL,
    lot_identifier     text NOT NULL,
    nuts_code          text NOT NULL,
    PRIMARY KEY (publication_number, lot_identifier, nuts_code),
    FOREIGN KEY (publication_number, lot_identifier)
        REFERENCES proc.ted_notice_lot(publication_number, lot_identifier) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS proc.ted_lot_result (
    publication_number text    NOT NULL REFERENCES proc.ted_notice(publication_number) ON DELETE CASCADE,
    result_ordinal     integer NOT NULL,
    lot_identifier     text,
    result_status      text,
    maximum_value      numeric(18,2),
    currency           text,
    raw_json           jsonb,
    PRIMARY KEY (publication_number, result_ordinal)
);

-- --------------------------------------------------------------------------- --
-- 5. Explicit app_runtime grants (belt-and-suspenders; ALTER DEFAULT PRIVILEGES
--    from the least-privilege migration already covers owner-created objects).
-- --------------------------------------------------------------------------- --
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON
        proc.act_group_identifier,
        proc.tender_lot, proc.tender_lot_cpv, proc.tender_lot_nuts,
        proc.act_scope, proc.act_lot_scope,
        proc.ted_notice_lot, proc.ted_notice_lot_cpv, proc.ted_notice_lot_nuts,
        proc.ted_lot_result
      TO app_runtime';
    EXECUTE 'GRANT USAGE, SELECT, UPDATE ON SEQUENCE proc.tender_lot_id_seq TO app_runtime';
  END IF;
END $$;

COMMIT;
