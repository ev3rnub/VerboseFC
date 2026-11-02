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
- **Duplicates view** — files with identical content, grouped; pick which copy to keep and **move the rest to the OS Trash** (Recycle Bin on Windows) or **move them to a backup folder** (relative paths preserved), with a confirmation step
- **Filename collisions view** — same name, different content
- Directory tree view, CSV report download
- **Open** / **Reveal in Finder / Explorer** for any file (cross-platform)

**Storage dashboard tab**
- Total size, file count, largest file, most common type
- Size and count by extension, top-10 largest files, files-by-month-modified charts

**Watch dashboard tab** (real-time folder monitoring)
- Add/remove folders to watch; start/stop monitoring (watchdog)
- Alerts for created / modified / deleted / moved files, persisted in the index database
- Unacknowledged alert count on the tab badge; acknowledge or clear alerts

**Search tab**
- Natural-language search over file names, extracted content, and metadata (SQLite FTS5)
- Query parsing: keywords, "exact phrases", date keywords (`today`, `yesterday`, `last week`, `last month`), file-type and size hints
- Filters: category, extension, min/max size, date range (Today / Yesterday / Last 7 days / Last 30 days)
- Pagination, term highlighting, content previews, syntax-highlighted text previews, and inline **PDF preview**
- **Open** / **Reveal in Finder / Explorer** buttons on every result
- CSV / JSON export of results
- Advanced search: Boolean (`AND` / `OR` / `NOT`), wildcards (`*`, `?`), phrase and regex (`/pattern/`) queries
- Search history, search suggestions, popular terms, and search feedback/refinement tips
- Index statistics

**Indexing pipeline**
- Content extraction from text/code files plus PDF, DOCX, XLSX, PPTX, HTML, and more
- Files auto-categorized: document, image, video, audio, archive, code, data
- Incremental indexing and search caching support

## Getting started

Requires Python 3.10+.

```bash
# Install dependencies
pip install -r src/requirements.txt

# Run the app
streamlit run src/verboseFC.py
```

Then, in the browser UI:

1. Set a **Base Path** in the sidebar (e.g. `~/Documents`) and a max recursion depth.
2. Click **Scan & Index Directory**. The app hashes files in parallel and builds the search index.
3. Browse the **File Explorer** tab for duplicates and the tree view, or use the **Search** tab to query the index.

> Note: the index database is a temporary SQLite file created in your system temp directory (`verboseFC_<pid>.db`) and is rebuilt on each app session.

## Project structure

```
src/
├── verboseFC.py          # Streamlit app: UI, tabs, scan controls
├── scan_runner.py        # ScanController — background scan with pause/resume/cancel, hash options
├── db.py                 # SearchDatabase — SQLite schema, FTS5 ops, watch alerts
├── indexer.py            # FileIndexer — indexing pipeline (hash options, skip-unchanged, pause/cancel)
├── content_extractor.py  # ContentExtractor — text extraction & file categorization
├── search_engine.py      # SearchEngine — query parsing + full-text search
├── enhanced_features.py  # AdvancedSearch, SearchCache, IncrementalIndexer, SearchHistory
├── ux_enhancements.py    # SearchSuggestions, SearchFeedback, VisualEnhancements
├── file_actions.py       # Open/Reveal (Finder/Explorer), move-to-trash, backup-move
├── watcher.py            # FolderWatcher — watchdog multi-folder monitoring
├── test_integration.py   # Smoke/integration tests (run: python test_integration.py)
└── requirements.txt
```

## Dependencies

- **streamlit** — web UI
- **pandas** — tabular scan results and CSV export
- **PyPDF2, python-docx, openpyxl, python-pptx, beautifulsoup4** — content extraction
- **python-magic** — file type detection (needs libmagic; usually present on macOS)
- **py7zr, rarfile** — archive handling
- **watchdog** — real-time folder monitoring (Watch dashboard)
- **send2trash** — move duplicates to the OS Trash / Recycle Bin

## Notes & limitations

- Files larger than 10 MB are indexed by metadata only (content extraction is skipped).
- The scan respects the max-depth setting; very large trees with a high depth can take a while — tune the **Parallel Workers** slider, enable **fast hashing**, or pause/cancel mid-scan.
- Windows artifact files (`Thumbs.db`, `$desktop.ini`) and `.DS_Store` are skipped automatically.
- The persistent index lives at `~/.verbosefc/verbosefc_index.db` (delete it to start clean). Watch alerts are stored in the same database and survive restarts.
- Fast hashing compares the first 1 MB + file size — very rare false positives for files identical in their first megabyte.
- PDF previews are embedded inline up to 20 MB.
- Open/Reveal launch native apps on the machine running the Streamlit server (fine for local use; not over a remote browser).
