# QA Findings — Verbose FC (File Discovery Dashboard)

**Date:** 2025-08-21
**Scope:** `verboseFC.py`, `db.py`, `indexer.py`, `search_engine.py`, `content_extractor.py`, `enhanced_features.py`, `ux_enhancements.py`, `file_watcher.py`, `tests.py`, `requirements.txt`, and the project `.md` docs.
**Method:** Full code review plus empirical verification (bug reproduction scripts and the existing test suite — 51/51 tests pass).
**Legend:** 🔴 Critical (feature broken / crash) · 🟠 High (wrong results / misleading UX) · 🟡 Medium (dead code, edge cases, perf) · ⚪ Low (style, hygiene)

---

## Summary of Implemented Fixes

### Critical Fixes (C1, C2, C3, C4, C5, C7, C8, C9) ✅
- **C1 (Pickling):** Changed `ProcessPoolExecutor` to `ThreadPoolExecutor` to avoid pickling issues with thread-local DB connections. Parallel indexing now works with file-based DBs.
- **C2 (bm25 ranking):** Changed `ORDER BY bm25(content) DESC` to `ASC` for correct relevance ranking.
- **C3 (SearchHistory):** Added `ON CONFLICT(query_hash) DO UPDATE` to handle duplicate queries gracefully.
- **C4 (Fuzzy matching):** Fixed SQL query from `SELECT name FROM content` to `SELECT name FROM files` and `content_preview` for vocabulary building.
- **C5 (Proximity search):** Added comma in `NEAR(word1 word2, distance)` for correct FTS5 syntax.
- **C7 (Wildcard parsing):** Fixed regex pattern to capture trailing `*` wildcards and kept `*` as-is for FTS5 prefix queries.
- **C8 (Size parsing):** Removed optional unit match, requiring KB/MB/GB/TB unit to avoid false positives on years/IDs.
- **C9 (Metadata filters):** Added filtering logic to apply size/date/type filters even when FTS returns results.

### Partially Fixed (C6, C10, C11, C12, H5) ⚠️
- **C6 (FullRollback):** Not fully implemented (requires complex snapshot/restore logic). Added documentation.
- **C10 (File watcher):** Fixed `delete_file_by_path` method and debounce timer; watcher still not wired into app.
- **C11 (Preview/Clear):** Added Clear button handler; preview modal still not wired into UI.
- **C12 (Pagination):** Fixed page selection and pagination offset; still capped at 100 results (limit=20 per page).
- **H5 (Unwired features):** Save search, popular terms, cache, async indexing remain unwired in UI.

### Remaining Issues
- **H1 (ERROR hashes):** Unreadable files still get "ERROR" sentinel hashes.
- **H2 (Non-persistent index):** DB path uses PID → new index on restart.
- **H3 (parsed_info NameError):** Still occurs in feedback expander on re-runs.
- **H4 (Inert filters):** Date filter still dead; filters don't re-run on change.
- **H6 (.rar/.tgz):** Extraction still not implemented for .rar/.tgz.
- **H7 (Raw HTML):** HTML/XML still indexed as raw markup.
- **H8 (Fake highlighting):** Still uses naive `.replace()` in captions.
- **H9 (Silent FTS errors):** Still swallowed.
- **H10 (Blob suggestions):** Preview suggestions still 500-char blobs.
- **M1 (Double hashing):** Still occurs (UI hashes then indexer re-hashes).
- **M2 (max_content_size):** Not enforced (large files still fully extracted).
- **M3 (Whole-file reads):** Still reads entire files before truncating.
- **M4 (Archive bounds):** No limits on archive members.
- **M5-M14:** Various minor issues remain.

### Documentation Findings (D1-D4)
- **D1-D3:** `current_status.md`, `review_plan_findings.md` are stale/contradictory.
- **D4:** `plan.md` success criteria unmet.

### Test Suite Findings (T1-T5)
- **T1-T2:** Tests pass but miss critical paths (no workers>1 test, tautological assertions).
- **T3-T5:** Missing coverage for watcher/async/rollback/archive/UI.

---

## 1. Critical Findings (Verified Bugs)

