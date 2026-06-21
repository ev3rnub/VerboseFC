"""
Search Engine module for Phase 2.
Handles search query processing, natural language parsing, and result retrieval.
"""

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from db import SearchDatabase


class SearchEngine:
    """
    Search engine that processes natural language queries and retrieves results.
    Uses SQLite FTS5 for full-text search.
    """
    
    # Date-related keywords
    DATE_KEYWORDS = {
        'today': lambda: (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0), 
                          datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)),
        'yesterday': lambda: ((datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
                              (datetime.now() - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)),
        'last week': lambda: ((datetime.now() - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0),
                              datetime.now()),
        'last month': lambda: ((datetime.now() - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0),
                               datetime.now()),
        'this week': lambda: ((datetime.now() - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0),
                              datetime.now()),
        'this month': lambda: ((datetime.now() - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0),
                               datetime.now()),
        'this year': lambda: ((datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0),
                               datetime.now())),
    }
    
    # Size-related keywords
    SIZE_KEYWORDS = {
        'large': 100 * 1024 * 1024,  # > 100MB
        'small': 1 * 1024 * 1024,     # < 1MB
        'medium': 10 * 1024 * 1024,   # 1-10MB
    }
    
    # File type aliases
    FILE_TYPE_ALIASES = {
        'pdf': '.pdf',
        'word': '.docx',
        'excel': '.xlsx',
        'powerpoint': '.pptx',
        'text': '.txt',
        'markdown': '.md',
        'image': 'image',
        'video': 'video',
        'audio': 'audio',
        'code': 'code',
        'document': 'document',
        'archive': 'archive',
    }
    
    def __init__(self, db: SearchDatabase):
        """
        Initialize the search engine.
        
        Args:
            db: SearchDatabase instance for querying
        """
        self.db = db
        self._search_history = None
        self._search_suggestions = None
        self._search_feedback = None
    
    def get_search_history(self) -> 'SearchHistory':
        """Get or create search history manager."""
        if self._search_history is None:
            from enhanced_features import SearchHistory
            self._search_history = SearchHistory(self.db)
        return self._search_history
    
    def get_search_suggestions(self) -> 'SearchSuggestions':
        """Get or create search suggestions manager."""
        if self._search_suggestions is None:
            from ux_enhancements import SearchSuggestions
            self._search_suggestions = SearchSuggestions(self.db)
        return self._search_suggestions
    
    def get_search_feedback(self) -> 'SearchFeedback':
        """Get or create search feedback manager."""
        if self._search_feedback is None:
            from ux_enhancements import SearchFeedback
            self._search_feedback = SearchFeedback(self.db)
        return self._search_feedback
    
    def parse_query(self, query: str) -> Dict[str, Any]:
        """
        Parse natural language query into structured components.
        
        Args:
            query: Natural language search query
            
        Returns:
            Dict with parsed components:
                - keywords: List of search keywords
                - phrases: List of quoted phrases
                - operators: Dict with AND/OR/NOT flags
                - dates: Date range tuple (start, end) if found
                - file_types: List of file type filters
                - size_range: Tuple (min_size, max_size) if found
                - paths: List of path filters
        """
        result = {
            'keywords': [],
            'phrases': [],
            'operators': {'and': [], 'or': [], 'not': []},
            'dates': None,
            'file_types': [],
            'size_range': (None, None),
            'paths': [],
            'raw_query': query
        }
        
        # Extract quoted phrases first
        phrase_pattern = r'"([^"]+)"|\'([^\']+)\''
        phrases = re.findall(phrase_pattern, query)
        for phrase in phrases:
            # Join the matched groups (one will be empty)
            actual_phrase = ''.join(phrase)
            result['phrases'].append(actual_phrase)
        
        # Remove phrases from query for further processing
        query_no_phrases = re.sub(phrase_pattern, '', query)
        
        # Extract operators
        and_pattern = r'\bAND\b|\bAND\b'
        or_pattern = r'\bOR\b|\bOR\b'
        not_pattern = r'\bNOT\b|\b-\b'
        
        # Extract AND terms
        and_terms = re.findall(r'\bAND\s+(\w+)', query_no_phrases, re.IGNORECASE)
        result['operators']['and'] = and_terms
        
        # Extract OR terms
        or_terms = re.findall(r'\bOR\s+(\w+)', query_no_phrases, re.IGNORECASE)
        result['operators']['or'] = or_terms
        
        # Extract NOT terms (both NOT word and -word)
        not_terms = re.findall(r'\bNOT\s+(\w+)|-(\w+)', query_no_phrases, re.IGNORECASE)
        result['operators']['not'] = [t[0] or t[1] for t in not_terms]
        
        # Remove operators from query
        query_clean = re.sub(r'\bAND\b|\bOR\b|\bNOT\b', '', query_no_phrases, flags=re.IGNORECASE)
        query_clean = re.sub(r'-\w+', '', query_clean)
        
        # Extract date references
        date_match = re.search(r'(today|yesterday|last\s+week|last\s+month|this\s+week|this\s+month|this\s+year)', 
                              query_clean, re.IGNORECASE)
        if date_match:
            date_key = date_match.group(1).lower()
            if date_key in self.DATE_KEYWORDS:
                result['dates'] = self.DATE_KEYWORDS[date_key]()
                query_clean = query_clean[:date_match.start()] + query_clean[date_match.end():]
        
        # Extract date range (e.g., "from 2024-01-01 to 2024-02-01")
        date_range_match = re.search(r'from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})', 
                                    query_clean, re.IGNORECASE)
        if date_range_match:
            try:
                start = datetime.strptime(date_range_match.group(1), '%Y-%m-%d')
                end = datetime.strptime(date_range_match.group(2), '%Y-%m-%d')
                end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
                result['dates'] = (start, end)
                query_clean = query_clean[:date_range_match.start()] + query_clean[date_range_match.end():]
            except ValueError:
                pass
        
        # Extract size references (e.g., "large", "small", ">10MB", "100KB")
        size_match = re.search(r'(large|small|medium)', query_clean, re.IGNORECASE)
        if size_match:
            size_key = size_match.group(1).lower()
            if size_key in self.SIZE_KEYWORDS:
                if size_key == 'large':
                    result['size_range'] = (self.SIZE_KEYWORDS['large'], None)
                elif size_key == 'small':
                    result['size_range'] = (None, self.SIZE_KEYWORDS['small'])
                else:
                    result['size_range'] = (self.SIZE_KEYWORDS['small'], self.SIZE_KEYWORDS['medium'])
                query_clean = query_clean[:size_match.start()] + query_clean[size_match.end():]
        
        # Extract numeric size (e.g., ">10MB", "<100KB", "1GB")
        size_numeric_match = re.search(r'(>|<|>=|<=)?\s*(\d+)\s*(KB|MB|GB|TB)?', query_clean, re.IGNORECASE)
        if size_numeric_match and not size_match:
            size_val = int(size_numeric_match.group(2))
            size_unit = size_numeric_match.group(3)
            if size_unit:
                size_unit = size_unit.upper()
                multipliers = {'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
                size_val *= multipliers[size_unit]
            
            operator = size_numeric_match.group(1) or ''
            if operator in ('>', '>='):
                result['size_range'] = (size_val, None)
            elif operator in ('<', '<='):
                result['size_range'] = (None, size_val)
            else:
                # Exact match or range
                result['size_range'] = (size_val, size_val)
            query_clean = re.sub(r'(>|<|>=|<=)?\s*\d+\s*(KB|MB|GB|TB)?', '', query_clean, flags=re.IGNORECASE)
        
        # Extract file type references
        file_type_match = re.search(r'(pdf|word|excel|powerpoint|text|markdown|image|video|audio|code|document|archive)', 
                                   query_clean, re.IGNORECASE)
        if file_type_match:
            type_key = file_type_match.group(1).lower()
            if type_key in self.FILE_TYPE_ALIASES:
                result['file_types'].append(self.FILE_TYPE_ALIASES[type_key])
                query_clean = query_clean[:file_type_match.start()] + query_clean[file_type_match.end():]
        
        # Extract extension directly (e.g., ".pdf", ".txt")
        ext_match = re.findall(r'\.(\w+)', query_clean)
        for ext in ext_match:
            result['file_types'].append('.' + ext)
        query_clean = re.sub(r'\.\w+', '', query_clean)
        
        # Extract path references (directories)
        path_match = re.findall(r'[/\\][\w\-./\\]+', query_clean)
        result['paths'] = path_match
        query_clean = re.sub(r'[/\\][\w\-./\\]+', '', query_clean)
        
        # Remaining words are keywords
        keywords = re.findall(r'\b(\w+)\b', query_clean.lower())
        result['keywords'] = [k for k in keywords if len(k) > 2]  # Filter short words
        
        return result
    
    def build_fts_query(self, parsed: Dict[str, Any]) -> str:
        """
        Build FTS5 query string from parsed components.
        
        Args:
            parsed: Dict from parse_query()
            
        Returns:
            FTS5 query string
        """
        terms = []
        
        # Handle quoted phrases exactly
        for phrase in parsed['phrases']:
            terms.append(f'"{phrase}"')
        
        # Handle AND terms
        for term in parsed['operators']['and']:
            terms.append(term)
        
        # Handle OR terms
        or_terms = []
        for term in parsed['operators']['or']:
            or_terms.append(term)
        if or_terms:
            terms.append(f'({" OR ".join(or_terms)})')
        
        # Handle NOT terms
        for term in parsed['operators']['not']:
            terms.append(f'-{term}')
        
        # Add keywords
        for keyword in parsed['keywords']:
            terms.append(keyword)
        
        return ' '.join(terms) if terms else ''
    
    def build_metadata_filter(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build metadata filter dict from parsed components.
        
        Args:
            parsed: Dict from parse_query()
            
        Returns:
            Dict with metadata filters
        """
        filters = {}
        
        if parsed['dates']:
            start, end = parsed['dates']
            filters['min_modified'] = start.timestamp()
            filters['max_modified'] = end.timestamp()
        
        if parsed['size_range'][0] is not None:
            filters['min_size'] = parsed['size_range'][0]
        
        if parsed['size_range'][1] is not None:
            filters['max_size'] = parsed['size_range'][1]
        
        # Convert file type aliases to categories
        categories = []
        extensions = []
        for ft in parsed['file_types']:
            if ft in self.FILE_TYPE_ALIASES:
                alias = self.FILE_TYPE_ALIASES[ft]
                if alias in ('image', 'video', 'audio', 'code', 'document', 'archive', 'data'):
                    categories.append(alias)
                else:
                    extensions.append(alias)
            else:
                extensions.append(ft)
        
        if categories:
            filters['file_categories'] = categories
        if extensions:
            filters['extensions'] = extensions
        
        return filters
    
    def search(self, 
               query: str, 
               limit: int = 100, 
               offset: int = 0) -> Tuple[List[Dict], int, Dict[str, Any]]:
        """
        Execute search with natural language query.
        
        Args:
            query: Natural language search query
            limit: Maximum results
            offset: Result offset for pagination
            
        Returns:
            Tuple of (results, total_count, parsed_query_info)
        """
        parsed = self.parse_query(query)
        fts_query = self.build_fts_query(parsed)
        
        # Start with empty results
        results = []
        total = 0
        
        # Build metadata filters
        metadata_filters = self.build_metadata_filter(parsed)
        
        if fts_query:
            # Perform FTS search
            try:
                results, total = self.db.search_files(fts_query, limit, offset)
            except Exception:
                # If FTS fails, fall back to metadata search
                results = []
                total = 0
        
        # Apply additional metadata filters if needed
        if metadata_filters:
            # If we have FTS results, filter them; otherwise do pure metadata search
            if not results:
                # Pure metadata search
                cat_filters = metadata_filters.get('file_categories', [])
                ext_filters = metadata_filters.get('extensions', [])
                
                # Build combined query
                combined_results = []
                combined_total = 0
                
                if cat_filters:
                    for cat in cat_filters:
                        cat_results, cat_total = self.db.search_files_by_metadata(
                            file_category=cat,
                            min_size=metadata_filters.get('min_size'),
                            max_size=metadata_filters.get('max_size'),
                            min_modified=metadata_filters.get('min_modified'),
                            max_modified=metadata_filters.get('max_modified'),
                            limit=limit,
                            offset=offset
                        )
                        combined_results.extend(cat_results)
                        combined_total += cat_total
                
                if ext_filters:
                    for ext in ext_filters:
                        ext_results, ext_total = self.db.search_files_by_metadata(
                            extension=ext,
                            min_size=metadata_filters.get('min_size'),
                            max_size=metadata_filters.get('max_size'),
                            min_modified=metadata_filters.get('min_modified'),
                            max_modified=metadata_filters.get('max_modified'),
                            limit=limit,
                            offset=offset
                        )
                        combined_results.extend(ext_results)
                        combined_total += ext_total
                
                # Remove duplicates
                seen = set()
                unique_results = []
                for r in combined_results:
                    if r['id'] not in seen:
                        seen.add(r['id'])
                        unique_results.append(r)
                
                results = unique_results
                total = len(unique_results)
        
        # Track search in history
        try:
            history = self.get_search_history()
            history.add(query, total)
        except Exception:
            pass  # Don't fail if history tracking fails
        
        # Apply pagination
        paginated_results = results[offset:offset + limit]
        
        return paginated_results, total, parsed
    
    def get_search_suggestions(self, 
                               prefix: str, 
                               limit: int = 10) -> List[Dict[str, str]]:
        """
        Get search suggestions based on prefix.
        
        Args:
            prefix: Partial search query
            limit: Maximum suggestions
            
        Returns:
            List of suggestion dicts with text and type
        """
        suggestions = []
        
        # Suggest from date keywords
        for keyword in self.DATE_KEYWORDS.keys():
            if keyword.startswith(prefix.lower()):
                suggestions.append({
                    'text': keyword,
                    'type': 'date',
                    'description': f'Search files from {keyword}'
                })
        
        # Suggest from size keywords
        for keyword in self.SIZE_KEYWORDS.keys():
            if keyword.startswith(prefix.lower()):
                suggestions.append({
                    'text': keyword,
                    'type': 'size',
                    'description': f'Search {keyword} files'
                })
        
        # Suggest from file type aliases
        for alias in self.FILE_TYPE_ALIASES.keys():
            if alias.startswith(prefix.lower()):
                suggestions.append({
                    'text': alias,
                    'type': 'file_type',
                    'description': f'Search {alias} files'
                })
        
        # Limit results
        return suggestions[:limit]
    
    def get_search_suggestions_for_prefix(self, prefix: str) -> List[Dict[str, str]]:
        """
        Get search suggestions based on prefix.
        
        Args:
            prefix: Partial search query
            
        Returns:
            List of suggestion dicts with text and type
        """
        suggestions = []
        
        # Get from indexed content
        try:
            sugg_mgr = self.get_search_suggestions()
            content_suggestions = sugg_mgr.get_from_indexed_content(prefix)
            suggestions.extend(content_suggestions)
        except Exception:
            pass
        
        # Get from search history
        try:
            history = self.get_search_history()
            history_suggestions = history.get_recent(10)
            for item in history_suggestions:
                if item['query'].lower().startswith(prefix.lower()):
                    suggestions.append({
                        'text': item['query'],
                        'type': 'history',
                        'category': 'query'
                    })
        except Exception:
            pass
        
        # Get from popular queries
        try:
            sugg_mgr = self.get_search_suggestions()
            popular = sugg_mgr.get_popular_terms(10)
            for item in popular:
                if item['term'].lower().startswith(prefix.lower()):
                    suggestions.append({
                        'text': item['term'],
                        'type': 'popular',
                        'category': item['category']
                    })
        except Exception:
            pass
        
        # Remove duplicates
        seen = set()
        unique = []
        for s in suggestions:
            if s['text'] not in seen:
                seen.add(s['text'])
                unique.append(s)
        
        return unique[:10]
    
    def highlight_terms(self, text: str, terms: List[str], max_length: int = 200) -> str:
        """
        Highlight search terms in text.
        
        Args:
            text: Text to highlight in
            terms: List of terms to highlight
            max_length: Maximum output length
            
        Returns:
            Highlighted text snippet
        """
        if not text or not terms:
            return text[:max_length] if text else ""
        
        # Escape terms for regex
        escaped_terms = [re.escape(term) for term in terms]
        pattern = re.compile('|'.join(escaped_terms), re.IGNORECASE)
        
        # Find matches and create snippet
        matches = list(pattern.finditer(text))
        if not matches:
            return text[:max_length] + "..." if len(text) > max_length else text
        
        # Get context around first match
        first_match = matches[0]
        start = max(0, first_match.start() - 50)
        end = min(len(text), first_match.end() + 50)
        
        snippet = text[start:end]
        
        # Highlight terms
        for term in terms:
            snippet = re.sub(
                re.escape(term),
                f'<mark>{term}</mark>',
                snippet,
                flags=re.IGNORECASE
            )
        
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        
        return snippet[:max_length] if len(snippet) > max_length else snippet


# Convenience function
def create_search_engine(db: SearchDatabase) -> SearchEngine:
    """Create a search engine instance."""
    return SearchEngine(db)
