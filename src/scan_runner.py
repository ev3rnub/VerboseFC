"""
Scan runner module.
Runs directory scans (walk -> hash -> index) in a background thread with
pause / resume / cancel controls and configurable hashing.
"""

import hashlib
import os
import time
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional

SKIP_NAMES = {"thumbs.db", "$desktop.ini", ".ds_store"}
# Directories never descended into (recycle bins / trash — can be network-backed and hang)
SKIP_DIRS = {".trash", "$recycle.bin", "system volume information"}

# Hashing modes
FULL = "full"
FAST = "fast"
NONE = "none"


def get_human_size(size: int) -> str:
    """Converts bytes into human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def compute_hashes(file_path: str, md5: bool = True, sha256: bool = True,
                   fast_limit: Optional[int] = None) -> Dict[str, str]:
    """
    Compute the enabled hashes in chunks.
    fast_limit: if set, only the first N bytes are hashed.
    Disabled hashes come back as empty strings.
    """
    md5_h = hashlib.md5() if md5 else None
    sha_h = hashlib.sha256() if sha256 else None
    if md5_h is None and sha_h is None:
        return {"md5": "", "sha256": ""}
    try:
        with open(file_path, "rb") as f:
            read = 0
            while True:
                n = 4096
                if fast_limit is not None:
                    n = min(4096, fast_limit - read)
                    if n <= 0:
                        break
                chunk = f.read(n)
                if not chunk:
                    break
                if md5_h is not None:
                    md5_h.update(chunk)
                if sha_h is not None:
                    sha_h.update(chunk)
                read += len(chunk)
        return {
            "md5": md5_h.hexdigest() if md5_h is not None else "",
            "sha256": sha_h.hexdigest() if sha_h is not None else ""
        }
    except Exception:
        return {
            "md5": "ERROR" if md5 else "",
            "sha256": "ERROR" if sha256 else ""
        }


def scan_worker(file_path: Path, hash_opts: Dict) -> Optional[Dict]:
    """
    Worker function for parallel processes.
    hash_opts: {'md5': bool, 'sha256': bool, 'fast_limit': int|None}
    Returns a dict to avoid Pickle serialization errors across processes.
    """
    try:
        stats = file_path.stat()
        hashes = compute_hashes(
            str(file_path),
            md5=hash_opts.get('md5', True),
            sha256=hash_opts.get('sha256', True),
            fast_limit=hash_opts.get('fast_limit')
        )
        return {
            "name": file_path.name,
            "full_path": str(file_path),
            "size_bytes": stats.st_size,
            "size_human": get_human_size(stats.st_size),
            "modified_at_epoch": stats.st_mtime,
            "md5": hashes["md5"],
            "sha256": hashes["sha256"]
        }
    except Exception:
        return None


class ScanController:
    """
    Runs a scan (walk -> parallel hash -> index) in a background thread.
    Supports pause / resume / cancel. State is polled from the UI thread.
    """

    def __init__(self):
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.file_list: List[Path] = []
        self.results: List[Dict] = []
        self.index_stats: Optional[Dict] = None
        self.error: Optional[str] = None
        self.status = "idle"  # idle | running | paused | cancelling | cancelled | done | error
        self.phase = ""       # walk | hash | index
        self.done = 0
        self.total = 0
        self.current = ""

    # ---- controls ----

    def start(self, root: Path, max_depth: int, workers: int,
              hash_opts: Dict, index_fn: Optional[Callable] = None):
        """Start a scan in a background thread."""
        self._pause.clear()
        self._cancel.clear()
        self.results = []
        self.index_stats = None
        self.error = None
        self.file_list = []
        self.done = 0
        self.total = 0
        self.current = ""
        self.status = "running"
        self.phase = "walk"
        self.thread = threading.Thread(
            target=self._run,
            args=(root, max_depth, workers, hash_opts, index_fn),
            daemon=True
        )
        self.thread.start()

    def pause(self):
        self._pause.set()
        with self._lock:
            if self.status == "running":
                self.status = "paused"

    def resume(self):
        self._pause.clear()
        with self._lock:
            if self.status == "paused":
                self.status = "running"

    def cancel(self):
        self._cancel.set()
        self._pause.clear()  # let the loop notice the cancel even while paused
        with self._lock:
            if self.status in ("running", "paused"):
                self.status = "cancelling"

    def is_active(self) -> bool:
        return self.status in ("running", "paused", "cancelling")

    def snapshot(self) -> Dict:
        """Thread-safe copy of progress state for the UI."""
        with self._lock:
            return {
                "status": self.status,
                "phase": self.phase,
                "done": self.done,
                "total": self.total,
                "current": self.current,
            }

    def _set(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def _stopped(self) -> bool:
        return self._cancel.is_set()

    def _wait_if_paused(self):
        while self._pause.is_set() and not self._cancel.is_set():
            time.sleep(0.25)

    # ---- the scan itself ----

    def _run(self, root: Path, max_depth: int, workers: int,
             hash_opts: Dict, index_fn: Optional[Callable]):
        try:
            # Phase 1: walk (single directory walk, reused by hashing and indexing).
            # os.walk prunes junk/trash dirs before descending and ignores
            # unreadable/network-stalled directories instead of crashing.
            files: List[Path] = []
            for dirpath, dirnames, filenames in os.walk(root):
                if self._stopped():
                    self._set(status="cancelled", phase="cancelled")
                    return
                self._wait_if_paused()
                try:
                    rel = Path(dirpath).relative_to(root)
                    dir_depth = 0 if str(rel) == "." else len(rel.parts)
                except ValueError:
                    dir_depth = 0
                # Prune: don't descend into trash dirs or past max_depth
                dirnames[:] = [d for d in dirnames
                               if d.lower() not in SKIP_DIRS and dir_depth + 1 < max_depth]
                if dir_depth + 1 <= max_depth:
                    for fn in filenames:
                        if fn.lower() in SKIP_NAMES:
                            continue
                        files.append(Path(dirpath) / fn)
            self.file_list = files
            self._set(total=len(files), done=0, phase="hash")

            # Phase 2: parallel hashing (batched so pause/cancel respond quickly)
            if files and (hash_opts.get('md5') or hash_opts.get('sha256')):
                batch_size = max(32, workers * 32)
                with ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
                    for start in range(0, len(files), batch_size):
                        self._wait_if_paused()
                        if self._stopped():
                            self._set(status="cancelled", phase="cancelled")
                            return
                        batch = files[start:start + batch_size]
                        futures = {executor.submit(scan_worker, f, hash_opts): f for f in batch}
                        for future in as_completed(futures):
                            if self._stopped():
                                executor.shutdown(wait=False, cancel_futures=True)
                                self._set(status="cancelled", phase="cancelled")
                                return
                            res = future.result()
                            if res:
                                self.results.append(res)
                            with self._lock:
                                self.done += 1
                                self.current = str(futures[future])
            else:
                # No hashing requested: still collect stats for the file list
                for f in files:
                    self._wait_if_paused()
                    if self._stopped():
                        self._set(status="cancelled", phase="cancelled")
                        return
                    try:
                        stats = f.stat()
                        self.results.append({
                            "name": f.name,
                            "full_path": str(f),
                            "size_bytes": stats.st_size,
                            "size_human": get_human_size(stats.st_size),
                            "modified_at_epoch": stats.st_mtime,
                            "md5": "",
                            "sha256": ""
                        })
                    except Exception:
                        pass
                    with self._lock:
                        self.done += 1
                        self.current = str(f)

            # Phase 3: indexing (reuses the same file list — no second walk)
            if index_fn is not None and files:
                self._set(phase="index", done=0, total=len(files))
                self.index_stats = index_fn(
                    self.file_list,
                    progress_callback=lambda d, t, cur: self._set(done=d, total=t, current=cur),
                    should_stop=self._stopped,
                    is_paused=lambda: self._pause.is_set()
                )

            if self._stopped():
                self._set(status="cancelled", phase="cancelled")
            else:
                self._set(status="done", phase="done")
        except Exception as e:
            import traceback
            self.error = traceback.format_exc()
            self._set(status="error", phase="error")
