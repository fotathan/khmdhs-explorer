-- extracted_tables_migration.sql
-- Persistent store for curator-extracted tables shown on act detail pages.
--
-- Per-table publishing: each row carries its own is_published flag, so a
-- curator can publish good tables and leave junk ones hidden (not deleted).
-- Run once:  psql "$DATABASE_URL" -f extracted_tables_migration.sql

CREATE TABLE IF NOT EXISTS proc.extracted_table (
    id           BIGSERIAL PRIMARY KEY,
    adam         TEXT        NOT NULL,
    source       TEXT        NOT NULL,   -- originating file label (t["source"])
    locator      TEXT        NOT NULL,   -- human location, e.g. "σελ. 3, πίνακας 1"
    rows         JSONB       NOT NULL,   -- [[cell,...], ...]; rows[0] is the header
    n_rows       INT         NOT NULL,
    n_cols       INT         NOT NULL,
    is_published BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Admin panel lists all tables for an act; ordered display.
CREATE INDEX IF NOT EXISTS idx_extracted_table_adam
    ON proc.extracted_table (adam, id);

-- Public detail page only ever reads the published subset for one act.
CREATE INDEX IF NOT EXISTS idx_extracted_table_pub
    ON proc.extracted_table (adam) WHERE is_published;
