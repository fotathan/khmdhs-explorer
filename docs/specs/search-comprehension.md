# Spec: Search Comprehension — Match Explanation, Occurrence Navigator

**Status:** Implemented, 2026-08-28 — Features B and C and the shared normaliser are built, tested and verified against live data. This document has been reconciled with the build: where the implementation diverged from the original design, it says so and why.
**Repo path:** `docs/specs/search-comprehension.md`
**Author:** drafted from a UX prototype review (Bolt prototype "Tender Results & Details UX"), 2026-08-28
**Scope:** two coupled features shipped as one piece of work. Attachment-level match sections, chain-position labelling and the feedback widget are explicitly out of scope here and noted in §8.

> **Scope change, 2026-08-28.** Feature A (AI-rewritten titles with a toggle to
> the original) was cut. Sections have been renumbered; Features B and C keep
> their original letters so the code comments, tests and commit history that
> reference them stay accurate.

---

## 1. Problem

A search over ~2.7M acts returns rows whose official Greek titles are long, abbreviation-heavy and frequently uninformative at a glance, and gives the user no evidence for *why* a given act came back. Once an act is opened, a matched term may sit anywhere in a multi-page OCR'd full text with no way to reach it. Two failures, one underlying cause: the result set is not self-explaining.

**Goal.** A user scanning results can (a) see which of their terms matched and how often, and (b) reach every occurrence in one click — without ever losing access to the verbatim official text.

**Non-goal.** Changing ranking, relevance or the search query language. This spec only changes what is *shown* about a match that the existing `search_tsv` query already produced.

---

## 2. Where this lives in the repo

The draft was written without repo access and used placeholders. Resolved:

| Placeholder | Real name | Note |
|---|---|---|
| `acts` | `proc.procurement_act` | ~2.88M rows |
| `acts.title` | `procurement_act.title` | |
| `acts.short_description` | **does not exist** | there is no act-level summary column; `short_description` exists only per line item on `act_object_detail`, and is not indexed into `search_tsv` |
| `acts.full_text` | `procurement_act.full_text` | one column, plus `full_text_html` for curator-authored rich text |
| `acts.search_tsv` | `procurement_act.search_tsv` | **yes, weighted** — `setweight(title,'A') \|\| setweight(full_text,'B')`, config `greek`, GIN index `ix_act_search_tsv` |
| `list_acts()` | `run_search()` in `app/main.py` | |
| `cpv_codes` | `object_detail_cpv` (prefix-filtered), labels from `proc.cpv_code` | |

Because there is no act-level summary column, the section vocabulary is **two** sections, not four: `Τίτλος` (weight A) and `Πλήρες κείμενο` (weight B). §3.1's speculative `C = πλήρες κείμενο`, `D = συνημμένα` mapping does not apply.

New files:

| File | Holds |
|---|---|
| `app/textmatch.py` | §5 — folding, tokenisation with offsets, `split_full_text`, `mark`, `snippet` |
| `app/search_match.py` | §3 and §4 — chips, the scan, `occurrences_for`, `list_chips` |
| `app/templates/_match_chips.html` | chip macros, both forms |
| `app/templates/_occurrences.html` | popover body |
| `app/static/js/occurrences.js` | §4.3 — open, scroll, flash, keyboard |
| `tests/test_textmatch.py` | 16 tests, no database |
| `tests/test_search_match.py` | 16 tests, database-backed |

No migration. Nothing in this scope changes the schema.

---

## 3. Feature B — "Γιατί ταιριάζει" match explanation panel

A panel above the act details listing one chip per matched term: the term, a hit count, and a colour by match class (green keyword, orange CPV). Compact row of chips, no headers, no prose.

### 3.1 Where the counts come from

`search_tsv` is weighted, so the tsvector holds lexeme, positions and a weight per position, and one cheap query reads them without re-scanning any text:

```sql
SELECT a.adam, l.lexeme, l.positions, l.weights
FROM   proc.procurement_act a, LATERAL unnest(a.search_tsv) AS l
WHERE  a.adam = ANY(%(adams)s)
  AND  l.lexeme = ANY(%(query_lexemes)s);
```

