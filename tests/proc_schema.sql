--
-- PostgreSQL database dump
--

\restrict ui3WVvTuYFkVuAyhDo8UOcl6P6lhwaAHakQq2pxP2ferrzFVEA5rV9z5FfyzMcp

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.10 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: proc; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA proc;


--
-- Name: act_type; Type: TYPE; Schema: proc; Owner: -
--

CREATE TYPE proc.act_type AS ENUM (
    'request',
    'notice',
    'auction',
    'contract',
    'payment'
);


--
-- Name: link_relation; Type: TYPE; Schema: proc; Owner: -
--

CREATE TYPE proc.link_relation AS ENUM (
    'request_to_notice',
    'request_to_auction',
    'request_to_contract',
    'request_to_payment',
    'request_approves',
    'notice_to_auction',
    'notice_amends_notice',
    'framework_of_notice',
    'notice_related',
    'notice_uses_request',
    'auction_to_contract',
    'auction_to_payment',
    'auction_amends_auction',
    'auction_under_notice',
    'contract_to_payment',
    'contract_from_auction',
    'contract_from_request',
    'contract_prev',
    'contract_next',
    'payment_for_contract',
    'payment_for_auction',
    'payment_for_request',
    'generic'
);


--
-- Name: participation_role; Type: TYPE; Schema: proc; Owner: -
--

CREATE TYPE proc.participation_role AS ENUM (
    'winner',
    'bidder',
    'subcontractor',
    'consortium_member'
);


--
-- Name: analytics_value_ceiling(); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.analytics_value_ceiling() RETURNS numeric
    LANGUAGE sql IMMUTABLE
    AS $$ SELECT 500000000::numeric $$;


--
-- Name: canon_authority(text); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.canon_authority(org text) RETURNS text
    LANGUAGE sql STABLE
    AS $$
  SELECT COALESCE(g.canonical_key, org)
  FROM (SELECT org) s
  LEFT JOIN proc.entity_member m
    ON m.kind='authority' AND m.member_key = org
  LEFT JOIN proc.entity_group g ON g.id = m.group_id;
$$;


--
-- Name: canon_contractor(text); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.canon_contractor(vat text) RETURNS text
    LANGUAGE sql STABLE
    AS $$
  SELECT COALESCE(g.canonical_key, vat)
  FROM (SELECT vat) s
  LEFT JOIN proc.entity_member m
    ON m.kind='contractor' AND m.member_key = vat
  LEFT JOIN proc.entity_group g ON g.id = m.group_id;
$$;


--
-- Name: compute_procedure_family(text); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.compute_procedure_family(raw text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    s text := lower(coalesce(raw, ''));
BEGIN
    IF raw IS NULL OR btrim(raw) = '' THEN
        RETURN NULL;
    END IF;

    -- ---- numeric codes (map to the same families as the text variants) ----
    CASE btrim(raw)
        WHEN '1'  THEN RETURN 'Ανοιχτή διαδικασία';
        WHEN '2'  THEN RETURN 'Κλειστή διαδικασία';
        WHEN '4'  THEN RETURN 'Ανταγωνιστικός διάλογος';
        WHEN '6'  THEN RETURN 'Απευθείας ανάθεση';
        WHEN '7'  THEN RETURN 'Ανταγωνιστική διαδικασία με διαπραγμάτευση';
        WHEN '11' THEN RETURN 'Σύμπραξη καινοτομίας';
        WHEN '12' THEN RETURN 'Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση';
        WHEN '13' THEN RETURN 'Διαπραγμάτευση με προηγούμενη προκήρυξη';
        WHEN '18' THEN RETURN 'Διαδικασία άρθρου 128';
        WHEN '9'  THEN RETURN 'Άλλο / Άγνωστο';
        WHEN '16' THEN RETURN 'Άλλο / Άγνωστο';
        ELSE
            -- not a bare code we know; fall through to text matching
    END CASE;

    -- ---- text variants: group by distinctive substring ----
    -- order matters: check more specific families before generic ones.
    IF s LIKE '%απευθείας%' THEN
        RETURN 'Απευθείας ανάθεση';
    ELSIF s LIKE '%συνοπτικ%' THEN
        RETURN 'Συνοπτικός διαγωνισμός';
    ELSIF s LIKE '%ανοιχτή%' OR s LIKE '%ανοικτή%' THEN
        RETURN 'Ανοιχτή διαδικασία';
    ELSIF s LIKE '%κλειστή%' THEN
        RETURN 'Κλειστή διαδικασία';
    ELSIF s LIKE '%ανταγωνιστικός διάλογος%' THEN
        RETURN 'Ανταγωνιστικός διάλογος';
    ELSIF s LIKE '%ανταγωνιστική%διαπραγμάτευση%' THEN
        RETURN 'Ανταγωνιστική διαδικασία με διαπραγμάτευση';
    ELSIF s LIKE '%με προηγούμενη προκήρυξη%' OR s LIKE '%αρ.266%' OR s LIKE '%αρ. 266%' THEN
        RETURN 'Διαπραγμάτευση με προηγούμενη προκήρυξη';
    ELSIF s LIKE '%χωρίς προηγούμενη δημοσίευση%' THEN
        RETURN 'Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση';
    ELSIF s LIKE '%σύμπραξη καινοτομίας%' THEN
        RETURN 'Σύμπραξη καινοτομίας';
    ELSIF s LIKE '%άρθρου 128%' THEN
        RETURN 'Διαδικασία άρθρου 128';
    ELSIF s LIKE '%κάτω των ορίων%' THEN
        RETURN 'Διαδικασία κάτω των ορίων εκτός ν.4412/2016';
    ELSE
        RETURN 'Άλλο / Άγνωστο';
    END IF;
END;
$$;


--
-- Name: f_unaccent(text); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.f_unaccent(text) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $_$ SELECT proc.unaccent($1) $_$;


--
-- Name: is_analytics_eligible(text, numeric, boolean); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.is_analytics_eligible(p_adam text, p_value numeric, p_cancelled boolean) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT (NOT coalesce(p_cancelled, false))
       AND (proc.resolved_value(p_adam, p_value) IS NULL
            OR proc.resolved_value(p_adam, p_value) <= proc.analytics_value_ceiling())
       AND NOT EXISTS (
            SELECT 1 FROM proc.v_act_annotation_current a
            WHERE a.adam = p_adam AND a.flag = 'suspicious')
       AND NOT EXISTS (
            SELECT 1 FROM proc.procurement_act pa
            WHERE pa.adam = p_adam
              AND pa.data_source = ANY (ARRAY['diavgeia', 'ted']));
$$;


--
-- Name: refresh_analytics(); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.refresh_analytics() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    BEGIN
        PERFORM proc.refresh_procedure_family();
    EXCEPTION WHEN undefined_function THEN NULL;
    END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_totals;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_authorities;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_contractors;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_monthly;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW proc.mv_analytics_cpv;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_contractor_counts;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_authority_counts;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    -- explore overview views (this migration)
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_authority;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_authority_name;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_contractor;
    EXCEPTION WHEN undefined_table THEN NULL; END;
    BEGIN REFRESH MATERIALIZED VIEW CONCURRENTLY proc.mv_explore_contractor_name;
    EXCEPTION WHEN undefined_table THEN NULL; END;
END;
$$;


--
-- Name: refresh_procedure_family(); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.refresh_procedure_family() RETURNS void
    LANGUAGE sql
    AS $$
    UPDATE proc.procurement_act
    SET procedure_family = proc.compute_procedure_family(procedure_type_code)
    WHERE procedure_family IS DISTINCT FROM
          proc.compute_procedure_family(procedure_type_code);
$$;


--
-- Name: resolved_item_cost(text, integer, numeric); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.resolved_item_cost(p_adam text, p_line_no integer, p_source numeric) RETURNS numeric
    LANGUAGE sql STABLE
    AS $$
    SELECT COALESCE(
        (SELECT corrected_cost_without_vat FROM proc.v_line_item_correction_current
         WHERE adam = p_adam AND line_no = p_line_no
           AND corrected_cost_without_vat IS NOT NULL),
        p_source);
$$;


--
-- Name: resolved_value(text, numeric); Type: FUNCTION; Schema: proc; Owner: -
--

CREATE FUNCTION proc.resolved_value(p_adam text, p_source numeric) RETURNS numeric
    LANGUAGE sql STABLE
    AS $$
    SELECT COALESCE(
        (SELECT corrected_value FROM proc.v_act_annotation_current
         WHERE adam = p_adam AND corrected_value IS NOT NULL),
        p_source);
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: act_additional_contract_type; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_additional_contract_type (
    adam text NOT NULL,
    contract_type_code text NOT NULL
);


--
-- Name: act_annotation; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_annotation (
    id bigint NOT NULL,
    adam text NOT NULL,
    note text,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    flag text,
    author text DEFAULT '(anonymous)'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded boolean DEFAULT false NOT NULL,
    corrected_value numeric(18,2),
    corrected_value_without_vat numeric(18,2)
);


--
-- Name: act_annotation_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.act_annotation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: act_annotation_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.act_annotation_id_seq OWNED BY proc.act_annotation.id;


--
-- Name: act_centralized_market; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_centralized_market (
    adam text NOT NULL,
    market_code text NOT NULL
);


--
-- Name: act_cpv; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_cpv (
    adam text NOT NULL,
    cpv_code text NOT NULL,
    ord integer DEFAULT 0 NOT NULL
);


--
-- Name: act_diavgeia_link; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_diavgeia_link (
    adam text NOT NULL,
    ada text NOT NULL,
    link_kind text NOT NULL
);


--
-- Name: act_funding; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_funding (
    id bigint NOT NULL,
    adam text NOT NULL,
    funding_kind text NOT NULL,
    funding_ref text
);


--
-- Name: act_funding_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.act_funding_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: act_funding_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.act_funding_id_seq OWNED BY proc.act_funding.id;


--
-- Name: act_group; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_group (
    id bigint NOT NULL,
    label text,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE act_group; Type: COMMENT; Schema: proc; Owner: -
--

COMMENT ON TABLE proc.act_group IS 'Act interconnection group (one tender lifecycle). Admin overlay, separate from proc.act_link (the official source graph).';


--
-- Name: act_group_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.act_group_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: act_group_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.act_group_id_seq OWNED BY proc.act_group.id;


--
-- Name: act_group_member; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_group_member (
    adam text NOT NULL,
    group_id bigint NOT NULL,
    is_duplicate boolean DEFAULT false NOT NULL,
    duplicate_of text,
    added_by text,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: act_link; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_link (
    source_adam text NOT NULL,
    target_adam text NOT NULL,
    relation proc.link_relation NOT NULL,
    discovered_at timestamp with time zone DEFAULT now()
);


--
-- Name: act_nuts; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_nuts (
    adam text NOT NULL,
    nuts_code character varying(8) NOT NULL
);


--
-- Name: act_object_detail; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_object_detail (
    id bigint NOT NULL,
    adam text NOT NULL,
    line_no integer,
    short_description text,
    quantity numeric,
    unit_code text,
    cost_without_vat numeric(18,2),
    vat_rate text,
    currency_code text,
    green_contract_code text,
    good_services_code text,
    budget_code text,
    delivery_address text,
    delivery_city text,
    delivery_street text,
    delivery_postal_code text,
    delivery_country text,
    city_of_construction text
);


--
-- Name: act_object_detail_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.act_object_detail_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: act_object_detail_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.act_object_detail_id_seq OWNED BY proc.act_object_detail.id;


--
-- Name: act_operator; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_operator (
    id bigint NOT NULL,
    adam text NOT NULL,
    operator_id bigint NOT NULL,
    role proc.participation_role NOT NULL,
    awarded_value_without_vat numeric(18,2),
    awarded_value_with_vat numeric(18,2)
);


--
-- Name: act_operator_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.act_operator_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: act_operator_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.act_operator_id_seq OWNED BY proc.act_operator.id;


--
-- Name: act_systemic_number; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.act_systemic_number (
    adam text NOT NULL,
    systemic_number text NOT NULL
);


--
-- Name: admin_action; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.admin_action (
    id bigint NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    user_id bigint,
    username text,
    method text NOT NULL,
    path text NOT NULL,
    status_code integer,
    ip text
);


--
-- Name: admin_action_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.admin_action_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_action_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.admin_action_id_seq OWNED BY proc.admin_action.id;


--
-- Name: app_user; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.app_user (
    id bigint NOT NULL,
    username text NOT NULL,
    email text,
    password_hash text NOT NULL,
    role text DEFAULT 'customer'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login_at timestamp with time zone,
    mfa_secret text,
    mfa_enabled boolean DEFAULT false NOT NULL,
    mfa_recovery_codes text[] DEFAULT '{}'::text[] NOT NULL,
    session_version integer DEFAULT 0 NOT NULL,
    must_change_password boolean DEFAULT false NOT NULL,
    CONSTRAINT app_user_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'customer'::text])))
);


