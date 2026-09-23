-- migrations/20260915120000_tender_service_skip_reason.sql
-- Why a stored Tender Service record is deliberately NOT shown in the app.
--
-- The Greek key's profile also returns Cyprus notices (www.eprocurement.gov.cy,
-- NUTS CY000). They are out of scope: kept in proc.tsg_record — storing them
-- costs no requests and keeps the choice reversible — but never projected.
-- skip_reason says so on the row, so a record that is absent from the app is
-- explained by something other than "held elsewhere" (held_adam) or a failure
-- (projection_error).

BEGIN;

ALTER TABLE proc.tsg_record ADD COLUMN IF NOT EXISTS skip_reason text;

COMMIT;