Query lexemes come from the **same** configuration `search_tsv` is generated with — reused verbatim, never a second one:

```sql
SELECT w.ord, l.lexeme
FROM   unnest(%(words)s::text[]) WITH ORDINALITY AS w(word, ord)
LEFT JOIN LATERAL unnest(to_tsvector('greek', w.word)) AS l ON true;
```

**As built: the two surfaces use different paths, on purpose.**

*Detail page* — chips, the occurrence list and every `<mark>` come from **one scan of that act's text**, not from the tsvector. §3.4's first criterion (the count on a chip *is* the number of highlights on the page) can only hold by construction if both are produced by the same pass; deriving the count from the index and the marks from a separate scan invites exactly the drift that criterion forbids. The scan splits the two concerns:

- **offsets** are ours — a Greek/Latin/digit word regex over the text, because Postgres' text-search parser reports tokens without character offsets and can emit overlapping tokens for hyphenated words and URLs;
- **whether a word matches** is Postgres' — the distinct surface forms go down in one array and the matching ones come back, stemmed by the same `greek` configuration. The app never develops a second opinion about what the index did.

```sql
SELECT t.tok, l.lexeme
FROM   unnest(%(surface_forms)s::text[]) AS t(tok)
JOIN   LATERAL unnest(to_tsvector('greek', t.tok)) AS l ON true
WHERE  l.lexeme = ANY(%(query_lexemes)s);
```

Measured at 14 ms for the 3,458 distinct tokens in the largest document in the database (1.03 MB).

*List page* — per-row scanning of full text is not affordable on the 2.7M-row path, so chips there are read from the stored tsvector by the first query above. See §3.3.

**The 255-position cap.** Postgres stores at most 255 positions per lexeme in a tsvector. A count read from the index therefore saturates silently: a term occurring 400 times reports 255. Counts that reach the cap are rendered `255+` rather than as a number the page cannot stand behind. The detail page is unaffected — its scan counts the real text.

### 3.2 CPV matches

CPV chips are a separate class in a distinct colour (orange; keywords green). They are matched against the act's line-item CPV codes as a **prefix**, mirroring what the `cpv` filter itself does, not against the tsvector, and count as one hit each unless the code also appears in the body text — in which case the literal appearances are counted and anchored, so the navigator works for them too.

The **label renders next to the code** (`90911200-8 · Υπηρεσίες καθαρισμού κτιρίων`), resolved from `proc.cpv_code` and localised — a bare `33111000` is unreadable.

### 3.3 List page

The same chips appear on the results row under the title (`Ταιριάζει:`), capped at `LIST_CHIP_CAP = 4` plus `+N`.

**As built: a second batched query, not a `LATERAL` inside the list query.** The draft called for producing chips in the same query as the result rows. Measured, that spends the cost on every candidate row rather than on the ~10–50 that are displayed. The implementation takes §6's own stated fallback instead — one extra query keyed by the ids the result page already returned, so the search query is **byte-for-byte unchanged** and the 2.7M-row path costs exactly what it did before. Still one query for the whole page; never a per-row follow-up. A test asserts the query count is 2 (lexeme resolution + the tsvector read) regardless of how many rows the page holds.

Trailing-`*` prefix terms bypass the tsvector here exactly as they do in the search itself, and are matched against the (short) titles of the page's rows.

### 3.4 Acceptance criteria

- [x] Chip counts equal the number of highlighted occurrences rendered on the page. *Verified live: chips 41 + 10, exactly 51 marks rendered; a second act, chip 307, marks 307. Guaranteed by construction — see §3.1 — and asserted by `test_chip_count_equals_rendered_marks`.*
- [x] CPV chips show code + label.
- [x] ~~Adding chips to the results list adds no additional query~~ — **superseded.** It adds exactly one batched query and leaves the search query unchanged; see §3.3. Cost: **+3.5 ms** at the default page size of 10, **+15 ms** at the maximum of 50, against a ~12 ms search and a ~2.2 s page render (under 1% of page time).
- [x] Panel is absent (not empty) when the user arrived without a query. *Also absent when the query is only stop words — the index holds nothing for them, so they explain nothing.*

### 3.5 Result-email pages (extension, 2026-08-31)

