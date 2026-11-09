# 06 — Testing, Environment & Known Quirks

## 1. Environment & running

- **Python:** `pyproject.toml` requires ≥3.13; `requirements.txt` pins
  streamlit ≥1.50 <2, pandas, plotly, watchdog, send2trash, and the document
  parsers (PyPDF2, python-docx, openpyxl, python-pptx, beautifulsoup4, py7zr).
- **Run:** `streamlit run src/verboseFC.py` (or `cd src && streamlit run
  verboseFC.py`). `.streamlit/config.toml`: `address="127.0.0.1"`,
  `headless=true`, usage stats off — the app is localhost-only with no auth;
  do not change the address without adding auth.
- **Entry point:** `main()` in `src/verboseFC.py` L315.
- **Index DB:** `~/.verbosefc/verbosefc_index.db` (Persistent Index on — default)
  or `<tempdir>/verboseFC_<pid>.db` (off). Deleting the file wipes the index;
  saved searches, schedules, ingestion rules and watch alerts live in the same
  file (but survive `clear_all()`).
- **Headless scheduled searches (no app running):**
  `*/5 * * * * cd ~/dev_archive/Verbose_fc/src && python scheduled_runner.py`
  — runs every due saved search against the persistent DB.

## 2. Test suite

All seven test files are **plain scripts, not pytest**: run `python test_<name>.py`
from `src/` (they rely on same-directory imports); success prints `OK` / check
lines, failures raise or `sys.exit(1)`. No test framework dependency.

| File | Covers |
|---|---|
| `test_walk.py` (37 L) | `ScanController` walk semantics: `.Trash`/`$RECYCLE.BIN` pruned; `max_depth=3` yields exactly `keep/ok.txt` + `keep/sub/deep.txt`; `max_depth=1` yields nothing for a no-root-files fixture. |
| `test_vpl.py` (166 L) | VPL parser/executor over shared fixture `make_db()` (L13–44, imported by other tests): `search` (phrase, `not`, OR), `where` on ext/size (`3mb`)/relative time (`7d`)/`contains`, `sort -size`, `head`, `stats count by ext`, `stats sum(size) as total`, `fields`, `date`/`file` aliases, `vpl_count`/`vpl_field_counts`/`vpl_timeline` (bucket width %60), `add_time_range`, `sourcetypes` (incl. scoped), `fieldsummary 3`, `index list`, and 7 `VplError` cases. |
| `test_ingestion.py` (160 L) | `IngestionStore` + VPL integration: derived defaults (`default` / category sourcetype); index rules (prefix+exts, dup rejection, `default` rejection); sourcetype matchers, priority tie → first-added wins; field extraction (first capture group wins; no-group and bad-name rejection; reserved name → `kv:size`); `stats count by index/sourcetype`; `fieldsummary` coverage; config commands (`index add/rm`, `sourcetype add`, `extract add`) + error cases; delete-rule re-derive; `index_stats`/`extracted_field_stats`. |
| `test_saved.py` (67 L) | `SavedSearchStore`/`time_range_bounds`: range mapping incl. custom `YYYY-MM-DD..YYYY-MM-DD`; save/list with invalid-VPL rejection at save time; `run_saved` count; `set_schedule` + `run_due` interval math; `recent_runs`/`unseen_runs`; broken query recorded in `scheduled_runs.error` instead of raising; `delete` cascades runs. |
| `test_gutter.py` (79 L) | Streamlit `AppTest` headless test of the search-bar line-number gutter in `search_page.render_search_tab` (absent at `q_area_h==44`, `<div class="vfc-gutter">1</div>` expanded, `1<br>2<br>3` for 3-line query, query text never mutated, disappears on collapse; `FakeDB`/`FakeStore`). |
| `test_integration.py` (121 L) | End-to-end smoke: `ScanController` + `FileIndexer.index_files(skip_unchanged=True)` single walk (4 files, one sha256 duplicate group, md5 disabled → empty); re-scan `skipped=4/indexed=0`; fast hashing (`fast_limit=1024`); cancel mid-scan → partial results + status `cancelled`; pause/resume completes 600 files; `add_alert`/`acknowledge_all_alerts`; `FolderWatcher` fires a `created` event; `backup_move` mirrors relative path and deletes the original. |
| `test_regressions.py` (171 L) | The 11/05 fixes, 7 numbered `check()`s (exit 1 on failure): numeric query can't match `file_id` (UNINDEXED); legacy FTS table rebuilt in place (rows kept, false positive gone); content size cap enforced (`content_available==0`, `content_skipped==1`); archive member count cap (`MAX_ARCHIVE_ENTRIES`) + per-member read cap (`ARCHIVE_MEMBER_READ_BYTES`); single-file text read bounded at `MAX_CONTENT_CHARS` even with `max_content_size=0`; `duplicate_mover_targets` refuses to move everything without a valid keeper; `get_all_files_meta` omits `content_preview`. |
Seven files total; the table above covers all of them.

