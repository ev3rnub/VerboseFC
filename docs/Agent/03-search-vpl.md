# 03 — Query Engines: VPL & Keyword Search

Files: `src/vpl_parser.py` (807 L), `src/search_engine.py` (732 L).

## 1. `vpl_parser.py` — the VPL pipeline language

The Search tab routes **every** query through this module (`search_page._to_pipeline`
prefixes bare keywords with `search `). Grammar overview: stages separated by `|`,
first stage must be a primary command (`search`, `where`, `stats`, `sourcetypes`,
`fieldsummary`, `index …`, or a config command).

### Errors
`VplError(ValueError)` L38 — raised for *every* parse/translation failure with a
human-readable message (the UI shows `VPL: <msg>`). Also wraps ingestion errors
(`_run_config` L697).

### Field map — `COLS` L84–100
VPL name → `(sql column, value type)`:

| VPL field | SQL column | type |
|---|---|---|
| `ext` | extension | text |
| `name` | name | text |
| `path` | full_path | text |
| `size` | size_bytes | bytes |
| `modified` | modified_at_epoch | epoch |
| `created` | created_at_epoch | epoch |
| `md5` / `sha256` | md5 / sha256 | text |
| `category` | file_category | text |
| `type` | file_type | text |
| aliases | `date`/`time` → modified_at_epoch; `file`/`filename` → name | |

Literal/unit regexes: `SIZE_RE` L102 (`500kb`, `1mb`, `2gb`, …), `TIME_RE` L103
(`s|m|h|d|w`), `DATE_RE` L104 (`YYYY-MM-DD`), multipliers `UNIT` L105 / `TUNIT` L106.
`DEFAULT_LIMIT = 1000` L108.

### Dynamic fields — `_Ctx` L47 / `_build_ctx(db, query)` L61
Per-query context adds `index → index_name` and `sourcetype → sourcetype`, then each
extraction rule from `IngestionStore.extracted_field_names()` registered as an
inlined expression `json_extract(extracted_json, '$.<name>')` (`add_json` L55 — the
expression is inlined into SQL, never bound as a parameter).
**`kv:` rule (L71–78):** an extraction whose plain name would shadow a built-in is
stored under `kv:<name>`; the parser keeps the `kv:` key only when the stripped name
already collides, skips if the final key exists anyway. So `where kv:size==…` works
while a shadowing bare name does not.

### Stage objects L113–163
`SearchStage(match)` 113 · `WhereStage(sql, params)` 117 · `SortStage(order)` 121 ·
`HeadStage(n)` 125 · `FieldsStage(cols)` 129 · `StatsStage(select, group)` 133 ·
`SourcetypesStage(limit)` 137 · `FieldSummaryStage(limit)` 141 · `IndexesStage` 145 ·
`IndexAddStage(name, path_prefix, exts)` 148 · `IndexRmStage(name)` 152 ·
`SourcetypeAddStage(name, exts, path_contains, content_regex)` 156 ·
`ExtractAddStage(name, regex, sourcetype)` 161.
`_CONFIG_STAGES = (IndexAdd, IndexRm, SourcetypeAdd, ExtractAdd)` L526 — mutate
ingestion config at execute time.

### Pipe splitting — `_split_pipes` L168
Char scan with a single quote state; `'` or `"` opens, same char closes; `|` splits
only outside quotes. **No escape handling** — an unterminated quote swallows the
rest of the query. Empty segments dropped.

### FTS normalization — `_normalize_fts` L188
Quoted runs copied verbatim; each alphabetic run: `and`/`or`/`not` (any case) →
uppercased; everything else unchanged (digits, `*` prefixes, parentheses, FTS5
syntax preserved). `_parse_search` L323 errors on empty terms. The normalized string
is wrapped as `(<match>)` and bound as the FTS5 `MATCH ?` argument — users get
`"phrase"`, `prefix*`, `AND/OR/NOT`, `-term`.

