-- ============================================================================
-- Greek Public Procurement Database — PostgreSQL schema
-- Sources: KHMDHS Opendata API (cerpp.eprocurement.gov.gr) + Diavgeia (org dir)
-- Target: PostgreSQL 14+
--
-- Design notes
--  * ADAM (referenceNumber) is the universal natural key for every act.
--  * All KHMDHS enumerations come as {key, value}. We store the KEY on the fact
--    table and resolve the label from lookup tables, so labels stay consistent
--    even when the API's wording drifts between endpoints.
--  * act_link is a single edge table that captures the whole
--    request -> notice -> auction(award) -> contract -> payment graph,
--    populated from the *RefNo[] arrays and the linked-acts endpoint.
--  * Contractor/bidder columns are marked TENTATIVE: the auction & contract
--    response schemas were not fully machine-readable from the help page, so the
--    award-supplier shape must be confirmed against one live call before relying
--    on it. See ingestion notes.
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS proc;
SET search_path TO proc, public;

-- ---------------------------------------------------------------------------
-- 1. ENUM for the five KHMDHS act types (the discriminator on procurement_act)
-- ---------------------------------------------------------------------------
CREATE TYPE act_type AS ENUM ('request', 'notice', 'auction', 'contract', 'payment');

-- The ADAM prefix per type, for validation/derivation:
--   request  -> ##REQ#########   notice  -> ##PROC#########
--   auction  -> ##AWRD#########  contract-> ##SYMV#########
--   payment  -> (e.g. ##PAY... ) confirm prefix from a live payment call.

-- ---------------------------------------------------------------------------
-- 2. LOOKUP / CODE TABLES  (seeded from {key,value} pairs seen in responses)
--    One generic table keeps the schema small; each domain is namespaced.
-- ---------------------------------------------------------------------------
CREATE TABLE code_list (
    domain      text NOT NULL,   -- e.g. 'contract_type','procedure_type','nuts','currency'
    code        text NOT NULL,   -- the {key}
    label_el    text,            -- the {value} (Greek)
    label_en    text,            -- optional translation you maintain
    PRIMARY KEY (domain, code)
);
COMMENT ON TABLE code_list IS
  'Generic lookup for all KHMDHS enumerations. Seed/refresh from live {key,value} responses, not from the submission docs (codes differ between submit and retrieve).';

-- CPV is large and standardised; give it its own table.
CREATE TABLE cpv_code (
    cpv_code    varchar(10) PRIMARY KEY,   -- format ########-#
    description text
);

-- NUTS region tree (region of the authority and place(s) of performance).
CREATE TABLE nuts_code (
    nuts_code   varchar(8) PRIMARY KEY,
    label       text,
    parent_code varchar(8) REFERENCES nuts_code(nuts_code)
);

-- ---------------------------------------------------------------------------
-- 3. PARTIES
-- ---------------------------------------------------------------------------

-- 3a. Contracting authorities / awarding bodies (Αναθέτουσες Αρχές).
--     PK = the KHMDHS organization `key`. Enriched from Diavgeia org-structure.
CREATE TABLE authority (
    org_id            text PRIMARY KEY,         -- KHMDHS organization.key (e.g. '100015981')
    name              text NOT NULL,            -- organization.value
    vat_number        text,                     -- organizationVatNumber
    is_greek_vat      boolean,                  -- greekOrganizationVatNumber
    aaht              text,                      -- e-invoicing code of the authority
    type_code         text,                      -- code_list domain 'authority_type'
    classification_code text,                    -- code_list domain 'org_classification'
    nuts_code         varchar(8) REFERENCES nuts_code(nuts_code),
    city              text,
    postal_code       text,
    country           text,
    diavgeia_org_uid  text,                      -- link to Diavgeia organization id
    source            text DEFAULT 'khmdhs',     -- 'khmdhs' | 'diavgeia' | 'merged'
    first_seen        timestamptz DEFAULT now(),
    last_seen         timestamptz DEFAULT now()
);
CREATE INDEX ix_authority_vat  ON authority(vat_number);
CREATE INDEX ix_authority_name ON authority USING gin (to_tsvector('simple', name));

-- 3b. Organizational units (Οργανική Μονάδα) — child of an authority.
CREATE TABLE org_unit (
    unit_id     text PRIMARY KEY,                -- contractingData.unitsOperator.key
    name        text,                            -- .value
    authority_id text REFERENCES authority(org_id)
);

-- 3c. Signers / deciding officers (Αποφαινόμενο όργανο).
CREATE TABLE signer (
    signer_id   text PRIMARY KEY,                -- contractingData.signers.key
    name        text,                            -- .value  (often 'NAME - ROLE')
    role_title  text,
    authority_id text REFERENCES authority(org_id)
);

-- 3d. Contractors & bidders (economic operators).  PK = VAT where available.
--     TENTATIVE: populated from auction/contract objects; confirm field names.
CREATE TABLE economic_operator (
    operator_id   bigserial PRIMARY KEY,
    vat_number    text UNIQUE,                   -- AFM; null only if foreign/unknown
    name          text NOT NULL,
    is_greek_vat  boolean,
    country       text,
    first_seen    timestamptz DEFAULT now(),
    last_seen     timestamptz DEFAULT now()
);
CREATE INDEX ix_operator_name ON economic_operator USING gin (to_tsvector('simple', name));

-- ---------------------------------------------------------------------------
-- 4. CORE FACT TABLE — one row per procurement act of any type
-- ---------------------------------------------------------------------------
CREATE TABLE procurement_act (
    adam            text PRIMARY KEY,            -- referenceNumber (universal key)
    type            act_type NOT NULL,
    title           text,

    -- common dates
    signed_date         date,                    -- signedDate (protocol/issue date)
    submission_date     timestamptz,             -- submissionDate (entry to KHMDHS)
    last_update_date    timestamptz,             -- lastUpdateDate
    published_eu_date   date,                    -- publishedDate (sent to EU) [notice]
    final_submission_date timestamptz,           -- finalSubmissionDate (tender deadline) [notice]
    procurement_delivery_date date,              -- [request]

    -- status / lifecycle (covers your "cancellations" + "corrections")
    cancelled           boolean DEFAULT false,
    cancellation_date   timestamptz,
    cancellation_type   text,                    -- code_list 'cancellation_type'
    cancellation_reason text,
    cancellation_ada    text,                    -- Diavgeia ADA of the cancellation act
    is_modified         boolean,                 -- correction/amendment flag [notice]
    amends_previous      boolean,                 -- amendPreviousNotice
    amended_adam         text,                    -- amendedNoticeADAM (self-ref by ADAM)

    -- classification
    contract_type_code      text,                -- code_list 'contract_type'
    mixed_contract          boolean,
    procedure_type_code     text,                -- code_list 'procedure_type'
    award_procedure_code    text,                -- code_list 'award_procedure' (justification)
    criteria_code           text,                -- code_list 'award_criteria'
    legal_context_code      text,                -- code_list 'legal_context'
    notice_type_code        text,                -- code_list 'notice_type' (Προκήρυξη/Διακήρυξη/Πρόσκληση)
    conducting_proceedings_code text,            -- code_list 'conducting'
    digital_platform_code   text,                -- code_list 'digital_platform'
    contracting_authority_activity_code text,    -- code_list 'authority_activity'

    -- money
    budget                  numeric(18,2),
    total_cost_without_vat  numeric(18,2),
    total_cost_with_vat     numeric(18,2),
    currency_code           text,                -- usually EUR

    -- geography (authority location; place(s) of performance live in act_nuts)
    nuts_code   varchar(8) REFERENCES nuts_code(nuts_code),
    city        text,
    postal_code text,
    country     text,

    -- relations to parties
    authority_id text REFERENCES authority(org_id),
    org_unit_id  text REFERENCES org_unit(unit_id),
    signer_id    text REFERENCES signer(signer_id),

    -- procedure scope / framework agreement
    number_of_sections      integer,
    contract_duration       numeric,
    contract_duration_unit  text,                -- code_list 'time_unit'
    offers_valid_time       numeric,
    offers_valid_time_unit  text,                -- code_list 'time_unit'
    max_number_of_contractors integer,
    option_right            boolean,
    option_right_description text,
    framework_agreement_adam text,               -- frameworkAgreementNoticeADAM
    bidding_website         text,                -- biddingWebsite

    -- contract-specific (type='contract')
    contract_number     text,                    -- contractNumber
    contract_signed_date date,                   -- contractSignedDate
    start_date          date,                     -- startDate
    end_date            date,                     -- endDate
    no_end_date         boolean,                  -- noEndDate
    assign_criteria_code text,                    -- code_list 'award_criteria' (assignCriteria)
    bids_submitted      integer,                  -- bidsSubmitted (competition count)
    max_bids_submitted  integer,                  -- maxBidsSubmitted

    -- payment-specific (type='payment')
    is_credit           boolean,                  -- credit
    payment_commitment_code text,                 -- paymentCommitmentCode
    contract_value      numeric(18,2),            -- contractValue

    -- references / approvals
    approval_ada        text,                    -- ADA of approval decision in Diavgeia
    commitment_no       text,                    -- ανάληψη υποχρέωσης
    protocol_number     text,
    author_email        text,

    -- award / contractor summary (TENTATIVE — confirm from auction/contract calls)
    awarded_operator_id bigint REFERENCES economic_operator(operator_id),
    award_value_without_vat numeric(18,2),
    award_value_with_vat    numeric(18,2),

    -- provenance
    raw_json        jsonb,                        -- keep the full API object verbatim
    source_endpoint text,                         -- which /khmdhs-opendata/<type> produced it
    ingested_at     timestamptz DEFAULT now()
);

CREATE INDEX ix_act_type        ON procurement_act(type);
CREATE INDEX ix_act_authority   ON procurement_act(authority_id);
CREATE INDEX ix_act_signed_date ON procurement_act(signed_date);
CREATE INDEX ix_act_contract_type ON procurement_act(contract_type_code);
CREATE INDEX ix_act_cancelled   ON procurement_act(cancelled);
CREATE INDEX ix_act_raw_gin     ON procurement_act USING gin (raw_json);

-- ---------------------------------------------------------------------------
-- 5. LINE ITEMS (objectDetails[])  — child of an act
-- ---------------------------------------------------------------------------
CREATE TABLE act_object_detail (
    id              bigserial PRIMARY KEY,
    adam            text NOT NULL REFERENCES procurement_act(adam) ON DELETE CASCADE,
    line_no         integer,                     -- ordinal within the array
    short_description text,
    quantity        numeric,
    unit_code       text,                        -- code_list 'uom' (objectDetails[].type.key)
    cost_without_vat numeric(18,2),
    vat_rate        text,
    currency_code   text,
    green_contract_code text,                    -- code_list 'green'
    good_services_code  text,                    -- code_list 'esd_category'
    budget_code     text
);
CREATE INDEX ix_obj_adam ON act_object_detail(adam);

-- M:N between a line item and CPV codes (a line can carry several CPVs).
CREATE TABLE object_detail_cpv (
    object_detail_id bigint NOT NULL REFERENCES act_object_detail(id) ON DELETE CASCADE,
    cpv_code         varchar(10) NOT NULL REFERENCES cpv_code(cpv_code),
    PRIMARY KEY (object_detail_id, cpv_code)
);

-- ---------------------------------------------------------------------------
-- 6. THE LINK GRAPH — every cross-ADAM reference becomes one edge
-- ---------------------------------------------------------------------------
CREATE TYPE link_relation AS ENUM (
    -- from request
    'request_to_notice', 'request_to_auction', 'request_to_contract',
    'request_to_payment', 'request_approves',
    -- from notice
    'notice_to_auction', 'notice_amends_notice', 'framework_of_notice',
    'notice_related', 'notice_uses_request',
    -- from auction
    'auction_to_contract', 'auction_to_payment', 'auction_amends_auction',
    'auction_under_notice',
    -- from contract
    'contract_to_payment', 'contract_from_auction', 'contract_from_request',
    'contract_prev', 'contract_next',
    -- from payment
    'payment_for_contract', 'payment_for_auction', 'payment_for_request',
    -- anything from the linked-acts (ADAM chain) endpoint
    'generic'
);

CREATE TABLE act_link (
    source_adam text NOT NULL,
    target_adam text NOT NULL,
    relation    link_relation NOT NULL,
    discovered_at timestamptz DEFAULT now(),
    PRIMARY KEY (source_adam, target_adam, relation)
);
-- Note: FKs to procurement_act are intentionally omitted so we can record an
-- edge before the target act has been ingested. Enforce with a periodic check
-- or add deferrable FKs once a full backfill is complete.
CREATE INDEX ix_link_source ON act_link(source_adam);
CREATE INDEX ix_link_target ON act_link(target_adam);
CREATE INDEX ix_link_rel    ON act_link(relation);

-- ---------------------------------------------------------------------------
-- 7. SECONDARY relations on an act (multi-valued attributes)
-- ---------------------------------------------------------------------------

-- Place(s) of performance (notice.nutsCodes[]).
CREATE TABLE act_nuts (
    adam      text NOT NULL REFERENCES procurement_act(adam) ON DELETE CASCADE,
    nuts_code varchar(8) NOT NULL REFERENCES nuts_code(nuts_code),
    PRIMARY KEY (adam, nuts_code)
);

-- Centralized markets / tools (notice.centralizedMarkets[]).
CREATE TABLE act_centralized_market (
    adam text NOT NULL REFERENCES procurement_act(adam) ON DELETE CASCADE,
    market_code text NOT NULL,                   -- code_list 'centralized_market'
    PRIMARY KEY (adam, market_code)
);

-- Additional contract characters (additionalContractTypes[]).
CREATE TABLE act_additional_contract_type (
    adam text NOT NULL REFERENCES procurement_act(adam) ON DELETE CASCADE,
    contract_type_code text NOT NULL,            -- code_list 'additional_contract_type'
    PRIMARY KEY (adam, contract_type_code)
);

-- ESHDIS / e-tender system numbers (systemicNumbers[]).
CREATE TABLE act_systemic_number (
    adam text NOT NULL REFERENCES procurement_act(adam) ON DELETE CASCADE,
    systemic_number text NOT NULL,
    PRIMARY KEY (adam, systemic_number)
);

-- Funding sources (notice.fundingDetails) flattened to key/value rows so we can
-- hold any of: ΣΑΕ, Ενάριθμος ΠΔΕ, ΟΠΣ, ΕΣΠΑ, ίδιοι πόροι, τακτικός π/υ.
CREATE TABLE act_funding (
    id      bigserial PRIMARY KEY,
    adam    text NOT NULL REFERENCES procurement_act(adam) ON DELETE CASCADE,
    funding_kind text NOT NULL,                  -- 'pde_sae','pde_enarithmos','pde_ops','cofund_ops','espa','self_funded','regular_budget'
    funding_ref  text
);
CREATE INDEX ix_funding_adam ON act_funding(adam);

-- ---------------------------------------------------------------------------
-- 8. AWARD PARTICIPATION (winners and, where published, bidders)
--    TENTATIVE structure — confirm against a live auction/contract response.
-- ---------------------------------------------------------------------------
CREATE TYPE participation_role AS ENUM ('winner', 'bidder', 'subcontractor', 'consortium_member');

CREATE TABLE act_operator (
    id          bigserial PRIMARY KEY,
    adam        text NOT NULL REFERENCES procurement_act(adam) ON DELETE CASCADE,
    operator_id bigint NOT NULL REFERENCES economic_operator(operator_id),
    role        participation_role NOT NULL,
    awarded_value_without_vat numeric(18,2),
    awarded_value_with_vat    numeric(18,2),
    UNIQUE (adam, operator_id, role)
);
CREATE INDEX ix_act_operator_adam ON act_operator(adam);
CREATE INDEX ix_act_operator_op   ON act_operator(operator_id);

-- ---------------------------------------------------------------------------
-- 9. DIAVGEIA decision layer (the ADA approval acts that KHMDHS points to)
-- ---------------------------------------------------------------------------
CREATE TABLE diavgeia_decision (
    ada            text PRIMARY KEY,             -- Diavgeia ADA
    subject        text,
    decision_type  text,                         -- Diavgeia type code (e.g. Δ.2.1, Β.2.2)
    organization_uid text,
    signer_uid     text,
    issue_date     date,
    document_url   text,
    raw_json       jsonb,
    ingested_at    timestamptz DEFAULT now()
);

-- Bridge: an act's approval_ada / cancellation_ada resolve here.
CREATE TABLE act_diavgeia_link (
    adam text NOT NULL REFERENCES procurement_act(adam) ON DELETE CASCADE,
    ada  text NOT NULL,                           -- may precede diavgeia_decision row
    link_kind text NOT NULL,                      -- 'approval' | 'cancellation'
    PRIMARY KEY (adam, ada, link_kind)
);

-- ---------------------------------------------------------------------------
-- 10. INGESTION BOOKKEEPING — drives the 180-day windowed backfill & deltas
-- ---------------------------------------------------------------------------
CREATE TABLE ingest_window (
    id          bigserial PRIMARY KEY,
    act_type    act_type NOT NULL,
    date_from   date NOT NULL,
    date_to     date NOT NULL,                    -- <= date_from + 180
    status      text NOT NULL DEFAULT 'pending',  -- pending|running|done|error
    pages_done  integer DEFAULT 0,
    total_pages integer,
    last_error  text,
    started_at  timestamptz,
    finished_at timestamptz,
    UNIQUE (act_type, date_from, date_to)
);

COMMIT;

-- ============================================================================
-- Convenience view: full lifecycle chain rooted at any ADAM (recursive).
-- ============================================================================
CREATE OR REPLACE VIEW proc.v_act_chain AS
WITH RECURSIVE chain AS (
    SELECT source_adam AS root, source_adam AS adam, 0 AS depth
    FROM proc.act_link
    UNION
    SELECT c.root, l.target_adam, c.depth + 1
    FROM chain c
    JOIN proc.act_link l ON l.source_adam = c.adam
    WHERE c.depth < 10
)
SELECT root, adam, depth FROM chain;