`/digests/<token>` — the list one result email contained — carries the same chips, and its act links carry the terms, so an act opened from a result email explains and highlights its own match exactly as one opened from a search. Two things had to be true for that:

- **The page has no query string.** Its rows come from `proc.digest_run_item`, recorded at send time, not from a live search. So the card no longer derives the act link from `request.query_params` alone: `_result_card.html` takes a caller-supplied `match_link` when there is one and falls back to `match_qs(request)` otherwise. The search path is byte-for-byte what it was.
- **The saved search is live; the run is history.** Explaining a three-week-old email with the words the customer's profile holds *today* would be confidently wrong, so each send records the filter set it was actually built with — `proc.digest_run.params_qs`, in the same querystring shape `search_profiles.params_to_qs` produces. This is the first schema change the feature has needed (`migrations/20260831093000_digest_run_search_terms_for_match_explanation.sql`), so §2's "no migration" holds for the original scope only. Runs recorded before that column existed fall back to the subscription's current profile — the best answer still available for them, and second, not first (`digests.run_params`).

The chips themselves are `list_chips` unchanged: one batched query for the page's rows, on the same terms. The email BODY is untouched — it lists acts, and the explanation lives where the reader can act on it.

---

## 4. Feature C — Occurrence navigator

Clicking a chip opens a popover listing every occurrence of that term: section label, a ±60-character snippet with the term emphasised, in document order. Clicking an occurrence closes the popover, scrolls to it and flashes it.

### 4.1 Endpoint

```
GET /act/{adam}/occurrences?term=<raw term>
→ HTML fragment (popover body)
```

Signed-in and entitled only: the full text is behind the paywall, so an index into it is too. Gated callers get a 404, asserted by test.

Chip markup:

```html
<button hx-get="/act/{{ act.adam }}/occurrences?term={{ term|urlencode }}"
        hx-target="#occ-popover" hx-swap="innerHTML"
        aria-haspopup="dialog" data-occ-term="{{ term }}">…</button>
```

The handler re-runs the same scan the page render used (§3.1), over `title` and `full_text` — exactly the fields `search_tsv` is generated from, so a chip can neither claim a hit in text the index never saw nor miss one it did. There is no `short_description` to scan (§2). Each hit returns its section, paragraph number and heading, an `anchor_id` (`occ-{term_slug}-{n}`, where the slug is a hash of the folded term so it is ASCII-safe in ids and fragments) and `snippet_html`.

Capped at `OCC_CAP = 50` per term, ordered by section then position, with `Εμφανίζονται 50 από 213` in the footer. The cap limits what is **listed**, never what is counted.

### 4.2 Section labels

Not "full text" — the label is what makes this feature useful:

- `Τίτλος`
- `Πλήρες κείμενο — παρ. 12`
- `Πλήρες κείμενο — ΤΜΗΜΑ 3 — παρ. 12` where a lot/section heading is detectable

Heading detection is deliberately narrow (`ΤΜΗΜΑ|ΜΕΡΟΣ|ΑΡΘΡΟ|ΠΑΡΑΡΤΗΜΑ|ΚΕΦΑΛΑΙΟ` + a number or Greek numeral, in either case): a false positive mislabels an occurrence, which is worse than a plain `παρ. 12`. A detected heading carries forward to the paragraphs beneath it.

Paragraph numbering comes from `split_full_text()` in `app/textmatch.py`, used by **both** the renderer and the occurrence scanner — the single most likely source of bugs in the feature, so there is exactly one splitter. It splits on blank lines, falls back to single newlines, and finally chunks an unbroken OCR blob at word boundaries so a label still means something. Offsets are preserved throughout.

### 4.3 Interaction with the collapsed full text

**As built: there are no lazy tabs.** The draft assumed the detail page loads tabs lazily over HTMX. It does not — only the extracted-tables panel is lazy; the full text is server-rendered inside a collapsed `<details id="ft-details">`. The handling is correspondingly simpler:

1. Find the target `<mark>` by id.
2. If it sits inside a closed `<details>`, open it **before** measuring — an element inside a closed one has no layout.
3. `scrollIntoView`, then add `.occ-flash` for 1.2s.