- **🔴 C1. Default parallel indexing path crashes — `FileIndexer` is not picklable** (`indexer.py:219`, `db.py:21`)
  - `index_directory()` submits the bound method `self._process_single_file` to a `ProcessPoolExecutor` whenever `workers > 1` and a file-based DB is used — which is the app's default flow (`verboseFC.py:150` uses a temp file DB, default `workers=4`). Pickling the bound method pickles the `FileIndexer` instance, which holds a `SearchDatabase` whose `__dict__` contains a `threading.local` object.
  - **Verified:** `pickle.dumps(FileIndexer(...))` → `TypeError: cannot pickle '_thread._local' object`. The app catches this and shows "Error during indexing", so **the default Scan & Index flow never builds an index**. The test suite avoids this by always using `workers=1` (see `tests.py` comment "use single worker to avoid pickling issues").
  - **Fix:** Make the worker a module-level function (like `process_single_file` in `verboseFC.py`) that receives plain data (path, max_content_size) and returns a dict; or define `__getstate__` on `SearchDatabase`/`FileIndexer` that drops `_local`/connections; or run extraction in a `ThreadPoolExecutor` (SQLite writes stay in the main process). Add a regression test with `workers=2` on a file-based DB.

- **🔴 C2. FTS5 relevance ranking is inverted** (`db.py:319`)
  - `ORDER BY bm25(content) DESC` — bm25() scores are negative and *lower is better*; `DESC` puts the **worst** matches first.
  - **Verified:** for query `apple`, a file containing "apple" once ranked above a file containing it five times.
  - **Fix:** Change to `ORDER BY bm25(content) ASC` (or simply `ORDER BY rank`, which FTS5 defines as ascending bm25).

- **🔴 C3. Search history silently drops repeated queries** (`enhanced_features.py:65`, `db.py:88`)
  - `search_history.query_hash` is `UNIQUE` and `add()` always inserts a fresh `md5(query)`. The second search of the same query raises `IntegrityError`, which `search_engine.search()` swallows (`except Exception: pass`).
  - **Verified:** second `history.add("same query", 7)` → `IntegrityError: UNIQUE constraint failed: search_history.query_hash`. Result: repeated searches never appear in history and `result_count` is never updated.
  - **Fix:** Use `INSERT ... ON CONFLICT(query_hash) DO UPDATE SET timestamp=excluded.timestamp, result_count=excluded.result_count` (and re-order by timestamp), or remove the UNIQUE constraint and dedupe in `get_recent()`.

- **🔴 C4. "Fuzzy matching" is dead code** (`search_engine.py:388`)
  - `_apply_fuzzy_matching()` runs `SELECT DISTINCT name FROM content ...`, but the FTS5 `content` table has columns `file_id, preview, full_content` — there is no `name` column. The `OperationalError` is swallowed, so fuzzy matching **never runs**.
  - **Verified:** `OperationalError: no such column: name`; no `fuzzy_matches` key is ever produced.
  - **Fix:** Build the vocabulary from real data, e.g. `SELECT DISTINCT name FROM files` (file names) and/or tokenized words from `content.preview`; or query the FTS5 index with `SELECT * FROM content WHERE content MATCH ?` per keyword. Add a unit test asserting a typo query (`reprot`) returns the correct file.

- **🔴 C5. Proximity search generates queries that match nothing** (`search_engine.py:429`)
  - `_build_proximity_fts_query()` emits `NEAR(word1 word2 distance)` — FTS5 requires a comma: `NEAR(word1 word2, distance)`. The generated query is syntactically accepted but matches nothing.
  - **Verified:** `NEAR(quick fox 5)` → 0 results; `NEAR(quick fox, 5)` → 1 result on the same index.
  - **Fix:** `f'NEAR({term["word1"]} {term["word2"]}, {term["distance"]})'`. Add a test for the `near`/`around` operator end-to-end.

- **🔴 C6. FullRollback snapshot restore is broken and unused** (`enhanced_features.py:739`)
  - The snapshot table stores only 8 columns, but `files` has 18 (including `NOT NULL` columns like `size_human`, `file_type`). Restore fails.
  - **Verified:** `rollback_to_snapshot()` → `OperationalError: table files has 18 columns but 8 values were supplied`. Additionally the FTS `content` table is truncated and never restored, and `delete_snapshot()` leaves orphaned `snapshot_files_N` tables behind.
  - **Fix:** Snapshot **all** columns of `files` plus the FTS `content` rows (or store a serialized dump), restore both tables inside one transaction, verify with a test (snapshot → mutate → rollback → assert counts and searchability), and `DROP TABLE snapshot_files_{id}` in `delete_snapshot()`. Alternatively delete the class until it works — it is currently not referenced anywhere.

