"""Database-level statistics for the Database dashboard tab.

These queries deliberately bypass SearchDatabase's ORM-ish helpers: this tab
reports on the SQLite file itself (page counts, freelist, FTS shadow tables),
which the app layer has no reason to know about. All access is read-only;
the connection uses a busy timeout so it never fights a running scan thread.
"""

import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional


# Tables that belong to SQLite/FTS internals but are worth surfacing as size.
def _safe_query(conn: sqlite3.Connection, sql: str, params=()) -> Optional[List[tuple]]:
    """Run one read query; return None (not raise) if the table is missing or
    the statement fails — an older index file may lack newer tables."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return None


def collect_db_stats(db_path: str) -> Dict:
    """Gather a stats snapshot for one SQLite database file.

    Returns {} when the file does not exist yet (e.g. temp DB before first use).
    """
    out: Dict = {'path': db_path}
    p = Path(db_path)
    if not p.exists():
        return {}

    # --- File-level facts (no SQL needed) ---
    st = p.stat()
    out['file_bytes'] = st.st_size
    out['mtime_epoch'] = st.st_mtime
    sidecars = {}
    for suffix in ('-wal', '-shm', '-journal'):
        side = Path(db_path + suffix)
        if side.exists():
            sidecars[suffix] = side.stat().st_size
    out['sidecar_bytes'] = sidecars

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        cur = conn.cursor()
        # A corrupt/truncated file raises on the first statement rather than
        # per-query; the tab treats an empty result as "cannot read this file".
        cur.execute("PRAGMA page_size").fetchone()

        # --- Page math: the real footprint story of an SQLite file ---
        page_size = cur.execute("PRAGMA page_size").fetchone()[0]
        page_count = cur.execute("PRAGMA page_count").fetchone()[0]
        free_pages = cur.execute("PRAGMA freelist_count").fetchone()[0]
        out['page_size'] = page_size
        out['page_count'] = page_count
        out['free_pages'] = free_pages
        out['used_bytes'] = (page_count - free_pages) * page_size

        # --- Per-table and per-index footprint via dbstat, when available ---
        per_object = _safe_query(
            conn,
            "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY SUM(pgsize) DESC")
        out['per_object'] = per_object  # None if the build lacks the dbstat eponym

        # --- Row counts for every user table ---
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        row_counts: Dict[str, int] = {}
        for t in tables:
            r = _safe_query(conn, f'SELECT COUNT(*) FROM "{t}"')
            if r is not None:
                row_counts[t] = r[0][0]
        out['row_counts'] = row_counts

        # --- FTS shadow-table sizes (content index lives here) ---
        fts_bytes = 0
        if out['per_object']:
            for name, nbytes in out['per_object']:
                if name.startswith('content_'):
                    fts_bytes += nbytes or 0
        out['fts_bytes'] = fts_bytes

        # --- Journal mode / integrity quick facts ---
        out['journal_mode'] = cur.execute("PRAGMA journal_mode").fetchone()[0]
        out['integrity'] = cur.execute("PRAGMA quick_check").fetchone()[0]

        # --- Time series for charts ---
        out['files_by_month'] = _safe_query(conn, """
            SELECT strftime('%Y-%m', modified_at_epoch, 'unixepoch') AS m,
                   COUNT(*), SUM(size_bytes)
            FROM files WHERE modified_at_epoch IS NOT NULL
            GROUP BY m ORDER BY m""") or []
        # Search activity over time (search_history.timestamp is TEXT ISO).
        out['searches_by_day'] = _safe_query(conn, """
            SELECT substr(timestamp, 1, 10) AS d, COUNT(*)
            FROM search_history GROUP BY d ORDER BY d""") or []
        out['top_queries'] = _safe_query(conn, """
            SELECT query, COUNT(*) AS n, AVG(result_count)
            FROM search_history GROUP BY query ORDER BY n DESC LIMIT 10""") or []

        # Watch alerts by event type and over time.
        out['alerts_by_type'] = _safe_query(conn, """
            SELECT event_type, COUNT(*) FROM watch_alerts
            GROUP BY event_type ORDER BY COUNT(*) DESC""") or []
        out['alerts_by_day'] = _safe_query(conn, """
            SELECT substr(created_at, 1, 10) AS d, COUNT(*)
            FROM watch_alerts GROUP BY d ORDER BY d""") or []

        # Index freshness: modified-file epochs as an "indexing activity" line.
        out['file_activity_by_day'] = _safe_query(conn, """
            SELECT strftime('%Y-%m-%d', modified_at_epoch, 'unixepoch') AS d, COUNT(*)
            FROM files WHERE modified_at_epoch > (strftime('%s','now') - 90*86400)
            GROUP BY d ORDER BY d""") or []

        # Category / extension snapshots for circle charts.
        out['by_category'] = _safe_query(conn, """
            SELECT file_category, COUNT(*), SUM(size_bytes)
            FROM files GROUP BY file_category ORDER BY COUNT(*) DESC LIMIT 12""") or []
        out['by_extension'] = _safe_query(conn, """
            SELECT extension, COUNT(*) AS n, SUM(size_bytes)
            FROM files WHERE extension != '' GROUP BY extension
            ORDER BY n DESC LIMIT 15""") or []

        # Content coverage donut.
        r = cur.execute("SELECT content_available, COUNT(*) FROM files GROUP BY content_available").fetchall()
        out['content_coverage'] = {str(k): v for k, v in r}

    except sqlite3.Error:
        # Corrupt or unreadable database file — the caller shows an error card.
        conn.close()
        return {}
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    return out
