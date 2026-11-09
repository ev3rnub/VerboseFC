# 02 — UI Layer

Files: `src/verboseFC.py` (1250 L), `src/search_page.py` (587 L), `src/ingestion_page.py` (286 L).
All Streamlit; remember that the whole script re-runs on every interaction and
`st.session_state` is the only persistent in-process state.

## 1. `verboseFC.py` — app shell, sidebar, scan control, File Explorer / Storage / Database / Watch tabs

### Constants (L33–43)
- `PERSISTENT_DB_PATH = Path.home()/".verbosefc"/"verbosefc_index.db"`
- `FAST_HASH_LIMIT = 1024*1024` (1 MB)
- `MAX_PDF_PREVIEW_BYTES = 20*1024*1024`
- `DUP_GROUPS_SHOWN = 100`, `DISKMAP_MAX_ROWS = 2000`

### Function index
`_dup_group_key` L45 · `_clear_duplicate_keepers` L57 · `_scan_is_running` L64 ·
`render_disk_map` L72 · `export_results_csv` L172 · `export_results_json` L177 ·
`show_pdf_preview` L182 · `show_file_preview` L203 · `get_resolved_path` L250 ·
`_PICKER_SNIPPET` L261 · `pick_folder_dialog` L278 · `main` L315 (entry at L1249).
`export_results_csv/_json` and both preview helpers are **dead code** (never called
by the live UI; CSV export happens inline in `search_page.py` / File List).

### Sidebar (L319–383, header "Settings")
| Widget | Label / key | Default | Notes |
|---|---|---|---|
| text_input | "Base Path" | `""` (via `picked_path` state) | **no `key=`, no default path on purpose** — a `~` prefill once caused whole-home scans. Value is deliberately not `.strip()`ed (L352) |
| button | "📁 Browse…" `base_browse` | — | calls `pick_folder_dialog`; on success sets `picked_path` + `st.rerun()`; on failure with empty state shows caption "Folder picker unavailable (no desktop session) — type the path instead." |
| slider | "Max Recursion Depth" 1–10 | 2 | |
| slider | "Parallel Workers" 1–16 | 4 | |
| checkbox | "Enable Full Indexing" | True | off ⇒ indexer/search-engine locals stay `None` |
| checkbox | "Persistent Index" | True | chooses DB path (below) |
| expander | "🔐 Hashing Options" | closed | `Compute MD5` True, `Compute SHA256` True, `Fast hashing (first 1 MB only)` False |
| button | "Scan & Index Directory" (primary) | — | `disabled=_scan_is_running()` |

`get_resolved_path(p)` (L250): `""`→cwd; `expanduser`+`expandvars`; `.resolve()`.

Folder picker (`pick_folder_dialog` L278): runs `_PICKER_SNIPPET` (L261 —
self-contained `tkinter.filedialog.askdirectory`, `root.withdraw()`, `-topmost`) in a
**subprocess** (`sys.executable -c … timeout=300`). Reason: Tk windows must be on the
main thread; Streamlit's ScriptRunner thread aborts on macOS otherwise. Returns
`None` on any failure; UI then keeps the text input.

### Database handle & service construction (L386–420)
- persistent → mkdir + `PERSISTENT_DB_PATH`; off → `tempfile.gettempdir()/verboseFC_<pid>.db`.
  The toggle is evaluated **per render**; flipping it swaps the DB handle.
- `db_holder = st.session_state.setdefault('db_holder', {})` (L399); rebuilt only when
  `session_state['db_path'] != db_path`; the old `SearchDatabase` is left to
  refcounting so an in-flight scan thread isn't cut off. `session_state['db'] = db` L405.
- `indexer_key = (db_path, hash_md5, hash_sha256, fast_hash)` (L407); on change pops
  `indexer` + `search_engine`. `FileIndexer(db, ContentExtractor(), hash_*=…, fast_hash_mb=…)`
  and `create_search_engine(db)` are rebuilt together (L415–420).

### Scan start (L428–456)
Validations: path must exist; at least one hash or indexing enabled. Then
`ScanController().start(root, max_depth, workers, hash_opts, index_fn)` where
`index_fn` (L443) wraps `indexer.index_files(files, workers, progress_callback,
should_stop, is_paused, skip_unchanged=persistent_index)` — the walk list is reused
(single directory walk per scan). State: `scan_controller`, `fast_hash_used`.

