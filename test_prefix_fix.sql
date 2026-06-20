-- A) What does stemming the user's typed prefix produce?
SELECT (to_tsvector('greek','ηλεκτρολογικ'))::text AS stemmed_input_vec;

-- B) If we prefix-match on the STEMMED form 'ηλεκτρολογ:*', do the words match?
SELECT
  to_tsvector('greek','ηλεκτρολογικά')  @@ to_tsquery('greek','ηλεκτρολογ:*') AS m_a,
  to_tsvector('greek','ηλεκτρολογικών') @@ to_tsquery('greek','ηλεκτρολογ:*') AS m_b,
  to_tsvector('greek','ηλεκτρολόγος')   @@ to_tsquery('greek','ηλεκτρολογ:*') AS m_diff;

-- C) The extraction trick: derive the stemmed lexeme from raw input automatically.
SELECT
  split_part(strip(to_tsvector('greek','ηλεκτρολογικ'))::text, '''', 2) AS lexeme;