"""
Content extraction module for Phase 1.
Extracts text content from various file types.
"""

import os
import re
from typing import Optional, Tuple
from pathlib import Path


class ContentExtractor:
    """Extracts text content from files for search indexing."""
    
    # File type categories for categorization
    FILE_CATEGORIES = {
        'document': {'.txt', '.md', '.csv', '.json', '.xml', '.log', '.rtf', '.html', '.htm', '.pdf', '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp'},
        'image': {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.svg', '.webp', '.ico'},
        'video': {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'},
        'audio': {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a'},
        'archive': {'.zip', '.tar', '.gz', '.rar', '.7z', '.bz2', '.xz', '.tgz'},
        'code': {'.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.hpp', '.cs', '.go', '.rs', '.php', '.rb', '.swift', '.kt', '.sql', '.sh', '.bash', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf'},
        'data': {'.db', '.sqlite', '.sqlite3', '.mdb', '.accdb', '.parquet', '.hdf', '.h5'},
    }
    
    # Text file extensions that can be read directly
    TEXT_EXTENSIONS = {'.txt', '.md', '.csv', '.json', '.xml', '.log', '.html', '.htm', '.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.hpp', '.cs', '.go', '.rs', '.php', '.rb', '.swift', '.kt', '.sql', '.sh', '.bash', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf'}
    
    def __init__(self):
        self._pdf_extractor = None
        self._docx_extractor = None
        self._xlsx_extractor = None
        self._pptx_extractor = None
        self._eml_extractor = None
        
    def _get_text_from_file(self, file_path: Path) -> Optional[str]:
        """Try to read file as text with various encodings."""
        encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                    content = f.read()
                    # Limit content to prevent memory issues
                    if len(content) > 500000:
                        return content[:500000] + "\n\n[Content truncated - file too large]"
                    return content
            except UnicodeDecodeError:
                continue
            except Exception:
                return None
        return None
    
    def _get_preview(self, content: str, max_length: int = 500) -> str:
        """Generate a preview of content (first N characters)."""
        if not content:
            return ""
        # Clean up whitespace
        content = re.sub(r'\s+', ' ', content.strip())
        if len(content) <= max_length:
            return content
        return content[:max_length] + "..."
    
    def extract_content(self, file_path: Path) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract text content and preview from a file.
        Returns (full_content, preview) or (None, None) if extraction fails.
        """
        ext = file_path.suffix.lower()
        
        # Text files - read directly
        if ext in self.TEXT_EXTENSIONS:
            content = self._get_text_from_file(file_path)
            if content:
                return content, self._get_preview(content)
            return None, None
        
        # PDF files
        if ext == '.pdf':
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(file_path)
                pages = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                content = '\n'.join(pages)
                return content, self._get_preview(content)
            except Exception:
                return None, None
        
        # Word documents
        if ext == '.docx':
            try:
                from docx import Document
                doc = Document(file_path)
                paragraphs = [p.text for p in doc.paragraphs if p.text]
                content = '\n'.join(paragraphs)
                return content, self._get_preview(content)
            except Exception:
                return None, None
        
        # Excel files
        if ext == '.xlsx':
            try:
                import openpyxl
                wb = openpyxl.load_workbook(file_path, read_only=True)
                sheets = []
                for sheet_name in wb.sheetnames:
                    sheet = wb[sheet_name]
                    sheet_data = []
                    for row in sheet.iter_rows(values_only=True):
                        row_data = [str(cell) if cell is not None else '' for cell in row]
                        sheet_data.append('\t'.join(row_data))
                    sheets.append(f"Sheet: {sheet_name}\n" + '\n'.join(sheet_data))
                content = '\n\n'.join(sheets)
                return content, self._get_preview(content)
            except Exception:
                return None, None
        
        # PowerPoint files
        if ext == '.pptx':
            try:
                from docx import Document  # Using python-pptx would be better but we'll use basic approach
                # Note: python-pptx is not in requirements yet, using alternative
                return None, None
            except Exception:
                return None, None
        
        # Email files
        if ext == '.eml':
            try:
                import email
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    msg = email.message_from_file(f)
                parts = []
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        if content_type in ['text/plain', 'text/html']:
                            charset = part.get_content_charset() or 'utf-8'
                            try:
                                text = part.get_payload(decode=True).decode(charset, errors='replace')
                                parts.append(text)
                            except Exception:
                                continue
                else:
                    charset = msg.get_content_charset() or 'utf-8'
                    try:
                        text = msg.get_payload(decode=True).decode(charset, errors='replace')
                        parts.append(text)
                    except Exception:
                        pass
                content = '\n\n'.join(parts)
                return content, self._get_preview(content)
            except Exception:
                return None, None
        
        # HTML/XML files - use BeautifulSoup if available
        if ext in {'.html', '.htm', '.xml'}:
            try:
                from bs4 import BeautifulSoup
                content = self._get_text_from_file(file_path)
                if content:
                    soup = BeautifulSoup(content, 'html.parser')
                    # Remove script and style elements
                    for script in soup(['script', 'style']):
                        script.decompose()
                    text = soup.get_text()
                    text = re.sub(r'\s+', ' ', text)
                    return text, self._get_preview(text)
            except Exception:
                pass
        
        # Other binary files - no content extraction
        return None, None
    
    def categorize_file(self, file_path: Path) -> str:
        """Categorize a file based on its extension."""
        ext = file_path.suffix.lower()
        
        for category, extensions in self.FILE_CATEGORIES.items():
            if ext in extensions:
                return category
        return 'other'
    
    def get_file_type_display(self, file_path: Path) -> str:
        """Get a display-friendly file type name."""
        ext = file_path.suffix.lower() or 'no extension'
        category = self.categorize_file(file_path)
        
        category_names = {
            'document': 'Document',
            'image': 'Image',
            'video': 'Video',
            'audio': 'Audio',
            'archive': 'Archive',
            'code': 'Code',
            'data': 'Data',
            'other': 'Other'
        }
        
        return f"{category_names.get(category, 'Other')} ({ext})"
