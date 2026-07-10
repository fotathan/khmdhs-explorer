-- migrations/20260710113553_act_assign_criteria_label.sql
-- act assign criteria label
--
-- The KHMDHS `assignCriteria` field is a {key,value} whose KEY is unreliable
-- (it's the contractType code — 9/10/12/13 — and the same key maps to different
-- labels across acts). So the award-criterion code can't be resolved from a
-- lookup table; store the API's label (value) directly. Populated from
-- kv_label(act, "assignCriteria").
--
-- Idempotent.

BEGIN;

ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS assign_criteria_label text;

COMMIT;
