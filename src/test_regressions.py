"""Regression tests for the 11/05 production fixes.

Run from src/:  python test_regressions.py

Covers the behaviours that were broken (or unguarded) before the fixes:
  1. FTS5 no longer indexes file_id, so numeric queries cannot match an id.
  2. An index written with the old FTS table is rebuilt in place, keeping rows.
  3. The content-size cap is enforced and counted (content_skipped).
  4. Archive members are read with a cap, and the member count is capped.
  5. A single file read is bounded, so a huge text file cannot balloon memory.
  6. duplicate_mover_targets refuses to move every copy when the kept copy is
     unknown (the old duplicate tool treated "no selection" as "move all").
  7. get_all_files_meta returns listing columns without the content blob.
"""

import sys
import tempfile
import zipfile
from pathlib import Path

from content_extractor import (ARCHIVE_MEMBER_READ_BYTES, MAX_ARCHIVE_ENTRIES,
                               MAX_CONTENT_CHARS, ContentExtractor)
from db import SearchDatabase
from file_actions import duplicate_mover_targets
from indexer import FileIndexer

FAILURES = []


def check(name, condition, detail=''):
    print(f"{'ok  ' if condition else 'FAIL'} {name}{'' if condition else '  <- ' + detail}")
    if not condition:
        FAILURES.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='vfc_regress_'))

    # --- 1. fresh index: numeric term must not match the row id --------------
    db = SearchDatabase(str(tmp / 'fresh.db'))
    indexer = FileIndexer(db)
    doc = tmp / 'report.txt'
    doc.write_text('quarterly revenue for the kettle project', encoding='utf-8')
    fid = indexer.index_file(doc)
    check('fixture indexed', bool(fid), repr(fid))
    _rows, total = db.search_files(str(fid))
    check('numeric query does not match file_id', total == 0, f'{total} rows for "{fid}"')
    _rows, total = db.search_files('kettle')
    check('word query still matches content', total == 1, f'{total} rows')

    # --- 2. legacy index is migrated in place --------------------------------
    legacy_path = str(tmp / 'legacy.db')
    db2 = SearchDatabase(legacy_path)
    doc2 = tmp / 'legacy_note.txt'
    doc2.write_text('archaeopteryx legacy migration marker', encoding='utf-8')
    fid2 = db2.insert_file({
        'name': doc2.name, 'full_path': str(doc2), 'size_bytes': doc2.stat().st_size,
        'size_human': '1 KB', 'md5': 'x', 'sha256': 'y', 'extension': '.txt',
        'file_type': 'Text', 'file_category': 'text', 'created_at': '0', 'modified_at': '0',
        'accessed_at': '0', 'created_at_epoch': 0, 'modified_at_epoch': 0, 'accessed_at_epoch': 0,
        'content': 'archaeopteryx legacy migration marker',
        'content_preview': 'archaeopteryx legacy migr…', 'content_available': 1})
    conn2 = db2._get_connection()
    # Rewrite the FTS table the way the old code created it: file_id indexed.
    conn2.execute('DROP TABLE content')
    conn2.execute('''
        CREATE VIRTUAL TABLE content USING fts5(
            file_id, preview, full_content, tokenize='porter unicode61'
        )
    ''')
    conn2.execute("INSERT INTO content (file_id, preview, full_content) VALUES (?, ?, ?)",
                  (fid2, 'archaeopteryx legacy migr…', 'archaeopteryx legacy migration marker'))
    conn2.commit()
    check('legacy index has the false positive',
          conn2.execute("SELECT COUNT(*) FROM content WHERE content MATCH '1'").fetchone()[0] == 1,
          'expected the old table to match the numeric id')
    db2.close()

    db3 = SearchDatabase(legacy_path)          # opening it must migrate
    conn3 = db3._get_connection()
    schema = conn3.execute("SELECT sql FROM sqlite_master WHERE name='content'").fetchone()[0]
    check('legacy FTS table rebuilt with UNINDEXED file_id', 'unindexed' in schema.lower(), schema)
    kept = conn3.execute('SELECT file_id, full_content FROM content').fetchall()
    check('legacy rows preserved by the migration',
          len(kept) == 1 and kept[0]['full_content'].startswith('archaeopteryx'), repr(kept))
    _r, total = db3.search_files('archaeopteryx')
    check('migrated index still searches', total == 1, f'{total} rows')
    _r, total = db3.search_files(str(fid2))
    check('migrated index loses the numeric false positive', total == 0, f'{total} rows')
    db3.close()

    # --- 3. size cap enforced and counted ------------------------------------
    big = tmp / 'huge.log'
    big.write_text('needle in a very large haystack ' * 4000, encoding='utf-8')  # ~116 KB
    db4 = SearchDatabase(str(tmp / 'cap.db'))
    capped = FileIndexer(db4, max_content_size=10_000)
    big_id = capped.index_file(big)
    row = db4.get_file_by_id(big_id)
    stats = capped.get_stats()
    check('oversized file stored without content',
          row['content_available'] == 0, repr(row['content_available']))
    check('oversized file counted as content_skipped',
          stats['content_skipped'] == 1, repr(stats))
    small = tmp / 'small.log'
    small.write_text('needle in a small haystack', encoding='utf-8')
    small_id = capped.index_file(small)
    check('file under the cap still gets content',
          db4.get_file_by_id(small_id)['content_available'] == 1)

    # --- 4. archive members: count and read size both capped ------------------
    archive = tmp / 'many.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        for i in range(MAX_ARCHIVE_ENTRIES + 150):
            zf.writestr(f'member_{i}.txt', f'marker_{i} ' + 'z' * 20000)
    full, preview = ContentExtractor().extract_archive_content(archive)
    members = [ln for ln in full.split('\n') if ln.endswith('.txt:')]
    check('archive member count capped', len(members) <= MAX_ARCHIVE_ENTRIES, f'{len(members)} members')
    check('archive text bounded', len(full) <= MAX_ARCHIVE_ENTRIES * 1000 + 200 * len(members),
          f'{len(full)} chars')
    single = tmp / 'one.zip'
    with zipfile.ZipFile(single, 'w') as zf:
        zf.writestr('big_member.txt', 'q' * 500_000)
    full_one, _p = ContentExtractor().extract_archive_content(single)
    check('single archive member read capped',
          len(full_one) <= ARCHIVE_MEMBER_READ_BYTES, f'{len(full_one)} chars')

    # --- 5. plain file read bounded ------------------------------------------
    huge_text = tmp / 'huge_text.txt'
    huge_text.write_text('lorem ipsum dolor sit amet ' * (MAX_CONTENT_CHARS // 9), encoding='utf-8')
    db5 = SearchDatabase(str(tmp / 'huge.db'))
    loose = FileIndexer(db5, max_content_size=0)   # 0 disables the size gate
    huge_id = loose.index_file(huge_text)
    check('oversized text file is indexed',
          db5.get_file_by_id(huge_id)['content_available'] == 1)
    stored = db5._get_connection().execute(
        'SELECT full_content FROM content WHERE file_id = ?', (huge_id,)).fetchone()[0]
    check('stored text is capped per file',
          len(stored) <= MAX_CONTENT_CHARS + 60, f'{len(stored)} chars stored')
    _r, total = db5.search_files('lorem')
    check('bounded extraction keeps the file searchable', total == 1, f'{total} rows')
    db5.close()

    # --- 6. duplicate mover refuses to move everything ------------------------
    paths = ['/x/a.txt', '/x/b.txt', '/x/c.txt']
    targets, skip = duplicate_mover_targets(paths, None)
    check('no keeper selected -> nothing moved', targets == [] and skip, repr((targets, skip)))
    targets, skip = duplicate_mover_targets(paths, '/x/gone.txt')
    check('keeper not in group -> nothing moved', targets == [] and skip, repr((targets, skip)))
    targets, skip = duplicate_mover_targets(paths, '/x/b.txt')
    check('keeper kept, others moved', skip is None and targets == ['/x/a.txt', '/x/c.txt'],
          repr(targets))

    # --- 7. listing query stays light -----------------------------------------
    metas = db.get_all_files_meta()
    check('listing query omits content_preview',
          metas and 'content_preview' not in metas[0] and 'full_path' in metas[0],
          repr(sorted(metas[0])) if metas else 'no rows')

    db4.close()
    db.close()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} REGRESSION TEST(S) FAILED: {", ".join(FAILURES)}')
        return 1
    print('ALL REGRESSION TESTS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
