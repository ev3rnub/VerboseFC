import os
import hashlib
import pathlib
import pandas as pd
import streamlit as st
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Dict, Optional
from content_extractor import ContentExtractor
from db import SearchDatabase
from indexer import FileIndexer, create_indexer
from search_engine import SearchEngine, create_search_engine
from enhanced_features import AdvancedSearch, SearchCache, IncrementalIndexer, SearchHistory
from ux_enhancements import SearchSuggestions, SearchFeedback, VisualEnhancements

# --- Utility Functions ---

def get_human_size(size: int) -> str:
    """Converts bytes into human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

def compute_hashes(file_path: str) -> Dict[str, str]:
    """Computes MD5 and SHA256 hashes in chunks to prevent memory overflow."""
    md5_hash = hashlib.md5()
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5_hash.update(chunk)
                sha256_hash.update(chunk)
        return {
            "md5": md5_hash.hexdigest(),
            "sha256": sha256_hash.hexdigest()
        }
    except Exception:
        return {"md5": "ERROR", "sha256": "ERROR"}

def process_single_file(file_path: pathlib.Path) -> Optional[Dict]:
    """
    Worker function for parallel processes.
    Returns a dict to avoid Pickle serialization errors across processes.
    """
    try:
        stats = file_path.stat()
        hashes = compute_hashes(str(file_path))
        return {
            "name": file_path.name,
            "full_path": str(file_path),
            "size_bytes": stats.st_size,
            "size_human": get_human_size(stats.st_size),
            "md5": hashes["md5"],
            "sha256": hashes["sha256"]
        }
    except Exception:
        return None


# --- Main App ---

def get_resolved_path(path_str: str) -> pathlib.Path:
    """Resolve a path string, handling ~ and relative paths."""
    if not path_str:
        return pathlib.Path.cwd()
    # Expand ~ and environment variables
    expanded = os.path.expanduser(os.path.expandvars(path_str))
    # Convert to absolute path
    return pathlib.Path(expanded).resolve()

def main():
    st.set_page_config(page_title="File Discovery Dashboard", layout="wide")
    st.title("📂 File Discovery & Management")

    with st.sidebar:
        st.header("Settings")
        target_path = st.text_input("Root Path", value="~")
        # Depth 1 = only root folder, Depth 2 = subfolders, etc.
        max_depth = st.slider("Max Recursion Depth", 1, 10, 2)
        workers = st.slider("Parallel Workers", 1, 16, 4)
        run_scan = st.button("Scan Directory", type="primary")
        
        # Indexing options
        st.divider()
        st.subheader("Indexing Options")
        enable_indexing = st.checkbox("Enable Full Indexing", value=True, help="Extract content, timestamps, and categorize files")
        index_path = st.text_input("Index Path", value="~")
        index_depth = st.slider("Index Depth", 1, 5, 2)
        index_workers = st.slider("Index Workers", 1, 8, 4)
        run_index = st.button("Build/Refresh Index", type="secondary")

    # Database initialization
    db = None
    if enable_indexing:
        # Use in-memory database for this demo
        db = SearchDatabase(":memory:")
        st.session_state['db'] = db

    # Indexer instance
    indexer = None
    if enable_indexing and db:
        extractor = ContentExtractor()
        indexer = FileIndexer(db, extractor)
        st.session_state['indexer'] = indexer

    # Search engine instance
    search_engine = None
    if enable_indexing and db:
        search_engine = create_search_engine(db)
        st.session_state['search_engine'] = search_engine

    # --- Indexing Action ---
    if run_index and enable_indexing and db:
        root = get_resolved_path(index_path)
        if not root.exists():
            st.error(f"Index path '{index_path}' does not exist!")
        else:
            st.info(f"Building index for {root} (Depth: {index_depth})...")
            try:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                def progress_callback(current, total, filename):
                    progress_bar.progress(current / total)
                    status_text.text(f"Indexing: {filename}")
                
                stats = indexer.index_directory(str(root), max_depth=index_depth, workers=index_workers, progress_callback=progress_callback)
                
                progress_bar.empty()
                status_text.empty()
                
                st.success(f"Indexing complete!")
                st.json(stats)
                
                # Store indexed files in session state
                st.session_state['indexed_files'] = db.get_all_files()
                st.session_state['index_stats'] = stats
                
            except Exception as e:
                st.error(f"Error during indexing: {e}")
                import traceback
                st.error(traceback.format_exc())

    # --- Scanning Action ---
    if run_scan:
        root = get_resolved_path(target_path)
        if not root.exists():
            st.error(f"Path '{target_path}' does not exist!")
            return

        st.info(f"Scanning {target_path} (Max Depth: {max_depth})...")
        files_to_scan = []
        
        # Phase 1: Recursive Discovery with Depth Control
        try:
            # rglob("*") traverses recursively
            for path in root.rglob("*"):
                # Calculate depth relative to root
                try:
                    relative_path = path.relative_to(root)
                    depth = len(relative_path.parts)
                except ValueError:
                    depth = 0
                
                if path.is_file() and depth <= max_depth:
                    # Filter out system junk
                    if path.name.lower() not in ["thumbs.db", "$desktop.ini"]:
                        files_to_scan.append(path)
        except Exception as e:
            st.error(f"Error accessing directory: {e}")
            return

        st.success(f"Found {len(files_to_scan)} files.")

        # Phase 2: Parallel Hashing
        if files_to_scan:
            st.info("Computing hashes in parallel...")
            progress_bar = st.progress(0)
            results = []
            
            with ProcessPoolExecutor(max_workers=workers) as executor:
                future_to_file = {executor.submit(process_single_file, f): f for f in files_to_scan}
                
                for i, future in enumerate(as_completed(future_to_file)):
                    try:
                        res = future.result()
                        if res:
                            results.append(res)
                    except Exception as e:
                        st.error(f"A worker failed: {e}")
                    
                    progress_bar.progress((i + 1) / len(files_to_scan))
        else:
            st.warning("No files found within the specified depth.")
            return

        df = pd.DataFrame(results)

        # --- UI Tabs ---
        tab1, tab2, tab3, tab4 = st.tabs(["📄 File List", "👯 Duplicates", "🌳 Tree View", "🔍 Search"])

        with tab1:
            st.subheader("All Files")
            st.dataframe(df, use_container_width=True)
            
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button("Download CSV Report", csv, "file_report.csv", "text/csv")

        with tab2:
            st.subheader("Duplicate Files (Identical Hashes)")
            dupes_hash = df[df.groupby('sha256')['sha256'].transform('count') > 1]
            if not dupes_hash.empty:
                st.dataframe(dupes_hash.sort_values("sha256"))
            else:
                st.write("No duplicate files found.")

            st.subheader("Filename Collision (Same name, different content)")
            name_groups = df.groupby('name')['sha256'].nunique()
            colliding_names = name_groups[name_groups > 1].index
            dupes_name = df[df['name'].isin(colliding_names)]
            if not dupes_name.empty:
                st.dataframe(dupes_name)
            else:
                st.write("No filename collisions found.")

        with tab3:
            st.subheader("Directory Structure")
            def generate_tree(current_path, indent=""):
                tree_str = ""
                try:
                    # Sort directories first, then files
                    items = sorted(list(current_path.iterdir()), key=lambda x: (not x.is_dir(), x.name.lower()))
                    for item in items:
                        depth = len(item.relative_to(root).parts)
                        if depth > max_depth:
                            continue
                        
                        prefix = "📁 " if item.is_dir() else "📄 "
                        tree_str += f"{indent}{prefix}{item.name}\n"
                        if item.is_dir():
                            tree_str += generate_tree(item, indent + "    ")
                except PermissionError:
                    tree_str += f"{indent} [Permission Denied]\n"
                return tree_str

            st.code(generate_tree(root), height=600)

        # --- Phase 2: Search Tab ---
        with tab4:
            st.subheader("🔍 Natural Language Search")
            
            if not enable_indexing or not db:
                st.info("Enable indexing in the sidebar to use search functionality.")
            else:
                # Search bar
                search_query = st.text_input(
                    "Search files by name, content, or metadata...",
                    placeholder="Search files by name, content, or metadata...",
                    key="search_input"
                )
                
                # Search button and clear button
                col1, col2, col3 = st.columns([1, 1, 3])
                with col1:
                    search_button = st.button("🔍 Search", use_container_width=True)
                with col2:
                    clear_button = st.button("🗑️ Clear", use_container_width=True)
                
                # Clear search on clear button click
                if clear_button:
                    st.session_state['search_input'] = ""
                    st.session_state['search_results'] = None
                    st.rerun()
                
                # Search filters (expandable section)
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
                
                # Execute search
                if search_button or (search_query and 'search_results' not in st.session_state):
                    results = []
                    total = 0
                    parsed_info = {}
                    execution_time = 0.0
                    
                    if search_query:
                        # Execute search using search engine
                        import time
                        start_time = time.time()
                        
                        try:
                            results, total, parsed_info = search_engine.search(
                                search_query, limit=100, offset=0
                            )
                            execution_time = time.time() - start_time
                        except Exception as e:
                            st.error(f"Search error: {e}")
                            import traceback
                            st.error(traceback.format_exc())
                            results = []
                            total = 0
                    else:
                        # Apply metadata filters only
                        category_map = {
                            "Document": "document", "Image": "image", "Video": "video",
                            "Audio": "audio", "Archive": "archive", "Code": "code", "Data": "data"
                        }
                        cat_filter = category_map.get(filter_category) if filter_category != "All" else None
                        
                        try:
                            results, total = db.search_files_by_metadata(
                                extension=filter_ext if filter_ext else None,
                                file_category=cat_filter,
                                min_size=filter_min_size if filter_min_size > 0 else None,
                                max_size=filter_max_size if filter_max_size > 0 else None
                            )
                        except Exception as e:
                            st.error(f"Search error: {e}")
                            results = []
                            total = 0
                    
                    # Store results in session state
                    st.session_state['search_results'] = {
                        'results': results,
                        'total': total,
                        'execution_time': execution_time,
                        'query': search_query
                    }
                    st.session_state['parsed_info'] = parsed_info
                
                # Display results
                if 'search_results' in st.session_state and st.session_state['search_results']:
                    results_data = st.session_state['search_results']
                    results = results_data['results']
                    total = results_data['total']
                    execution_time = results_data['execution_time']
                    
                    if results:
                        # Search feedback
                        st.success(f"Found {total} result(s) in {execution_time:.3f} seconds")
                        
                        # Show parsed query info
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
                        
                        # Pagination
                        page_size = 20
                        total_pages = (total + page_size - 1) // page_size
                        current_page = st.number_input(
                            "Page", min_value=1, max_value=max(1, total_pages), value=1
                        )
                        
                        start_idx = (current_page - 1) * page_size
                        end_idx = min(start_idx + page_size, total)
                        page_results = results[start_idx:end_idx]
                        
                        # Results display
                        st.write(f"Showing {start_idx + 1}-{end_idx} of {total} results")
                        
                        for i, result in enumerate(page_results):
                            # Create card for each result
                            col1, col2 = st.columns([3, 1])
                            with col1:
                                st.write(f"**📄 {result['name']}**")
                                st.write(f"*{result['full_path']}*")
                                st.write(f"Type: {result['file_type']} | Size: {result['size_human']}")
                                if result.get('content_preview'):
                                    preview = result['content_preview']
                                    # Highlight search terms
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
                            st.divider()
                        
                        # Pagination controls
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
                        
                        # Export results
                        if results:
                            export_df = pd.DataFrame(results)
                            csv = export_df.to_csv(index=False).encode('utf-8')
                            st.download_button(
                                "📥 Export Results (CSV)",
                                csv,
                                "search_results.csv",
                                "text/csv"
                            )
                    else:
                        st.info("No results found.")
                
                # Index statistics
                if 'index_stats' in st.session_state:
                    with st.expander("📊 Index Statistics", expanded=False):
                        st.json(st.session_state['index_stats'])
                
                # Show indexed file count
                if db:
                    file_count = db.get_file_count()
                    st.info(f"Total indexed files: {file_count}")
                
                # Phase 3: Search History
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
                
                # Phase 3: Advanced Search
                with st.expander("⚡ Advanced Search", expanded=False):
                    st.info("Advanced search features:")
                    st.write("- **Boolean search**: AND, OR, NOT operators")
                    st.write("- **Wildcard search**: * and ? wildcards")
                    st.write("- **Phrase search**: \"exact phrase\"")
                    st.write("- **Regex search**: /pattern/")
                    
                    advanced_query = st.text_input("Advanced Query", placeholder='"report" AND (pdf OR docx)')
                    if advanced_query:
                        try:
                            from enhanced_features import AdvancedSearch
                            adv_search = AdvancedSearch(db)
                            parsed = adv_search.parse_advanced_query(advanced_query)
                            fts_query = adv_search.build_advanced_fts_query(parsed)
                            st.json({"Parsed": parsed, "FTS Query": fts_query})
                        except Exception as e:
                            st.error(f"Advanced search error: {e}")
                
                # Phase 5: Search Suggestions
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
                
                # Phase 5: Search Feedback
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
                
                # Phase 5: Visual Enhancements
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


if __name__ == "__main__":
    # Start main
    main()