### Value conversion — `_convert_value(raw, vtype)` L213
1. Matching quotes stripped.
2. `bytes`: `SIZE_RE` → `n * UNIT`; bare digits → int; else VplError.
3. `epoch`: `TIME_RE` → `time.time() − n*TUNIT` (**relative = "older than"**:
   `7d`, `24h`, `2w`); `DATE_RE` → local midnight; bare number → raw epoch float;
   else VplError.
4. text: lowercased.

### Where grammar — `_WhereParser` L245 (recursive descent)
- Tokenizer `_TOK_RE` L240; operators `_OPS` L242: `==`/`=` → `=`, `!=` → `<>`,
  `> >= < <=` direct; `contains` handled as a word token.
- Precedence: `or` < `and` < atom; parentheses supported; joins parenthesized.
- `parse_comparison` L293: field must exist in the (extended) cols map, else
  `where: unknown field '<f>' (fields: …)`.
  - `contains` → `lower(col) LIKE '%value%'`.
  - `ext` comparisons strip leading dots both sides via `ltrim(col,'.')` (L307–308) —
    `ext==pdf` and `ext==.pdf` both work.
  - text equality (`=`/`<>`) is `lower(col) <op> ?` (case-insensitive); other
    comparisons raw.
- `_parse_where(body, cols)` L314 wraps SQL as `(<sql>)`, params tuple.

### Other stage parsers
- `sort` L330: comma list, `-field` or `field desc` for descending, default asc;
  unknown field / bad direction → VplError.
- `head` L349: digits only.
- `fields` L355: comma list; validated at execution.
- `stats` L365 with `_AGG_RE` L362: `^(count|sum|avg|min|max)(\(col\))?( as alias)?$`;
  body `… [by <field>]` (the **last** `by` wins; group must be a known field).
  `count` → `COUNT(*)`; other aggregates need a known **non-text** column
  (`size`, `modified`, `created`). Default alias `<func>_<col>`; duplicates rejected.
  Group column placed **first** in the select list.
- `sourcetypes [N]` L410 / `fieldsummary [N]` L414 via `_parse_limit` L401 (default 20).
- `index` L418: only `''`/`list` for the report form; dispatch L484 →
  `IndexesStage | add | rm|remove|delete`.
- `index add <name> <path-prefix> [exts=a,b]` L424 — name lowercased, `exts=`/`ext=`
  removed anywhere from the remainder.
- `index rm <name>` L440.
- `sourcetype add <name> [exts=…] [path=…] [content="regex"]` L447 — at least one
  matcher; `content=` accepts double/single-quoted or bare values.
  ⚠️ the regex is matched against the `file_type` display string, not file content
  (see 04-data-layer.md). `_dispatch_sourcetype` L497: `add` only; any other sub
  falls through to the `sourcetypes` report parser (so `sourcetype 5` ≡ `sourcetypes 5`).
- `extract add <name> "regex(with (group))" [sourcetype=text]` L469 — the only
  parser with backslash escapes (`\"` unescaped L477).

### `parse_vpl(query, db=None) -> list` L529
Empty → VplError. Field map = `_build_ctx(db, query).cols` **only when `db` is
given** (without a db, `index`/`sourcetype`/extracted fields are unknown fields).
Each segment: first word = command, unknown → VplError with command list. `where`,
`sort`, `stats` receive the cols map. Pipeline must contain ≥1 primary stage
(`SearchStage, WhereStage, StatsStage, SourcetypesStage, FieldSummaryStage,
IndexesStage` or any config stage).

### SQL translation & execution
`VplResult(df, summary, total)` L552.

`_build_base(stages, ctx)` L557: all search matches AND-joined `(m1) AND (m2)`;
all where SQLs AND-joined (default `1=1`), params in stage order.
With a search stage: `FROM files f JOIN content ON f.id = content.file_id WHERE
content MATCH ? AND <wheres>` (FTS expr is `params[0]`); without: `FROM files f WHERE <wheres>`.

`add_time_range(stages, min_epoch, max_epoch)` L576 appends a `WhereStage` on
`modified_at_epoch` — no-op for config pipelines.
`vpl_count(db, stages)` L594 · `vpl_field_counts(db, stages, field, limit=12)` L602
(`GROUP BY <col> ORDER BY n DESC`, unknown field → VplError).

