-- Two prefix terms ORed: the raw typed prefix and its stemmed form.
SELECT
  to_tsvector('greek','ηλεκτρολογικά')  @@ to_tsquery('greek','ηλεκτρολογικ:*') AS raw_only_a,
  to_tsvector('greek','ηλεκτρολογικά')  @@ to_tsquery('greek','ηλεκτρολογ:*')   AS stem_only_a;

SELECT
  to_tsvector('greek','ηλεκτρολογικός') @@ to_tsquery('greek','ηλεκτρολογ:*') AS fullword_matches;

-- KEY: does 'rawprefix:* | stemmedprefix:*' match all the right words
-- WITHOUT matching the unrelated 'ηλεκτρονικός'?
SELECT
  to_tsvector('greek','ηλεκτρολογικά')  @@ to_tsquery('greek','ηλεκτρολογικ:* | ηλεκτρολογ:*') AS combo_a,
  to_tsvector('greek','ηλεκτρολογικών') @@ to_tsquery('greek','ηλεκτρολογικ:* | ηλεκτρολογ:*') AS combo_b,
  to_tsvector('greek','ηλεκτρολόγος')   @@ to_tsquery('greek','ηλεκτρολογικ:* | ηλεκτρολογ:*') AS combo_diff,
  to_tsvector('greek','ηλεκτρονικός')   @@ to_tsquery('greek','ηλεκτρολογικ:* | ηλεκτρολογ:*') AS combo_unrelated;