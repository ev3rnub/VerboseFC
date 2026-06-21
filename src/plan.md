# Plan: Adding Natural Language Search to Folder Indexer

## Current State Analysis
The existing `verboseFC.py` is a Streamlit-based folder indexer that:
- Crawls directories with configurable depth
- Computes MD5 and SHA256 hashes for each file
- Uses parallel processing for efficient hashing
- Displays results in 3 tabs:
  1. **File List** - All indexed files with metadata
  2. **Duplicates** - Files with identical hashes and filename collisions
  3. **Tree View** - Visual directory structure

## Goal
Add a **4th tab** with natural language search capability that allows users to search files using conversational queries (e.g., "find all PDFs from last month", "show me large video files", "documents containing 'report'").

---

## Implementation Plan

### Phase 1: Data Enrichment & Indexing

#### 1.1 Extend File Metadata Collection
- **Add file content extraction:**
  - Text files (.txt, .md, .csv, .json, .xml, .log)
  - PDF files (using PyPDF2 or pdfplumber)
  - Office documents (.docx, .xlsx, .pptx using python-docx/openpyxl)
  - Email files (.eml)
  - Archive contents (optional: .zip, .tar.gz)

- **Add file content preview:**
  - First 200-500 characters of text content for search snippets

- **Add timestamps:**
  - Creation date, modification date, access date

- **Add file extension categorization:**
  - Document, Image, Video, Audio, Archive, Code, Data, Other

#### 1.2 Create Search Index Structure
- **SQLite database** for persistent search index (better than in-memory for larger datasets)
- **Tables:**
  - `files` - Core file metadata (path, hash, size, timestamps, extension)
  - `content` - File content text and previews
  - `tokens` - Tokenized words for full-text search (optional)
  - `tags` - User-defined or auto-generated tags

#### 1.3 Indexing Pipeline
- Process content extraction in parallel with hashing
- Store all data in SQLite database
- Create FTS5 (Full-Text Search) virtual table for efficient natural language queries

---

### Phase 2: Search Interface & Engine

#### 2.1 Search Tab UI Design
- **Search bar** with:
  - Placeholder text: "Search files by name, content, or metadata..."
  - Search button
  - Clear/Reset button
  
- **Search filters** (expandable section):
  - Date range (created/modified)
  - File type/extension
  - Size range
  - Path/Directory filter
  - File owner (if applicable)

- **Results display** with:
  - File name (clickable link to preview)
  - File path (truncated for display)
  - File size and type
  - Last modified date
  - Relevance score/ranking
  - Highlighted search terms in preview snippet

#### 2.2 Search Query Processing
- **Natural language parsing:**
  - Extract keywords and phrases
  - Identify operators (AND, OR, NOT)
  - Detect date/time references
  - Identify file type filters
  - Recognize size modifiers (large, small, >10MB)

- **Query transformation:**
  - Convert natural language to SQL/FTS query
  - Handle fuzzy matching for typos
  - Support proximity searches ("near" or "around")

#### 2.3 Search Engine Options
- **Option A: SQLite FTS5** (Recommended for simplicity)
  - Built into Python 3.14
  - No external dependencies
  - Good for local/single-user use
  
- **Option B: Whoosh**
  - Pure Python search library
  - More advanced ranking algorithms
  - Better for larger datasets
  
- **Option C: SQLite + RAG with embedding model**
  - For semantic search (understand intent, not just keywords)
  - Requires additional dependencies (sentence-transformers)
  - More complex but powerful

#### 2.4 Search Results Handling
- **Pagination** for large result sets
- **Quick preview** modal for file content
- **Export results** to CSV/JSON
- **Save search** functionality (optional)

---

### Phase 3: Enhanced Features

#### 3.1 Advanced Search Capabilities
- **Boolean search**: Support AND/OR/NOT operators
- **Wildcard search**: Support * and ? wildcards
- **Phrase search**: Quoted phrases for exact matches
- **Regex search**: Optional regex pattern matching

