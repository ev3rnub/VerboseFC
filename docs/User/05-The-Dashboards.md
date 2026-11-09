# 5. The Dashboards

Three tabs are "views" rather than tools you act on: **📊 Storage**, **🗄️ Database**,
and **👀 Watch**. (Watch is a tool — it tells you when folders change.)

## 📊 Storage — where your disk space went

Uses the most recent scan (or the saved list if you have a persistent index).

### 📈 Overview sub-tab
Big numbers at the top: **Files**, **Total Size**, **Largest File**,
**Most Common Type**. Below them:

- **Size by extension (top 15)** — which file types are eating the space.
- **File count by extension (top 15)** — which types are most numerous.
- **Top 10 largest files** — the individual heavyweights.
- **Files by month modified (last 24 months)** — when things were touched.

### 🗺️ Disk Map sub-tab
A round, ring-shaped picture (like TreeSize) of your folder:

- The **center** is the folder you scanned; each ring outward is one level deeper.
- Bigger slice = more megabytes. Hover any slice for its size and its share of its
  parent folder.
- **Drill in**: pick a folder in the **"Drill into folder (largest first)"** box —
  the picture redraws centered on that folder. **⬅ Up one level** and **🏠 Root**
  take you back out.
- **Rings to show** slider (1–4): how many levels are drawn at once. Deeper files
  roll up into a "(deeper…)" chunk to keep the picture readable.

## 👀 Watch — alerts when folders change

Use this if you want to know when files appear, change, or disappear in particular
folders (for example a shared "Inbox").

1. Type or **📁 Browse…** for a folder, then **➕ Add folder**. Add as many as you like.
2. Press **🟢 Start monitoring**. The program now pays attention to those folders
   while it's running. **🔴 Stop monitoring** turns it off; **Remove** forgets a folder.
3. Every change becomes an **alert** (created / modified / deleted / moved).
   The tab shows a count badge — e.g. `👀 Watch (7)` — of alerts you haven't seen.
4. The alert list updates by itself every few seconds. Use
   **✔ Acknowledge all** to mark them seen, or **🧹 Clear all alerts** to wipe the list.
   Alerts are saved, so they're still there when you reopen the program.

> Watching only works while the program is running. It does not run in the
> background after you close the browser.

## 🗄️ Database — the program's own record book

Most users never need this tab; it's for checking the program is healthy.

- **Top numbers:** how big the record file is, how much of it is actually used,
  wasted-but-reusable space, and an **Integrity** check (should say `ok`).
  A hint appears if the file could be slimmed (a "VACUUM" cleanup).
- **🧿 Size & Composition:** round charts of where the bytes go (the word-search
  index vs. tables vs. indexes), file categories, and what share of your files
  actually had readable text extracted.
- **📈 Activity:** searches per day, your most frequent queries, how many indexed
  files were modified in the last 90 days, and watch alerts by type/day.
- **🧱 Tables & Integrity:** row counts and storage per table, plus the size of
  the record file's helper files. Everything here is read-only — it never
  interferes with a running scan.

Next: [6. Saved & Scheduled Searches →](06-Saved-and-Scheduled-Searches.md)
