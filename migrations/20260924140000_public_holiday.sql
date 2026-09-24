-- public_holiday
--
-- Overrides for the computed Greek holiday set in app/workdays.py
-- (docs/specs/working-day-deadlines.md §3c). Seeded EMPTY: with no rows the
-- computed set stands, so working days work before anyone touches this.
--
--   * is_holiday = true   adds a day ("1 May is observed on the 5th").
--   * is_holiday = false  removes a computed day (the matching "remove the
--                         1st" half of the same decision).
--   * Greece moves holidays by ministerial decision, published weeks ahead.
--     A row here absorbs that without a deploy; the app re-reads the table
--     every few minutes.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
-- Do NOT tick "Run and enable RLS" for this table in Supabase.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.public_holiday (
    day         date    PRIMARY KEY,
    name        text    NOT NULL,
    is_holiday  boolean NOT NULL DEFAULT true,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE proc.public_holiday IS
    'Overrides for app/workdays.py: is_holiday=true adds a non-working day, false removes a computed one. Empty = the computed set.';

COMMIT;