--
-- Name: TABLE app_user; Type: COMMENT; Schema: proc; Owner: -
--

COMMENT ON TABLE proc.app_user IS 'Application accounts. role=admin (full + /admin) | customer (full read). Anonymous visitors have no row and get the public teaser tier.';


--
-- Name: app_user_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.app_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: app_user_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.app_user_id_seq OWNED BY proc.app_user.id;


--
-- Name: authority; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.authority (
    org_id text NOT NULL,
    name text NOT NULL,
    vat_number text,
    is_greek_vat boolean,
    aaht text,
    type_code text,
    classification_code text,
    nuts_code character varying(8),
    city text,
    postal_code text,
    country text,
    diavgeia_org_uid text,
    source text DEFAULT 'khmdhs'::text,
    first_seen timestamp with time zone DEFAULT now(),
    last_seen timestamp with time zone DEFAULT now(),
    name_original text,
    name_edited_at timestamp with time zone,
    identifier text,
    orgdb_id text,
    street_address text,
    contact_email text,
    contact_phone text,
    contact_fax text,
    contact_url text
);


--
-- Name: code_list; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.code_list (
    domain text NOT NULL,
    code text NOT NULL,
    label_el text,
    label_en text
);


--
-- Name: TABLE code_list; Type: COMMENT; Schema: proc; Owner: -
--

COMMENT ON TABLE proc.code_list IS 'Generic lookup for all KHMDHS enumerations. Seed/refresh from live {key,value} responses, not from the submission docs (codes differ between submit and retrieve).';


--
-- Name: cpv_category_map; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.cpv_category_map (
    cpv_code character varying(10) NOT NULL,
    category_id integer NOT NULL,
    subcategory_id integer NOT NULL
);


--
-- Name: cpv_code; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.cpv_code (
    cpv_code character varying(10) NOT NULL,
    description text,
    description_tsv tsvector GENERATED ALWAYS AS (to_tsvector('greek'::regconfig, COALESCE(description, ''::text))) STORED,
    description_en text
);


--
-- Name: customer_call; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.customer_call (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    subject text,
    direction text DEFAULT 'outgoing'::text NOT NULL,
    status text DEFAULT 'planned'::text NOT NULL,
    scheduled_at timestamp with time zone,
    outcome text,
    assigned_to bigint,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone,
    CONSTRAINT customer_call_direction_check CHECK ((direction = ANY (ARRAY['incoming'::text, 'outgoing'::text]))),
    CONSTRAINT customer_call_status_check CHECK ((status = ANY (ARRAY['planned'::text, 'held'::text, 'not_held'::text, 'not_answered'::text, 'cancelled'::text])))
);


--
-- Name: customer_call_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.customer_call_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_call_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.customer_call_id_seq OWNED BY proc.customer_call.id;


--
-- Name: customer_note; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.customer_note (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    body text NOT NULL,
    author_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: customer_note_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.customer_note_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_note_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.customer_note_id_seq OWNED BY proc.customer_note.id;


--
-- Name: customer_profile; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.customer_profile (
    user_id bigint NOT NULL,
    full_name text,
    phone text,
    mobile text,
    job_title text,
    company text,
    vat_number text,
    industry text,
    country text,
    city text,
    address text,
    lead_source text,
    about text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by bigint
);


--
-- Name: TABLE customer_profile; Type: COMMENT; Schema: proc; Owner: -
--

COMMENT ON TABLE proc.customer_profile IS 'Admin-editable CRM profile fields for a customer account (1:1 with proc.app_user). Missing row = empty profile.';


--
-- Name: customer_task; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.customer_task (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    subject text NOT NULL,
    body text,
    status text DEFAULT 'open'::text NOT NULL,
    due_at timestamp with time zone,
    outcome text,
    assigned_to bigint,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT customer_task_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text, 'cancelled'::text])))
);


--
-- Name: customer_task_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.customer_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_task_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.customer_task_id_seq OWNED BY proc.customer_task.id;


--
-- Name: diavgeia_attachment; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_attachment (
    id bigint NOT NULL,
    ada text NOT NULL,
    filename text,
    mimetype text,
    url text,
    checksum text
);


--
-- Name: diavgeia_attachment_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.diavgeia_attachment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diavgeia_attachment_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.diavgeia_attachment_id_seq OWNED BY proc.diavgeia_attachment.id;


--
-- Name: diavgeia_decision; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_decision (
    ada text NOT NULL,
    subject text,
    decision_type text,
    organization_uid text,
    signer_uid text,
    issue_date date,
    document_url text,
    raw_json jsonb,
    ingested_at timestamp with time zone DEFAULT now(),
    protocol_number text,
    status text,
    version_id text,
    corrected_version_id text,
    private_data boolean,
    publish_timestamp timestamp with time zone,
    submission_timestamp timestamp with time zone,
    document_checksum text,
    api_url text,
    authority_id text,
    document_type text,
    amount numeric(18,2),
    currency_code text,
    contest_progress_type text,
    selection_criterion text,
    manifest_contract_type text,
    org_budget_code text,
    text_related_ada text,
    contract_type text,
    number_of_people integer,
    financed_project boolean,
    duration text
);


--
-- Name: diavgeia_decision_cpv; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_decision_cpv (
    ada text NOT NULL,
    cpv_code character varying(10) NOT NULL,
    ord integer
);


--
-- Name: diavgeia_decision_person; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_decision_person (
    id bigint NOT NULL,
    ada text NOT NULL,
    operator_id bigint,
    afm text,
    name text,
    afm_type text,
    afm_country text,
    ord integer
);


--
-- Name: diavgeia_decision_person_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.diavgeia_decision_person_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diavgeia_decision_person_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.diavgeia_decision_person_id_seq OWNED BY proc.diavgeia_decision_person.id;


--
-- Name: diavgeia_decision_signer; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_decision_signer (
    ada text NOT NULL,
    signer_uid text NOT NULL,
    ord integer
);


--
-- Name: diavgeia_decision_thematic; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_decision_thematic (
    ada text NOT NULL,
    thematic_uid text NOT NULL
);


--
-- Name: diavgeia_decision_unit; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_decision_unit (
    ada text NOT NULL,
    unit_uid text NOT NULL,
    ord integer
);


--
-- Name: diavgeia_ingest_window; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_ingest_window (
    id bigint NOT NULL,
    decision_type text NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    pages_done integer DEFAULT 0,
    total_pages integer,
    last_error text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone
);


--
-- Name: diavgeia_ingest_window_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.diavgeia_ingest_window_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: diavgeia_ingest_window_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.diavgeia_ingest_window_id_seq OWNED BY proc.diavgeia_ingest_window.id;


--
-- Name: diavgeia_related; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_related (
    source_ada text NOT NULL,
    target_ada text NOT NULL,
    kind text NOT NULL,
    discovered_at timestamp with time zone DEFAULT now()
);


--
-- Name: diavgeia_signer; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_signer (
    uid text NOT NULL,
    first_name text,
    last_name text
);


--
-- Name: diavgeia_unit; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.diavgeia_unit (
    uid text NOT NULL,
    label text,
    category text
);


--
-- Name: economic_operator; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.economic_operator (
    operator_id bigint NOT NULL,
    vat_number text,
    name text NOT NULL,
    is_greek_vat boolean,
    country text,
    first_seen timestamp with time zone DEFAULT now(),
    last_seen timestamp with time zone DEFAULT now(),
    name_original text,
    name_edited_at timestamp with time zone,
    statistical_or_tax_number text,
    contact_person text,
    orgdb_id text,
    city text,
    postal_code text,
    nuts_code character varying(5),
    street_address text,
    contact_email text,
    contact_phone text,
    contact_fax text,
    contact_url text,
    ar_gemi text
);


--
-- Name: economic_operator_operator_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.economic_operator_operator_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: economic_operator_operator_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.economic_operator_operator_id_seq OWNED BY proc.economic_operator.operator_id;


--
-- Name: entity_group; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.entity_group (
    id bigint NOT NULL,
    kind text NOT NULL,
    canonical_key text NOT NULL,
    display_name text,
    created_by text DEFAULT '(anonymous)'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    note text,
    CONSTRAINT entity_group_kind_check CHECK ((kind = ANY (ARRAY['contractor'::text, 'authority'::text])))
);


--
-- Name: entity_group_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.entity_group_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: entity_group_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.entity_group_id_seq OWNED BY proc.entity_group.id;


--
-- Name: entity_member; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.entity_member (
    group_id bigint NOT NULL,
    kind text NOT NULL,
    member_key text NOT NULL,
    CONSTRAINT entity_member_kind_check CHECK ((kind = ANY (ARRAY['contractor'::text, 'authority'::text])))
);


--
-- Name: extracted_table; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.extracted_table (
    id bigint NOT NULL,
    adam text NOT NULL,
    source text NOT NULL,
    locator text NOT NULL,
    rows jsonb NOT NULL,
    n_rows integer NOT NULL,
    n_cols integer NOT NULL,
    is_published boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('greek'::regconfig, COALESCE((jsonb_path_query_array(rows, '$[*][*]'::jsonpath))::text, ''::text))) STORED
);


--
-- Name: extracted_table_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.extracted_table_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: extracted_table_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.extracted_table_id_seq OWNED BY proc.extracted_table.id;


--
-- Name: gemi_enrichment; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.gemi_enrichment (
    afm text NOT NULL,
    ar_gemi text,
    legal_name text,
    trade_title text,
    legal_type text,
    status text,
    status_id integer,
    is_branch boolean,
    street text,
    street_number text,
    zip_code text,
    city text,
    municipality text,
    prefecture text,
    phone text,
    fax text,
    email text,
    url text,
    primary_kad text,
    primary_kad_descr text,
    activities_active jsonb DEFAULT '[]'::jsonb NOT NULL,
    incorporation_date date,
    raw jsonb,
    match_count integer,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    fetch_status text DEFAULT 'ok'::text NOT NULL
);


