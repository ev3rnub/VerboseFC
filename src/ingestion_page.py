"""Ingestion tab — Splunk-style settings for the index.

Three sub-pages, mirroring Splunk's Settings → Data:

* **Data Inputs** — named indexes with a path prefix (+ optional extension
  whitelist). Files are assigned to the first input whose prefix matches.
* **Sourcetypes** — rules that classify files (extension / path / content
  regex, priority order). Unmatched files keep their built-in category.
* **Field Extractions** — regexes with one capture group that pull fields
  out of indexed content; the fields become queryable in VPL.

Every change is declarative: **Apply & reindex** recomputes the derived
columns (`index`, `sourcetype`, extracted fields) for all indexed files
without touching the filesystem. The same operations are available as VPL
commands in the Search tab (`index add …`, `sourcetype add …`,
`extract add …`).
"""

import pandas as pd
import streamlit as st

from ingestion import DEFAULT_INDEX, IngestionError, IngestionStore


def _apply(store: IngestionStore):
    stats = store.reindex_all()
    st.session_state['ingest_msg'] = (
        f"✅ Reindexed {stats['updated']} file(s) — {stats['indexes']} index rule(s), "
        f"{stats['sourcetypes']} sourcetype rule(s), {stats['fields']} field rule(s).")
    st.rerun()


def _msg():
    m = st.session_state.pop('ingest_msg', None)
    if m:
        (st.success if m.startswith('✅') else st.error)(m)


def _err(e):
    st.session_state['ingest_msg'] = f"❌ {e}"
    st.rerun()


# --- Data Inputs ---------------------------------------------------------------

