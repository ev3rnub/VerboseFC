import os
import sys
import subprocess
import time
import hashlib
import json
import base64
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from content_extractor import ContentExtractor
from db import SearchDatabase
from db_stats import collect_db_stats
from indexer import FileIndexer
from search_engine import SearchEngine, create_search_engine
from scan_runner import ScanController, get_human_size
from file_actions import (open_path, reveal_path, move_to_trash, backup_move,
                        duplicate_mover_targets)
from watcher import FolderWatcher
from saved_searches import SavedSearchStore
from search_page import render_search_tab
from ingestion import IngestionStore
from ingestion_page import render_ingestion_tab

# --- Constants ---

PERSISTENT_DB_PATH = Path.home() / ".verbosefc" / "verbosefc_index.db"
FAST_HASH_LIMIT = 1024 * 1024  # first 1 MB when fast hashing is enabled
MAX_PDF_PREVIEW_BYTES = 20 * 1024 * 1024
# How many duplicate groups the Duplicates tab renders and acts on in one pass.
DUP_GROUPS_SHOWN = 100


DISKMAP_MAX_ROWS = 2000


def _dup_group_key(group) -> str:
    """Stable id for a duplicate group, independent of its position in the list.

    Group widgets used to be keyed by their index in dup_groups, so when a group
    disappeared (after an apply, or after the hash/dedup setting changed) the
    stored 'kept copy' belonged to a different group and a group could end up
    with no valid keeper selected — moving every copy in it.
    """
    paths = '|'.join(sorted(group['full_path'].tolist()))
    return hashlib.sha1(paths.encode('utf-8', 'replace')).hexdigest()[:12]


def _clear_duplicate_keepers() -> None:
    """Drop keeper/act selections that refer to a previous result frame."""
    for key in [k for k in list(st.session_state.keys())
                if k.startswith('dupkeep_') or k.startswith('dupgrp_')]:
        del st.session_state[key]


def _scan_is_running() -> bool:
    """True while a ScanController thread is still walking/hashing/indexing."""
    controller = st.session_state.get('scan_controller')
    return controller is not None and controller.is_active()


# --- Disk Map (sunburst) ---

def render_disk_map(sdf: pd.DataFrame) -> None:
    """Sunburst disk-usage map with drill-down, built from scan/index data."""
    scan_root = str(st.session_state.get('scan_root', '')).rstrip('/\\')
    if not scan_root:
        st.info("Run a scan first — the Disk Map is built from scan results.")
        return

    cur = st.session_state.get('diskmap_path') or scan_root
    if cur != scan_root and not cur.startswith(scan_root + os.sep):
        cur = scan_root  # stale path from a previous scan

    def under(p: str) -> bool:
        return p == cur or p.startswith(cur + os.sep)

    cur_files = sdf[sdf['full_path'].map(under)]
    if cur_files.empty:
        st.info("No indexed files under this folder.")
        return

    total_cur = int(cur_files['size_bytes'].sum())
    cur_name = os.path.basename(cur) or cur

    st.caption(f"📍 **{cur}**")
    m1, m2, m3 = st.columns(3)
    m1.metric("Folder size", get_human_size(total_cur))
    m2.metric("Files", f"{len(cur_files):,}")

    depth = st.slider("Rings to show", 1, 4, 2, key="diskmap_depth")

    # Aggregate: files within the ring budget are leaves; deeper files roll
    # up into a '(deeper…)' marker so every row stays a leaf (Plotly rule).
    agg: Dict[tuple, int] = defaultdict(int)
    child_sizes: Dict[str, int] = defaultdict(int)
    for p, sz in zip(cur_files['full_path'], cur_files['size_bytes']):
        parts = os.path.relpath(p, cur).split(os.sep)
        child_sizes[parts[0]] += sz
        if len(parts) <= depth:
            agg[(cur_name,) + tuple(parts)] += sz
        else:
            agg[(cur_name,) + tuple(parts[:depth]) + ('(deeper…)',)] += sz
    m3.metric("Subfolders", len(child_sizes))

    # File/dir name collision: a leaf tuple that is a prefix of a bucket tuple
    for bucket in [t for t in agg if t[-1] == '(deeper…)']:
        for leaf in [t for t in agg if t[-1] != '(deeper…)']:
            if len(leaf) < len(bucket) and bucket[:len(leaf)] == leaf:
                agg[leaf[:-1] + (leaf[-1] + ' ·file',)] = agg.pop(leaf)

    # Cap rows for fast rendering; the tail becomes one '(rest)' slice
    if len(agg) > DISKMAP_MAX_ROWS:
        items = sorted(agg.items(), key=lambda kv: -kv[1])
        agg = dict(items[:DISKMAP_MAX_ROWS - 1])
        rest = sum(v for _, v in items[DISKMAP_MAX_ROWS - 1:])
        agg[(cur_name, f"(rest: {len(items) - DISKMAP_MAX_ROWS + 1} more)")] = rest

    # Trim path columns to the deepest actual tuple — an all-empty column
    # crashes Plotly's hierarchy builder.
    max_len = max(len(t) for t in agg)
    rows = [{f"L{i + 1}": (t[i] if i < len(t) else None) for i in range(max_len)}
            | {'size_mb': v / (1024 * 1024)} for t, v in agg.items()]
    fig = px.sunburst(pd.DataFrame(rows), path=[f"L{i + 1}" for i in range(max_len)],
                      values='size_mb')
    # NOTE: sunburst hovertemplate only supports label/value/percent/currentPath —
    # percentParent is textinfo-only and would render the tooltip blank.
    fig.update_traces(textinfo='label+percent parent', insidetextorientation='radial',
                      hovertemplate='<b>%{label}</b><br>%{value:,.2f} MB'
                                    '<br>%{percent} of parent<extra></extra>',
                      hoverlabel=dict(bgcolor='rgba(30, 33, 48, 0.95)',
                                      bordercolor='#6c7a9c', font=dict(size=18, color='white')))
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=640,
                      hoverlabel=dict(font=dict(size=18)))
    st.plotly_chart(fig, width='stretch')

    # Drill controls (Streamlit cannot capture clicks on the chart itself)
    children = sorted(child_sizes.items(), key=lambda kv: -kv[1])[:30]
    c1, c2, c3 = st.columns([3, 1.2, 1.2])
    with c1:
        choice = st.selectbox("Drill into folder (largest first)",
                              ["— stay here —"] + [n for n, _ in children],
                              key="diskmap_drill")
    with c2:
        st.write("")
        st.write("")
        if st.button("⬅ Up one level", disabled=(cur == scan_root), width='stretch'):
            st.session_state['diskmap_path'] = os.path.dirname(cur) or scan_root
            st.rerun()
    with c3:
        st.write("")
        st.write("")
        if st.button("🏠 Root", disabled=(cur == scan_root), width='stretch'):
            st.session_state['diskmap_path'] = scan_root
            st.rerun()
    if choice != "— stay here —":
        st.session_state['diskmap_path'] = os.path.join(cur, choice)
        del st.session_state['diskmap_drill']  # avoid re-drilling on rerun
        st.rerun()


