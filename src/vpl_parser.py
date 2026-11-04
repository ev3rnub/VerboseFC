"""Mini VPL — a small Splunk-like pipeline language for the VerboseFC index.

Grammar (stages joined by '|'):

    search <terms>            full-text terms (FTS5: "phrase", prefix*, AND/OR/NOT)
    where <expr>              field filters joined by and/or, parentheses ok
                              fields: ext name path size modified created md5 sha256
                              ops: == != > >= < <= contains
                              values: pdf  "quarterly report"  1mb 500kb 2gb
                                      7d 24h (relative to now)  2025-01-15
    sort -size | sort size desc [,...]
    head N
    fields name, size_human, modified_at
    stats count | sum(size) | avg(size) | min(size) | max(size) [as alias] by ext

    Introspection (report commands — summarize the index itself):
    index list                  the index db + top-level roots, with file counts
    sourcetypes [N]             distinct file types/extensions with counts (top N)
    fieldsummary [N]            available fields: distinct values + coverage %

Example:
    search "quarterly report" not draft
    | where ext==pdf and size>1mb and modified>7d
    | sort -size | head 20 | fields name, size_human
"""

import json
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ingestion import DEFAULT_INDEX, IngestionStore


class VplError(ValueError):
    """Raised for any parse or translation error, with a human-readable message."""


# --- dynamic field map -------------------------------------------------------
# COLS is the static built-in map. When a pipeline references an ingestion
# field (`index`, `sourcetype`, or an extracted `user`/`kv:user`), the executor
# builds a per-query map that adds those as JSON-extracted SQL expressions.

class _Ctx:
    """Per-query context: the dynamic field map and any JSON params it needs."""

    def __init__(self, db):
        self.db = db
        self.cols: Dict[str, Tuple[str, str]] = dict(COLS)
        self.json_params: list = []

    def add_json(self, name: str, expr: str, vtype: str) -> None:
        """Register an extracted field as a JSON-extract expression under `name`."""
        self.json_params.append(expr)
        self.cols[name] = (f'({expr})', vtype)


def _build_ctx(db, query: str) -> _Ctx:
    """Extend the field map with ingestion fields referenced in `query`."""
    ctx = _Ctx(db)
    ctx.cols['index'] = ('index_name', 'text')
    ctx.cols['sourcetype'] = ('sourcetype', 'text')
    try:
        names = IngestionStore(db).extracted_field_names()
    except Exception:
        names = []
    for n in names:
        # `kv:<name>` is the query name for an extraction whose plain name would
        # shadow a built-in field; register it under the kv: name itself.
        key = n if n.startswith('kv:') and n[3:] in ctx.cols else \
            (n[3:] if n.startswith('kv:') else n)
        if key in ctx.cols:
            continue
        expr = "json_extract(extracted_json, '$." + n.replace("'", "''") + "')"
        ctx.add_json(key, expr, 'text')
    return ctx


# --- field map: VPL name -> (sql column, value type) -----------------------

COLS = {
    'ext': ('extension', 'text'),
    'name': ('name', 'text'),
    'path': ('full_path', 'text'),
    'size': ('size_bytes', 'bytes'),
    'modified': ('modified_at_epoch', 'epoch'),
    'created': ('created_at_epoch', 'epoch'),
    'md5': ('md5', 'text'),
    'sha256': ('sha256', 'text'),
    'category': ('file_category', 'text'),
    'type': ('file_type', 'text'),
    # aliases
    'date': ('modified_at_epoch', 'epoch'),
    'time': ('modified_at_epoch', 'epoch'),
    'file': ('name', 'text'),
    'filename': ('name', 'text'),
}

SIZE_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*(b|kb|mb|gb|tb)$', re.I)
TIME_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*(s|m|h|d|w)$', re.I)
DATE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')
UNIT = {'b': 1, 'kb': 1024, 'mb': 1024**2, 'gb': 1024**3, 'tb': 1024**4}
TUNIT = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}

DEFAULT_LIMIT = 1000


# --- stages -----------------------------------------------------------------

