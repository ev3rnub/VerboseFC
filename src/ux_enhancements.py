"""
User Experience Enhancements module for Phase 5.
Implements search suggestions, search feedback, and visual enhancements.
"""

import re
from typing import Dict, List, Optional, Tuple
from db import SearchDatabase


class SearchSuggestions:
    """
    Provides search suggestions based on indexed content.
    """
    
    def __init__(self, db: SearchDatabase, max_suggestions: int = 10):
        """
        Initialize search suggestions.
        
        Args:
            db: SearchDatabase instance
            max_suggestions: Maximum number of suggestions to return
        """
        self.db = db
        self.max_suggestions = max_suggestions
        
        # Create suggestions table if not exists
        self._create_suggestions_table()
    
    def _create_suggestions_table(self):
        """Create suggestions table if it doesn't exist."""
        cursor = self.db._get_connection().cursor()
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
        self.db._get_connection().commit()
    
    def get_from_indexed_content(self, prefix: str, limit: int = 10) -> List[Dict[str, str]]:
        """
        Get suggestions from indexed file content.
        
        Args:
            prefix: Partial search term
            limit: Maximum suggestions
            
        Returns:
            List of suggestion dicts with text and type
        """
        cursor = self.db._get_connection().cursor()
        
        # Search in file names
        cursor.execute('''
            SELECT DISTINCT name as term, 'filename' as category, 'content' as suggestion_type
            FROM files
            WHERE name LIKE ?
            ORDER BY name
            LIMIT ?
        ''', (f'%{prefix}%', limit))
        
        name_suggestions = cursor.fetchall()
        
        # Search in content preview
        cursor.execute('''
            SELECT DISTINCT content_preview as term, 'preview' as category, 'content' as suggestion_type
            FROM files
            WHERE content_preview LIKE ?
            ORDER BY content_preview
            LIMIT ?
        ''', (f'%{prefix}%', limit))
        
        preview_suggestions = cursor.fetchall()
        
        # Combine and deduplicate
        seen = set()
        suggestions = []
        
        for row in list(name_suggestions) + list(preview_suggestions):
            term = row['term']
            if term and term not in seen:
                seen.add(term)
                suggestions.append({
                    'text': term,
                    'type': row['suggestion_type'],
                    'category': row['category']
                })
        
        return suggestions[:self.max_suggestions]
    
    def get_from_popular_queries(self, prefix: str, limit: int = 10) -> List[Dict[str, str]]:
        """
        Get suggestions from popular search queries.
        
        Args:
            prefix: Partial search term
            limit: Maximum suggestions
            
        Returns:
            List of suggestion dicts
        """
        cursor = self.db._get_connection().cursor()
        
        cursor.execute('''
            SELECT DISTINCT query as term, 'query' as category, 'history' as suggestion_type
            FROM search_history
            WHERE query LIKE ?
            ORDER BY query
            LIMIT ?
        ''', (f'%{prefix}%', limit))
        
        results = cursor.fetchall()
        
        return [
            {
                'text': row['term'],
                'type': row['suggestion_type'],
                'category': row['category']
            }
            for row in results if row['term']
        ][:self.max_suggestions]
    
    def get_suggestions(self, prefix: str) -> List[Dict[str, str]]:
        """
        Get combined suggestions from all sources.
        
        Args:
            prefix: Partial search term
            
        Returns:
            List of suggestion dicts
        """
        content_suggestions = self.get_from_indexed_content(prefix)
        history_suggestions = self.get_from_popular_queries(prefix)
        
        # Combine and deduplicate by text
        seen = set()
        all_suggestions = []
        
        for s in content_suggestions + history_suggestions:
            if s['text'] not in seen:
                seen.add(s['text'])
                all_suggestions.append(s)
        
        return all_suggestions[:self.max_suggestions]
    
    def add_popular_term(self, term: str, category: str = 'general'):
        """
        Track a popular search term.
        
        Args:
            term: Search term
            category: Category of the term
        """
        cursor = self.db._get_connection().cursor()
        
        # Check if term exists
        cursor.execute('SELECT id, count FROM search_suggestions WHERE term = ?', (term,))
        existing = cursor.fetchone()
        
        if existing:
            # Increment count
            cursor.execute('''
                UPDATE search_suggestions SET count = count + 1 WHERE id = ?
            ''', (existing['id'],))
        else:
            # Insert new term
            cursor.execute('''
                INSERT INTO search_suggestions (term, category, suggestion_type)
                VALUES (?, ?, 'popular')
            ''', (term, category))
        
        self.db._get_connection().commit()
    
    def get_popular_terms(self, limit: int = 10) -> List[Dict[str, str]]:
        """
        Get most popular search terms.
        
        Args:
            limit: Maximum terms to return
            
        Returns:
            List of term dicts with count
        """
        cursor = self.db._get_connection().cursor()
        
        cursor.execute('''
            SELECT term, count, category
            FROM search_suggestions
            ORDER BY count DESC
            LIMIT ?
        ''', (limit,))
        
        return [dict(row) for row in cursor.fetchall()]


