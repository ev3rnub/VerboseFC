"""Splunk-style Search tab: query bar + time picker, match bar, timeline,
field sidebar (Selected / Interesting fields), and Events / Statistics /
Visualization / Scheduled tabs.

The query bar accepts plain keywords or a full VPL pipeline; both run
through the VPL engine so results, drill-downs and the timeline share one
code path.
"""

import re
import time as _time
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from file_actions import open_path, reveal_path
from saved_searches import (TIME_RANGES, SavedSearchStore, SchedulerThread,
                            time_range_bounds)
from vpl_parser import (ExtractAddStage, FieldSummaryStage, IndexAddStage,
                        IndexesStage, IndexRmStage, SearchStage, SourcetypeAddStage,
                        SourcetypesStage, StatsStage, VplError, WhereStage,
                        add_time_range, parse_vpl, run_vpl, vpl_count,
                        vpl_field_counts, vpl_timeline)

VPL_STARTS = ('search ', 'where ', 'stats ', 'sort ', 'head ', 'fields ',
              'sourcetypes', 'sourcetype ', 'fieldsummary', 'index ', 'indexes',
              'extract ')

SELECTED_FIELDS = ['ext', 'category', 'type', 'index', 'sourcetype']  # sidebar, with value counts
INTERESTING_FIELDS = ['name', 'path', 'size', 'modified', 'created', 'md5', 'sha256',
                      'index', 'sourcetype']

SCHEDULE_CHOICES = {"Off": None, "Every 5 min": 5, "Every 15 min": 15,
                    "Every 30 min": 30, "Hourly": 60, "Every 6 hours": 360,
                    "Daily": 1440}

_HOVER = dict(bgcolor='rgba(30, 33, 48, 0.95)', bordercolor='#6c7a9c',
              font=dict(size=18, color='white'))


def _to_pipeline(q: str) -> str:
    """Keywords or VPL → a valid pipeline: prefix the first segment with
    'search' unless it already starts with a command (drill-downs append
    '| where …' to plain keyword queries)."""
    first = q.strip().split('|', 1)[0].strip().lower()
    if first.startswith(VPL_STARTS):
        return q.strip()
    return f'search {q.strip()}'


def _highlight_terms(q: str):
    """Words worth bolding in previews (skip commands and stopwords)."""
    skip = set(VPL_STARTS) | {'and', 'or', 'not', 'by', 'as', 'count', 'sum',
                              'avg', 'min', 'max', 'contains', 'head', 'fields',
                              'stats', 'sort', 'where', 'search', 'list',
                              'sourcetypes', 'sourcetype', 'fieldsummary', 'index',
                              'indexes', 'add', 'rm', 'remove', 'delete', 'extract'}
    return [w for w in re.findall(r"[A-Za-z0-9']{3,}", q) if w.lower() not in skip]


def _fmt_epoch(e):
    try:
        return datetime.fromtimestamp(float(e)).strftime('%m/%d/%y %H:%M')
    except (TypeError, ValueError):
        return '—'


def _ensure_scheduler(store: SavedSearchStore):
    th = st.session_state.get('sched_thread')
    if th is None or not th.is_alive():
        th = SchedulerThread(store)
        th.start()
        st.session_state['sched_thread'] = th
    if getattr(th, 'last_error', None):
        st.warning("The background scheduler hit an error (it retries on the next tick): "
                   f"{th.last_error}")


def _time_picker():
    """Time Range Picker → (label, min_epoch, max_epoch)."""
    choice = st.selectbox("Time range", TIME_RANGES + ["Custom range"],
                          index=0, key="time_range_pick",
                          label_visibility="collapsed")
    if choice == "Custom range":
        c1, c2 = st.columns(2)
        with c1:
            d_from = st.date_input("From", value=None, key="tr_from",
                                   label_visibility="collapsed")
        with c2:
            d_to = st.date_input("To", value=None, key="tr_to",
                                 label_visibility="collapsed")
        if d_from and d_to:
            label = f"{d_from:%Y-%m-%d}..{d_to:%Y-%m-%d}"
            return label, *time_range_bounds(label)
        return "Custom range", None, None
    return choice, *time_range_bounds(choice)


