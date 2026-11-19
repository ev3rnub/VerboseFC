"""Tests for the File List tab's "Move / Delete Selected Files" engine path.

Run from src/:  python test_fileops.py

The UI section is a thin shell over two guarantees that live below the UI:
  - file_actions.move_files_out — batch-move selected files (Trash or
    backup). A file that is already gone reports "missing" instead of
    moving or failing, one bad file never aborts the batch, and a missing
    backup destination refuses to move anything.
  - db.delete_file_by_path — when the File List applies a move, the search
    index drops the file's row (and its FTS text), so ghosts stop
    haunting the Search tab and the Duplicates view.
"""

import os
import sys
import tempfile
from pathlib import Path

from db import SearchDatabase
from file_actions import move_files_out
from indexer import FileIndexer

FAILURES = []


def check(name, condition, detail=''):
    print(f"{'ok  ' if condition else 'FAIL'} {name}{'' if condition else '  <- ' + detail}")
    if not condition:
        FAILURES.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='vfc_fileops_'))
    scan = tmp / 'scan'
    (scan / 'docs' / 'deep').mkdir(parents=True)
    backup = tmp / 'backup'

    # --- 1. backup move mirrors the scan-root path ------------------------
    f1 = scan / 'readme.md'
    f1.write_text('notes body\n' * 40)
    res = move_files_out([str(f1)], 'backup', str(backup), str(scan))
    check('backup move mirrors the relative path and empties the source',
          res[0][1] == 'moved' and not f1.exists()
          and (backup / 'readme.md').exists(), repr(res))

    # --- 2. an already-missing file is reported, never "moved" ------------
    res = move_files_out([str(scan / 'ghost.txt')], 'trash', str(backup), str(scan))
    check('missing file reports "missing"', res[0][1] == 'missing', repr(res))

    # --- 3. one unreadable file cannot abort the batch ---------------------
    bad, good = scan / 'locked.txt', scan / 'free.txt'
    bad.write_text('locked\n')
    good.write_text('free\n')
    if os.name == 'posix' and os.geteuid() != 0:   # root would bypass chmod 000
        os.chmod(bad, 0o000)
        res = move_files_out([str(bad), str(good)], 'backup',
                             str(backup), str(scan))
        os.chmod(bad, 0o644)
        check('batch continues past an unreadable file',
              res[0][1] == 'failed' and res[1][1] == 'moved' and not good.exists(),
              repr(res))

    # --- 4. no backup destination means nothing moves ---------------------
    stay = scan / 'docs' / 'deep' / 'stay.txt'
    stay.write_text('stay\n')
    res = move_files_out([str(stay)], 'backup', None, str(scan))
    check('empty backup destination refuses to move',
          res[0][1] == 'failed' and stay.exists(), repr(res))

    # --- 5. trash action where the platform supports it --------------------
    try:
        import send2trash  # noqa: F401
        have_trash = True
    except ImportError:
        have_trash = False
    if have_trash:
        trashed = scan / 'trashme.txt'
        trashed.write_text('bye\n')
        res = move_files_out([str(trashed)], 'trash', str(backup), str(scan))
        # Either the file left (moved) or the platform refused (failed) —
        # what must never happen is a silent half-move.
        check('trash action empties the source or fails cleanly',
              (res[0][1] == 'moved' and not trashed.exists())
              or (res[0][1] == 'failed' and trashed.exists()), repr(res))

    # --- 6. a moved file leaves the search index too -----------------------
    (scan / 'notes.txt').write_text('quadrilateral report draft\n' * 10)
    (scan / 'still_here.txt').write_text('file on a volume that stayed mounted\n' * 10)
    db = SearchDatabase(str(tmp / 'idx.db'))
    idx = FileIndexer(db)
    stats = idx.index_files([scan / 'notes.txt', scan / 'still_here.txt'])
    check('fixture indexed both files', stats.get('indexed', 0) == 2, repr(stats))

    # The UI contract: a moved file's index row is purged with it; a file
    # that merely errored (or vanished) keeps its row.
    res = move_files_out([str(scan / 'notes.txt')], 'backup', str(backup), str(scan))
    removed = db.delete_file_by_path(res[0][0])
    paths_left = [m['full_path'] for m in db.get_all_files_meta()]
    check('moved file leaves the index (row purged after move)',
          removed and not any(p.endswith('notes.txt') for p in paths_left),
          repr(paths_left))
    check('index keeps a file that is still on disk',
          any(p.endswith('still_here.txt') for p in paths_left), repr(paths_left))

    db.close()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FILEOPS TEST(S) FAILED: {", ".join(FAILURES)}')
        return 1
    print('ALL FILEOPS TESTS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
