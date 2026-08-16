# Canvas -> Notion Assignment Sync

Pulls your Canvas assignments and keeps a Notion database in sync, one-way
(Canvas -> Notion). Re-running it is safe — it updates existing rows instead
of duplicating them. Also auto-links every assignment to a per-course page,
so you get a course-level view with a live progress bar.

## 1. Get a Canvas API token

1. Log into Canvas -> click your profile picture -> **Settings**
2. Scroll to **Approved Integrations** -> **New Access Token**
3. Purpose: "Notion sync" (or anything). Leave expiry blank, or set it far out.
4. Copy the token immediately — Canvas only shows it once.

## 2. Get your Canvas base URL

It's whatever's before `/courses/...` when you're logged into Canvas —
something like `https://yourschool.instructure.com`.

## 3. Create the Assignments database

In Notion, create a new **database** (table view is easiest) with exactly
these properties:

| Property name | Type       | Notes |
|----------------|-----------|-------|
| Name           | Title (default) | |
| Course         | Select    | plain text tag |
| Due            | Date      | |
| Done           | Checkbox  | true once Submitted or Graded |
| Submission     | Status    | options: `Unsubmitted`, `Submitted`, `Graded` |
| Grade          | Number, format **Percent** | only set once actually graded |
| CanvasID       | Text      | used for dedup, don't rename |
| Link           | URL       | |
| Course Link    | Relation -> Courses database | see step 4; auto-filled by the script |

`CanvasID` is what the script uses to detect "have I already created this
row" — don't skip it, and don't rename it.

`Done` and `Submission` carry overlapping info on purpose — checkbox for the
per-course progress bar, status for scanning what's outstanding at a glance.
Both get overwritten every run based on what Canvas reports; see the notes
below for why, and for the one exception (paper assignments).

## 4. Create the Courses database (optional, but recommended)

Create a second, separate database called "Courses" with just a `Name`
(title) property — the script creates one page per course automatically the
first time it sees that course, so you don't need to add rows by hand.

On the **Assignments** database, add the `Course Link` relation property
(from the table above) pointing at this Courses database, and turn on
**two-way relation**. Notion will auto-create the reverse property on
Courses — name it something like `Course Assignments`.

**For the progress bar:** on the Courses database, add a Rollup property —
Relation: `Course Assignments`, Property: `Done`, Calculate: **Percent
checked**. Format it as Percent, and set "Show as" to **Bar**. Sort the
Courses view by that property, ascending, to surface whatever course needs
the most attention first.

If you'd rather see assignments filtered *inside* each course page instead
of just a linked list, Notion supports a self-referencing database template
for this — ask if you want to set that up.

## 5. Create a Notion integration

1. Go to https://www.notion.so/my-integrations -> **New integration**
2. Name it "Canvas Sync", associated workspace = yours
3. Copy the **Internal Integration Secret** — this is `NOTION_TOKEN`

## 6. Share both databases with the integration

For **each** database (Assignments and Courses, if you're using it): open it
-> **···** menu (top right) -> **Connections** -> add "Canvas Sync". Skip
this on either one and every API call to it will 404, even with a valid
token and correct ID.

## 7. Get the database IDs

Open a database as a full page, look at the URL:

```
https://www.notion.so/yourworkspace/<DATABASE_ID>?v=...
```

`DATABASE_ID` is a 32-character string (with or without dashes). You need
this for both `NOTION_DATABASE_ID` (Assignments) and, if using it,
`NOTION_COURSES_DATABASE_ID` (Courses).

## 8. Install and run

```bash
cd canvas-notion-sync
pip install -r requirements.txt --break-system-packages   # or use a venv
cp envExample.txt .env.private
# fill in .env.private with your real values
python sync.py
```

`.env.private` only matters if you're running locally or via cron (Option A
below). If you're going straight to GitHub Actions (Option B), skip this —
Actions gets its values from repo secrets instead, not this file.

You should see it print how many rows it created/updated, and the rows
should show up in Notion within a few seconds.

## 9. Put it on a schedule

**Option A — cron (if you leave a machine running):**

```bash
crontab -e
# run every hour
0 * * * * cd /path/to/canvas-notion-sync && /usr/bin/python3 sync.py >> sync.log 2>&1
```

**Option B — GitHub Actions (works even with your laptop closed):**

Push this folder to a repo, add `CANVAS_BASE_URL`, `CANVAS_TOKEN`,
`NOTION_TOKEN`, `NOTION_DATABASE_ID` as repo secrets (Settings -> Secrets and
variables -> Actions). `NOTION_COURSES_DATABASE_ID` and `IGNORED_COURSE` are
optional — add them too if you're using those features. Then add
`.github/workflows/sync.yml`:

```yaml
name: Canvas Notion Sync
on:
  schedule:
    - cron: "0 * * * *"   # every hour, UTC
  workflow_dispatch: {}    # lets you trigger it manually from the Actions tab
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: python sync.py
        env:
          CANVAS_BASE_URL: ${{ secrets.CANVAS_BASE_URL }}
          CANVAS_TOKEN: ${{ secrets.CANVAS_TOKEN }}
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          NOTION_DATABASE_ID: ${{ secrets.NOTION_DATABASE_ID }}
          NOTION_COURSES_DATABASE_ID: ${{ secrets.NOTION_COURSES_DATABASE_ID }}
          IGNORED_COURSE: ${{ secrets.IGNORED_COURSE }}
```

GitHub Actions is free and unmetered on **public** repos, so hourly (or more
frequent) is fine with no cost. Private repos get 2,000 free minutes/month,
which an hourly run easily stays under too.

## Notes / things to know

- **`Done` and `Submission` are overwritten every run for most assignments**,
  reflecting exactly what Canvas reports rather than a manual promise. If
  you check `Done` yourself between syncs but Canvas doesn't show it
  submitted yet, the next run flips it back. That's intentional — it stops
  the checkbox from lying to you about work that's "done" but not actually
  turned in.
- **Exception: paper assignments.** Canvas has no way to ever know about a
  paper submission, so assignments whose `submission_types` includes
  `on_paper` are left alone once Canvas shows them unsubmitted — check them
  off by hand, and the script won't fight you on it.
- **`Grade` is only set once an assignment is actually graded**, stored as a
  decimal fraction (0.9 = 90%) to match Notion's percent format.
- **Sync is one-way, Canvas -> Notion.** Nothing in Notion ever writes back
  to Canvas.
- **Only assignments with a due date are pulled**, and only ones due on or
  after `SCHOOL_YEAR_START` in `sync.py` — change that constant directly in
  the file for a new school year.
- **`IGNORED_COURSE` in `.env.private`** skips whole courses by name
  (comma-separated) — useful for cohort/orientation shells that show up as
  courses but aren't real classes.
- **Non-Canvas / paper-only assignments never entered in Canvas** still need
  to be added to the Notion database by hand — this script only covers
  whatever Canvas itself knows about.

## Notes on Notion API versioning

This script uses Notion API version `2026-03-11`. Since late 2025, Notion
splits a "database" (the container) from its "data source" (the actual
table of rows) — the script automatically looks up each database's data
source ID on first run and caches it, so you don't need to do anything extra
beyond providing the database IDs as usual.

## Notion sample
[Samples (with images)](images.md)