### Scan progress UI (L458–483)
`snap = controller.snapshot()` → `{phase, status, done, total, current}`.
Phase labels 🚶 Walking / 🔐 Hashing / 📚 Indexing; status running/PAUSED/cancelling.
`st.progress(done/total)`; buttons **⏸ Pause** / **▶️ Resume** / **🛑 Cancel** with
disabled rules per status; unconditional `time.sleep(0.4); st.rerun()` → ~2.5 reruns/s poll.

### Scan completion (L485–516)
Guarded by ad-hoc attribute `controller._handled` (L487). Writes
`session_state['scan_results'] = pd.DataFrame(controller.results)`, `scan_root`,
`index_stats`; calls `_clear_duplicate_keepers()`. Success line reports files,
indexed count, content-extracted count, unchanged-skipped; `content_skipped` and
`errors` become info/warning lines. `cancelled` → "Partial results kept."

### Main tabs (L520–535)
`st.tabs(["📁 File Explorer", search_label, "📥 Ingestion", "📊 Storage", "🗄️ Database", alert_label])`
unpacked into `main_tab1, main_tab2, main_tab3, main_tab4, main_tab6, main_tab5` —
**variable names main_tab5/main_tab6 are inverted vs. display position** (6th tab is
Watch in `main_tab5`, 5th is Database in `main_tab6`).
Badges: Watch tab label shows unacknowledged alert count (`db.get_unacknowledged_alert_count()`);
Search tab label shows unseen scheduled runs (`saved_store.unseen_runs(sched_seen_id)`).
`SavedSearchStore` / `IngestionStore` are rebuilt only when `store_db_path != db_path`.

### Tab 1 — File Explorer (L538–799)
Cold-start hydration (L539–548): no `scan_results` but DB has files → load
`db.get_all_files_meta()`; `scan_root = os.path.commonpath(...)` of all paths
(`ValueError` on mixed Windows drives → skip).
Sub-tabs `["📄 File List", "👯 Duplicates", "🌳 Tree View"]`.
- File List: `st.dataframe(df, width='stretch')` + "Download CSV Report";
  "🗂 File Actions" selectbox + **📂 Open file** / **📍 Reveal in Finder / Explorer**.
- Duplicates (L586–769): hash column preference **sha256 → md5** (first with any
  non-empty value). `fast_hash_used` ⇒ group key = `[hash_col, 'size_bytes']` and a
  false-positive caveat note. Action radio `["Move to Trash","Move to backup folder"]`
  + "Backup folder" text (default `~/verbosefc_backups`). Only the first 100 groups
  (`DUP_GROUPS_SHOWN`) are actionable.
  Per group (`st.expander`): per-copy table (path, size, truncated hash `h[:12]+'…'`,
  modified), `st.radio("Keep this copy", …, key=f"dupkeep_{gkey}")` — default keeper
  is the **oldest** (`modified_at_epoch.idxmin()`), `st.checkbox("Act on this group…",
  key=f"dupgrp_{gkey}")`. Group key `gkey = sha1('|'.join(sorted(paths)))[:12]`
  (content-based, survives list changes).
  Confirmation: `dup_confirm` checkbox + tally from populated `dupgrp_*` keys +
  **🚚 Apply to selected groups** (`dup_apply`). Nested `_apply_duplicate_groups()`
  L660: validates keeper against group labels *and* `df.index`;
  `duplicate_mover_targets(paths, keeper)` guards the keeper; moves via
  `move_to_trash` / `backup_move(path, backup_root, scan_root)`; removes moved rows
  by filtering (not dropping by label) to keep other groups' labels valid; report via
  `dup_apply_note` popped on next render.
- Collisions (L762): `groupby('name')[hash_col].nunique() > 1`.
- Tree View (L771): built from the in-memory `df['full_path']` (no second walk),
  `render_tree` L786 sorts dirs first, `st.code(..., height=600)`.

### Tab — Search (L802): delegates to `search_page.render_search_tab(db, search_engine, saved_store)`.
### Tab — Ingestion (L806): `render_ingestion_tab(db, ingest_store)`.

