"""Tests for saved & scheduled searches."""
import os
import tempfile
import time

from db import SearchDatabase
from saved_searches import SavedSearchStore, time_range_bounds
from vpl_parser import VplError
from test_vpl import make_db


def main():
    db = make_db()
    store = SavedSearchStore(db)

    # time range mapping
    lo, hi = time_range_bounds("Any time")
    assert lo is None and hi is None
    lo, _ = time_range_bounds("Last 7 days")
    assert abs((time.time() - 7 * 86400) - lo) < 5
    lo, hi = time_range_bounds("2026-01-01..2026-02-01", now=time.time())
    assert lo < hi

    # save / list / reject invalid
    sid = store.save("quarterly pdfs", 'search quarterly | where ext==pdf', "Any time")
    assert store.list()[0]['name'] == "quarterly pdfs"
    try:
        store.save("bad", 'frobnicate x')
        raise AssertionError("invalid VPL must be rejected at save time")
    except VplError:
        pass

    # run now
    outcome = store.run_saved(store.list()[0])
    assert outcome['error'] is None and outcome['result_count'] == 2, outcome

    # schedule + run_due respects the interval
    store.set_schedule(sid, 60)
    assert store.run_due() == 1          # first due: never run
    assert store.run_due() == 0          # just ran, not due again
    assert store.run_due(now=time.time() + 3600) == 1  # an hour later: due

    runs = store.recent_runs()
    assert len(runs) == 2 and runs[0]['result_count'] == 2, runs
    assert store.unseen_runs(0) == 2
    assert store.unseen_runs(runs[-1]['id']) == 1  # runs are newest-first

    # failing search is recorded, not raised (invalid query injected directly)
    conn = db._get_connection()
    conn.execute('INSERT INTO saved_searches (name, query, time_range, schedule_minutes, created_at) '
                 'VALUES (?, ?, ?, ?, ?)',
                 ('broken', 'frobnicate x', 'Any time', 60, '2026-01-01T00:00:00'))
    conn.commit()
    sid2 = conn.execute('SELECT last_insert_rowid() AS i').fetchone()['i']
    assert store.run_due(now=time.time() + 999999) >= 1
    broken = [r for r in store.recent_runs(50) if r['saved_id'] == sid2]
    assert broken and broken[0]['error'], broken

    # delete removes runs too
    store.delete(sid2)
    assert all(r['saved_id'] != sid2 for r in store.recent_runs(100))

    print("ALL SAVED-SEARCH TESTS PASSED")


if __name__ == "__main__":
    main()
