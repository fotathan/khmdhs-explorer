-- VERIFY_prefix_search.sql
-- Sanity checks for the trailing-* prefix search, run against your DB after
-- deploy. These show HOW the Greek config stems a prefix term, so you can
-- confirm the breadth of matching is what you want. Read-only; safe to run.

-- 1. See what the prefix term actually becomes. Compare the lexeme the stemmer
--    produces for a full word vs. the prefix you'd type. If they share a root,
--    prefix mode will match the word.
SELECT to_tsquery('greek', 'ηλεκτρολογικ:*')      AS prefix_query,
       to_tsvector('greek', 'ηλεκτρολογικά')      AS full_word_vector,
       to_tsvector('greek', 'ηλεκτρολογικών')     AS inflected_vector,
       to_tsvector('greek', 'ηλεκτρολόγος')       AS different_word_vector;

-- 2. Does the prefix query match each of those? (t = matches, f = no)
SELECT
  to_tsvector('greek','ηλεκτρολογικά')  @@ to_tsquery('greek','ηλεκτρολογικ:*') AS matches_inflection_a,
  to_tsvector('greek','ηλεκτρολογικών') @@ to_tsquery('greek','ηλεκτρολογικ:*') AS matches_inflection_b,
  to_tsvector('greek','ηλεκτρολόγος')   @@ to_tsquery('greek','ηλεκτρολογικ:*') AS matches_different_word;

-- 3. Real-data check: how many published tables match a prefix vs the full word?
--    (swap in a stem you know exists in your tables)
SELECT
  count(*) FILTER (WHERE content_tsv @@ to_tsquery('greek','ηλεκτρολογικ:*'))      AS prefix_hits,
  count(*) FILTER (WHERE content_tsv @@ websearch_to_tsquery('greek','ηλεκτρολογικά')) AS word_hits
FROM proc.extracted_table
WHERE is_published;