--
-- Name: ingest_act_log; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.ingest_act_log (
    id bigint NOT NULL,
    job_id integer NOT NULL,
    adam text NOT NULL,
    act_type text,
    title text,
    action text NOT NULL,
    full_text_extracted boolean DEFAULT false NOT NULL,
    full_text_chars integer,
    full_text_note text,
    logged_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ingest_act_log_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.ingest_act_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ingest_act_log_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.ingest_act_log_id_seq OWNED BY proc.ingest_act_log.id;


--
-- Name: ingest_job; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.ingest_job (
    id bigint NOT NULL,
    pid integer,
    status text DEFAULT 'running'::text NOT NULL,
    types text[] NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    resume boolean DEFAULT false NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    exit_code integer,
    log_path text,
    last_error text,
    source text DEFAULT 'khmdhs'::text NOT NULL,
    command text[],
    job_env jsonb,
    log_text text,
    worker_id text,
    heartbeat_at timestamp with time zone,
    cancel_requested boolean DEFAULT false NOT NULL,
    queued_at timestamp with time zone
);


--
-- Name: COLUMN ingest_job.source; Type: COMMENT; Schema: proc; Owner: -
--

COMMENT ON COLUMN proc.ingest_job.source IS 'which harvester this job ran: khmdhs | diavgeia';


--
-- Name: ingest_job_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.ingest_job_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ingest_job_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.ingest_job_id_seq OWNED BY proc.ingest_job.id;


--
-- Name: ingest_window; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.ingest_window (
    id bigint NOT NULL,
    act_type proc.act_type NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    pages_done integer DEFAULT 0,
    total_pages integer,
    last_error text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone
);


--
-- Name: ingest_window_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.ingest_window_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ingest_window_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.ingest_window_id_seq OWNED BY proc.ingest_window.id;


--
-- Name: line_item_correction; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.line_item_correction (
    id bigint NOT NULL,
    adam text NOT NULL,
    line_no integer NOT NULL,
    corrected_cost_without_vat numeric(18,2),
    note text,
    author text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded boolean DEFAULT false NOT NULL
);


--
-- Name: line_item_correction_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.line_item_correction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: line_item_correction_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.line_item_correction_id_seq OWNED BY proc.line_item_correction.id;


--
-- Name: login_throttle; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.login_throttle (
    key text NOT NULL,
    fail_count integer DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: match_rule; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.match_rule (
    code text NOT NULL,
    label text NOT NULL,
    kind text NOT NULL,
    field text,
    weight integer NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT match_rule_kind_check CHECK ((kind = ANY (ARRAY['identifier'::text, 'authority'::text]))),
    CONSTRAINT match_rule_weight_check CHECK ((weight >= 0))
);


--
-- Name: match_setting; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.match_setting (
    key text NOT NULL,
    value integer NOT NULL
);


--
-- Name: procurement_act; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.procurement_act (
    adam text NOT NULL,
    type proc.act_type NOT NULL,
    title text,
    signed_date date,
    submission_date timestamp with time zone,
    last_update_date timestamp with time zone,
    published_eu_date date,
    final_submission_date timestamp with time zone,
    procurement_delivery_date date,
    cancelled boolean DEFAULT false,
    cancellation_date timestamp with time zone,
    cancellation_type text,
    cancellation_reason text,
    cancellation_ada text,
    is_modified boolean,
    amends_previous boolean,
    amended_adam text,
    contract_type_code text,
    mixed_contract boolean,
    procedure_type_code text,
    award_procedure_code text,
    criteria_code text,
    legal_context_code text,
    notice_type_code text,
    conducting_proceedings_code text,
    digital_platform_code text,
    contracting_authority_activity_code text,
    budget numeric(18,2),
    total_cost_without_vat numeric(18,2),
    total_cost_with_vat numeric(18,2),
    currency_code text,
    nuts_code character varying(8),
    city text,
    postal_code text,
    country text,
    authority_id text,
    org_unit_id text,
    signer_id text,
    number_of_sections bigint,
    contract_duration numeric,
    contract_duration_unit text,
    offers_valid_time numeric,
    offers_valid_time_unit text,
    max_number_of_contractors bigint,
    option_right boolean,
    option_right_description text,
    framework_agreement_adam text,
    bidding_website text,
    contract_number text,
    contract_signed_date date,
    start_date date,
    end_date date,
    no_end_date boolean,
    assign_criteria_code text,
    bids_submitted bigint,
    max_bids_submitted bigint,
    is_credit boolean,
    payment_commitment_code text,
    contract_value numeric(18,2),
    approval_ada text,
    commitment_no text,
    protocol_number text,
    author_email text,
    awarded_operator_id bigint,
    award_value_without_vat numeric(18,2),
    award_value_with_vat numeric(18,2),
    raw_json jsonb,
    source_endpoint text,
    ingested_at timestamp with time zone DEFAULT now(),
    procedure_family text,
    full_text text,
    full_text_extracted_at timestamp with time zone,
    full_text_source text,
    origin text DEFAULT 'import'::text NOT NULL,
    data_source text,
    source_url text,
    authored_by text,
    last_edited_by text,
    last_edited_at timestamp with time zone,
    external_id text,
    source_uuid text,
    authority_reference text,
    reference_number text,
    short_description text,
    lot_number text,
    language text,
    nature_of_contract text,
    type_of_document text,
    subtype_of_document text,
    procedure_label text,
    source_status text,
    regulation_of_procurement text,
    e_auction text,
    dynamic_purchasing_system text,
    has_attachments boolean,
    send_with_next_sre boolean DEFAULT false,
    search_tsv tsvector GENERATED ALWAYS AS ((setweight(to_tsvector('greek'::regconfig, COALESCE(title, ''::text)), 'A'::"char") || setweight(to_tsvector('greek'::regconfig, COALESCE(full_text, ''::text)), 'B'::"char"))) STORED,
    full_text_html text,
    divided_into_lots boolean,
    is_framework_agreement boolean,
    type_of_bid_required text,
    alternative_offers_allowed boolean,
    price_weighting numeric(8,2),
    number_of_offers integer,
    prolongation_option boolean,
    prolongation_in_months integer,
    vat_rate numeric(5,2),
    vat_included boolean,
    value_eur numeric(19,2),
    value_usd numeric(19,2),
    estimated_price_min numeric(19,2),
    estimated_price_max numeric(19,2),
    yearly_budget numeric(19,2),
    bid_bond_amount numeric(19,2),
    eligibility_criteria text,
    eligibility_category text,
    journal_number text,
    eprocurement_portal text,
    contact_email text,
    contact_phone text,
    contact_fax text,
    street_address text,
    contact_url text,
    assign_criteria_label text,
    CONSTRAINT procurement_act_origin_chk CHECK ((origin = ANY (ARRAY['import'::text, 'authored'::text])))
);


--
-- Name: mv_analytics_authorities; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_analytics_authorities AS
 SELECT proc.canon_authority(authority_id) AS authority_id,
    count(*) AS n_contracts,
    COALESCE(sum(proc.resolved_value(adam, total_cost_with_vat)), (0)::numeric) AS awarded_value
   FROM proc.procurement_act a
  WHERE ((type = 'contract'::proc.act_type) AND (authority_id IS NOT NULL) AND proc.is_analytics_eligible(adam, total_cost_with_vat, cancelled))
  GROUP BY (proc.canon_authority(authority_id))
  WITH NO DATA;


--
-- Name: mv_analytics_contractors; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_analytics_contractors AS
 SELECT proc.canon_contractor(eo.vat_number) AS vat_number,
    count(DISTINCT a.adam) AS n_contracts,
    COALESCE(sum(COALESCE(ao.awarded_value_with_vat, proc.resolved_value(a.adam, a.total_cost_with_vat))), (0)::numeric) AS awarded_value
   FROM ((proc.act_operator ao
     JOIN proc.economic_operator eo ON ((eo.operator_id = ao.operator_id)))
     JOIN proc.procurement_act a ON ((a.adam = ao.adam)))
  WHERE ((a.type = 'contract'::proc.act_type) AND proc.is_analytics_eligible(a.adam, a.total_cost_with_vat, a.cancelled))
  GROUP BY (proc.canon_contractor(eo.vat_number))
  WITH NO DATA;


--
-- Name: object_detail_cpv; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.object_detail_cpv (
    object_detail_id bigint NOT NULL,
    cpv_code character varying(10) NOT NULL
);


--
-- Name: v_act_annotation_current; Type: VIEW; Schema: proc; Owner: -
--

CREATE VIEW proc.v_act_annotation_current AS
 SELECT DISTINCT ON (adam) adam,
    id,
    note,
    tags,
    flag,
    author,
    created_at,
    corrected_value,
    corrected_value_without_vat
   FROM proc.act_annotation
  WHERE (NOT superseded)
  ORDER BY adam, created_at DESC;


--
-- Name: mv_analytics_cpv; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_analytics_cpv AS
 WITH items AS (
         SELECT a.type,
            a.adam,
            substr((oc.cpv_code)::text, 1, 2) AS division,
            proc.resolved_item_cost(a.adam, od.line_no, od.cost_without_vat) AS item_cost
           FROM ((proc.procurement_act a
             JOIN proc.act_object_detail od ON ((od.adam = a.adam)))
             JOIN proc.object_detail_cpv oc ON ((oc.object_detail_id = od.id)))
          WHERE ((a.type = ANY (ARRAY['notice'::proc.act_type, 'contract'::proc.act_type])) AND (NOT a.cancelled) AND ((a.data_source IS NULL) OR (a.data_source <> ALL (ARRAY['diavgeia'::text, 'ted'::text]))) AND (NOT (EXISTS ( SELECT 1
                   FROM proc.v_act_annotation_current an
                  WHERE ((an.adam = a.adam) AND (an.flag = 'suspicious'::text))))) AND ((a.type <> 'contract'::proc.act_type) OR (proc.resolved_value(a.adam, a.total_cost_with_vat) IS NULL) OR (proc.resolved_value(a.adam, a.total_cost_with_vat) <= proc.analytics_value_ceiling())))
        ), agg AS (
         SELECT items.division,
            count(DISTINCT items.adam) FILTER (WHERE (items.type = 'contract'::proc.act_type)) AS contract_count,
            COALESCE(sum(items.item_cost) FILTER (WHERE (items.type = 'contract'::proc.act_type)), (0)::numeric) AS contract_value,
            count(DISTINCT items.adam) FILTER (WHERE (items.type = 'notice'::proc.act_type)) AS notice_count,
            COALESCE(sum(items.item_cost) FILTER (WHERE (items.type = 'notice'::proc.act_type)), (0)::numeric) AS notice_value
           FROM items
          GROUP BY items.division
        )
 SELECT division,
    contract_count,
    contract_value,
    notice_count,
    notice_value,
    ( SELECT cpv_code.description
           FROM proc.cpv_code
          WHERE ((cpv_code.cpv_code)::text ~~ (agg.division || '000000-_'::text))
         LIMIT 1) AS label
   FROM agg
  ORDER BY contract_value DESC
  WITH NO DATA;


--
-- Name: mv_analytics_monthly; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_analytics_monthly AS
 SELECT (date_trunc('month'::text, submission_date))::date AS month,
    count(*) AS n_contracts,
    COALESCE(sum(proc.resolved_value(adam, total_cost_with_vat)), (0)::numeric) AS awarded_value
   FROM proc.procurement_act
  WHERE ((type = 'contract'::proc.act_type) AND (submission_date IS NOT NULL) AND proc.is_analytics_eligible(adam, total_cost_with_vat, cancelled))
  GROUP BY (date_trunc('month'::text, submission_date))
  ORDER BY ((date_trunc('month'::text, submission_date))::date)
  WITH NO DATA;


--
-- Name: mv_analytics_totals; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_analytics_totals AS
 SELECT count(*) AS n_contracts,
    COALESCE(sum(proc.resolved_value(adam, total_cost_with_vat)), (0)::numeric) AS awarded_value,
    count(DISTINCT authority_id) AS n_authorities,
    min(submission_date) AS earliest,
    max(submission_date) AS latest
   FROM proc.procurement_act
  WHERE ((type = 'contract'::proc.act_type) AND proc.is_analytics_eligible(adam, total_cost_with_vat, cancelled))
  WITH NO DATA;


--
-- Name: mv_authority_counts; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_authority_counts AS
 WITH keymap AS (
         SELECT auth.org_id,
            COALESCE(g.canonical_key, auth.org_id) AS canon_org
           FROM ((proc.authority auth
             LEFT JOIN proc.entity_member m ON (((m.kind = 'authority'::text) AND (m.member_key = auth.org_id))))
             LEFT JOIN proc.entity_group g ON ((g.id = m.group_id)))
        )
 SELECT k.canon_org AS org_id,
    count(a.adam) AS n_acts,
    count(a.adam) FILTER (WHERE (a.type = 'notice'::proc.act_type)) AS n_notices,
    count(a.adam) FILTER (WHERE (a.type = 'contract'::proc.act_type)) AS n_contracts
   FROM (keymap k
     JOIN proc.procurement_act a ON ((a.authority_id = k.org_id)))
  GROUP BY k.canon_org
  WITH NO DATA;


--
-- Name: mv_contractor_counts; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_contractor_counts AS
 WITH keymap AS (
         SELECT eo.operator_id,
            COALESCE(g.canonical_key, eo.vat_number) AS canon_vat
           FROM ((proc.economic_operator eo
             LEFT JOIN proc.entity_member m ON (((m.kind = 'contractor'::text) AND (m.member_key = eo.vat_number))))
             LEFT JOIN proc.entity_group g ON ((g.id = m.group_id)))
        )
 SELECT k.canon_vat AS vat_number,
    count(ao.adam) AS n_acts,
    count(DISTINCT a.authority_id) AS n_buyers
   FROM ((keymap k
     JOIN proc.act_operator ao ON ((ao.operator_id = k.operator_id)))
     LEFT JOIN proc.procurement_act a ON ((a.adam = ao.adam)))
  GROUP BY k.canon_vat
  WITH NO DATA;


--
-- Name: mv_explore_authority; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_explore_authority AS
 WITH base AS (
         SELECT COALESCE(ga.canonical_key, a.authority_id) AS auth_key,
            a.type,
            a.adam,
            COALESCE(corr.corrected_value, a.total_cost_with_vat) AS rv
           FROM (((proc.procurement_act a
             LEFT JOIN proc.v_act_annotation_current corr ON ((corr.adam = a.adam)))
             LEFT JOIN proc.entity_member ma ON (((ma.kind = 'authority'::text) AND (ma.member_key = a.authority_id))))
             LEFT JOIN proc.entity_group ga ON ((ga.id = ma.group_id)))
          WHERE ((a.authority_id IS NOT NULL) AND (NOT a.cancelled) AND ((COALESCE(corr.corrected_value, a.total_cost_with_vat) IS NULL) OR (COALESCE(corr.corrected_value, a.total_cost_with_vat) <= ('1000000000000'::bigint)::numeric)) AND (corr.flag IS DISTINCT FROM 'suspicious'::text))
        )
 SELECT auth_key,
    type,
    count(*) AS n,
    COALESCE(sum(rv), (0)::numeric) AS value
   FROM base
  GROUP BY auth_key, type
  WITH NO DATA;


--
-- Name: mv_explore_authority_name; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_explore_authority_name AS
 SELECT DISTINCT ON (COALESCE(ga.canonical_key, auth.org_id)) COALESCE(ga.canonical_key, auth.org_id) AS auth_key,
    auth.name
   FROM ((proc.authority auth
     LEFT JOIN proc.entity_member ma ON (((ma.kind = 'authority'::text) AND (ma.member_key = auth.org_id))))
     LEFT JOIN proc.entity_group ga ON ((ga.id = ma.group_id)))
  ORDER BY COALESCE(ga.canonical_key, auth.org_id), auth.name
  WITH NO DATA;


--
-- Name: mv_explore_contractor; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_explore_contractor AS
 WITH base AS (
         SELECT COALESCE(gc.canonical_key, eo.vat_number) AS contr_key,
            a.type,
            a.adam,
            COALESCE(ao.awarded_value_with_vat, COALESCE(corr.corrected_value, a.total_cost_with_vat)) AS rv
           FROM (((((proc.procurement_act a
             LEFT JOIN proc.v_act_annotation_current corr ON ((corr.adam = a.adam)))
             JOIN proc.act_operator ao ON ((ao.adam = a.adam)))
             JOIN proc.economic_operator eo ON ((eo.operator_id = ao.operator_id)))
             LEFT JOIN proc.entity_member mc ON (((mc.kind = 'contractor'::text) AND (mc.member_key = eo.vat_number))))
             LEFT JOIN proc.entity_group gc ON ((gc.id = mc.group_id)))
          WHERE ((NOT a.cancelled) AND ((COALESCE(corr.corrected_value, a.total_cost_with_vat) IS NULL) OR (COALESCE(corr.corrected_value, a.total_cost_with_vat) <= ('1000000000000'::bigint)::numeric)) AND (corr.flag IS DISTINCT FROM 'suspicious'::text))
        )
 SELECT contr_key,
    type,
    count(DISTINCT adam) AS n,
    COALESCE(sum(rv), (0)::numeric) AS value
   FROM base
  GROUP BY contr_key, type
  WITH NO DATA;


--
-- Name: mv_explore_contractor_name; Type: MATERIALIZED VIEW; Schema: proc; Owner: -
--

CREATE MATERIALIZED VIEW proc.mv_explore_contractor_name AS
 SELECT DISTINCT ON (COALESCE(gc.canonical_key, eo.vat_number)) COALESCE(gc.canonical_key, eo.vat_number) AS contr_key,
    eo.name,
    (gc.canonical_key IS NOT NULL) AS is_merged
   FROM ((proc.economic_operator eo
     LEFT JOIN proc.entity_member mc ON (((mc.kind = 'contractor'::text) AND (mc.member_key = eo.vat_number))))
     LEFT JOIN proc.entity_group gc ON ((gc.id = mc.group_id)))
  ORDER BY COALESCE(gc.canonical_key, eo.vat_number), eo.name
  WITH NO DATA;


--
-- Name: nuts_code; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.nuts_code (
    nuts_code character varying(8) NOT NULL,
    label text,
    parent_code character varying(8)
);


--
-- Name: org_unit; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.org_unit (
    unit_id text NOT NULL,
    name text,
    authority_id text
);


--
-- Name: postal_nuts; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.postal_nuts (
    postal_code text NOT NULL,
    nuts_code character varying(8) NOT NULL
);


--
-- Name: product; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.product (
    code text NOT NULL,
    name text NOT NULL,
    default_period_days integer NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT product_default_period_days_check CHECK ((default_period_days > 0))
);


--
-- Name: TABLE product; Type: COMMENT; Schema: proc; Owner: -
--

COMMENT ON TABLE proc.product IS 'Subscription products. Access is identical; default_period_days is the admin-editable default grant length (test=7, paid=365).';


--
-- Name: schema_migration; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.schema_migration (
    filename text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_by text,
    baseline boolean DEFAULT false NOT NULL
);


--
-- Name: search_profile; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.search_profile (
    id bigint NOT NULL,
    name text NOT NULL,
    scope text DEFAULT 'customer'::text NOT NULL,
    owner_user_id bigint,
    based_on_id bigint,
    params jsonb,
    is_published boolean DEFAULT false NOT NULL,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT search_profile_owner_chk CHECK ((((scope = 'portal'::text) AND (owner_user_id IS NULL)) OR ((scope = 'customer'::text) AND (owner_user_id IS NOT NULL)))),
    CONSTRAINT search_profile_params_chk CHECK (((params IS NOT NULL) OR (based_on_id IS NOT NULL))),
    CONSTRAINT search_profile_scope_chk CHECK ((scope = ANY (ARRAY['portal'::text, 'customer'::text])))
);


--
-- Name: search_profile_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.search_profile_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: search_profile_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.search_profile_id_seq OWNED BY proc.search_profile.id;


--
-- Name: signer; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.signer (
    signer_id text NOT NULL,
    name text,
    role_title text,
    authority_id text
);


--
-- Name: table_extract_job; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.table_extract_job (
    id bigint NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    filter_desc text,
    total_acts integer,
    save_tables boolean DEFAULT false NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    pid integer,
    log_path text,
    last_error text,
    command text[],
    job_env jsonb,
    log_text text,
    worker_id text,
    heartbeat_at timestamp with time zone,
    cancel_requested boolean DEFAULT false NOT NULL,
    queued_at timestamp with time zone,
    exit_code integer
);


--
-- Name: table_extract_job_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.table_extract_job_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: table_extract_job_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.table_extract_job_id_seq OWNED BY proc.table_extract_job.id;


--
-- Name: table_extract_log; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.table_extract_log (
    id bigint NOT NULL,
    job_id integer NOT NULL,
    adam text NOT NULL,
    act_type text,
    title text,
    outcome text NOT NULL,
    n_tables integer,
    n_files integer,
    note text,
    logged_at timestamp with time zone DEFAULT now() NOT NULL,
    n_saved integer DEFAULT 0 NOT NULL
);


--
-- Name: table_extract_log_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.table_extract_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: table_extract_log_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.table_extract_log_id_seq OWNED BY proc.table_extract_log.id;


--
-- Name: table_extract_target; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.table_extract_target (
    job_id integer NOT NULL,
    adam text NOT NULL,
    ord integer DEFAULT 0 NOT NULL,
    done boolean DEFAULT false NOT NULL
);


--
-- Name: ted_ingest_window; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.ted_ingest_window (
    id bigint NOT NULL,
    country text NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    notices integer,
    last_error text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone
);


--
-- Name: ted_ingest_window_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.ted_ingest_window_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ted_ingest_window_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.ted_ingest_window_id_seq OWNED BY proc.ted_ingest_window.id;


--
-- Name: ted_notice; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.ted_notice (
    publication_number text NOT NULL,
    notice_identifier text,
    procedure_identifier text,
    notice_type text,
    procedure_type text,
    publication_date date,
    title text,
    buyer_name text,
    buyer_country text,
    estimated_value numeric,
    currency text,
    winner_name text,
    winner_identifier text,
    contract_conclusion_date date,
    xml_url text,
    html_url text,
    pdf_url text,
    raw_json jsonb,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    description text,
    full_text text,
    full_text_extracted_at timestamp with time zone
);


--
-- Name: TABLE ted_notice; Type: COMMENT; Schema: proc; Owner: -
--

COMMENT ON TABLE proc.ted_notice IS 'Source-native TED notices (Search API metadata). Projected into proc.procurement_act as data_source=''ted'' (adam = ''TED:''||publication_number).';


--
-- Name: ted_notice_cpv; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.ted_notice_cpv (
    publication_number text NOT NULL,
    cpv_code character varying(10) NOT NULL,
    ord integer
);


--
-- Name: tender_category; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.tender_category (
    id integer NOT NULL,
    name text NOT NULL,
    name_en text
);


--
-- Name: tender_subcategory; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.tender_subcategory (
    id integer NOT NULL,
    name text NOT NULL,
    parent_category_id integer NOT NULL,
    name_en text
);


--
-- Name: unit_code; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.unit_code (
    code text NOT NULL,
    name text
);


--
-- Name: user_subscription; Type: TABLE; Schema: proc; Owner: -
--

CREATE TABLE proc.user_subscription (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    product_code text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    granted_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE user_subscription; Type: COMMENT; Schema: proc; Owner: -
--

COMMENT ON TABLE proc.user_subscription IS 'Per-grant subscription history. Current grant = greatest expires_at for the user; when that is in the past the customer falls back to the teaser.';


--
-- Name: user_subscription_id_seq; Type: SEQUENCE; Schema: proc; Owner: -
--

CREATE SEQUENCE proc.user_subscription_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_subscription_id_seq; Type: SEQUENCE OWNED BY; Schema: proc; Owner: -
--

ALTER SEQUENCE proc.user_subscription_id_seq OWNED BY proc.user_subscription.id;


--
-- Name: v_act_chain_any; Type: VIEW; Schema: proc; Owner: -
--

CREATE VIEW proc.v_act_chain_any AS
 WITH RECURSIVE chain AS (
         SELECT act_link.source_adam AS root,
            act_link.source_adam AS adam,
            0 AS depth,
            ARRAY[act_link.source_adam] AS path
           FROM proc.act_link
        UNION ALL
         SELECT c.root,
            l.target_adam,
            (c.depth + 1),
            (c.path || l.target_adam)
           FROM (chain c
             JOIN proc.act_link l ON ((l.source_adam = c.adam)))
          WHERE ((c.depth < 12) AND (NOT (l.target_adam = ANY (c.path))))
        )
 SELECT DISTINCT root,
    adam,
    depth,
    path
   FROM chain;


--
-- Name: v_entity_canonical; Type: VIEW; Schema: proc; Owner: -
--

CREATE VIEW proc.v_entity_canonical AS
 SELECT m.kind,
    m.member_key,
    g.canonical_key,
    g.display_name,
    g.id AS group_id
   FROM (proc.entity_member m
     JOIN proc.entity_group g ON ((g.id = m.group_id)));


--
-- Name: v_line_item_correction_current; Type: VIEW; Schema: proc; Owner: -
--

CREATE VIEW proc.v_line_item_correction_current AS
 SELECT DISTINCT ON (adam, line_no) adam,
    line_no,
    corrected_cost_without_vat,
    note,
    author,
    created_at
   FROM proc.line_item_correction
  WHERE (NOT superseded)
  ORDER BY adam, line_no, created_at DESC;


--
-- Name: act_annotation id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_annotation ALTER COLUMN id SET DEFAULT nextval('proc.act_annotation_id_seq'::regclass);


--
-- Name: act_funding id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_funding ALTER COLUMN id SET DEFAULT nextval('proc.act_funding_id_seq'::regclass);


--
-- Name: act_group id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_group ALTER COLUMN id SET DEFAULT nextval('proc.act_group_id_seq'::regclass);


--
-- Name: act_object_detail id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_object_detail ALTER COLUMN id SET DEFAULT nextval('proc.act_object_detail_id_seq'::regclass);


--
-- Name: act_operator id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_operator ALTER COLUMN id SET DEFAULT nextval('proc.act_operator_id_seq'::regclass);


--
-- Name: admin_action id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.admin_action ALTER COLUMN id SET DEFAULT nextval('proc.admin_action_id_seq'::regclass);


--
-- Name: app_user id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.app_user ALTER COLUMN id SET DEFAULT nextval('proc.app_user_id_seq'::regclass);


--
-- Name: customer_call id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_call ALTER COLUMN id SET DEFAULT nextval('proc.customer_call_id_seq'::regclass);


--
-- Name: customer_note id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_note ALTER COLUMN id SET DEFAULT nextval('proc.customer_note_id_seq'::regclass);


--
-- Name: customer_task id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_task ALTER COLUMN id SET DEFAULT nextval('proc.customer_task_id_seq'::regclass);


--
-- Name: diavgeia_attachment id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_attachment ALTER COLUMN id SET DEFAULT nextval('proc.diavgeia_attachment_id_seq'::regclass);


--
-- Name: diavgeia_decision_person id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_person ALTER COLUMN id SET DEFAULT nextval('proc.diavgeia_decision_person_id_seq'::regclass);


--
-- Name: diavgeia_ingest_window id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_ingest_window ALTER COLUMN id SET DEFAULT nextval('proc.diavgeia_ingest_window_id_seq'::regclass);


--
-- Name: economic_operator operator_id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.economic_operator ALTER COLUMN operator_id SET DEFAULT nextval('proc.economic_operator_operator_id_seq'::regclass);


--
-- Name: entity_group id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.entity_group ALTER COLUMN id SET DEFAULT nextval('proc.entity_group_id_seq'::regclass);


--
-- Name: extracted_table id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.extracted_table ALTER COLUMN id SET DEFAULT nextval('proc.extracted_table_id_seq'::regclass);


--
-- Name: ingest_act_log id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ingest_act_log ALTER COLUMN id SET DEFAULT nextval('proc.ingest_act_log_id_seq'::regclass);


--
-- Name: ingest_job id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ingest_job ALTER COLUMN id SET DEFAULT nextval('proc.ingest_job_id_seq'::regclass);


--
-- Name: ingest_window id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ingest_window ALTER COLUMN id SET DEFAULT nextval('proc.ingest_window_id_seq'::regclass);


--
-- Name: line_item_correction id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.line_item_correction ALTER COLUMN id SET DEFAULT nextval('proc.line_item_correction_id_seq'::regclass);


--
-- Name: search_profile id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.search_profile ALTER COLUMN id SET DEFAULT nextval('proc.search_profile_id_seq'::regclass);


--
-- Name: table_extract_job id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.table_extract_job ALTER COLUMN id SET DEFAULT nextval('proc.table_extract_job_id_seq'::regclass);


--
-- Name: table_extract_log id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.table_extract_log ALTER COLUMN id SET DEFAULT nextval('proc.table_extract_log_id_seq'::regclass);


--
-- Name: ted_ingest_window id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ted_ingest_window ALTER COLUMN id SET DEFAULT nextval('proc.ted_ingest_window_id_seq'::regclass);


--
-- Name: user_subscription id; Type: DEFAULT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.user_subscription ALTER COLUMN id SET DEFAULT nextval('proc.user_subscription_id_seq'::regclass);


--
-- Name: act_additional_contract_type act_additional_contract_type_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_additional_contract_type
    ADD CONSTRAINT act_additional_contract_type_pkey PRIMARY KEY (adam, contract_type_code);


--
-- Name: act_annotation act_annotation_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_annotation
    ADD CONSTRAINT act_annotation_pkey PRIMARY KEY (id);


--
-- Name: act_centralized_market act_centralized_market_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_centralized_market
    ADD CONSTRAINT act_centralized_market_pkey PRIMARY KEY (adam, market_code);


--
-- Name: act_cpv act_cpv_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_cpv
    ADD CONSTRAINT act_cpv_pkey PRIMARY KEY (adam, cpv_code);


--
-- Name: act_diavgeia_link act_diavgeia_link_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_diavgeia_link
    ADD CONSTRAINT act_diavgeia_link_pkey PRIMARY KEY (adam, ada, link_kind);


--
-- Name: act_funding act_funding_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_funding
    ADD CONSTRAINT act_funding_pkey PRIMARY KEY (id);


--
-- Name: act_group_member act_group_member_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_group_member
    ADD CONSTRAINT act_group_member_pkey PRIMARY KEY (adam);


--
-- Name: act_group act_group_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_group
    ADD CONSTRAINT act_group_pkey PRIMARY KEY (id);


--
-- Name: act_link act_link_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_link
    ADD CONSTRAINT act_link_pkey PRIMARY KEY (source_adam, target_adam, relation);


--
-- Name: act_nuts act_nuts_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_nuts
    ADD CONSTRAINT act_nuts_pkey PRIMARY KEY (adam, nuts_code);


--
-- Name: act_object_detail act_object_detail_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_object_detail
    ADD CONSTRAINT act_object_detail_pkey PRIMARY KEY (id);


--
-- Name: act_operator act_operator_adam_operator_id_role_key; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_operator
    ADD CONSTRAINT act_operator_adam_operator_id_role_key UNIQUE (adam, operator_id, role);


--
-- Name: act_operator act_operator_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_operator
    ADD CONSTRAINT act_operator_pkey PRIMARY KEY (id);


--
-- Name: act_systemic_number act_systemic_number_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_systemic_number
    ADD CONSTRAINT act_systemic_number_pkey PRIMARY KEY (adam, systemic_number);


--
-- Name: admin_action admin_action_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.admin_action
    ADD CONSTRAINT admin_action_pkey PRIMARY KEY (id);


--
-- Name: app_user app_user_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.app_user
    ADD CONSTRAINT app_user_pkey PRIMARY KEY (id);


--
-- Name: authority authority_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.authority
    ADD CONSTRAINT authority_pkey PRIMARY KEY (org_id);


--
-- Name: code_list code_list_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.code_list
    ADD CONSTRAINT code_list_pkey PRIMARY KEY (domain, code);


--
-- Name: cpv_category_map cpv_category_map_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.cpv_category_map
    ADD CONSTRAINT cpv_category_map_pkey PRIMARY KEY (cpv_code);


--
-- Name: cpv_code cpv_code_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.cpv_code
    ADD CONSTRAINT cpv_code_pkey PRIMARY KEY (cpv_code);


--
-- Name: customer_call customer_call_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_call
    ADD CONSTRAINT customer_call_pkey PRIMARY KEY (id);


--
-- Name: customer_note customer_note_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_note
    ADD CONSTRAINT customer_note_pkey PRIMARY KEY (id);


--
-- Name: customer_profile customer_profile_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_profile
    ADD CONSTRAINT customer_profile_pkey PRIMARY KEY (user_id);


--
-- Name: customer_task customer_task_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_task
    ADD CONSTRAINT customer_task_pkey PRIMARY KEY (id);


--
-- Name: diavgeia_attachment diavgeia_attachment_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_attachment
    ADD CONSTRAINT diavgeia_attachment_pkey PRIMARY KEY (id);


--
-- Name: diavgeia_decision_cpv diavgeia_decision_cpv_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_cpv
    ADD CONSTRAINT diavgeia_decision_cpv_pkey PRIMARY KEY (ada, cpv_code);


--
-- Name: diavgeia_decision_person diavgeia_decision_person_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_person
    ADD CONSTRAINT diavgeia_decision_person_pkey PRIMARY KEY (id);


--
-- Name: diavgeia_decision diavgeia_decision_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision
    ADD CONSTRAINT diavgeia_decision_pkey PRIMARY KEY (ada);


--
-- Name: diavgeia_decision_signer diavgeia_decision_signer_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_signer
    ADD CONSTRAINT diavgeia_decision_signer_pkey PRIMARY KEY (ada, signer_uid);


--
-- Name: diavgeia_decision_thematic diavgeia_decision_thematic_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_thematic
    ADD CONSTRAINT diavgeia_decision_thematic_pkey PRIMARY KEY (ada, thematic_uid);


--
-- Name: diavgeia_decision_unit diavgeia_decision_unit_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_unit
    ADD CONSTRAINT diavgeia_decision_unit_pkey PRIMARY KEY (ada, unit_uid);


--
-- Name: diavgeia_ingest_window diavgeia_ingest_window_decision_type_date_from_date_to_key; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_ingest_window
    ADD CONSTRAINT diavgeia_ingest_window_decision_type_date_from_date_to_key UNIQUE (decision_type, date_from, date_to);


--
-- Name: diavgeia_ingest_window diavgeia_ingest_window_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_ingest_window
    ADD CONSTRAINT diavgeia_ingest_window_pkey PRIMARY KEY (id);


--
-- Name: diavgeia_related diavgeia_related_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_related
    ADD CONSTRAINT diavgeia_related_pkey PRIMARY KEY (source_ada, target_ada, kind);


--
-- Name: diavgeia_signer diavgeia_signer_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_signer
    ADD CONSTRAINT diavgeia_signer_pkey PRIMARY KEY (uid);


--
-- Name: diavgeia_unit diavgeia_unit_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_unit
    ADD CONSTRAINT diavgeia_unit_pkey PRIMARY KEY (uid);


--
-- Name: economic_operator economic_operator_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.economic_operator
    ADD CONSTRAINT economic_operator_pkey PRIMARY KEY (operator_id);


--
-- Name: economic_operator economic_operator_vat_number_key; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.economic_operator
    ADD CONSTRAINT economic_operator_vat_number_key UNIQUE (vat_number);


--
-- Name: entity_group entity_group_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.entity_group
    ADD CONSTRAINT entity_group_pkey PRIMARY KEY (id);


--
-- Name: entity_member entity_member_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.entity_member
    ADD CONSTRAINT entity_member_pkey PRIMARY KEY (kind, member_key);


--
-- Name: extracted_table extracted_table_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.extracted_table
    ADD CONSTRAINT extracted_table_pkey PRIMARY KEY (id);


--
-- Name: gemi_enrichment gemi_enrichment_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.gemi_enrichment
    ADD CONSTRAINT gemi_enrichment_pkey PRIMARY KEY (afm);


--
-- Name: ingest_act_log ingest_act_log_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ingest_act_log
    ADD CONSTRAINT ingest_act_log_pkey PRIMARY KEY (id);


--
-- Name: ingest_job ingest_job_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ingest_job
    ADD CONSTRAINT ingest_job_pkey PRIMARY KEY (id);


--
-- Name: ingest_window ingest_window_act_type_date_from_date_to_key; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ingest_window
    ADD CONSTRAINT ingest_window_act_type_date_from_date_to_key UNIQUE (act_type, date_from, date_to);


--
-- Name: ingest_window ingest_window_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ingest_window
    ADD CONSTRAINT ingest_window_pkey PRIMARY KEY (id);


--
-- Name: line_item_correction line_item_correction_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.line_item_correction
    ADD CONSTRAINT line_item_correction_pkey PRIMARY KEY (id);


--
-- Name: login_throttle login_throttle_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.login_throttle
    ADD CONSTRAINT login_throttle_pkey PRIMARY KEY (key);


--
-- Name: match_rule match_rule_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.match_rule
    ADD CONSTRAINT match_rule_pkey PRIMARY KEY (code);


--
-- Name: match_setting match_setting_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.match_setting
    ADD CONSTRAINT match_setting_pkey PRIMARY KEY (key);


--
-- Name: nuts_code nuts_code_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.nuts_code
    ADD CONSTRAINT nuts_code_pkey PRIMARY KEY (nuts_code);


--
-- Name: object_detail_cpv object_detail_cpv_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.object_detail_cpv
    ADD CONSTRAINT object_detail_cpv_pkey PRIMARY KEY (object_detail_id, cpv_code);


--
-- Name: org_unit org_unit_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.org_unit
    ADD CONSTRAINT org_unit_pkey PRIMARY KEY (unit_id);


--
-- Name: postal_nuts postal_nuts_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.postal_nuts
    ADD CONSTRAINT postal_nuts_pkey PRIMARY KEY (postal_code);


--
-- Name: procurement_act procurement_act_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.procurement_act
    ADD CONSTRAINT procurement_act_pkey PRIMARY KEY (adam);


--
-- Name: product product_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.product
    ADD CONSTRAINT product_pkey PRIMARY KEY (code);


--
-- Name: schema_migration schema_migration_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.schema_migration
    ADD CONSTRAINT schema_migration_pkey PRIMARY KEY (filename);


--
-- Name: search_profile search_profile_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.search_profile
    ADD CONSTRAINT search_profile_pkey PRIMARY KEY (id);


--
-- Name: signer signer_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.signer
    ADD CONSTRAINT signer_pkey PRIMARY KEY (signer_id);


--
-- Name: table_extract_job table_extract_job_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.table_extract_job
    ADD CONSTRAINT table_extract_job_pkey PRIMARY KEY (id);


--
-- Name: table_extract_log table_extract_log_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.table_extract_log
    ADD CONSTRAINT table_extract_log_pkey PRIMARY KEY (id);


--
-- Name: table_extract_target table_extract_target_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.table_extract_target
    ADD CONSTRAINT table_extract_target_pkey PRIMARY KEY (job_id, adam);


--
-- Name: ted_ingest_window ted_ingest_window_country_date_from_date_to_key; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ted_ingest_window
    ADD CONSTRAINT ted_ingest_window_country_date_from_date_to_key UNIQUE (country, date_from, date_to);


--
-- Name: ted_ingest_window ted_ingest_window_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ted_ingest_window
    ADD CONSTRAINT ted_ingest_window_pkey PRIMARY KEY (id);


--
-- Name: ted_notice_cpv ted_notice_cpv_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ted_notice_cpv
    ADD CONSTRAINT ted_notice_cpv_pkey PRIMARY KEY (publication_number, cpv_code);


--
-- Name: ted_notice ted_notice_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ted_notice
    ADD CONSTRAINT ted_notice_pkey PRIMARY KEY (publication_number);


--
-- Name: tender_category tender_category_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.tender_category
    ADD CONSTRAINT tender_category_pkey PRIMARY KEY (id);


--
-- Name: tender_subcategory tender_subcategory_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.tender_subcategory
    ADD CONSTRAINT tender_subcategory_pkey PRIMARY KEY (id);


--
-- Name: unit_code unit_code_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.unit_code
    ADD CONSTRAINT unit_code_pkey PRIMARY KEY (code);


--
-- Name: user_subscription user_subscription_pkey; Type: CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.user_subscription
    ADD CONSTRAINT user_subscription_pkey PRIMARY KEY (id);


--
-- Name: code_list_domain_code_uidx; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX code_list_domain_code_uidx ON proc.code_list USING btree (domain, code);


--
-- Name: idx_cpv_cat_map_cat; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX idx_cpv_cat_map_cat ON proc.cpv_category_map USING btree (category_id);


--
-- Name: idx_cpv_cat_map_sub; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX idx_cpv_cat_map_sub ON proc.cpv_category_map USING btree (subcategory_id);


--
-- Name: idx_extracted_table_adam; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX idx_extracted_table_adam ON proc.extracted_table USING btree (adam, id);


--
-- Name: idx_extracted_table_pub; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX idx_extracted_table_pub ON proc.extracted_table USING btree (adam) WHERE is_published;


--
-- Name: idx_login_throttle_updated_at; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX idx_login_throttle_updated_at ON proc.login_throttle USING btree (updated_at);


--
-- Name: ix_act_authority; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_authority ON proc.procurement_act USING btree (authority_id);


--
-- Name: ix_act_authority_signed; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_authority_signed ON proc.procurement_act USING btree (authority_id, signed_date DESC);


--
-- Name: ix_act_authority_submission; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_authority_submission ON proc.procurement_act USING btree (authority_id, submission_date DESC NULLS LAST);


--
-- Name: ix_act_cancelled; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_cancelled ON proc.procurement_act USING btree (cancelled);


--
-- Name: ix_act_contract_type; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_contract_type ON proc.procurement_act USING btree (contract_type_code);


--
-- Name: ix_act_cpv_code; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_cpv_code ON proc.act_cpv USING btree (cpv_code);


--
-- Name: ix_act_data_source; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_data_source ON proc.procurement_act USING btree (data_source);


--
-- Name: ix_act_external_id; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_external_id ON proc.procurement_act USING btree (external_id);


--
-- Name: ix_act_final_submission; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_final_submission ON proc.procurement_act USING btree (final_submission_date);


--
-- Name: ix_act_full_text_gr; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_full_text_gr ON proc.procurement_act USING gin (to_tsvector('greek'::regconfig, COALESCE(full_text, ''::text)));


--
-- Name: ix_act_group_member_group; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_group_member_group ON proc.act_group_member USING btree (group_id);


--
-- Name: ix_act_operator_adam; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_operator_adam ON proc.act_operator USING btree (adam);


--
-- Name: ix_act_operator_op; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_operator_op ON proc.act_operator USING btree (operator_id);


--
-- Name: ix_act_operator_operator; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_operator_operator ON proc.act_operator USING btree (operator_id);


--
-- Name: ix_act_origin; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_origin ON proc.procurement_act USING btree (origin);


--
-- Name: ix_act_procedure_family; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_procedure_family ON proc.procurement_act USING btree (procedure_family);


--
-- Name: ix_act_raw_gin; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_raw_gin ON proc.procurement_act USING gin (raw_json);


--
-- Name: ix_act_search_tsv; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_search_tsv ON proc.procurement_act USING gin (search_tsv);


--
-- Name: ix_act_signed_date; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_signed_date ON proc.procurement_act USING btree (signed_date);


--
-- Name: ix_act_source_status; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_source_status ON proc.procurement_act USING btree (source_status);


--
-- Name: ix_act_submission_date; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_submission_date ON proc.procurement_act USING btree (submission_date DESC NULLS LAST);


--
-- Name: ix_act_title_trgm; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_title_trgm ON proc.procurement_act USING gin (proc.f_unaccent(lower(title)) proc.gin_trgm_ops);


--
-- Name: ix_act_type; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_type ON proc.procurement_act USING btree (type);


--
-- Name: ix_act_type_signed; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_type_signed ON proc.procurement_act USING btree (type, signed_date DESC);


--
-- Name: ix_act_type_submission; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_type_submission ON proc.procurement_act USING btree (type, submission_date DESC NULLS LAST);


--
-- Name: ix_act_value; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_act_value ON proc.procurement_act USING btree (total_cost_with_vat);


--
-- Name: ix_admin_action_at; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_admin_action_at ON proc.admin_action USING btree (at DESC);


--
-- Name: ix_admin_action_user; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_admin_action_user ON proc.admin_action USING btree (user_id, at DESC);


--
-- Name: ix_annotation_adam; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_annotation_adam ON proc.act_annotation USING btree (adam, created_at DESC);


--
-- Name: ix_annotation_flag; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_annotation_flag ON proc.act_annotation USING btree (flag) WHERE ((flag IS NOT NULL) AND (NOT superseded));


--
-- Name: ix_annotation_tags; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_annotation_tags ON proc.act_annotation USING gin (tags);


--
-- Name: ix_auth_name_trgm; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_auth_name_trgm ON proc.authority USING gin (translate(proc.f_unaccent(lower(name)), 'ς'::text, 'σ'::text) proc.gin_trgm_ops);


--
-- Name: ix_authority_name; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_authority_name ON proc.authority USING gin (to_tsvector('simple'::regconfig, name));


--
-- Name: ix_authority_vat; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_authority_vat ON proc.authority USING btree (vat_number);


--
-- Name: ix_cpv_code_prefix; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_cpv_code_prefix ON proc.cpv_code USING btree (cpv_code text_pattern_ops);


--
-- Name: ix_cpv_description_tsv; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_cpv_description_tsv ON proc.cpv_code USING gin (description_tsv);


--
-- Name: ix_cpv_division_root; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_cpv_division_root ON proc.cpv_code USING btree (substr((cpv_code)::text, 1, 2)) WHERE (substr((cpv_code)::text, 3, 6) = '000000'::text);


--
-- Name: ix_customer_call_user; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_customer_call_user ON proc.customer_call USING btree (user_id, COALESCE(scheduled_at, created_at) DESC);


--
-- Name: ix_customer_note_user; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_customer_note_user ON proc.customer_note USING btree (user_id, created_at DESC);


--
-- Name: ix_customer_task_user; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_customer_task_user ON proc.customer_task USING btree (user_id, created_at DESC);


--
-- Name: ix_diavgeia_attachment_ada; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_attachment_ada ON proc.diavgeia_attachment USING btree (ada);


--
-- Name: ix_diavgeia_authority; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_authority ON proc.diavgeia_decision USING btree (authority_id);


--
-- Name: ix_diavgeia_cpv_code; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_cpv_code ON proc.diavgeia_decision_cpv USING btree (cpv_code);


--
-- Name: ix_diavgeia_issue; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_issue ON proc.diavgeia_decision USING btree (issue_date);


--
-- Name: ix_diavgeia_org; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_org ON proc.diavgeia_decision USING btree (organization_uid);


--
-- Name: ix_diavgeia_person_ada; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_person_ada ON proc.diavgeia_decision_person USING btree (ada);


--
-- Name: ix_diavgeia_person_op; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_person_op ON proc.diavgeia_decision_person USING btree (operator_id);


--
-- Name: ix_diavgeia_related_src; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_related_src ON proc.diavgeia_related USING btree (source_ada);


--
-- Name: ix_diavgeia_related_tgt; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_related_tgt ON proc.diavgeia_related USING btree (target_ada);


--
-- Name: ix_diavgeia_type; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_diavgeia_type ON proc.diavgeia_decision USING btree (decision_type);


--
-- Name: ix_entity_member_group; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_entity_member_group ON proc.entity_member USING btree (group_id);


--
-- Name: ix_entity_member_key; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_entity_member_key ON proc.entity_member USING btree (kind, member_key);


--
-- Name: ix_eo_name_trgm; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_eo_name_trgm ON proc.economic_operator USING gin (translate(proc.f_unaccent(lower(name)), 'ς'::text, 'σ'::text) proc.gin_trgm_ops);


--
-- Name: ix_extracted_table_content_tsv; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_extracted_table_content_tsv ON proc.extracted_table USING gin (content_tsv) WHERE is_published;


--
-- Name: ix_funding_adam; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_funding_adam ON proc.act_funding USING btree (adam);


--
-- Name: ix_gemi_enrichment_activities; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_gemi_enrichment_activities ON proc.gemi_enrichment USING gin (activities_active);


--
-- Name: ix_gemi_enrichment_fetched; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_gemi_enrichment_fetched ON proc.gemi_enrichment USING btree (fetched_at);


--
-- Name: ix_gemi_enrichment_status; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_gemi_enrichment_status ON proc.gemi_enrichment USING btree (fetch_status);


--
-- Name: ix_ingest_act_log_adam; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_ingest_act_log_adam ON proc.ingest_act_log USING btree (adam);


--
-- Name: ix_ingest_act_log_job; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_ingest_act_log_job ON proc.ingest_act_log USING btree (job_id, id DESC);


--
-- Name: ix_ingest_job_queued; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_ingest_job_queued ON proc.ingest_job USING btree (id) WHERE (status = 'queued'::text);


--
-- Name: ix_ingest_job_started; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_ingest_job_started ON proc.ingest_job USING btree (started_at DESC);


--
-- Name: ix_ingest_job_status; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_ingest_job_status ON proc.ingest_job USING btree (status);


--
-- Name: ix_lic_adam_line; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_lic_adam_line ON proc.line_item_correction USING btree (adam, line_no) WHERE (NOT superseded);


--
-- Name: ix_link_rel; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_link_rel ON proc.act_link USING btree (relation);


--
-- Name: ix_link_source; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_link_source ON proc.act_link USING btree (source_adam);


--
-- Name: ix_link_target; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_link_target ON proc.act_link USING btree (target_adam);


--
-- Name: ix_mv_auth_value; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_mv_auth_value ON proc.mv_analytics_authorities USING btree (awarded_value DESC);


--
-- Name: ix_mv_authority_counts_acts; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_mv_authority_counts_acts ON proc.mv_authority_counts USING btree (n_acts DESC);


--
-- Name: ix_mv_authority_counts_org; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX ix_mv_authority_counts_org ON proc.mv_authority_counts USING btree (org_id);


--
-- Name: ix_mv_contractor_counts_acts; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_mv_contractor_counts_acts ON proc.mv_contractor_counts USING btree (n_acts DESC);


--
-- Name: ix_mv_contractor_counts_vat; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX ix_mv_contractor_counts_vat ON proc.mv_contractor_counts USING btree (vat_number);


--
-- Name: ix_mv_contractor_value; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_mv_contractor_value ON proc.mv_analytics_contractors USING btree (awarded_value DESC);


--
-- Name: ix_mv_cpv_cvalue; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_mv_cpv_cvalue ON proc.mv_analytics_cpv USING btree (contract_value DESC);


--
-- Name: ix_mv_explore_authority_value; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_mv_explore_authority_value ON proc.mv_explore_authority USING btree (value DESC);


--
-- Name: ix_mv_explore_contractor_value; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_mv_explore_contractor_value ON proc.mv_explore_contractor USING btree (value DESC);


--
-- Name: ix_obj_adam; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_obj_adam ON proc.act_object_detail USING btree (adam);


--
-- Name: ix_operator_name; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_operator_name ON proc.economic_operator USING gin (to_tsvector('simple'::regconfig, name));


--
-- Name: ix_pa_commitment_no_bt; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_pa_commitment_no_bt ON proc.procurement_act USING btree (btrim(commitment_no)) WHERE (commitment_no IS NOT NULL);


--
-- Name: ix_pa_contract_number_bt; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_pa_contract_number_bt ON proc.procurement_act USING btree (btrim(contract_number)) WHERE (contract_number IS NOT NULL);


--
-- Name: ix_pa_protocol_number_bt; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_pa_protocol_number_bt ON proc.procurement_act USING btree (btrim(protocol_number)) WHERE (protocol_number IS NOT NULL);


--
-- Name: ix_postal_nuts_nuts; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_postal_nuts_nuts ON proc.postal_nuts USING btree (nuts_code);


--
-- Name: ix_search_profile_owner; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_search_profile_owner ON proc.search_profile USING btree (owner_user_id) WHERE (owner_user_id IS NOT NULL);


--
-- Name: ix_search_profile_portal; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_search_profile_portal ON proc.search_profile USING btree (is_published) WHERE (scope = 'portal'::text);


--
-- Name: ix_table_extract_job_queued; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_table_extract_job_queued ON proc.table_extract_job USING btree (id) WHERE (status = 'queued'::text);


--
-- Name: ix_ted_notice_pubdate; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_ted_notice_pubdate ON proc.ted_notice USING btree (publication_date);


--
-- Name: ix_tel_job; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_tel_job ON proc.table_extract_log USING btree (job_id, id DESC);


--
-- Name: ix_tet_job; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_tet_job ON proc.table_extract_target USING btree (job_id, ord);


--
-- Name: ix_user_subscription_user_expires; Type: INDEX; Schema: proc; Owner: -
--

CREATE INDEX ix_user_subscription_user_expires ON proc.user_subscription USING btree (user_id, expires_at DESC);


--
-- Name: ux_app_user_email; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX ux_app_user_email ON proc.app_user USING btree (lower(email)) WHERE (email IS NOT NULL);


--
-- Name: ux_app_user_username; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX ux_app_user_username ON proc.app_user USING btree (lower(username));


--
-- Name: ux_mv_explore_authority; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX ux_mv_explore_authority ON proc.mv_explore_authority USING btree (auth_key, type);


--
-- Name: ux_mv_explore_authority_name; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX ux_mv_explore_authority_name ON proc.mv_explore_authority_name USING btree (auth_key);


--
-- Name: ux_mv_explore_contractor; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX ux_mv_explore_contractor ON proc.mv_explore_contractor USING btree (contr_key, type);


--
-- Name: ux_mv_explore_contractor_name; Type: INDEX; Schema: proc; Owner: -
--

CREATE UNIQUE INDEX ux_mv_explore_contractor_name ON proc.mv_explore_contractor_name USING btree (contr_key);


--
-- Name: act_additional_contract_type act_additional_contract_type_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_additional_contract_type
    ADD CONSTRAINT act_additional_contract_type_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_centralized_market act_centralized_market_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_centralized_market
    ADD CONSTRAINT act_centralized_market_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_cpv act_cpv_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_cpv
    ADD CONSTRAINT act_cpv_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_diavgeia_link act_diavgeia_link_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_diavgeia_link
    ADD CONSTRAINT act_diavgeia_link_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_funding act_funding_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_funding
    ADD CONSTRAINT act_funding_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_group_member act_group_member_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_group_member
    ADD CONSTRAINT act_group_member_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_group_member act_group_member_duplicate_of_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_group_member
    ADD CONSTRAINT act_group_member_duplicate_of_fkey FOREIGN KEY (duplicate_of) REFERENCES proc.procurement_act(adam) ON DELETE SET NULL;


--
-- Name: act_group_member act_group_member_group_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_group_member
    ADD CONSTRAINT act_group_member_group_id_fkey FOREIGN KEY (group_id) REFERENCES proc.act_group(id) ON DELETE CASCADE;


--
-- Name: act_nuts act_nuts_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_nuts
    ADD CONSTRAINT act_nuts_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_nuts act_nuts_nuts_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_nuts
    ADD CONSTRAINT act_nuts_nuts_code_fkey FOREIGN KEY (nuts_code) REFERENCES proc.nuts_code(nuts_code);


--
-- Name: act_object_detail act_object_detail_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_object_detail
    ADD CONSTRAINT act_object_detail_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_operator act_operator_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_operator
    ADD CONSTRAINT act_operator_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: act_operator act_operator_operator_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_operator
    ADD CONSTRAINT act_operator_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES proc.economic_operator(operator_id);


--
-- Name: act_systemic_number act_systemic_number_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.act_systemic_number
    ADD CONSTRAINT act_systemic_number_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: admin_action admin_action_user_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.admin_action
    ADD CONSTRAINT admin_action_user_id_fkey FOREIGN KEY (user_id) REFERENCES proc.app_user(id) ON DELETE SET NULL;


--
-- Name: authority authority_nuts_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.authority
    ADD CONSTRAINT authority_nuts_code_fkey FOREIGN KEY (nuts_code) REFERENCES proc.nuts_code(nuts_code);


--
-- Name: cpv_category_map cpv_category_map_category_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.cpv_category_map
    ADD CONSTRAINT cpv_category_map_category_id_fkey FOREIGN KEY (category_id) REFERENCES proc.tender_category(id);


--
-- Name: cpv_category_map cpv_category_map_cpv_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.cpv_category_map
    ADD CONSTRAINT cpv_category_map_cpv_code_fkey FOREIGN KEY (cpv_code) REFERENCES proc.cpv_code(cpv_code);


--
-- Name: cpv_category_map cpv_category_map_subcategory_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.cpv_category_map
    ADD CONSTRAINT cpv_category_map_subcategory_id_fkey FOREIGN KEY (subcategory_id) REFERENCES proc.tender_subcategory(id);


--
-- Name: customer_call customer_call_assigned_to_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_call
    ADD CONSTRAINT customer_call_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES proc.app_user(id) ON DELETE SET NULL;


--
-- Name: customer_call customer_call_created_by_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_call
    ADD CONSTRAINT customer_call_created_by_fkey FOREIGN KEY (created_by) REFERENCES proc.app_user(id) ON DELETE SET NULL;


--
-- Name: customer_call customer_call_user_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_call
    ADD CONSTRAINT customer_call_user_id_fkey FOREIGN KEY (user_id) REFERENCES proc.app_user(id) ON DELETE CASCADE;


--
-- Name: customer_note customer_note_author_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_note
    ADD CONSTRAINT customer_note_author_id_fkey FOREIGN KEY (author_id) REFERENCES proc.app_user(id) ON DELETE SET NULL;


--
-- Name: customer_note customer_note_user_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_note
    ADD CONSTRAINT customer_note_user_id_fkey FOREIGN KEY (user_id) REFERENCES proc.app_user(id) ON DELETE CASCADE;


--
-- Name: customer_profile customer_profile_updated_by_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_profile
    ADD CONSTRAINT customer_profile_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES proc.app_user(id);


--
-- Name: customer_profile customer_profile_user_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_profile
    ADD CONSTRAINT customer_profile_user_id_fkey FOREIGN KEY (user_id) REFERENCES proc.app_user(id) ON DELETE CASCADE;


--
-- Name: customer_task customer_task_assigned_to_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_task
    ADD CONSTRAINT customer_task_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES proc.app_user(id) ON DELETE SET NULL;


--
-- Name: customer_task customer_task_created_by_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_task
    ADD CONSTRAINT customer_task_created_by_fkey FOREIGN KEY (created_by) REFERENCES proc.app_user(id) ON DELETE SET NULL;


--
-- Name: customer_task customer_task_user_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.customer_task
    ADD CONSTRAINT customer_task_user_id_fkey FOREIGN KEY (user_id) REFERENCES proc.app_user(id) ON DELETE CASCADE;


--
-- Name: diavgeia_attachment diavgeia_attachment_ada_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_attachment
    ADD CONSTRAINT diavgeia_attachment_ada_fkey FOREIGN KEY (ada) REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE;


--
-- Name: diavgeia_decision diavgeia_decision_authority_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision
    ADD CONSTRAINT diavgeia_decision_authority_id_fkey FOREIGN KEY (authority_id) REFERENCES proc.authority(org_id);


--
-- Name: diavgeia_decision_cpv diavgeia_decision_cpv_ada_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_cpv
    ADD CONSTRAINT diavgeia_decision_cpv_ada_fkey FOREIGN KEY (ada) REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE;


--
-- Name: diavgeia_decision_cpv diavgeia_decision_cpv_cpv_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_cpv
    ADD CONSTRAINT diavgeia_decision_cpv_cpv_code_fkey FOREIGN KEY (cpv_code) REFERENCES proc.cpv_code(cpv_code);


--
-- Name: diavgeia_decision_person diavgeia_decision_person_ada_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_person
    ADD CONSTRAINT diavgeia_decision_person_ada_fkey FOREIGN KEY (ada) REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE;


--
-- Name: diavgeia_decision_person diavgeia_decision_person_operator_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_person
    ADD CONSTRAINT diavgeia_decision_person_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES proc.economic_operator(operator_id);


--
-- Name: diavgeia_decision_signer diavgeia_decision_signer_ada_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_signer
    ADD CONSTRAINT diavgeia_decision_signer_ada_fkey FOREIGN KEY (ada) REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE;


--
-- Name: diavgeia_decision_thematic diavgeia_decision_thematic_ada_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_thematic
    ADD CONSTRAINT diavgeia_decision_thematic_ada_fkey FOREIGN KEY (ada) REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE;


--
-- Name: diavgeia_decision_unit diavgeia_decision_unit_ada_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.diavgeia_decision_unit
    ADD CONSTRAINT diavgeia_decision_unit_ada_fkey FOREIGN KEY (ada) REFERENCES proc.diavgeia_decision(ada) ON DELETE CASCADE;


--
-- Name: entity_member entity_member_group_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.entity_member
    ADD CONSTRAINT entity_member_group_id_fkey FOREIGN KEY (group_id) REFERENCES proc.entity_group(id) ON DELETE CASCADE;


--
-- Name: ingest_act_log ingest_act_log_job_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ingest_act_log
    ADD CONSTRAINT ingest_act_log_job_id_fkey FOREIGN KEY (job_id) REFERENCES proc.ingest_job(id) ON DELETE CASCADE;


--
-- Name: line_item_correction line_item_correction_adam_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.line_item_correction
    ADD CONSTRAINT line_item_correction_adam_fkey FOREIGN KEY (adam) REFERENCES proc.procurement_act(adam) ON DELETE CASCADE;


--
-- Name: nuts_code nuts_code_parent_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.nuts_code
    ADD CONSTRAINT nuts_code_parent_code_fkey FOREIGN KEY (parent_code) REFERENCES proc.nuts_code(nuts_code);


--
-- Name: object_detail_cpv object_detail_cpv_cpv_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.object_detail_cpv
    ADD CONSTRAINT object_detail_cpv_cpv_code_fkey FOREIGN KEY (cpv_code) REFERENCES proc.cpv_code(cpv_code);


--
-- Name: object_detail_cpv object_detail_cpv_object_detail_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.object_detail_cpv
    ADD CONSTRAINT object_detail_cpv_object_detail_id_fkey FOREIGN KEY (object_detail_id) REFERENCES proc.act_object_detail(id) ON DELETE CASCADE;


--
-- Name: org_unit org_unit_authority_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.org_unit
    ADD CONSTRAINT org_unit_authority_id_fkey FOREIGN KEY (authority_id) REFERENCES proc.authority(org_id);


--
-- Name: postal_nuts postal_nuts_nuts_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.postal_nuts
    ADD CONSTRAINT postal_nuts_nuts_code_fkey FOREIGN KEY (nuts_code) REFERENCES proc.nuts_code(nuts_code);


--
-- Name: procurement_act procurement_act_authority_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.procurement_act
    ADD CONSTRAINT procurement_act_authority_id_fkey FOREIGN KEY (authority_id) REFERENCES proc.authority(org_id);


--
-- Name: procurement_act procurement_act_awarded_operator_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.procurement_act
    ADD CONSTRAINT procurement_act_awarded_operator_id_fkey FOREIGN KEY (awarded_operator_id) REFERENCES proc.economic_operator(operator_id);


--
-- Name: procurement_act procurement_act_nuts_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.procurement_act
    ADD CONSTRAINT procurement_act_nuts_code_fkey FOREIGN KEY (nuts_code) REFERENCES proc.nuts_code(nuts_code);


--
-- Name: procurement_act procurement_act_org_unit_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.procurement_act
    ADD CONSTRAINT procurement_act_org_unit_id_fkey FOREIGN KEY (org_unit_id) REFERENCES proc.org_unit(unit_id);


--
-- Name: procurement_act procurement_act_signer_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.procurement_act
    ADD CONSTRAINT procurement_act_signer_id_fkey FOREIGN KEY (signer_id) REFERENCES proc.signer(signer_id);


--
-- Name: search_profile search_profile_based_on_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.search_profile
    ADD CONSTRAINT search_profile_based_on_id_fkey FOREIGN KEY (based_on_id) REFERENCES proc.search_profile(id) ON DELETE SET NULL;


--
-- Name: search_profile search_profile_created_by_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.search_profile
    ADD CONSTRAINT search_profile_created_by_fkey FOREIGN KEY (created_by) REFERENCES proc.app_user(id) ON DELETE SET NULL;


--
-- Name: search_profile search_profile_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.search_profile
    ADD CONSTRAINT search_profile_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES proc.app_user(id) ON DELETE CASCADE;


--
-- Name: signer signer_authority_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.signer
    ADD CONSTRAINT signer_authority_id_fkey FOREIGN KEY (authority_id) REFERENCES proc.authority(org_id);


--
-- Name: table_extract_log table_extract_log_job_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.table_extract_log
    ADD CONSTRAINT table_extract_log_job_id_fkey FOREIGN KEY (job_id) REFERENCES proc.table_extract_job(id) ON DELETE CASCADE;


--
-- Name: table_extract_target table_extract_target_job_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.table_extract_target
    ADD CONSTRAINT table_extract_target_job_id_fkey FOREIGN KEY (job_id) REFERENCES proc.table_extract_job(id) ON DELETE CASCADE;


--
-- Name: ted_notice_cpv ted_notice_cpv_publication_number_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.ted_notice_cpv
    ADD CONSTRAINT ted_notice_cpv_publication_number_fkey FOREIGN KEY (publication_number) REFERENCES proc.ted_notice(publication_number) ON DELETE CASCADE;


--
-- Name: tender_subcategory tender_subcategory_parent_category_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.tender_subcategory
    ADD CONSTRAINT tender_subcategory_parent_category_id_fkey FOREIGN KEY (parent_category_id) REFERENCES proc.tender_category(id);


--
-- Name: user_subscription user_subscription_granted_by_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.user_subscription
    ADD CONSTRAINT user_subscription_granted_by_fkey FOREIGN KEY (granted_by) REFERENCES proc.app_user(id);


--
-- Name: user_subscription user_subscription_product_code_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.user_subscription
    ADD CONSTRAINT user_subscription_product_code_fkey FOREIGN KEY (product_code) REFERENCES proc.product(code);


--
-- Name: user_subscription user_subscription_user_id_fkey; Type: FK CONSTRAINT; Schema: proc; Owner: -
--

ALTER TABLE ONLY proc.user_subscription
    ADD CONSTRAINT user_subscription_user_id_fkey FOREIGN KEY (user_id) REFERENCES proc.app_user(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict ui3WVvTuYFkVuAyhDo8UOcl6P6lhwaAHakQq2pxP2ferrzFVEA5rV9z5FfyzMcp

