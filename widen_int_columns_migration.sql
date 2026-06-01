-- ============================================================================
-- Migration: widen integer count columns to bigint.
-- Run:  psql "$LOCAL"  -f widen_int_columns_migration.sql
--       psql "$REMOTE" -f widen_int_columns_migration.sql
--
-- WHY: ingestion of newer contracts failed with
--   NumericValueOutOfRange: integer out of range
-- because KHMDHS sent a value larger than PostgreSQL's integer max
-- (2,147,483,647) for one of these count fields. Rather than skip the record
-- (losing a real contract) or clamp the value (corrupting what was reported),
-- we widen the columns to bigint (max ~9.2e18), which stores the value
-- faithfully. Widening integer->bigint is safe and lossless.
--
-- These are all count-type fields (bids, sections, max contractors), so the
-- overflow likely reflects an unusual or malformed source value; bigint
-- absorbs it without error and the value remains inspectable.
-- ============================================================================

ALTER TABLE proc.procurement_act
    ALTER COLUMN bids_submitted            TYPE bigint,
    ALTER COLUMN max_bids_submitted        TYPE bigint,
    ALTER COLUMN max_number_of_contractors TYPE bigint,
    ALTER COLUMN number_of_sections        TYPE bigint;
