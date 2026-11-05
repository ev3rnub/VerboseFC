"""
Search history for the Search tab.

Phase-3 drafts that the shipped UI never adopted (AdvancedSearch, SearchCache,
IncrementalIndexer, FullRollback) were removed on 11/05 rather than left as
unreachable code; their tables are still created in db.py for older index files.
"""

import hashlib
from typing import Dict, List
from datetime import datetime
from db import SearchDatabase


class SearchHistory:
    """
    Manages search history with persistence.
    """
    
    def __init__(self, db: SearchDatabase, max_history: int = 50):
        """
        Initialize search history manager.
        
        Args:
            db: SearchDatabase instance
            max_history: Maximum number of history entries to keep
        """
        self.db = db
        self.max_history = max_history
        self._create_history_table()
    
    def _create_history_table(self):
        """Create history table if it doesn't exist."""
        cursor = self.db._get_connection().cursor()
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
        self.db._get_connection().commit()
    
    def add(self, query: str, results_count: int = 0, query_type: str = 'fts'):
        """
        Add a search query to history.
        
        Args:
            query: Search query
            results_count: Number of results found
            query_type: Type of query
        """
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            INSERT INTO search_history (query, timestamp, result_count, category, query_hash)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(query_hash) DO UPDATE SET
                timestamp = excluded.timestamp,
                result_count = excluded.result_count
        ''', (query, datetime.now().isoformat(), results_count, query_type, hashlib.md5(query.encode()).hexdigest()))
        
        # Clean up old entries
        cursor.execute(f'''
            DELETE FROM search_history WHERE id NOT IN (
                SELECT id FROM search_history ORDER BY timestamp DESC LIMIT {self.max_history}
            )
        ''')
        
        self.db._get_connection().commit()
    
    def get_recent(self, limit: int = 10) -> List[Dict]:
        """
        Get recent search history.
        
        Args:
            limit: Maximum number of entries to return
            
        Returns:
            List of history entries
        """
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            SELECT id, query, result_count, timestamp, category
            FROM search_history
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_all(self) -> List[Dict]:
        """
        Get all search history.
        
        Returns:
            List of all history entries
        """
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            SELECT id, query, result_count, timestamp, category
            FROM search_history
            ORDER BY timestamp DESC
        ''')
        
        return [dict(row) for row in cursor.fetchall()]
    
    def clear(self):
        """Clear all search history."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('DELETE FROM search_history')
        self.db._get_connection().commit()
    
    def delete_by_id(self, history_id: int) -> bool:
        """
        Delete a specific history entry.
        
        Args:
            history_id: ID of the entry to delete
            
        Returns:
            True if deleted
        """
        cursor = self.db._get_connection().cursor()
        cursor.execute('DELETE FROM search_history WHERE id = ?', (history_id,))
        deleted = cursor.rowcount > 0
        self.db._get_connection().commit()
        return deleted


def create_search_history(db: SearchDatabase, max_history: int = 50) -> SearchHistory:
    """Create a search history manager."""
    return SearchHistory(db, max_history)
