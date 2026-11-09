# 05 — Support Services

Files: `src/scan_runner.py` (282 L), `src/watcher.py` (101 L),
`src/saved_searches.py` (202 L), `src/scheduled_runner.py` (30 L),
`src/file_actions.py` (105 L), `src/enhanced_features.py` (139 L),
`src/ux_enhancements.py` (384 L), `src/db_stats.py` (141 L).

## `scan_runner.py` — background scan

- Module constants: `SKIP_NAMES = {"thumbs.db","$desktop.ini",".ds_store"}` L15;
  `SKIP_DIRS = {".trash","$recycle.bin","system volume information"}` L17 — never
  descended (trash can be network-backed and hang). Hash-mode constants
  `FULL/FAST/NONE` L20–22 are **unused by the UI** — the app passes explicit
  `{'md5': bool, 'sha256': bool, 'fast_limit': int|None}` with `fast_limit =
  1024*1024` (`verboseFC.py` L35, L439).
- `get_human_size(size)` L25.
- `compute_hashes(file_path, md5=True, sha256=True, fast_limit=None)` L34–70:
  4096-byte chunks, prefix-limited when `fast_limit` set, `""` for disabled hashes,
  `"ERROR"` on read failure.
- `scan_worker(file_path, hash_opts)` L73–97: process-pool-safe; `stat()` + hashes;
  returns a plain dict `{name, full_path, size_bytes, size_human,
  modified_at_epoch, md5, sha256}` (plain dict avoids pickling issues); `None` on error.

### `ScanController` L100
- State L106–119: `threading.Event` `_pause`/`_cancel`, `threading.Lock`,
  `thread`, `file_list`, `results`, `index_stats`, `error`,
  `status ∈ idle|running|paused|cancelling|cancelled|done|error` L115,
  `phase ∈ walk|hash|index` L116, `done`, `total`, `current`.
- `start(root, max_depth, workers, hash_opts, index_fn=None)` L123 — resets state,
  launches a **daemon** thread running `_run`.
- `pause()` L144 sets `_pause` (status→`paused`); `resume()` L150 clears it;
  `cancel()` L156 sets `_cancel` **and clears `_pause`** so a paused loop notices;
  `is_active()` L163 = running/paused/cancelling; `snapshot()` L166 returns
  lock-guarded `{"status","phase","done","total","current"}` — the UI polls this.
- `_set(**kwargs)` L177 lock-guarded writes; `_stopped()` L182;
  `_wait_if_paused()` L185 0.25 s sleep loop that also exits on cancel.

### `_run` L191–282 — three phases in one thread
1. **walk** L197–216: `os.walk`; per-directory cancel/pause checks; depth via
   `Path(dirpath).relative_to(root)`; prune
   `dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and dir_depth+1 < max_depth]`
   (L209–210); collect files while `dir_depth+1 <= max_depth` (L211), skipping
   `SKIP_NAMES`. **The list is reused by hashing and indexing — one walk per scan.**
2. **hash** L220–263: `ProcessPoolExecutor(max_workers=max(1, workers))` L222 with
   `batch_size = max(32, workers*32)` L221; pause/cancel per batch; on cancel
   `executor.shutdown(wait=False, cancel_futures=True)` and status/phase `cancelled`
   (L231–234). If hashing is fully disabled (L241–263) files are stat'ed serially with
   empty hash strings.
3. **index** L266–273: `phase="index"`, then `index_fn(self.file_list,
   progress_callback=…, should_stop=self._stopped, is_paused=…)` — the app wires this
   to `FileIndexer.index_files`. Finish: `cancelled` or `done`; exceptions store
   `traceback.format_exc()` in `self.error`, status/phase `error` (L279–282).

**Threading model:** one controller thread + a **process** pool for hashing +
(inside `index_fn`) a thread pool for extraction/DB writes. All UI-visible state is
lock-protected and read via `snapshot()`.

## `watcher.py` — real-time folder monitoring

- `_EventHandler(FileSystemEventHandler)` L16–43: `_emit(event_type, path)` L23
  resolves `name = Path(path).name` and calls
  `on_event(event_type, path, name, watch_path)`. `on_created/on_modified/on_deleted`
  emit only for **non-directories** (L29–39); `on_moved` emits `moved` with
  `"{src_path} -> {dest_path}"` as the path (L41–43). Alert types are exactly
  `created | modified | deleted | moved`.
- `FolderWatcher` L46: `__init__(on_event)` L52–58 creates
  `watchdog.observers.Observer`, daemon, **started immediately**;
  `watches: Dict[id → {'path','wd'}]` under `_lock`.
  `watch(path)` L60–72: `expanduser().resolve()`, `ValueError` unless existing dir,
  dedupes by resolved path, schedules `recursive=True`, id = `uuid4().hex[:8]`.
  `unwatch` L74–84 (swallows errors), `watched_paths()` L86, `stop()` L90–98
  (`observer.stop(); join(timeout=3)`), `is_alive()` L100.
