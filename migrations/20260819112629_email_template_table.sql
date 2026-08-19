-- migrations/20260819112629_email_template_table.sql
-- email template table
--
-- Backing store for the CRM email builder (app/email_builder.py): an admin
-- pastes translated text on a customer's CRM page, it is merged into one of
-- these template bodies, and the merged HTML + a plain-text alternative come
-- back for copy/download. Nothing here is ever sent — this repo has no mailer.
--
-- Ported from the standalone Multilingual-HTML-Template-Builder, which kept its
-- single template as a source constant because Vercel bundles each function and
-- repo files are not guaranteed present in the artifact. That constraint does
-- not apply here (the Docker image ships the repo), and we need one body per
-- language, so the templates live in the database and are editable from /admin
-- instead of requiring a deploy to change a word.
--
-- body_html is an HTML *fragment*, not a document — the merge parses it in
-- fragment mode, so a stored body must not be wrapped in <html>/<body>.
--
-- Two markup conventions travel with the body and are enforced by the merge,
-- not by this schema:
--   @@token     — protects a paragraph (salutation, sign-off): never filled,
--                 never removed, consumes no pasted block.
--   [[field]]   — a merge field resolved from the customer's profile
--                 (full_name, company, vat_number, email, ...). Deliberately
--                 not {{ }}, which would collide with Jinja2.
--
-- Privileges need no statements here: the least-privilege grants migration
-- (20260709201920) set ALTER DEFAULT PRIVILEGES IN SCHEMA proc for the owner
-- role, so app_runtime picks up DML on this table automatically.
--
-- Wrap the body so a failure leaves nothing half-applied. Idempotent throughout.

BEGIN;

CREATE TABLE IF NOT EXISTS proc.email_template (
    id         bigserial   PRIMARY KEY,
    slug       text        NOT NULL,       -- stable id across languages ('outreach')
    lang       text        NOT NULL,       -- 'el' | 'en' — matches app/i18n.py
    name       text        NOT NULL,       -- label shown in the CRM picker
    subject    text,                       -- optional; copied alongside the body
    body_html  text        NOT NULL,       -- HTML fragment, see note above
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by bigint      REFERENCES proc.app_user(id) ON DELETE SET NULL,
    CONSTRAINT email_template_lang_ck CHECK (lang IN ('el', 'en'))
);

-- One body per (template, language); also the lookup the CRM panel does.
CREATE UNIQUE INDEX IF NOT EXISTS ux_email_template_slug_lang
    ON proc.email_template (slug, lang);

COMMIT;