### Tab 4 — Storage (L811–872)
Source: `scan_results` else `db.get_all_files_meta()` (persistent mode). Sub-tabs
`["📈 Overview", "🗺️ Disk Map"]`. Overview: metrics Files / Total Size / Largest File /
Most Common Type; bar charts size & count by extension (top 15); top-10 largest
files; files-by-month-modified (last 24 months).
`render_disk_map` L72: sunburst from `session_state['diskmap_path']` (reset to
`scan_root` if stale). Slider "Rings to show" 1–4 (key `diskmap_depth`). Aggregation:
leaf tuples limited to depth with roll-up marker `'(deeper…)'` (Plotly requires
every row to be a leaf); leaf/dir name collisions renamed `… ·file` (L114); row cap
2000 with `(rest: N more)` tail bucket (L121); `px.sunburst(values='size_mb',
textinfo='label+percent parent', insidetextorientation='radial')` + custom
hovertemplate. Drill UI (chart clicks are not captured in vanilla Streamlit):
selectbox of top-30 children + **⬅ Up one level** + **🏠 Root**, each sets
`diskmap_path` + rerun.

### Tab 6 (var `main_tab5`) — Watch (L875–1008)
State: `watch_paths` list, `watcher` (FolderWatcher or None), `picked_watch`.
Row: "Folder to watch" text + **📁 Browse…** + **➕ Add folder** (validates dir,
dedupes, live `watcher.watch(p)`). **🟢 Start monitoring** builds
`FolderWatcher(on_event)`; `on_event(event_type, path, name, watch_path)` →
`db.add_alert(...)`; failures accumulate in `db_holder['watch_errors']` (cap 20,
surfaced as warning). **🔴 Stop monitoring**; per-folder **Remove** (`unwatch_{wp}`).
Alerts render in a nested `@st.fragment(run_every=3.0)` (`alerts_section` L983):
"Unacknowledged only" checkbox, `db.get_alerts(limit=200, include_acknowledged=…)`,
metrics, **✔ Acknowledge all**, **🧹 Clear all alerts**.

### Tab 5 (var `main_tab6`) — Database (L1011–1246)
`collect_db_stats(db_path)` from `db_stats.py` (see 05-services). Headline metrics
File size / Actual used / Free (reusable) / Integrity / Journal mode; row counts for
`files, content, search_history, watch_alerts`. Sub-tabs
`["🧿 Size & Composition", "📈 Activity", "🧱 Tables & Integrity"]`.
Local helpers `_consolidate_slices` L1048 (folds <1% donut slices into
`Other (N)`, always keeps ≥3 largest) and `_donut` L1069 (`px.pie(hole=0.45)`,
per-point `texttemplate`, black in-slice text; `textposition`/`textinfo` must be
trace attrs, not `px.pie` kwargs). Composition buckets FTS content index / Auto
indexes / Indexes from `per_object` (coarse fallback when `dbstat` module absent).
Activity: searches/day, most frequent queries (labels truncated 28 chars;
`orientation='h'` silently dropped in Plotly v6 — axis swap instead, L1181),
files modified last 90 days (area), alerts by type/day. Tables: row-count bar chart,
storage per table/index top-30 (horizontal via axis swap L1223), sidecar file sizes,
VACUUM hint when free space > 50 MB and rows exist, `st.error` when
`integrity != 'ok'`. All queries are read-only.

### Session-state keys (verboseFC.py)
`picked_path, scan_controller, fast_hash_used, db_holder, db_path, db, indexer_key,
indexer, search_engine, scan_results, scan_root, index_stats, diskmap_path,
diskmap_depth, diskmap_drill, dup_confirm, dup_apply, dup_apply_note,
dupkeep_<gkey>, dupgrp_<gkey>, saved_store, ingest_store, store_db_path,
sched_seen_id, watcher, watch_paths, picked_watch, alerts_only_pending`.

## 2. `search_page.py` — the Search tab

Constants: `VPL_STARTS` L28 (prefixes that mean "already a pipeline");
`SELECTED_FIELDS = ['ext','category','type','index','sourcetype']` L32;
`INTERESTING_FIELDS` L33; `SCHEDULE_CHOICES` L36
(`Off, Every 5/15/30 min, Hourly, Every 6 hours, Daily` → minutes); `_HOVER` L40.