#### 3.2 Search History
- Save recent searches
- Quick access to previous queries
- Search history management

#### 3.3 Real-time Index Updates
- Watch for file changes (inotify/fswatch)
- Incremental indexing (only new/modified files)
- Index versioning for rollback

#### 3.4 Performance Optimizations
- Background indexing for large directories
- Index caching strategies
- Query result caching
- Async search execution

---

### Phase 4: Technical Implementation Details

#### 4.1 File Structure
```
src/
├── verboseFC.py              # Main Streamlit app (refactored)
├── indexer.py                # New: Indexing logic
├── search_engine.py          # New: Search functionality
├── content_extractor.py      # New: File content extraction
├── db.py                     # New: Database operations
└── requirements.txt          # New dependencies
```

#### 4.2 Required Dependencies (New)
- `pysqlite3` (for SQLite FTS5)
- `PyPDF2` or `pdfplumber` (PDF extraction)
- `python-docx` (Word documents)
- `openpyxl` (Excel files)
- `beautifulsoup4` (HTML/XML content)
- `python-magic` (file type detection)

#### 4.3 Database Schema
```sql
-- Files table
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    name TEXT,
    full_path TEXT UNIQUE,
    size_bytes INTEGER,
    size_human TEXT,
    md5 TEXT,
    sha256 TEXT,
    extension TEXT,
    file_type TEXT,
    created_at TEXT,
    modified_at TEXT,
    accessed_at TEXT
);

-- Content table (FTS5)
CREATE VIRTUAL TABLE content USING fts5(
    file_id,
    preview,
    full_content,
    tokenize='porter unicode61'
);

-- Tags table (optional)
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    file_id INTEGER,
    tag TEXT,
    FOREIGN KEY (file_id) REFERENCES files(id)
);
```

#### 4.4 Streamlit Tab Integration
```python
tab1, tab2, tab3, tab4 = st.tabs([
    "📄 File List", 
    "👯 Duplicates", 
    "🌳 Tree View",
    "🔍 Search"  # NEW TAB
])
```

---

### Phase 5: User Experience Enhancements

#### 5.1 Search Suggestions
- Auto-complete based on indexed content
- Popular search terms
- Recent search history

#### 5.2 Search Feedback
- Query execution time
- Number of results found
- Suggestions for refining search

#### 5.3 Visual Enhancements
- Color-coded file types in results
- Icons for different file categories
- Progress indicators for indexing

---

### Phase 6: Testing & Validation

#### 6.1 Unit Tests
- Content extraction for various file types
- Search query parsing
- FTS5 query generation
- Database operations

#### 6.2 Integration Tests
- Full indexing workflow
- Search after indexing
- Concurrent access scenarios

#### 6.3 Performance Tests
- Large directory handling (100k+ files)
- Search query latency
- Memory usage under load

---

### Phase 7: Documentation

#### 7.1 User Guide
- How to use the search feature
- Search syntax examples
- Tips for effective searching

#### 7.2 Developer Guide
- Code architecture
- Extending content extractors
- Custom search configurations

---

## Implementation Order (Recommended)

1. **Content Extraction Module** - Extract text from files
2. **Database Schema & Operations** - Set up SQLite with FTS5
3. **Search Engine Core** - Implement query processing
4. **Search Tab UI** - Build the Streamlit interface
5. **Integration & Testing** - Connect all components
6. **Documentation** - User and developer guides

---

## Potential Challenges & Solutions

| Challenge | Solution |
|-----------|----------|
| Large file indexing | Chunked processing, progress tracking |
| Binary file content | Skip or store as hex dump |
| Performance degradation | Index caching, query optimization |
| Unicode/encoding issues | UTF-8 with error handling |
| Search relevance | FTS5 ranking, custom scoring |

---

## Success Criteria

- [ ] Search tab displays results for natural language queries
- [ ] Search works with file names, content, and metadata
- [ ] Results include relevance ranking
- [ ] Search is performant (<2s for 10k files)
- [ ] Index persists between app restarts
- [ ] No breaking changes to existing functionality