`app/static/js/occurrences.js` (~90 lines) does this, plus popover placement, click-outside, `Esc`, and arrow-key navigation. No framework; the only state is the chip that opened the popover, so `Esc` can return focus to it.

**Rendering trade-off.** When a query has hits in the full text, the panel renders the paragraph-split, anchored, highlighted view instead of `full_text_html`. Anchors cannot be placed reliably inside curator-authored rich text, and the anchors are what make the feature work. With no query — or no full-text hits — the page renders exactly as it did before, `full_text_html` included. The official text is never altered; highlighting is added to the view only.

### 4.4 Acceptance criteria

- [x] Every listed occurrence, when clicked, lands on a visibly highlighted term — including when the full text was never expanded. *Asserted by test: every listed anchor exists in the rendered DOM.*
- [x] Popover opens in < 300 ms for a very large OCR'd full text. *189 ms for the largest document in the database (1.03 MB).*
- [x] Snippets never split a UTF-8 grapheme, and are trimmed at word boundaries.
- [x] Keyboard: chip is focusable, popover items are arrow-navigable, Esc closes and returns focus to the chip. *Verified in-browser: focus lands on the first item, ArrowDown stays in the list, Esc hides and restores focus, `aria-expanded` toggles.*
- [x] Occurrence count matches the chip count exactly (§3.4).

---

## 5. Shared: Greek-safe matching and highlighting

Both features depend on one normaliser. Get this wrong and the counts, the snippets and the highlights all disagree with each other.

Requirements:

1. **Accent-insensitive**: `ΑΝΆΘΕΣΗ`, `ανάθεση`, `αναθεση` are the same term.
2. **Final sigma**: `ς` folds to `σ`.
3. **Case-insensitive**, using Greek casing rules (`Σ`/`σ`/`ς`).
4. **Offset-preserving** — the constraint that dictates the implementation. Do **not** use NFD-decompose-and-strip: it changes string length and destroys the index mapping back to the original text. Fold per *character*, so `len(fold(s)) == len(s)` and every index in the folded string is valid in the original.

**Correction to the draft.** The sketched implementation was:

```python
def fold(s): return s.casefold().translate(_FOLD)   # NOT length-preserving
```

`str.casefold()` is not length-preserving — `ß` → `ss`, `ﬁ` → `fi` — and `str.lower()` has the same problem (`İ` → `i̇`, two codepoints). Either one silently breaks the offset guarantee for a minority of inputs, which is the hardest class of bug to notice here: the counts stay right and the highlights land one character off. As built, folding goes through a lazy per-codepoint `str.translate` table that lowercases only where the result is a single character, falls back to the original otherwise, and strips NFD combining marks to recover the base letter — covering polytonic Greek and accented Latin as a side effect. `test_fold_preserves_length_exactly` pins the invariant.

Match on `fold(text)`, slice snippets and insert `<mark>` using the resulting offsets against the **original** text. Escape the original text first, then insert the marks — never the other way round, and never `|safe` on unescaped act content.

Highlighting is `<mark class="hl">` styled to the existing brand palette.

**Stemming is visible now, and that is the point.** The `greek` configuration stems aggressively: `καθαρισμός` and `καθαρού` both reduce to `καθαρ`, so a search for the first highlights the second. That is the existing behaviour of the index — the feature does not introduce it, it exposes it. The help page says so plainly, since a count larger than the number of exact spellings would otherwise read as a bug.

---

## 6. Performance and index notes

Measured on the live local database (~2.88M acts), median of 7 runs.

| | Measured |
|---|---|
| The search query itself | **unchanged** — chips are a separate batched query |
| List chips, 10 rows (default page size) | +3.5 ms |
| List chips, 50 rows (maximum page size) | +15 ms |
| List chips, 50 *largest* documents in the DB (synthetic worst case) | 47 ms |
| Occurrence popover, 1.03 MB OCR'd document | 189 ms (budget 300) |
| Stemming 3,458 distinct tokens in that document | 14 ms |

