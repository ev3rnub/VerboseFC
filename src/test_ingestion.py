"""Tests for the ingestion layer (indexes, sourcetypes, field extractions)
and its VPL integration."""
import os
import tempfile
import time

from db import SearchDatabase
from ingestion import DEFAULT_INDEX, IngestionError, IngestionStore
from vpl_parser import VplError, parse_vpl, run_vpl
from test_vpl import make_db

NOW = time.time()


def _names(res):
    return sorted(res.df['name'].tolist()) if not res.df.empty else []


def main():
    db = make_db()
    store = IngestionStore(db)

    # --- derived columns exist and default cleanly ---------------------------
    r = run_vpl(db, parse_vpl('where index==default', db))
    assert _names(r) == ['app.log', 'photo.png', 'q1.pdf', 'q2.pdf', 'todo.txt'], _names(r)
    r = run_vpl(db, parse_vpl('where sourcetype==document', db))
    assert _names(r) == ['q1.pdf', 'q2.pdf'], _names(r)
    print("1. default index + built-in sourcetype ok")

    # --- index rules ----------------------------------------------------------
    store.add_index('reports', '/r/reports')
    store.add_index('logs', '/r/data', 'log')
    assert store.index_names() == ['logs', 'reports']
    r = run_vpl(db, parse_vpl('where index==reports', db))
    assert _names(r) == ['q1.pdf', 'q2.pdf'], _names(r)
    r = run_vpl(db, parse_vpl('where index==logs', db))
    assert _names(r) == ['app.log'], _names(r)
    try:
        store.add_index('reports', '/x')
        raise AssertionError("duplicate index name must be rejected")
    except IngestionError:
        pass
    try:
        store.add_index('default', '/x')
        raise AssertionError("'default' is reserved")
    except IngestionError:
        pass
    print("2. index add + where index== ok")

    # --- sourcetype rules -----------------------------------------------------
    store.add_sourcetype('syslog', exts='log', path_contains='/logs/')
    r = run_vpl(db, parse_vpl('where sourcetype==syslog', db))
    assert _names(r) == ['app.log'], _names(r)
    try:
        store.add_sourcetype('empty')
        raise AssertionError("sourcetype with no matcher must be rejected")
    except IngestionError:
        pass
    try:
        store.add_sourcetype('badre', content_regex='([unclosed')
        raise AssertionError("invalid regex must be rejected")
    except IngestionError:
        pass
    print("3. sourcetype add + where sourcetype== ok")

    # --- field extractions ----------------------------------------------------
    store.add_field('errkind', r'error ([a-z]+)', 'text')
    r = run_vpl(db, parse_vpl('where errkind==connection', db))
    assert _names(r) == ['app.log'], _names(r)
    r = run_vpl(db, parse_vpl('where errkind==timeout', db))
    assert _names(r) == [], _names(r)  # first match wins: 'connection'
    r = run_vpl(db, parse_vpl('search error | fields name, errkind', db))
    assert list(r.df['errkind']) == ['connection'], r.df
    try:
        store.add_field('errkind2', 'no capture group here')
        raise AssertionError("regex without a capture group must be rejected")
    except IngestionError:
        pass
    try:
        store.add_field('bad name!', r'(x)')
        raise AssertionError("invalid field name must be rejected")
    except IngestionError:
        pass
    print("4. extract add + where <field>== ok")

    # reserved-name extraction is exposed as kv:<name>
    store.add_field('size', r'error ([a-z]+)')
    assert 'kv:size' in store.extracted_field_names()
    r = run_vpl(db, parse_vpl('where kv:size==connection', db))
    assert _names(r) == ['app.log'], _names(r)
    store.delete_field(store.list_fields()[-1]['id'])
    print("5. reserved-name extraction exposed as kv:<name> ok")

    # --- stats / grouping by ingestion fields ---------------------------------
    r = run_vpl(db, parse_vpl('stats count by index', db))
    got = dict(zip(r.df['index'], r.df['count']))
    assert got == {'reports': 2, 'logs': 1, DEFAULT_INDEX: 2}, got
    r = run_vpl(db, parse_vpl('stats count by sourcetype', db))
    got = dict(zip(r.df['sourcetype'], r.df['count']))
    assert got.get('syslog') == 1 and got.get('document') == 2, got
    print("6. stats count by index/sourcetype ok")

    # --- sort by ingestion field ----------------------------------------------
    r = run_vpl(db, parse_vpl('search report | sort index | fields name, index_name', db))
    assert list(r.df['index_name']) == sorted(r.df['index_name']), r.df
    print("7. sort by index ok")

    # --- fieldsummary shows derived columns ------------------------------------
    r = run_vpl(db, parse_vpl('fieldsummary', db))
    assert 'index_name' in list(r.df['field']) and 'sourcetype' in list(r.df['field']), r.df
    print("8. fieldsummary covers index_name/sourcetype ok")

    # --- VPL config commands (index add / rm / sourcetype add / extract add) --
    r = run_vpl(db, parse_vpl('index add photos /r exts=png', db))
    assert 'applied 1' in r.summary, r.summary
    r = run_vpl(db, parse_vpl('where index==photos', db))
    assert _names(r) == ['photo.png'], _names(r)
    r = run_vpl(db, parse_vpl('index rm photos', db))
    r = run_vpl(db, parse_vpl('where index==photos', db))
    assert _names(r) == [], _names(r)  # back to default
    r = run_vpl(db, parse_vpl('sourcetype add jsonlog exts=log content="text/plain"', db))
    r = run_vpl(db, parse_vpl('where sourcetype==jsonlog', db))
    assert _names(r) == [], _names(r)  # syslog (added first, same priority) wins for app.log
    r = run_vpl(db, parse_vpl('sourcetype add pngtype exts=png', db))
    r = run_vpl(db, parse_vpl('where sourcetype==pngtype', db))
    assert _names(r) == ['photo.png'], _names(r)
    r = run_vpl(db, parse_vpl('extract add errword "timeout ([a-z]+)"', db))
    assert 'applied 1' in r.summary and 'field rule' in r.summary, r.summary
    assert list(r.df['action']) == ['extract add errword'], r.df
    print("9. VPL config commands ok")

    # --- delete rules re-derive ------------------------------------------------
    for styp in store.list_sourcetypes():
        store.delete_sourcetype(styp['id'])
    store.reindex_all()
    r = run_vpl(db, parse_vpl('where sourcetype==text', db))
    assert _names(r) == ['app.log', 'todo.txt'], _names(r)  # both fall back to their category
    print("10. delete rule + reindex falls back ok")

    # --- VPL error handling for config commands --------------------------------
    for bad in ['index frobnicate x', 'index add onlyname',
                'index rm nosuchindex', 'extract add x', 'extract add x "(g"']:
        try:
            run_vpl(db, parse_vpl(bad, db))
            raise AssertionError(f"'{bad}' should have raised")
        except VplError:
            pass
    print("11. config command error handling ok")

    # --- store stats helpers ----------------------------------------------------
    assert {s['name'] for s in store.index_stats()} >= {DEFAULT_INDEX, 'reports'}
    fs = store.extracted_field_stats()
    assert any(f['field'] == 'errkind' and f['sample'] == 'connection' for f in fs), fs
    print("12. store stats helpers ok")

    print("ALL INGESTION TESTS PASSED")


if __name__ == "__main__":
    main()
