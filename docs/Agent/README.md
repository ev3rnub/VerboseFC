# Agent Reference — Verbose FC Codebase

*Detailed technical documentation for agents and developers working on this codebase.*

Verbose FC is a local file discovery / indexing / search tool with a Streamlit web UI,
backed by a single SQLite database (FTS5). All application code lives in `src/`.

## Contents

| Doc | Covers |
|---|---|
| [01-architecture.md](01-architecture.md) | Process model, module map, data flow, startup sequence, threading |
| [02-ui-layer.md](02-ui-layer.md) | `verboseFC.py`, `search_page.py`, `ingestion_page.py` — screens, widgets, session-state keys |
| [03-search-vpl.md](03-search-vpl.md) | `vpl_parser.py`, `search_engine.py` — the VPL language, SQL translation, keyword search |
| [04-data-layer.md](04-data-layer.md) | `db.py` schema, `indexer.py`, `content_extractor.py`, `ingestion.py` |
| [05-services.md](05-services.md) | `scan_runner.py`, `watcher.py`, `saved_searches.py`, `file_actions.py`, `enhanced_features.py`, `ux_enhancements.py`, `db_stats.py`, `scheduled_runner.py` |
| [06-testing-dev.md](06-testing-dev.md) | Test suites, environment, known quirks & fragile spots |

## Quick orientation

- **Entry point:** `streamlit run src/verboseFC.py` → `main()` in `src/verboseFC.py` (L315).
- **Database:** SQLite at `~/.verbosefc/verbosefc_index.db` (persistent mode, default)
  or `<tempdir>/verboseFC_<pid>.db` (temp mode). Schema owner: `src/db.py`.
- **Two query engines:** the VPL pipeline language (`vpl_parser.py`, used by the
  Search tab for *everything*) and a natural-language keyword engine
  (`search_engine.py`, history/suggestions/fuzzy path).
- **Everything UI is Streamlit**: the whole page re-executes on each interaction;
  state lives in `st.session_state` (keys catalogued in 02-ui-layer.md).
- **Server binds `127.0.0.1`** by default via `.streamlit/config.toml`; no auth.
- **Python 3.13+**, Streamlit ≥1.50 (<2), pandas, plotly, watchdog, send2trash,
  plus content-extraction libraries (see `src/requirements.txt`).