_QUERY_CSS = r"""
<style>
/* Splunk-style query bar: one row — time picker | query | Search button */
div[data-testid="stHorizontalBlock"]:has(> div:has(> div[data-testid="stSelectbox"]
     > div > label[pw-label="Time range"])) {
  align-items: flex-end;
  row-gap: 0;
}
/* Collapse the empty spacer columns so the row stays tight */
div[data-testid="stHorizontalBlock"]:has(> div:has(> div[data-testid="stSelectbox"]
     > div > label[pw-label="Time range"])) div[data-testid="stColumn"]:empty {
  min-height: 0;
}
div[data-testid="stSelectbox"] > div > [data-baseweb="select"] > div {
  font-size: 0.85rem;
}
/* Query bar: monospace, resizable vertically via the bottom-right drag
   handle (Splunk-style multiline expansion) */
textarea[data-testid="stTextAreaInput"] {
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  font-size: 0.9rem;
  line-height: 1.5;
  padding: 6px 8px;
  resize: vertical;
  min-height: 38px;
}
/* ---- line-number gutter ----
   Streamlit strips <script> from markdown, so the numbers are rendered
   server-side: when the query bar is expanded, a markdown element holds
   "1<br>2<br>…" and CSS floats it over the textarea's left edge. The
   numbers are a pure indicator — never part of the query text. */
div[data-testid="stColumn"]:has(.vfc-gutter) {
  position: relative;
}
div[data-testid="stElementContainer"]:has(.vfc-gutter) {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  z-index: 5;
  pointer-events: none;
}
div[data-testid="stMarkdownContainer"]:has(.vfc-gutter) {
  height: 100%;
}
.vfc-gutter {
  width: 36px;
  height: 100%;
  box-sizing: border-box;
  padding: 7px 8px 0 0;   /* 1px border + 6px textarea padding */
  text-align: right;
  font-family: "SFMono-Regular", Menlo, Consolas, monospace;
  font-size: 0.9rem;
  line-height: 1.5;       /* must match the textarea's line-height */
  color: #9da7b1;
  background: rgba(128, 128, 128, .10);
  border-right: 1px solid rgba(128, 128, 128, .25);
  overflow: hidden;
}
/* indent the query text so it starts right of the gutter */
div[data-testid="stColumn"]:has(.vfc-gutter) textarea[data-testid="stTextAreaInput"] {
  padding-left: 44px;
}
</style>
"""


def _execute(db, query: str, time_label: str, lo, hi) -> dict:
    stages = parse_vpl(_to_pipeline(query), db)
    stages = add_time_range(stages, lo, hi)
    t0 = _time.time()
    res = run_vpl(db, stages)
    return {'df': res.df, 'total': res.total, 'exec': _time.time() - t0,
            'query': query, 'time_label': time_label, 'stages': stages,
            'terms': _highlight_terms(query)}


def _base_stages(stages):
    """Search/where stages only — what counts, timeline and stats group by."""
    return [s for s in stages if isinstance(s, (SearchStage, WhereStage))]


def _append_to_query(field: str, value: str):
    """Callback: append a drill-down filter and re-run the search."""
    q = st.session_state.get('vpl_query', '')
    v_sql = str(value).replace('"', '')
    st.session_state['vpl_query'] = f'{q} | where {field}=="{v_sql}"'
    st.session_state['_q_hit'] = True


def _load_saved(query: str, time_range: str):
    """Callback: load a saved/history query into the search bar and run it."""
    st.session_state['vpl_query'] = query
    st.session_state['time_range_pick'] = time_range
    st.session_state['_q_hit'] = True


# --- field sidebar -----------------------------------------------------------