When changing engine code, at minimum run `test_vpl.py`, `test_ingestion.py`,
`test_saved.py`, `test_regressions.py`, `test_integration.py` (the last touches
real filesystems in temp dirs and watchdog).

## 3. Known quirks, dead code & documented defects

Flag these when modifying code — several look like bugs but are current,
intended-to-survive behavior (regression-tested where noted).

### Engine semantics (intentional)
- Sourcetype `content_regex` matches the **`file_type` display string**, not file
  content (`ingestion.py` L313). Use `extract add` for content matching.
- Field-extraction rule's `sourcetype` filter compares against
  `files.file_category`, not the assigned sourcetype (`ingestion.py` L329).
- `SearchHistory.add(query_type=…)` writes into the `category` column
  (`enhanced_features.py` L62–67).
- `SchedulerThread.run` sleeps `interval` **twice** → ~40 s effective cadence
  (`saved_searches.py` L198–199).
- `fields` stage silently drops names that exist in `ctx.cols` but not in the
  result frame (e.g. `ext` vs `extension`) — `vpl_parser.py` L793.
- No WAL; all writers share `PRAGMA busy_timeout=10000` (`db.py` L71).
- `clear_all()` deliberately leaves `watch_alerts`, `saved_searches`,
  `scheduled_runs`, `ingest_*` untouched (`db.py` L552).

### Dead / unreachable code (candidates for cleanup, harmless today)
- `content_extractor.py`: BeautifulSoup branch for `.html/.htm/.xml` L196–209 —
  `TEXT_EXTENSIONS` (branch 1) returns first.
- `search_engine.py`: name collision `get_search_suggestions` L578 shadows the
  lazy accessor L137 → dynamic suggestion paths are dead (`TypeError` swallowed);
  `paths` collected in `parse_query` but never filtered on; fuzzy keywords added
  after `build_fts_query` (info-only); nested `SavedSearches` table name-collides
  with `saved_searches.py`'s table (unused by UI).
- `verboseFC.py`: `export_results_csv`/`export_results_json` (~L230–254) and
  `show_pdf_preview`/`show_file_preview` (~L171–189) never called.
- `AsyncIndexer.cancel_indexing` cancels a never-assigned attribute (no-op);
  `scan_runner.FULL/FAST/NONE` constants unused by the UI.
- `index_state` / `index_versions` / `search_cache` tables: created and cleared
  but never written.
- Nothing calls `SearchSuggestions.add_popular_term` → popular-terms table stays empty.

### UI-layer notes (see 02-ui-layer.md)
- Tab variables `main_tab5`/`main_tab6` are assigned in inverted order vs.
  display — cosmetic naming trap only.
- `_is_report(query)` checks *any* report stage, not the last stage.
- Duplicate `key="ev_csv"` in Database tab (harmless: single render path at a time).
- `_test_regex` uses private `db._get_connection()` (accept regex against the
  `file_type` string, matching the quirk above).
- Scan completion handler is guarded by ad-hoc `controller._handled` attribute
  (`verboseFC.py` ~L494).

## 4. Conventions observed in the codebase

- Schema ownership is centralized in `db.py::_initialize_schema`; module-local
  `_init_tables` only *extends* (ingestion columns, saved-search tables) and
  re-declares with `IF NOT EXISTS` so import order never matters.
- Long-running work always goes through events (`threading.Event`) +
  lock-protected snapshots polled by the UI; never block the ScriptRunner thread.
- UI never mutates file contents; destructive actions are gated by confirm
  checkbox + keeper guard + trash/backup semantics (05-services.md).
- All VPL failures funnel through `VplError` with user-facing messages.
