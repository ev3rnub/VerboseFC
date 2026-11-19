Verbose FC — File Discovery & Management

A local file discovery, indexing, and search tool with a Streamlit web UI. Point it at a folder and it will scan the files, compute hashes to find duplicates, extract text content, build a SQLite full-text index, and let you search everything by name, content, or metadata.

Features

Scan controls

Base Path folder picker — a 📁 Browse… button opens the native Finder / Explorer dialog to choose the scan root (no more typing paths; ~ still works if you prefer typing)
Pause / Resume / Cancel a running scan (runs in a background thread with live progress)
Configurable hashing: toggle MD5 and SHA256 independently, or enable fast hashing (first 1 MB only) for much faster duplicate detection on large files
Persistent index (toggle): the search index is kept in ~/.verbosefc/verbosefc_index.db between sessions; unchanged files are skipped on re-scan. Turn it off for a fresh temporary index each session
Single directory walk per scan — the file list is reused for hashing and indexing
File Explorer tab

Recursive directory scan with configurable max depth and parallel workers
Duplicates view — files with identical content, grouped; each group expands to a per-copy table (path, size, hash, modified time) so you can verify the match — including fast-mode candidates and ERROR hashes from unreadable files — before acting. Pick which copy to keep and move the rest to the OS Trash (Recycle Bin on Windows) or move them to a backup folder (relative paths preserved), with a confirmation step
Filename collisions view — same name, different content
Directory tree view, CSV report download
Open / Reveal in Finder / Explorer for any file (cross-platform)
Storage dashboard tab (two sub-tabs)

Overview: total size, file count, largest file, most common type; size and count by extension, top-10 largest files, files-by-month-modified charts
Disk Map: interactive sunburst (TreeSize-style) of folder sizes built from scan data — pick a folder under the chart (largest slices first) to redraw it centered on that folder; Up / Root buttons to navigate back out; adjustable ring depth (1–4); hover shows MB and % of parent
Database dashboard tab (stats for the SQLite file behind the index)

Headline metrics: file size vs. actually-used pages, free space SQLite keeps for reuse (VACUUM hint when it gets large), integrity check, journal mode; counts of indexed files, FTS documents, search history and watch alerts
Size & Composition: donut charts of where the bytes live (FTS index vs. tables vs. indexes, via dbstat), file categories, content-extraction coverage; top-extensions bar chart. Slices under 1% fold into one "Other" with the labels kept inside the ring, so the charts stay readable
Activity: searches per day (line), most frequent queries (bar), indexed files modified in the last 90 days (area), watch alerts by type / per day
Tables & Integrity: row counts per table, storage per table/index, sidecar file sizes; read-only queries, so it never disturbs a running scan
Watch dashboard tab (real-time folder monitoring)

Add/remove folders to watch — with the same 📁 Browse… native folder picker as the scan path; start/stop monitoring (watchdog)
Alerts for created / modified / deleted / moved files, persisted in the index database
Unacknowledged alert count on the tab badge; acknowledge or clear alerts
Search tab (Splunk-style layout)

One query bar that accepts plain keywords or a VPL pipeline — the whole page (results, timeline, field counts, drill-downs) runs through the VPL engine
Expandable query bar — the ▾ / ▴ handles grow and shrink the query field for long pipelines (it's also drag-resizable from its bottom-right corner, like Splunk's multiline search box); when expanded, a grey line-number gutter appears at the left edge of the bar — display-only indicators, never part of the query
Time Range Picker: Any time / Last hour / 24 hours / 7 days / Today / Yesterday / 30 days / 90 days / This year / Custom date range — sits inline left of the query bar, Search button on the right, all on one row; applies to keyword and VPL searches alike
Match bar — "N of M files matched in X.XXs", like Splunk's event count
Timeline — matches bucketed by modified time (auto bucket width from minutes to months), hover shows count
Field sidebar — Selected Fields (ext, category, type, index, sourcetype) with value counts; click a value to append | where ext=="pdf" and re-run (drill-down). Interesting Fields listed below
Events tab — compact result rows with ▸ expand (all fields, Open / Reveal / remove-from-results, highlighted content preview), pagination, CSV export
Statistics tab — count/size metrics and count by ext / category / type charts
Visualization tab — bar (ext), pie (category), and size-vs-time scatter of the matches
VPL stages (Verbose Processing Language): search (FTS5: "phrases", prefix*, AND/OR/NOT), where (ext==pdf, size>1mb, modified>7d, name contains "x"), sort -size, head N, fields a, b, stats count|sum(size)|avg|min|max by <field>
VPL index reports — index list (the index db + top-level roots with file counts), sourcetypes [N] (distinct file types/extensions with counts), fieldsummary [N] (available fields with distinct values and coverage %); scope them with a preceding search/where, e.g. error | sourcetypes
VPL ingestion commands — configure the Ingestion tab from the query bar: index add <name> <path-prefix> [exts=pdf,log], index rm <name>, sourcetype add <name> [exts=…] [path=/logs/] [content="regex"], extract add <name> "regex(with (capture) group)" [sourcetype=text]. They apply immediately and re-derive every file (no re-scan needed)
Saved & scheduled searches — save any query (💾), give it a schedule (5 min → daily); runs land in a Scheduled Runs inbox with result counts and top matches, and the Search tab shows a badge for new runs. Schedules fire while the app is open (background thread); for always-on, run python src/scheduled_runner.py from cron/launchd
Search history expander — click a past query to re-run it
Ingestion tab (Splunk-style data pipeline settings)

Data Inputs & Indexes — route indexed files into named indexes by path prefix and/or extension whitelist. Every file lands in exactly one index; unmatched files fall into the built-in default catch-all. Query with where index==<name> or group with stats count by index
Sourcetypes — classify files with rules on extension, path fragment, or a regex against the detected MIME type, applied in priority order; unmatched files fall back to their category (document, text, …). Query with where sourcetype==<name>
Field Extractions — regex with one capture group, run against each file's indexed content (optionally limited to a sourcetype). Extracted fields become first-class VPL fields: where errkind==timeout, fields name, errkind, stats count by errkind. A field name that collides with a built-in (size, name, …) is queried as kv:<name>
Rules apply immediately (add/delete re-derives all rows in place — no filesystem re-scan) or via the ♻️ Apply & reindex all files button; each sub-tab shows live counts, and the Field Extractions tab includes a regex tester against already-indexed content
Indexing pipeline

Content extraction from text/code files plus PDF, DOCX, XLSX, PPTX, HTML, and more
Files auto-categorized: document, image, video, audio, archive, code, data
Archives are indexed through the text of their members: .zip, .tar* and .7z contribute up to 50 text-like members, 4 KB each. .rar files are indexed by metadata only (no reader is bundled)
Re-scans skip files whose size/mtime are unchanged when Persistent index is on; files over the content-size cap are indexed as metadata and counted in the "content skipped" line rather than dropped silently
