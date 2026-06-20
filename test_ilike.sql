SELECT translate(proc.f_unaccent(lower('ηλεκτρολογικ')), 'ς', 'σ') AS norm_prefix,
       translate(proc.f_unaccent(lower('Ηλεκτρολογικά')), 'ς', 'σ') AS norm_word;

SELECT translate(proc.f_unaccent(lower('Ηλεκτρολογικά έργα')), 'ς', 'σ')
       LIKE '%' || translate(proc.f_unaccent(lower('ηλεκτρολογικ')), 'ς', 'σ') || '%' AS matches;

SELECT translate(proc.f_unaccent(lower('[["Ηλεκτρολογικά υλικά","100"]]')), 'ς', 'σ')
       LIKE '%' || translate(proc.f_unaccent(lower('ηλεκτρολογικ')), 'ς', 'σ') || '%' AS json_matches;

SELECT translate(proc.f_unaccent(lower('Ηλεκτρονικά')), 'ς', 'σ')
       LIKE '%' || translate(proc.f_unaccent(lower('ηλεκτρολογικ')), 'ς', 'σ') || '%' AS unrelated_should_be_f;