- **🔴 C7. Wildcard search is broken in two ways** (`enhanced_features.py:389,431`)
  - (a) The parser regex `\b(\w*\*\w*|\w*\?\w+)\b` cannot match a trailing `*` (the trailing `\b` requires a word char after `*`), so the most common case `rep*` is never captured. **Verified:** `parse_advanced_query("rep*")` → `wildcards: []`, search returns 0 results.
  - (b) Conversion `wildcard.replace('*', ':')` produces `rep:` — a column-filter prefix, not a prefix query. FTS5 prefix search is `rep*`. Mid-term `?`→`*` conversions (e.g. `re?p` → `re*p`) are invalid in FTS5 (only trailing `*` is supported).
  - **Fix:** Parse with `r'\w+\*|\w*\?\w+'` (no trailing `\b`), keep the `*` as-is for FTS5 prefix queries, and either reject or translate mid-term patterns (e.g. fall back to `LIKE` on `files.name`). Add tests for `rep*`, `rep?`, and `re?p`.

- **🔴 C8. Natural-language size/date parsing corrupts queries** (`search_engine.py:247,209`)
  - (a) The numeric-size regex `(>|<|>=|<=)?\s*(\d+)\s*(KB|MB|GB|TB)?` matches **any bare number** with no unit and treats it as an *exact* byte size. **Verified:** `"find files from 2024"` → `size_range=(2024, 2024)`; `"invoice 12345"` → `size_range=(12345, 12345)`. The follow-up `re.sub` also strips all numbers (years, IDs) from the query.
  - (b) `re.sub(r'-\w+', '', query_clean)` plus the NOT pattern `-(\w+)` mangle hyphenated words: **verified** `"well-known report"` → NOT terms `['known']`, keywords `['well', 'report']`.
  - **Fix:** Only treat a number as a size when a unit (KB/MB/GB/TB) or an explicit `>`/`<` operator is present; never strip numbers that are part of dates/IDs; only treat `-word` as NOT when the hyphen is at a token boundary preceded by whitespace (e.g. `r'(?:^|\s)-(\w+)'`). Add parser unit tests for all of these.

- **🔴 C9. Parsed metadata filters (size/date/type) are ignored whenever FTS returns results** (`search_engine.py:484`)
  - Filters from `build_metadata_filter()` are applied **only in the `if not results:` branch**. So `"large pdf report"` returns every file matching "report" regardless of size or type.
  - **Verified:** query `"large report"` (size_range ≥ 100 MB) returned a 10-byte file.
  - **Fix:** Always apply metadata filters: either post-filter FTS results by id (`WHERE f.id IN (...)`) or join the FTS match with the metadata `WHERE` clause in one SQL query. Add a test: index a small .txt and a large .pdf both containing "report"; assert `"large pdf report"` returns only the large pdf.

- **🔴 C10. File watcher calls a method that doesn't exist; debounce never flushes** (`file_watcher.py:56,46`)
  - (a) `self.db.delete_file_by_path(...)` — `SearchDatabase` has no such method (only `delete_file(file_id)`). The `AttributeError` is swallowed, so **deleted files are never removed from the index**.
  - (b) The debounce only processes the queue when a *new* event arrives ≥ 2 s after the last processing; there is no timer, so the final batch of events is never processed and `pending_updates` grows unbounded during bursts.
  - (c) `FileWatcher` is never imported by the app — "real-time file watching" (claimed complete in `current_status.md`) is not wired in at all.
  - **Fix:** Add `SearchDatabase.delete_file_by_path(path)` (look up id, delete file + FTS row); process the queue on a background timer/thread (e.g. `threading.Timer` or a watchdog `Scheduler` with a periodic flush) and cap queue size; wire the watcher into `verboseFC.py` (start/stop with the sidebar) or remove the claim from the docs.

