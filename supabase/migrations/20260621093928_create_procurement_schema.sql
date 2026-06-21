
-- Core tables for Greek Public Procurement Explorer

-- Authorities (contracting bodies)
CREATE TABLE authorities (
  id text PRIMARY KEY,
  name text NOT NULL,
  vat_number text,
  nuts_code text,
  city text,
  postal_code text,
  country text DEFAULT 'GR',
  act_count integer DEFAULT 0,
  total_value numeric DEFAULT 0,
  created_at timestamptz DEFAULT now()
);
ALTER TABLE authorities ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public_read_authorities" ON authorities FOR SELECT TO anon USING (true);

-- Contractors (economic operators)
CREATE TABLE contractors (
  id bigserial PRIMARY KEY,
  name text NOT NULL,
  vat_number text UNIQUE,
  country text DEFAULT 'GR',
  act_count integer DEFAULT 0,
  total_value numeric DEFAULT 0,
  created_at timestamptz DEFAULT now()
);
ALTER TABLE contractors ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public_read_contractors" ON contractors FOR SELECT TO anon USING (true);

-- CPV codes
CREATE TABLE cpv_codes (
  code varchar(10) PRIMARY KEY,
  description text NOT NULL
);
ALTER TABLE cpv_codes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public_read_cpv_codes" ON cpv_codes FOR SELECT TO anon USING (true);

-- Main fact table: procurement acts
CREATE TABLE procurement_acts (
  adam text PRIMARY KEY,
  type text NOT NULL CHECK (type IN ('request', 'notice', 'auction', 'contract', 'payment')),
  title text,
  authority_id text REFERENCES authorities(id),
  signed_date date,
  submission_date timestamptz,
  final_submission_date timestamptz,
  contract_type text,
  procedure_type text,
  status text DEFAULT 'active' CHECK (status IN ('active', 'cancelled', 'modified')),
  budget numeric,
  cost_without_vat numeric,
  cost_with_vat numeric,
  currency text DEFAULT 'EUR',
  nuts_code text,
  city text,
  cpv_main varchar(10),
  created_at timestamptz DEFAULT now()
);
ALTER TABLE procurement_acts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public_read_acts" ON procurement_acts FOR SELECT TO anon USING (true);

-- Act awards (links acts to contractors)
CREATE TABLE act_awards (
  id bigserial PRIMARY KEY,
  act_adam text REFERENCES procurement_acts(adam),
  contractor_id bigint REFERENCES contractors(id),
  awarded_value numeric,
  role text DEFAULT 'winner'
);
ALTER TABLE act_awards ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public_read_awards" ON act_awards FOR SELECT TO anon USING (true);

-- Analytics summary (materialized as a regular table for simplicity)
CREATE TABLE analytics_summary (
  id serial PRIMARY KEY,
  metric_name text NOT NULL,
  metric_value numeric,
  period text,
  category text,
  updated_at timestamptz DEFAULT now()
);
ALTER TABLE analytics_summary ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public_read_analytics" ON analytics_summary FOR SELECT TO anon USING (true);

-- Indexes for performance
CREATE INDEX idx_acts_type ON procurement_acts(type);
CREATE INDEX idx_acts_authority ON procurement_acts(authority_id);
CREATE INDEX idx_acts_submission ON procurement_acts(submission_date DESC);
CREATE INDEX idx_acts_status ON procurement_acts(status);
CREATE INDEX idx_acts_cpv ON procurement_acts(cpv_main);
CREATE INDEX idx_acts_cost ON procurement_acts(cost_without_vat DESC NULLS LAST);
CREATE INDEX idx_awards_act ON act_awards(act_adam);
CREATE INDEX idx_awards_contractor ON act_awards(contractor_id);