- `_to_pipeline(q)` L44: if the first `|`-segment doesn't start with a
  `VPL_STARTS` command, prefix `search ` — plain keywords and drill-down pipelines
  share one engine path.
- `_ensure_scheduler(store)` L71: starts `SchedulerThread(store)` unless a live one
  is in `session_state['sched_thread']`; surfaces `th.last_error` as a warning.
- `_time_picker()` L82: `st.selectbox("Time range", TIME_RANGES + ["Custom range"],
  key="time_range_pick", label_visibility="collapsed")`. Custom → two `st.date_input`
  (`tr_from`, `tr_to`); label becomes `"YYYY-MM-DD..YYYY-MM-DD"`. Bounds come from
  `saved_searches.time_range_bounds(label)` → `(lo, hi)` epochs; `Any time` →
  `(None, None)`; relative labels subtract from `now`; `Today` = midnight→now;
  `Yesterday` = previous midnight band; custom range `hi` = end-date + 1 day clamped
  to `now`.
- Query bar CSS `_QUERY_CSS` L102 (injected L477): flex-row alignment of time picker /
  textarea / Search; monospace textarea `resize: vertical`; **line-number gutter**:
  rendered *server-side* when `q_area_h > 44` (`st.markdown('<div class="vfc-gutter">…')`
  with `1<br>2<br>…` for `q.count('\n')+2` lines, L490–494); CSS floats `.vfc-gutter`
  (36 px) over the textarea element and indents it `padding-left: 44px`. Display-only —
  never part of the query. (Streamlit strips `<script>`, so this must be static HTML.)
- `render_search_tab` L463–587 flow:
  1. `_ensure_scheduler`; closures `_submitted` (sets `_q_hit`), `_grow`/`_shrink`
     (`q_area_h ± 60`, clamped 44..320, default 44).
  2. Row `st.columns([1.15, 4.6, 0.34, 0.34, 0.95])`: time picker; textarea
     `st.text_area("Search", key="vpl_query", height=q_area_h, placeholder='report | where ext==pdf and size>1mb | sort -size | head 20   — or just type keywords')`;
     **▾** `q_grow` / **▴** `q_shrink`; **🔍 Search** (primary).
  3. Execute when `_q_hit` (set by callbacks: drill-down, saved run, history click)
     or Search button. `session_state['search_out'] = _execute(db, q, time_label, lo, hi)`;
     history recorded via `search_engine.get_search_history().add(q, total)` (swallowed).
     `VplError` → pop `search_out`, `st.error(f"VPL: {e}")`.
  4. Match bar: report → `**N** report rows in X.XXXs — time range: **label**`;
     file results → `**len(df)** of **total** files matched in X.XXXs — time range: …`.
  5. Timeline: `vpl_timeline(db, _base_stages(out['stages']))` → green `go.Bar`,
     height 140, `displayModeBar: False`.
  6. `_field_sidebar` | sub-tabs in `st.columns([1, 3.4])`.
  7. Footer `st.expander("📜 Recent searches")`: `get_recent(10)` → buttons that call
     `_load_saved(query, 'Any time')`.
- `_execute` L180 output dict (stored as `session_state['search_out']`):
  `{'df', 'total', 'exec', 'query', 'time_label', 'stages', 'terms'}` where
  `stages = add_time_range(parse_vpl(_to_pipeline(query), db), lo, hi)`.
  `_base_stages` L190 filters to `SearchStage|WhereStage` — used for counts,
  timeline, field counts, stats. `_highlight_terms` L54: words `[A-Za-z0-9']{3,}`
  minus VPL commands/stopwords (used to bold previews via `re.sub`).
- `_field_sidebar` L212: per `SELECTED_FIELDS`, `vpl_field_counts(db, stages, field,
  limit=10)` → buttons `"{value}  {n}"` (keys `drill_{field}_{value}`) whose callback
  `_append_to_query` (L195) appends ` | where {field}=="{value}"` and sets `_q_hit`.
  Then **INTERESTING FIELDS** as captions (no drill).
- `_is_report(out)` L235: True if **any** stage is
  `StatsStage|SourcetypesStage|FieldSummaryStage|IndexesStage|IndexAdd|IndexRm|SourcetypeAdd|ExtractAdd`
  (docstring says "ends in"; implementation checks *any* stage). Gates match-bar
  wording and Events layout.
