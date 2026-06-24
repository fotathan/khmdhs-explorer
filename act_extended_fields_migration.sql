-- act_extended_fields_migration.sql
-- ===========================================================================
-- Adds a second batch of multi-source act fields, mirroring columns from the
-- main company tender platform (tender / tender_extended_data / tender_money /
-- tender_estimated_price / tender_eligibility* / tender_address).
--
-- Like act_origin_and_source_fields_migration.sql, every column here is purely
-- ADDITIVE and safe: all nullable, nothing existing touched, nothing existing
-- breaks. Existing KHMDHS-imported acts simply leave these NULL until a source
-- that provides them populates them.
--
-- Foreign coded enums (their INT columns) are stored here as free-form text /
-- boolean / numeric rather than forced into the Greek code_list, exactly as the
-- act_origin migration did — other sources use their own vocabularies.
--
-- No indexes: these are attribute fields read on the act detail page, not
-- list/filter facets. Add an index later only if one becomes a filter.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f act_extended_fields_migration.sql
--   psql "<supabase-direct-url>" -f act_extended_fields_migration.sql
-- ===========================================================================

-- --- Procedure & classification --------------------------------------------
-- Whether the procurement is split into lots (you already store lot_number and
-- number_of_sections; this is the explicit yes/no the source provides).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS divided_into_lots boolean;
-- Whether this act IS a framework agreement (distinct from framework_agreement_adam,
-- which only points at a related framework notice).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS is_framework_agreement boolean;
-- "Type of bid required" (source's coded value, kept as text).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS type_of_bid_required text;
-- Whether alternative offers are allowed.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS alternative_offers_allowed boolean;

-- --- Offers & award ---------------------------------------------------------
-- Price weighting (%) within the award criteria (price vs. quality).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS price_weighting numeric(8,2);
-- Number of offers received. NOTE: overlaps the existing bids_submitted; kept as
-- a separate source-reported value per the field review.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS number_of_offers integer;

-- --- Duration & extension ---------------------------------------------------
-- Whether an extension/prolongation option exists (the source's specific
-- prolongation flag; the existing option_right/option_right_description is the
-- generic KHMDHS option-right).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS prolongation_option boolean;
-- Extension/prolongation length, in months.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS prolongation_in_months integer;

-- --- Financial --------------------------------------------------------------
-- VAT rate (%) and whether the stored amounts include VAT.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS vat_rate numeric(5,2);
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS vat_included boolean;
-- Currency-normalised values (for cross-currency comparison / analytics).
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS value_eur numeric(19,2);
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS value_usd numeric(19,2);
-- Estimated price range (min/max) the source reports.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS estimated_price_min numeric(19,2);
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS estimated_price_max numeric(19,2);
-- Yearly budget figure.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS yearly_budget numeric(19,2);
-- Bid bond / tender guarantee amount.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS bid_bond_amount numeric(19,2);

-- --- Eligibility / qualification --------------------------------------------
-- Eligibility / qualification criteria text, and the source's category for it.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS eligibility_criteria text;
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS eligibility_category text;

-- --- References & portal ----------------------------------------------------
-- Official journal / OJEU publication number.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS journal_number text;
-- E-procurement portal name/URL. NOTE: overlaps the existing bidding_website;
-- kept separate per the field review.
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS eprocurement_portal text;

-- --- Contact (richer than the existing city/postal/country/nuts) ------------
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS contact_email text;
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS contact_phone text;
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS contact_fax text;
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS street_address text;
ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS contact_url text;

ANALYZE proc.procurement_act;
