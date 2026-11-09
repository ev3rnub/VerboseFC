# 4. Cleaning Up Duplicates

Two files are "duplicates" when the program has checked their contents and they are
**byte-for-byte identical** (not just the same name). The duplicates finder lives in
the **📁 File Explorer** tab, sub-tab **👯 Duplicates**.

> This needs hashing from the scan (the sidebar **🔐 Hashing Options**: MD5 or
> SHA256). If the tab says there is no hash information, turn one on and scan again.

## Step by step

1. Open **📁 File Explorer → 👯 Duplicates**.
   You see one box per group of identical files, headed like
   `🔁 3 copies — 4.2 MB — budget.xlsx`.
2. **Open a group and check it.** Each copy is listed with its full path, size,
   fingerprint (hash) and modified date. Identical content is confirmed by the
   same hash — read the paths carefully.
3. **Choose which copy to keep** with the *Keep this copy* buttons.
   By default the program preselects the **oldest** copy; change it if you prefer.
4. **Tick** *Act on this group (move the other copies)* for each group you want to fix.
5. **Choose what happens to the others** (top of the page):
   - **Move to Trash** — the normal Recycle Bin / Trash. You can restore later.
   - **Move to backup folder** — moved into a folder like `~/verbosefc_backups`,
     keeping the same subfolder structure so you can put things back.
6. **Tick the confirmation checkbox** *✅ I confirm: move the selected duplicate
   copies*, then press **🚚 Apply to selected groups**.
7. A report shows how many were moved and whether anything failed.

Nothing happens before step 6, and the kept copy is never touched.
If more than 100 duplicate groups exist, only the first 100 are shown — the rest
are left alone until you re-scan a smaller folder.

### A note on "fast hashing"

If you ticked **Fast hashing (first 1 MB only)** in the sidebar, the page says so:
files are compared by their first megabyte plus their size. This is much faster but
in rare cases two different files can look identical. Always read the paths in a
group before moving anything.

## Same name, but different content

Below the duplicates there's a **"Filename Collision"** table: files with the same
name but *different* contents (e.g. `notes.docx` in three folders). The program
lists them but never merges or removes them — that's your call in Finder/Explorer.

## Opening files and finding them on disk

Anywhere you see a file (File List, Search results), you can use:

- **📂 Open file** — opens it in its normal app (Preview, Word, …).
- **📍 Reveal in Finder / Explorer** — shows the file inside its folder.

Both act on the computer running the program, which is the same machine you're
sitting at in normal use.

## The 🌳 Tree View sub-tab

A plain indented listing of the scanned folder — useful to see the shape of
everything without charts or search.

Next: [5. The Dashboards →](05-The-Dashboards.md)
