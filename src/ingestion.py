"""Ingestion configuration for the VerboseFC index — Splunk-style.

Three kinds of settings, all stored in the same SQLite index DB:

* **Indexes / Data Inputs** — named buckets that indexed files are assigned to.
  An input is a path prefix (optionally with an extension whitelist); every
  file whose full_path starts with the prefix lands in that index. Files that
  match no input land in `default`.

* **Sourcetypes** — named rules that classify a file into a sourcetype. A rule
  matches on extension (`.log`), a path fragment, or a content regex (first
  match wins, in priority order). The effective sourcetype of a file is the
  rule's, or the built-in `file_category` when no rule matches.

* **Field Extractions** — named regexes that pull fields out of a file's
  extracted content (one capture group = one field). Extracted values are
  stored per file and become queryable VPL fields (`<name>`, or `kv:<name>`
  when the name collides with a built-in field).

Everything is *declarative*: change a rule and click **Apply** (or run
`reindex_all`) and the derived columns (`index_name`, `sourcetype`,
`extracted_json`) are recomputed for every indexed file — no re-scan needed.
"""

import json
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

# Built-in VPL field names — an extraction with one of these names is exposed
# as `kv:<name>` instead of shadowing the built-in column.
RESERVED_FIELDS = {
    'ext', 'name', 'path', 'size', 'modified', 'created', 'accessed', 'md5',
    'sha256', 'category', 'type', 'date', 'time', 'file', 'filename',
    'index', 'sourcetype', 'id', 'full_path', 'extension', 'file_type',
    'file_category', 'size_bytes', 'size_human', 'created_at', 'modified_at',
    'accessed_at', 'created_at_epoch', 'modified_at_epoch', 'accessed_at_epoch',
    'content_available', 'content_preview', 'index_name', 'extracted_json',
}

DEFAULT_INDEX = 'default'


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


class IngestionError(ValueError):
    """Raised for invalid ingestion configuration, with a readable message."""