# --- Utility Functions ---

def export_results_csv(results: List[Dict]) -> bytes:
    """Export search results as CSV."""
    df = pd.DataFrame(results)
    return df.to_csv(index=False).encode('utf-8')

def export_results_json(results: List[Dict]) -> bytes:
    """Export search results as JSON."""
    json_data = json.dumps(results, indent=2, default=str).encode('utf-8')
    return json_data

def show_pdf_preview(file_path: str):
    """Embed a PDF preview inline (base64 data URI, capped at 20 MB)."""
    try:
        size = os.path.getsize(file_path)
    except OSError:
        st.warning("PDF not found on disk.")
        return
    if size > MAX_PDF_PREVIEW_BYTES:
        st.info(f"PDF is {get_human_size(size)} — preview limited to {get_human_size(MAX_PDF_PREVIEW_BYTES)}.")
        return
    try:
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except OSError as e:
        st.error(f"Could not read PDF: {e}")
        return
    components.html(
        f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="650px" style="border:0"></iframe>',
        height=660
    )

def show_file_preview(file_data: Dict):
    """Display file content preview (text with syntax highlighting, PDF inline)."""
    st.subheader(f"📄 Preview: {file_data['name']}")

    # PDF: inline embedded preview when the file is still on disk
    full_path = file_data.get('full_path', '')
    if full_path and full_path.lower().endswith('.pdf') and os.path.exists(full_path):
        show_pdf_preview(full_path)

    if file_data.get('content'):
        # Show full content with syntax highlighting
        content = file_data['content']
        if len(content) > 10000:
            content = content[:10000] + "\n\n... [Content truncated]"

        # Syntax highlighting based on file type
        extension = file_data.get('extension', '')
        language_map = {
            '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
            '.java': 'java', '.cpp': 'cpp', '.c': 'c', '.h': 'c',
            '.cs': 'csharp', '.go': 'go', '.rs': 'rust', '.php': 'php',
            '.rb': 'ruby', '.swift': 'swift', '.kt': 'kotlin', '.sql': 'sql',
            '.sh': 'bash', '.bash': 'bash', '.yml': 'yaml', '.yaml': 'yaml',
            '.toml': 'toml', '.ini': 'ini', '.cfg': 'ini', '.conf': 'ini',
            '.html': 'html', '.htm': 'html', '.xml': 'xml', '.json': 'json',
            '.md': 'markdown'
        }
        language = language_map.get(extension, None)

        if language:
            st.code(content, language=language)
        else:
            st.text(content)
    elif not (full_path and full_path.lower().endswith('.pdf')):
        st.info("Content not available for preview")

    # Add download button
    if file_data.get('content'):
        csv = file_data['content'].encode('utf-8')
        file_id = file_data.get('id', 'file')
        st.download_button(
            "Download Content",
            csv,
            f"content_{file_id}.txt",
            "text/plain"
        )

def get_resolved_path(path_str: str) -> Path:
    """Resolve a path string, handling ~ and relative paths."""
    if not path_str:
        return Path.cwd()
    expanded = os.path.expanduser(os.path.expandvars(path_str))
    return Path(expanded).resolve()


