"""
Indexer module for Phase 1.
Handles the indexing pipeline for files with content extraction and database storage.
"""

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from content_extractor import ContentExtractor
from db import SearchDatabase


class FileIndexer:
    """Indexes files with metadata, content extraction, and database storage."""
    
    def __init__(self, db: SearchDatabase, extractor: ContentExtractor = None, max_content_size: int = 10 * 1024 * 1024,
                 hash_md5: bool = True, hash_sha256: bool = True, fast_hash_mb: float = 0.0):
        """
        Initialize the indexer.
        
        Args:
            db: SearchDatabase instance for storing index
            extractor: ContentExtractor instance for text extraction
            max_content_size: Maximum file size to extract content from (default 10MB)
            hash_md5: Compute MD5 hashes (False stores empty string)
            hash_sha256: Compute SHA256 hashes (False stores empty string)
            fast_hash_mb: If > 0, hash only the first N MB of each file
        """
        self.db = db
        self.extractor = extractor or ContentExtractor()
        self.max_content_size = max_content_size
        self.hash_md5 = hash_md5
        self.hash_sha256 = hash_sha256
        self.fast_hash_mb = fast_hash_mb
        self._stats = {
            'total': 0,
            'indexed': 0,
            'skipped': 0,
            'errors': 0,
            'content_extracted': 0,
            'content_skipped': 0,
            'error_samples': []
        }
    
    def _get_file_stats(self, file_path: Path) -> Dict:
        """Get file system statistics."""
        stats = file_path.stat()
        return {
            'size_bytes': stats.st_size,
            'size_human': self._get_human_size(stats.st_size),
            'created_at': time.ctime(stats.st_ctime),
            'modified_at': time.ctime(stats.st_mtime),
            'accessed_at': time.ctime(stats.st_atime),
            'created_at_epoch': stats.st_ctime,
            'modified_at_epoch': stats.st_mtime,
            'accessed_at_epoch': stats.st_atime
        }
    
    def _get_human_size(self, size: int) -> str:
        """Convert bytes to human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"
    
    def _compute_hashes(self, file_path: str) -> Dict[str, str]:
        """Computes the enabled hashes in chunks. Disabled hashes return ''."""
        import hashlib
        md5_hash = hashlib.md5() if self.hash_md5 else None
        sha256_hash = hashlib.sha256() if self.hash_sha256 else None
        if md5_hash is None and sha256_hash is None:
            return {"md5": "", "sha256": ""}
        limit = int(self.fast_hash_mb * 1024 * 1024) if self.fast_hash_mb > 0 else None
        try:
            with open(file_path, "rb") as f:
                read = 0
                while True:
                    n = 4096
                    if limit is not None:
                        n = min(4096, limit - read)
                        if n <= 0:
                            break
                    chunk = f.read(n)
                    if not chunk:
                        break
                    if md5_hash is not None:
                        md5_hash.update(chunk)
                    if sha256_hash is not None:
                        sha256_hash.update(chunk)
                    read += len(chunk)
            return {
                "md5": md5_hash.hexdigest() if md5_hash is not None else "",
                "sha256": sha256_hash.hexdigest() if sha256_hash is not None else ""
            }
        except Exception:
            return {
                "md5": "ERROR" if self.hash_md5 else "",
                "sha256": "ERROR" if self.hash_sha256 else ""
            }
    
    def _is_ignored_name(self, file_path: Path) -> bool:
        """Files that should not be indexed at all (Windows shell leftovers)."""
        return file_path.name.lower() in ("thumbs.db", "$desktop.ini")

    def _content_too_large(self, size_bytes: int) -> bool:
        """True when a file is over the content budget: index metadata, skip reading.

        The old helper returned False ("do not skip") on this branch, so the
        advertised 10 MB content cap never applied and every readable file was
        pulled into memory regardless of size.
        """
        return self.max_content_size > 0 and size_bytes > self.max_content_size

    def _should_extract_content(self, file_path: Path, size_bytes: int) -> bool:
        """Metadata-only vs full extraction for one file."""
        if self._is_ignored_name(file_path):
            return False
        return not self._content_too_large(size_bytes)

    def _note_error(self, file_path: Path, exc: Exception) -> None:
        """Keep a few failure reasons so the UI can explain silent skips."""
        samples = self._stats.get('error_samples')
        if samples is not None and len(samples) < 5:
            samples.append(f"{file_path.name}: {type(exc).__name__}: {exc}")
    
    def _process_single_file(self, file_path: Path) -> Optional[Dict]:
        """
        Process a single file for indexing.
        Returns a dict with all file data or None if processing fails.
        """
        try:
            # Get file stats
            stats = self._get_file_stats(file_path)
            
            # Compute hashes
            hashes = self._compute_hashes(str(file_path))
            
            # Categorize file
            file_category = self.extractor.categorize_file(file_path)
            file_type_display = self.extractor.get_file_type_display(file_path)
            
            # Extract content if applicable
            content = None
            content_preview = None
            content_available = 0
            
            if self._should_extract_content(file_path, stats['size_bytes']):
                content, content_preview = self.extractor.extract_content(file_path)
                if content:
                    content_available = 1
            elif self._content_too_large(stats['size_bytes']):
                # Counted, not silently dropped: the file is still listed with
                # its metadata, only the text extraction is skipped.
                self._stats['content_skipped'] = self._stats.get('content_skipped', 0) + 1
            
            return {
                'name': file_path.name,
                'full_path': str(file_path),
                'size_bytes': stats['size_bytes'],
                'size_human': stats['size_human'],
                'md5': hashes['md5'],
                'sha256': hashes['sha256'],
                'extension': file_path.suffix.lower(),
                'file_type': file_type_display,
                'file_category': file_category,
                'created_at': stats['created_at'],
                'modified_at': stats['modified_at'],
                'accessed_at': stats['accessed_at'],
                'created_at_epoch': stats['created_at_epoch'],
                'modified_at_epoch': stats['modified_at_epoch'],
                'accessed_at_epoch': stats['accessed_at_epoch'],
                'content': content,
                'content_preview': content_preview,
                'content_available': content_available
            }
        except Exception as e:
            self._note_error(file_path, e)
            return None
    
    def index_file(self, file_path: Path, callback: Callable = None) -> Optional[int]:
        """
        Index a single file.
        Returns the file ID or None if indexing fails.
        """
        file_data = self._process_single_file(file_path)
        
        if file_data is None:
            self._stats['errors'] += 1
            return None
        
        file_id = self.db.insert_file(file_data)
        
        if file_id:
            self._stats['indexed'] += 1
            if file_data.get('content'):
                self._stats['content_extracted'] += 1
        else:
            self._stats['errors'] += 1
        
        if callback:
            callback(file_data)
        
        return file_id
    
    def index_directory(self, 
                        root_path: str, 
                        max_depth: int = 2,
                        workers: int = 4,
                        progress_callback: Callable = None) -> Dict:
        """
        Index an entire directory tree.
        
        Args:
            root_path: Root directory to scan
            max_depth: Maximum recursion depth (1 = root only)
            workers: Number of parallel workers
            progress_callback: Optional callback(progress, total, current_file)
        
        Returns:
            Statistics dict with indexing results
        """
        root = Path(root_path)
        if not root.exists():
            raise ValueError(f"Path '{root_path}' does not exist!")
        
        # Discover files
        files_to_scan = []
        try:
            for path in root.rglob("*"):
                if path.is_file():
                    try:
                        relative_path = path.relative_to(root)
                        depth = len(relative_path.parts)
                        if depth <= max_depth:
                            if not self._is_ignored_name(path):
                                files_to_scan.append(path)
                    except ValueError:
                        pass
        except Exception as e:
            raise RuntimeError(f"Error scanning directory: {e}")
        
        return self.index_files(files_to_scan, workers=workers, progress_callback=progress_callback)

    def index_files(self,
                    file_paths: List[Path],
                    workers: int = 4,
                    progress_callback: Callable = None,
                    should_stop: Callable = None,
                    is_paused: Callable = None,
                    skip_unchanged: bool = False) -> Dict:
        """
        Index a pre-discovered list of files (avoids re-walking the tree).
        
        Args:
            file_paths: Already-discovered file paths to index
            workers: Number of parallel workers
            progress_callback: Optional callback(progress, total, current_file)
            should_stop: Optional callable returning True to abort (cancel)
            is_paused: Optional callable returning True while paused
            skip_unchanged: If True, skip files already indexed with the same mtime
        
        Returns:
            Statistics dict with indexing results
        """
        from concurrent.futures import ThreadPoolExecutor
        
        # Reset stats
        self._stats = {
            'total': 0,
            'indexed': 0,
            'skipped': 0,
            'errors': 0,
            'content_extracted': 0,
            'content_skipped': 0,
            'error_samples': []
        }
        
        # Optionally skip files whose stored mtime matches the on-disk mtime
        if skip_unchanged:
            kept = []
            for p in file_paths:
                try:
                    stored = self.db.get_indexed_mtime(str(p))
                    if stored is not None and abs(stored - p.stat().st_mtime) < 1e-6:
                        self._stats['skipped'] += 1
                        continue
                except Exception:
                    pass
                kept.append(p)
            file_paths = kept
        
        self._stats['total'] = len(file_paths)
        
        if not file_paths:
            return self._stats
        
        def _stopped():
            return bool(should_stop and should_stop())
        
        def _wait_if_paused():
            while is_paused and is_paused() and not _stopped():
                time.sleep(0.25)
        
        # Process in batches so pause/cancel can take effect between batches
        batch_size = max(16, workers * 16)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            i = 0
            total = len(file_paths)
            for start in range(0, total, batch_size):
                _wait_if_paused()
                if _stopped():
                    break
                batch = file_paths[start:start + batch_size]
                future_to_file = {
                    executor.submit(self._process_single_file, f): f
                    for f in batch
                }
                for future in as_completed(future_to_file):
                    file_data = future.result()
                    
                    if file_data:
                        self.db.insert_file(file_data)
                        self._stats['indexed'] += 1
                        if file_data.get('content'):
                            self._stats['content_extracted'] += 1
                    else:
                        self._stats['errors'] += 1
                    
                    i += 1
                    if progress_callback:
                        progress_callback(i, total, str(future_to_file[future]))
        
        return self._stats
    
    def get_stats(self) -> Dict:
        """Get current indexing statistics."""
        return self._stats.copy()
    
    def refresh_index(self, root_path: str, max_depth: int = 2, workers: int = 4) -> Dict:
        """
        Refresh the index by clearing and re-indexing.
        Returns statistics dict.
        """
        self.db.clear_all()
        return self.index_directory(root_path, max_depth, workers)


import asyncio
from concurrent.futures import ThreadPoolExecutor

class AsyncIndexer:
    """Handle asynchronous indexing operations."""
    
    def __init__(self, db, extractor=None):
        self.db = db
        self.extractor = extractor or ContentExtractor()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self._indexing_task = None
    
    async def index_directory_async(self, root_path: str, max_depth: int = 2) -> Dict:
        """Index directory asynchronously."""
        loop = asyncio.get_event_loop()
        
        # Run synchronous indexing in thread pool
        indexer = FileIndexer(self.db, self.extractor)
        stats = await loop.run_in_executor(
            self.executor, 
            indexer.index_directory, 
            root_path, 
            max_depth
        )
        
        return stats
    
    async def index_file_async(self, file_path: str) -> Optional[int]:
        """Index a single file asynchronously."""
        loop = asyncio.get_event_loop()
        indexer = FileIndexer(self.db, self.extractor)
        file_id = await loop.run_in_executor(
            self.executor,
            indexer.index_file,
            Path(file_path)
        )
        return file_id
    
    def cancel_indexing(self):
        """Cancel ongoing indexing."""
        if self._indexing_task:
            self._indexing_task.cancel()


# Convenience function for single-use scenarios
def create_indexer(db_path: str = None, max_content_size: int = 10 * 1024 * 1024) -> FileIndexer:
    """Create an indexer with a database connection."""
    db = SearchDatabase(db_path)
    extractor = ContentExtractor()
    return FileIndexer(db, extractor, max_content_size)
