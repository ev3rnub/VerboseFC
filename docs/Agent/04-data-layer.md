# 04 — Data Layer: Schema, Indexing, Extraction, Ingestion

Files: `src/db.py` (657 L), `src/indexer.py` (400 L), `src/content_extractor.py` (338 L),
`src/ingestion.py` (379 L).

## 1. `db.py` — `SearchDatabase`

### Connection model
- `SearchDatabase` L13; `__init__(db_path=None)` L16 → `":memory:"` when no path;
  `threading.local()` L19.
- `_get_connection()` L21: lazily `sqlite3.connect(db_path)` **per thread**,
  `row_factory = sqlite3.Row`, runs `_initialize_schema()` once per thread.
  `_ensure_connection()` L238 is the same bootstrap for readers.
- `close()` L640 closes only the calling thread's connection; `__del__` L646 swallows errors.
- **Pragmas:** only `PRAGMA busy_timeout=10000` (L71). WAL is deliberately **not**
  enabled (comment L72–73: default rollback journal is used for thread safety).
  `db_stats.py` opens its own read-only connection (`timeout=10`).
- `get_database(db_path=None)` L655 is a **plain factory, not a singleton** — the app
  caches its own instance in `db_holder`/`st.session_state['db']`.

### Schema (`_initialize_schema` L65–236; all `IF NOT EXISTS`)

