import os
import time
import json
import base64
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from content_extractor import ContentExtractor
from db import SearchDatabase
from indexer import FileIndexer
from search_engine import SearchEngine, create_search_engine
from enhanced_features import AdvancedSearch, SearchCache, IncrementalIndexer, SearchHistory
from ux_enhancements import SearchSuggestions, SearchFeedback, VisualEnhancements
from scan_runner import ScanController, get_human_size
from file_actions import open_path, reveal_path, move_to_trash, backup_move
from watcher import FolderWatcher

# --- Constants ---

PERSISTENT_DB_PATH = Path.home() / ".verbosefc" / "verbosefc_index.db"
FAST_HASH_LIMIT = 1024 * 1024  # first 1 MB when fast hashing is enabled
MAX_PDF_PREVIEW_BYTES = 20 * 1024 * 1024


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

def date_range_bounds(choice: str):
    """Map the Date Range filter to (min_epoch, max_epoch) or (None, None)."""
    if choice == "Any time":
        return None, None
    now = datetime.now()
    if choice == "Today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif choice == "Yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (now - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif choice == "Last 7 days":
        start = now - timedelta(days=7)
        end = now
    elif choice == "Last 30 days":
        start = now - timedelta(days=30)
        end = now
    else:
        return None, None
    return start.timestamp(), end.timestamp()


# --- Main App ---

def main():
    st.set_page_config(page_title="File Discovery Dashboard", layout="wide")
    st.title("📂 File Discovery & Management")

    with st.sidebar:
        st.header("Settings")
        base_path = st.text_input("Base Path", value="~", help="Root directory to scan and index")
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

        run_scan = st.button("Scan & Index Directory", type="primary")

    # --- Database / services ---
    if persistent_index:
        PERSISTENT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_path = str(PERSISTENT_DB_PATH)
    else:
        db_path = os.path.join(tempfile.gettempdir(), f"verboseFC_{os.getpid()}.db")

    db = SearchDatabase(db_path)
    st.session_state['db'] = db
    st.session_state['db_path'] = db_path

    indexer = None
    search_engine = None
    if enable_indexing:
        extractor = ContentExtractor()
        indexer = FileIndexer(
            db, extractor,
            hash_md5=hash_md5, hash_sha256=hash_sha256,
            fast_hash_mb=(FAST_HASH_LIMIT / (1024 * 1024)) if fast_hash else 0.0
        )
        st.session_state['indexer'] = indexer
        search_engine = create_search_engine(db)
        st.session_state['search_engine'] = search_engine

    # --- Scan control (pause / resume / cancel) ---
    controller: Optional[ScanController] = st.session_state.get('scan_controller')

    if run_scan:
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
            if controller.index_stats is not None:
                st.session_state['index_stats'] = controller.index_stats
                st.session_state['indexed_files'] = db.get_all_files()
            if controller.status == 'done':
                st.success(f"Scan complete: {len(controller.results)} files."
                           + (f" Index stats: {controller.index_stats}" if controller.index_stats else ""))
            elif controller.status == 'cancelled':
                st.warning("Scan cancelled. Partial results kept.")
            time.sleep(0.5)
            st.rerun()

    # --- Main UI Tabs ---
    pending_alerts = db.get_unacknowledged_alert_count()
    alert_label = f"👀 Watch ({pending_alerts})" if pending_alerts else "👀 Watch"
    main_tab1, main_tab2, main_tab3, main_tab4 = st.tabs(
        ["📁 File Explorer", "🔍 Search", "📊 Storage", alert_label]
    )

    # ==================== FILE EXPLORER ====================
    with main_tab1:
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
                        confirm = st.checkbox("✅ I confirm: move the selected duplicate copies",
                                              key="dup_confirm")

                        total_selected = 0
                        for i, group in enumerate(dup_groups[:100]):
                            first = group.iloc[0]
                            n = len(group)
                            with st.expander(f"🔁 {n} copies — {first['size_human']} — {first['name']}"):
                                st.caption(first['full_path'])
                                # Oldest file is kept by default
                                keeper_default = int(group['modified_at_epoch'].idxmin())
                                keeper = st.radio(
                                    "Keep this copy",
                                    group.index.tolist(),
                                    index=group.index.tolist().index(keeper_default),
                                    format_func=lambda idx, g=group: f"{g.loc[idx, 'full_path']}  (modified {time.ctime(g.loc[idx, 'modified_at_epoch'])})",
                                    key=f"dupkeep_{i}"
                                )
                                act = st.checkbox("Act on this group (move the other copies)", key=f"dupgrp_{i}")
                                if act:
                                    total_selected += n - 1

                        if total_selected > 0:
                            st.warning(f"{total_selected} duplicate copies will be **{('trashed' if action == 'Move to Trash' else 'moved to ' + backup_path)}**. The kept copy stays in place.")
                            if not confirm:
                                st.info("Tick the confirmation checkbox above to enable the action.")
                            if st.button("🚚 Apply to selected groups", disabled=not confirm, type="primary"):
                                moved, failed = 0, 0
                                messages = []
                                for i, group in enumerate(dup_groups[:100]):
                                    if not st.session_state.get(f'dupgrp_{i}', False):
                                        continue
                                    keeper_idx = st.session_state.get(f'dupkeep_{i}')
                                    for idx in group.index:
                                        if idx == keeper_idx:
                                            continue
                                        path = df.loc[idx, 'full_path']
                                        try:
                                            if action == "Move to Trash":
                                                msg = move_to_trash(path)
                                            else:
                                                msg = backup_move(path, backup_path, str(root))
                                            messages.append(msg)
                                            moved += 1
                                            df = df.drop(index=idx)
                                        except Exception as e:
                                            failed += 1
                                            messages.append(f"FAILED: {path} — {e}")
                                st.session_state['scan_results'] = df
                                st.success(f"Done: {moved} moved, {failed} failed.")
                                for m in messages[:50]:
                                    st.caption(m)

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
        st.subheader("🔍 Natural Language Search")

        if not enable_indexing or not db:
            st.info("Enable indexing in the sidebar to use search functionality.")
        else:
            search_query = st.text_input(
                "Search files by name, content, or metadata...",
                placeholder="Search files by name, content, or metadata...",
                key="search_input"
            )

            col1, col2, col3 = st.columns([1, 1, 3])
            with col1:
                search_button = st.button("🔍 Search", use_container_width=False, width='stretch')
            with col2:
                clear_button = st.button("🗑️ Clear", use_container_width=False, width='stretch')

            if clear_button:
                st.session_state.pop('search_input', None)
                st.session_state.pop('search_results', None)
                st.session_state.pop('parsed_info', None)
                st.session_state.pop('current_page', None)
                st.rerun()

            with st.expander("🔍 Search Filters", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    filter_category = st.selectbox(
                        "File Category",
                        ["All"] + ["Document", "Image", "Video", "Audio", "Archive", "Code", "Data"]
                    )
                    filter_ext = st.text_input("Extension (e.g., .pdf, .txt)")
                    filter_min_size = st.number_input("Min Size (bytes)", min_value=0, value=0)
                with col2:
                    filter_max_size = st.number_input("Max Size (bytes)", min_value=0, value=0)
                    filter_date_range = st.selectbox(
                        "Date Range",
                        ["Any time", "Today", "Yesterday", "Last 7 days", "Last 30 days"]
                    )

            if 'parsed_info' not in st.session_state:
                st.session_state['parsed_info'] = {}
            should_search = search_button or (search_query and 'search_results' not in st.session_state)

            if should_search:
                results = []
                total = 0
                parsed_info = {}
                execution_time = 0.0

                if search_query:
                    import time as _time
                    start_time = _time.time()
                    offset = (st.session_state.get('current_page', 1) - 1) * 20
                    try:
                        results, total, parsed_info = search_engine.search(
                            search_query, limit=20, offset=offset
                        )
                        execution_time = _time.time() - start_time
                    except Exception as e:
                        st.error(f"Search error: {e}")
                        import traceback
                        st.error(traceback.format_exc())
                        results = []
                        total = 0
                else:
                    category_map = {
                        "Document": "document", "Image": "image", "Video": "video",
                        "Audio": "audio", "Archive": "archive", "Code": "code", "Data": "data"
                    }
                    cat_filter = category_map.get(filter_category) if filter_category != "All" else None
                    min_mod, max_mod = date_range_bounds(filter_date_range)

                    parsed_info = {
                        'keywords': [],
                        'phrases': [],
                        'operators': {'and': [], 'or': [], 'not': []},
                        'file_types': [],
                        'size_range': (None, None),
                        'dates': (min_mod, max_mod)
                    }

                    try:
                        results, total = db.search_files_by_metadata(
                            extension=filter_ext if filter_ext else None,
                            file_category=cat_filter,
                            min_size=filter_min_size if filter_min_size > 0 else None,
                            max_size=filter_max_size if filter_max_size > 0 else None,
                            min_modified=min_mod,
                            max_modified=max_mod
                        )
                    except Exception as e:
                        st.error(f"Search error: {e}")
                        results = []
                        total = 0

                st.session_state['search_results'] = {
                    'results': results,
                    'total': total,
                    'execution_time': execution_time,
                    'query': search_query
                }
                st.session_state['parsed_info'] = parsed_info

            if 'search_results' in st.session_state:
                results_data = st.session_state['search_results']
                results = results_data['results']
                total = results_data['total']
                execution_time = results_data['execution_time']

                search_performed = search_query or filter_category != "All" or filter_ext or filter_min_size > 0 or filter_max_size > 0 or filter_date_range != "Any time"

                if search_performed:
                    if results:
                        st.success(f"Found {total} result(s) in {execution_time:.3f} seconds")
                    elif search_query:
                        st.info(f"No results found for '{search_query}'")
                    elif filter_category != "All":
                        st.info(f"No results found for category: {filter_category}")
                    else:
                        st.info("No results found for the selected filters")

                    if 'parsed_info' in st.session_state and st.session_state['parsed_info']:
                        with st.expander("🔍 Query Analysis", expanded=False):
                            parsed = st.session_state['parsed_info']
                            st.json({
                                'keywords': parsed.get('keywords', []),
                                'phrases': parsed.get('phrases', []),
                                'operators': parsed.get('operators', {}),
                                'file_types': parsed.get('file_types', []),
                                'size_range': parsed.get('size_range', [])
                            })

                    page_size = 20
                    total_pages = (total + page_size - 1) // page_size
                    current_page = st.session_state.get('current_page', 1)
                    current_page = st.number_input(
                        "Page", min_value=1, max_value=max(1, total_pages), value=current_page, key="page_input"
                    )
                    st.session_state['current_page'] = current_page

                    offset = (current_page - 1) * page_size
                    start_idx = offset
                    end_idx = min(start_idx + page_size, total)
                    page_results = results[start_idx:end_idx]

                    st.write(f"Showing {offset + 1}-{end_idx} of {total} results")

                    for i, result in enumerate(page_results):
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.write(f"**📄 {result['name']}**")
                            st.write(f"*{result['full_path']}*")
                            st.write(f"Type: {result['file_type']} | Size: {result['size_human']}")
                            if result.get('content_preview'):
                                preview = result['content_preview']
                                if search_query:
                                    terms = parsed_info.get('keywords', [])
                                    if terms:
                                        for term in terms:
                                            preview = preview.replace(term, f"**{term}**")
                                st.caption(f"Preview: {preview[:200]}...")
                        with col2:
                            st.write(f"Modified: {result.get('modified_at', 'N/A')}")
                            st.write(f"Category: {result.get('file_category', 'N/A')}")
                            if result.get('content_available'):
                                st.write("✅ Content indexed")

                        # Per-result actions
                        acol1, acol2, acol3 = st.columns([1, 1, 4])
                        with acol1:
                            if st.button("📂 Open", key=f"open_{result.get('id', i)}", width='stretch'):
                                try:
                                    open_path(result['full_path'])
                                except Exception as e:
                                    st.error(f"Could not open: {e}")
                        with acol2:
                            if st.button("📍 Reveal", key=f"reveal_{result.get('id', i)}", width='stretch'):
                                try:
                                    reveal_path(result['full_path'])
                                except Exception as e:
                                    st.error(f"Could not reveal: {e}")
                        with acol3:
                            if st.button("👁 Preview", key=f"prev_{result.get('id', i)}", width='stretch'):
                                st.session_state['preview_target'] = result
                        st.divider()

                    # Inline preview for the selected result
                    if 'preview_target' in st.session_state:
                        target = st.session_state['preview_target']
                        full = target.get('full_path', '')
                        if full and os.path.exists(full):
                            with st.expander(f"👁 Preview: {target['name']}", expanded=True):
                                if full.lower().endswith('.pdf'):
                                    show_pdf_preview(full)
                                # Text preview from the index, if any
                                row = db.get_file_by_id(target['id']) if target.get('id') else None
                                if row and row.get('content_available'):
                                    st.text((row.get('content_preview') or '')[:5000])
                                elif not full.lower().endswith('.pdf'):
                                    st.info("No indexed content for this file.")
                        else:
                            st.warning("File no longer exists on disk.")

                    if total_pages > 1:
                        col1, col2, col3 = st.columns([1, 2, 1])
                        with col1:
                            if current_page > 1:
                                if st.button("⬅️ Previous", key="prev_page"):
                                    st.session_state['current_page'] = current_page - 1
                                    st.rerun()
                        with col2:
                            st.write(f"Page {current_page} of {total_pages}")
                        with col3:
                            if current_page < total_pages:
                                if st.button("➡️ Next", key="next_page"):
                                    st.session_state['current_page'] = current_page + 1
                                    st.rerun()

                    if results:
                        col1, col2 = st.columns(2)
                        with col1:
                            csv = export_results_csv(results)
                            st.download_button(
                                "📥 Export Results (CSV)",
                                csv,
                                "search_results.csv",
                                "text/csv"
                            )
                        with col2:
                            json_data = export_results_json(results)
                            st.download_button(
                                "📥 Export Results (JSON)",
                                json_data,
                                "search_results.json",
                                "application/json"
                            )
                else:
                    st.info("No results found.")

            if 'index_stats' in st.session_state:
                with st.expander("📊 Index Statistics", expanded=False):
                    st.json(st.session_state['index_stats'])

            if db:
                file_count = db.get_file_count()
                st.info(f"Total indexed files: {file_count} — index: `{db_path}`")

            with st.expander("📜 Search History", expanded=False):
                try:
                    history = search_engine.get_search_history()
                    recent = history.get_recent(10)
                    if recent:
                        for item in recent:
                            st.write(f"**{item['query']}** ({item['results_count']} results) - {item['executed_at']}")
                    else:
                        st.write("No search history yet.")

                    if st.button("Clear History"):
                        history.clear()
                        st.rerun()
                except Exception as e:
                    st.error(f"Error accessing search history: {e}")

            with st.expander("⚡ Advanced Search", expanded=False):
                st.info("Advanced search features:")
                st.write("- **Boolean search**: AND, OR, NOT operators")
                st.write("- **Wildcard search**: * and ? wildcards")
                st.write("- **Phrase search**: \"exact phrase\"")
                st.write("- **Regex search**: /pattern/")

                advanced_query = st.text_input("Advanced Query", placeholder='"report" AND (pdf OR docx)')
                if advanced_query:
                    try:
                        adv_search = AdvancedSearch(db)
                        parsed = adv_search.parse_advanced_query(advanced_query)
                        fts_query = adv_search.build_advanced_fts_query(parsed)
                        st.json({"Parsed": parsed, "FTS Query": fts_query})
                    except Exception as e:
                        st.error(f"Advanced search error: {e}")

            with st.expander("💡 Search Suggestions", expanded=False):
                try:
                    if search_query:
                        suggestions = search_engine.get_search_suggestions_for_prefix(search_query)
                        if suggestions:
                            st.write("**Suggestions based on your search:**")
                            for s in suggestions:
                                st.write(f"- {s['text']} ({s.get('type', 'suggestion')})")
                        else:
                            st.write("No suggestions found.")

                    st.write("**Popular search terms:**")
                    try:
                        sugg_mgr = search_engine.get_search_suggestions()
                        popular = sugg_mgr.get_popular_terms(5)
                        for item in popular:
                            st.write(f"- {item['term']} ({item['count']} searches)")
                    except Exception:
                        st.write("No popular terms available.")
                except Exception as e:
                    st.error(f"Error accessing suggestions: {e}")

            with st.expander("📊 Search Feedback", expanded=False):
                try:
                    if 'search_results' in st.session_state and st.session_state['search_results']:
                        results_data = st.session_state['search_results']
                        feedback = search_engine.get_search_feedback().format_feedback(
                            query=results_data['query'],
                            results_count=results_data['total'],
                            execution_time=results_data['execution_time'],
                            parsed_info=parsed_info
                        )

                        st.write(f"**Query:** {feedback['query']}")
                        st.write(f"**Results:** {feedback['results_count']}")
                        st.write(f"**Execution Time:** {feedback['execution_time_ms']}ms ({feedback['time_label']})")

                        if feedback['suggestions']:
                            st.write("**Suggestions for refining search:**")
                            for s in feedback['suggestions']:
                                st.write(f"- {s}")
                    else:
                        st.write("Run a search to see feedback.")
                except Exception as e:
                    st.error(f"Error accessing feedback: {e}")

            with st.expander("🎨 Visual Enhancements", expanded=False):
                st.info("File category colors:")

                visual = VisualEnhancements()
                categories = ['document', 'image', 'video', 'audio', 'archive', 'code', 'data', 'other']

                for cat in categories:
                    icon = visual.get_category_icon(cat)
                    color = visual.get_category_color(cat)
                    st.write(f"{icon} **{cat.capitalize()}**: <span style='color:{color};'>●</span>", unsafe_allow_html=True)

                st.divider()
                st.info("File type icons:")
                st.write("📄 Document | 🖼️ Image | 🎥 Video | 🔊 Audio | 📦 Archive | 💻 Code | 📊 Data | 📄 Other")

    # ==================== STORAGE DASHBOARD ====================
    with main_tab3:
        st.subheader("📊 Storage Dashboard")

        storage_df = None
        if 'scan_results' in st.session_state:
            storage_df = st.session_state['scan_results']
            source_note = "from the latest scan"
        elif enable_indexing:
            all_files = db.get_all_files()
            if all_files:
                storage_df = pd.DataFrame(all_files)
                source_note = "from the persistent index"

        if storage_df is None or storage_df.empty:
            st.info("Run a scan to populate the storage dashboard.")
        else:
            sdf = storage_df.copy()
            total_bytes = int(sdf['size_bytes'].sum())

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

    # ==================== WATCH DASHBOARD ====================
    with main_tab4:
        st.subheader("👀 Folder Change Monitoring")

        if 'watch_paths' not in st.session_state:
            st.session_state['watch_paths'] = []

        watcher = st.session_state.get('watcher')
        if watcher is not None and not watcher.is_alive():
            st.session_state['watcher'] = watcher = None

        col1, col2 = st.columns([3, 1])
        with col1:
            new_watch = st.text_input("Folder to watch", placeholder="~/Documents or C:\\Users\\you\\Downloads")
        with col2:
            st.write("")
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
                    try:
                        db.add_alert(event_type, path, name, watch_path)
                    except Exception:
                        pass
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


if __name__ == "__main__":
    main()