class IngestionStore:
    """CRUD + apply for indexes, sourcetypes and field extractions."""

    def __init__(self, db):
        self.db = db
        self._init_tables()

    def _conn(self):
        return self.db._get_connection()

    def _init_tables(self):
        cur = self._conn().cursor()

        # Derived columns on `files` (added in place for existing databases).
        cur.execute("PRAGMA table_info(files)")
        have = {r['name'] for r in cur.fetchall()}
        for col in ('index_name', 'sourcetype', 'extracted_json'):
            if col not in have:
                cur.execute(f'ALTER TABLE files ADD COLUMN {col}')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_files_index ON files(index_name)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_files_styp ON files(sourcetype)')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS ingest_indexes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                path_prefix TEXT NOT NULL DEFAULT '',
                exts TEXT NOT NULL DEFAULT '',
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS ingest_sourcetypes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                exts TEXT NOT NULL DEFAULT '',
                path_contains TEXT NOT NULL DEFAULT '',
                content_regex TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS ingest_fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                regex TEXT NOT NULL,
                sourcetype TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        ''')
        self._conn().commit()
        self._ensure_derived()

    def _ensure_derived(self):
        """Backfill derived columns for rows written before ingestion existed
        (or by an indexer that doesn't set them). One-time, cheap."""
        cur = self._conn().cursor()
        cur.execute('SELECT COUNT(*) AS n FROM files WHERE index_name IS NULL')
        if cur.fetchone()['n']:
            self.reindex_all()

    # --- validation helpers --------------------------------------------------

    @staticmethod
    def _norm_exts(raw: str) -> str:
        """'.pdf, txt' -> '.pdf|.txt' (lowercase, dot-prefixed, pipe-joined)."""
        out = []
        for e in re.split(r'[,\s|]+', raw or ''):
            e = e.strip().lower().lstrip('.')
            if e:
                out.append('.' + e)
        return '|'.join(out)

    @staticmethod
    def _ext_match(exts: str, ext: str) -> bool:
        if not exts:
            return True
        return ext.lower() in exts.split('|')

    @staticmethod
    def _check_regex(pattern: str, what: str) -> None:
        try:
            re.compile(pattern)
        except re.error as e:
            raise IngestionError(f"{what}: invalid regex — {e}")

    # --- indexes / data inputs ----------------------------------------------

    def add_index(self, name: str, path_prefix: str = '', exts: str = '',
                  description: str = '') -> int:
        name = (name or '').strip().lower()
        if not name:
            raise IngestionError("index: name is required")
        if name == DEFAULT_INDEX:
            raise IngestionError(f"index: '{DEFAULT_INDEX}' is the built-in catch-all")
        path_prefix = (path_prefix or '').strip()
        cur = self._conn().cursor()
        try:
            cur.execute(
                'INSERT INTO ingest_indexes (name, path_prefix, exts, description, created_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (name, path_prefix, self._norm_exts(exts), (description or '').strip(), _now()))
        except sqlite3.IntegrityError:
            raise IngestionError(f"index: '{name}' already exists")
        self._conn().commit()
        self.reindex_all()
        return cur.lastrowid

    def delete_index(self, index_id: int) -> None:
        cur = self._conn().cursor()
        cur.execute('DELETE FROM ingest_indexes WHERE id = ?', (index_id,))
        self._conn().commit()
        self.reindex_all()

    def list_indexes(self) -> List[Dict]:
        cur = self._conn().cursor()
        cur.execute('SELECT * FROM ingest_indexes ORDER BY name')
        return [dict(r) for r in cur.fetchall()]

    def index_names(self) -> List[str]:
        return [i['name'] for i in self.list_indexes()]

    # --- sourcetypes ---------------------------------------------------------

    def add_sourcetype(self, name: str, exts: str = '', path_contains: str = '',
                       content_regex: str = '', priority: int = 100) -> int:
        name = (name or '').strip().lower()
        if not name:
            raise IngestionError("sourcetype: name is required")
        exts = self._norm_exts(exts)
        path_contains = (path_contains or '').strip()
        content_regex = (content_regex or '').strip()
        if not (exts or path_contains or content_regex):
            raise IngestionError(
                "sourcetype: give at least one matcher — exts, path_contains or content_regex")
        if content_regex:
            self._check_regex(content_regex, 'sourcetype')
        cur = self._conn().cursor()
        cur.execute(
            'INSERT INTO ingest_sourcetypes (name, exts, path_contains, content_regex, priority, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (name, exts, path_contains, content_regex, int(priority), _now()))
        self._conn().commit()
        self.reindex_all()
        return cur.lastrowid

    def delete_sourcetype(self, styp_id: int) -> None:
        cur = self._conn().cursor()
        cur.execute('DELETE FROM ingest_sourcetypes WHERE id = ?', (styp_id,))
        self._conn().commit()
        self.reindex_all()

    def list_sourcetypes(self) -> List[Dict]:
        cur = self._conn().cursor()
        cur.execute('SELECT * FROM ingest_sourcetypes ORDER BY priority, id')
        return [dict(r) for r in cur.fetchall()]

    # --- field extractions ---------------------------------------------------

    def add_field(self, name: str, regex: str, sourcetype: str = '') -> int:
        name = (name or '').strip().lower()
        if not name:
            raise IngestionError("field: name is required")
        if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise IngestionError("field: name must be letters/digits/underscore, starting with a letter")
        regex = (regex or '').strip()
        if not regex:
            raise IngestionError("field: regex is required")
        self._check_regex(regex, 'field')
        if re.compile(regex).groups < 1:
            raise IngestionError("field: regex needs at least one capture group, e.g. r'(\\w+@\\w+)'")
        sourcetype = (sourcetype or '').strip().lower()
        cur = self._conn().cursor()
        cur.execute(
            'INSERT INTO ingest_fields (name, regex, sourcetype, created_at) VALUES (?, ?, ?, ?)',
            (name, regex, sourcetype, _now()))
        self._conn().commit()
        self.reindex_all()
        return cur.lastrowid

    def delete_field(self, field_id: int) -> None:
        cur = self._conn().cursor()
        cur.execute('DELETE FROM ingest_fields WHERE id = ?', (field_id,))
        self._conn().commit()
        self.reindex_all()

    def list_fields(self) -> List[Dict]:
        cur = self._conn().cursor()
        cur.execute('SELECT * FROM ingest_fields ORDER BY id')
        return [dict(r) for r in cur.fetchall()]

    # --- apply / reindex -----------------------------------------------------

    def reindex_all(self) -> Dict:
        """Recompute index_name, sourcetype and extracted_json for every file.

        Cheap: reads the already-indexed rows (no filesystem walk, no content
        re-extraction). Returns a stats dict.
        """
        conn = self._conn()
        cur = conn.cursor()
        idx_rules = self.list_indexes()
        styp_rules = self.list_sourcetypes()
        field_rules = self.list_fields()
        # Precompile content regexes once.
        styp_rules = [dict(r, _re=re.compile(r['content_regex']) if r['content_regex'] else None)
                      for r in styp_rules]
        field_rules = [dict(r, _re=re.compile(r['regex'])) for r in field_rules]

        cur.execute('SELECT id, full_path, extension, file_category, file_type FROM files')
        files = [dict(r) for r in cur.fetchall()]

        updated = 0
        for f in files:
            index_name = self._assign_index(f['full_path'], f['extension'], idx_rules)
            sourcetype = self._assign_sourcetype(
                f['extension'], f['full_path'], f['file_category'], f['file_type'], styp_rules)
            extracted = self._extract_fields(f['id'], f['file_category'], field_rules)
            cur.execute(
                'UPDATE files SET index_name = ?, sourcetype = ?, extracted_json = ? WHERE id = ?',
                (index_name, sourcetype,
                 json.dumps(extracted) if extracted else None, f['id']))
            updated += 1
        conn.commit()
        return {'updated': updated, 'indexes': len(idx_rules),
                'sourcetypes': len(styp_rules), 'fields': len(field_rules)}

    def _assign_index(self, full_path: str, ext: str, idx_rules: List[Dict]) -> str:
        for r in idx_rules:
            if r['path_prefix'] and not (full_path or '').startswith(r['path_prefix']):
                continue
            if not self._ext_match(r['exts'], ext or ''):
                continue
            return r['name']
        return DEFAULT_INDEX

    def _assign_sourcetype(self, ext: str, full_path: str, category: str,
                           file_type: str, styp_rules: List[Dict]) -> str:
        for r in styp_rules:
            if not self._ext_match(r['exts'], ext or ''):
                continue
            if r['path_contains'] and r['path_contains'].lower() not in (full_path or '').lower():
                continue
            if r['_re'] is not None and not r['_re'].search(file_type or ''):
                continue
            return r['name']
        return (category or 'other').lower()

    def _extract_fields(self, file_id: int, category: str, field_rules: List[Dict]) -> Dict[str, str]:
        if not field_rules:
            return {}
        cur = self._conn().cursor()
        cur.execute('SELECT full_content FROM content WHERE file_id = ?', (file_id,))
        row = cur.fetchone()
        content = row['full_content'] if row else None
        if not content:
            return {}
        out: Dict[str, str] = {}
        for r in field_rules:
            if r['sourcetype'] and r['sourcetype'] != (category or '').lower():
                continue
            m = r['_re'].search(content)
            if not m:
                continue
            key = r['name'] if r['name'] not in RESERVED_FIELDS else f"kv:{r['name']}"
            out[key] = m.group(1)
        return out

    # --- reporting for the UI / VPL ------------------------------------------

    def index_stats(self) -> List[Dict]:
        """Per-index file counts + size, including the default catch-all."""
        cur = self._conn().cursor()
        cur.execute('SELECT index_name AS name, COUNT(*) AS files, '
                    'SUM(size_bytes) AS sz FROM files GROUP BY index_name ORDER BY files DESC')
        return [dict(r) for r in cur.fetchall()]

    def sourcetype_stats(self) -> List[Dict]:
        cur = self._conn().cursor()
        cur.execute('SELECT sourcetype AS name, COUNT(*) AS files FROM files '
                    'GROUP BY sourcetype ORDER BY files DESC')
        return [dict(r) for r in cur.fetchall()]

    def extracted_field_stats(self) -> List[Dict]:
        """Per extracted-field-name: how many files have a value + a sample."""
        cur = self._conn().cursor()
        cur.execute('SELECT extracted_json FROM files WHERE extracted_json IS NOT NULL')
        counts: Dict[str, int] = {}
        samples: Dict[str, str] = {}
        for r in cur.fetchall():
            try:
                d = json.loads(r['extracted_json'])
            except (ValueError, TypeError):
                continue
            for k, v in d.items():
                counts[k] = counts.get(k, 0) + 1
                samples.setdefault(k, str(v))
        return [{'field': k, 'files': counts[k], 'sample': samples.get(k, '')}
                for k in sorted(counts)]

    def extracted_field_names(self) -> List[str]:
        """Query names for every extraction rule: `<name>`, or `kv:<name>` when
        the name collides with a built-in field. Derived from the rules, not
        from extracted values, so a rule with no matches yet still shows up."""
        out = []
        for r in self.list_fields():
            key = r['name'] if r['name'] not in RESERVED_FIELDS else f"kv:{r['name']}"
            if key not in out:
                out.append(key)
        return out
