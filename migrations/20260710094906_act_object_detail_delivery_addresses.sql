-- migrations/20260710094906_act_object_detail_delivery_addresses.sql
-- act object detail delivery addresses
--
-- Per-line delivery / place-of-realisation address from the KHMDHS objectDetails
-- (present on contract/payment acts): addressForDelivery, city, streetNumber,
-- postalCode, countryOfDelivery (a {key,value} — we keep the ISO key),
-- cityOfConstruction. Populated by khmdhs_ingest.replace_object_details.
--
-- Idempotent.

BEGIN;

ALTER TABLE proc.act_object_detail
    ADD COLUMN IF NOT EXISTS delivery_address     text,
    ADD COLUMN IF NOT EXISTS delivery_city        text,
    ADD COLUMN IF NOT EXISTS delivery_street      text,
    ADD COLUMN IF NOT EXISTS delivery_postal_code text,
    ADD COLUMN IF NOT EXISTS delivery_country     text,
    ADD COLUMN IF NOT EXISTS city_of_construction text;

COMMIT;