def _inputs_page(db, store: IngestionStore):
    st.markdown("**Data Inputs** — point VerboseFC at folders; files found under a "
                "path prefix land in that **index**. Files matching no input stay in "
                f"`{DEFAULT_INDEX}`. (Scanning a folder still happens on the Scan tab — "
                "inputs group what's already indexed.)")

    rules = store.list_indexes()
    stats = {s['name']: s for s in store.index_stats()}

    if rules:
        rows = []
        for r in rules:
            s = stats.get(r['name'], {})
            rows.append({'name': r['name'], 'path prefix': r['path_prefix'] or '(any path)',
                         'extensions': r['exts'].replace('|', ', ') or '(any)',
                         'files': s.get('files', 0), 'description': r['description']})
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.info(f"No inputs yet — every file is in `{DEFAULT_INDEX}`.")

    st.divider()
    st.markdown("#### ➕ Add a data input")
    with st.form('ing_add_index', clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Index name", placeholder="projects")
        prefix = c2.text_input("Path prefix", placeholder="/Users/you/projects")
        c3, c4 = st.columns(2)
        exts = c3.text_input("Extensions (optional)", placeholder="pdf, md, txt")
        desc = c4.text_input("Description (optional)")
        if st.form_submit_button("Add input", width='stretch'):
            try:
                store.add_index(name, prefix, exts, desc)
                _apply(store)
            except IngestionError as e:
                _err(e)

    if rules:
        st.markdown("#### 🗑 Remove an input")
        opts = {f"{r['name']}  ({r['path_prefix'] or 'any path'})": r['id'] for r in rules}
        c1, c2 = st.columns([3, 1])
        pick = c1.selectbox("Input", list(opts), key="ing_rm_idx")
        if c2.button("Remove", key="ing_rm_idx_btn", width='stretch'):
            store.delete_index(opts[pick])
            _apply(store)

    st.divider()
    st.markdown("#### Current index assignment")
    df = pd.DataFrame([{'index': s['name'], 'files': s['files'],
                        'size': _human(s['sz'] or 0)} for s in store.index_stats()])
    st.dataframe(df, width='stretch', hide_index=True)
    st.caption("Query it any time: `index list` · `stats count by index` · "
               "`where index==projects`")


# --- Sourcetypes ---------------------------------------------------------------

def _sourcetypes_page(db, store: IngestionStore):
    st.markdown("**Sourcetypes** — classify files by extension, path fragment, or a "
                "content regex (checked against the MIME type). First match wins in "
                "priority order; unmatched files keep their built-in category "
                "(document / image / code …).")

    rules = store.list_sourcetypes()
    stats = {s['name']: s['files'] for s in store.sourcetype_stats()}

    if rules:
        rows = [{'name': r['name'], 'exts': r['exts'].replace('|', ', ') or '(any)',
                 'path contains': r['path_contains'] or '—',
                 'content regex': r['content_regex'] or '—',
                 'priority': r['priority'], 'files': stats.get(r['name'], 0)}
                for r in rules]
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.info("No custom sourcetypes — files use their built-in category.")

    st.divider()
    st.markdown("#### ➕ Add a sourcetype")
    with st.form('ing_add_styp', clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Sourcetype name", placeholder="syslog")
        prio = c2.number_input("Priority (lower = checked first)", 1, 999, 100)
        c3, c4 = st.columns(2)
        exts = c3.text_input("Extensions", placeholder="log, txt")
        path = c4.text_input("Path contains", placeholder="/logs/")
        regex = st.text_input("Content regex (matched against MIME type)",
                              placeholder=r"application/json")
        if st.form_submit_button("Add sourcetype", width='stretch'):
            try:
                store.add_sourcetype(name, exts, path, regex, int(prio))
                _apply(store)
            except IngestionError as e:
                _err(e)

    if rules:
        st.markdown("#### 🗑 Remove a sourcetype")
        opts = {f"{r['name']}  (prio {r['priority']})": r['id'] for r in rules}
        c1, c2 = st.columns([3, 1])
        pick = c1.selectbox("Sourcetype", list(opts), key="ing_rm_styp")
        if c2.button("Remove", key="ing_rm_styp_btn", width='stretch'):
            store.delete_sourcetype(opts[pick])
            _apply(store)

    st.divider()
    st.markdown("#### Current sourcetype distribution")
    df = pd.DataFrame([{'sourcetype': s['name'], 'files': s['files']}
                       for s in store.sourcetype_stats()])
    st.dataframe(df, width='stretch', hide_index=True)
    st.caption("Query it any time: `sourcetypes` · `stats count by sourcetype` · "
               "`where sourcetype==syslog`")


# --- Field Extractions ---------------------------------------------------------

def _fields_page(db, store: IngestionStore):
    st.markdown("**Field Extractions** — a regex with one capture group pulls a field "
                "out of each file's indexed content (first match). The field becomes "
                "queryable in VPL: `where user==alice`, `stats count by user`.")

    rules = store.list_fields()
    fstats = {f['field']: f for f in store.extracted_field_stats()}

    if rules:
        rows = []
        for r in rules:
            key = r['name'] if not r['name'].startswith('kv:') else r['name']
            fs = fstats.get(r['name'], {})
            rows.append({'field': r['name'], 'regex': r['regex'],
                         'sourcetype filter': r['sourcetype'] or '(any)',
                         'files with value': fs.get('files', 0),
                         'sample': fs.get('sample', '—')})
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.info("No extractions yet. Add one below — e.g. name `user`, regex "
                "`user=([a-z0-9_]+)` pulls the value after `user=` from file contents.")

    st.divider()
    st.markdown("#### ➕ Add a field extraction")
    with st.form('ing_add_field', clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Field name", placeholder="user")
        styp = c2.text_input("Limit to sourcetype (optional)", placeholder="text")
        regex = st.text_input("Regex (needs one capture group)",
                              placeholder=r"user=([a-z0-9_]+)")
        if st.form_submit_button("Add extraction", width='stretch'):
            try:
                store.add_field(name, regex, styp)
                _apply(store)
            except IngestionError as e:
                _err(e)

    if rules:
        st.markdown("#### 🗑 Remove an extraction")
        opts = {f"{r['name']}  ({r['regex'][:40]})": r['id'] for r in rules}
        c1, c2 = st.columns([3, 1])
        pick = c1.selectbox("Extraction", list(opts), key="ing_rm_field")
        if c2.button("Remove", key="ing_rm_field_btn", width='stretch'):
            store.delete_field(opts[pick])
            _apply(store)

    st.divider()
    st.markdown("#### Try it on the current index")
    c1, c2 = st.columns([3, 1])
    test_re = c1.text_input("Test regex against indexed content", key="ing_test_re",
                            placeholder=r"error ([a-z]+)")
    if c2.button("Test", key="ing_test_btn", width='stretch'):
        _test_regex(db, test_re)


def _test_regex(db, pattern: str):
    import re as _re
    if not pattern.strip():
        st.session_state['ingest_msg'] = "❌ test: enter a regex first"
        st.rerun()
    try:
        rx = _re.compile(pattern)
    except _re.error as e:
        st.session_state['ingest_msg'] = f"❌ test: invalid regex — {e}"
        st.rerun()
    if rx.groups < 1:
        st.session_state['ingest_msg'] = "❌ test: regex needs one capture group, e.g. (word)"
        st.rerun()
    cur = db._get_connection().cursor()
    cur.execute('SELECT f.name, c.full_content FROM files f '
                'JOIN content c ON c.file_id = f.id LIMIT 2000')
    hits = []
    for r in cur.fetchall():
        m = rx.search(r['full_content'] or '')
        if m:
            hits.append({'file': r['name'], 'value': m.group(1),
                         'context': (r['full_content'] or '')[max(0, m.start() - 30):m.end() + 30]})
        if len(hits) >= 15:
            break
    if hits:
        st.session_state['ingest_msg'] = f"✅ test: {len(hits)} match(es) shown"
        st.session_state['ingest_test_hits'] = hits
    else:
        st.session_state['ingest_msg'] = "❌ test: no matches in indexed content"
    st.rerun()


def _human(n: int) -> str:
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


# --- entry point ---------------------------------------------------------------

def render_ingestion_tab(db, store: IngestionStore):
    _msg()
    hits = st.session_state.pop('ingest_test_hits', None)
    if hits:
        with st.expander("🧪 Regex test matches", expanded=True):
            st.dataframe(pd.DataFrame(hits), width='stretch', hide_index=True)

    st.button("♻️ Apply & reindex all files", key="ing_apply",
              on_click=_apply, args=(store,), width='stretch')
    st.caption("Recomputes index assignment, sourcetypes and extracted fields for "
               "every indexed file — no re-scan, no content re-extraction.")

    t_in, t_st, t_f = st.tabs(["📥 Data Inputs", "🏷 Sourcetypes", "🧲 Field Extractions"])
    with t_in:
        _inputs_page(db, store)
    with t_st:
        _sourcetypes_page(db, store)
    with t_f:
        _fields_page(db, store)