class SearchFeedback:
    """
    Provides search feedback including execution time, result count, and suggestions.
    """
    
    def __init__(self, db: SearchDatabase):
        """
        Initialize search feedback.
        
        Args:
            db: SearchDatabase instance
        """
        self.db = db
    
    def format_feedback(self, 
                       query: str,
                       results_count: int,
                       execution_time: float,
                       parsed_info: Dict = None) -> Dict[str, any]:
        """
        Format search feedback.
        
        Args:
            query: Search query
            results_count: Number of results
            execution_time: Query execution time in seconds
            parsed_info: Parsed query information
            
        Returns:
            Dict with formatted feedback
        """
        feedback = {
            'query': query,
            'results_count': results_count,
            'execution_time_ms': round(execution_time * 1000, 2),
            'executed_at': None,
            'suggestions': []
        }
        
        # Format execution time
        if execution_time < 0.1:
            feedback['time_label'] = 'Very fast'
            feedback['time_color'] = 'green'
        elif execution_time < 1.0:
            feedback['time_label'] = 'Fast'
            feedback['time_color'] = 'green'
        elif execution_time < 2.0:
            feedback['time_label'] = 'Moderate'
            feedback['time_color'] = 'orange'
        else:
            feedback['time_label'] = 'Slow'
            feedback['time_color'] = 'red'
        
        # Format result count
        if results_count == 0:
            feedback['result_label'] = 'No results found'
            feedback['result_color'] = 'red'
        elif results_count < 10:
            feedback['result_label'] = 'Low results'
            feedback['result_color'] = 'orange'
        elif results_count < 100:
            feedback['result_label'] = 'Good results'
            feedback['result_color'] = 'green'
        else:
            feedback['result_label'] = 'Many results'
            feedback['result_color'] = 'green'
        
        # Add suggestions for refining search
        if results_count == 0:
            feedback['suggestions'] = self._get_refinement_suggestions(query)
        elif results_count > 50:
            feedback['suggestions'] = self._get_narrowing_suggestions(query)
        
        return feedback
    
    def _get_refinement_suggestions(self, query: str) -> List[str]:
        """Get suggestions for refining a search with no results."""
        suggestions = [
            'Try broader terms',
            'Remove some filters',
            'Check spelling',
            'Try partial matches'
        ]
        
        # Parse query for specific suggestions
        parsed = self._parse_query(query)
        
        if parsed['phrases'] and len(parsed['phrases']) > 1:
            suggestions.append('Try single words instead of phrases')
        
        if parsed['operators']['not']:
            suggestions.append('Remove NOT operators')
        
        return suggestions
    
    def _get_narrowing_suggestions(self, query: str) -> List[str]:
        """Get suggestions for narrowing a search with many results."""
        suggestions = [
            'Add more specific terms',
            'Add filters (date, size, type)',
            'Use quotes for exact match',
            'Add AND operators'
        ]
        
        parsed = self._parse_query(query)
        
        if not parsed['operators']['and']:
            suggestions.append('Try adding AND between terms')
        
        if not parsed['phrases']:
            suggestions.append('Use quotes for exact phrase match')
        
        return suggestions
    
    def _parse_query(self, query: str) -> Dict[str, any]:
        """Parse query into components."""
        result = {
            'phrases': [],
            'operators': {'and': [], 'or': [], 'not': []},
            'keywords': []
        }
        
        # Extract phrases
        phrases = re.findall(r'"([^"]+)"|\'([^\']+)\'', query)
        result['phrases'] = [''.join(p) for p in phrases]
        
        # Extract operators
        result['operators']['and'] = re.findall(r'\bAND\s+(\w+)', query, re.IGNORECASE)
        result['operators']['or'] = re.findall(r'\bOR\s+(\w+)', query, re.IGNORECASE)
        not_terms = re.findall(r'\bNOT\s+(\w+)|-(\w+)', query, re.IGNORECASE)
        result['operators']['not'] = [t[0] or t[1] for t in not_terms]
        
        # Extract keywords
        clean_query = re.sub(r'"[^"]*"|\'[^\']*\'', '', query)
        clean_query = re.sub(r'\bAND\b|\bOR\b|\bNOT\b', '', clean_query, flags=re.IGNORECASE)
        result['keywords'] = re.findall(r'\b(\w+)\b', clean_query.lower())
        
        return result
    
    def get_search_summary(self, 
                          query: str,
                          results_count: int,
                          execution_time: float,
                          total_files: int) -> str:
        """
        Get a human-readable search summary.
        
        Args:
            query: Search query
            results_count: Number of results
            execution_time: Query execution time in seconds
            total_files: Total indexed files
            
        Returns:
            Formatted summary string
        """
        summary_parts = []
        
        # Query info
        summary_parts.append(f'Searched: "{query}"')
        
        # Results info
        if results_count == 0:
            summary_parts.append('No results found')
        else:
            summary_parts.append(f'{results_count} result(s) found')
        
        # Execution time
        time_ms = round(execution_time * 1000, 2)
        summary_parts.append(f'in {time_ms}ms')
        
        # Index size context
        if total_files > 0:
            percentage = (results_count / total_files) * 100
            summary_parts.append(f'({percentage:.1f}% of {total_files} files)')
        
        return ' '.join(summary_parts)


