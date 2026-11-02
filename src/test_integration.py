"""Integration smoke test for scan_runner, db alerts, watcher, file_actions."""
import time, os, tempfile, shutil
from pathlib import Path
from collections import Counter

from scan_runner import ScanController
from db import SearchDatabase
from indexer import FileIndexer


def main():
    root = Path(tempfile.mkdtemp(prefix="vfc_test_"))
    (root / "a").mkdir(); (root / "b").mkdir()
    (root / "a" / "one.txt").write_text("hello world duplicate content" * 100)
    (root / "b" / "two.txt").write_text("hello world duplicate content" * 100)
    (root / "a" / "unique.txt").write_text("totally different")
    (root / "a" / "sub").mkdir()
    (root / "a" / "sub" / "deep.txt").write_text("x" * 5000)

    db_dir = Path(tempfile.mkdtemp(prefix="vfc_db_"))
    db = SearchDatabase(str(db_dir / "idx.db"))
    idx = FileIndexer(db, hash_md5=False, hash_sha256=True, fast_hash_mb=0)

    def index_fn(files, progress_callback, should_stop, is_paused):
        return idx.index_files(files, workers=2, progress_callback=progress_callback,
                               should_stop=should_stop, is_paused=is_paused, skip_unchanged=True)

    # 1. scan with full hashing + indexing (single walk)
    c = ScanController()
    c.start(root, 3, 2, {'md5': False, 'sha256': True, 'fast_limit': None}, index_fn)
    while c.is_active():
        time.sleep(0.1)
    print("1. status:", c.status, "| files:", len(c.results), "| index stats:", c.index_stats)
    assert c.status == "done" and len(c.results) == 4, c.error

    h = Counter(r['sha256'] for r in c.results if r['sha256'])
    dup_groups = [k[:8] for k, v in h.items() if v > 1]
    print("   dup groups:", dup_groups, "| md5 disabled -> all empty:", all(r['md5'] == '' for r in c.results))
    assert len(dup_groups) == 1

    # 2. persistent re-scan skips unchanged
    c2 = ScanController()
    c2.start(root, 3, 2, {'md5': False, 'sha256': True, 'fast_limit': None}, index_fn)
    while c2.is_active():
        time.sleep(0.1)
    print("2. re-scan stats (expect skipped=4, indexed=0):", c2.index_stats)
    assert c2.index_stats['skipped'] == 4 and c2.index_stats['indexed'] == 0

    # 3. fast hashing
    c3 = ScanController()
    c3.start(root, 3, 2, {'md5': False, 'sha256': True, 'fast_limit': 1024}, None)
    while c3.is_active():
        time.sleep(0.1)
    print("3. fast hash ok, files:", len(c3.results))
    assert len(c3.results) == 4

    # 4. cancel mid-scan
    big = Path(tempfile.mkdtemp(prefix="vfc_big_"))
    n_big = 600
    for i in range(n_big):
        (big / f"f{i}.bin").write_bytes(os.urandom(1_000_000))
    c4 = ScanController()
    c4.start(big, 2, 2, {'md5': True, 'sha256': True, 'fast_limit': None}, None)
    time.sleep(0.3)
    c4.cancel()
    while c4.is_active():
        time.sleep(0.1)
    print("4. cancel status:", c4.status, "| partial results kept:", len(c4.results), "of", n_big)
    assert c4.status == "cancelled" and len(c4.results) < n_big

    # 5. pause/resume
    c5 = ScanController()
    c5.start(big, 2, 2, {'md5': True, 'sha256': True, 'fast_limit': None}, None)
    time.sleep(0.2)
    c5.pause()
    time.sleep(0.5)
    done_at_pause = c5.snapshot()['done']
    time.sleep(1.0)
    still = c5.snapshot()['done']
    c5.resume()
    while c5.is_active():
        time.sleep(0.1)
    print("5. pause held:", still == done_at_pause, "| resumed to:", c5.status, len(c5.results))
    assert still == done_at_pause and c5.status == "done" and len(c5.results) == n_big
    shutil.rmtree(big, ignore_errors=True)

    # 6. db alerts
    db.add_alert("created", str(root / "a" / "new.txt"), "new.txt", str(root))
    db.add_alert("deleted", str(root / "a" / "gone.txt"), "gone.txt", str(root))
    print("6. alerts:", len(db.get_alerts()), "unacked:", db.get_unacknowledged_alert_count())
    db.acknowledge_all_alerts()
    print("   after ack, unacked:", db.get_unacknowledged_alert_count())
    assert db.get_unacknowledged_alert_count() == 0

    # 7. watcher fires events
    from watcher import FolderWatcher
    events = []
    w = FolderWatcher(lambda et, p, n, wp: events.append((et, n)))
    w.watch(str(root))
    time.sleep(1.0)
    (root / "watched_new.txt").write_text("hi")
    time.sleep(1.5)
    w.stop()
    print("7. watcher events:", events)
    assert any(n == "watched_new.txt" for _, n in events)

    # 8. backup_move
    from file_actions import backup_move
    src = root / "a" / "unique.txt"
    backup = root / "backups"
    msg = backup_move(str(src), str(backup), str(root))
    print("8.", msg, "| original gone:", not src.exists(), "| backup exists:", (backup / "a" / "unique.txt").exists())
    assert not src.exists() and (backup / "a" / "unique.txt").exists()

    db.close()
    shutil.rmtree(root, ignore_errors=True)
    print("ALL INTEGRATION TESTS PASSED")


if __name__ == "__main__":
    main()