class SearchStage:
    def __init__(self, match: str):
        self.match = match

class WhereStage:
    def __init__(self, sql: str, params: tuple):
        self.sql, self.params = sql, params

class SortStage:
    def __init__(self, order: str):
        self.order = order

class HeadStage:
    def __init__(self, n: int):
        self.n = n

class FieldsStage:
    def __init__(self, cols: List[str]):
        self.cols = cols

class StatsStage:
    def __init__(self, select: str, group: Optional[str]):
        self.select, self.group = select, group

class SourcetypesStage:
    def __init__(self, limit: int):
        self.limit = limit

class FieldSummaryStage:
    def __init__(self, limit: int):
        self.limit = limit

class IndexesStage:
    pass

class IndexAddStage:
    def __init__(self, name: str, path_prefix: str, exts: str):
        self.name, self.path_prefix, self.exts = name, path_prefix, exts

class IndexRmStage:
    def __init__(self, name: str):
        self.name = name

class SourcetypeAddStage:
    def __init__(self, name: str, exts: str, path_contains: str, content_regex: str):
        self.name, self.exts, self.path_contains, self.content_regex = \
            name, exts, path_contains, content_regex

class ExtractAddStage:
    def __init__(self, name: str, regex: str, sourcetype: str):
        self.name, self.regex, self.sourcetype = name, regex, sourcetype


# --- helpers ----------------------------------------------------------------

def _split_pipes(query: str) -> List[str]:
    """Split on '|' outside quotes."""
    parts, cur, quote = [], [], None
    for ch in query:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in '"\'':
            quote = ch
            cur.append(ch)
        elif ch == '|':
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append(''.join(cur))
    return [p.strip() for p in parts if p.strip()]


def _normalize_fts(terms: str) -> str:
    """Uppercase bare AND/OR/NOT outside quotes; keep everything else verbatim."""
    out, i, n = [], 0, len(terms)
    while i < n:
        ch = terms[i]
        if ch in '"\'':
            j = terms.find(ch, i + 1)
            j = n if j == -1 else j + 1
            out.append(terms[i:j])
            i = j
            continue
        m = re.match(r'[A-Za-z]+', terms[i:])
        if m:
            word = m.group(0)
            if word.lower() in ('and', 'or', 'not'):
                out.append(word.upper())
            else:
                out.append(word)
            i += len(word)
            continue
        out.append(ch)
        i += 1
    return ''.join(out).strip()


def _convert_value(raw: str, vtype: str) -> object:
    """Convert a VPL literal to a SQL parameter based on the column type."""
    raw = raw.strip()
    if raw.startswith(('"', "'")) and raw.endswith(('"', "'")) and len(raw) >= 2:
        return raw[1:-1]
    if vtype == 'bytes':
        m = SIZE_RE.match(raw)
        if m:
            return int(float(m.group(1)) * UNIT[m.group(2).lower()])
        if re.fullmatch(r'\d+', raw):
            return int(raw)
        raise VplError(f"'{raw}' is not a size (use e.g. 500kb, 1mb, 2gb or a byte count)")
    if vtype == 'epoch':
        m = TIME_RE.match(raw)
        if m:
            return time.time() - float(m.group(1)) * TUNIT[m.group(2).lower()]
        m = DATE_RE.match(raw)
        if m:
            return datetime(*map(int, m.groups())).timestamp()
        if re.fullmatch(r'\d+(\.\d+)?', raw):
            return float(raw)
        raise VplError(f"'{raw}' is not a time (use e.g. 7d, 24h or 2025-01-15)")
    return raw.lower() if raw else raw


# --- where expression parser (recursive descent) -----------------------------

_TOK_RE = re.compile(r"""\(|\)|==|!=|>=|<=|=|>|<|"[^"]*"|'[^']*'|[^\s()=!<>|]+""")

_OPS = {'==': '=', '=': '=', '!=': '<>', '>': '>', '>=': '>=', '<': '<', '<=': '<='}


