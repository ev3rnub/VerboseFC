"""Run due scheduled searches once and exit — point cron/launchd at this.

Crontab example (every 5 minutes):
    */5 * * * * cd ~/dev_archive/Verbose_fc/src && python scheduled_runner.py

Uses the persistent index at ~/.verbosefc/verbosefc_index.db by default.
"""

import sys
from pathlib import Path

from db import SearchDatabase
from saved_searches import SavedSearchStore

PERSISTENT_DB_PATH = Path.home() / ".verbosefc" / "verbosefc_index.db"


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(PERSISTENT_DB_PATH)
    if not Path(db_path).exists():
        print(f"index not found: {db_path}")
        return 1
    store = SavedSearchStore(SearchDatabase(db_path))
    n = store.run_due()
    print(f"ran {n} due scheduled search(es) against {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
