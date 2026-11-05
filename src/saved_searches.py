"""Saved & scheduled searches for the Search tab.

Tables live in the same SQLite index DB (thread-local connections make the
background scheduler safe). Schedules run while the app is open via
SchedulerThread, or from cron/launchd via scheduled_runner.py.
"""

import json
import threading
import time
from datetime import datetime, timedelta

from vpl_parser import VplError, add_time_range, parse_vpl, run_vpl

# Time-range labels shared with the Search tab picker.
TIME_RANGES = [
    "Any time", "Last hour", "Last 24 hours", "Last 7 days", "Today",
    "Yesterday", "Last 30 days", "Last 90 days", "This year",
]


def time_range_bounds(choice: str, now: float = None):
    """Map a time-range label to (min_epoch, max_epoch) or (None, None).

    Custom ranges are stored as 'YYYY-MM-DD..YYYY-MM-DD' and parsed here.
    """
    now = time.time() if now is None else now
    if not choice or choice == "Any time":
        return None, None
    if ".." in choice:
        try:
            a, b = choice.split("..", 1)
            lo = datetime.strptime(a.strip(), "%Y-%m-%d")
            hi = datetime.strptime(b.strip(), "%Y-%m-%d") + timedelta(days=1)
            return lo.timestamp(), min(hi.timestamp(), now)
        except ValueError:
            return None, None
    dt = datetime.fromtimestamp(now)
    if choice == "Last hour":
        return now - 3600, now
    if choice == "Last 24 hours":
        return now - 86400, now
    if choice == "Last 7 days":
        return now - 7 * 86400, now
    if choice == "Last 30 days":
        return now - 30 * 86400, now
    if choice == "Last 90 days":
        return now - 90 * 86400, now
    if choice == "Today":
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp(), now
    if choice == "Yesterday":
        start = (dt - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp(), start + timedelta(days=1) - timedelta(microseconds=1)
    if choice == "This year":
        return dt.replace(month=1, day=1).timestamp(), now
    return None, None


class SavedSearchStore:
    """CRUD + execution for saved searches and their scheduled runs."""

    def __init__(self, db):
        self.db = db
        self._init_tables()

    def _init_tables(self):
        cur = self.db._get_connection().cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS saved_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                query TEXT NOT NULL,
                time_range TEXT NOT NULL DEFAULT 'Any time',
                schedule_minutes INTEGER,
                created_at TEXT NOT NULL,
                last_run_at REAL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                saved_id INTEGER NOT NULL,
                run_at REAL NOT NULL,
                result_count INTEGER,
                top_rows TEXT,
                error TEXT
            )
        ''')
        self.db._get_connection().commit()

    # --- CRUD ---

    def save(self, name: str, query: str, time_range: str = "Any time",
             schedule_minutes: int = None) -> int:
        name, query = name.strip(), query.strip()
        if not name or not query:
            raise VplError("save: name and query are both required")
        parse_vpl(query)  # reject invalid pipelines at save time
        cur = self.db._get_connection().cursor()
        cur.execute(
            'INSERT INTO saved_searches (name, query, time_range, schedule_minutes, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (name, query, time_range, schedule_minutes, datetime.now().isoformat(timespec='seconds')))
        self.db._get_connection().commit()
        return cur.lastrowid

    def delete(self, saved_id: int):
        cur = self.db._get_connection().cursor()
        cur.execute('DELETE FROM scheduled_runs WHERE saved_id = ?', (saved_id,))
        cur.execute('DELETE FROM saved_searches WHERE id = ?', (saved_id,))
        self.db._get_connection().commit()

    def set_schedule(self, saved_id: int, minutes):
        cur = self.db._get_connection().cursor()
        cur.execute('UPDATE saved_searches SET schedule_minutes = ? WHERE id = ?',
                    (minutes, saved_id))
        self.db._get_connection().commit()

    def list(self) -> list:
        cur = self.db._get_connection().cursor()
        cur.execute('SELECT * FROM saved_searches ORDER BY id')
        return [dict(r) for r in cur.fetchall()]

    def recent_runs(self, limit: int = 25) -> list:
        cur = self.db._get_connection().cursor()
        cur.execute('''SELECT r.*, s.name FROM scheduled_runs r
                       JOIN saved_searches s ON s.id = r.saved_id
                       ORDER BY r.id DESC LIMIT ?''', (limit,))
        return [dict(r) for r in cur.fetchall()]

    def unseen_runs(self, since_id: int = 0) -> int:
        cur = self.db._get_connection().cursor()
        cur.execute('SELECT COUNT(*) AS n FROM scheduled_runs WHERE id > ?', (since_id,))
        return cur.fetchone()['n']

    # --- execution ---

    def run_saved(self, saved: dict) -> dict:
        """Run one saved search now. Returns a run record dict (not stored)."""
        try:
            stages = parse_vpl(saved['query'])
            lo, hi = time_range_bounds(saved.get('time_range') or 'Any time')
            stages = add_time_range(stages, lo, hi)
            res = run_vpl(self.db, stages)
            top = res.df.head(10)
            cols = [c for c in ('name', 'size_human', 'modified_at') if c in top.columns]
            return {'result_count': res.total,
                    'top_rows': json.dumps(top[cols].to_dict('records'), default=str),
                    'error': None}
        except (VplError, Exception) as e:  # noqa: BLE001 — store any failure for the inbox
            return {'result_count': None, 'top_rows': None, 'error': str(e)}

    def record_run(self, saved_id: int, outcome: dict) -> int:
        cur = self.db._get_connection().cursor()
        cur.execute('INSERT INTO scheduled_runs (saved_id, run_at, result_count, top_rows, error) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (saved_id, time.time(), outcome['result_count'],
                     outcome['top_rows'], outcome['error']))
        cur.execute('UPDATE saved_searches SET last_run_at = ? WHERE id = ?',
                    (time.time(), saved_id))
        self.db._get_connection().commit()
        return cur.lastrowid

    def run_due(self, now: float = None) -> int:
        """Run every scheduled search whose interval has elapsed. Returns count run."""
        now = time.time() if now is None else now
        cur = self.db._get_connection().cursor()
        cur.execute('''SELECT * FROM saved_searches
                       WHERE schedule_minutes IS NOT NULL
                         AND (last_run_at IS NULL OR last_run_at <= ? - schedule_minutes * 60)''',
                    (now,))
        due = [dict(r) for r in cur.fetchall()]
        for saved in due:
            self.record_run(saved['id'], self.run_saved(saved))
        return len(due)


class SchedulerThread(threading.Thread):
    """Daemon thread that fires due scheduled searches while the app is open."""

    def __init__(self, store: SavedSearchStore, interval: float = 20.0):
        super().__init__(daemon=True, name='verbosefc-scheduler')
        self.store = store
        self.interval = interval
        self._stop = threading.Event()
        self.last_error = None   # last scheduler failure, shown in the Search tab

    def run(self):
        while not self._stop.is_set():
            try:
                self.store.run_due()
                self.last_error = None
            except Exception as e:
                # Transient DB locks are expected and retried, but the failure
                # is kept so the Search tab can say why nothing showed up.
                self.last_error = f"{type(e).__name__}: {e}"
            self._stop.wait(self.interval)
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
