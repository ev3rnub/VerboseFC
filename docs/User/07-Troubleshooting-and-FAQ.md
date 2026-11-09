# 7. Troubleshooting & FAQ

**The page says nothing about my files.**
Nothing is scanned until you pick a **Base Path** in the left panel and press
**Scan & Index Directory**. Start with guide [2](02-First-Scan.md).

**The scan is very slow.**
Big folders take time. Try: lower the **Max Recursion Depth** slider, raise
**Parallel Workers**, and tick **Fast hashing (first 1 MB only)**. You can
**⏸ Pause** or **🛑 Cancel** anytime — cancelling keeps what was already found.

**I closed the Terminal window and the page stopped working.**
That window *is* the program. Open Terminal and run
`streamlit run src/verboseFC.py` again.

**I searched for a word inside a PDF but the file isn't found.**
Check the file is actually inside the folder you scanned, then:
- Files over **10 MB** have only their name/size saved, never their text.
- Some PDFs are just pictures of pages — there is no text to find.
- A **Scan complete** line reports how many files had text extracted
  ("text extracted from N").

**I scanned before and the search tab is empty today.**
With **Persistent Index** on, your list is saved and loads automatically. If it's
empty, the toggle was probably off last time — turn it on and scan again.

**The duplicates tab says no hash information.**
In the sidebar open **🔐 Hashing Options** and make sure **Compute SHA256** (or MD5)
is ticked, then scan again.

**A duplicate group looks wrong — the files aren't really the same!**
If you used **Fast hashing**, that's the known small risk (comparison is limited to
the first 1 MB + file size). Untick it, re-scan, and compare again.

**I moved duplicates by mistake.**
"Trash" is your normal Trash / Recycle Bin — restore them there. "Backup folder"
moves keep the folder structure, so you can copy them back.

**The 📁 Browse… button does nothing (or says picker unavailable).**
The folder picker needs a normal desktop session on the computer running the
program. On a machine without one, type the path instead (e.g. `~/Documents`).

**A search shows an error in red starting with "VPL:".**
That's the search-box language complaining about a typo. Common fixes: every piece
is separated by `|`; `where` needs a known field (`ext, name, path, size, modified,
created, md5, sha256, category, type`); sizes look like `500kb`/`1mb`; ages look
like `7d`/`24h` or a date like `2025-01-15`.

**Search seems to ignore numbers I type.**
By design, plain numbers don't match inside text — a recent update stopped things
like `12` matching byte sizes in the index.

**Charts or lists show data from an old scan.**
Storage uses the latest scan (or the saved list). Re-scan to refresh.

**May I move or delete the file at `~/.verbosefc/verbosefc_index.db`?**
It's the saved list (plus alerts and history). Deleting it only loses the list —
nothing on your disk changes — but the next scan starts from zero.
Windows path: `C:\Users\you\.verbosefc\verbosefc_index.db`.

**Can other people on my Wi-Fi see this?**
No. The program only listens to your own computer unless someone deliberately
starts it differently on purpose — and it has no password, so it should stay that way.

**Did the program change any of my files?**
It only ever *reads* them, plus two deliberate actions: moving duplicates
(Trash/backup, always after your confirmation) and nothing else. Open/Reveal just
launches the file normally.
