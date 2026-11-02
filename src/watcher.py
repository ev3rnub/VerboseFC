"""
Folder watcher module.
Wraps watchdog's Observer to monitor multiple folders and push change
events (created / modified / deleted / moved) to a callback.
"""

import threading
import uuid
from pathlib import Path
from typing import Callable, Dict, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class _EventHandler(FileSystemEventHandler):
    """Translates watchdog events into (event_type, path, name, watch_path)."""

    def __init__(self, watch_path: str, on_event: Callable):
        self.watch_path = watch_path
        self.on_event = on_event

    def _emit(self, event_type: str, path: str):
        if not path:
            return
        name = Path(path).name
        self.on_event(event_type, path, name, self.watch_path)

    def on_created(self, event):
        if not event.is_directory:
            self._emit("created", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._emit("modified", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._emit("deleted", event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._emit("moved", f"{event.src_path} -> {event.dest_path}")


class FolderWatcher:
    """
    Manages a set of watched folders on a background Observer thread.
    Events are delivered to the on_event callback supplied at construction.
    """

    def __init__(self, on_event: Callable):
        self.on_event = on_event
        self.observer = Observer()
        self.observer.daemon = True
        self.observer.start()
        self.watches: Dict[str, Dict] = {}  # id -> {'path': str, 'wd': watchdog handle}
        self._lock = threading.Lock()

    def watch(self, path: str) -> str:
        """Start watching a folder. Returns a watch id."""
        p = Path(path).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            raise ValueError(f"Not a valid folder: {path}")
        with self._lock:
            for wid, w in self.watches.items():
                if w['path'] == str(p):
                    return wid  # already watching
            wd = self.observer.schedule(_EventHandler(str(p), self.on_event), str(p), recursive=True)
            wid = uuid.uuid4().hex[:8]
            self.watches[wid] = {'path': str(p), 'wd': wd}
            return wid

    def unwatch(self, watch_id: str) -> bool:
        """Stop watching a folder by id."""
        with self._lock:
            w = self.watches.pop(watch_id, None)
        if not w:
            return False
        try:
            self.observer.unschedule(w['wd'])
        except Exception:
            pass
        return True

    def watched_paths(self) -> list:
        with self._lock:
            return [w['path'] for w in self.watches.values()]

    def stop(self):
        """Stop the observer thread entirely."""
        try:
            self.observer.stop()
            self.observer.join(timeout=3)
        except Exception:
            pass
        with self._lock:
            self.watches.clear()

    def is_alive(self) -> bool:
        return self.observer.is_alive()
