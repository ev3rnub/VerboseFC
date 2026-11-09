# 3. Finding Files

Everything you find is based on the list the scan built. Open the **🔍 Search** tab.

## The simple way: just type words

In the big search box, type a word or a phrase and press **🔍 Search**.

```
quarterly report
```

The search box finds files whose **content or name** contains those words. You get:

- A line at the top: `**42** of **1,300** files matched in 0.05s` — how many matched
  your words vs. how many are in the list at all.
- A **timeline** bar chart: when the matching files were last changed.
- A **field list on the left**: quick counts like `pdf (12)`, `document (40)`.
  **Click any value to narrow your results** — it adds a filter to your search and
  runs it again. (This is the easiest filter tool you have.)
- An **Events** list: one row per file. Press **▸** on a row to see all its details,
  a preview of the matching text, and buttons **📂 Open** and **📍 Reveal** (opens the
  file, or shows it in its folder).

Useful word tricks in the search box:

| You type | Meaning |
|---|---|
| `"exact phrase"` | These words together, in this order |
| cat dog | Files containing both (usually) |
| cat OR dog | Either one |
| NOT draft | Must not contain "draft" |
| repor | Finds "report", "reports"… (word-starts-with matching) |

## Searching by date

Next to the search box there's a **Time range** picker:
*Any time, Last hour, Last 24 hours, Last 7 days, Today, Yesterday, Last 30 days,
Last 90 days, This year,* or **Custom range** (pick two dates).
It filters by the file's **last-modified date** and applies to every search.

## A little more power (optional)

The search box also accepts "pipelines" — the same filters, written as words
separated by `|` (a vertical line, typed with Shift+\ on most keyboards). You never
*have* to use these, but they answer questions the word box alone can't:

```
search report | where ext==pdf and size>1mb | sort -size | head 10
```
Read it left to right: *find "report" → keep PDFs bigger than 1 MB → sort biggest
first → show 10.* You can combine word search with dates and sizes:

```
where modified>7d | where name contains "invoice"
```
(files modified more than 7 days ago whose name includes "invoice")

Handy pieces:

| Piece | What it does |
|---|---|
| `where ext==pdf` | only PDFs (also `size>500kb`, `size<10mb`) |
| `where modified>7d` / `created>30d` | older than 7 days / 30 days |
| `where modified>2025-01-15` | modified since that date |
| `where name contains "screenshot"` | name includes text |
| `where category==image` | by kind: document, image, video, audio, archive, code, data |
| `sort -size` / `sort modified` | biggest / most recent first |
| `head 20` | keep only the first 20 |
| `fields name, size_human` | show only those columns |
| `stats count by ext` | a small report: how many files of each type |

## The other result tabs

Next to **Events** there are three more tabs for the *same* results:

- **Statistics** — totals plus bar charts ("count by ext", "count by category").
- **Visualization** — bar/pie charts and a "size vs. date" scatter of the matches.
- **⏰ Scheduled** — see guide 6.

## Saving your place

- **⬇️ CSV** button: download the current results as a spreadsheet file.
- **🗑 Remove from results**: take a file out of the list you're looking at
  (this does not touch the real file).
- **📜 Recent searches** (bottom of the page): click one to run it again.
- The search box can be made taller with the **▾ / ▴** buttons (for long searches).

Next: [4. Cleaning Up Duplicates →](04-Cleaning-Up-Duplicates.md)
