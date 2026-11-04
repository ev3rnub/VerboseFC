"""Tests for the mini VPL parser/executor."""
import os
import tempfile
import time

from db import SearchDatabase
from vpl_parser import (VplError, add_time_range, parse_vpl, run_vpl,
                        vpl_count, vpl_field_counts, vpl_timeline)

NOW = time.time()


def make_db() -> SearchDatabase:
    path = os.path.join(tempfile.mkdtemp(prefix="vfc_spl_"), "spl.db")
    db = SearchDatabase(path)
    files = [
        dict(name="q1.pdf", full_path="/r/reports/q1.pdf", size_bytes=5 * 1024**2,
             size_human="5 MB", md5="a1", sha256="A1", extension=".pdf",
             file_type="application/pdf", file_category="document",
             created_at_epoch=NOW - 86400 * 2, modified_at_epoch=NOW - 86400 * 2,
             content="quarterly report 2025 revenue", content_preview="quarterly report"),
        dict(name="q2.pdf", full_path="/r/reports/q2.pdf", size_bytes=2 * 1024**2,
             size_human="2 MB", md5="a2", sha256="A2", extension=".pdf",
             file_type="application/pdf", file_category="document",
             created_at_epoch=NOW - 86400 * 40, modified_at_epoch=NOW - 86400 * 40,
             content="quarterly report draft", content_preview="quarterly draft"),
        dict(name="todo.txt", full_path="/r/notes/todo.txt", size_bytes=1024,
             size_human="1 KB", md5="a3", sha256="A3", extension=".txt",
             file_type="text/plain", file_category="text",
             created_at_epoch=NOW - 3600, modified_at_epoch=NOW - 3600,
             content="buy milk", content_preview="buy milk"),
        dict(name="app.log", full_path="/r/data/logs/app.log", size_bytes=10 * 1024**2,
             size_human="10 MB", md5="a4", sha256="A4", extension=".log",
             file_type="text/plain", file_category="text",
             created_at_epoch=NOW - 86400 * 3, modified_at_epoch=NOW - 86400 * 3,
             content="error connection failed error timeout", content_preview="error log"),
        dict(name="photo.png", full_path="/r/photo.png", size_bytes=3 * 1024**2,
             size_human="3 MB", md5="a5", sha256="A5", extension=".png",
             file_type="image/png", file_category="image",
             created_at_epoch=NOW - 86400 * 10, modified_at_epoch=NOW - 86400 * 10),
    ]
    for f in files:
        db.insert_file(f)
    return db


def names(res):
    return sorted(res.df['name'].tolist()) if not res.df.empty else []


def main():
    db = make_db()

    def run(q):
        return run_vpl(db, parse_vpl(q))

    r = run('search "quarterly report" not draft')
    assert names(r) == ["q1.pdf"], names(r)
    print("1. search phrase NOT ok")

    r = run('search quarterly | where ext==pdf and size>3mb')
    assert names(r) == ["q1.pdf"], names(r)
    print("2. search + where (ext, size) ok")

    r = run('search quarterly | where modified>7d')
    assert names(r) == ["q1.pdf"], names(r)
    print("3. relative time filter ok")

    r = run('where ext==log and size>5mb')
    assert names(r) == ["app.log"], names(r)
    print("4. where-only pipeline ok")

    r = run('search error | sort -size | head 5')
    assert names(r) == ["app.log"], names(r)
    print("5. sort/head ok")

    r = run('stats count by ext')
    got = dict(zip(r.df['ext'], r.df['count']))
    assert got == {'.pdf': 2, '.txt': 1, '.log': 1, '.png': 1}, got
    print("6. stats count by ext ok")

    r = run('where name contains todo')
    assert names(r) == ["todo.txt"], names(r)
    print("7. contains ok")

    r = run('search quarterly | stats sum(size) as total by ext')
    assert list(r.df['total']) == [7 * 1024**2], list(r.df['total'])
    print("8. stats sum(size) as total ok")

    r = run('where ext==pdf | fields name, size_human')
    assert list(r.df.columns) == ['name', 'size_human'], list(r.df.columns)
    print("9. fields ok")

    r = run('search quarterly OR milk')
    assert names(r) == ["q1.pdf", "q2.pdf", "todo.txt"], names(r)
    print("10. OR ok")

    r = run('search quarterly | where ext==md | sort -date | head 20 | fields name, size_human')
    assert names(r) == [], names(r)  # no .md files in fixture, but query must parse & run
    r = run('search quarterly | sort -date')
    assert names(r) == ["q1.pdf", "q2.pdf"], names(r)  # date alias = modified, newest first
    r = run('where file contains q1')
    assert names(r) == ["q1.pdf"], names(r)
    print("12. date/file aliases ok")

    r = run('where ext==pdf | where category==document')
    assert names(r) == ["q1.pdf", "q2.pdf"], names(r)
    print("12. category field ok")

    stages = parse_vpl('search quarterly')
    assert vpl_count(db, stages) == 2
    got = dict(vpl_field_counts(db, stages, 'ext'))
    assert got == {'.pdf': 2}, got
    got = dict(vpl_field_counts(db, stages, 'category'))
    assert got == {'document': 2}, got
    print("13. vpl_count / vpl_field_counts ok")

    tl = vpl_timeline(db, parse_vpl('where name contains .'))
    assert tl and sum(n for _, n in tl) == 5, tl
    assert all(b % 60 == 0 for b, _ in tl)  # bucketed on a clean ladder width
    print("14. vpl_timeline ok")

    ranged = add_time_range(parse_vpl('search quarterly'), NOW - 86400 * 10, None)
    assert names(run_vpl(db, ranged)) == ["q1.pdf"], names(run_vpl(db, ranged))
    print("15. add_time_range ok")

    st_df = run_vpl(db, parse_vpl('sourcetypes'))
    rows = st_df.df.to_dict('records')
    assert len(rows) == 4, rows  # (.pdf,.txt,.log,.png) x file_type groups
    assert rows[0]['count'] == 2 and rows[0]['extension'] == '.pdf', rows
    scoped = run_vpl(db, parse_vpl('search quarterly | sourcetypes'))
    assert scoped.df['count'].sum() == 2, scoped.df  # only the 2 quarterly pdfs
    print("16. sourcetypes ok")

    fs = run_vpl(db, parse_vpl('fieldsummary 3'))
    assert list(fs.df['field']) == ['name', 'full_path', 'extension'], fs.df
    assert fs.df.iloc[0]['distinct'] == 5 and fs.df.iloc[0]['coverage'] == '100%'
    print("17. fieldsummary ok")

    ix = run_vpl(db, parse_vpl('index list'))
    assert ix.df.iloc[0]['files'] == 5, ix.df  # first row: the index db itself
    roots = {r['index']: r['files'] for _, r in ix.df.iterrows()}
    assert roots.get('/r/reports') == 2, roots
    print("18. index list ok")

    for bad, why in [
        ('frobnicate x', 'unknown command'),
        ('where color==red', 'unknown field'),
        ('where size>banana', 'bad size'),
        ('search', 'empty search'),
        ('head 5', 'needs search/where'),
        ('index frobnicate', 'bad index subcommand'),
        ('sourcetypes abc', 'bad limit'),
    ]:
        try:
            run(bad)
            raise AssertionError(f"'{bad}' should have raised ({why})")
        except VplError:
            pass
    print("11. error handling ok")

    print("ALL VPL TESTS PASSED")


if __name__ == "__main__":
    main()
