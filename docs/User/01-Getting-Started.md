# 1. Getting Started

## What Verbose FC does

Think of Verbose FC as a **very thorough librarian for one folder on your computer**.

1. You point it at a folder (for example `Documents`).
2. It walks through every file inside — including folders inside folders — and makes
   a detailed list: names, sizes, dates, and the *text inside* the files.
3. You can then search that list in seconds: find files containing a word, find
   double copies of the same file, see which folders eat up your disk space, and so on.

It does **not** upload anything anywhere. Everything happens on your computer.

## Starting the program

You (or whoever set it up) start it once, and then use it in a web browser.

1. Open the **Terminal** on a Mac (or **PowerShell** on Windows).
2. Type these two lines, pressing Enter after each one:

   ```
   cd "the folder where Verbose FC lives"
   streamlit run src/verboseFC.py
   ```

3. Your browser opens a page at an address like `http://127.0.0.1:8501`.
   That page **is** the program. Leave the Terminal window open while you work —
   closing it stops the program.

> If it says `streamlit` is not found, the program's setup step was not done yet.
> Run `pip install -r src/requirements.txt` once, then try again.

## The main screen

When the page opens you will see:

- **A settings panel on the left** — this is where you choose *which folder to look
  at* and press the big **Scan & Index Directory** button.
- **Six tabs across the top:**

| Tab | In plain words |
|---|---|
| 📁 File Explorer | The list of everything found, plus a duplicates finder and a folder tree |
| 🔍 Search | Type words and find files — like a search engine for your own folder |
| 📥 Ingestion | (Advanced) how files get sorted into categories — most people skip this |
| 📊 Storage | Charts: what's big, what's common, and a picture of your disk space |
| 🗄️ Database | Health and size report for the program's own record book |
| 👀 Watch | Get alerts when files appear/change in folders you choose |

> The program deliberately starts with an **empty list**. Nothing is looked at until
> you choose a folder and press Scan.

## The one setting that matters most: Base Path

The **Base Path** box in the left panel is *which folder to look inside*.

- Easiest: press **📁 Browse…** and pick the folder in the normal Finder /
  Explorer window that pops up. (The picker opens on the computer running the
  program, not "in the browser" — they're the same computer in normal use.)
- You can also type it, e.g. `~/Documents` on a Mac or `C:\Users\you\Documents`
  on Windows.

## Ready when you are

Next step: [2. Your First Scan →](02-First-Scan.md)