**Timeline:** `_TIMELINE_LADDER` L615 = 1m,5m,15m,1h,3h,6h,1d,3d,7d,30d.
`vpl_timeline(db, stages, max_buckets=40)` L618: MIN/MAX of modified; bucket width =
first ladder value with `span/width ≤ 40` (else 30d); then
`SELECT (CAST(modified_at_epoch/? AS INTEGER)*?) AS b, COUNT(*) GROUP BY b` →
`[(bucket_epoch, count)]`.

**Reports:**
- `_run_index_list(db)` L653 — row 0 = the DB path with file count / with-content /
  total size; then top-20 pseudo-index roots (first two path components of file
  paths) by file count. Columns `index, files, with_content, total_size`.
- `fieldsummary N` L746 — over `_SUMMARY_FIELDS` L639 (13 columns): per field
  `distinct` + `coverage %`.
- `sourcetypes` L737 — `GROUP BY extension, file_type ORDER BY count DESC LIMIT ?`.
- `stats` L761 — `SELECT <select> {base} GROUP BY <group> ORDER BY 2 DESC LIMIT 500`
  (group col first in select ⇒ `ORDER BY 2` sorts by the first aggregate).
- Config commands → `_run_config` L676: applies each stage to `IngestionStore`,
  then `reindex_all()`; `IngestionError` → `VplError`. Result df column `action`.

### `run_vpl(db, stages, default_limit=1000)` L712 — execution order
1. Any config stage → `_run_config` short-circuit (L716).
2. Stage selection L721–728: all search/where stages; **last** sort/fields/stats/
   sourcetypes/fieldsummary; `head = min(all heads)`; **first** IndexesStage.
3. `index list` branch L733.
4. Report branches (sourcetypes / fieldsummary / stats) above; else file rows L769:
   default `ORDER BY bm25(content) ASC` when searches exist (best relevance first),
   else `modified_at_epoch DESC`; `SELECT f.* {base} ORDER BY <order> LIMIT head`.
5. Extracted fields surfaced as DataFrame columns L775 (`SELECT id, extracted_json
   WHERE id IN (…)`; keys added unless the column already exists).
6. `total` L787 = `len(df)`; re-counted via `vpl_count`-style query only when the
   head cap was hit.
7. `fields` applied last L793: unknown *and unbuildable* names → VplError; names
   buildable in `ctx.cols` but absent from the result (e.g. `ext` vs `extension`)
   are **silently dropped**; output order follows the request.
8. `summary` L800 = ` | `-joined per-stage renderings.

### Gotchas
- Multiple search/where stages AND-combine; only the **last** sort/fields/stats/
  report stage counts; smallest `head` wins.
- No filesystem re-scan after config commands — `reindex_all()` rewrites derived
  columns only.

## 2. `search_engine.py` — `SearchEngine` (keyword / natural-language path)

The live Search tab uses this class mainly for **history**; the query itself goes
through VPL. The class implements a separate natural-language keyword path.

### Constants
- `DATE_KEYWORDS` L21–36: `today, yesterday, last week, this week, last month,
  this month, this year` → `(start,end)` datetimes (`last/this week` identical =
  now−7d; `last/this month` identical = now−30d).
- `SIZE_KEYWORDS` L39–43: `large = 100 MiB`, `small = 1 MiB`, `medium = 10 MiB`.
- `FILE_TYPE_ALIASES` L46–59: `pdf→.pdf, word→.docx, excel→.xlsx, powerpoint→.pptx,
  text→.txt, markdown→.md`, and bare category names `image/video/audio/code/document/archive`.

### Saved searches (legacy nested class)
`SearchEngine.SavedSearches` L74 creates its own `saved_searches(id, name, query,
filters TEXT json, created_at, is_active DEFAULT 1)` table — ⚠️ **name-collides with
`saved_searches.py`'s richer `saved_searches` table** (different columns; whichever
runs first wins, the other's `IF NOT EXISTS` no-ops). The UI only uses
`SavedSearchStore` from `saved_searches.py`; this nested class is not wired in.
Accessors L124/L130/L137/L144 lazily expose `SavedSearches`, `SearchHistory`,
`SearchSuggestions`, `SearchFeedback`.

