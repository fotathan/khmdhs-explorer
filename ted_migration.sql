-- ted_migration.sql
--
-- TED (EU Tenders Electronic Daily) as a THIRD source alongside KHMDHS and
-- Diavgeia. Source-native tables are authoritative (TED is procedure/lot/result
-- oriented and does not map 1:1 onto the KHMDHS ADAM model); a digest is
-- projected into proc.procurement_act with data_source='ted' (see
-- ted_ingest.project_all), exactly as Diavgeia does.
--
-- v1 stores Search-API metadata only (no XML parsing yet). Mirrors the
-- diavgeia_* tables + diavgeia_ingest_window.
--
-- Safe/idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.ted_notice (
    publication_number       text PRIMARY KEY,          -- e.g. '370748-2026'
    notice_identifier        text,
    procedure_identifier     text,
    notice_type              text,                       -- 'can-standard', 'cn-*', 'pin-*'
    procedure_type           text,
    publication_date         date,
    title                    text,                       -- resolved ell→eng→any
    buyer_name               text,
    buyer_country            text,
    estimated_value          numeric,
    currency                 text,
    winner_name              text,
    winner_identifier        text,
    contract_conclusion_date date,
    xml_url                  text,
    html_url                 text,
    pdf_url                  text,
    raw_json                 jsonb,
    ingested_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ted_notice_pubdate ON proc.ted_notice (publication_date);

CREATE TABLE IF NOT EXISTS proc.ted_notice_cpv (
    publication_number text NOT NULL REFERENCES proc.ted_notice(publication_number) ON DELETE CASCADE,
    cpv_code           varchar(10) NOT NULL,
    ord                integer,
    PRIMARY KEY (publication_number, cpv_code)
);

-- Windowed, resumable backfill state — mirrors proc.diavgeia_ingest_window.
CREATE TABLE IF NOT EXISTS proc.ted_ingest_window (
    id          bigserial PRIMARY KEY,
    country     text NOT NULL,
    date_from   date NOT NULL,
    date_to     date NOT NULL,
    status      text NOT NULL DEFAULT 'pending',   -- pending|running|done|error
    notices     integer,
    last_error  text,
    started_at  timestamptz,
    finished_at timestamptz,
    UNIQUE (country, date_from, date_to)
);

COMMENT ON TABLE proc.ted_notice IS
    'Source-native TED notices (Search API metadata). Projected into '
    'proc.procurement_act as data_source=''ted'' (adam = ''TED:''||publication_number).';

COMMIT;
