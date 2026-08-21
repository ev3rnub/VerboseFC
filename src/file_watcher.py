"""
File watcher module for Phase 3.
Real-time file system monitoring for incremental indexing updates.
"""

import time
from pathlib import Path
from typing import Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class IndexUpdateHandler(FileSystemEventHandler):
    """Handle file system events for incremental updates."""
    
    def __init__(self, indexer, db):
        self.indexer = indexer
        self.db = db
        self.pending_updates = []
        self.last_update = time.time()
        self.debounce_delay = 2  # seconds
        self._running = True
    
    def on_modified(self, event):
        if not event.is_directory:
            self._queue_update(event.src_path, 'modified')
    
    def on_created(self, event):
        if not event.is_directory:
            self._queue_update(event.src_path, 'created')
    
    def on_deleted(self, event):
        if not event.is_directory:
            self._queue_update(event.src_path, 'deleted')
    
    def _queue_update(self, path: str, event_type: str):
        """Queue file update for processing."""
        self.pending_updates.append({
            'path': path,
            'type': event_type,
            'timestamp': time.time()
        })
        
        # Process after debounce delay
        current_time = time.time()
        if current_time - self.last_update >= self.debounce_delay:
            self._process_pending_updates()
            self.last_update = current_time
    
    def _process_pending_updates(self):
        """Process queued file updates."""
        for update in self.pending_updates:
            if update['type'] == 'deleted':
                # Remove from index
                try:
                    self.db.delete_file_by_path(update['path'])
                except Exception:
                    pass
            else:
                # Index the file
                file_path = Path(update['path'])
                try:
                    self.indexer.index_file(file_path)
                except Exception:
                    pass
        
        self.pending_updates = []
    
    def stop(self):
        """Stop the handler."""
        self._running = False


class FileWatcher:
    """Manage file system watching for real-time updates."""
    
    def __init__(self, indexer, db):
        self.indexer = indexer
        self.db = db
        self.observer: Optional[Observer] = None
        self.handler: Optional[IndexUpdateHandler] = None
    
    def start_watching(self, path: str, recursive: bool = True) -> bool:
        """Start watching directory for changes."""
        try:
            self.handler = IndexUpdateHandler(self.indexer, self.db)
            self.observer = Observer()
            self.observer.schedule(self.handler, path, recursive=recursive)
            self.observer.start()
            return True
        except Exception:
            return False
    
    def stop_watching(self):
        """Stop watching directory."""
        if self.observer:
            self.observer.stop()
            if self.handler:
                self.handler.stop()
            try:
                self.observer.join(timeout=5)
            except Exception:
                pass
            self.observer = None
            self.handler = None
    
    def is_watching(self) -> bool:
        """Check if watcher is active."""
        return self.observer is not None and self.observer.is_alive()