class _WhereParser:
    def __init__(self, text: str, cols: Dict[str, Tuple[str, str]]):
        self.toks = _TOK_RE.findall(text)
        self.i = 0
        self.cols = cols

    def peek(self) -> Optional[str]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> str:
        if self.i >= len(self.toks):
            raise VplError("where: expression ended unexpectedly")
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self) -> Tuple[str, tuple]:
        sql, params = self.parse_or()
        if self.peek() is not None:
            raise VplError(f"where: unexpected token '{self.peek()}'")
        return sql, params

    def parse_or(self):
        sql, params = self.parse_and()
        while (self.peek() or '').lower() == 'or':
            self.next()
            r_sql, r_params = self.parse_and()
            sql, params = f"({sql} OR {r_sql})", params + r_params
        return sql, params

    def parse_and(self):
        sql, params = self.parse_atom()
        while (self.peek() or '').lower() == 'and':
            self.next()
            r_sql, r_params = self.parse_atom()
            sql, params = f"({sql} AND {r_sql})", params + r_params
        return sql, params

    def parse_atom(self):
        tok = self.peek()
        if tok == '(':
            self.next()
            sql, params = self.parse_or()
            if self.next() != ')':
                raise VplError("where: missing ')'")
            return sql, params
        return self.parse_comparison()

    def parse_comparison(self):
        field = self.next().lower()
        if field not in self.cols:
            raise VplError(f"where: unknown field '{field}' "
                           f"(fields: {', '.join(sorted(self.cols))})")
        col, vtype = self.cols[field]
        op = self.next()
        if op.lower() == 'contains':
            val = _convert_value(self.next(), 'text')
            return f"lower({col}) LIKE ?", (f"%{str(val).lower()}%",)
        if op not in _OPS:
            raise VplError(f"where: unknown operator '{op}' (use == != > >= < <= contains)")
        val = _convert_value(self.next(), vtype)
        if field == 'ext':
            # Index stores '.md'; accept 'md' or '.md' by comparing without the dot.
            return f"ltrim({col}, '.') {_OPS[op]} ?", (str(val).lstrip('.'),)
        if vtype == 'text' and _OPS[op] in ('=', '<>'):
            return f"lower({col}) {_OPS[op]} ?", (val,)
        return f"{col} {_OPS[op]} ?", (val,)


def _parse_where(body: str, cols: Dict[str, Tuple[str, str]] = None) -> WhereStage:
    if not body.strip():
        raise VplError("where: empty expression")
    sql, params = _WhereParser(body, cols or COLS).parse()
    return WhereStage(f"({sql})", tuple(params))


# --- other stage parsers ------------------------------------------------------

def _parse_search(body: str) -> SearchStage:
    match = _normalize_fts(body)
    if not match:
        raise VplError("search: no terms (e.g. search \"quarterly report\" not draft)")
    return SearchStage(match)


def _parse_sort(body: str, cols: Dict[str, Tuple[str, str]] = None) -> SortStage:
    cols = cols or COLS
    parts = [p.strip() for p in body.split(',') if p.strip()]
    if not parts:
        raise VplError("sort: expected e.g. sort -size or sort size desc")
    frags = []
    for p in parts:
        toks = p.split()
        field, direction = toks[0], (toks[1].lower() if len(toks) > 1 else 'asc')
        if field.startswith('-'):
            field, direction = field[1:], 'desc'
        if field not in cols:
            raise VplError(f"sort: unknown field '{field}' (fields: {', '.join(sorted(cols))})")
        if direction not in ('asc', 'desc'):
            raise VplError(f"sort: '{direction}' is not asc/desc")
        frags.append(f"{cols[field][0]} {direction.upper()}")
    return SortStage(', '.join(frags))


def _parse_head(body: str) -> HeadStage:
    if not re.fullmatch(r'\d+', body.strip()):
        raise VplError(f"head: expected a number, got '{body.strip()}'")
    return HeadStage(int(body.strip()))


def _parse_fields(body: str) -> FieldsStage:
    cols = [c.strip() for c in body.split(',') if c.strip()]
    if not cols:
        raise VplError("fields: expected e.g. fields name, size_human, modified_at")
    return FieldsStage(cols)


