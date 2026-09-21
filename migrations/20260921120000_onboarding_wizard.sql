-- onboarding_wizard
--
-- First-login wizard ("Ρύθμιση ραντάρ") that turns a few answers into saved
-- searches. Spec: docs/specs/onboarding-wizard.md.
--
-- Two things live here, and they are kept apart on purpose:
--
--   * customer_profile.tender_experience — the customer's own yes/no answer at
--     registration ("have you bid in public tenders before?"). It is a fact
--     about THEM that they told us, so it belongs on the profile. NULL means
--     "never asked" (admin-created and older accounts), not "no".
--
--   * proc.onboarding.declared_afm — the ΑΦΜ they typed. It is a CLAIM, not an
--     identity: it only drives suggestions. It must never reach
--     customer_profile.vat_number / tax_number / operator_id, because those
--     decide the fit score, the ledger link and eventually an invoice, and a
--     link there is made only by an admin (app/company_match.py).
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.

BEGIN;

ALTER TABLE proc.customer_profile
    ADD COLUMN IF NOT EXISTS tender_experience boolean;

COMMENT ON COLUMN proc.customer_profile.tender_experience IS
    'Self-declared at registration: has bid in public tenders before. NULL = never asked.';

CREATE TABLE IF NOT EXISTS proc.onboarding (
    user_id             bigint PRIMARY KEY
                        REFERENCES proc.app_user(id) ON DELETE CASCADE,
    step                smallint NOT NULL DEFAULT 0,
    answers             jsonb    NOT NULL DEFAULT '{}'::jsonb,
    declared_afm        text,
    ledger_found        boolean,
    started_at          timestamptz NOT NULL DEFAULT now(),
    completed_at        timestamptz,
    skipped_at          timestamptz,
    created_profile_ids bigint[] NOT NULL DEFAULT '{}'
);

COMMENT ON TABLE proc.onboarding IS
    'First-login wizard state, one row per customer. answers = the in-progress choices; declared_afm is an unverified claim and never reaches customer_profile.';
COMMENT ON COLUMN proc.onboarding.declared_afm IS
    'ΑΦΜ the customer typed. Suggestions only — an admin links it through the ΓΕΜΗ match panel.';

CREATE INDEX IF NOT EXISTS ix_onboarding_declared_afm
    ON proc.onboarding (declared_afm) WHERE declared_afm IS NOT NULL;

COMMIT;
