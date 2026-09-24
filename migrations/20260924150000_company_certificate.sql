-- company_certificate
--
-- The certificates a customer's firm HOLDS (ISO 9001, 13485, …) — and,
-- optionally, those of the manufacturers it carries — for the evaluation
-- layer (docs/specs/evaluation-layer.md). The checklist shows them under the
-- items that ask for them: «Στο προφίλ σας: ISO 9001:2015, ισχύει έως …».
--
--   * Per customer (user_id), like company_profile. DECLARED only: nothing
--     derives a certificate from the award ledger or the ΓΕΜΗ registry.
--   * scheme is a key of the closed catalogue in app/eligibility_eval.py
--     (CATALOGUE); the CHECK below repeats it so a typo cannot be stored.
--   * holder: 'self' = the customer's firm; 'manufacturer' = a manufacturer
--     whose products the customer supplies (medical tenders ask that «ο
--     κατασκευαστής» hold ISO 13485). manufacturer names it, and is required
--     exactly when holder = 'manufacturer'.
--   * valid_until NULL = not entered — never inferred.
--   * Never read by anything act-scoped or cached: act_ai_summary is served
--     to everyone (ai-summary spec §3). Test-enforced.
--
-- No DO $$ blocks: the Supabase dashboard editor splits them.
-- Do NOT tick "Run and enable RLS" for this table in Supabase: with no
-- policies, every app INSERT is refused (proc.onboarding, 2026-09-22).

BEGIN;

CREATE TABLE IF NOT EXISTS proc.company_certificate (
    id            bigserial PRIMARY KEY,
    user_id       bigint NOT NULL
                  REFERENCES proc.app_user(id) ON DELETE CASCADE,
    scheme        text   NOT NULL
                  CONSTRAINT company_certificate_scheme_ck
                  CHECK (scheme IN ('iso9001', 'iso13485', 'iso14001',
                                    'iso45001', 'iso27001', 'iso37001',
                                    'iso22000', 'haccp', 'iso22301',
                                    'iso50001', 'iso39001')),
    holder        text   NOT NULL DEFAULT 'self'
                  CONSTRAINT company_certificate_holder_ck
                  CHECK (holder IN ('self', 'manufacturer')),
    manufacturer  text,
    edition       text
                  CONSTRAINT company_certificate_edition_ck
                  CHECK (edition IS NULL OR edition ~ '^[0-9]{4}$'),
    number        text   CHECK (number IS NULL OR length(number) <= 100),
    issuer        text   CHECK (issuer IS NULL OR length(issuer) <= 200),
    valid_until   date,
    source        text   NOT NULL DEFAULT 'admin'
                  CONSTRAINT company_certificate_source_ck
                  CHECK (source IN ('admin', 'customer')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    bigint,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    bigint,
    CONSTRAINT company_certificate_manufacturer_ck CHECK (
        (holder = 'self' AND manufacturer IS NULL)
        OR (holder = 'manufacturer' AND length(btrim(manufacturer)) BETWEEN 1 AND 200))
);

-- One row per scheme for the firm itself, one per scheme AND manufacturer.
CREATE UNIQUE INDEX IF NOT EXISTS ux_company_certificate_holder
    ON proc.company_certificate
       (user_id, scheme, holder, lower(coalesce(manufacturer, '')));

COMMENT ON TABLE proc.company_certificate IS
    'Certificates a customer declared (their own, or a manufacturer''s they carry). Private to user_id. Never read by anything act-scoped or cached — see app/ai_summary.py §3.';

COMMIT;