_AGG_RE = re.compile(r'^(count|sum|avg|min|max)\s*(?:\(\s*(\w+)\s*\))?(?:\s+as\s+(\w+))?$', re.I)


def _parse_stats(body: str, cols: Dict[str, Tuple[str, str]] = None) -> StatsStage:
    cols = cols or COLS
    m = re.match(r'^(.*?)(?:\s+by\s+(\w+))?\s*$', body, re.I)
    aggs_txt, group = (m.group(1) or '').strip(), (m.group(2) or '').lower() or None
    if not aggs_txt:
        raise VplError("stats: expected e.g. stats count by ext")
    if group and group not in cols:
        raise VplError(f"stats: cannot group by '{group}' (fields: {', '.join(sorted(cols))})")
    select_frags, seen_alias = [], set()
    for chunk in aggs_txt.split(','):
        am = _AGG_RE.match(chunk.strip())
        if not am:
            raise VplError(f"stats: cannot parse '{chunk.strip()}' "
                           "(use count, sum(size), avg(size), min(size), max(size))")
        func, col, alias = am.group(1).lower(), (am.group(2) or '').lower(), am.group(3)
        if func != 'count':
            if col not in cols:
                raise VplError(f"stats: {func}() needs a numeric field (size, modified, created)")
            if cols[col][1] == 'text':
                raise VplError(f"stats: {func}({col}) — {col} is not numeric")
            sql_col = cols[col][0]
        else:
            sql_col = '*'
        alias = alias or (f"{func}_{col}" if func != 'count' and col else func)
        if alias in seen_alias:
            raise VplError(f"stats: duplicate output column '{alias}'")
        seen_alias.add(alias)
        select_frags.append(f'{func.upper()}({sql_col}) AS "{alias}"')
    if group:
        gcol = cols[group][0]
        select_frags.insert(0, f'{gcol} AS "{group}"')
    return StatsStage(', '.join(select_frags), cols[group][0] if group else None)


# --- introspection stage parsers ----------------------------------------------

def _parse_limit(body: str, cmd: str, default: int = 20) -> int:
    body = body.strip()
    if not body:
        return default
    if not re.fullmatch(r'\d+', body):
        raise VplError(f"{cmd}: expected a number (e.g. {cmd} 10), got '{body}'")
    return int(body)


def _parse_sourcetypes(body: str) -> SourcetypesStage:
    return SourcetypesStage(_parse_limit(body, 'sourcetypes'))


def _parse_fieldsummary(body: str) -> FieldSummaryStage:
    return FieldSummaryStage(_parse_limit(body, 'fieldsummary'))


def _parse_index(body: str) -> IndexesStage:
    if body.strip().lower() not in ('', 'list'):
        raise VplError(f"index: expected 'index list', got 'index {body.strip()}'")
    return IndexesStage()


def _parse_index_add(body: str) -> IndexAddStage:
    m = re.match(r'^(\S+)\s+(.*)$', body.strip())
    if not m:
        raise VplError("index add: expected 'index add <name> <path> [exts=pdf,log]'")
    name, rest = m.group(1).lower(), m.group(2)
    exts = ''
    em = re.search(r'\bexts?=([^\s]+)', rest)
    if em:
        exts = em.group(1)
        rest = rest[:em.start()] + rest[em.end():]
    path_prefix = rest.strip()
    if not path_prefix:
        raise VplError("index add: needs a path prefix (or exts=…) after the name")
    return IndexAddStage(name, path_prefix, exts)


def _parse_index_rm(body: str) -> IndexRmStage:
    name = body.strip().lower()
    if not name:
        raise VplError("index rm: expected 'index rm <name>'")
    return IndexRmStage(name)


