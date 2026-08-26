-- migrations/20260826055404_index_object_detail_cpv_cpv_code.sql
-- index object_detail_cpv cpv_code
--
-- object_detail_cpv (2.2M+ rows) is only indexed on the composite PK
-- (object_detail_id, cpv_code) i.e. keyed by object detail, not by code. The
-- "top contractors for this tender's CPV codes" panel filters by cpv_code
-- first, so without this index that query is a full table scan.
--
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so this
-- file deliberately has no BEGIN/COMMIT (unlike the scaffold default).

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_object_detail_cpv_code
    ON proc.object_detail_cpv (cpv_code);
