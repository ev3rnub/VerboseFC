"""
Content extraction module for Phase 1.
Extracts text content from various file types.
"""

import os
import re
from typing import Optional, Tuple
from pathlib import Path


# Bounded-read budgets. Without these, extraction read whole files (and whole
# archive members) into memory before truncating, so one huge .log or one
# multi-gigabyte zip member could stall or OOM a worker process.
MAX_CONTENT_CHARS = 500_000        # stored text per file
MAX_READ_BYTES = 2_000_000         # bytes read from a plain text file
ARCHIVE_MEMBER_READ_BYTES = 4096   # bytes read per archive member (we keep 1000 chars)
MAX_ARCHIVE_ENTRIES = 50           # members inspected per archive
ARCHIVE_TEXT_EXTS = ('.txt', '.md', '.csv', '.json', '.xml', '.log', '.yml', '.yaml', '.ini', '.cfg')


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
        
    def _get_text_from_file(self, file_path: Path,
                           max_bytes: int = MAX_READ_BYTES) -> Optional[str]:
        """Read at most `max_bytes` of a file and decode it as text.

        Reads a bounded prefix instead of the whole file: the old version did
        f.read() on the entire file and only then cut it to 500,000 characters,
        so a multi-gigabyte .log allocated the whole thing in the worker.
        UTF-8 with errors='replace' never raises, so the previous four-encoding
        loop only ever ran its first iteration; BOM handling is preserved.
        """
        try:
            with open(file_path, 'rb') as f:
                raw = f.read(max_bytes)
        except Exception:
            return None
        if raw.startswith(b"\xef\xbb\xbf"):      # utf-8-sig compatibility
            raw = raw[3:]
        content = raw.decode('utf-8', errors='replace')
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + "\n\n[Content truncated - file too large]"
        return content

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
                from pptx import Presentation
                prs = Presentation(file_path)
                slides = []
                for slide in prs.slides:
                    slide_text = []
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text:
                            slide_text.append(shape.text)
                    if slide_text:
                        slides.append(f"Slide {len(slides) + 1}:\n" + '\n'.join(slide_text))
                content = '\n\n'.join(slides)
                return content, self._get_preview(content)
            except Exception:
                return None, None
        
        # Archive files
        if ext in {'.zip', '.tar', '.gz', '.7z', '.rar'}:
            # For .tar.gz, check the full extension
            full_ext = ''.join(file_path.suffixes).lower()
            if full_ext == '.tar.gz':
                return self.extract_archive_content(file_path)
            elif ext == '.gz':
                # Not a tar.gz, skip
                pass
            else:
                return self.extract_archive_content(file_path)
        
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
    
    def extract_archive_content(self, file_path: Path) -> Tuple[Optional[str], Optional[str]]:
        """Extract text from .zip / .tar / .tar.gz / .7z members.

        Member reads are capped (ARCHIVE_MEMBER_READ_BYTES) and the number of
        members inspected is capped (MAX_ARCHIVE_ENTRIES), so one big archive
        can no longer dominate a scan. .rar is deliberately not handled: this
        app uses no RAR library, so those archives are indexed by metadata only.
        """
        import zipfile
        import tarfile

        def member_text(name: str, read) -> Optional[str]:
            """Keep only the first characters of a text member, labelled by name."""
            if not name.endswith(ARCHIVE_TEXT_EXTS):
                return None
            try:
                raw = read(ARCHIVE_MEMBER_READ_BYTES)
            except Exception:
                return None
            if not raw:
                return None
            return f"{name}:\n{raw.decode('utf-8', errors='replace')[:1000]}"

        ext = file_path.suffix.lower()

        try:
            if ext == '.zip':
                files = []
                with zipfile.ZipFile(file_path, 'r') as zf:
                    for info in zf.infolist():
                        if len(files) >= MAX_ARCHIVE_ENTRIES:
                            break
                        if info.is_dir():
                            continue
                        try:
                            with zf.open(info) as fh:
                                entry = member_text(info.filename, fh.read)
                        except Exception:
                            continue
                        if entry:
                            files.append(entry)
                full_content = '\n\n'.join(files)
                return full_content, self._get_preview(full_content)

            if ext == '.tar' or str(file_path).endswith('.tar.gz'):
                mode = 'r:gz' if str(file_path).endswith('.tar.gz') else 'r'
                files = []
                with tarfile.open(file_path, mode) as tf:
                    for member in tf:
                        if len(files) >= MAX_ARCHIVE_ENTRIES:
                            break
                        if not member.isfile():
                            continue
                        try:
                            fh = tf.extractfile(member)
                        except Exception:
                            continue
                        if fh is None:
                            continue
                        try:
                            entry = member_text(member.name, fh.read)
                        finally:
                            fh.close()
                        if entry:
                            files.append(entry)
                full_content = '\n\n'.join(files)
                return full_content, self._get_preview(full_content)

            if ext == '.7z':
                try:
                    import py7zr
                except ImportError:
                    return None, None
                files = []
                try:
                    with py7zr.SevenZipFile(file_path, mode='r') as z:
                        # read() only decompresses the listed members; readall()
                        # would decompress the entire archive into memory.
                        names = [en.filename for en in z.list()
                                 if not en.filename.endswith('/')][:MAX_ARCHIVE_ENTRIES]
                        if not names:
                            return None, None
                        for name, fh in z.read(names).items():
                            try:
                                entry = member_text(name, fh.read)
                            finally:
                                fh.close()
                            if entry:
                                files.append(entry)
                except Exception:
                    pass
                full_content = '\n\n'.join(files)
                return full_content, self._get_preview(full_content)

        except Exception:
            pass

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
