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
    
    def __init__(self, db: SearchDatabase, extractor: ContentExtractor = None, max_content_size: int = 10 * 1024 * 1024):
        """
        Initialize the indexer.
        
        Args:
            db: SearchDatabase instance for storing index
            extractor: ContentExtractor instance for text extraction
            max_content_size: Maximum file size to extract content from (default 10MB)
        """
        self.db = db
        self.extractor = extractor or ContentExtractor()
        self.max_content_size = max_content_size
        self._stats = {
            'total': 0,
            'indexed': 0,
            'skipped': 0,
            'errors': 0,
            'content_extracted': 0
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
        """Computes MD5 and SHA256 hashes in chunks."""
        import hashlib
        md5_hash = hashlib.md5()
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    md5_hash.update(chunk)
                    sha256_hash.update(chunk)
            return {"md5": md5_hash.hexdigest(), "sha256": sha256_hash.hexdigest()}
        except Exception:
            return {"md5": "ERROR", "sha256": "ERROR"}
    
    def _should_skip_file(self, file_path: Path) -> bool:
        """Check if file should be skipped based on various criteria."""
        # Skip system files
        if file_path.name.lower() in ["thumbs.db", "$desktop.ini"]:
            return True
        
        # Skip files larger than max content size for content extraction
        try:
            if file_path.stat().st_size > self.max_content_size:
                return False  # Still index metadata but skip content
        except Exception:
            return True
        
        return False
    
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
            
            if not self._should_skip_file(file_path):
                content, content_preview = self.extractor.extract_content(file_path)
                if content:
                    content_available = 1
            
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
        
        # Reset stats
        self._stats = {
            'total': 0,
            'indexed': 0,
            'skipped': 0,
            'errors': 0,
            'content_extracted': 0
        }
        
        # Discover files
        files_to_scan = []
        try:
            for path in root.rglob("*"):
                if path.is_file():
                    try:
                        relative_path = path.relative_to(root)
                        depth = len(relative_path.parts)
                        if depth <= max_depth:
                            if not self._should_skip_file(path):
                                files_to_scan.append(path)
                    except ValueError:
                        pass
        except Exception as e:
            raise RuntimeError(f"Error scanning directory: {e}")
        
        self._stats['total'] = len(files_to_scan)
        
        if not files_to_scan:
            return self._stats
        
        # Process files in parallel
        # Note: Multiprocessing cannot be used with in-memory SQLite databases
        # because thread-local connections cannot be pickled.
        # Use single-threaded mode for in-memory databases.
        if workers > 1 and self.db.db_path != ":memory:":
            with ProcessPoolExecutor(max_workers=workers) as executor:
                future_to_file = {
                    executor.submit(self._process_single_file, f): f 
                    for f in files_to_scan
                }
                
                for i, future in enumerate(as_completed(future_to_file)):
                    file_data = future.result()
                    
                    if file_data:
                        self.db.insert_file(file_data)
                        self._stats['indexed'] += 1
                        if file_data.get('content'):
                            self._stats['content_extracted'] += 1
                    else:
                        self._stats['errors'] += 1
                    
                    if progress_callback:
                        progress_callback(i + 1, len(files_to_scan), str(future_to_file[future]))
        else:
            # Single-threaded for testing/debugging or in-memory databases
            for i, file_path in enumerate(files_to_scan):
                file_data = self._process_single_file(file_path)
                
                if file_data:
                    self.db.insert_file(file_data)
                    self._stats['indexed'] += 1
                    if file_data.get('content'):
                        self._stats['content_extracted'] += 1
                else:
                    self._stats['errors'] += 1
                
                if progress_callback:
                    progress_callback(i + 1, len(files_to_scan), str(file_path))
        
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


# Convenience function for single-use scenarios
def create_indexer(db_path: str = None, max_content_size: int = 10 * 1024 * 1024) -> FileIndexer:
    """Create an indexer with a database connection."""
    db = SearchDatabase(db_path)
    extractor = ContentExtractor()
    return FileIndexer(db, extractor, max_content_size)
