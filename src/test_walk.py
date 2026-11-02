"""Walk pruning + depth semantics test."""
import time, tempfile, shutil
from pathlib import Path
from scan_runner import ScanController


def main():
    root = Path(tempfile.mkdtemp(prefix="vfc_prune_"))
    (root / "keep").mkdir(); (root / "keep" / "ok.txt").write_text("a")
    (root / ".Trash").mkdir(); (root / ".Trash" / "junk.txt").write_text("j")
    (root / "$RECYCLE.BIN").mkdir(); (root / "$RECYCLE.BIN" / "junk2.txt").write_text("j")
    (root / "keep" / "sub").mkdir(); (root / "keep" / "sub" / "deep.txt").write_text("d")
    (root / "keep" / "sub" / "deeper").mkdir()
    (root / "keep" / "sub" / "deeper" / "toodeep.txt").write_text("x")

    c = ScanController()
    c.start(root, 3, 2, {'md5': False, 'sha256': True, 'fast_limit': None}, None)
    while c.is_active():
        time.sleep(0.1)
    found = sorted(Path(r['full_path']).relative_to(root).as_posix() for r in c.results)
    print("depth 3 found:", found)
    assert found == ["keep/ok.txt", "keep/sub/deep.txt"], found

    c2 = ScanController()
    c2.start(root, 1, 2, {'md5': False, 'sha256': True, 'fast_limit': None}, None)
    while c2.is_active():
        time.sleep(0.1)
    found1 = [Path(r['full_path']).relative_to(root).as_posix() for r in c2.results]
    print("depth 1 found:", found1, "(root-level files only — none in fixture)")
    assert found1 == [], found1

    shutil.rmtree(root, ignore_errors=True)
    print("PRUNING + DEPTH OK")


if __name__ == "__main__":
    main()