def _parse_sourcetype_add(body: str) -> SourcetypeAddStage:
    m = re.match(r'^(\S+)\s+(.*)$', body.strip())
    if not m:
        raise VplError("sourcetype add: expected "
                       "'sourcetype add <name> exts=log path=/logs/ content=\"regex\"'")
    name, rest = m.group(1).lower(), m.group(2)
    exts = path_contains = content_regex = ''
    em = re.search(r'\bexts?=([^\s]+)', rest)
    if em:
        exts = em.group(1); rest = rest[:em.start()] + rest[em.end():]
    pm = re.search(r'\bpath(?:_contains)?=([^\s]+)', rest)
    if pm:
        path_contains = pm.group(1); rest = rest[:pm.start()] + rest[pm.end():]
    cm = re.search(r'\bcontent(?:_regex)?=("([^"]*)"|\'([^\']*)\'|(\S+))', rest)
    if cm:
        content_regex = cm.group(2) or cm.group(3) or cm.group(4) or ''
        rest = rest[:cm.start()] + rest[cm.end():]
    if not (exts or path_contains or content_regex):
        raise VplError("sourcetype add: needs exts=…, path=… or content=\"regex\"")
    return SourcetypeAddStage(name, exts, path_contains, content_regex)


def _parse_extract_add(body: str) -> ExtractAddStage:
    m = re.match(r'^(\S+)\s+("((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')'
                 r'(?:\s+sourcetype=(\S+))?\s*$', body.strip())
    if not m:
        raise VplError('extract add: expected '
                       'extract add <name> "regex(with (group))" [sourcetype=text]')
    name = m.group(1).lower()
    regex = (m.group(3) if m.group(3) is not None else m.group(4)) or ''
    regex = regex.replace('\\"', '"').replace("\\'", "'")
    sourcetype = (m.group(5) or '').lower()
    return ExtractAddStage(name, regex, sourcetype)


# --- pipeline parse -----------------------------------------------------------

def _dispatch_index(body: str):
    sub = body.strip().split(None, 1)
    cmd = (sub[0].lower() if sub else '')
    rest = sub[1] if len(sub) > 1 else ''
    if cmd in ('', 'list'):
        return _parse_index(body)
    if cmd == 'add':
        return _parse_index_add(rest)
    if cmd in ('rm', 'remove', 'delete'):
        return _parse_index_rm(rest)
    raise VplError(f"index: unknown subcommand '{cmd}' (list | add | rm)")


def _dispatch_sourcetype(body: str):
    sub = body.strip().split(None, 1)
    cmd = (sub[0].lower() if sub else '')
    rest = sub[1] if len(sub) > 1 else ''
    if cmd == 'add':
        return _parse_sourcetype_add(rest)
    # bare `sourcetypes [N]` (registered under the plural name)
    return _parse_sourcetypes(body)


def _dispatch_extract(body: str):
    sub = body.strip().split(None, 1)
    cmd = (sub[0].lower() if sub else '')
    rest = sub[1] if len(sub) > 1 else ''
    if cmd == 'add':
        return _parse_extract_add(rest)
    raise VplError("extract: expected 'extract add <name> \"regex\" [sourcetype=…]'")


_STAGE_PARSERS = {
    'search': _parse_search, 'where': _parse_where, 'sort': _parse_sort,
    'head': _parse_head, 'fields': _parse_fields, 'stats': _parse_stats,
    'sourcetypes': _parse_sourcetypes, 'fieldsummary': _parse_fieldsummary,
    'index': _dispatch_index, 'indexes': _dispatch_index,
    'sourcetype': _dispatch_sourcetype,
    'extract': _dispatch_extract,
}

# Commands that mutate ingestion config (run at execute time, not query time).
_CONFIG_STAGES = (IndexAddStage, IndexRmStage, SourcetypeAddStage, ExtractAddStage)


def parse_vpl(query: str, db=None) -> list:
    if not query or not query.strip():
        raise VplError("empty query")
    cols = _build_ctx(db, query).cols if db is not None else COLS
    stages = []
    for raw in _split_pipes(query):
        cmd, _, body = raw.partition(' ')
        parser = _STAGE_PARSERS.get(cmd.lower())
        if parser is None:
            raise VplError(f"unknown command '{cmd}' "
                           f"(commands: {', '.join(_STAGE_PARSERS)})")
        stages.append(parser(body, cols) if cmd.lower() in ('where', 'sort', 'stats')
                      else parser(body))
    if not any(isinstance(s, (SearchStage, WhereStage, StatsStage, SourcetypesStage,
                              FieldSummaryStage, IndexesStage, _CONFIG_STAGES))
               for s in stages):
        raise VplError("pipeline needs at least a search, where, or report command "
                       "(index list / sourcetypes / fieldsummary)")
    return stages