- **Persistence:** none in this file. The app keeps the folder list in
  `st.session_state['watch_paths']` and the callback writes alerts through
  `db.add_alert(...)` into `watch_alerts` (capped at 5000 rows by `add_alert`);
  write failures accumulate in `db_holder['watch_errors']` (cap 20).

## `saved_searches.py` — saved & scheduled searches

- `TIME_RANGES` L16–19: `Any time, Last hour, Last 24 hours, Last 7 days, Today,
  Yesterday, Last 30 days, Last 90 days, This year`.
- `time_range_bounds(choice, now=None) -> (min_epoch, max_epoch|None)` L22–57:
  `Any time`/empty → `(None, None)`; custom `YYYY-MM-DD..YYYY-MM-DD` → day start →
  next-day start clamped to `now`; relative labels → `now − N·86400` (hour=3600);
  `Today` = midnight→now; `Yesterday` = previous midnight band;
  `This year` = Jan 1→now; unknown label → `(None, None)`.

### `SavedSearchStore` L60 — tables in the index DB (`_init_tables` L67–90)
- `saved_searches`: `id` PK autoinc, `name NOT NULL`, `query NOT NULL`,
  `time_range TEXT DEFAULT 'Any time'`, `schedule_minutes INTEGER` (NULL = unscheduled),
  `created_at`, `last_run_at REAL`.
- `scheduled_runs`: `id` PK autoinc, `saved_id`, `run_at REAL`, `result_count`,
  `top_rows TEXT` (JSON), `error TEXT`. (No FK/indexes.)

Methods:
- `save(name, query, time_range='Any time', schedule_minutes=None)` L94 — both
  required; **`parse_vpl(query)` validates the pipeline at save time** (L99).
- `delete(saved_id)` L108 (runs first, then row); `set_schedule` L114; `list()` L120;
  `recent_runs(limit=25)` L125 (joins name, `ORDER BY r.id DESC`);
  `unseen_runs(since_id=0)` L132 (drives both badges).
- `run_saved(saved)` L139–152: `parse_vpl` → `add_time_range(stages, lo, hi)` →
  `run_vpl(self.db, stages)` → `{'result_count': res.total, 'top_rows': JSON of
  df.head(10) restricted to ('name','size_human','modified_at')}`; on any exception
  returns `{'result_count': None, 'error': str(e)}` — **failures are stored, not raised**.
- `record_run(saved_id, outcome)` L154–163 inserts the run + updates `last_run_at`.
- `run_due(now=None)` L165–176: runs each row where
  `schedule_minutes IS NOT NULL AND (last_run_at IS NULL OR last_run_at <= now − schedule_minutes*60)`; returns count.

`SchedulerThread(threading.Thread)` L179–202: daemon, name `verbosefc-scheduler`,
`interval=20.0`; `run()` L189 loops `store.run_due()`, records/clears `last_error`
(retried next tick), then `_stop.wait(interval)` **twice** (L198–199) →
**effective ~40 s cadence**. `stop()` L201. Started lazily by
`search_page._ensure_scheduler`, which surfaces `last_error` as a UI warning.

## `scheduled_runner.py` — headless cron entry

`PERSISTENT_DB_PATH = Path.home()/".verbosefc"/"verbosefc_index.db"` L15;
`main()` L18–26: optional `sys.argv[1]` = alternate DB path; prints
`index not found: <path>` + exit 1 if absent; else
`SavedSearchStore(SearchDatabase(db_path)).run_due()` and prints
`ran N due scheduled search(es) against <path>`.
Docstring cron line: `*/5 * * * * cd ~/dev_archive/Verbose_fc/src && python scheduled_runner.py`.

## `file_actions.py` — OS interactions

- `_system()` L16 = `platform.system()`.
- `duplicate_mover_targets(group_paths, keeper_path) -> (targets, skip_reason)`
  L20–35: keeper falsy → `([], "no valid kept copy selected - reopen the group and
  choose one")`; keeper not in group → `([], "kept copy is no longer listed")`;
  else all paths except the keeper. Rationale (L23–27): a stale UI selection would
  otherwise turn "keep one" into "keep none".
- `open_path` L38–46: Windows `os.startfile`; macOS `open`; Linux `xdg-open`.
- `reveal_path` L49–58: Windows `explorer /select,`; macOS `open -R`; Linux
  `xdg-open <dirname>` (no universal select).
- `move_to_trash` L61–71: lazy `send2trash` import; missing → `"SKIP (missing): <path>"`.
- `backup_move(file_path, backup_root, scan_root=None)` L74–105: `mkdir(parents=True)`;
  **path preservation** L87–93 — `rel = src.relative_to(scan_root)` when given,
  else bare filename; collision → `dest.with_name(f"{stem}.{YYYYmmdd-HHMMSS}{suffix}.bak")`
  (L99–101); `shutil.copy2(src, dest)` **then** `os.remove(src)` (copy-then-delete,
  not `shutil.move`, preserving mtime/attrs).

## `enhanced_features.py` — `SearchHistory`