def _field_sidebar(db, out: dict):
    stages = _base_stages(out['stages'])
    st.markdown("**SELECTED FIELDS**")
    for field in SELECTED_FIELDS:
        try:
            counts = vpl_field_counts(db, stages, field, limit=10)
        except VplError:
            continue
        if not counts:
            continue
        st.caption(field)
        for v, n in counts:
            label = f"{v or '(none)'}  {n}"
            st.button(label, key=f"drill_{field}_{v}", width='stretch',
                      on_click=_append_to_query, args=(field, v))
    st.divider()
    st.markdown("**INTERESTING FIELDS**")
    for f in INTERESTING_FIELDS:
        st.caption(f"# {f}")


# --- events ------------------------------------------------------------------

def _is_report(out: dict) -> bool:
    """True when the pipeline ends in a report/config command (stats,
    sourcetypes, fieldsummary, index list, index add/rm, sourcetype add,
    extract add) — those return summary rows, not files."""
    return any(isinstance(s, (StatsStage, SourcetypesStage, FieldSummaryStage,
                              IndexesStage, IndexAddStage, IndexRmStage,
                              SourcetypeAddStage, ExtractAddStage))
               for s in out['stages'])


def _events_tab(db, out: dict):
    df = out['df']
    total = out['total']
    if df.empty:
        st.write("No matches.")
        return

    if _is_report(out):
        st.dataframe(df, width='stretch', hide_index=True)
        st.download_button("⬇️ CSV", df.to_csv(index=False).encode('utf-8'),
                           "vpl_report.csv", "text/csv", key="ev_csv",
                           width='stretch')
        return

    c1, c2, c3 = st.columns([1, 1, 3])
    with c1:
        per_page = st.selectbox("Per page", [20, 50, 100], key="ev_per_page")
    with c2:
        pages = max(1, -(-total // per_page))
        page = st.number_input("Page", 1, pages, 1, key="ev_page")
    with c3:
        st.write("")
        st.write("")
        st.download_button("⬇️ CSV", df.to_csv(index=False).encode('utf-8'),
                           "search_results.csv", "text/csv", key="ev_csv",
                           width='stretch')

    page_df = df.iloc[(page - 1) * per_page: page * per_page]
    st.caption(f"{(page - 1) * per_page + 1}-{(page - 1) * per_page + len(page_df)} "
               f"of {total}")

    for i, (_, r) in enumerate(page_df.iterrows()):
        # Position-based key: names can repeat (duplicate files) and `id`
        # disappears when the pipeline ends with `| fields …`.
        rid = f"{page}_{i}"
        path = r.get('full_path') or ''
        with st.container():
            c1, c2 = st.columns([1.1, 4])
            with c1:
                st.caption(_fmt_epoch(r.get('modified_at_epoch')))
            with c2:
                st.markdown(f"**{r['name']}** &nbsp;·&nbsp; {r.get('size_human', '')} "
                            f"&nbsp;·&nbsp; `{r.get('extension', '')}` "
                            f"&nbsp;·&nbsp; {r.get('file_category', '')}")
                st.caption(path or r['name'])
            with st.expander("▸"):
                fields = {k: r.get(k) for k in
                          ('name', 'full_path', 'size_bytes', 'size_human', 'extension',
                           'file_type', 'file_category', 'created_at', 'modified_at',
                           'content_available', 'md5', 'sha256')
                          if k in df.columns}
                st.json(fields)
                b1, b2, b3 = st.columns(3)
                with b1:
                    if st.button("📂 Open", key=f"ev_open_{rid}", disabled=not path):
                        try:
                            open_path(path)
                        except Exception as e:
                            st.error(f"Could not open: {e}")
                with b2:
                    if st.button("📍 Reveal", key=f"ev_reveal_{rid}", disabled=not path):
                        try:
                            reveal_path(path)
                        except Exception as e:
                            st.error(f"Could not reveal: {e}")
                with b3:
                    if st.button("🗑 Remove from results", key=f"ev_rm_{rid}",
                                 disabled=not path):
                        st.session_state['search_out']['df'] = \
                            out['df'][out['df']['full_path'] != path]
                        st.rerun()
                prev = r.get('content_preview')
                if prev:
                    text = str(prev)[:600]
                    for term in out['terms']:
                        text = re.sub(f"({re.escape(term)})", r"**\1**", text, flags=re.I)
                    st.caption(text)


# --- statistics & visualization ----------------------------------------------

def _stats_tab(db, out: dict):
    stages = _base_stages(out['stages'])
    total = vpl_count(db, stages)
    m1, m2, m3 = st.columns(3)
    m1.metric("Files matched", f"{total:,}")
    if 'size_bytes' in out['df'].columns and not out['df'].empty:
        shown = out['df']
        m2.metric("Total size (shown)", f"{shown['size_bytes'].sum() / (1024 ** 2):,.1f} MB")
        m3.metric("Avg size (shown)", f"{shown['size_bytes'].mean() / 1024:,.1f} KB")

    for field in SELECTED_FIELDS:
        counts = vpl_field_counts(db, stages, field, limit=20)
        if not counts:
            continue
        st.write(f"**count by {field}**")
        cdf = pd.DataFrame(counts, columns=[field, 'count'])
        cdf = cdf.sort_values(field)
        st.bar_chart(cdf.set_index(field))


def _viz_tab(db, out: dict):
    df = out['df']
    if df.empty:
        st.write("No matches to visualize.")
        return
    stages = _base_stages(out['stages'])

    ext_counts = vpl_field_counts(db, stages, 'ext', limit=15)
    if ext_counts:
        edf = pd.DataFrame(ext_counts, columns=['ext', 'count']).sort_values('ext')
        st.plotly_chart(
            px.bar(edf, x='ext', y='count').update_layout(
                height=320, margin=dict(t=30, b=10)),
            width='stretch')

    cat_counts = vpl_field_counts(db, stages, 'category', limit=10)
    if cat_counts:
        cdf = pd.DataFrame(cat_counts, columns=['category', 'count'])
        st.plotly_chart(
            px.pie(cdf, names='category', values='count').update_layout(
                height=380, margin=dict(t=30, b=10)),
            width='stretch')

    if {'modified_at_epoch', 'size_bytes'} <= set(df.columns):
        sdf = df.dropna(subset=['modified_at_epoch', 'size_bytes']).copy()
        if not sdf.empty:
            sdf['modified'] = pd.to_datetime(sdf['modified_at_epoch'], unit='s')
            st.plotly_chart(
                px.scatter(sdf, x='modified', y='size_bytes', color='file_category',
                           log_y=True, hover_data=['name'],
                           labels={'size_bytes': 'size (bytes)'}).update_layout(
                    height=380, margin=dict(t=30, b=10)),
                width='stretch')


# --- scheduled searches --------------------------------------------------------

def _scheduled_tab(db, store: SavedSearchStore, out: dict):
    cur_q = st.session_state.get('vpl_query', '')
    cur_tr = st.session_state.get('search_out', {}).get('time_label', 'Any time')

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        name = st.text_input("Name this search", key="save_name",
                             placeholder="e.g. big pdfs")
    with c2:
        st.text_input("Query (edit before saving)", value=cur_q, key="save_query")
    with c3:
        st.write("")
        st.write("")
        if st.button("💾 Save", width='stretch', disabled=not (name and cur_q)):
            try:
                sid = store.save(name, _to_pipeline(st.session_state['save_query']), cur_tr)
                st.success(f"Saved #{sid}.")
            except VplError as e:
                st.error(f"Save failed: {e}")

    saved = store.list()
    if not saved:
        st.info("No saved searches yet. Run a query, then save it above.")
    for s in saved:
        with st.expander(f"🔖 {s['name']} — `{s['query'][:70]}`"):
            st.caption(f"time range: {s['time_range']}")
            c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
            with c1:
                sched_label = next((k for k, v in SCHEDULE_CHOICES.items()
                                    if v == s['schedule_minutes']), "Off")
                pick = st.selectbox("Schedule", list(SCHEDULE_CHOICES),
                                    index=list(SCHEDULE_CHOICES).index(sched_label),
                                    key=f"sched_{s['id']}")
                if pick != sched_label:
                    store.set_schedule(s['id'], SCHEDULE_CHOICES[pick])
                    st.rerun()
            with c2:
                st.write("")
                st.write("")
                st.button("▶ Run now", key=f"run_{s['id']}",
                          on_click=_load_saved, args=(s['query'], s['time_range']))
            with c3:
                st.write("")
                st.write("")
                if st.button("🗑 Delete", key=f"del_{s['id']}"):
                    store.delete(s['id'])
                    st.rerun()
            with c4:
                last = _fmt_epoch(s['last_run_at']) if s['last_run_at'] else 'never'
                st.caption(f"last scheduled run: {last}")

    st.divider()
    st.markdown("**SCHEDULED RUNS**")
    seen = st.session_state.get('sched_seen_id', 0)
    runs = store.recent_runs(25)
    if not runs:
        st.write("No scheduled runs yet. Schedules fire while the app is open "
                 "(or via `scheduled_runner.py` from cron).")
        return
    unread = sum(1 for r in runs if r['id'] > seen)
    r1, r2 = st.columns([3, 1])
    with r1:
        st.caption(f"{len(runs)} most recent · {unread} new since you last looked")
    with r2:
        if unread and st.button("✔ Mark as read"):
            st.session_state['sched_seen_id'] = max(r['id'] for r in runs)
            st.rerun()
    rdf = pd.DataFrame(runs)[['run_at', 'name', 'result_count', 'error']]
    rdf['run_at'] = rdf['run_at'].map(_fmt_epoch)
    st.dataframe(rdf, width='stretch', hide_index=True)
    for r in runs[:5]:
        if r['top_rows']:
            import json
            with st.expander(f"Top matches — {r['name']} ({r['result_count']})"):
                st.dataframe(pd.DataFrame(json.loads(r['top_rows'])),
                             width='stretch', hide_index=True)


# --- main entry -----------------------------------------------------------------

def render_search_tab(db, search_engine, store: SavedSearchStore):
    _ensure_scheduler(store)

    def _submitted():
        st.session_state['_q_hit'] = True

    def _grow():
        st.session_state['q_area_h'] = min(
            st.session_state.get('q_area_h', 44) + 60, 320)

    def _shrink():
        st.session_state['q_area_h'] = max(
            st.session_state.get('q_area_h', 44) - 60, 44)

    st.markdown(_QUERY_CSS, unsafe_allow_html=True)
    if 'q_area_h' not in st.session_state:
        st.session_state['q_area_h'] = 44
    # One row: time picker | query bar | expand handle | Search button —
    # all bottom-aligned, like Splunk's search bar.
    t0, t1, t2, t3, t4 = st.columns([1.15, 4.6, 0.34, 0.34, 0.95])
    with t0:
        time_label, lo, hi = _time_picker()
    with t1:
        # Line-number gutter: when the bar is expanded, render the line
        # numbers server-side (no JS is possible in Streamlit markdown) and
        # let the CSS above float them over the textarea's left edge.
        # Indicator only — the numbers never enter the query.
        if st.session_state['q_area_h'] > 44:
            cur_q = st.session_state.get('vpl_query', '')
            nums = '<br>'.join(str(i) for i in range(1, cur_q.count('\n') + 2))
            st.markdown(f'<div class="vfc-gutter">{nums}</div>',
                        unsafe_allow_html=True)
        q = st.text_area(
            "Search", key="vpl_query",
            height=st.session_state['q_area_h'],
            placeholder='report | where ext==pdf and size>1mb | sort -size | head 20   '
                        '— or just type keywords',
            label_visibility="collapsed")
    with t2:
        grow = st.button("▾", key="q_grow", help="Expand the query bar (multiline)",
                         disabled=st.session_state['q_area_h'] >= 320)
    with t3:
        shrink = st.button("▴", key="q_shrink", help="Collapse the query bar",
                           disabled=st.session_state['q_area_h'] <= 44)
    with t4:
        run_btn = st.button("🔍 Search", type="primary", width='stretch')
    if grow or shrink:
        # Change the height on the next rerun; keep the typed query.
        _grow() if grow else _shrink()

    if st.session_state.pop('_q_hit', False) or run_btn:
        if q.strip():
            try:
                st.session_state['search_out'] = _execute(db, q, time_label, lo, hi)
                if search_engine is not None:
                    try:
                        search_engine.get_search_history().add(q, st.session_state['search_out']['total'])
                    except Exception:
                        pass
            except VplError as e:
                st.session_state.pop('search_out', None)
                st.error(f"VPL: {e}")
        else:
            st.session_state.pop('search_out', None)

    out = st.session_state.get('search_out')
    if out is None:
        st.info("Type a query above — plain keywords or a VPL pipeline. "
                "Example: `error | where ext==log and size>1mb | sort -size`. "
                "Index reports: `index list`, `sourcetypes`, `fieldsummary`.")
        st.caption(f"Indexed files: {db.get_file_count():,} · with searchable "
                   f"content: {db.get_content_count():,}")
        return

    # Match bar
    if _is_report(out):
        st.success(f"**{out['total']}** report rows in {out['exec']:.3f}s — "
                   f"time range: **{out['time_label']}**")
    else:
        st.success(f"**{len(out['df'])}** of **{out['total']}** files matched in "
                   f"{out['exec']:.3f}s — time range: **{out['time_label']}**")

    # Timeline
    buckets = vpl_timeline(db, _base_stages(out['stages']))
    if buckets:
        tdf = pd.DataFrame(buckets, columns=['t', 'n'])
        tdf['t'] = pd.to_datetime(tdf['t'], unit='s')
        fig = go.Figure(go.Bar(x=tdf['t'], y=tdf['n'], marker_color='#3d9970',
                               hovertemplate='<b>%{x|%m/%d/%y %H:%M}</b>'
                                             '<br>%{y} matches<extra></extra>',
                               hoverlabel=_HOVER))
        fig.update_layout(height=140, margin=dict(t=10, b=10, l=40, r=10),
                          hoverlabel=_HOVER)
        st.plotly_chart(fig, width='stretch', config={'displayModeBar': False})

    # Sidebar + results
    left, right = st.columns([1, 3.4])
    with left:
        _field_sidebar(db, out)
    with right:
        unread = store.unseen_runs(st.session_state.get('sched_seen_id', 0))
        sched_label = f"⏰ Scheduled ({unread})" if unread else "⏰ Scheduled"
        tab_ev, tab_st, tab_viz, tab_sched = st.tabs(
            [f"Events ({out['total']})", "Statistics", "Visualization", sched_label])
        with tab_ev:
            _events_tab(db, out)
        with tab_st:
            _stats_tab(db, out)
        with tab_viz:
            _viz_tab(db, out)
        with tab_sched:
            _scheduled_tab(db, store, out)

    # Search history
    if search_engine is not None:
        with st.expander("📜 Recent searches", expanded=False):
            try:
                recent = search_engine.get_search_history().get_recent(10)
                for item in recent:
                    st.button(f"{item.get('query', '')} "
                              f"({item.get('results_count', '?')})",
                              key=f"hist_{item.get('id', item.get('query'))}",
                              on_click=_load_saved, args=(item['query'], 'Any time'))
            except Exception as e:
                st.error(f"History unavailable: {e}")