**`files`** (L76–97)
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
full_path TEXT UNIQUE NOT NULL,
size_bytes INTEGER NOT NULL, size_human TEXT NOT NULL,
md5 TEXT NOT NULL, sha256 TEXT NOT NULL,
extension TEXT NOT NULL, file_type TEXT NOT NULL, file_category TEXT NOT NULL,
created_at TEXT, modified_at TEXT, accessed_at TEXT,
content_available INTEGER DEFAULT 0, content_preview TEXT,
created_at_epoch REAL, modified_at_epoch REAL, accessed_at_epoch REAL
```
Plus columns added later by `ingestion.py`: `index_name`, `sourcetype`, `extracted_json` (see §4).

**`content`** — FTS5 virtual table (L101–108):
```sql
fts5(file_id UNINDEXED, preview, full_content, tokenize='porter unicode61')
```
`file_id` is UNINDEXED on purpose — while it was indexed, a numeric query like `12`
matched internal row ids (false positives).

**`tags`** (L115–124): `id` PK, `file_id` FK→files ON DELETE CASCADE, `tag`,
`created_at`, `UNIQUE(file_id, tag)`. Not used by the shipped UI.

**`search_history`** (L127–136): `id`, `query`, `timestamp`, `result_count`,
`category`, `query_hash UNIQUE`; index `idx_history_timestamp(timestamp DESC)`.

**Reserved (created, never written by the UI):** `index_state` (L145–153),
`index_versions` (L159–166), `search_cache` (L169–179) with indexes
`idx_state_hash`, `idx_cache_hash`, `idx_cache_expires`. Kept so old index files load
and `clear_all` has something to empty (comment L142–144).

**`search_suggestions`** (L190–199): `id`, `term`, `category`, `suggestion_type`,
`count`, `created_at`; index `idx_suggestions_term`.

**`watch_alerts`** (L206–216): `id`, `created_at`, `event_type`, `path`, `name`,
`watch_path`, `acknowledged INTEGER DEFAULT 0`; index `idx_alerts_created DESC`.

**File indexes:** `idx_files_path(full_path)`, `idx_files_extension(extension)`,
`idx_files_category(file_category)`, `idx_files_modified(modified_at_epoch)` (L223–232).

### FTS migration — `_migrate_fts_file_id(conn)` L31–63
Class lock `_fts_migration_lock` L29; called from `_initialize_schema` L112. If
`sqlite_master.sql` for `content` lacks `unindexed`: create `content_v2` with
`file_id UNINDEXED`, copy rows, `DROP TABLE content`, rename. `OperationalError` →
rollback (another connection already did it). Rows preserved; no-op afterwards.

### File upsert / CRUD
- `insert_file(file_data) -> int` L245: upsert on `full_path` — UPDATE all 16
  metadata columns (L259–297) or INSERT (L300–326); `content_available =
  1 if file_data.get('content')`; FTS branch L329–342 (UPDATE or INSERT into
  `content (file_id, preview, full_content)`); single `commit()` L344.
- `get_file_by_id` L347, `get_file_by_path` L358, `delete_file_by_path` L369 and
  `delete_file(file_id)` L537 (both delete FTS rows first), `clear_all()` L552.
- ⚠️ `clear_all()` clears `content, tags, files, search_history, index_state,
  index_versions, search_cache, search_suggestions` — it does **not** clear
  `watch_alerts`, `saved_searches`, `scheduled_runs`, or the `ingest_*` tables.

### Search / listing
- `search_files(query, limit=100, offset=0)` L392:
  `SELECT f.*, content.rank FROM files f JOIN content ON f.id = content.file_id
  WHERE content MATCH ? ORDER BY bm25(content) ASC LIMIT ? OFFSET ?` +
  separate `COUNT(*)` → `(results, total)`. Rows carry `rank`.
- `search_files_by_metadata(name, extension, file_category, min_size, max_size,
  min_modified, max_modified, limit, offset)` L421 — `name LIKE %..%`, equality on
  ext/category, ranges on size/mtime epoch, `ORDER BY modified_at_epoch DESC`.
- `get_all_files` L489; `get_all_files_meta()` L502 — explicit column list
  **omitting `content_preview`** (used to hydrate dashboard dataframes).
- `get_file_count()` L521; `get_content_count()` L529 (FTS rows).
- `get_indexed_mtime(full_path)` L630 → stored `modified_at_epoch` or None
  (powers skip-unchanged).

### Watch alerts
`add_alert(event_type, path, name, watch_path=None)` L571 — inserts
`datetime.now().isoformat(timespec='seconds')`, trims to newest **5000** rows
(L580–584). `get_alerts(limit=200, include_acknowledged=True)` L588,
`get_unacknowledged_alert_count` L599, `acknowledge_alert` L606,
`acknowledge_all_alerts` L614, `clear_alerts` L622.

### Duplicates
No duplicate queries in `db.py` — it only stores `md5`/`sha256`. The Duplicates tab
groups the scan **DataFrame** in `verboseFC.py` L587–600 (`sha256` preferred;
fast-hash mode adds `size_bytes` to the group key).

## 2. `indexer.py` — `FileIndexer` / `AsyncIndexer`

`FileIndexer` L15; `__init__(db, extractor=None, max_content_size=10*1024*1024,
hash_md5=True, hash_sha256=True, fast_hash_mb=0.0)` L18–36.
`_stats` keys L37–45: `total, indexed, skipped, errors, content_extracted,
content_skipped, error_samples`.

**Per-file pipeline** `_process_single_file` L129–181:
1. `_get_file_stats` L47 — `time.ctime` strings + raw epochs.
2. `_compute_hashes` L69–102 — 4096-byte chunks; disabled hash → `""`;
   `fast_hash_mb>0` reads only that prefix; any read exception → `"ERROR"` for
   enabled hashes.
3. `extractor.categorize_file` + `get_file_type_display` (L142–143).
4. Content gate `_should_extract_content` L117: skips ignored names
   (`_is_ignored_name` L104: `thumbs.db`, `$desktop.ini`) and oversized files
   (`_content_too_large` L108–115: `size_bytes > max_content_size`;
   `max_content_size = 0` disables the gate). Oversized files are indexed
   metadata-only and counted in `content_skipped` (L154–157).
5. `extractor.extract_content` L151 → returns the `file_data` dict L159–178.
   Failures → `_note_error` L123 (≤5 `error_samples` of `"name: ExcType: msg"`), return `None`.

**Batch API:**
- `index_file(path, callback=None)` L183.
- `index_directory(root, max_depth=2, workers=4, progress_callback=None)` L208 —
  `rglob("*")`, keep `is_file()` with `len(relative.parts) <= max_depth`.
- `index_files(paths, workers=4, progress_callback=None, should_stop=None,
  is_paused=None, skip_unchanged=False)` L247 — the one the app uses. Resets stats
  L271; **skip_unchanged** L282–293 drops paths where
  `abs(db.get_indexed_mtime(p) - p.stat().st_mtime) < 1e-6` (counts `skipped`);
  `ThreadPoolExecutor(max_workers=max(1, workers))` over batches of
  `max(16, workers*16)` (L308–309); pause polls `is_paused()` every 0.25 s (L303–305);
  `should_stop()` between batches L314; each future → `db.insert_file` + stat bumps;
  `progress_callback(i, total, str(path))` L334.
- `get_stats()` L338; `refresh_index` L342 = `db.clear_all()` + `index_directory`.

**`AsyncIndexer`** L354 — asyncio wrapper with its own `ThreadPoolExecutor(4)`;
each `index_directory_async`/`index_file_async` constructs a **new** `FileIndexer`;
`cancel_indexing()` L389 cancels `self._indexing_task`, which is never assigned
(dead code). Not used by the Streamlit app. `create_indexer(db_path=None, …)` L396 factory.

## 3. `content_extractor.py` — `ContentExtractor`

**Budgets (L15–19):** `MAX_CONTENT_CHARS = 500_000` stored text per file;
`MAX_READ_BYTES = 2_000_000` read from a plain text file;
`ARCHIVE_MEMBER_READ_BYTES = 4096` (1000 chars kept per member);
`MAX_ARCHIVE_ENTRIES = 50` members inspected per archive;
`ARCHIVE_TEXT_EXTS = ('.txt','.md','.csv','.json','.xml','.log','.yml','.yaml','.ini','.cfg')`.

**Categories `FILE_CATEGORIES` L26–34 (dict order = match priority):**
- `document`: .txt .md .csv .json .xml .log .rtf .html .htm .pdf .docx .xlsx .pptx .odt .ods .odp
- `image`: .jpg .jpeg .png .gif .bmp .tiff .tif .svg .webp .ico
- `video`: .mp4 .avi .mkv .mov .wmv .flv .webm .m4v
- `audio`: .mp3 .wav .flac .aac .ogg .wma .m4a
- `archive`: .zip .tar .gz .rar .7z .bz2 .xz .tgz
- `code`: .py .js .ts .java .cpp .c .h .hpp .cs .go .rs .php .rb .swift .kt .sql .sh .bash .yml .yaml .toml .ini .cfg .conf
- `data`: .db .sqlite .sqlite3 .mdb .accdb .parquet .hdf .h5
- anything else → `other`

`TEXT_EXTENSIONS` L37: .txt .md .csv .json .xml .log .html .htm plus the code list.

`extract_content(file_path) -> (full_content, preview)` L78–212, dispatch order:
1. `TEXT_EXTENSIONS` → `_get_text_from_file` L46–66 (bounded prefix read, strips
   UTF-8 BOM, `decode('utf-8', errors='replace')`, truncate at `MAX_CONTENT_CHARS`
   with `"\n\n[Content truncated - file too large]"`).
2. `.pdf` → PyPDF2, `page.extract_text()` joined by `\n` (L93–105).
3. `.docx` → python-docx paragraphs (L108–116).
4. `.xlsx` → openpyxl `read_only=True`, tab-joined rows, blocks `Sheet: <name>` (L119–134).
5. `.pptx` → python-pptx shapes, blocks `Slide N:` (L137–152).
6. archives `.zip/.tar/.gz/.7z/.rar` L155–164 — full-suffix `.tar.gz` handled;
   plain `.gz` intentionally skipped; `.rar` falls through (metadata only);
   others → `extract_archive_content` L214–311:
   `member_text` keeps only `ARCHIVE_MEMBER_EXTS` members, reads 4096 B each, emits
   `"{name}:\n" + decoded[:1000]`; zip via `infolist()` capped 50; tar via
   `tarfile.open`; 7z via optional `py7zr` (`ImportError` → None,None) reading at
   most 50 listed entries (deliberate — `readall()` would decompress everything).
7. `.eml` → stdlib `email`, `msg.walk()` keeping text/plain & text/html (L167–193).
8. `.html/.htm/.xml` → BeautifulSoup with `script`/`style` decomposed (L196–209) —
   **unreachable**: branch 1 catches them first.
9. else `(None, None)`. Every branch `try/except → (None, None)`.

Preview: `_get_preview(content, 500)` L68–76 — whitespace-collapsed first 500 chars + `"..."`.
`categorize_file` L313–320 (first set containing the lowercase suffix);
`get_file_type_display` L322–338 → `"Category (ext)"` (or `"Category (no extension)"`) —
this display string is what the sourcetype `content_regex` is matched against (see §4).

## 4. `ingestion.py` — `IngestionStore`

Splunk-style declarative layer over the same index DB (docstring L1–23).

- `RESERVED_FIELDS` L33–40: all built-in VPL/DB field names; an extraction with a
  colliding name is exposed as `kv:<name>`. `DEFAULT_INDEX = 'default'` L42.
  `IngestionError(ValueError)` L49.
- `__init__(db)` L56 → `_init_tables()` L63–106:
  - `PRAGMA table_info(files)` then `ALTER TABLE files ADD COLUMN` for
    `index_name`, `sourcetype`, `extracted_json` when missing (L67–71);
    indexes `idx_files_index`, `idx_files_styp` (L72–73).
  - `ingest_indexes` L75–84: `id`, `name UNIQUE NOT NULL`, `path_prefix DEFAULT ''`,
    `exts DEFAULT ''`, `description`, `created_at`.
  - `ingest_sourcetypes` L85–95: `id`, `name`, `exts`, `path_contains`,
    `content_regex`, `priority INTEGER DEFAULT 100`, `created_at`.
  - `ingest_fields` L96–104: `id`, `name`, `regex NOT NULL`, `sourcetype DEFAULT ''`, `created_at`.
  - `_ensure_derived()` L108–115: if any `files.index_name IS NULL` →
    `reindex_all(only_missing=True)` (backfill on first open).

**Helpers:** `_norm_exts` L119 (`'.pdf, txt' → '.pdf|.txt'`), `_ext_match` L129
(empty exts = match-all), `_check_regex` L135.

**Rule CRUD (every mutation triggers `reindex_all()`):**
- `add_index(name, path_prefix='', exts='', description='')` L144 — lowercased,
  `'default'` rejected (L149–150), duplicate name → `IngestionError`.
  `delete_index` L164; `list_indexes` L170 `ORDER BY name` (the matching order); `index_names` L175.
- `add_sourcetype(name, exts, path_contains, content_regex, priority=100)` L180 —
  at least one matcher required; `list_sourcetypes` L208
  `ORDER BY priority, id` → **ascending priority, first match wins**.
- `add_field(name, regex, sourcetype='')` L215 — name must match
  `[a-z][a-z0-9_]*`; regex must compile and have ≥1 capture group (L224–226);
  `delete_field` L236; `list_fields` L242.

**Re-derive `reindex_all(progress=None, only_missing=False)` L249–295:**
no filesystem walk, no content re-extraction. Rules precompiled (L266–268); selects
`id, full_path, extension, file_category, file_type` from `files`
(+`WHERE index_name IS NULL` for `only_missing`); per row `UPDATE files SET
index_name=?, sourcetype=?, extracted_json=?` (`json.dumps(extracted)` or `None`);
**commits every 500 rows** so watcher/scan threads can write (L289–290);
`progress(done,total)` every 50. Returns `{'updated','indexes','sourcetypes','fields'}`.

**Matching semantics:**
- `_assign_index(full_path, ext, rules)` L297–304: rule matches when
  `full_path.startswith(path_prefix)` (non-empty prefix) **and** ext whitelist
  matches; first rule wins; fallback `'default'`.
- `_assign_sourcetype(ext, full_path, category, file_type, rules)` L306–316:
  ext whitelist → `path_contains` (case-insensitive substring of full path) →
  `content_regex.search(file_type)` — ⚠️ matched against the **`file_type` display
  string** (e.g. `Document (.pdf)` / MIME-ish label), *not* file content (L313).
  First rule wins; fallback `(category or 'other').lower()`.
- `_extract_fields(file_id, category, rules)` L318–336: reads
  `content.full_content`; no content → `{}`; rule skipped when
  `rule.sourcetype` set and `!= files.file_category.lower()` (L329 — ⚠️ compares to
  `file_category`, not the assigned sourcetype); first `search()` hit wins,
  value = `group(1)`; key = `name` or `kv:{name}` when it collides with
  `RESERVED_FIELDS` (L334).

**Reporting:** `index_stats()` L340 (name/files/size), `sourcetype_stats()` L347,
`extracted_field_stats()` L353 (parses `extracted_json` in Python →
`{'field','files','sample'}`), `extracted_field_names()` L370 (derives names from
rules so unmatched rules still list; consumed by `vpl_parser._build_ctx`).
