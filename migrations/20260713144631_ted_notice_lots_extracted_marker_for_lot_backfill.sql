-- migrations/20260713144631_ted_notice_lots_extracted_marker_for_lot_backfill.sql
-- ted notice lots extracted marker for lot backfill
--
-- A "we attempted lot extraction for this notice" marker. The lot-backfill pass
-- (ted-lot-backfill) re-fetches a notice's XML to capture its source-native lot
-- snapshot for notices imported BEFORE structured lots existed. Without a marker
-- it would re-fetch genuinely lot-less notices on every run; lots_extracted_at
-- IS NULL is the "not yet attempted" set. Set to now() whenever a notice's lots
-- are (re)written or an attempt is made. Idempotent.

BEGIN;

ALTER TABLE proc.ted_notice
  ADD COLUMN IF NOT EXISTS lots_extracted_at timestamptz;

COMMIT;