- `_events_tab` L245: report → `st.dataframe` + CSV (`vpl_report.csv`). Files →
  pagination (`ev_per_page` 20/50/100, `ev_page`), CSV (`search_results.csv`;
  duplicate key `ev_csv` is harmless — branches are mutually exclusive), per row:
  modified timestamp caption, `**name** · size · ext · category`, path,
  `st.expander("▸")` with `st.json` subset + **📂 Open** / **📍 Reveal** /
  **🗑 Remove from results** (`ev_rm_{page}_{i}` mutates `search_out['df']` in place),
  content preview `[:600]` with terms bolded. Row id is positional (`page_i`) because
  names repeat and `id` can vanish after `| fields …`.
- `_stats_tab` L326: `vpl_count` + metrics; per `SELECTED_FIELDS`
  `vpl_field_counts(limit=20)` bar charts.
- `_viz_tab` L346: ext bar (15), category pie (10), `px.scatter(x='modified',
  y='size_bytes', color='file_category', log_y=True)` when columns present.
- `_scheduled_tab` L383: 💾 Save (`save_name`, `save_query` → `store.save(name,
  _to_pipeline(query), cur_tr)`); saved list expanders with Schedule selectbox
  (`sched_{id}` → `store.set_schedule`), **▶ Run now** (`_load_saved` sets
  `vpl_query`, `time_range_pick`, `_q_hit`), **🗑 Delete**; **SCHEDULED RUNS** table
  (`store.recent_runs(25)`, cols `run_at,name,result_count,error`), **✔ Mark as read**
  sets `sched_seen_id = max(r['id'])`; top-5 runs get a "Top matches" expander
  (JSON `top_rows`).

### Session-state keys (search_page.py)
`sched_thread, time_range_pick, tr_from, tr_to, q_area_h, vpl_query, _q_hit,
search_out, ev_per_page, ev_page, ev_csv, ev_open_/ev_reveal_/ev_rm_<page>_<i>,
drill_<field>_<value>, save_name, save_query, sched_<id>, run_<id>, del_<id>, hist_<id>`.

## 3. `ingestion_page.py` — the Ingestion tab

- `render_ingestion_tab` L268: `_msg()` pops `ingest_msg` (success if starts with
  `✅`, else error); pops `ingest_test_hits` → "🧪 Regex test matches" expander;
  **♻️ Apply & reindex all files** button (`ing_apply`) rendered **above** the
  sub-tabs; `st.tabs(["📥 Data Inputs", "🏷 Sourcetypes", "🧲 Field Extractions"])`.
- `_apply(store)` L25: progress bar + `store.reindex_all(progress=_progress)`;
  sets `ingest_msg = "✅ Reindexed {updated} file(s) — …"` and reruns. Declarative
  only — rewrites derived DB columns, never the filesystem.
- Data Inputs L58: table (`store.list_indexes()` + `index_stats()`), add form
  `ing_add_index` (`store.add_index(name, prefix, exts, desc)` then auto `_apply`),
  remove selectbox+button (`store.delete_index`), current assignment table, caption
  hints `index list · stats count by index · where index==projects`.
- Sourcetypes L114: same pattern; fields exts / path contains / content regex
  (against MIME type) / priority (`number_input 1–999, default 100`).
- Field Extractions L171: field name, optional sourcetype filter, regex needing one
  capture group; `store.add_field` + `_apply`.
- Regex tester `_test_regex` L226: validates (empty / `re.error` / <1 group), then
  **private API** `db._get_connection()` — `SELECT f.name, c.full_content FROM files f
  JOIN content c ON c.file_id = f.id LIMIT 2000`, first match per row, hits capped 15,
  each `{file, value=m.group(1), context=±30 chars}`; results via `ingest_test_hits`.
- `IngestionError` anywhere → `_err` writes `❌ {e}` to `ingest_msg` + rerun.

### Session-state keys (ingestion_page.py)
`ingest_msg, ingest_test_hits`; widget keys `ing_add_index, ing_rm_idx,
ing_rm_idx_btn, ing_add_styp, ing_rm_styp, ing_rm_styp_btn, ing_add_field,
ing_rm_field, ing_rm_field_btn, ing_test_re, ing_test_btn, ing_apply`.