### `parse_query(query) -> dict` L151
Returns `{keywords, phrases, operators{and,or,not}, dates, file_types,
size_range, paths, raw_query}`. Steps (each match removed from the working string):
1. Quoted phrases L180.
2. `AND w` L196, `OR w` L200, `NOT w`/` -word` L205 (`-word` only after whitespace,
   so hyphenated words survive).
3. Date keyword L214 → `dates`; explicit `from YYYY-MM-DD to YYYY-MM-DD` L223
   **overwrites** it.
4. Size words `large|small|medium` L236; numeric `>5mb`-style L250 **only if no size
   word** (no operator ⇒ an exact band).
5. First file-type alias L269; bare `.ext` tokens L278.
6. Path-like tokens starting with `/` or `\` L284 → `paths` — collected but **never
   used** by any filter.
7. Remaining words → `keywords` (lowercased, length > 2).

`build_fts_query(parsed)` L294: `"phrase"` + AND terms + `(a OR b)` + `-not` +
keywords, space-joined (FTS5 implicit AND).
`build_metadata_filter(parsed)` L331: min/max modified, min/max size,
file_types split into `file_categories` vs `extensions` (L355–370).

### Fuzzy & proximity
- `_fuzzy_match_keywords` L374 (`difflib.SequenceMatcher` ≥0.8) /
  `_apply_fuzzy_matching` L385 (vocabulary = 1000 names + 500 previews; wrapped in
  `try/except: pass`). ⚠️ In `search()` fuzzy keywords are added **after**
  `build_fts_query`, so they only show up in the returned info, never in the query.
- `_parse_proximity_search` L416: `X near|around N Y` → `_build_proximity_fts_query`
  L433 emits `NEAR(X Y, distance)`.

### `search(query, limit=100, offset=0) -> (results, total, parsed)` L442
Proximity extraction → `parse_query` → `build_fts_query` → fuzzy (info-only) →
proximity AND-joined → metadata filter.
- FTS path L482: `db.search_files(fts_query, limit, offset)`; any exception →
  `([], 0)` silently.
- **Metadata filtering happens in Python** over the returned page (size, dates,
  category, extension); `total` becomes the post-filter count.
- Pure-metadata fallback L520–566 (no FTS hits): one
  `db.search_files_by_metadata` call per category/extension, concatenated and
  deduped by `id`.
- History recorded via `get_search_history().add(query, total)` L568 (swallowed).

### Result row shape
`dict(row)` of `files` (+ `rank` on the FTS path): `id, name, full_path, size_bytes,
size_human, md5, sha256, extension, file_type, file_category, created_at,
modified_at, accessed_at, content_available, content_preview,
created_at_epoch, modified_at_epoch, accessed_at_epoch` — plus `index_name,
sourcetype, extracted_json` after `ingestion.py` has run its `ALTER`s.

### Suggestions & feedback
- `get_search_suggestions(prefix, limit=10)` L578 returns static keyword
  completions (dates/sizes/file types). ⚠️ **Documented defect:** the lazy manager
  accessor at L137 shares this name; the later definition wins, so the accessor is
  unreachable and the `self.get_search_suggestions()` calls inside
  `get_search_suggestions_for_prefix` (L623) raise `TypeError` swallowed by
  `except: pass`. Only the history branch of suggestions works.
- `highlight_terms(text, terms, max_length=200)` L681 — `<mark>` snippets ±50 chars
  around the first match (currently unused by the UI; `search_page` does its own
  `re.sub` bolding).
- `create_search_engine(db)` L730 factory.

### Scope corrections (for other docs)
- Timeline bucketing and field counts live in **`vpl_parser.py`**, not here.
- CSV export is a Streamlit-UI concern (`search_page.py` L254, L269), not an engine feature.
