"""
File actions module.
Cross-platform open/reveal (Finder / Explorer), move-to-trash, and
backup-move helpers used by the File Explorer and Search tabs.
"""

import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional


def _system() -> str:
    return platform.system()  # Windows | Darwin | Linux


def duplicate_mover_targets(group_paths, keeper_path):
    """Decide which copies of a duplicate group get moved.

    Returns (targets, skip_reason). A group whose kept copy cannot be resolved is
    skipped instead of having every copy moved: with a stale UI selection the
    "keep one" default becomes "keep none", and files that reach the Trash are
    not recoverable from inside the app.

    group_paths: the paths of this group that are still present in the results
    keeper_path: the path the user chose to keep, or None when unknown/stale
    """
    if not keeper_path:
        return [], "no valid kept copy selected - reopen the group and choose one"
    if keeper_path not in group_paths:
        return [], "kept copy is no longer listed"
    return [p for p in group_paths if p != keeper_path], None


def open_path(file_path: str) -> None:
    """Open a file or folder with its default application."""
    p = str(Path(file_path))
    if _system() == "Windows":
        os.startfile(p)  # type: ignore[attr-defined]
    elif _system() == "Darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def reveal_path(file_path: str) -> None:
    """Reveal a file in Finder (macOS) / Explorer (Windows) / file manager (Linux)."""
    p = str(Path(file_path))
    if _system() == "Windows":
        subprocess.Popen(["explorer", "/select,", p])
    elif _system() == "Darwin":
        subprocess.Popen(["open", "-R", p])
    else:
        # No universal 'select' on Linux; open the containing folder
        subprocess.Popen(["xdg-open", os.path.dirname(p)])


def move_to_trash(file_path: str) -> str:
    """
    Move a file to the OS trash / Recycle Bin.
    Returns a human-readable result message.
    """
    from send2trash import send2trash
    p = Path(file_path)
    if not p.exists():
        return f"SKIP (missing): {file_path}"
    send2trash(str(p))
    return f"Trashed: {file_path}"


def backup_move(file_path: str, backup_root: str, scan_root: Optional[str] = None) -> str:
    """
    Copy a file into the backup folder (mirroring its relative path when a
    scan root is given), then delete the original.
    Returns a human-readable result message.
    """
    src = Path(file_path)
    if not src.exists():
        return f"SKIP (missing): {file_path}"

    backup = Path(backup_root).expanduser()
    backup.mkdir(parents=True, exist_ok=True)

    if scan_root:
        try:
            rel = src.relative_to(Path(scan_root))
        except ValueError:
            rel = Path(src.name)
    else:
        rel = Path(src.name)

    dest = backup / rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Avoid clobbering an existing backup copy
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = dest.with_name(f"{dest.stem}.{stamp}{dest.suffix}.bak")

    shutil.copy2(str(src), str(dest))
    os.remove(str(src))
    return f"Backed up: {file_path} -> {dest}"
