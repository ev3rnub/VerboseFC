import os
import hashlib
import pathlib
import pandas as pd
import streamlit as st
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Dict, Optional

# --- Utility Functions ---
# get files sizes, compute hashes

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

def main():
    st.set_page_config(page_title="File Discovery Dashboard", layout="wide")
    st.title("📂 File Discovery & Management")

    with st.sidebar:
        st.header("Settings")
        target_path = st.text_input("Root Path", value=".")
        # Depth 1 = only root folder, Depth 2 = subfolders, etc.
        max_depth = st.slider("Max Recursion Depth", 1, 10, 2)
        workers = st.slider("Parallel Workers", 1, 16, 4)
        run_scan = st.button("Scan Directory", type="primary")

    if run_scan:
        root = pathlib.Path(target_path)
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
        tab1, tab2, tab3 = st.tabs(["📄 File List", "👯 Duplicates", "🌳 Tree View"])

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

if __name__ == "__main__":
    # Start main
    main()