# --- execution ----------------------------------------------------------------

class VplResult:
    def __init__(self, df: pd.DataFrame, summary: str, total: int):
        self.df, self.summary, self.total = df, summary, total


def _build_base(stages: list, ctx: _Ctx = None):
    """Shared FROM/WHERE fragment for a pipeline's search+where stages."""
    searches = [s.match for s in stages if isinstance(s, SearchStage)]
    wheres = [s for s in stages if isinstance(s, WhereStage)]
    where_sql = ' AND '.join(w.sql for w in wheres) or '1=1'
    params: list = [p for w in wheres for p in w.params]
    # JSON-extracted field expressions are inlined into the SQL (not bound as
    # placeholders), so they never contribute params.

    if searches:
        match = ' AND '.join(f"({m})" for m in searches)
        base = ("FROM files f JOIN content ON f.id = content.file_id "
                "WHERE content MATCH ? AND " + where_sql)
        params.insert(0, match)
    else:
        base = "FROM files f WHERE " + where_sql
    return base, tuple(params)


def add_time_range(stages: list, min_epoch, max_epoch) -> list:
    """Append a modified-time range (from the time picker) to a pipeline.
    Config pipelines (index add/rm, sourcetype add, extract add) ignore it."""
    if any(isinstance(s, _CONFIG_STAGES) for s in stages):
        return stages
    out = list(stages)
    conds, params = [], []
    if min_epoch is not None:
        conds.append('modified_at_epoch >= ?')
        params.append(min_epoch)
    if max_epoch is not None:
        conds.append('modified_at_epoch <= ?')
        params.append(max_epoch)
    if conds:
        out.append(WhereStage('(' + ' AND '.join(conds) + ')', tuple(params)))
    return out


def vpl_count(db, stages: list) -> int:
    """Total number of files matched by the pipeline's filters."""
    base, params = _build_base(stages)
    cur = db._get_connection().cursor()
    cur.execute(f'SELECT COUNT(*) AS n {base}', params)
    return cur.fetchone()['n']


def vpl_field_counts(db, stages: list, field: str, limit: int = 12) -> list:
    """Top values of a VPL field within the matched set: [(value, count), ...]."""
    ctx = _build_ctx(db, '')
    if field not in ctx.cols:
        raise VplError(f"field counts: unknown field '{field}'")
    col = ctx.cols[field][0]
    base, params = _build_base(stages, ctx)
    cur = db._get_connection().cursor()
    cur.execute(f'SELECT {col} AS v, COUNT(*) AS n {base} '
                f'GROUP BY {col} ORDER BY n DESC LIMIT ?', params + (limit,))
    return [(r['v'], r['n']) for r in cur.fetchall()]


_TIMELINE_LADDER = [60, 300, 900, 3600, 10800, 21600, 86400, 259200, 604800, 2592000]


def vpl_timeline(db, stages: list, max_buckets: int = 40) -> list:
    """Match counts bucketed by modified time: [(bucket_epoch, count), ...]."""
    base, params = _build_base(stages)
    cur = db._get_connection().cursor()
    cur.execute(f'SELECT MIN(modified_at_epoch) AS lo, MAX(modified_at_epoch) AS hi {base}',
                params)
    row = cur.fetchone()
    lo, hi = row['lo'], row['hi']
    if lo is None or hi is None:
        return []
    span = max(1.0, hi - lo)
    w = _TIMELINE_LADDER[-1]
    for cand in _TIMELINE_LADDER:
        if span / cand <= max_buckets:
            w = cand
            break
    cur.execute(f'SELECT (CAST(modified_at_epoch / ? AS INTEGER) * ?) AS b, COUNT(*) AS n '
                f'{base} GROUP BY b ORDER BY b', (w, w) + params)
    return [(r['b'], r['n']) for r in cur.fetchall()]