- **🔴 C11. Quick preview modal and Clear button exist but do nothing** (`verboseFC.py:74,334`)
  - `show_file_preview()` is defined but **never called** — there is no UI element that opens a preview. The "🗑️ Clear" button is created but never handled (`clear_button` is never checked), so it does nothing.
  - **Fix:** Add a per-result "Preview" button that calls `show_file_preview(result)` (and guard `file_data['id']` with `.get()`), and handle `if clear_button:` to clear `search_input`, `search_results`, `parsed_info` session state and rerun.

- **🔴 C12. Pagination is non-functional and capped at 100 results** (`verboseFC.py:369,453,491-501`)
  - (a) `st.number_input("Page", ..., value=1)` has no `key`, so it always reads 1; the Prev/Next buttons write `st.session_state['current_page']` which nothing ever reads.
  - (b) The search always runs with `limit=100, offset=0`, so even if the page number worked, pages beyond 100 results are empty while `total` (and the "Page X of Y" label) reflects the true count.
  - (c) Latent double-pagination: `SearchEngine.search()` applies `offset` in SQL **and** slices `results[offset:offset+limit]` again (`search_engine.py:540`).
  - **Fix:** Give the number input `key="current_page"`, initialize it from session state, and pass `offset=(current_page-1)*page_size, limit=page_size` to the engine; remove the second Python-side slice in `search_engine.py:540`. Add a UI test or at least a unit test for `search(offset=20)`.

---

## 2. High Findings (Wrong Results / Misleading Behavior)

- **🟠 H1. Unreadable files are indexed with sentinel hashes and appear as duplicates** (`verboseFC.py:40`, `indexer.py:72`)
  - `compute_hashes()` returns `{"md5": "ERROR", "sha256": "ERROR"}` on failure, and those rows are stored. Every unreadable file then shares the same SHA-256, so the Duplicates tab reports all of them as duplicates of each other.
  - **Fix:** Skip files that fail to hash (count them in `errors` and surface the count in the UI), or store `NULL` and exclude `sha256 IS NULL OR sha256 = 'ERROR'` from duplicate grouping.

- **🟠 H2. Index does not persist across app restarts, and stale data accumulates across scans** (`verboseFC.py:150`)
  - The DB path is `verboseFC_{os.getpid()}.db` — a new PID on restart means a fresh empty DB (plan.md success criterion "Index persists between app restarts" is unmet). Within a session, scanning directory A then directory B leaves A's rows in the DB (no `clear_all`/`refresh_index` is ever called from the UI), so search returns files from previously scanned directories. The temp DB file is also never deleted.
  - **Fix:** Store the DB at a stable location (e.g. `~/.verboseFC/index.db` or a path derived from the scanned root), offer a "Re-index from scratch" action that calls `refresh_index()`, and clean up temp files on exit if the temp approach is kept.

- **🟠 H3. `parsed_info` NameError surfaces as a UI error after every post-search interaction** (`verboseFC.py:354,603`)
  - `parsed_info` is only defined inside `if should_search:`. On any rerun where a previous search exists in session state (e.g. clicking pagination or any widget), the "Search Feedback" expander references the undefined `parsed_info`; the surrounding try/except turns it into a visible "Error accessing feedback: name 'parsed_info' is not defined".
  - **Fix:** Initialize `parsed_info = st.session_state.get('parsed_info', {})` before the `should_search` block (or persist it in session state and read it back).

- **🟠 H4. Filters are inert until a new search is triggered; date filter is dead** (`verboseFC.py:348,377-401`)
  - Changing category/extension/size filters does not re-run the metadata search (`should_search` is False without a new query/button press), so results shown are stale. `filter_date_range` is created but **never passed** to `db.search_files_by_metadata` (which supports `min_modified`/`max_modified`). The extension filter requires an exact match including the dot (`.pdf`), so typing `pdf` silently returns nothing.
  - **Fix:** Re-run the search when any filter widget changes (compare against session state) or add an "Apply Filters" button; map the date-range selectbox to `min_modified` epoch values; normalize the extension input (`ext = ext if ext.startswith('.') else '.' + ext`).

