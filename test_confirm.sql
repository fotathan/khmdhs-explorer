SELECT translate(proc.f_unaccent(lower('Ηλεκτρολογικά έργα 2024')), 'ς', 'σ')
       LIKE translate(proc.f_unaccent(lower('%ηλεκτρολογικ%')), 'ς', 'σ') AS ft_match;

SELECT translate(proc.f_unaccent(lower('[["Ηλεκτρολογικά υλικά","100"]]'::text)), 'ς', 'σ')
       LIKE translate(proc.f_unaccent(lower('%ηλεκτρολογικ%')), 'ς', 'σ') AS tbl_match;

SELECT translate(proc.f_unaccent(lower('Ηλεκτρονικά συστήματα')), 'ς', 'σ')
       LIKE translate(proc.f_unaccent(lower('%ηλεκτρολογικ%')), 'ς', 'σ') AS unrelated_f;

SELECT
  translate(proc.f_unaccent(lower('καθαριότητα και ηλεκτρολογικά')), 'ς', 'σ') LIKE translate(proc.f_unaccent(lower('%καθαρ%')), 'ς', 'σ')
  AND
  translate(proc.f_unaccent(lower('καθαριότητα και ηλεκτρολογικά')), 'ς', 'σ') LIKE translate(proc.f_unaccent(lower('%ηλεκτρ%')), 'ς', 'σ')
  AS two_word_and;