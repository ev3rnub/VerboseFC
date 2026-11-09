# Verbose FC — File Discovery & Management

A local file discovery, indexing, and search tool with a [Streamlit](https://streamlit.io) web UI. Point it at a folder and it will scan the files, compute hashes to find duplicates, extract text content, build a SQLite full-text index, and let you search everything by name, content, or metadata.

## Features

**Scan controls**
- Pause / Resume / Cancel a running scan (runs in a background thread with live progress)
- Configurable hashing: toggle MD5 and SHA256 independently, or enable **fast hashing** (first 1 MB only) for much faster duplicate detection on large files
- **Persistent index** (toggle): the search index is kept in `~/.verbosefc/verbosefc_index.db` between sessions; unchanged files are skipped on re-scan. Turn it off for a fresh temporary index each session
- Single directory walk per scan — the file list is reused for hashing and indexing

**File Explorer tab**
- Recursive directory scan with configurable max depth and parallel workers
- **Duplicates view** — files with identical content, grouped; each group expands to a per-copy table (path, size, hash, modified time) so you can verify the match — including fast-mode candidates and `ERROR` hashes from unreadable files — before acting. Pick which copy to keep and **move the rest to the OS Trash** (Recycle Bin on Windows) or **move them to a backup folder** (relative paths preserved), with a confirmation step
- **Filename collisions view** — same name, different content
- Directory tree view, CSV report download
- **Open** / **Reveal in Finder / Explorer** for any file (cross-platform)

**Storage dashboard tab** (two sub-tabs)
- *Overview*: total size, file count, largest file, most common type;
  size and count by extension, top-10 largest files, files-by-month-modified charts
- *Disk Map*: interactive sunburst (TreeSize-style) of folder sizes built from
  scan data — pick a folder under the chart (largest slices first) to redraw it
  centered on that folder; Up / Root buttons to navigate back out; adjustable
  ring depth (1–4); hover shows MB and % of parent

**Watch dashboard tab** (real-time folder monitoring)
- Add/remove folders to watch; start/stop monitoring (watchdog)
- Alerts for created / modified / deleted / moved files, persisted in the index database
- Unacknowledged alert count on the tab badge; acknowledge or clear alerts

**Search tab** (Splunk-style layout)
- One query bar that accepts **plain keywords or a VPL pipeline** — the whole
  page (results, timeline, field counts, drill-downs) runs through the VPL engine
- **Expandable query bar** — the ▾ / ▴ handles grow and shrink the query field
  for long pipelines (it's also drag-resizable from its bottom-right corner,
  like Splunk's multiline search box); when expanded, a grey **line-number
  gutter** appears at the left edge of the bar — display-only indicators,
  never part of the query
- **Time Range Picker**: Any time / Last hour / 24 hours / 7 days / Today /
  Yesterday / 30 days / 90 days / This year / Custom date range — sits inline
  left of the query bar, Search button on the right, all on one row; applies to
  keyword and VPL searches alike
- **Match bar** — "N of M files matched in X.XXs", like Splunk's event count
- **Timeline** — matches bucketed by modified time (auto bucket width from
  minutes to months), hover shows count
- **Field sidebar** — *Selected Fields* (`ext`, `category`, `type`, `index`,
  `sourcetype`) with value counts; click a value to append `| where ext=="pdf"`
  and re-run (drill-down). *Interesting Fields* listed below
- **Events tab** — compact result rows with `▸` expand (all fields, Open /
  Reveal / remove-from-results, highlighted content preview), pagination, CSV export
- **Statistics tab** — count/size metrics and `count by ext / category / type` charts
- **Visualization tab** — bar (ext), pie (category), and size-vs-time scatter of the matches
- **VPL stages** (Verbose Processing Language): `search` (FTS5: `"phrases"`, `prefix*`, `AND/OR/NOT`),
  `where` (`ext==pdf`, `size>1mb`, `modified>7d`, `name contains "x"`),
  `sort -size`, `head N`, `fields a, b`, `stats count|sum(size)|avg|min|max by <field>`
- **VPL index reports** — `index list` (the index db + top-level roots with file
  counts), `sourcetypes [N]` (distinct file types/extensions with counts),
  `fieldsummary [N]` (available fields with distinct values and coverage %);
  scope them with a preceding `search`/`where`, e.g. `error | sourcetypes`
- **VPL ingestion commands** — configure the Ingestion tab from the query bar:
  `index add <name> <path-prefix> [exts=pdf,log]`, `index rm <name>`,
  `sourcetype add <name> [exts=…] [path=/logs/] [content="regex"]`,
  `extract add <name> "regex(with (capture) group)" [sourcetype=text]`.
  They apply immediately and re-derive every file (no re-scan needed)
- **Saved & scheduled searches** — save any query (💾), give it a schedule
  (5 min → daily); runs land in a **Scheduled Runs** inbox with result counts
  and top matches, and the Search tab shows a badge for new runs. Schedules
  fire while the app is open (background thread); for always-on, run
  `python src/scheduled_runner.py` from cron/launchd
- Search history expander — click a past query to re-run it

**Ingestion tab** (Splunk-style data pipeline settings)
- *Data Inputs & Indexes* — route indexed files into named **indexes** by path
  prefix and/or extension whitelist. Every file lands in exactly one index;
  unmatched files fall into the built-in `default` catch-all. Query with
  `where index==<name>` or group with `stats count by index`
- *Sourcetypes* — classify files with rules on extension, path fragment, or a
  regex against the detected MIME type, applied in priority order; unmatched
  files fall back to their category (`document`, `text`, …). Query with
  `where sourcetype==<name>`
- *Field Extractions* — regex with one capture group, run against each file's
  indexed content (optionally limited to a sourcetype). Extracted fields become
  first-class VPL fields: `where errkind==timeout`, `fields name, errkind`,
  `stats count by errkind`. A field name that collides with a built-in
  (`size`, `name`, …) is queried as `kv:<name>`
- Rules apply immediately (add/delete re-derives all rows in place — no
  filesystem re-scan) or via the **♻️ Apply & reindex all files** button; each
  sub-tab shows live counts, and the Field Extractions tab includes a regex
  tester against already-indexed content

**Indexing pipeline**
- Content extraction from text/code files plus PDF, DOCX, XLSX, PPTX, HTML, and more
- Files auto-categorized: document, image, video, audio, archive, code, data
- Archives are indexed through the text of their members: `.zip`, `.tar*` and
  `.7z` contribute up to 50 text-like members, 4 KB each. `.rar` files are
  indexed by metadata only (no reader is bundled)
- Re-scans skip files whose size/mtime are unchanged when **Persistent index**
  is on; files over the content-size cap are indexed as metadata and counted in
  the "content skipped" line rather than dropped silently

## Getting started

Requires Python 3.10+. Verified on Python 3.12 with Streamlit 1.66, pandas 3.1
and watchdog 7 (`src/requirements.txt` lists the floors; the UI calls need
Streamlit 1.50 or newer).

```bash
# Install dependencies
pip install -r src/requirements.txt

# Run the app (from this folder, so .streamlit/config.toml is picked up)
streamlit run src/verboseFC.py
```

> The bundled `.streamlit/config.toml` binds the server to `127.0.0.1`. The app
> has no login and can open/move your files, so do not expose it on the network
> unless you mean to: `streamlit run src/verboseFC.py --server.address 0.0.0.0`.

Then, in the browser UI:

1. Set a **Base Path** in the sidebar (e.g. `~/Documents`) and a max recursion depth.
   It starts empty on purpose — nothing is scanned until you point it somewhere.
2. Click **Scan & Index Directory** (greyed out while a scan is already running). The app hashes files in parallel and builds the search index.
3. Browse the **File Explorer** tab for duplicates and the tree view, or use the **Search** tab to query the index.

> Note: with **Persistent index** on (the default), the index lives at `~/.verbosefc/verbosefc_index.db` and survives restarts — the File Explorer and Search tab load it automatically on a fresh start. Turn the toggle off to use a temporary database in your system temp directory (`verboseFC_<pid>.db`) that is rebuilt each session.

## Project structure

```
src/
├── verboseFC.py          # Streamlit app: UI, tabs, scan controls
├── search_page.py        # Splunk-style Search tab: query bar, time picker, timeline, field sidebar, tabs
├── vpl_parser.py         # Mini VPL: parser + SQL translation + counts/timeline helpers
├── ingestion.py          # IngestionStore — indexes, sourcetypes, field extractions + reindex
├── ingestion_page.py     # Ingestion tab UI (data inputs, sourcetypes, field extractions)
├── saved_searches.py     # SavedSearchStore + SchedulerThread — saved & scheduled searches
├── scheduled_runner.py   # cron/launchd entry point for scheduled searches
├── scan_runner.py        # ScanController — background scan with pause/resume/cancel, hash options
├── db.py                 # SearchDatabase — SQLite schema, FTS5 ops, watch alerts
├── indexer.py            # FileIndexer — indexing pipeline (hash options, skip-unchanged, pause/cancel)
├── content_extractor.py  # ContentExtractor — text extraction & file categorization
├── search_engine.py      # SearchEngine — query parsing + full-text search
├── enhanced_features.py  # SearchHistory — persisted search history
├── ux_enhancements.py    # SearchSuggestions, SearchFeedback
├── file_actions.py       # Open/Reveal (Finder/Explorer), move-to-trash, backup-move
├── watcher.py            # FolderWatcher — watchdog multi-folder monitoring
├── test_integration.py   # Smoke/integration tests (run: python test_integration.py)
├── test_vpl.py           # VPL language tests
├── test_ingestion.py     # Ingestion (indexes/sourcetypes/extractions) tests
├── test_saved.py         # Saved/scheduled search tests
├── test_gutter.py        # Search query-bar line-number gutter test
└── requirements.txt
```

## Dependencies

- **streamlit** — web UI
- **pandas** — tabular scan results and CSV export
- **PyPDF2, python-docx, openpyxl, python-pptx, beautifulsoup4** — content extraction
- **py7zr** — 7z archive content (`.rar` is metadata-only)
- **watchdog** — real-time folder monitoring (Watch dashboard)
- **send2trash** — move duplicates to the OS Trash / Recycle Bin

## Notes & limitations

- Files larger than 10 MB are indexed by metadata only (content extraction is skipped) — field extractions therefore only see files whose content was extracted.
- Ingestion rules (indexes, sourcetypes, extractions) live in the same database and apply to already-indexed files; they never touch the filesystem. `sourcetype add content="regex"` matches the detected MIME type (e.g. `text/plain`), not file content — use `extract add` for content-based rules.
- The scan respects the max-depth setting; very large trees with a high depth can take a while — tune the **Parallel Workers** slider, enable **fast hashing**, or pause/cancel mid-scan.
- Windows artifact files (`Thumbs.db`, `$desktop.ini`) and `.DS_Store` are skipped automatically.
- The persistent index lives at `~/.verbosefc/verbosefc_index.db` (delete it to start clean). Watch alerts are stored in the same database and survive restarts.
- Fast hashing compares the first 1 MB + file size — very rare false positives for files identical in their first megabyte.
- PDF previews are embedded inline up to 20 MB.
- Content extraction is capped per file (10 MB) and per extracted document
  (500,000 characters), so a huge log file cannot balloon the index.
- Opening an index built before 11/05 migrates the full-text table in place
  (`file_id` becomes UNINDEXED, which stops numeric queries such as `12`
  matching byte sizes). Rows are preserved; the migration is a no-op afterwards.
- The app binds `127.0.0.1` by default (`.streamlit/config.toml`) and has no
  authentication of any kind — treat any override as "anyone on this network
  can browse, open and trash the files this process can reach".

## Running the tests

The suites are plain scripts (no pytest needed). From `src/`:

```bash
python test_walk.py           # directory walk, depth limits, pruning
python test_vpl.py            # VPL parser + SQL translation
python test_ingestion.py      # indexes, sourcetypes, field extractions
python test_saved.py          # saved & scheduled searches
python test_gutter.py         # search query-bar line gutter
python test_integration.py    # end-to-end: index → search → duplicates
python test_regressions.py    # size caps, FTS migration, duplicate keeper guard
```
- Open/Reveal launch native apps on the machine running the Streamlit server (fine for local use; not over a remote browser).