Docstring L1–7: Phase-3 drafts (`AdvancedSearch`, `SearchCache`,
`IncrementalIndexer`, `FullRollback`) were deleted 11/05; their tables remain in `db.py`.
`SearchHistory` L15–134: `__init__(db, max_history=50)` L20;
`_create_history_table()` L32–49 re-declares `search_history` +
`idx_history_timestamp` identical to `db.py`.
- `add(query, results_count=0, query_type='fts')` L51–76: `query_hash =
  md5(query)`, `INSERT … ON CONFLICT(query_hash) DO UPDATE SET timestamp,
  result_count` (repeat searches move to "recent" instead of duplicating);
  ⚠️ the `query_type` argument is written into the **`category`** column (L62–67);
  then prunes to newest `max_history` rows (L70–74).
- `get_recent(limit=10)` L78, `get_all()` L98, `clear()` L114, `delete_by_id` L120.
  Factory `create_search_history(db, max_history=50)` L137.

## `ux_enhancements.py` — suggestions & feedback

Docstring L1–6: `VisualEnhancements` removed 11/05.
- `SearchSuggestions` L13–205: `_create_suggestions_table()` L32–49 re-declares
  `search_suggestions` + `idx_suggestions_term`.
  `get_from_indexed_content(prefix, limit=10)` L51–100: two `LIKE %prefix%` queries —
  `DISTINCT name` (tagged `filename`/`content`) and `DISTINCT content_preview`
  (`preview`/`content`) — merged, deduped, sliced.
  `get_from_popular_queries` L102–132 (DISTINCT query from `search_history`).
  `get_suggestions(prefix)` L134–156 merges content+history, deduped by `text`.
  `add_popular_term(term, category='general')` L158–184 upserts
  (`count = count + 1` else insert `suggestion_type='popular'`);
  `get_popular_terms(limit=10)` L186–205. Tables touched: `files`,
  `search_history`, `search_suggestions`. ⚠️ Nothing in the shipped UI calls
  `add_popular_term`, so the popular-terms table stays empty.
- `SearchFeedback` L208–384 (presentation only, writes no tables):
  `format_feedback(query, results_count, execution_time, parsed_info=None)` L222–281
  → dict `{query, results_count, execution_time_ms, executed_at: None, suggestions,
  time_label, time_color, result_label, result_color}`; timing bands `<0.1s` "Very
  fast", `<1.0s` "Fast", `<2.0s` "Moderate", else "Slow" (L248–259); result bands
  `0` "No results found", `<10` "Low results", `<100` "Good results", else "Many
  results" (L262–273); zero results → `_get_refinement_suggestions` L283–301,
  `>50` → `_get_narrowing_suggestions` L303–320; `_parse_query` L322–345;
  `get_search_summary(...)` L347–384.

## `db_stats.py` — Database tab stats

`_safe_query(conn, sql, params)` L16–22 → rows or `None` on `sqlite3.Error`
(older index files may lack tables). `collect_db_stats(db_path)` L25–141:
- Seeds `{'path': db_path}`; `{}` if the file doesn't exist (L32–33).
- File facts without SQL: `file_bytes`, `mtime_epoch`, `sidecar_bytes` for
  `-wal`, `-shm`, `-journal` (L36–44).
- Read-only connection `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
  timeout=10)` L46; first `PRAGMA page_size` probe (L51) trips the "unreadable"
  path for a corrupt file (`{}` via L132–135).
- Page math: `page_size`, `page_count`, `free_pages` (freelist_count),
  `used_bytes = (page_count − free_pages) * page_size` (L54–60).
- **dbstat** eponym: `SELECT name, SUM(pgsize) FROM dbstat GROUP BY name …` →
  `per_object`; `None` when the build lacks dbstat (L63–66);
  `fts_bytes` = sum of rows named `content_*` (FTS shadow tables) (L79–85).
- `row_counts` per non-`sqlite_%` table (L69–77); `journal_mode`, `integrity`
  (`PRAGMA quick_check`) (L88–89).
- Series: `files_by_month`, `searches_by_day` (`substr(timestamp,1,10)`),
  `top_queries` (query, COUNT, AVG(result_count) LIMIT 10) L92–103;
  `alerts_by_type`, `alerts_by_day` L106–111; `file_activity_by_day` (last 90 days)
  L114–117; `by_category` (top 12), `by_extension` (top 15) L120–126;
  `content_coverage = {str(content_available): count}` L129.
- Series keys default to `[]` when their query fails; connection closed in `finally`
  (L136–140).

## Cross-cutting facts

- `get_database()` is a factory, not a singleton; the singleton effect is app-level
  (`db_holder`/`st.session_state['db']` over `PERSISTENT_DB_PATH`).
- WAL is intentionally off; writers rely on `PRAGMA busy_timeout=10000`, and
  `reindex_all` commits every 500 rows to avoid holding the write lock.
- `SearchDatabase.clear_all()` does **not** clear `watch_alerts`, `saved_searches`,
  `scheduled_runs`, or `ingest_*`.
- Reserved-but-unused tables (`index_state`, `index_versions`, `search_cache`) are
  still created, still listed by `db_stats.row_counts`, and still emptied by `clear_all`.
