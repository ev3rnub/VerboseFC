"""
Database operations module for Phase 1.
Handles SQLite database creation and operations for the search index.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import threading


class SearchDatabase:
    """Manages SQLite database for file search indexing with FTS5 support."""
    
    def __init__(self, db_path: str = None):
        """Initialize database connection. Uses in-memory if no path provided."""
        self.db_path = db_path or ":memory:"
        self._local = threading.local()  # Thread-local storage for connections
        
    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local database connection."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(self.db_path)
            self._local.connection.row_factory = sqlite3.Row
            self._initialize_schema()
        return self._local.connection
    
    _fts_migration_lock = threading.Lock()

    def _migrate_fts_file_id(self, conn):
        """Rebuild the FTS table once if file_id is still an indexed column.

        Indexes written before file_id became UNINDEXED return false positives
        for numeric terms (the file's own id matched). Rows are copied into a
        rebuilt table, so an existing index survives the upgrade instead of
        being thrown away.
        """
        with SearchDatabase._fts_migration_lock:
            try:
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='content'"
                ).fetchone()
                if not row or 'unindexed' in (row[0] or '').lower():
                    return
                conn.execute('''
                    CREATE VIRTUAL TABLE IF NOT EXISTS content_v2 USING fts5(
                        file_id UNINDEXED,
                        preview,
                        full_content,
                        tokenize='porter unicode61'
                    )
                ''')
                conn.execute('''
                    INSERT INTO content_v2 (file_id, preview, full_content)
                    SELECT file_id, preview, full_content FROM content
                ''')
                conn.execute('DROP TABLE content')
                conn.execute('ALTER TABLE content_v2 RENAME TO content')
                conn.commit()
            except sqlite3.OperationalError:
                # Another connection already rebuilt it; this one can carry on.
                conn.rollback()

    def _initialize_schema(self):
        """Create database schema if it doesn't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Set busy timeout for concurrent access (without WAL to avoid locking issues)
        cursor.execute('PRAGMA busy_timeout=10000')
        # Note: Using default rollback journal mode for thread safety
        # WAL mode can cause locking issues with multiple threads
        
        # Files table - core file metadata
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                full_path TEXT UNIQUE NOT NULL,
                size_bytes INTEGER NOT NULL,
                size_human TEXT NOT NULL,
                md5 TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                extension TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_category TEXT NOT NULL,
                created_at TEXT,
                modified_at TEXT,
                accessed_at TEXT,
                content_available INTEGER DEFAULT 0,
                content_preview TEXT,
                created_at_epoch REAL,
                modified_at_epoch REAL,
                accessed_at_epoch REAL
            )
        ''')
        
        # Create FTS5 virtual table for full-text search
        # Using 'porter' tokenizer for stemming and 'unicode61' for proper tokenization
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS content USING fts5(
                file_id UNINDEXED,
                preview,
                full_content,
                tokenize='porter unicode61'
            )
        ''')
        # file_id is UNINDEXED on purpose: while it was an indexed column, a
        # numeric query matched the internal row id ("1" returned whatever file
        # happened to have id 1). Older index files are migrated below.
        self._migrate_fts_file_id(conn)
        
        # Tags table for user-defined or auto-generated tags
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
                UNIQUE(file_id, tag)
            )
        ''')
        
        # Search history table for Phase 3
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                result_count INTEGER DEFAULT 0,
                category TEXT,
                query_hash TEXT UNIQUE
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_history_timestamp 
            ON search_history(timestamp DESC)
        ''')
        
        # Reserved (nothing in the shipped UI writes to these three tables;
        # they stay defined so older index files keep loading and clear_all
        # has something to empty). See enhanced_features.py.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS index_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL,
                last_indexed TEXT NOT NULL,
                indexed_version INTEGER DEFAULT 1
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_state_hash ON index_state(file_hash)
        ''')
        

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS index_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                file_count INTEGER NOT NULL
            )
        ''')
        

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT NOT NULL,
                query_type TEXT NOT NULL,
                results_json TEXT NOT NULL,
                total_count INTEGER NOT NULL,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_cache_hash 
            ON search_cache(query_hash)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_cache_expires 
            ON search_cache(expires_at)
        ''')
        
        # Search suggestions table for Phase 5
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                category TEXT NOT NULL,
                suggestion_type TEXT NOT NULL,
                count INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_suggestions_term 
            ON search_suggestions(term)
        ''')
        
        # Watch alerts table for folder-change monitoring dashboard
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watch_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                watch_path TEXT,
                acknowledged INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_alerts_created
            ON watch_alerts(created_at DESC)
        ''')

        # Index for faster queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_files_path ON files(full_path)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_files_category ON files(file_category)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_files_modified ON files(modified_at_epoch)
        ''')
        
        conn.commit()
    
    def _ensure_connection(self):
        """Ensure we have a valid connection (for multi-threading scenarios)."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(self.db_path)
            self._local.connection.row_factory = sqlite3.Row
            self._initialize_schema()
    
    def insert_file(self, file_data: Dict) -> int:
        """
        Insert or update a file record.
        Returns the file ID.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Check if file already exists
        cursor.execute('SELECT id FROM files WHERE full_path = ?', (file_data['full_path'],))
        existing = cursor.fetchone()
        
        if existing:
            # Update existing record
            cursor.execute('''
                UPDATE files SET
                    name = ?,
                    size_bytes = ?,
                    size_human = ?,
                    md5 = ?,
                    sha256 = ?,
                    extension = ?,
                    file_type = ?,
                    file_category = ?,
                    created_at = ?,
                    modified_at = ?,
                    accessed_at = ?,
                    content_available = ?,
                    content_preview = ?,
                    created_at_epoch = ?,
                    modified_at_epoch = ?,
                    accessed_at_epoch = ?
                WHERE id = ?
            ''', (
                file_data['name'],
                file_data['size_bytes'],
                file_data['size_human'],
                file_data['md5'],
                file_data['sha256'],
                file_data['extension'],
                file_data['file_type'],
                file_data['file_category'],
                file_data.get('created_at'),
                file_data.get('modified_at'),
                file_data.get('accessed_at'),
                1 if file_data.get('content') else 0,
                file_data.get('content_preview'),
                file_data.get('created_at_epoch'),
                file_data.get('modified_at_epoch'),
                file_data.get('accessed_at_epoch'),
                existing['id']
            ))
            file_id = existing['id']
        else:
            # Insert new record
            cursor.execute('''
                INSERT INTO files (
                    name, full_path, size_bytes, size_human, md5, sha256,
                    extension, file_type, file_category, created_at, modified_at,
                    accessed_at, content_available, content_preview,
                    created_at_epoch, modified_at_epoch, accessed_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                file_data['name'],
                file_data['full_path'],
                file_data['size_bytes'],
                file_data['size_human'],
                file_data['md5'],
                file_data['sha256'],
                file_data['extension'],
                file_data['file_type'],
                file_data['file_category'],
                file_data.get('created_at'),
                file_data.get('modified_at'),
                file_data.get('accessed_at'),
                1 if file_data.get('content') else 0,
                file_data.get('content_preview'),
                file_data.get('created_at_epoch'),
                file_data.get('modified_at_epoch'),
                file_data.get('accessed_at_epoch')
            ))
            file_id = cursor.lastrowid
        
        # Handle FTS5 content
        content = file_data.get('content')
        if content:
            # Check if FTS5 record exists
            cursor.execute('SELECT file_id FROM content WHERE file_id = ?', (file_id,))
            if cursor.fetchone():
                # Update existing FTS5 record
                cursor.execute('''
                    UPDATE content SET preview = ?, full_content = ? WHERE file_id = ?
                ''', (file_data.get('content_preview', ''), content, file_id))
            else:
                # Insert new FTS5 record
                cursor.execute('''
                    INSERT INTO content (file_id, preview, full_content) VALUES (?, ?, ?)
                ''', (file_id, file_data.get('content_preview', ''), content))
        
        conn.commit()
        return file_id
    
    def get_file_by_id(self, file_id: int) -> Optional[Dict]:
        """Retrieve a file record by ID."""
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM files WHERE id = ?', (file_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    def get_file_by_path(self, full_path: str) -> Optional[Dict]:
        """Retrieve a file record by full path."""
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM files WHERE full_path = ?', (full_path,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    def delete_file_by_path(self, full_path: str) -> bool:
        """Delete a file record by full path and its FTS5 content."""
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get file ID
        cursor.execute('SELECT id FROM files WHERE full_path = ?', (full_path,))
        row = cursor.fetchone()
        if not row:
            return False
        
        file_id = row['id']
        
        # Delete FTS5 content first
        cursor.execute('DELETE FROM content WHERE file_id = ?', (file_id,))
        
        # Delete file record
        cursor.execute('DELETE FROM files WHERE id = ?', (file_id,))
        
        conn.commit()
        return cursor.rowcount > 0
    
    def search_files(self, query: str, limit: int = 100, offset: int = 0) -> Tuple[List[Dict], int]:
        """
        Search files using FTS5.
        Returns (results, total_count).
        """
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # FTS5 search - use bm25() for ranking (ASC = best matches first)
        cursor.execute('''
            SELECT f.*, content.rank
            FROM files f
            JOIN content ON f.id = content.file_id
            WHERE content MATCH ?
            ORDER BY bm25(content) ASC
            LIMIT ? OFFSET ?
        ''', (query, limit, offset))
        
        results = [dict(row) for row in cursor.fetchall()]
        
        # Get total count
        cursor.execute('''
            SELECT COUNT(*) as count FROM content WHERE content MATCH ?
        ''', (query,))
        total = cursor.fetchone()['count']
        
        return results, total
    
    def search_files_by_metadata(self, 
                                  name: str = None,
                                  extension: str = None,
                                  file_category: str = None,
                                  min_size: int = None,
                                  max_size: int = None,
                                  min_modified: float = None,
                                  max_modified: float = None,
                                  limit: int = 100,
                                  offset: int = 0) -> Tuple[List[Dict], int]:
        """
        Search files by metadata (without full-text search).
        Returns (results, total_count).
        """
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        
        conditions = []
        params = []
        
        if name:
            conditions.append('name LIKE ?')
            params.append(f'%{name}%')
        
        if extension:
            conditions.append('extension = ?')
            params.append(extension)
        
        if file_category:
            conditions.append('file_category = ?')
            params.append(file_category)
        
        if min_size is not None:
            conditions.append('size_bytes >= ?')
            params.append(min_size)
        
        if max_size is not None:
            conditions.append('size_bytes <= ?')
            params.append(max_size)
        
        if min_modified is not None:
            conditions.append('modified_at_epoch >= ?')
            params.append(min_modified)
        
        if max_modified is not None:
            conditions.append('modified_at_epoch <= ?')
            params.append(max_modified)
        
        where_clause = ''
        if conditions:
            where_clause = 'WHERE ' + ' AND '.join(conditions)
        
        # Get total count
        cursor.execute(f'SELECT COUNT(*) as count FROM files {where_clause}', params)
        total = cursor.fetchone()['count']
        
        # Get results
        cursor.execute(f'''
            SELECT * FROM files {where_clause}
            ORDER BY modified_at_epoch DESC
            LIMIT ? OFFSET ?
        ''', params + [limit, offset])
        
        results = [dict(row) for row in cursor.fetchall()]
        
        return results, total
    
    def get_all_files(self, limit: int = None, offset: int = 0) -> List[Dict]:
        """Retrieve all files with optional pagination."""
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if limit:
            cursor.execute('SELECT * FROM files ORDER BY modified_at_epoch DESC LIMIT ? OFFSET ?', (limit, offset))
        else:
            cursor.execute('SELECT * FROM files ORDER BY modified_at_epoch DESC')
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_all_files_meta(self) -> List[Dict]:
        """Every indexed row except content_preview.

        The dashboard dataframes load the whole index; dropping the preview text
        keeps them from carrying a few hundred characters of text for every file
        in the index.
        """
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, full_path, size_bytes, size_human, md5, sha256,
                   extension, file_type, file_category, created_at, modified_at,
                   accessed_at, content_available, created_at_epoch,
                   modified_at_epoch, accessed_at_epoch
            FROM files ORDER BY modified_at_epoch DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]

    def get_file_count(self) -> int:
        """Get total number of indexed files."""
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM files')
        return cursor.fetchone()['count']
    
    def get_content_count(self) -> int:
        """Number of files with extracted full-text content (FTS index)."""
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM content')
        return cursor.fetchone()['count']

    def delete_file(self, file_id: int) -> bool:
        """Delete a file record and its FTS5 content."""
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Delete FTS5 content first (cascading)
        cursor.execute('DELETE FROM content WHERE file_id = ?', (file_id,))
        
        # Delete file record
        cursor.execute('DELETE FROM files WHERE id = ?', (file_id,))
        
        conn.commit()
        return cursor.rowcount > 0
    
    def clear_all(self):
        """Clear all indexed data."""
        self._ensure_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM content')
        cursor.execute('DELETE FROM tags')
        cursor.execute('DELETE FROM files')
        cursor.execute('DELETE FROM search_history')
        cursor.execute('DELETE FROM index_state')
        cursor.execute('DELETE FROM index_versions')
        cursor.execute('DELETE FROM search_cache')
        cursor.execute('DELETE FROM search_suggestions')
        
        conn.commit()
    
    # --- Watch alerts ---

    def add_alert(self, event_type: str, path: str, name: str, watch_path: str = None) -> int:
        """Record a folder-change alert. Returns the alert id."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO watch_alerts (created_at, event_type, path, name, watch_path)
            VALUES (?, ?, ?, ?, ?)
        ''', (datetime.now().isoformat(timespec='seconds'), event_type, path, name, watch_path))
        # Keep the table bounded
        cursor.execute('''
            DELETE FROM watch_alerts WHERE id NOT IN (
                SELECT id FROM watch_alerts ORDER BY created_at DESC LIMIT 5000
            )
        ''')
        conn.commit()
        return cursor.lastrowid

    def get_alerts(self, limit: int = 200, include_acknowledged: bool = True) -> List[Dict]:
        """Retrieve recent watch alerts, newest first."""
        conn = self._get_connection()
        cursor = conn.cursor()
        query = 'SELECT * FROM watch_alerts'
        if not include_acknowledged:
            query += ' WHERE acknowledged = 0'
        query += ' ORDER BY created_at DESC, id DESC LIMIT ?'
        cursor.execute(query, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_unacknowledged_alert_count(self) -> int:
        """Count alerts the user has not acknowledged yet."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM watch_alerts WHERE acknowledged = 0')
        return cursor.fetchone()['count']

    def acknowledge_alert(self, alert_id: int) -> bool:
        """Mark a single alert as acknowledged."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE watch_alerts SET acknowledged = 1 WHERE id = ?', (alert_id,))
        conn.commit()
        return cursor.rowcount > 0

    def acknowledge_all_alerts(self) -> int:
        """Mark all pending alerts as acknowledged. Returns count updated."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE watch_alerts SET acknowledged = 1 WHERE acknowledged = 0')
        conn.commit()
        return cursor.rowcount

    def clear_alerts(self) -> int:
        """Delete all alerts. Returns count deleted."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM watch_alerts')
        conn.commit()
        return cursor.rowcount

    def get_indexed_mtime(self, full_path: str) -> Optional[float]:
        """Return the stored modified_at_epoch for a path, or None if not indexed."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT modified_at_epoch FROM files WHERE full_path = ?', (full_path,))
        row = cursor.fetchone()
        if row:
            return row['modified_at_epoch']
        return None

    def close(self):
        """Close all database connections."""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
    
    def __del__(self):
        """Destructor to ensure connection cleanup."""
        try:
            self.close()
        except Exception:
            pass


# Convenience function for single-use scenarios
def get_database(db_path: str = None) -> SearchDatabase:
    """Get a new database instance."""
    return SearchDatabase(db_path)
