# Verbose FC — Documentation

Verbose FC is a local file discovery, indexing, and search tool with a web UI
(your machine only — it doesn't reach the internet and nothing leaves your computer).

This documentation set is split into two audiences:

## [User/](User/README.md) — for everyday use, no technical background needed
Step-by-step guides: opening the app, your first scan, finding files, cleaning up
duplicates, the dashboards, saved/scheduled searches, and troubleshooting.

1. [Getting Started](User/01-Getting-Started.md)
2. [Your First Scan](User/02-First-Scan.md)
3. [Finding Files](User/03-Finding-Files.md)
4. [Cleaning Up Duplicates](User/04-Cleaning-Up-Duplicates.md)
5. [The Dashboards](User/05-The-Dashboards.md)
6. [Saved and Scheduled Searches](User/06-Saved-and-Scheduled-Searches.md)
7. [Troubleshooting & FAQ](User/07-Troubleshooting-and-FAQ.md)

## [Agent/](Agent/README.md) — detailed technical reference (code-level)
For developers and AI agents working on the codebase. Line numbers refer to the
files in `src/`.

1. [Architecture Overview](Agent/01-architecture.md)
2. [UI Layer](Agent/02-ui-layer.md) — `verboseFC.py`, `search_page.py`, `ingestion_page.py`
3. [Query Engines: VPL & Keyword Search](Agent/03-search-vpl.md)
4. [Data Layer: Schema, Indexing, Extraction, Ingestion](Agent/04-data-layer.md)
5. [Support Services](Agent/05-services.md) — scan runner, watcher, saved searches, file actions, stats
6. [Testing, Environment & Known Quirks](Agent/06-testing-dev.md)

Quick start for either audience: `README.md` at the repo root has the feature
summary; the guides above go deeper.
