# 2. Your First Scan

A **scan** is the one-time (per folder) job of looking through everything and
building the program's list. Everything else — searching, duplicates, charts —
works from that list, so you need to scan once before anything is useful.

## Step by step

1. **Choose the folder.** In the left panel, press **📁 Browse…** and pick a folder,
   or type its path (e.g. `~/Documents`).

2. **Decide how deep** with the **Max Recursion Depth** slider.
   - `1` = only the folder itself. `2` = the folder plus folders directly inside it.
   - The default is **2**. For a normal Documents folder, 2–4 is fine.
   - A big number (like 10) on a huge folder can take a long time.

3. **Leave the two checkboxes as they are** unless you have a reason:
   - **Enable Full Indexing** (on) — reads the text inside files so you can search
     for words. Turning it off means you can only search by name/size/date.
   - **Persistent Index** (on) — saves the list between visits, so later scans are
     fast and skip files that haven't changed. If you turn it off, the program
     starts fresh every time it opens.

4. **(Optional) Hashing Options.** Press **🔐 Hashing Options** to open it.
   "Hashing" is how the program proves two files are *identical*.
   - **Compute MD5 / Compute SHA256** — leave at least one ticked if you want the
     duplicates finder. SHA256 is the better one.
   - **Fast hashing (first 1 MB only)** — tick this for very big folders. It only
     compares the beginning of each file: much faster, with a tiny chance of
     calling two different files "the same."

5. **Press 🔍 Scan & Index Directory.** You'll see live progress:
   *🚶 Walking directories → 🔐 Hashing files → 📚 Indexing files*, with a progress
   bar and the file currently being read.
   - You can **⏸ Pause**, **▶️ Resume**, or **🛑 Cancel** at any time.
   - Cancelling keeps what it already found.

6. **Done.** You'll see a line like
   `Scan complete: 12,431 files. Indexed 12,400 (text extracted from 9,802, 12 unchanged files skipped).`
   The 📁 File Explorer and 🔍 Search tabs now have content.

## What the program skips (so you're not surprised)

- Junk/system files like `.DS_Store`, `Thumbs.db` are ignored automatically.
- Files bigger than **10 MB** are listed, but their *contents* are not read
  (so you can find them by name/size, but not by words inside).
- Compressed files (`.zip`, `.tar`, `.7z`) contribute some text from inside them;
  `.rar` files are listed by name only.

## Coming back later

Because **Persistent Index** is on by default, your list is saved
automatically (in a hidden folder in your home directory) and loads when you open
the program again. Just press **Scan & Index Directory** again to refresh: files
that didn't change are skipped, so the second scan is much faster.

> If you ever want to start completely clean, ask whoever maintains the program to
> delete the saved list file at `~/.verbosefc/verbosefc_index.db`
> (Windows: `C:\Users\you\.verbosefc\verbosefc_index.db`).

Next: [3. Finding Files →](03-Finding-Files.md)
