-- garbled_text_audit.sql — READ-ONLY audit of font-mapping corruption in
-- proc.procurement_act.full_text.
--
-- Why this exists: khmdhs_ingest.looks_garbled() only catches (cid:N) tokens,
-- U+FFFD, near-zero-Greek and control-char noise. A PDF with a WRONG (rather
-- than missing) ToUnicode CMap emits perfectly valid Greek letters that spell
-- nothing — it passes every one of those checks. This audit finds that class.
--
-- Run:  psql -h 127.0.0.1 -p 5433 -U postgres -d procurement -f garbled_text_audit.sql
--
-- Three independent detectors; an act is CONFIRMED if any one fires.
--   1. sig_reserved  — U+03A2 is a RESERVED, unassigned Greek codepoint. No
--                      legitimate Greek text can contain it. Zero false
--                      positives by construction. Every broken-font family
--                      observed so far maps capital Σ onto it.
--   2. sig_swap      — glyph-swap families that still "look" Greek: final
--                      sigma ς standing in non-final position, and/or θ
--                      outnumbering η (real Greek runs ~0.27 θ per η).
--   3. sig_scramble  — full scramble ("Στην" -> "Σηελ"): common Greek function
--                      words vanish. Gated on >=400 lowercase Greek chars so
--                      ALL-CAPS form documents (clean, but function-word-free)
--                      are not falsely accused.
--
-- SCOPE: this audit counts the WRONG-cmap family only. Acts whose text is
-- garbled the older way — (cid:N) tokens, U+FFFD, ISO-8859-7-read-as-Latin-1
-- mojibake — are NOT counted here; looks_garbled() checks 1-4 already cover
-- those. So treat the numbers below as a floor on total garbling, and build any
-- re-extraction worklist from looks_garbled() itself rather than from this
-- query alone.

\set ECHO none

CREATE TEMP TABLE _ga AS
WITH s AS (
  SELECT adam, type, data_source, full_text_source, ingested_at,
         left(full_text, 20000) AS t          -- cap work, mirrors looks_garbled
  FROM proc.procurement_act
  WHERE full_text IS NOT NULL AND length(full_text) > 200
)
SELECT adam, type, data_source, full_text_source, ingested_at, length(t) AS len,
  regexp_count(t, 'ς(?=[α-ωίϊΐάέήόύώϋΰ])')::numeric      AS nonfinal_sigma,
  regexp_count(t, '[σς]')::numeric                        AS sigma_total,
  regexp_count(t, U&'\03A2')::numeric                     AS reserved_u3a2,
  regexp_count(t, '[ηΗ]')::numeric                        AS eta,
  regexp_count(t, '[θΘ]')::numeric                        AS theta,
  regexp_count(t, '[α-ωάέήίόύώϊϋΐΰς]')::numeric           AS lower_greek,
  regexp_count(t, '\m(και|του|της|την|των|για|στην|στο|στη|είναι|από|προς|με|το|τα|οι|ως|σε|επί|που|αυτ)')::numeric AS func_words
FROM s;

CREATE TEMP VIEW garbled AS
SELECT *,
  CASE WHEN lower_greek > 0 THEN func_words / lower_greek * 1000 END AS fw_per1k,
  (reserved_u3a2 >= 3)                                          AS sig_reserved,
  ( (sigma_total >= 20 AND nonfinal_sigma / NULLIF(sigma_total,0) >= 0.25)
    OR (eta >= 20 AND theta / NULLIF(eta,0) >= 1.0) )            AS sig_swap,
  (lower_greek >= 400 AND func_words / NULLIF(lower_greek,0) * 1000 < 12) AS sig_scramble,
  (lower_greek >= 400 AND func_words / NULLIF(lower_greek,0) * 1000 < 20) AS sig_scramble_wide
FROM _ga;

CREATE TEMP VIEW garbled_verdict AS
SELECT *,
  (sig_reserved OR sig_swap OR sig_scramble)      AS confirmed,
  (sig_reserved OR sig_swap OR sig_scramble_wide) AS suspected
FROM garbled;

\set ECHO none
\echo ''
\echo '======== 1. headline ========'
SELECT count(*) AS acts_with_text,
       count(*) FILTER (WHERE confirmed) AS confirmed,
       round(100.0 * count(*) FILTER (WHERE confirmed) / count(*), 2) AS pct_confirmed,
       count(*) FILTER (WHERE suspected) AS suspected,
       round(100.0 * count(*) FILTER (WHERE suspected) / count(*), 2) AS pct_suspected
FROM garbled_verdict;

\echo '======== 2. detector overlap (agreement = precision) ========'
SELECT sig_reserved, sig_swap, sig_scramble, count(*)
FROM garbled_verdict WHERE confirmed GROUP BY 1,2,3 ORDER BY 4 DESC;

\echo '======== 3. by extraction path ========'
SELECT CASE WHEN full_text_source LIKE 'manual:%' THEN 'manual:*' ELSE full_text_source END AS src,
       count(*) AS n, count(*) FILTER (WHERE confirmed) AS bad,
       round(100.0 * count(*) FILTER (WHERE confirmed) / count(*), 1) AS pct
FROM garbled_verdict GROUP BY 1 ORDER BY 2 DESC;

\echo '======== 4. by data_source ========'
SELECT data_source, count(*) AS n, count(*) FILTER (WHERE confirmed) AS bad,
       round(100.0 * count(*) FILTER (WHERE confirmed) / count(*), 1) AS pct
FROM garbled_verdict GROUP BY 1 ORDER BY 2 DESC;

\echo '======== 5. by act type ========'
SELECT type, count(*) AS n, count(*) FILTER (WHERE confirmed) AS bad,
       round(100.0 * count(*) FILTER (WHERE confirmed) / count(*), 1) AS pct
FROM garbled_verdict GROUP BY 1 ORDER BY 2 DESC;

\echo '======== 6. by ingest month ========'
SELECT date_trunc('month', ingested_at)::date AS month, count(*) AS n,
       count(*) FILTER (WHERE confirmed) AS bad,
       round(100.0 * count(*) FILTER (WHERE confirmed) / count(*), 1) AS pct
FROM garbled_verdict GROUP BY 1 ORDER BY 1;

\echo '======== 7. worklist head (feeds any re-extraction backfill) ========'
SELECT adam, type, len, reserved_u3a2, round(fw_per1k,1) AS fw_per1k,
       sig_reserved, sig_swap, sig_scramble
FROM garbled_verdict WHERE confirmed ORDER BY len DESC LIMIT 20;