_SUMMARY_FIELDS = ['name', 'full_path', 'extension', 'file_type', 'file_category',
                   'size_bytes', 'md5', 'sha256', 'created_at', 'modified_at',
                   'content_available', 'index_name', 'sourcetype']


def _human_bytes(n) -> str:
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f"{int(n)} {unit}" if unit == 'B' else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _run_index_list(db) -> pd.DataFrame:
    """`index list`: the index db itself + top-level path roots as pseudo-indexes."""
    cur = db._get_connection().cursor()
    cur.execute('SELECT COUNT(*) AS n, SUM(size_bytes) AS sz, '
                'SUM(content_available) AS c FROM files')
    r = cur.fetchone()
    rows = [{'index': db.db_path, 'files': r['n'], 'with_content': r['c'] or 0,
             'total_size': _human_bytes(r['sz'])}]
    cur.execute('SELECT full_path, size_bytes FROM files')
    roots: Dict[str, List[int]] = {}
    for fr in cur.fetchall():
        p = fr['full_path'] or ''
        parts = [x for x in p.split('/') if x]
        root = ('/' + '/'.join(parts[:2])) if p.startswith('/') else (parts[0] if parts else '(unknown)')
        agg = roots.setdefault(root, [0, 0])
        agg[0] += 1
        agg[1] += fr['size_bytes'] or 0
    for root, (n, sz) in sorted(roots.items(), key=lambda kv: -kv[1][0])[:20]:
        rows.append({'index': root, 'files': n, 'with_content': None,
                     'total_size': _human_bytes(sz)})
    return pd.DataFrame(rows)


def _run_config(db, cfg: list) -> VplResult:
    """Apply ingestion config commands, recompute derived columns, report."""
    from ingestion import IngestionError, IngestionStore
    store = IngestionStore(db)
    actions = []
    try:
        for s in cfg:
            if isinstance(s, IndexAddStage):
                store.add_index(s.name, s.path_prefix, s.exts)
                actions.append(f"index add {s.name} ({s.path_prefix})")
            elif isinstance(s, IndexRmStage):
                store.delete_index(_index_id(store, s.name))
                actions.append(f"index rm {s.name}")
            elif isinstance(s, SourcetypeAddStage):
                store.add_sourcetype(s.name, s.exts, s.path_contains, s.content_regex)
                actions.append(f"sourcetype add {s.name}")
            elif isinstance(s, ExtractAddStage):
                store.add_field(s.name, s.regex, s.sourcetype)
                actions.append(f"extract add {s.name}")
        stats = store.reindex_all()
    except IngestionError as e:
        raise VplError(str(e))
    df = pd.DataFrame([{'action': a} for a in actions])
    summary = (f"applied {len(actions)} change(s); recomputed "
               f"{stats['updated']} file(s) — {stats['indexes']} index(es), "
               f"{stats['sourcetypes']} sourcetype(s), {stats['fields']} field rule(s)")
    return VplResult(df, summary, len(df))


def _index_id(store, name: str) -> int:
    for i in store.list_indexes():
        if i['name'] == name:
            return i['id']
    raise VplError(f"index rm: no index named '{name}'")


