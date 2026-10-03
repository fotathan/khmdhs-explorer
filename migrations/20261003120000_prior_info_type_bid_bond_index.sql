-- migrations/20261003120000_prior_info_type_bid_bond_index.sql
-- A document type for «Προηγούμενες Πληροφορίες», an index for the bid bond
-- filter, and the ledger of bid bonds copied onto acts we already show.
--
-- 1. proc.act_type gains 'prior_info'. Tender Service labels a document
--    «Προηγούμενες Πληροφορίες» (PRIOR_INFORMATION: prior information notices,
--    and ΚΗΜΔΗΣ requests as Tender Service files them). Until now the ingester
--    had no type for it and filed it as a notice (tsg_ingest.type_of's
--    fallback), so it showed up among open tenders with no deadline. No other
--    source uses the value yet. ADD VALUE inside a transaction is fine on
--    PG >= 12 as long as nothing in the SAME transaction uses the new value,
--    and nothing here does.
--
-- 2. bid_bond_amount (Εγγύηση συμμετοχής) already exists: the manual act form
--    writes it and the act page shows it. Tender Service sends it as bidBond on
--    ~6% of its notices. The new search filter (bond_min / bond_max) and the
--    "show the filter only when some act has a bid bond" check
--    (main._build_lookups) both read this index. It is partial, so it only holds
--    the few rows with a value. It is built without CONCURRENTLY: on the
--    production table the build reads every row once, and blocks writes
--    (not reads) for that time. Run it outside an ingest.
--
-- 3. proc.act_bid_bond_fill: which bid bond the Tender Service ingester COPIED
--    onto an act we already show (a ΚΗΜΔΗΣ act, usually), from a Tender
--    Service record hidden behind it as an exact duplicate
--    (tsg_ingest.sync_bid_bonds). ΚΗΜΔΗΣ has no bid bond field; ~95% of the
--    bonds Tender Service sends belong to tenders we show from ΚΗΜΔΗΣ. Fill
--    only if empty; `amount` is what we wrote, so an undone match reverts the
--    act only while it still holds exactly that (an admin's later edit always
--    survives). `internal_ids` = the Tender Service records it came from.
--    Created everywhere (empty in production, where nothing writes it yet):
--    it names no tsg_* table, so the core rule below still holds.
--
-- Core migration: touches no tsg_* table (prod has none). No DO $$ blocks.

BEGIN;

ALTER TYPE proc.act_type ADD VALUE IF NOT EXISTS 'prior_info';

CREATE INDEX IF NOT EXISTS ix_act_bid_bond
    ON proc.procurement_act (bid_bond_amount)
    WHERE bid_bond_amount IS NOT NULL;

CREATE TABLE IF NOT EXISTS proc.act_bid_bond_fill (
    adam          text PRIMARY KEY
                  REFERENCES proc.procurement_act (adam) ON DELETE CASCADE,
    amount        numeric NOT NULL,
    internal_ids  text[] NOT NULL,
    filled_at     timestamptz NOT NULL DEFAULT now()
);

COMMIT;