class VisualEnhancements:
    """
    Provides visual enhancements for search results.
    """
    
    # File category icons
    CATEGORY_ICONS = {
        'document': '📄',
        'image': '🖼️',
        'video': '🎥',
        'audio': '🔊',
        'archive': '📦',
        'code': '💻',
        'data': '📊',
        'other': '📄'
    }
    
    # File category colors
    CATEGORY_COLORS = {
        'document': '#3498db',      # Blue
        'image': '#9b59b6',         # Purple
        'video': '#e74c3c',         # Red
        'audio': '#f39c12',         # Orange
        'archive': '#95a5a6',       # Gray
        'code': '#2ecc71',          # Green
        'data': '#34495e',          # Dark Blue
        'other': '#7f8c8d'          # Gray
    }
    
    def __init__(self):
        """Initialize visual enhancements."""
        pass
    
    def get_category_icon(self, category: str) -> str:
        """Get icon for a file category."""
        return self.CATEGORY_ICONS.get(category.lower(), self.CATEGORY_ICONS['other'])
    
    def get_category_color(self, category: str) -> str:
        """Get color for a file category."""
        return self.CATEGORY_COLORS.get(category.lower(), self.CATEGORY_COLORS['other'])
    
    def format_file_type(self, file_type: str, category: str = None) -> str:
        """Format file type with icon."""
        if category:
            icon = self.get_category_icon(category)
            return f'{icon} {file_type}'
        return file_type
    
    def create_result_card(self, 
                          file_data: Dict,
                          highlight_terms: List[str] = None) -> Dict[str, any]:
        """
        Create a formatted result card.
        
        Args:
            file_data: File data dict
            highlight_terms: Terms to highlight
            
        Returns:
            Dict with formatted card data
        """
        category = file_data.get('file_category', 'other')
        
        card = {
            'name': file_data.get('name', 'Unknown'),
            'path': file_data.get('full_path', ''),
            'size': file_data.get('size_human', 'Unknown'),
            'size_bytes': file_data.get('size_bytes', 0),
            'type': file_data.get('file_type', 'Unknown'),
            'category': category,
            'icon': self.get_category_icon(category),
            'color': self.get_category_color(category),
            'modified': file_data.get('modified_at', 'Unknown'),
            'content_preview': file_data.get('content_preview', ''),
            'content_available': file_data.get('content_available', 0)
        }
        
        # Highlight terms in preview
        if highlight_terms and card['content_preview']:
            preview = card['content_preview']
            for term in highlight_terms:
                preview = re.sub(
                    re.escape(term),
                    f'<mark>{term}</mark>',
                    preview,
                    flags=re.IGNORECASE
                )
            card['highlighted_preview'] = preview
        
        return card
    
    def create_search_bar(self, placeholder: str = "Search files...") -> Dict[str, any]:
        """
        Create search bar configuration.
        
        Args:
            placeholder: Placeholder text
            
        Returns:
            Dict with search bar config
        """
        return {
            'placeholder': placeholder,
            'show_search_button': True,
            'show_clear_button': True,
            'show_filters': True,
            'show_suggestions': True
        }
    
    def create_filter_chip(self, 
                          label: str,
                          value: str,
                          count: int = None,
                          active: bool = False) -> Dict[str, any]:
        """
        Create a filter chip.
        
        Args:
            label: Filter label
            value: Filter value
            count: Optional result count
            active: Whether filter is active
            
        Returns:
            Dict with filter chip config
        """
        return {
            'label': label,
            'value': value,
            'count': count,
            'active': active,
            'style': 'primary' if active else 'default'
        }
    
    def create_progress_indicator(self, 
                                  current: int,
                                  total: int,
                                  label: str = "Processing") -> Dict[str, any]:
        """
        Create a progress indicator configuration.
        
        Args:
            current: Current progress
            total: Total items
            label: Progress label
            
        Returns:
            Dict with progress indicator config
        """
        percentage = (current / total * 100) if total > 0 else 0
        
        return {
            'label': label,
            'current': current,
            'total': total,
            'percentage': round(percentage, 1),
            'show_bar': True,
            'show_text': True
        }


# Convenience functions
def create_search_suggestions(db: SearchDatabase, max_suggestions: int = 10) -> SearchSuggestions:
    """Create a search suggestions instance."""
    return SearchSuggestions(db, max_suggestions)


def create_search_feedback(db: SearchDatabase) -> SearchFeedback:
    """Create a search feedback instance."""
    return SearchFeedback(db)


def create_visual_enhancements() -> VisualEnhancements:
    """Create a visual enhancements instance."""
    return VisualEnhancements()
