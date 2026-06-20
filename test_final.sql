-- Show the constructed query text:
WITH q AS (SELECT 'ηλεκτρολογικ'::text AS p)
SELECT p AS input,
  p || ':* | ' || coalesce(
        nullif(split_part(strip(to_tsvector('greek', p))::text, '''', 2), ''),
        p) || ':*' AS built_query_text
FROM q;

-- Match test (a,b,diff should be t; unrelated should be f):
WITH q AS (SELECT 'ηλεκτρολογικ'::text AS p)
SELECT
  to_tsvector('greek','ηλεκτρολογικά')  @@ to_tsquery('greek', (SELECT p||':* | '||coalesce(nullif(split_part(strip(to_tsvector('greek',p))::text,'''',2),''),p)||':*' FROM q)) AS a,
  to_tsvector('greek','ηλεκτρολογικών') @@ to_tsquery('greek', (SELECT p||':* | '||coalesce(nullif(split_part(strip(to_tsvector('greek',p))::text,'''',2),''),p)||':*' FROM q)) AS b,
  to_tsvector('greek','ηλεκτρολόγος')   @@ to_tsquery('greek', (SELECT p||':* | '||coalesce(nullif(split_part(strip(to_tsvector('greek',p))::text,'''',2),''),p)||':*' FROM q)) AS diff,
  to_tsvector('greek','ηλεκτρονικός')   @@ to_tsquery('greek', (SELECT p||':* | '||coalesce(nullif(split_part(strip(to_tsvector('greek',p))::text,'''',2),''),p)||':*' FROM q)) AS unrelated;

-- Non-stemming word still works:
WITH q AS (SELECT 'καθαριότητ'::text AS p)
SELECT
  to_tsvector('greek','καθαριότητα') @@ to_tsquery('greek', (SELECT p||':* | '||coalesce(nullif(split_part(strip(to_tsvector('greek',p))::text,'''',2),''),p)||':*' FROM q)) AS clean_match;