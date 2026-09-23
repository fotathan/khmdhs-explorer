-- migrations/20260915090000_tender_service_source_tables.sql
-- Tender Service (Data Export API v3) as a fourth source — source-native tables.
--
-- tsg_ingest.py stores every record it walks in proc.tsg_record, and projects
-- into proc.procurement_act (adam 'TSG:<internal_id>', data_source 'tsg') only
-- the records we do not already hold from KHMDHS, Diavgeia or TED. So this
-- table holds MORE than the app shows: most of the feed is procurement we
-- already have, and keeping it is what lets a projection be withdrawn when our
-- own ingester catches up (held_keys → held_adam).
--
-- Tender Service exports no uuid; internal_id is its only per-record identifier.
--
-- content_hash / projected_hash: projection only revisits rows whose payload
-- changed since they were last projected. A failed projection sets
-- projected_hash anyway and keeps the reason in projection_error, so one bad
-- payload is not retried until its content changes.
--
-- proc.tsg_ingest_window: one row per (slice, day). A query stops at offset
-- 10,000, so windows are single days; status 'partial' is a day that had not
-- ended, 'incomplete' a walk that delivered fewer distinct records than its
-- reported total (offset paging over a live set), 'over_cap' a day too big for
-- one query. Only 'done' is skipped by --resume and moves the catch-up
-- watermark.
--
-- The analytics exclusion is a SEPARATE migration (it recreates a matview).

BEGIN;

CREATE TABLE IF NOT EXISTS proc.tsg_record (
    internal_id       text PRIMARY KEY,
    type_label        text,                    -- display label as returned: 'Προκήρυξη', 'Αποτέλεσμα'
    status            text,                    -- ACTIVE | EXPIRED
    data_source       text,                    -- the UPSTREAM portal, e.g. 'eprocurement-gov-gr'
    external_id       text,
    reference_number  text,
    title             text,
    publication_date  date,
    held_keys         text[] NOT NULL DEFAULT '{}',  -- adams it would have if we held it elsewhere
    content_hash      text NOT NULL,
    raw_json          jsonb NOT NULL,
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at      timestamptz NOT NULL DEFAULT now(),
    changed_at        timestamptz NOT NULL DEFAULT now(),
    held_adam         text,                    -- the act we hold instead (not projected)
    projected_adam    text,                    -- 'TSG:<internal_id>' while projected
    projected_hash    text,
    projected_at      timestamptz,
    projection_error  text
);
CREATE INDEX IF NOT EXISTS ix_tsg_record_pubdate ON proc.tsg_record (publication_date);
CREATE INDEX IF NOT EXISTS ix_tsg_record_unprojected ON proc.tsg_record (changed_at)
    WHERE projected_hash IS DISTINCT FROM content_hash;
CREATE INDEX IF NOT EXISTS ix_tsg_record_projected ON proc.tsg_record (internal_id)
    WHERE projected_adam IS NOT NULL AND held_adam IS NULL;

CREATE TABLE IF NOT EXISTS proc.tsg_ingest_window (
    kind            text NOT NULL,             -- 'pub:tender:active', 'upd:expired', ...
    day             date NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'partial',
                                      'incomplete', 'over_cap', 'error')),
    total           integer,
    fetched         integer,
    new_records     integer,
    changed_records integer,
    requests        integer,
    last_error      text,
    started_at      timestamptz,
    finished_at     timestamptz,
    PRIMARY KEY (kind, day)
);

COMMENT ON TABLE proc.tsg_record IS
    'Source-native Tender Service records (Data Export API v3). Projected into '
    'proc.procurement_act as data_source=''tsg'' (adam = ''TSG:''||internal_id) '
    'only when not already held from another source. See tsg_ingest.py.';
COMMENT ON TABLE proc.tsg_ingest_window IS
    'Tender Service walk state, one row per slice x day. See tsg_ingest.run_windows.';

COMMIT;
