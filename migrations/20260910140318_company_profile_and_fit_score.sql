-- company_profile_and_fit_score
--
-- The capability record behind "is this tender worth bidding for", and the
-- first thing in the app that is a property of a CUSTOMER rather than of an
-- act. Two rules follow from that and are the reason this is its own table
-- rather than more columns on customer_profile:
--
--   * customer_profile is a CRM contact record — who they are, who owns the
--     relationship, what stage the deal is at. This is what the firm can
--     actually DO. They change for different reasons, are written by
--     different people, and only one of them is derived from the corpus.
--
--   * NOTHING act-scoped may read it. proc.act_ai_summary is cached once per
--     act and served to every reader (see app/ai_summary.py §3), so a
--     customer-shaped input anywhere near that cache is a privacy leak. Fit
--     scoring is computed per (customer, act) at read time and stored
--     nowhere, which keeps the two apart by construction.
--
-- The profile is DERIVED first: seeded from the contractor ledger by ΑΦΜ, so
-- a linked customer has a usable profile with no data entry, built from what
-- they have actually won rather than what a form would claim they can do.
-- Admin overrides sit beside the derived values, never on top of them, so
-- re-deriving can never silently discard a human correction.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.company_profile (
    user_id         bigint PRIMARY KEY
                    REFERENCES proc.app_user(id) ON DELETE CASCADE,

    -- Ledger identities this profile was built from. An array because a firm
    -- can appear under several operator_ids (merged entities), and the seed
    -- has to aggregate all of them or it understates the history.
    operator_ids    bigint[]    NOT NULL DEFAULT '{}',
    derived_at      timestamptz,
    n_awards        integer     NOT NULL DEFAULT 0,
    n_buyers        integer     NOT NULL DEFAULT 0,

    -- The value band this firm actually wins in, from its own award history.
    -- Percentiles, not min/max: one freak contract should not stretch the
    -- band so wide that it stops discriminating.
    value_p10       numeric,
    value_median    numeric,
    value_p90       numeric,

    -- Admin corrections. NULL means "use the derived value" — a distinct
    -- state from 0, which is why these are nullable rather than defaulted.
    value_min_override numeric,
    value_max_override numeric,
    note            text,

    -- Off by default: a profile nobody has looked at should not silently
    -- start driving what a customer is shown.
    is_active       boolean     NOT NULL DEFAULT false,

    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      bigint
);

COMMENT ON TABLE proc.company_profile IS
    'What a customer''s firm can do, seeded from its award history by ΑΦΜ. '
    'Never read by anything act-scoped or cached — see app/ai_summary.py §3.';

-- What they supply. Stored per CPV prefix with the weight of that prefix in
-- their history, because "has won 200 contracts in division 44" and "won one,
-- once" are different claims and a flat set cannot tell them apart.
CREATE TABLE IF NOT EXISTS proc.company_profile_cpv (
    user_id     bigint  NOT NULL
                REFERENCES proc.company_profile(user_id) ON DELETE CASCADE,
    cpv_prefix  text    NOT NULL,          -- 2, 4 or 8 digits
    n_acts      integer NOT NULL DEFAULT 0,
    total_value numeric NOT NULL DEFAULT 0,
    -- 'derived' rows are replaced wholesale on every re-seed; 'declared' rows
    -- are a human's and must survive it.
    source      text    NOT NULL DEFAULT 'derived'
                CHECK (source IN ('derived', 'declared')),
    PRIMARY KEY (user_id, cpv_prefix, source)
);

-- Where they work. Same shape, same reason.
CREATE TABLE IF NOT EXISTS proc.company_profile_nuts (
    user_id     bigint  NOT NULL
                REFERENCES proc.company_profile(user_id) ON DELETE CASCADE,
    nuts_prefix text    NOT NULL,
    n_acts      integer NOT NULL DEFAULT 0,
    source      text    NOT NULL DEFAULT 'derived'
                CHECK (source IN ('derived', 'declared')),
    PRIMARY KEY (user_id, nuts_prefix, source)
);

-- Who has bought from them. The component Cato structurally cannot compute:
-- it needs an award-history ledger keyed by tax number, which is exactly what
-- this corpus is and a notice feed is not.
CREATE TABLE IF NOT EXISTS proc.company_profile_buyer (
    user_id     bigint  NOT NULL
                REFERENCES proc.company_profile(user_id) ON DELETE CASCADE,
    authority_id text   NOT NULL,
    n_acts      integer NOT NULL DEFAULT 0,
    total_value numeric NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, authority_id)
);

CREATE INDEX IF NOT EXISTS ix_company_profile_cpv_prefix
    ON proc.company_profile_cpv (cpv_prefix);
CREATE INDEX IF NOT EXISTS ix_company_profile_nuts_prefix
    ON proc.company_profile_nuts (nuts_prefix);

COMMIT;
