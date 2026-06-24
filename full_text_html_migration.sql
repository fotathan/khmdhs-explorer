-- full_text_html_migration.sql
-- ===========================================================================
-- Adds a SECOND full-text column holding a rich-text (HTML) version of an act's
-- full text, edited via the curator's WYSIWYG editor on /tables/fulltext.
--
-- WHY A SEPARATE COLUMN (rather than putting HTML into full_text):
--   * full_text stays the PLAIN-TEXT search/snippet source. search_tsv and
--     full_text_tsv are generated from full_text, and /search snippets use
--     ts_headline('greek', full_text, ...). Indexing HTML markup there would
--     pollute the tsvector and produce snippets with tags mid-highlight.
--   * The OCR/extraction importer keeps writing plain text to full_text,
--     untouched. full_text_html is curator-authored, opt-in, and nullable.
--
-- So the contract is: full_text = canonical plain text (search source);
-- full_text_html = optional pretty rendering shown on the detail page when set,
-- always kept in sync with full_text on save. Both are written together by the
-- /tables/fulltext save handler, which stores Quill's plain text in full_text
-- and the sanitised (nh3) HTML in full_text_html.
--
-- Purely additive and safe: one nullable column, no existing data touched.
--
-- RUN ON BOTH local AND Supabase:
--   psql "$DATABASE_URL"        -f full_text_html_migration.sql
--   psql "<supabase-direct-url>" -f full_text_html_migration.sql
-- ===========================================================================

ALTER TABLE proc.procurement_act
    ADD COLUMN IF NOT EXISTS full_text_html text;

-- No index: full_text_html is never searched or filtered (search stays on the
-- plain-text full_text / search_tsv). It is read only on the single-act detail
-- page, already keyed by adam (PK).
