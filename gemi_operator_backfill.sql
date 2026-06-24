-- gemi_operator_backfill.sql
-- ===========================================================================
-- One-time (re-runnable) backfill: copy ALREADY-harvested ΓΕΜΗ data from
-- proc.gemi_enrichment into the editable contractor fields on
-- proc.economic_operator. No ΓΕΜΗ API calls — pure SQL over data we already have.
--
-- Same rule as the live path (gemi_client.upsert): FILL-ONLY-IF-EMPTY, so any
-- value a curator already entered is kept. Safe to run repeatedly.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f gemi_operator_backfill.sql
--   psql "<supabase-direct-url>" -f gemi_operator_backfill.sql
-- ===========================================================================

UPDATE proc.economic_operator o SET
    ar_gemi        = COALESCE(NULLIF(o.ar_gemi, ''),        g.ar_gemi),
    city           = COALESCE(NULLIF(o.city, ''),           g.city),
    postal_code    = COALESCE(NULLIF(o.postal_code, ''),    g.zip_code),
    street_address = COALESCE(NULLIF(o.street_address, ''),
                              NULLIF(btrim(concat_ws(' ', g.street, g.street_number)), '')),
    contact_phone  = COALESCE(NULLIF(o.contact_phone, ''),  g.phone),
    contact_fax    = COALESCE(NULLIF(o.contact_fax, ''),    g.fax),
    contact_email  = COALESCE(NULLIF(o.contact_email, ''),  g.email),
    contact_url    = COALESCE(NULLIF(o.contact_url, ''),    g.url)
FROM proc.gemi_enrichment g
WHERE g.fetch_status = 'ok'
  AND o.vat_number = g.afm
  -- only touch rows that still have at least one of these fields empty
  AND (o.ar_gemi IS NULL OR o.city IS NULL OR o.postal_code IS NULL
       OR o.street_address IS NULL OR o.contact_phone IS NULL
       OR o.contact_fax IS NULL OR o.contact_email IS NULL OR o.contact_url IS NULL);
