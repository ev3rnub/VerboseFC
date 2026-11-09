# 01 — Architecture Overview

Verbose FC is a single-user, local-first file indexing and search tool:
**Streamlit UI → SQLite (FTS5) index → filesystem**. No server tier, no auth,
no network exposure by default (`.streamlit/config.toml` binds `127.0.0.1`).

## Module map

```
src/
├── verboseFC.py          # App shell: sidebar, scan control, tabs 1/4/5/6 (~1250 L)
├── search_page.py        # Search tab UI (VPL front-end)
├── ingestion_page.py     # Ingestion tab UI
├── vpl_parser.py         # VPL language: parser + SQL translation + aggregation helpers
├── search_engine.py      # Natural-language keyword engine (history, fuzzy, metadata filters)
├── db.py                 # SearchDatabase: schema, FTS5, alerts, upsert — single schema owner
├── indexer.py            # FileIndexer: extract + hash + upsert pipeline (thread pool)
├── content_extractor.py  # ContentExtractor: text extraction + categorization
├── ingestion.py          # IngestionStore: indexes / sourcetypes / field extractions
├── scan_runner.py        # ScanController: walk + hash (process pool) orchestration
├── watcher.py            # FolderWatcher: watchdog wrapper
├── saved_searches.py     # SavedSearchStore + SchedulerThread + time-range helpers
├── scheduled_runner.py   # cron/launchd entry point (headless run_due)
├── file_actions.py       # open/reveal/trash/backup-move (OS-specific)
├── enhanced_features.py  # SearchHistory
├── ux_enhancements.py    # SearchSuggestions, SearchFeedback
├── db_stats.py           # collect_db_stats for the Database tab
└── test_*.py             # plain-script test suites (see 06-testing-dev.md)
```

Detailed references: [02 UI layer](02-ui-layer.md) ·
[03 Search & VPL](03-search-vpl.md) · [04 Data layer](04-data-layer.md) ·
[05 Services](05-services.md).

## Runtime model

- **Process:** one Streamlit process. Every UI interaction re-runs `main()`
  top-to-bottom; `st.session_state` holds the long-lived objects (DB handle,
  indexer, `ScanController`, `FolderWatcher`, stores, dataframes).
- **Threads:**
  - Streamlit ScriptRunner thread runs `main()` (the whole UI).
  - `ScanController` starts one daemon worker thread (walk → hash → index);
    hashing fans out to a **ProcessPoolExecutor** (`scan_worker` returns picklable
    dicts); indexing fans out to a `ThreadPoolExecutor` inside
    `FileIndexer.index_files`.
  - `watchdog` `Observer` thread delivers FS events → `db.add_alert` writes.
  - `SchedulerThread` daemon (`verbosefc-scheduler`) runs due saved searches
    every ~40 s while the app is open.
  - SQLite connections are `threading.local()` — each thread gets its own.
- **Concurrency safety:** no WAL (rollback journal + `busy_timeout=10000`);
  `reindex_all` commits every 500 rows so other writers aren't starved; the scan
  completion handler is guarded by the ad-hoc `controller._handled` flag.

## The pipeline, end to end

```
Sidebar: Base Path + options
   └─ ScanController.start(root, depth, workers, hash_opts, index_fn)
        1. walk  : os.walk, depth + SKIP_DIRS/SKIP_NAMES pruning → file_list (reused!)
        2. hash  : scan_worker per file (md5/sha256, optional 1 MB fast prefix) → results
        3. index : FileIndexer.index_files(file_list, skip_unchanged=persistent)
             per file: stat → hashes (again, see note) → categorize →
             ContentExtractor.extract_content (caps: 10 MB file / 2 MB read / 500k chars)
             → db.insert_file (files upsert + FTS5 content row)
   └─ UI states: scan_results DataFrame, scan_root, index_stats
        ├─ File Explorer / Storage read the DataFrame
        └─ Search / Ingestion / Watch read the DB
```

Note: hashing happens in **both** the scan phase (for the duplicates DataFrame) and
the indexer phase (stored per row). The single *walk* is the optimization —
`index_files` reuses `file_list` rather than re-walking.

- **Skip-unchanged:** with Persistent Index on, `index_files` drops files whose
  stored `modified_at_epoch` matches the current mtime within 1e-6 s.
- **Persistent DB:** `~/.verbosefc/verbosefc_index.db` (Windows
  `%USERPROFILE%\.verbosefc\verbosefc_index.db`); temp mode uses
  `<tempdir>/verboseFC_<pid>.db`. The DB survives restarts; on open the app
  hydrates the File Explorer from `db.get_all_files_meta()`.

## Two query engines

1. **VPL (`vpl_parser.py`)** — the Search tab runs *everything* through it. Plain
   keywords are rewritten by `search_page._to_pipeline` into `search <terms> | …`.
   Stages become SQL over `files JOIN content` (FTS5 MATCH + bm25 ordering);
   config stages (`index add`, `extract add`, …) mutate `IngestionStore` rules and
   re-derive rows in place. Aggregation helpers (`vpl_count`, `vpl_field_counts`,
   `vpl_timeline`) power the sidebar/timeline/statistics.
2. **Keyword engine (`search_engine.py`)** — `parse_query` understands quotes,
   AND/OR/NOT, date words, size words, type aliases; applies metadata filters in
   Python over FTS rows, falls back to metadata-only search; owns history,
   suggestions and feedback plumbing. The live Search tab calls it mainly for
   history; the VPL path bypasses its parser. (Several of its fuzzy/suggestion
   features are dead code — see 03 and 06.)

## Data ownership

| Owner | Objects |
|---|---|
| `db.py` | `files`, FTS5 `content`, `tags`, `search_history`, reserved `index_state/index_versions/search_cache`, `search_suggestions`, `watch_alerts` + indexes; FTS `file_id UNINDEXED` migration |
| `ingestion.py` | `ALTER TABLE files ADD COLUMN index_name/sourcetype/extracted_json`, `ingest_indexes`, `ingest_sourcetypes`, `ingest_fields`, `idx_files_index/styp` |
| `saved_searches.py` | `saved_searches`, `scheduled_runs` |
| `enhanced_features.py` | re-declares `search_history` (identical to `db.py`) |

All schemas are `IF NOT EXISTS`; opening any store against an old DB upgrades it.

## Key invariants & boundaries

- The app never edits file *contents* — the only filesystem mutations are
  duplicate moves (`move_to_trash`, `backup_move`, always behind a confirmation
  checkbox + keeper guard) and nothing else.
- `sourcetype add content="regex"` matches the `file_type` display string, not file
  content (vpl/ingestion quirk); `extract add` is the content-based rule.
- Ingestion rules never re-scan the disk; they rewrite derived columns in place.
- Folder picker, Open, and Reveal all act on the **machine running Streamlit**,
  via subprocesses (tkinter picker runs in its own process to dodge macOS
  main-thread Tk rules).
- Watch alerts cap at 5000 rows; history at 50; scheduled runs inbox is unbounded
  but read with limit 25.

## Failure & safety behavior

- Scan errors are collected (`error_samples`, ≤5) and surfaced after the scan;
  a crashed scan stores the traceback in `controller.error`.
- Cancellation keeps partial results; the UI marks `cancelled`.
- DB corruption: `db_stats` returns `{}` → Database tab distinguishes missing vs
  unreadable file; `PRAGMA quick_check` shown as Integrity.
- VPL parse errors are always `VplError` with actionable messages; the UI shows
  `VPL: <message>` and clears stale results.