def run_vpl(db, stages: list, default_limit: int = DEFAULT_LIMIT) -> VplResult:
    """Translate the parsed pipeline to SQL and run it against the index."""
    # Config commands (index add/rm, sourcetype add, extract add) mutate the
    # ingestion settings, then recompute derived columns and report.
    cfg = [s for s in stages if isinstance(s, _CONFIG_STAGES)]
    if cfg:
        return _run_config(db, cfg)

    ctx = _build_ctx(db, '')
    searches = [s.match for s in stages if isinstance(s, SearchStage)]
    sort = next((s for s in reversed(stages) if isinstance(s, SortStage)), None)
    head = min((s.n for s in stages if isinstance(s, HeadStage)), default=default_limit)
    fields = next((s for s in reversed(stages) if isinstance(s, FieldsStage)), None)
    stats = next((s for s in reversed(stages) if isinstance(s, StatsStage)), None)
    styp = next((s for s in reversed(stages) if isinstance(s, SourcetypesStage)), None)
    fsum = next((s for s in reversed(stages) if isinstance(s, FieldSummaryStage)), None)
    idx = next((s for s in stages if isinstance(s, IndexesStage)), None)

    conn = db._get_connection()
    cur = conn.cursor()

    if idx:
        df = _run_index_list(db)
        return VplResult(df, 'index list', len(df))

    base, params = _build_base(stages, ctx)

    if styp:
        cur.execute(f'SELECT extension, file_type, COUNT(*) AS count {base} '
                    f'GROUP BY extension, file_type ORDER BY count DESC LIMIT ?',
                    tuple(params) + (styp.limit,))
        df = pd.DataFrame([dict(r) for r in cur.fetchall()])
        return VplResult(df, f'sourcetypes top {styp.limit}', len(df))

    if fsum:
        cols = _SUMMARY_FIELDS[:fsum.limit]
        frags = ['COUNT(*) AS total']
        for i, c in enumerate(cols):
            frags.append(f'COUNT(DISTINCT {c}) AS d{i}')
            frags.append(f'COUNT({c}) AS c{i}')
        cur.execute(f'SELECT {", ".join(frags)} {base}', tuple(params))
        row = cur.fetchone()
        total = row['total'] or 0
        rows = [{'field': c, 'distinct': row[f'd{i}'],
                 'coverage': f"{round(row[f'c{i}'] * 100 / total)}%" if total else '0%'}
                for i, c in enumerate(cols)]
        df = pd.DataFrame(rows)
        return VplResult(df, 'fieldsummary', len(df))

    if stats:
        sql = f'SELECT {stats.select} {base}'
        if stats.group:
            sql += f' GROUP BY {stats.group} ORDER BY 2 DESC LIMIT 500'
        cur.execute(sql, tuple(params))
        df = pd.DataFrame([dict(r) for r in cur.fetchall()])
        return VplResult(df, f"stats: {stats.select}", len(df))

    order = sort.order if sort else ('bm25(content) ASC' if searches else 'modified_at_epoch DESC')
    sql = f'SELECT f.* {base} ORDER BY {order} LIMIT ?'
    cur.execute(sql, tuple(params) + (head,))
    df = pd.DataFrame([dict(r) for r in cur.fetchall()])

    # Surface extracted fields as real columns (json_extract of extracted_json).
    json_cols = [name for name in ctx.cols
                 if name not in COLS and name not in ('index', 'sourcetype')]
    if json_cols and not df.empty:
        ids = df['id'].tolist()
        marks = ', '.join('?' * len(ids))
        cur.execute(f'SELECT id, extracted_json FROM files WHERE id IN ({marks})', ids)
        by_id = {r['id']: (json.loads(r['extracted_json']) if r['extracted_json'] else {})
                 for r in cur.fetchall()}
        for name in json_cols:
            if name not in df.columns:
                df[name] = [by_id.get(i, {}).get(name) for i in ids]

    total = len(df)
    if len(df) >= head:  # cheap total count only when we hit the cap
        count_sql = f'SELECT COUNT(*) AS n {base}'
        cur.execute(count_sql, tuple(params))
        total = cur.fetchone()['n']

    if fields:
        missing = [c for c in fields.cols if c not in df.columns and c not in ctx.cols]
        if missing and not df.empty:
            raise VplError(f"fields: unknown column(s) {missing} "
                           f"(available: {', '.join(df.columns)})")
        df = df[[c for c in fields.cols if c in df.columns]]

    summary = ' | '.join(
        s.match if isinstance(s, SearchStage) else
        'where …' if isinstance(s, WhereStage) else
        s.order if isinstance(s, SortStage) else
        f'head {s.n}' if isinstance(s, HeadStage) else
        'fields ' + ', '.join(s.cols) if isinstance(s, FieldsStage) else ''
        for s in stages)
    return VplResult(df, summary, total)
