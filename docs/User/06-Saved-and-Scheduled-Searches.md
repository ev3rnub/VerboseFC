# 6. Saved & Scheduled Searches

A **saved search** is a search you keep so you can run it again without retyping.
A **scheduled search** additionally runs by itself on a timer, and its results land
in an inbox.

## Saving a search

1. Run any search you like in the **🔍 Search** tab.
2. Open the **⏰ Scheduled** sub-tab (next to Events / Statistics / Visualization).
3. Type a **Name this search**, adjust the **Query (edit before saving)** box if
   needed, and press **💾 Save**. The query is saved together with the time range
   you used (e.g. "Last 7 days").

## Running a saved search

In the saved list, open the search's box and press **▶ Run now** — the search tab
fills its query box and runs it. **🗑 Delete** removes it.

## Scheduling it

In the same box there is a **Schedule** dropdown:

*Off, Every 5 min, Every 15 min, Every 30 min, Hourly, Every 6 hours, Daily.*

While the program is open, scheduled searches run by themselves in the background
and each run is recorded in the **SCHEDULED RUNS** list (when it ran, which search,
how many results, any error). New results get a badge, e.g.
`🔍 Search (3)`, so you notice them.

- **✔ Mark as read** clears the badge.
- The first 5 runs show a **Top matches** box with a few result rows.

> Scheduled searches only fire **while the program is running**. If you want them
> to run even when nobody has the program open, whoever set the program up can put
> one small command (`python src/scheduled_runner.py`) in the system's task
> scheduler. That's optional.

## A note on the 📥 Ingestion tab

The **📥 Ingestion** tab is a more advanced feature for sorting files into named
"indexes" and pulling custom fields out of file contents. Most people don't need
it. If someone set it up, those extra fields simply appear as extra filter options
in search (for example `where errkind==timeout`).

Next: [7. Troubleshooting & FAQ →](07-Troubleshooting-and-FAQ.md)
