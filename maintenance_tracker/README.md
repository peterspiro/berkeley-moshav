# The maintenance tracker

The Maintenance & Warranty Circle's tracker for the building: repair
requests, warranty claims, contacts, equipment, and maintenance schedules.
It's an Airtable base, "Building Maintenance", with an Apps Script that
does what happens to it without anyone doing it. Everything about it is
in this folder, except four scripts in the repo root.

| Where | What |
|---|---|
| `maintenance-tracker-plan.md` | The design, kept for its reasoning, and what's built so far |
| `maintenance-tracker-guide.md` | The guide to using the tracker, for the circle's members |
| `base/` | The base as files (`schema.json`, `seed.json`), and in its `README.md` how to change the base, set one up, and use it |
| `apps_script/` | The Apps Script project that acknowledges and follows up requests, emails the circle, and backs the base up; its `README.md` says how to deploy it |
| `pages/` | The sources of the two pages that hold the tracker's links: `maintenance-tracker-members-page.md` (a Gather wiki page) and `maintenance-tracker-start-here.md` (a Google Doc in the circle's Drive folder, written by `../update_start_here_doc.py`) |
| `../maintenance_base.py` | Makes a base match `base/schema.json` and `base/seed.json` |
| `../sync_maintenance_people.py` | Copies the members in from Gather to the base's People table |
| `../update_start_here_doc.py` | Writes the circle's "start here" Google Doc from `pages/maintenance-tracker-start-here.md`, with its links filled in from the git-ignored `maintenance_links` |
| `../maintenance_demo.py` | Replaces the test base's rows, except Locations and People, with demo data for showing the tracker to the circle |

The shared Python code is in `../util/` (`airtable.py`, `airtable_schema.py`,
`maintenance_people.py`), and the tests are in `../tests/`.

`apps_script/` is the only folder `clasp` pushes from. Keep other files out
of it: its `.clasp.json` pushes every `.json` file under it, subfolders
included.
