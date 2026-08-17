-- migrations/20260817100404_sip_extension_and_call_cti_columns.sql
-- sip extension and call cti columns
--
-- CTI (computer-telephony integration) prototype — see TELEPHONY_RUNBOOK.md.
--   1. proc.sip_extension  — maps an app_user to a SIP endpoint on the PBX so
--      the browser softphone knows which credentials to register with, and the
--      AMI screen-pop listener knows which agent an inbound call is ringing.
--   2. proc.customer_call gains the fields a real call produces: the external
--      number, and start/end/duration. The pre-existing direction/status/
--      assigned_to/created_by columns are reused as-is.
--
-- Wrap the body so a failure leaves nothing half-applied. Idempotent throughout.

BEGIN;

-- 1. app_user  →  SIP endpoint on the PBX ----------------------------------- --
-- sip_secret is the plaintext SIP registration password. This is a PROTOTYPE
-- convenience: the browser must present it to REGISTER, and Asterisk stores the
-- matching secret in pjsip.conf. A production deployment would issue short-lived
-- credentials instead of persisting the secret here (see the runbook).
CREATE TABLE IF NOT EXISTS proc.sip_extension (
    user_id     bigint      PRIMARY KEY REFERENCES proc.app_user(id) ON DELETE CASCADE,
    extension   text        NOT NULL UNIQUE,       -- dialable number on the PBX, e.g. '1001'
    sip_user    text        NOT NULL UNIQUE,        -- PJSIP endpoint / auth username
    sip_secret  text        NOT NULL,               -- plaintext SIP password (prototype)
    display_name text,                              -- optional caller-id name for outbound
    is_active   boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz
);

COMMENT ON TABLE proc.sip_extension IS
    'Maps an app_user to a SIP endpoint on the PBX (CTI prototype). One row per '
    'agent who can place/receive calls from the browser softphone.';

-- 2. Real-call fields on the existing call log ------------------------------- --
ALTER TABLE proc.customer_call
    ADD COLUMN IF NOT EXISTS external_number text,      -- the other party's number (E.164 where known)
    ADD COLUMN IF NOT EXISTS started_at      timestamptz,
    ADD COLUMN IF NOT EXISTS ended_at        timestamptz,
    ADD COLUMN IF NOT EXISTS duration_s      integer;

COMMIT;