- Feature C is per-act detail-page work: single-row, no impact on the list path.
- Feature B's list chips were the only 2.7M-row risk, and the batched-query design (§3.3) removes it — `run_search()` is untouched, so there is nothing to re-`EXPLAIN`.
- No `setweight` change was needed: `search_tsv` was already weighted, so **no GIN rebuild and no `CREATE INDEX CONCURRENTLY`** — and therefore no direct-5432 step.
- Materialised views that aggregate over acts are unaffected — no columns they read are changed.

---

## 7. Repo constraints checklist (from `CLAUDE.md`)

- [x] Bind and test against `127.0.0.1`, never `localhost`.
- [x] No `--reload` when running uvicorn.
- [x] Every migration applied to **both** local and Supabase before any code push. *N/A — this work adds no migration.*
- [x] Any `CREATE INDEX CONCURRENTLY` run over the direct 5432 connection. *N/A — no index change; see §6.*
- [x] The four critical `main.py` wirings preserved: `TABLES_ENABLED`, the `full_text` detail columns, the `reltuples` counter fix, and the root-anchored `WITH RECURSIVE` chain query. *Verified: the act-detail `SELECT` is unchanged; the paragraph split reads `full_text` from the row that query already returns rather than refactoring it.*
- [x] After any CSS edit, grep for `</style>`. *One occurrence in `beta_base.html`.*
- [x] Tests ship with the feature. *32 new; full suite 458 passed.*
- [x] `/help` updated — a user-facing feature changed.

---

## 8. Deliberately out of scope

- **Attachment-level matches.** Now a moderate increment rather than a large one: uploaded attachments already carry `extracted_text` and a `content_tsv` on `proc.act_attachment`, and the `q` box already searches them. What is missing is a third section source in the scan plus a `Παράρτημα Β, σελ. 4` style label — the `section` field is designed to take it without a schema change. (Note the distinction: OCR of the act's *own* document lands in `procurement_act.full_text` with `full_text_source = 'auto:ocr-local'`, and is therefore already covered.)
- **Chain position as a match section.** Showing *which link* of the προκήρυξη → διακήρυξη → κατακύρωση → σύμβαση chain a match landed on is arguably worth more than any of the above. It needs the recursive chain query in the search path and belongs in its own spec.
- **Feedback widget and inbox.** Separate, unrelated, cheap.
- **AI-rewritten titles** (the former Feature A): an AI-generated Greek title alongside the official one, with a global header toggle, a batch generation command with post-validation, and an authored-class column the import guard must never overwrite. Cut on 2026-08-28. It is the only part of the original spec that needs a schema migration.
- Everything decorative in the prototype: the star/print/share/assign/note toolbar, "Manage views", saved views, the empty "Competition & Market Information" tab.

---

## 9. Build order (as executed)

1. §5 normaliser + tests (accented, uppercase and final-sigma Greek fixtures). Nothing else works without it.
2. §4.2 shared `split_full_text` helper, with the full-text renderer switched onto it and the `full_text` detail columns re-verified.
3. Feature B: detail-page panel first (single-row, no list risk), then list chips behind a measurement.
4. Feature C: endpoint, popover, scroll/flash handling.

---

## 10. Questions, resolved

1. **Is `search_tsv` built with `setweight`?** Yes — `A` = title, `B` = full text, config `greek`. No rebuild needed.
2. **Is OCR'd attachment text inside `full_text`, or separate?** Both, and the distinction matters: OCR of the act's own document is written into `procurement_act.full_text` (`full_text_source = 'auto:ocr-local'`), while *uploaded* attachment files keep their own `extracted_text` + `content_tsv` on `proc.act_attachment`. See §8.
3. **Does the results list return a rank value that could carry the matched lexemes for free?** No. `run_search()` computes `ts_rank` only when sorting by relevance on the `fulltext` param, and carries no lexemes. Not needed — §3.3's batched query is cheaper than threading them through.
4. **Which Greek text search configuration?** `greek` (Snowball), used identically for the query side, the per-token stemming and the stored vectors.

### Still open

- **Should the list page show counts at all, given the 255-position cap?** Today a saturated count renders `255+`, which is honest but draws the eye to a number that is really "a lot". The alternative is chips without counts on the list and counts only on the detail page, where they are exact. Worth a look once there is usage to judge it by.