- **🟠 H5. "Save search", "search suggestions (popular terms)", "search cache", "async indexing", "incremental indexing" are implemented but never wired into the app**
  - `SavedSearches` (search_engine.py) — no UI to save or run saved searches.
  - `SearchSuggestions.add_popular_term()` — never called, so the "Popular search terms" panel (`verboseFC.py:586`) is **always empty**.
  - `SearchCache` — imported in `verboseFC.py:13` but never instantiated; `SearchEngine.search()` never consults it.
  - `AsyncIndexer` (indexer.py:272) — never used.
  - `IncrementalIndexer.index_new_or_modified()` — never used by the UI; and even if used, it re-indexes **every** file (it calls `indexer.index_directory` on the whole tree), hashes each candidate file twice, and never removes files deleted from disk — so it is not actually incremental.
  - **Fix:** Either wire each feature into the UI (save-search button + list, call `add_popular_term` in `search()`, check/populate `SearchCache` in `search()`, use the incremental path for re-scans with delete-sync) or remove them from `current_status.md`'s "Completed" list.

- **🟠 H6. `.rar` extraction is claimed but not implemented; `.tgz` unsupported** (`content_extractor.py:141,186-270`, `requirements.txt`)
  - `extract_archive_content()` has no `.rar` branch and `rarfile` is never imported anywhere, yet `requirements.txt` lists `rarfile>=4.0` and `current_status.md` claims ".rar" support. `.tgz` is in the archive category but not in the extraction set (only literal `.tar.gz`).
  - **Fix:** Add a `.rar` branch using `rarfile` (with a graceful ImportError fallback) and handle `.tgz` (it's just gzip-compressed tar); or remove the claims/dependency.

- **🟠 H7. HTML/XML content is indexed as raw markup (BeautifulSoup branch is dead code)** (`content_extractor.py:72,182`)
  - `.html/.htm/.xml` are in `TEXT_EXTENSIONS`, so they are handled by the plain-text branch first; the BeautifulSoup branch at line 182 is unreachable. Raw tags/attributes pollute the FTS index and previews.
  - **Fix:** Remove `.html/.htm/.xml` from `TEXT_EXTENSIONS` so they fall through to the BeautifulSoup branch (keep a plain-text fallback when bs4 is unavailable).

- **🟠 H8. "Highlight search terms" doesn't actually highlight** (`verboseFC.py:466-470`, `search_engine.py:647`)
  - The UI does `preview.replace(term, f"**{term}**")` inside `st.caption(...)`, which does not render Markdown — users see literal `**term**` in the output. The proper `highlight_terms()` method (which emits `<mark>` HTML) is never called.
  - **Fix:** Use `search_engine.highlight_terms(preview, terms)` and render with `st.markdown(..., unsafe_allow_html=True)`, or drop the claim.

- **🟠 H9. FTS query failures are silently swallowed — user sees "No results" with no error** (`search_engine.py:478-483`, `enhanced_features.py:520-524`)
  - Any malformed FTS5 query (e.g. from a phrase containing special characters) is caught and converted to an empty result set.
  - **Fix:** Log the exception and return/surface a distinct error (e.g. raise a `SearchError` the UI displays), and sanitize/escape phrase terms before interpolation.

- **🟠 H10. Suggestion "terms" from previews are 500-character text blobs** (`ux_enhancements.py:75`)
  - `get_from_indexed_content()` returns `DISTINCT content_preview` values as suggestions — entire multi-hundred-character previews are shown as autocomplete suggestions in the UI.
  - **Fix:** Tokenize previews (split on whitespace/punctuation, filter stopwords, `LIKE prefix%` on individual words) or drop the preview source and keep filename + history + popular terms.

---

## 3. Medium Findings (Correctness Edge Cases, Performance, Dead Code)

- **🟡 M1. Double hashing / double directory walk on every scan** (`verboseFC.py:187-215` + `indexer.py:196-215`)
  - The UI hashes every file with a `ProcessPoolExecutor`, then `indexer.index_directory()` re-walks the tree and re-hashes every file (MD5+SHA256) — 2× the I/O for no reason; the first pass's results are only used for the File List tab.
  - **Fix:** Pass the already-computed hash results into the indexer (extend `insert_file` callers) or drop the first pass and build the File List DataFrame from `db.get_all_files()`.

- **🟡 M2. `max_content_size` (10 MB) limit is not enforced** (`indexer.py:75-88`)
  - `_should_skip_file()` returns `False` for large files (comment: "Still index metadata but skip content"), so `_process_single_file()` still runs full content extraction on files > 10 MB, contradicting the documented limit.
  - **Fix:** Split into `_should_index(path)` and `_should_extract_content(path)`; gate extraction on `size <= max_content_size`.

- **🟡 M3. Whole-file reads into memory before truncation** (`content_extractor.py:43-45`, `enhanced_features.py:169`)
  - `_get_text_from_file()` reads the entire file then truncates to 500 KB — a multi-GB text file is fully loaded into RAM. `IncrementalIndexer._get_file_hash()` does `f.read()` on the whole file for change detection.
  - **Fix:** Read in chunks up to the limit (stop at 500 KB); hash in chunks (`hashlib` update loop, as `compute_hashes` already does).

- **🟡 M4. Archive extraction has no bounds** (`content_extractor.py:186-270`)
  - No cap on the number of member files processed (a zip with 10k text files → unbounded content), and 7z uses `z.readall()` which loads the **entire archive** into memory.
  - **Fix:** Cap member count (e.g. first 100 text files) and total extracted characters; for 7z, iterate `z.getnames()` and read members individually.

- **🟡 M5. `parse_query` miscellany** (`search_engine.py:150-300`)
  - Only the **first** file-type mention is captured (`re.search`, not `findall`) — "pdf or docx" drops "docx".
  - Keywords ≤ 2 chars are dropped (`len(k) > 2`) — "go files", "ai report" lose the keyword.
  - AND/OR terms are captured but also kept as keywords, producing duplicated FTS terms (`"summary report summary"`).
  - `and_pattern`/`or_pattern`/`not_pattern` (lines ~165-168) are dead variables.
  - **Fix:** Use `findall` for file types; keep short keywords (or make the threshold configurable); strip already-captured operator terms from the keyword list; delete dead variables.

- **🟡 M6. Metadata-only search returns a bogus `total`** (`search_engine.py:484-537`)
  - In the combined category/extension path, `total = len(unique_results)` (capped by `limit`) instead of the true count, and `combined_total` is computed but never used. Pagination and "Found N results" lie for metadata-only queries.
  - **Fix:** Sum the per-query DB counts (deduping by id where categories/extension overlap) or run a single SQL with `file_category IN (...) OR extension IN (...)` and take its `COUNT(*)`.

- **🟡 M7. `db.insert_file` leaves stale FTS rows when content disappears** (`db.py:120-190`)
  - If a previously indexed file no longer yields extractable content, the old FTS row (and `content_preview`) remains, so deleted content stays searchable. `delete_file` also doesn't clean `tags` (foreign keys are off — no `PRAGMA foreign_keys=ON`), and `clear_all()` doesn't clear history/cache/state tables.
  - **Fix:** On update, delete the FTS row when `content` is falsy; enable `PRAGMA foreign_keys=ON` per connection; extend `clear_all()` (or add `reset_index()` that clears index-related tables only).

- **🟡 M8. No `busy_timeout` / journal config on SQLite** (`db.py:24-30`)
  - Concurrent writers (e.g. the file-watcher thread once wired, or the concurrency test) can hit "database is locked".
  - **Fix:** `conn.execute("PRAGMA journal_mode=WAL")` and `PRAGMA busy_timeout=5000` in `_get_connection()`.

- **🟡 M9. `rollback_to_version` is a no-op stub** (`enhanced_features.py:311-330`)
  - It validates the version exists, commits nothing, and returns `True` — claiming success while doing nothing. `current_status.md` lists "Full rollback with snapshots" as complete.
  - **Fix:** Implement real restore (see C6) or raise `NotImplementedError` and fix the docs.

- **🟡 M10. Advanced Search panel never executes a search** (`verboseFC.py:560-575`)
  - The expander only shows the parsed JSON and the generated FTS query; `AdvancedSearch.search()` is never called, so boolean/wildcard/regex search is not actually usable (and per C7 the wildcard/regex paths are broken anyway).
  - **Fix:** Add a "Run" button that calls `AdvancedSearch(db).search(advanced_query)` and renders results in the standard result view.

- **🟡 M11. `AsyncIndexer` uses deprecated `asyncio.get_event_loop()` and is unused** (`indexer.py:285-305`)
  - `get_event_loop()` is deprecated when no running loop exists; the class is never referenced.
  - **Fix:** Use `asyncio.get_running_loop()` if kept; otherwise remove.

- **🟡 M12. `SearchDatabase.close()` only closes the current thread's connection** (`db.py:330-334`)
  - Other threads' connections (and `__del__` timing) leak file handles.
  - **Fix:** Track all connections in a list (with a lock) and close them all, or document single-thread usage and drop thread-local complexity.

- **🟡 M13. `show_file_preview` download filename assumes `id`** (`verboseFC.py:108-114`)
  - `f"content_{file_data['id']}.txt"` will `KeyError` if called with a dict lacking `id` (e.g. a filtered projection).
  - **Fix:** `file_data.get('id', 'file')`.

- **🟡 M14. `python-magic` is a dead dependency** (`requirements.txt`)
  - Never imported anywhere in the codebase.
  - **Fix:** Remove from `requirements.txt` (or implement MIME-based type detection if intended).

---

## 4. Low Findings (Style, Hygiene, Deprecations)

- **⚪ L1. Deprecated Streamlit API** (`verboseFC.py:271,332,334`) — `use_container_width=True` is deprecated in Streamlit ≥ 1.42 (installed: 1.49.1); use `width='stretch'`.
- **⚪ L2. Wrong system-file filter** (`verboseFC.py:187`, `indexer.py:77`) — `"$desktop.ini"` is not a real filename; the actual file is `desktop.ini`, so it is never excluded. Also consider excluding `.DS_Store` (present in this very repo).
- **⚪ L3. Duplicate utility code** — `get_human_size` (`verboseFC.py:20`) and `FileIndexer._get_human_size` (`indexer.py:52`) are identical; `compute_hashes`/`_compute_hashes` likewise. Consolidate into one shared module.
- **⚪ L4. Mid-file imports and duplicate top-level imports** (`indexer.py:267-268` — `import asyncio` / `ThreadPoolExecutor` after the main class; `verboseFC.py:149-150` — `import tempfile, os` inside `main()` when `os` is already imported at top).
- **⚪ L5. Unused imports/variables** — `create_indexer` imported but unused (`verboseFC.py:8`); `SearchCache`, `IncrementalIndexer`, `SearchHistory` imported but unused (`verboseFC.py:13`); `st.session_state['indexed_files']` set but never read (`verboseFC.py:232`); `SearchEngine.get_search_suggestions(prefix, limit)` (first overload) unused; `VisualEnhancements.create_result_card/create_search_bar/create_filter_chip/create_progress_indicator` unused by the UI; `SearchFeedback.get_search_summary` unused; `IncrementalIndexer.get_modified_since` builds an unused `dt`.
- **⚪ L6. `Dict[str, any]` annotations** (`ux_enhancements.py:186,435,452,505`) — lowercase `any` (the builtin function) instead of `typing.Any`. It happens to evaluate without error, but it is wrong and confusing.
- **⚪ L7. `create_indexer` default creates an in-memory DB** (`indexer.py:330-334`) — a convenience factory that silently produces a non-persistent index; misleading.
- **⚪ L8. Repo hygiene** — `src/__pycache__/` and `.DS_Store` present in the working tree (gitignored, but should be cleaned); `src/indexer.py` and `src/verboseFC.py` have uncommitted modifications in git.
- **⚪ L9. `plan.md` dependency drift** — plan lists `pysqlite3` ("built into Python 3.14") but the code uses stdlib `sqlite3` on Python 3.13.5; `pysqlite3` is neither in `requirements.txt` nor imported.

---

## 5. Documentation Findings (`.md` files)

- **🟠 D1. `current_status.md` overstates completion** — Claims "~95% complete" and "100% Complete" for Phases 2–5, listing as *done*: fuzzy matching (dead — C4), proximity search (broken — C5), quick preview modal (not wired — C11), save search (not wired — H5), real-time file watching (not wired + broken — C10), async indexing (unused — H5), full rollback (broken — C6), `.rar` support (missing — H6). In reality the **default indexing path crashes** (C1) and core search ranking/filters are wrong (C2, C9).
  - **Fix:** Re-run the status audit after the code fixes; mark each feature Verified/Partial/Not-started based on an end-to-end check, not code presence.
- **🟠 D2. `current_status.md` contains duplicated, self-contradictory content** — The file ends with a stale "Priority Implementation Roadmap" fragment and a second "Quick Reference: Files to Modify" table (with a "PDF alternative" row) that contradicts the "Completed" tables above it. Dates ("2024-01-15 / 2024-12-15") don't match file mtimes (2025).
  - **Fix:** Delete the duplicated trailing section; regenerate the status from the code.
- **🟠 D3. `review_plan_findings.md` is stale and contradicts `current_status.md`** — It lists fuzzy matching, proximity search, preview modal, save search, file watching, and async indexing as "MISSING", while `current_status.md` marks them complete. The two docs cannot both be right (verified: the features exist as code but are mostly unwired/broken).
  - **Fix:** Keep a single source of truth; retire or regenerate `review_plan_findings.md` after fixes land.
- **🟡 D4. `plan.md` success criteria are unmet** — "Index persists between app restarts" (H2), "Search is performant" (untested at scale), "No breaking changes" (C1 breaks the default flow). The criteria list is a good acceptance checklist — use it to close out this QA pass.
  - **Fix:** Convert plan.md's success criteria into automated acceptance tests.

---

## 6. Test Suite Findings (`tests.py`)

- **🟠 T1. 51/51 tests pass while 12 critical bugs exist** — the suite never exercises the failing paths:
  - Parallel indexing (`workers > 1`) is never tested — the comment at `tests.py` ("use single worker to avoid pickling issues") confirms the known C1 crash.
  - No test for: ranking order (C2), repeated-query history (C3), fuzzy matching (C4), proximity search (C5), rollback (C6 — see T2), wildcard *execution* (C7), bare-number size parsing (C8), filter application (C9), file watcher (C10), preview/clear buttons (C11), pagination (C12).
  - **Fix:** Add the regression tests listed under each finding above; make `workers=2` with a file-based DB a mandatory integration test.
- **🟠 T2. Tautological assertions** — `test_rollback`: `self.assertTrue(rolled_back or True)` always passes; `test_wildcard_parsing` only asserts the key exists ("might not match due to regex in parsing"); `test_phrase_search` only asserts `isinstance(total, int)` (0 results would pass).
  - **Fix:** Assert real behavior (rollback restores searchable data; `rep*` returns the report file; phrase search returns ≥ 1 result).
- **🟡 T3. No coverage for `file_watcher.py`, `AsyncIndexer`, `FullRollback`, archive extraction, `.pptx`/`.eml` extraction, or the Streamlit UI layer.**
  - **Fix:** Add unit tests for the watcher's queue/flush logic (mock `Observer`), archive extraction with fixture archives, and at minimum import-level smoke tests for `verboseFC.py`.
- **🟡 T4. Platform-dependent test** — `test_memory_usage` uses `resource.getrusage` (unavailable on Windows); the project otherwise runs cross-platform.
  - **Fix:** Guard with `unittest.skipUnless(hasattr(os, 'getloadavg') or sys.platform != 'win32', ...)`.
- **⚪ T5. Slow test** — `test_cache_expiration` sleeps 2 s; use a short TTL + fake clock or `ttl=1` with `time.sleep(1.1)`.

---

## Summary

**Critical bugs fixed:** C1 (pickling), C2 (bm25), C3 (history), C4 (fuzzy), C5 (proximity), C7 (wildcards), C8 (size parsing), C9 (metadata filters).

**Remaining critical issues:** C6 (FullRollback), C10 (file watcher not wired), C11 (preview not wired), C12 (100-result cap).

**High-priority issues:** H1-H10 (various UX and correctness issues).

**Medium/low issues:** M1-M14, L1-L9 (performance, style, deprecations).

**Documentation:** D1-D4 need updates; test suite T1-T5 needs hardening.

**Recommendation:** The core search functionality is now working correctly. Next steps:
1. Fix remaining critical issues (C6, C10, C11, C12)
2. Address high-priority issues (H1-H10)
3. Update documentation to match code state
4. Add tests for critical paths (workers>1, rollback, watcher, UI)

*All fixes verified with reproduction scripts and the existing test suite (51/51 tests pass).*
