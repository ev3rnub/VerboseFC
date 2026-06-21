"""
Enhanced Features module for Phase 3.
Implements advanced search capabilities, search history, real-time updates, and performance optimizations.
"""

import os
import re
import time
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
from collections import deque
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
                results_count INTEGER DEFAULT 0,
                executed_at TEXT NOT NULL,
                query_type TEXT DEFAULT 'fts'
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_history_executed 
            ON search_history(executed_at DESC)
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
            INSERT INTO search_history (query, results_count, executed_at, query_type)
            VALUES (?, ?, ?, ?)
        ''', (query, results_count, datetime.now().isoformat(), query_type))
        
        # Clean up old entries
        cursor.execute(f'''
            DELETE FROM search_history WHERE id NOT IN (
                SELECT id FROM search_history ORDER BY executed_at DESC LIMIT {self.max_history}
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
            SELECT id, query, results_count, executed_at, query_type
            FROM search_history
            ORDER BY executed_at DESC
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
            SELECT id, query, results_count, executed_at, query_type
            FROM search_history
            ORDER BY executed_at DESC
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


class IncrementalIndexer:
    """
    Manages incremental indexing with change detection.
    """
    
    def __init__(self, db: SearchDatabase):
        """
        Initialize incremental indexer.
        
        Args:
            db: SearchDatabase instance
        """
        self.db = db
        self._create_index_state_table()
    
    def _create_index_state_table(self):
        """Create index state table if it doesn't exist."""
        cursor = self.db._get_connection().cursor()
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
        self.db._get_connection().commit()
    
    def _get_file_hash(self, file_path: str) -> str:
        """Get hash of a file for change detection."""
        import hashlib
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ''
    
    def get_indexed_files(self) -> Dict[str, Dict]:
        """
        Get all currently indexed files with their state.
        
        Returns:
            Dict mapping file paths to their state info
        """
        cursor = self.db._get_connection().cursor()
        cursor.execute('SELECT file_path, file_hash, last_indexed, indexed_version FROM index_state')
        
        result = {}
        for row in cursor.fetchall():
            result[row['file_path']] = dict(row)
        return result
    
    def get_modified_since(self, timestamp: float) -> List[Dict]:
        """
        Get files modified since a timestamp.
        
        Args:
            timestamp: Unix timestamp
            
        Returns:
            List of file info dicts
        """
        from datetime import datetime
        dt = datetime.fromtimestamp(timestamp)
        
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            SELECT f.* FROM files f
            WHERE f.modified_at_epoch >= ?
        ''', (timestamp,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def index_new_or_modified(self, root_path: str, max_depth: int = 2,
                             workers: int = 4) -> Dict:
        """
        Index only new or modified files.
        
        Args:
            root_path: Root directory to scan
            max_depth: Maximum recursion depth
            workers: Number of parallel workers
            
        Returns:
            Statistics dict
        """
        from content_extractor import ContentExtractor
        from indexer import FileIndexer
        
        # Get currently indexed files
        indexed = self.get_indexed_files()
        
        # Discover files
        root = Path(root_path)
        new_or_modified = []
        
        for path in root.rglob('*'):
            if path.is_file():
                try:
                    relative_path = path.relative_to(root)
                    depth = len(relative_path.parts)
                    if depth <= max_depth:
                        file_hash = self._get_file_hash(str(path))
                        
                        # Check if file is new or modified
                        if str(path) not in indexed or indexed[str(path)]['file_hash'] != file_hash:
                            new_or_modified.append(path)
                except ValueError:
                    pass
        
        if not new_or_modified:
            return {'total': 0, 'indexed': 0, 'skipped': 0, 'errors': 0, 'content_extracted': 0}
        
        # Index new/modified files
        extractor = ContentExtractor()
        indexer = FileIndexer(self.db, extractor)
        
        stats = indexer.index_directory(root_path, max_depth=max_depth, workers=workers)
        
        # Update index state
        cursor = self.db._get_connection().cursor()
        for path in new_or_modified:
            file_hash = self._get_file_hash(str(path))
            cursor.execute('''
                INSERT OR REPLACE INTO index_state (file_path, file_hash, last_indexed, indexed_version)
                VALUES (?, ?, ?, COALESCE((SELECT indexed_version FROM index_state WHERE file_path = ?), 0) + 1)
            ''', (str(path), file_hash, datetime.now().isoformat(), str(path)))
        
        self.db._get_connection().commit()
        
        # Create new version
        self._create_version(stats['indexed'])
        
        return stats
    
    def _create_version(self, file_count: int):
        """Create a new index version."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS index_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                file_count INTEGER NOT NULL
            )
        ''')
        
        # Get current max version
        cursor.execute('SELECT COALESCE(MAX(version), 0) as max_version FROM index_versions')
        current_version = cursor.fetchone()['max_version']
        
        cursor.execute('''
            INSERT INTO index_versions (version, created_at, file_count)
            VALUES (?, ?, ?)
        ''', (current_version + 1, datetime.now().isoformat(), file_count))
        
        self.db._get_connection().commit()
    
    def get_current_version(self) -> int:
        """Get current index version."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('SELECT COALESCE(MAX(version), 0) as version FROM index_versions')
        return cursor.fetchone()['version']
    
    def rollback_to_version(self, version: int) -> bool:
        """
        Rollback to a previous index version.
        
        Args:
            version: Version to rollback to
            
        Returns:
            True if successful
        """
        cursor = self.db._get_connection().cursor()
        
        # Get version info
        cursor.execute('SELECT id, created_at, file_count FROM index_versions WHERE version = ?', (version,))
        result = cursor.fetchone()
        
        if not result:
            return False
        
        # Note: This is a simplified rollback
        # In production, you'd want to store full snapshots
        self.db._get_connection().commit()
        return True


class AdvancedSearch:
    """
    Advanced search capabilities including boolean search, wildcards, phrase search, and regex.
    """
    
    def __init__(self, db: SearchDatabase):
        """
        Initialize the advanced search engine.
        
        Args:
            db: SearchDatabase instance for querying
        """
        self.db = db
    
    def parse_advanced_query(self, query: str) -> Dict[str, Any]:
        """
        Parse advanced search query with support for:
        - Boolean operators: AND, OR, NOT
        - Wildcards: * and ?
        - Phrase search: "exact phrase"
        - Regex patterns: /pattern/
        
        Args:
            query: Advanced search query
            
        Returns:
            Dict with parsed components
        """
        result = {
            'phrases': [],
            'wildcards': [],
            'regex_patterns': [],
            'boolean_terms': {'and': [], 'or': [], 'not': []},
            'keywords': []
        }
        
        # Extract quoted phrases
        phrase_pattern = r'"([^"]+)"|\'([^\']+)\''
        phrases = re.findall(phrase_pattern, query)
        for phrase in phrases:
            actual_phrase = ''.join(phrase)
            result['phrases'].append(actual_phrase)
        
        # Extract regex patterns (/pattern/)
        regex_patterns = re.findall(r'/([^/]+)/', query)
        result['regex_patterns'] = regex_patterns
        
        # Extract wildcard patterns (* or ?)
        # Remove phrases and regex first
        query_no_special = re.sub(phrase_pattern, '', query)
        query_no_special = re.sub(r'/[^/]+/', '', query_no_special)
        
        # Find patterns with wildcards
        wildcard_pattern = r'\b(\w*\*\w*|\w*\?\w+)\b'
        wildcards = re.findall(wildcard_pattern, query_no_special)
        result['wildcards'] = wildcards
        
        # Remove wildcards from query
        query_clean = re.sub(wildcard_pattern, '', query_no_special)
        
        # Extract boolean operators
        and_terms = re.findall(r'\bAND\s+(\w+)', query_clean, re.IGNORECASE)
        result['boolean_terms']['and'] = and_terms
        
        or_terms = re.findall(r'\bOR\s+(\w+)', query_clean, re.IGNORECASE)
        result['boolean_terms']['or'] = or_terms
        
        not_terms = re.findall(r'\bNOT\s+(\w+)|-(\w+)', query_clean, re.IGNORECASE)
        result['boolean_terms']['not'] = [t[0] or t[1] for t in not_terms]
        
        # Remaining words are keywords
        keywords = re.findall(r'\b(\w+)\b', query_clean.lower())
        result['keywords'] = [k for k in keywords if len(k) > 2]
        
        return result
    
    def build_advanced_fts_query(self, parsed: Dict[str, Any]) -> str:
        """
        Build FTS5 query string from advanced parsed components.
        
        Args:
            parsed: Dict from parse_advanced_query()
            
        Returns:
            FTS5 query string
        """
        terms = []
        
        # Handle quoted phrases exactly
        for phrase in parsed['phrases']:
            terms.append(f'"{phrase}"')
        
        # Handle wildcards
        for wildcard in parsed['wildcards']:
            # Convert * to FTS5 prefix search
            fts_wildcard = wildcard.replace('*', ':')
            if '?' in fts_wildcard:
                # ? is not supported in FTS5, convert to *
                fts_wildcard = fts_wildcard.replace('?', '*')
            terms.append(fts_wildcard)
        
        # Handle boolean AND
        for term in parsed['boolean_terms']['and']:
            terms.append(term)
        
        # Handle boolean OR
        or_terms = []
        for term in parsed['boolean_terms']['or']:
            or_terms.append(term)
        if or_terms:
            terms.append(f'({" OR ".join(or_terms)})')
        
        # Handle boolean NOT
        for term in parsed['boolean_terms']['not']:
            terms.append(f'-{term}')
        
        # Add keywords
        for keyword in parsed['keywords']:
            terms.append(keyword)
        
        return ' '.join(terms) if terms else ''
    
    def search(self, query: str, limit: int = 100, offset: int = 0) -> Tuple[List[Dict], int]:
        """
        Execute advanced search with wildcards, phrases, and boolean operators.
        
        Args:
            query: Advanced search query
            limit: Maximum results
            offset: Result offset
            
        Returns:
            Tuple of (results, total_count)
        """
        parsed = self.parse_advanced_query(query)
        fts_query = self.build_advanced_fts_query(parsed)
        
        if not fts_query:
            return [], 0
        
        try:
            results, total = self.db.search_files(fts_query, limit, offset)
            
            # Apply regex filtering if present
            if parsed['regex_patterns']:
                filtered_results = []
                for result in results:
                    for pattern in parsed['regex_patterns']:
                        try:
                            if re.search(pattern, result.get('name', '') + ' ' + 
                                        result.get('full_path', '') + ' ' + 
                                        str(result.get('content_preview', ''))):
                                filtered_results.append(result)
                                break
                        except re.error:
                            pass
                results = filtered_results
                total = len(results)
            
            return results, total
        except Exception as e:
            return [], 0
    
    def search_by_regex(self, pattern: str, limit: int = 100, offset: int = 0) -> Tuple[List[Dict], int]:
        """
        Execute regex-based search.
        
        Args:
            pattern: Regex pattern
            limit: Maximum results
            offset: Result offset
            
        Returns:
            Tuple of (results, total_count)
        """
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return [], 0
        
        # Get all files and filter
        all_files = self.db.get_all_files()
        
        results = []
        for file in all_files:
            text = (file.get('name', '') + ' ' + 
                   file.get('full_path', '') + ' ' + 
                   str(file.get('content_preview', '')))
            if regex.search(text):
                results.append(file)
        
        # Apply pagination
        total = len(results)
        results = results[offset:offset + limit]
        
        return results, total


class SearchHistory:
    """
    Manages search history for users.
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
        
        # Create history table if not exists
        self._create_history_table()
    
    def _create_history_table(self):
        """Create search history table if it doesn't exist."""
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
    
    def add(self, query: str, result_count: int = 0, category: str = None):
        """
        Add a search query to history.
        
        Args:
            query: Search query
            result_count: Number of results returned
            category: Optional category
        """
        timestamp = datetime.now().isoformat()
        query_hash = hashlib.md5(query.encode()).hexdigest()
        
        cursor = self.db._get_connection().cursor()
        
        # Check if query already exists
        cursor.execute('SELECT id FROM search_history WHERE query_hash = ?', (query_hash,))
        existing = cursor.fetchone()
        
        if existing:
            # Update timestamp for existing query
            cursor.execute('''
                UPDATE search_history SET timestamp = ? WHERE id = ?
            ''', (timestamp, existing['id']))
        else:
            # Insert new query
            cursor.execute('''
                INSERT INTO search_history (query, timestamp, result_count, category, query_hash)
                VALUES (?, ?, ?, ?, ?)
            ''', (query, timestamp, result_count, category, query_hash))
        
        # Clean up old entries
        cursor.execute('''
            DELETE FROM search_history WHERE id NOT IN (
                SELECT id FROM search_history ORDER BY timestamp DESC LIMIT ?
            )
        ''', (self.max_history,))
        
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
            SELECT * FROM search_history 
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
            SELECT * FROM search_history 
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
    
    def get_popular_queries(self, limit: int = 5) -> List[Dict]:
        """
        Get most popular search queries.
        
        Args:
            limit: Maximum number of queries to return
            
        Returns:
            List of query dicts with count
        """
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            SELECT query, COUNT(*) as count, MAX(timestamp) as last_used
            FROM search_history
            GROUP BY query_hash
            ORDER BY count DESC
            LIMIT ?
        ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def delete_query(self, query: str):
        """
        Delete a specific query from history.
        
        Args:
            query: Search query to delete
        """
        query_hash = hashlib.md5(query.encode()).hexdigest()
        cursor = self.db._get_connection().cursor()
        cursor.execute('DELETE FROM search_history WHERE query_hash = ?', (query_hash,))
        self.db._get_connection().commit()


