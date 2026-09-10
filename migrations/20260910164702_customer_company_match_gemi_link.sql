-- customer_company_match_gemi_link
--
-- Which ΓΕΜΗ company a CRM customer IS, when they never gave us an ΑΦΜ.
--
-- Customers register without a VAT number and only hand one over when they
-- start paying, so the CRM card is full of accounts we cannot connect to the
-- award ledger, to a fit score, or to an invoice. app/company_match.py finds
-- the company from a name or an email domain, an admin picks from the
-- candidates, and the pick is recorded here.
--
-- One row per customer: the CURRENT link, not a history. The audit trail for
-- who linked what and when is proc.admin_action, which already records every
-- state-changing request under /admin.
--
-- `filled` is what makes the link reversible, and is the reason this is a
-- table rather than three more columns on customer_profile. The import fills
-- only-if-empty, so afterwards nothing in customer_profile distinguishes a
-- value we imported from one an admin typed. `filled` holds column -> the
-- value we wrote; unlink clears a field only while it still holds exactly
-- that, so an admin's later correction always survives.
--
-- `signals` keeps the scoring components of the pick (name similarity, email
-- domain, place, ledger presence). Components are shown, never just a total —
-- same rule as app/fit.py — and keeping them lets a later reader see WHY this
-- company was chosen over the others the registry returned.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.customer_company_match (
    user_id       bigint PRIMARY KEY
                  REFERENCES proc.app_user(id) ON DELETE CASCADE,
    afm           text,
    ar_gemi       text,
    operator_id   bigint REFERENCES proc.economic_operator(operator_id),
    method        text NOT NULL
                  CHECK (method IN ('afm', 'ledger', 'gemi_name', 'manual')),
    score         numeric(4,3),
    signals       jsonb NOT NULL DEFAULT '{}'::jsonb,
    filled        jsonb NOT NULL DEFAULT '{}'::jsonb,
    matched_by    bigint REFERENCES proc.app_user(id),
    matched_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE proc.customer_company_match IS
    'Which ΓΕΜΗ company a customer is (one current row per customer). filled = column -> value the import wrote, so unlink can revert only what it wrote.';
COMMENT ON COLUMN proc.customer_company_match.method IS
    'How the ΑΦΜ was arrived at: afm (already on the profile), ledger (matched a contractor), gemi_name (registry name search), manual (admin typed it).';
COMMENT ON COLUMN proc.customer_company_match.filled IS
    'column -> the value the import wrote into customer_profile. Unlink clears a column only while it still holds exactly this.';

CREATE INDEX IF NOT EXISTS ix_customer_company_match_afm
    ON proc.customer_company_match (afm);

-- No index for the candidate search: ix_eo_name_trgm already covers
-- translate(proc.f_unaccent(lower(name)), 'ς', 'σ') on proc.economic_operator,
-- which is the expression app/company_match.py searches on. Note the nesting
-- order — f_unaccent(lower(x)), NOT lower(f_unaccent(x)) as leads._fold_sql
-- builds. Postgres matches index expressions structurally, so the other order
-- silently sequential-scans 143k rows.

COMMIT;
