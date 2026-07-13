-- migrations/20260713073607_act_authority_and_act_contractor_child_tables_multi_value_party_detail_optional_link.sql
-- act_authority and act_contractor child tables (multi-value party detail + optional link)
--
-- An act can name MORE THAN ONE contracting authority and MORE THAN ONE
-- contractor/winner, each with its own detail (name, ΑΦΜ, address, contact,
-- notes; contractor also the award amount). We capture that detail ON THE ACT
-- (these tables), and OPTIONALLY relate each row to the normalised
-- proc.authority / proc.economic_operator entity via a nullable link FK — set
-- automatically on an exact ΑΦΜ/id/name match (Phase B) or manually via search
-- (Phase C). The link is nullable: a party can be captured without ever being
-- related to the entity DB.
--
-- Ordered by `ord` (0-based, as authored). ON DELETE CASCADE with the act; the
-- entity link is ON DELETE SET NULL so removing an authority/operator never
-- deletes act data. Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.act_authority (
    adam          text     NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    ord           smallint NOT NULL DEFAULT 0,
    -- detail captured from the act
    name          text,
    afm           text,                 -- ΑΦΜ / VAT/UID
    external_id   text,                 -- source-side authority id
    source_code   text,                 -- AUTHORITY / AUTHORITYPERSON / AUTHORITYSUBORGANISATION / …
    type_code     text,                 -- authority type (body governed by public law, ministry, …)
    activity_code text,                 -- code_list 'authority_activity'
    street        text,
    postal_code   text,
    city          text,
    country       text,
    phone         text,
    email         text,
    fax           text,
    url           text,
    address_text  text,
    notes         text,
    -- optional relation to the normalised authority DB
    authority_id  text     REFERENCES proc.authority(org_id) ON DELETE SET NULL,
    PRIMARY KEY (adam, ord)
);

CREATE INDEX IF NOT EXISTS ix_act_authority_afm  ON proc.act_authority (afm)          WHERE afm IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_act_authority_link ON proc.act_authority (authority_id) WHERE authority_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS proc.act_contractor (
    adam            text     NOT NULL REFERENCES proc.procurement_act(adam) ON DELETE CASCADE,
    ord             smallint NOT NULL DEFAULT 0,
    name            text,
    afm             text,                 -- ΑΦΜ / VAT/UID
    tax_number      text,                 -- statistical / tax number
    street          text,
    postal_code     text,
    city            text,
    country         text,
    email           text,
    phone           text,
    fax             text,
    url             text,
    address_text    text,
    contact_person  text,
    notes           text,
    -- award to this contractor
    award_amount        numeric(18,2),
    award_currency      text,
    award_vat_rate      numeric,
    award_vat_included  boolean,
    -- optional relation to the normalised operator DB
    operator_id     bigint   REFERENCES proc.economic_operator(operator_id) ON DELETE SET NULL,
    PRIMARY KEY (adam, ord)
);

CREATE INDEX IF NOT EXISTS ix_act_contractor_afm  ON proc.act_contractor (afm)         WHERE afm IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_act_contractor_link ON proc.act_contractor (operator_id) WHERE operator_id IS NOT NULL;

COMMIT;