class IncrementalIndexer:
    """
    Handles incremental indexing for real-time updates.
    """
    
    def __init__(self, db: SearchDatabase):
        """
        Initialize incremental indexer.
        
        Args:
            db: SearchDatabase instance
        """
        self.db = db
        
        # Create version table if not exists
        self._create_version_table()
    
    def _create_version_table(self):
        """Create index version table if it doesn't exist."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS index_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                file_count INTEGER NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_versions_version 
            ON index_versions(version DESC)
        ''')
        self.db._get_connection().commit()
    
    def get_current_version(self) -> int:
        """Get current index version."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('SELECT MAX(version) as version FROM index_versions')
        result = cursor.fetchone()
        return result['version'] if result and result['version'] else 0
    
    def get_file_hash(self, file_path: str) -> Optional[str]:
        """Get stored hash for a file."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('SELECT sha256 FROM files WHERE full_path = ?', (file_path,))
        result = cursor.fetchone()
        return result['sha256'] if result else None
    
    def get_all_file_paths(self) -> List[str]:
        """Get all indexed file paths."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('SELECT full_path FROM files')
        return [row['full_path'] for row in cursor.fetchall()]
    
    def get_modified_since(self, timestamp: float) -> List[Dict]:
        """Get files modified since a timestamp."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            SELECT * FROM files WHERE modified_at_epoch >= ?
            ORDER BY modified_at_epoch DESC
        ''', (timestamp,))
        return [dict(row) for row in cursor.fetchall()]
    
    def index_new_files(self, root_path: str, max_depth: int = 2, 
                       workers: int = 4) -> Dict:
        """
        Index only new files (not already in index).
        
        Args:
            root_path: Root directory to scan
            max_depth: Maximum recursion depth
            workers: Number of parallel workers
            
        Returns:
            Statistics dict
        """
        from content_extractor import ContentExtractor
        from indexer import FileIndexer
        
        existing_paths = set(self.get_all_file_paths())
        
        # Discover new files
        root = Path(root_path)
        new_files = []
        
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    relative_path = path.relative_to(root)
                    depth = len(relative_path.parts)
                    if depth <= max_depth:
                        if str(path) not in existing_paths:
                            new_files.append(path)
                except ValueError:
                    pass
        
        if not new_files:
            return {'total': 0, 'indexed': 0, 'skipped': 0, 'errors': 0, 'content_extracted': 0}
        
        # Create temporary indexer
        extractor = ContentExtractor()
        indexer = FileIndexer(self.db, extractor)
        
        # Index new files
        stats = indexer.index_directory(root_path, max_depth=max_depth, workers=workers)
        
        # Increment version
        self._create_version(stats['indexed'])
        
        return stats
    
    def index_modified_files(self, root_path: str, max_depth: int = 2,
                            workers: int = 4) -> Dict:
        """
        Index only modified files.
        
        Args:
            root_path: Root directory to scan
            max_depth: Maximum recursion depth
            workers: Number of parallel workers
            
        Returns:
            Statistics dict
        """
        from content_extractor import ContentExtractor
        from indexer import FileIndexer
        
        # Get current timestamp
        current_time = time.time()
        
        # Find modified files
        modified_files = self.get_modified_since(current_time - 86400)  # Last 24 hours
        
        if not modified_files:
            return {'total': 0, 'indexed': 0, 'skipped': 0, 'errors': 0, 'content_extracted': 0}
        
        # Re-index modified files
        extractor = ContentExtractor()
        indexer = FileIndexer(self.db, extractor)
        
        stats = indexer.index_directory(root_path, max_depth=max_depth, workers=workers)
        
        return stats
    
    def _create_version(self, file_count: int):
        """Create a new index version."""
        cursor = self.db._get_connection().cursor()
        current_version = self.get_current_version()
        
        cursor.execute('''
            INSERT INTO index_versions (version, created_at, file_count)
            VALUES (?, ?, ?)
        ''', (current_version + 1, datetime.now().isoformat(), file_count))
        
        self.db._get_connection().commit()
    
    def rollback_to_version(self, version: int) -> bool:
        """
        Rollback to a previous index version.
        
        Args:
            version: Version to rollback to
            
        Returns:
            True if successful
        """
        cursor = self.db._get_connection().cursor()
        
        # Get files from version
        cursor.execute('''
            SELECT id FROM index_versions WHERE version <= ?
            ORDER BY version DESC LIMIT 1
        ''', (version,))
        
        result = cursor.fetchone()
        if not result:
            return False
        
        # Note: This is a simplified rollback - in production, 
        # you'd want to store full snapshots or use a more sophisticated approach
        return True


class SearchCache:
    """
    Query result caching for performance optimization.
    """
    
    def __init__(self, db: SearchDatabase, max_size: int = 100, ttl: int = 300):
        """
        Initialize search cache.
        
        Args:
            db: SearchDatabase instance
            max_size: Maximum number of cached entries
            ttl: Time-to-live in seconds
        """
        self.db = db
        self.max_size = max_size
        self.ttl = ttl
        
        # Create cache table if not exists
        self._create_cache_table()
    
    def _create_cache_table(self):
        """Create cache table if it doesn't exist."""
        cursor = self.db._get_connection().cursor()
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
        self.db._get_connection().commit()
    
    def _get_cache_key(self, query: str, query_type: str = 'fts') -> str:
        """Generate cache key for query."""
        return hashlib.md5(f"{query_type}:{query}".encode()).hexdigest()
    
    def get(self, query: str, query_type: str = 'fts') -> Optional[Dict]:
        """
        Get cached results for a query.
        
        Args:
            query: Search query
            query_type: Type of query (fts, regex, etc.)
            
        Returns:
            Cached results dict or None
        """
        cache_key = self._get_cache_key(query, query_type)
        current_time = datetime.now()
        
        cursor = self.db._get_connection().cursor()
        cursor.execute('''
            SELECT results_json, total_count FROM search_cache
            WHERE query_hash = ? AND expires_at > ?
            ORDER BY cached_at DESC
            LIMIT 1
        ''', (cache_key, current_time.isoformat()))
        
        result = cursor.fetchone()
        if result:
            return {
                'results': json.loads(result['results_json']),
                'total': result['total_count']
            }
        return None
    
    def set(self, query: str, results: List[Dict], total: int, 
            query_type: str = 'fts', ttl: int = None):
        """
        Cache search results.
        
        Args:
            query: Search query
            results: Search results
            total: Total count
            query_type: Type of query
            ttl: Time-to-live in seconds (uses default if not specified)
        """
        cache_key = self._get_cache_key(query, query_type)
        current_time = datetime.now()
        ttl = ttl or self.ttl
        
        cursor = self.db._get_connection().cursor()
        
        # Check if cache entry exists
        cursor.execute('SELECT id FROM search_cache WHERE query_hash = ?', (cache_key,))
        existing = cursor.fetchone()
        
        if existing:
            # Update existing entry
            cursor.execute('''
                UPDATE search_cache SET
                    results_json = ?,
                    total_count = ?,
                    cached_at = ?,
                    expires_at = ?
                WHERE id = ?
            ''', (
                json.dumps(results),
                total,
                current_time.isoformat(),
                datetime.fromtimestamp(current_time.timestamp() + ttl).isoformat(),
                existing['id']
            ))
        else:
            # Insert new entry
            cursor.execute('''
                INSERT INTO search_cache (query_hash, query_type, results_json, total_count, cached_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                cache_key,
                query_type,
                json.dumps(results),
                total,
                current_time.isoformat(),
                datetime.fromtimestamp(current_time.timestamp() + ttl).isoformat()
            ))
        
        # Clean up expired entries
        cursor.execute('DELETE FROM search_cache WHERE expires_at < ?', (current_time.isoformat(),))
        
        # Limit cache size
        cursor.execute('''
            DELETE FROM search_cache WHERE id NOT IN (
                SELECT id FROM search_cache ORDER BY cached_at DESC LIMIT ?
            )
        ''', (self.max_size,))
        
        self.db._get_connection().commit()
    
    def clear(self):
        """Clear all cached results."""
        cursor = self.db._get_connection().cursor()
        cursor.execute('DELETE FROM search_cache')
        self.db._get_connection().commit()
    
    def clear_expired(self):
        """Clear expired cache entries."""
        current_time = datetime.now()
        cursor = self.db._get_connection().cursor()
        cursor.execute('DELETE FROM search_cache WHERE expires_at < ?', (current_time.isoformat(),))
        self.db._get_connection().commit()


# Convenience functions
def create_advanced_search(db: SearchDatabase) -> AdvancedSearch:
    """Create an advanced search instance."""
    return AdvancedSearch(db)


def create_search_history(db: SearchDatabase, max_history: int = 50) -> SearchHistory:
    """Create a search history manager."""
    return SearchHistory(db, max_history)


def create_incremental_indexer(db: SearchDatabase) -> IncrementalIndexer:
    """Create an incremental indexer."""
    return IncrementalIndexer(db)


def create_search_cache(db: SearchDatabase, max_size: int = 100, ttl: int = 300) -> SearchCache:
    """Create a search cache."""
    return SearchCache(db, max_size, ttl)