# Tk code that runs in a helper subprocess (see pick_folder_dialog). It must
# not import anything from this app — it is deliberately self-contained so the
# child starts fast and cannot crash on an unrelated import.
_PICKER_SNIPPET = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)  # bring the picker in front of the browser
initialdir = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
chosen = filedialog.askdirectory(
    title="Choose the folder to scan",
    initialdir=initialdir,
    mustexist=True,
)
root.destroy()
sys.stdout.write(chosen or '')
"""


def pick_folder_dialog(initial: str = "") -> Optional[str]:
    """Open the native OS folder picker (Finder / Explorer / GTK) and return
    the chosen path, or None if cancelled.

    Streamlit runs in the browser and has no directory picker of its own, so
    this borrows the OS-native one via tkinter — which ships with CPython on
    Windows and macOS and needs no new dependency.

    The picker runs in a throwaway subprocess, NOT in this process: Streamlit
    executes this script on a ScriptRunner thread, and Tk windows must be
    created on the main thread — on macOS that aborts the whole server with
    'NSWindow should only be instantiated on the main thread!'. A child
    process has its own main thread, so the dialog is safe there. If tkinter
    (or a display) is unavailable — headless server, Python without Tk — the
    caller falls back to the plain text input."""
    initialdir = ""
    if initial:
        cand = get_resolved_path(initial)
        if cand.is_dir():
            initialdir = str(cand)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PICKER_SNIPPET, initialdir],
            capture_output=True, text=True, timeout=300,  # generous: user thinks in the dialog
        )
    except Exception:
        return None
    chosen = proc.stdout.strip()
    if proc.returncode != 0 or not chosen:
        # Non-zero exit usually means no Tk / no display; treat as cancelled
        # so the UI keeps the text input instead of crashing.
        return None
    # Normalise separators for display (posixpath/ntpath match the child's OS).
    return str(Path(chosen))

# --- Main App ---

def main():
    st.set_page_config(page_title="File Discovery Dashboard", layout="wide")
    st.title("📂 File Discovery & Management")

    with st.sidebar:
        st.header("Settings")
        # No default on purpose: prefilling "~" made the primary button start a
        # scan (and full-content index) of the user's entire home directory.
        # A native folder picker sits next to the text box: typing "~/..." is
        # meaningless on Windows, and browsing removes the guesswork entirely.
        # The text box stays as the fallback for headless runs where tkinter
        # has no display (the picker button then disappears).
        if 'picked_path' not in st.session_state:
            st.session_state['picked_path'] = ""
        path_col, btn_col = st.columns([5, 2])
        with path_col:
            base_path_typed = st.text_input(
                "Base Path", value=st.session_state['picked_path'],
                placeholder="~/Documents or C:\\Users\\you",
                help="Root directory to scan and index — type it, or use Browse…")
        with btn_col:
            # The two empty writes used to push the button down beside the
            # input overshot the label+input block, leaving Browse hanging
            # below the text field. A bare st.button has no visible label row
            # in this Streamlit (confirmed in its frontend bundle), so it only
            # needs one spacer line — the same height as the "Base Path"
            # label next to it — and nothing more.
            st.write("")
            browse_ok = True
            if st.button("📁 Browse…", width='stretch', key="base_browse",
                         help="Opens the native Finder / Explorer folder picker"):
                chosen = pick_folder_dialog(base_path_typed)
                if chosen:
                    st.session_state['picked_path'] = chosen
                    st.rerun()
                elif not st.session_state['picked_path']:
                    browse_ok = False
        base_path = base_path_typed  # Streamlit already strips outer whitespace; do not strip inner/trailing-space paths like "My Folder "
        if not browse_ok:
            st.caption("Folder picker unavailable (no desktop session) — type the path instead.")
        max_depth = st.slider("Max Recursion Depth", 1, 10, 2)
        workers = st.slider("Parallel Workers", 1, 16, 4)

        enable_indexing = st.checkbox("Enable Full Indexing", value=True, help="Extract content, timestamps, and categorize files")
        persistent_index = st.checkbox(
            "Persistent Index", value=True,
            help="Keep the search index in ~/.verbosefc between sessions. "
                 "Unchanged files are skipped on re-scan. When off, a fresh temporary index is built each session."
        )

        with st.expander("🔐 Hashing Options", expanded=False):
            hash_md5 = st.checkbox("Compute MD5", value=True)
            hash_sha256 = st.checkbox("Compute SHA256", value=True)
            fast_hash = st.checkbox(
                "Fast hashing (first 1 MB only)", value=False,
                help="Much faster on large files. Duplicate detection compares the first 1 MB plus file size — "
                     "rare false positives possible for files identical in their first megabyte."
            )
            if not hash_md5 and not hash_sha256:
                st.caption("No hashing selected — duplicate detection will be unavailable for this scan.")

        # A second scan would replace session_state['scan_controller'] and leave
        # the first thread running with no reference to pause or cancel it.
        scan_running = _scan_is_running()
        run_scan = st.button("Scan & Index Directory", type="primary",
                             disabled=scan_running)
        if scan_running:
            st.caption("A scan is already running — use Pause / Cancel. Starting "
                       "another would orphan the first one.")

    # --- Database / services ---
    if persistent_index:
        PERSISTENT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_path = str(PERSISTENT_DB_PATH)
    else:
        db_path = os.path.join(tempfile.gettempdir(), f"verboseFC_{os.getpid()}.db")

    # Services are rebuilt only when their configuration changes. The scan
    # progress loop reruns this script about every second, and a brand-new
    # SearchDatabase means a fresh SQLite connection plus the whole schema
    # script re-executed on every one of those reruns.
    # The previous db object is left to reference counting: a scan already
    # started holds its own reference, and closing the connection out from
    # under that thread would abort it mid-write.
    db_holder = st.session_state.setdefault('db_holder', {})
    if st.session_state.get('db_path') != db_path:
        db_holder['db'] = SearchDatabase(db_path)
        st.session_state['db_path'] = db_path
        st.session_state.pop('indexer_key', None)   # rebuild against the new db
    db = db_holder['db']
    st.session_state['db'] = db

    indexer_key = (db_path, hash_md5, hash_sha256, fast_hash)
    if st.session_state.get('indexer_key') != indexer_key:
        st.session_state.pop('indexer', None)
        st.session_state.pop('search_engine', None)
    indexer = None
    search_engine = None
    if enable_indexing:
        if 'indexer' not in st.session_state:
            st.session_state['indexer'] = FileIndexer(
                db, ContentExtractor(),
                hash_md5=hash_md5, hash_sha256=hash_sha256,
                fast_hash_mb=(FAST_HASH_LIMIT / (1024 * 1024)) if fast_hash else 0.0
            )
            st.session_state['search_engine'] = create_search_engine(db)
        st.session_state['indexer_key'] = indexer_key
        indexer = st.session_state['indexer']
        search_engine = st.session_state['search_engine']

    # --- Scan control (pause / resume / cancel) ---
    controller: Optional[ScanController] = st.session_state.get('scan_controller')

    if run_scan and not scan_running:
        root = get_resolved_path(base_path)
        if not root.exists():
            st.error(f"Path '{base_path}' does not exist!")
        elif not hash_md5 and not hash_sha256 and not enable_indexing:
            st.error("Nothing to do: enable indexing or at least one hash type.")
        else:
            controller = ScanController()
            hash_opts = {
                'md5': hash_md5,
                'sha256': hash_sha256,
                'fast_limit': FAST_HASH_LIMIT if fast_hash else None
            }
            index_fn = None
            if enable_indexing:
                def index_fn(files, progress_callback, should_stop, is_paused):
                    # Reuses the scan's file list — no second directory walk
                    return indexer.index_files(
                        files,
                        workers=workers,
                        progress_callback=progress_callback,
                        should_stop=should_stop,
                        is_paused=is_paused,
                        skip_unchanged=persistent_index
                    )
            controller.start(root, max_depth, workers, hash_opts, index_fn)
            st.session_state['scan_controller'] = controller
            st.session_state['fast_hash_used'] = fast_hash
            st.rerun()

    if controller is not None:
        snap = controller.snapshot()
        if controller.is_active():
            phase_label = {"walk": "🚶 Walking directories", "hash": "🔐 Hashing files",
                           "index": "📚 Indexing files"}.get(snap['phase'], snap['phase'])
            status_label = {"running": "running", "paused": "PAUSED", "cancelling": "cancelling…"}.get(snap['status'], snap['status'])
            st.info(f"**Scan {status_label}** — {phase_label} — {snap['done']}/{snap['total']} — `{snap['current'][:90]}`")
            if snap['total']:
                st.progress(min(1.0, snap['done'] / snap['total']))

            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("⏸ Pause", disabled=snap['status'] != 'running', width='stretch'):
                    controller.pause()
                    st.rerun()
            with col2:
                if st.button("▶️ Resume", disabled=snap['status'] != 'paused', width='stretch'):
                    controller.resume()
                    st.rerun()
            with col3:
                if st.button("🛑 Cancel", disabled=snap['status'] == 'cancelling', width='stretch'):
                    controller.cancel()
                    st.rerun()

            time.sleep(0.4)
            st.rerun()

        elif not getattr(controller, '_handled', False):
            # Scan finished — collect results
            controller._handled = True
            if controller.status == 'error':
                st.error(f"Scan failed:\n{controller.error}")
            if controller.results:
                df = pd.DataFrame(controller.results)
                st.session_state['scan_results'] = df
                st.session_state['scan_root'] = get_resolved_path(base_path)
                _clear_duplicate_keepers()   # row labels are meaningless for the new frame
            if controller.index_stats is not None:
                st.session_state['index_stats'] = controller.index_stats
            stats = controller.index_stats
            if controller.status == 'done':
                st.success(f"Scan complete: {len(controller.results)} files."
                           + (f" Indexed {stats['indexed']} (text extracted from {stats['content_extracted']}, "
                              f"{stats.get('skipped', 0)} unchanged files skipped)." if stats else ""))
            if stats:
                if stats.get('content_skipped'):
                    limit_mb = int((indexer.max_content_size if indexer else 10 * 1024 * 1024)
                                   / (1024 * 1024))
                    st.info(f"{stats['content_skipped']} file(s) over {limit_mb} MB are listed with "
                            "metadata only — their text was not read, so a scan can never block on a "
                            "huge file.")
                if stats.get('errors'):
                    samples = stats.get('error_samples') or []
                    st.warning(f"{stats['errors']} file(s) could not be indexed."
                               + (f" Examples: {'; '.join(samples)}" if samples else ""))
            elif controller.status == 'cancelled':
                st.warning("Scan cancelled. Partial results kept.")
            time.sleep(0.5)
            st.rerun()

    # --- Main UI Tabs ---
    # --- Main UI Tabs ---
    pending_alerts = db.get_unacknowledged_alert_count()
    alert_label = f"👀 Watch ({pending_alerts})" if pending_alerts else "👀 Watch"
    # Stores run schema/derived-column checks on construction, so they are kept
    # across reruns and only replaced when the index path changes.
    if not isinstance(st.session_state.get('saved_store'), SavedSearchStore) or \
            st.session_state.get('store_db_path') != db_path:
        st.session_state['saved_store'] = SavedSearchStore(db)
        st.session_state['ingest_store'] = IngestionStore(db)
        st.session_state['store_db_path'] = db_path
    saved_store = st.session_state['saved_store']
    new_runs = saved_store.unseen_runs(st.session_state.get('sched_seen_id', 0))
    search_label = f"🔍 Search ({new_runs})" if new_runs else "🔍 Search"
    ingest_store = st.session_state['ingest_store']
    main_tab1, main_tab2, main_tab3, main_tab4, main_tab6, main_tab5 = st.tabs(
        ["📁 File Explorer", search_label, "📥 Ingestion", "📊 Storage", "🗄️ Database", alert_label]
    )

    # ==================== FILE EXPLORER ====================
    with main_tab1:
        if 'scan_results' not in st.session_state and db.get_file_count():
            # Fresh launch with a populated index: show the saved index
            # instead of an empty screen (the Storage tab already reads it).
            rows = db.get_all_files_meta()
            st.session_state['scan_results'] = pd.DataFrame(rows)
            try:
                st.session_state['scan_root'] = Path(os.path.commonpath(
                    [r['full_path'] for r in rows]))
            except ValueError:
                pass  # mixed drives (Windows); Tree tab falls back to cwd
        if 'scan_results' in st.session_state:
            df = st.session_state['scan_results']
            root = st.session_state.get('scan_root', Path.cwd())

            st.subheader(f"📁 Files from: {root}")

            tab1, tab2, tab3 = st.tabs(["📄 File List", "👯 Duplicates", "🌳 Tree View"])

            with tab1:
                st.subheader("All Files")
                st.dataframe(df, width='stretch')

                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("Download CSV Report", csv, "file_report.csv", "text/csv")

                st.divider()
                st.subheader("🗂 File Actions")
                options = df['full_path'].tolist()
                selected = st.selectbox("Choose a file to open or reveal", options, index=None,
                                        placeholder="Search the list…")
                if selected:
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("📂 Open file", key="open_file", width='stretch'):
                            try:
                                open_path(selected)
                                st.success(f"Opened: {selected}")
                            except Exception as e:
                                st.error(f"Could not open: {e}")
                    with col2:
                        if st.button("📍 Reveal in Finder / Explorer", key="reveal_file", width='stretch'):
                            try:
                                reveal_path(selected)
                                st.success(f"Revealed: {selected}")
                            except Exception as e:
                                st.error(f"Could not reveal: {e}")

            with tab2:
                # Pick the best available hash column for duplicate detection
                hash_col = None
                if 'sha256' in df.columns and (df['sha256'] != '').any():
                    hash_col = 'sha256'
                elif 'md5' in df.columns and (df['md5'] != '').any():
                    hash_col = 'md5'

                if hash_col is None:
                    st.info("No hashes were computed for this scan. Enable MD5 or SHA256 in the sidebar Hashing Options to detect duplicates.")
                else:
                    fast_mode = bool(st.session_state.get('fast_hash_used'))
                    mode_note = " (fast mode: first 1 MB + size — rare false positives possible)" if fast_mode else ""
                    keys = [hash_col] + (['size_bytes'] if fast_mode else [])
                    grouped = df[df[hash_col] != ''].groupby(keys)
                    dup_groups = [g for _, g in grouped if len(g) > 1]

                    st.subheader(f"👯 Duplicate Groups ({len(dup_groups)}){mode_note}")

                    if not dup_groups:
                        st.write("No duplicate files found.")
                    else:
                        action = st.radio(
                            "Action on non-kept duplicates",
                            ["Move to Trash", "Move to backup folder"],
                            horizontal=True,
                            help="Trash uses the OS Recycle Bin / Trash. Backup copies the file to a folder (mirroring its path) and removes the original."
                        )
                        backup_path = ""
                        if action == "Move to backup folder":
                            backup_path = st.text_input(
                                "Backup folder", value="~/verbosefc_backups",
                                help="Duplicates are copied here (relative paths preserved), then the originals are removed."
                            )
                        if len(dup_groups) > DUP_GROUPS_SHOWN:
                            st.warning(
                                f"Only the first {DUP_GROUPS_SHOWN} of {len(dup_groups)} groups are "
                                "listed and actionable in this pass — the others are left alone.")
                        confirm = st.checkbox("✅ I confirm: move the selected duplicate copies",
                                              key="dup_confirm")
                        # The Apply button lives next to the confirmation checkbox,
                        # not at the bottom of a list that can run 100 groups long:
                        # ticking the box then scrolling back down hunting for the
                        # button was the old two-scroll-trip flow. Which groups are
                        # ticked comes from session state (the group checkboxes are
                        # drawn below), so Apply can still be disabled when nothing
                        # is picked — st.session_state already holds them this run.

                        pending_keys = [k for k in list(st.session_state.keys())
                                        if k.startswith('dupgrp_') and st.session_state[k]]
                        if pending_keys:
                            n_copies = 0
                            for g in dup_groups[:DUP_GROUPS_SHOWN]:
                                if st.session_state.get(f"dupgrp_{_dup_group_key(g)}", False):
                                    n_copies += len(g) - 1
                            dest = ('trashed' if action == 'Move to Trash'
                                    else 'moved to ' + backup_path)
                            st.warning(f"{n_copies} duplicate copies in {len(pending_keys)} "
                                       f"group(s) will be **{dest}**. The kept copy stays in place.")
                        apply_clicked = st.button(
                            "🚚 Apply to selected groups", type="primary",
                            disabled=not confirm or not pending_keys, key="dup_apply")
                        if pending_keys and not confirm:
                            st.info("Tick the confirmation checkbox above to enable the action.")

                        # Result of an apply that already reran the page: show it
                        # here, above the (rebuilt) group list.
                        apply_note = st.session_state.pop('dup_apply_note', None)
                        if apply_note:
                            moved, failed, messages = apply_note
                            st.success(f"Done: {moved} moved, {failed} failed.")
                            for m in messages[:50]:
                                st.caption(m)

                        def _apply_duplicate_groups():
                            """Move the non-kept copies of every ticked group."""
                            nonlocal df
                            moved, failed = 0, 0
                            messages = []
                            for i, group in enumerate(dup_groups[:DUP_GROUPS_SHOWN]):
                                gkey = _dup_group_key(group)
                                if not st.session_state.get(f'dupgrp_{gkey}', False):
                                    continue
                                keeper_idx = st.session_state.get(f'dupkeep_{gkey}')
                                labels = [int(x) for x in group.index.tolist()]
                                # Resolve the radio value to a path. A label that
                                # is stale (the frame changed since the radio was
                                # drawn) resolves to no keeper at all.
                                keeper_path = None
                                if (keeper_idx is not None and int(keeper_idx) in labels
                                        and int(keeper_idx) in df.index):
                                    keeper_path = df.loc[int(keeper_idx), 'full_path']
                                paths = [df.loc[idx, 'full_path'] for idx in group.index
                                         if idx in df.index]
                                targets, skip = duplicate_mover_targets(paths, keeper_path)
                                if skip:
                                    failed += 1
                                    messages.append(f"SKIPPED ({skip}): {group['name'].iloc[0]}")
                                    continue
                                for path in targets:
                                    try:
                                        if action == "Move to Trash":
                                            msg = move_to_trash(path)
                                        else:
                                            msg = backup_move(path, backup_path, str(root))
                                        messages.append(msg)
                                        moved += 1
                                        # Filter, not drop(index=...): the row labels of
                                        # the other groups stay valid for this pass.
                                        df = df[df['full_path'] != path]
                                    except Exception as e:
                                        failed += 1
                                        messages.append(f"FAILED: {path} — {e}")
                            st.session_state['scan_results'] = df
                            if moved or failed:
                                # Rebuild the group list from the filtered frame and
                                # carry the report across the rerun, so a trashed
                                # copy never lingers in an expanded group. Keeper/act
                                # selections are dropped only for groups that were
                                # actually acted on (their row-label ids belong to
                                # the pre-move frame); untouched groups keep their
                                # tick and keeper choice.
                                for g in dup_groups[:DUP_GROUPS_SHOWN]:
                                    gk = _dup_group_key(g)
                                    if st.session_state.get(f'dupgrp_{gk}', False):
                                        for pfx in ('dupkeep_', 'dupgrp_'):
                                            st.session_state.pop(pfx + gk, None)
                                st.session_state['dup_apply_note'] = (moved, failed, messages)
                                st.rerun()

                        if apply_clicked:
                            _apply_duplicate_groups()

                        # Shorten long digests so the per-copy table stays readable.
                        def _short_hash(h: str) -> str:
                            return h[:12] + '…' if len(h) > 16 else h

                        total_selected = 0
                        for i, group in enumerate(dup_groups[:DUP_GROUPS_SHOWN]):
                            first = group.iloc[0]
                            gkey = _dup_group_key(group)
                            n = len(group)
                            with st.expander(f"🔁 {n} copies — {first['size_human']} — {first['name']}"):
                                # Per-copy details: the hash that grouped these files,
                                # plus size and modified time for every copy. In fast
                                # mode a group is only a candidate duplicate (first 1 MB
                                # + size), and unreadable files share the literal
                                # 'ERROR' hash — both are visible here before any move.
                                shown = group.copy()
                                shown['hash'] = shown[hash_col].map(_short_hash)
                                cols = ['full_path', 'size_human', 'hash']
                                if 'modified_at' in shown.columns:
                                    cols.append('modified_at')
                                st.dataframe(
                                    shown[cols],
                                    hide_index=True,
                                    column_config={
                                        'full_path': st.column_config.TextColumn("Path"),
                                        'size_human': st.column_config.TextColumn("Size"),
                                        'hash': st.column_config.TextColumn(hash_col.upper()),
                                        'modified_at': st.column_config.TextColumn("Modified"),
                                    },
                                    width='stretch',
                                )
                                # Oldest file is kept by default
                                keeper_default = int(group['modified_at_epoch'].idxmin())
                                keeper = st.radio(
                                    "Keep this copy",
                                    group.index.tolist(),
                                    index=group.index.tolist().index(keeper_default),
                                    format_func=lambda idx, g=group: f"{g.loc[idx, 'full_path']}  (modified {time.ctime(g.loc[idx, 'modified_at_epoch'])})",
                                    key=f"dupkeep_{gkey}"
                                )
                                act = st.checkbox("Act on this group (move the other copies)", key=f"dupgrp_{gkey}")

                        st.divider()
                        st.subheader("Filename Collision (Same name, different content)")
                        name_groups = df[df[hash_col] != ''].groupby('name')[hash_col].nunique()
                        colliding_names = name_groups[name_groups > 1].index
                        dupes_name = df[df['name'].isin(colliding_names)]
                        if not dupes_name.empty:
                            st.dataframe(dupes_name)
                        else:
                            st.write("No filename collisions found.")

            with tab3:
                st.subheader("Directory Structure")
                # Build the tree from the scan results already in memory —
                # no second filesystem walk (avoids hangs on cloud/network folders)
                tree: Dict = {}
                for p in df['full_path']:
                    try:
                        rel = Path(p).relative_to(root)
                    except ValueError:
                        continue
                    node = tree
                    for part in rel.parts[:-1]:
                        node = node.setdefault(part, {})
                    node[rel.name] = None

                def render_tree(node: Dict, indent: str = "") -> str:
                    out = ""
                    entries = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0].lower()))
                    for name, child in entries:
                        if child is None:
                            out += f"{indent}📄 {name}\n"
                        else:
                            out += f"{indent}📁 {name}\n"
                            out += render_tree(child, indent + "    ")
                    return out

                st.code(render_tree(tree) or "(empty)", height=600)
        else:
            st.info("Run a scan from the sidebar to see your files here.")

    # ==================== SEARCH ====================
    with main_tab2:
        render_search_tab(db, search_engine, saved_store)

    # ==================== INGESTION ====================
    with main_tab3:
        st.subheader("📥 Ingestion — indexes, sourcetypes & field extractions")
        render_ingestion_tab(db, ingest_store)

    # ==================== STORAGE DASHBOARD ====================
    with main_tab4:
        st.subheader("📊 Storage Dashboard")

        storage_df = None
        if 'scan_results' in st.session_state:
            storage_df = st.session_state['scan_results']
            source_note = "from the latest scan"
        elif enable_indexing:
            all_files = db.get_all_files_meta()
            if all_files:
                storage_df = pd.DataFrame(all_files)
                source_note = "from the persistent index"

        if storage_df is None or storage_df.empty:
            st.info("Run a scan to populate the storage dashboard.")
        else:
            sdf = storage_df.copy()
            total_bytes = int(sdf['size_bytes'].sum())

            overview_tab, diskmap_tab = st.tabs(["📈 Overview", "🗺️ Disk Map"])

            with overview_tab:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Files", f"{len(sdf):,}")
                m2.metric("Total Size", get_human_size(total_bytes))
                m3.metric("Largest File", get_human_size(int(sdf['size_bytes'].max())))
                avg_ext = sdf['name'].str.lower().str.rsplit('.', n=1).str[-1]
                common = avg_ext.value_counts()
                m4.metric("Most Common Type", f".{common.index[0]}" if len(common) else "—")

                st.divider()
                col1, col2 = st.columns(2)

                with col1:
                    st.write("**Size by extension (top 15)**")
                    ext_sizes = sdf.assign(ext=avg_ext).groupby('ext')['size_bytes'].sum() \
                        .sort_values(ascending=False).head(15) / (1024 * 1024)
                    st.bar_chart(ext_sizes)

                with col2:
                    st.write("**File count by extension (top 15)**")
                    ext_counts = avg_ext.value_counts().head(15)
                    st.bar_chart(ext_counts)

                st.divider()
                col1, col2 = st.columns(2)
                with col1:
                    st.write("**Top 10 largest files**")
                    top10 = sdf.nlargest(10, 'size_bytes')[['name', 'size_human', 'full_path']]
                    st.dataframe(top10, width='stretch', hide_index=True)
                with col2:
                    if 'modified_at_epoch' in sdf.columns:
                        st.write("**Files by month modified (last 24 months)**")
                        months = pd.to_datetime(sdf['modified_at_epoch'], unit='s').dt.to_period('M').astype(str)
                        recent = months[months >= (pd.Timestamp.now() - pd.DateOffset(months=23)).strftime('%Y-%m')]
                        st.bar_chart(recent.value_counts().sort_index())

            with diskmap_tab:
                st.subheader("🗺️ Disk Map")
                st.caption("Sunburst of folder sizes — pick a folder below the chart to "
                           "redraw it centered on that folder (largest slices first).")
                render_disk_map(sdf)

    # ==================== WATCH DASHBOARD ====================
    with main_tab5:
        st.subheader("👀 Folder Change Monitoring")

        if 'watch_paths' not in st.session_state:
            st.session_state['watch_paths'] = []

        watcher = st.session_state.get('watcher')
        if watcher is not None and not watcher.is_alive():
            st.session_state['watcher'] = watcher = None

        # Same native folder picker as the sidebar Base Path: browse instead
        # of typing, with the text box kept for typed paths and headless runs.
        if 'picked_watch' not in st.session_state:
            st.session_state['picked_watch'] = ""
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            new_watch = st.text_input(
                "Folder to watch", value=st.session_state['picked_watch'],
                placeholder="~/Documents or C:\\Users\\you\\Downloads")
        with col2:
            # One spacer line only: the text input next door has a label row
            # and the bare button does not, so two spacers overshot and left
            # Browse hanging below the field.
            st.write("")
            watch_browse_ok = True
            if st.button("📁 Browse…", width='stretch', key="watch_browse",
                         help="Opens the native Finder / Explorer folder picker"):
                chosen = pick_folder_dialog(new_watch)
                if chosen:
                    st.session_state['picked_watch'] = chosen
                    st.rerun()
                elif not st.session_state['picked_watch']:
                    watch_browse_ok = False
        if not watch_browse_ok:
            st.caption("Folder picker unavailable (no desktop session) — type the path instead.")
        with col3:
            # One spacer line only, matching the Browse column above.
            st.write("")
            if st.button("➕ Add folder", width='stretch'):
                p = get_resolved_path(new_watch) if new_watch else None
                if p and p.is_dir():
                    if str(p) not in st.session_state['watch_paths']:
                        st.session_state['watch_paths'].append(str(p))
                        if watcher:
                            try:
                                watcher.watch(str(p))
                            except Exception as e:
                                st.error(f"Could not watch {p}: {e}")
                        st.success(f"Added: {p}")
                    else:
                        st.info("Already in the watch list.")
                else:
                    st.error("Folder does not exist.")

        # Start / stop monitoring
        if watcher is None:
            if st.button("🟢 Start monitoring", type="primary", disabled=not st.session_state['watch_paths']):
                def on_event(event_type, path, name, watch_path):
                    # Resolved at event time: this callback outlives the render
                    # that created it, and the index object can be swapped when
                    # the Persistent Index setting changes.
                    # Failures are kept instead of swallowed, so a Watch tab
                    # badge that never moves can actually be diagnosed.
                    try:
                        db_holder['db'].add_alert(event_type, path, name, watch_path)
                    except Exception as e:
                        errors = db_holder.setdefault('watch_errors', [])
                        if len(errors) < 20:
                            errors.append(f"{event_type} {path}: {type(e).__name__}: {e}")
                watcher = FolderWatcher(on_event)
                st.session_state['watcher'] = watcher
                for wp in st.session_state['watch_paths']:
                    try:
                        watcher.watch(wp)
                    except Exception as e:
                        st.error(f"Could not watch {wp}: {e}")
                st.rerun()
            st.info("Monitoring is stopped. Add folders above, then start monitoring.")
        else:
            st.success(f"🟢 Monitoring {len(watcher.watched_paths())} folder(s) in real time.")
            watch_errors = db_holder.get('watch_errors') or []
            if watch_errors:
                st.warning(f"{len(watch_errors)} change event(s) could not be recorded "
                           "(last 20 kept). Latest: " + " | ".join(watch_errors[-3:]))
            if st.button("🔴 Stop monitoring"):
                watcher.stop()
                st.session_state['watcher'] = None
                st.rerun()

        # Manage watched folders
        if st.session_state['watch_paths']:
            st.write("**Watched folders:**")
            for wp in list(st.session_state['watch_paths']):
                c1, c2 = st.columns([5, 1])
                with c1:
                    st.write(f"📁 `{wp}`")
                with c2:
                    if st.button("Remove", key=f"unwatch_{wp}"):
                        st.session_state['watch_paths'].remove(wp)
                        if watcher:
                            for wid, w in list(watcher.watches.items()):
                                if w['path'] == wp:
                                    watcher.unwatch(wid)
                        st.rerun()

        st.divider()
        st.subheader("🚨 Alerts")

        @st.fragment(run_every=3.0)
        def alerts_section():
            only_pending = st.checkbox("Unacknowledged only", value=False, key="alerts_only_pending")
            alerts = db.get_alerts(limit=200, include_acknowledged=not only_pending)

            pending = db.get_unacknowledged_alert_count()
            a1, a2, a3 = st.columns(3)
            a1.metric("Unacknowledged", pending)
            a2.metric("Showing", len(alerts))
            with a3:
                if st.button("✔ Acknowledge all", disabled=pending == 0, width='stretch'):
                    n = db.acknowledge_all_alerts()
                    st.success(f"Acknowledged {n} alert(s).")
                    st.rerun()

            if alerts:
                adf = pd.DataFrame(alerts)[['created_at', 'event_type', 'name', 'path', 'acknowledged']]
                st.dataframe(adf, width='stretch', hide_index=True)
                if st.button("🧹 Clear all alerts"):
                    n = db.clear_alerts()
                    st.success(f"Cleared {n} alert(s).")
                    st.rerun()
            else:
                st.write("No alerts yet. Start monitoring and change something in a watched folder.")

        alerts_section()

    # ==================== DATABASE DASHBOARD ====================
    with main_tab6:
        st.subheader("🗄️ Database Dashboard")
        st.caption(f"Stats for the SQLite file backing this session: `{db_path}`")

        dbstats = collect_db_stats(db_path)
        if not dbstats:
            if Path(db_path).exists():
                st.error("Could not read this database file — it may be corrupt. "
                         "Check it with a SQLite tool, or delete it to rebuild the index.")
            else:
                st.info("The database file does not exist yet — run a scan with indexing on.")
        else:
            if st.button("🔄 Refresh stats"):
                st.rerun()

            # --- Headline metrics ---
            d1, d2, d3, d4, d5 = st.columns(5)
            d1.metric("File size", get_human_size(dbstats['file_bytes']))
            d2.metric("Actual used", get_human_size(dbstats['used_bytes']),
                      help="page_count minus freelist — the rest is reclaimed space SQLite keeps for reuse.")
            d3.metric("Free (reusable)", get_human_size(dbstats['free_pages'] * dbstats['page_size']))
            d4.metric("Integrity", str(dbstats['integrity']))
            d5.metric("Journal mode", str(dbstats['journal_mode']))

            rc = dbstats['row_counts']
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Indexed files", f"{rc.get('files', 0):,}")
            r2.metric("FTS documents", f"{rc.get('content', 0):,}")
            r3.metric("Search history", f"{rc.get('search_history', 0):,}")
            r4.metric("Watch alerts", f"{rc.get('watch_alerts', 0):,}")

            st.divider()

            # --- Size composition: circle charts ---
            size_tab, activity_tab, tables_tab = st.tabs(
                ["🧿 Size & Composition", "📈 Activity", "🧱 Tables & Integrity"])

            def _consolidate_slices(rows, label_col: str, value_col: str,
                                    min_pct: float = 1.0):
                """Fold slices smaller than min_pct of the total into one 'Other'.

                A pie with ~20 slivers stacks its outside labels into an
                unreadable pile (and the legend runs off the chart) — which is
                exactly what the raw per-table and per-category breakdowns do.
                Keeps at least the 3 largest slices so tiny charts stay intact.
                """
                rows = sorted(rows, key=lambda r: -r[value_col])
                total = sum(r[value_col] for r in rows) or 1
                keep = [r for r in rows if r[value_col] / total * 100 >= min_pct]
                rest = [r for r in rows if r not in keep]
                if len(keep) < 3:
                    keep = rows[:3]
                    rest = rows[3:]
                if rest:
                    merged = {label_col: f"Other ({len(rest)})", value_col: sum(r[value_col] for r in rest)}
                    keep.append(merged)
                return keep

            def _donut(rows, label_col: str, value_col: str, unit: str):
                """Donut with one readable inside label per slice (no legend)."""
                dfc = pd.DataFrame(rows)
                # textposition/textinfo are go.Pie attributes, not px.pie kwargs —
                # apply them to the trace after building the figure.
                fig = px.pie(dfc, names=label_col, values=value_col, hole=0.45)
                # Contrast pass: Plotly's default pie text is light gray and
                # disappears on its own pastel slices (and Streamlit's dark theme
                # puts a white strip behind the legend). Big slices get their name
                # + percent in dark text; slices too small to hold text show only
                # the percent, and the legend carries the names. texttemplate is
                # per-point arrayOk, unlike textinfo — that's how we vary it.
                total = sum(r[value_col] for r in rows) or 1
                pcts = [r[value_col] / total * 100 for r in rows]
                # These are plain strings, not f-strings: doubling the braces
                # here is what rendered literal "%{{label}}" text on the chart.
                templates = ['%{label}<br>%{percent:.1f}%' if p >= 8 else '%{percent:.1f}%'
                             for p in pcts]
                fig.update_traces(
                    textposition='inside',
                    insidetextfont=dict(color='#111111', size=13),
                    texttemplate=templates,
                )
                fig.update_layout(
                    legend=dict(font=dict(size=11, color='#dddddd'),
                                orientation='h', yanchor='bottom',
                                bgcolor='rgba(0,0,0,0)'),
                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig, width='stretch')

            with size_tab:
                c1, c2 = st.columns(2)
                with c1:
                    st.write("**Where the bytes live**")
                    if dbstats['per_object']:
                        # Group per-object pages into logical buckets.
                        buckets: Dict[str, int] = defaultdict(int)
                        for name, nbytes in dbstats['per_object']:
                            if name.startswith('content_') or name == 'content':
                                buckets['FTS content index'] += nbytes or 0
                            elif name.startswith('sqlite_autoindex'):
                                buckets['Auto indexes'] += nbytes or 0
                            elif name.startswith('idx_'):
                                buckets['Indexes'] += nbytes or 0
                            else:
                                buckets[name] += nbytes or 0
                        rows = [{'component': k, 'bytes': v}
                                for k, v in buckets.items() if v > 0]
                        _donut(_consolidate_slices(rows, 'component', 'bytes'),
                               'component', 'bytes', 'bytes')
                    else:
                        fts_b = dbstats['fts_bytes']
                        used = max(dbstats['used_bytes'], 1)
                        sdf = pd.DataFrame(
                            [('FTS content index', fts_b), ('Other', max(used - fts_b, 0))],
                            columns=['component', 'bytes'])
                        st.plotly_chart(px.pie(sdf, names='component', values='bytes', hole=0.45),
                                        width='stretch')
                        st.caption("This Python's SQLite lacks the dbstat module — showing a coarse split.")

                with c2:
                    st.write("**File categories (count)**")
                    if dbstats['by_category']:
                        cat_rows = [{'category': c or '(none)', 'count': n}
                                    for c, n, _b in dbstats['by_category']]
                        _donut(_consolidate_slices(cat_rows, 'category', 'count'),
                               'category', 'count', 'files')
                    else:
                        st.write("No indexed files yet.")

                st.write("**Top extensions by count**")
                if dbstats['by_extension']:
                    ext = pd.DataFrame(dbstats['by_extension'],
                                       columns=['extension', 'count', 'bytes'])
                    bar = px.bar(ext, x='extension', y='count', labels={'count': 'files'})
                    st.plotly_chart(bar, width='stretch')

                cc = dbstats['content_coverage']
                if cc:
                    st.write("**Content extraction coverage**")
                    cov = pd.DataFrame(
                        [('Text extracted', cc.get('1', 0)), ('Metadata only', cc.get('0', 0))],
                        columns=['state', 'files'])
                    covfig = px.pie(cov, names='state', values='files', hole=0.5,
                                     color_discrete_sequence=['#2ca02c', '#bbbbbb'])
                    # Same contrast pass as the donuts above: black in-slice and
                    # legend text so nothing goes white-on-white or gray-on-pastel.
                    covfig.update_traces(
                        textposition='inside',
                        insidetextfont=dict(color='#111111', size=13),
                        texttemplate='%{percent:.1f}%')
                    covfig.update_layout(
                        legend=dict(font=dict(size=11, color='#dddddd'),
                                    bgcolor='rgba(0,0,0,0)'),
                        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(covfig, width='stretch')

            with activity_tab:
                if dbstats['searches_by_day']:
                    st.write("**Searches per day**")
                    sdf = pd.DataFrame(dbstats['searches_by_day'], columns=['day', 'searches'])
                    sdf['day'] = pd.to_datetime(sdf['day'])
                    st.plotly_chart(px.line(sdf, x='day', y='searches',
                                            labels={'searches': 'search runs'}),
                                    width='stretch')
                else:
                    st.write("No search history yet.")

                if dbstats['top_queries']:
                    st.write("**Most frequent queries**")
                    tq = pd.DataFrame(dbstats['top_queries'],
                                      columns=['query', 'runs', 'avg_results'])
                    # orientation='h' was silently ignored by this Plotly
                    # (renamed in v6), so the long query strings ended up as
                    # cramped category labels on the x axis with all bars
                    # pinned at 1 — it read like a single merged bar. Use an
                    # explicit vertical bar chart: one bar per query, query
                    # text in hover, and truncated tick labels for legibility.
                    tq['label'] = tq['query'].str.slice(0, 28).where(
                        tq['query'].str.len() <= 28,
                        tq['query'].str.slice(0, 28) + '…')
                    tqfig = px.bar(tq, x='label', y='runs',
                                   labels={'label': 'query', 'runs': 'runs'},
                                   hover_data={'query': True, 'label': False})
                    tqfig.update_layout(xaxis_title=None)
                    st.plotly_chart(tqfig, width='stretch')

                if dbstats['file_activity_by_day']:
                    st.write("**Indexed files modified in the last 90 days**")
                    fas = pd.DataFrame(dbstats['file_activity_by_day'], columns=['day', 'files'])
                    fas['day'] = pd.to_datetime(fas['day'])
                    st.plotly_chart(px.area(fas, x='day', y='files'), width='stretch')

                if dbstats['alerts_by_type']:
                    c1, c2 = st.columns(2)
                    with c1:
                        st.write("**Watch alerts by event type**")
                        abt = pd.DataFrame(dbstats['alerts_by_type'], columns=['event', 'count'])
                        st.plotly_chart(px.bar(abt, x='event', y='count'), width='stretch')
                    with c2:
                        st.write("**Alerts per day**")
                        abd = pd.DataFrame(dbstats['alerts_by_day'], columns=['day', 'count'])
                        abd['day'] = pd.to_datetime(abd['day'])
                        st.plotly_chart(px.line(abd, x='day', y='count'), width='stretch')

            with tables_tab:
                st.write("**Row counts per table**")
                st.bar_chart(pd.Series(rc).sort_values(ascending=False))

                if dbstats['per_object']:
                    st.write("**Storage per table / index (MB)**")
                    pos = [(n, (b or 0) / (1024 * 1024)) for n, b in dbstats['per_object'] if (b or 0) > 0]
                    pos.sort(key=lambda t: -t[1])
                    obj_df = pd.DataFrame(pos[:30], columns=['object', 'MB'])
                    # orientation='h' is silently ignored by Plotly v6+ (the
                    # option was renamed), so the long object names piled up as
                    # overlapping rotated x-labels with hairline bars. Passing
                    # y=object / x=MB gives a true horizontal bar chart: each
                    # object gets its own row, no rotated text to collide.
                    fig = px.bar(obj_df, y='object', x='MB')
                    fig.update_traces(marker_color='#6ea8dc')
                    fig.update_layout(yaxis_title=None, xaxis_title='MB',
                                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(fig, width='stretch')

                if dbstats['sidecar_bytes']:
                    st.write("**Sidecar files** (present next to the .db):")
                    for suffix, nbytes in dbstats['sidecar_bytes'].items():
                        st.caption(f"`{db_path}{suffix}` — {get_human_size(nbytes)}")

                if rc.get('files', 0) > 0 and dbstats['free_pages'] * dbstats['page_size'] > 50 * 1024 * 1024:
                    st.info(
                        f"{get_human_size(dbstats['free_pages'] * dbstats['page_size'])} of the file "
                        "is free space SQLite keeps for reuse after deletes. A one-time "
                        "`VACUUM` (any SQLite tool) reclaims it.")

                if dbstats['integrity'] != 'ok':
                    st.error(f"quick_check reported: {dbstats['integrity']} — consider rebuilding the index.")


if __name__ == "__main__":
    main()
