# Plan: Circle agenda docs

A Google Doc for each circle holding its meetings' agendas, based loosely on
Sociocracy For All's "Session Circle Agenda" template (a starting point
only; the design is expected to diverge from it), with scripts doing the
arithmetic and the setting up. So far there's the template, built from a
blank doc by `circle_agendas/build_template.gs`; the web app
(`circle_agendas/web_app.gs`), whose links add the circle's next meeting
from its calendar, and, in each meeting, add an agenda item and update the
times (`circle_agendas/meetings.gs`, with `circle_agendas/circles.gs`
finding the circle); the time arithmetic in
`circle_agendas/agenda_rules.gs`; and the tests in
`circle_agendas/agenda_probe.gs`.

A new meeting is the first event on the circle's calendar after the day of
the newest meeting in the doc (or after now, if there's none), so no
meeting is added twice, and clicking again adds the one after.

Next: making circle docs (by `add_new_group.py`, a backfill for existing
circles, and handling copies made by hand), filling in About this circle;
and the template changes still to come.

## The template is built from code

The template is built from a blank doc entirely by
`circle_agendas/build_template.gs`, not copied from another doc and edited,
so it can be rebuilt from scratch at any time and owes nothing to the doc
it was modelled on. Every change to the template is a change to that file;
`buildTemplate` rebuilds it in place, keeping its ID and link. Circle docs
already copied from the template don't change when it's rebuilt; changes
they need will take their own scripts.

## What a circle's doc does

- **Tabs**: "Circle" (About this circle, Members and roles, Other
  policies, with "+ New meeting" in a "Create a new meeting" section under
  the title); "Backlog" (built with the
  Docs API alone, since DocumentApp can't see a tab added in the same run);
  then one tab per meeting, newest first. The template has no meetings.
- **New meeting**: "+ New meeting" adds a meeting in a tab of its own,
  titled with its date, after the Circle and Backlog tabs. Its date, start time, and total duration come from
  the next event on the circle's calendar that isn't in the doc yet, and can
  then be edited. Its Place is the circle's Zoom room, its Minutes a link to
  the previous meeting's tab, and its Next meeting the following calendar
  event.
  The previous meeting's Action Items Recap, a bulleted list, becomes its
  Action Items Accountability's checklist (each item's text only). Adding
  it removes the links from the meetings before it, which are then past
  and not to be changed.
- **Layout**: each meeting's tab has its date; a line with its start time,
  length, and place; and its attendance; then the agenda table.
- **Columns**: Time (12-hour, without AM/PM, for room), Min. (the
  duration), Description, and Notes; the first two narrow, the others
  sharing the rest of the page.
- **Agenda**: Opening Round; Administrative matters (Announcements, Brief
  Reports, Next meeting date & time, Minutes of last meeting, and
  modifications and consent to the agenda, as a bulleted list; 5 minutes);
  Action Items Accountability; one 5-minute Topic; Action Items Recap;
  Closing Round, whose duration is red when there's no time left. Notes
  start empty.
- **New agenda item**: a link in each meeting adds a topic after its last
  topic (before Action Items Recap), with a duration of 5 minutes.
- **Meeting length**: after "Duration (hours)" in the line above the
  table, as 1.5 or 1:30.
- **Times**: every item's duration can be edited except the closing
  round's, which is the meeting's end minus the time the closing round
  starts. Each item's time, and the final "end" row, are calculated from the
  start time and the durations before it. Docs can't lock cells, so the
  calculated cells are shaded and overwritten whenever times are
  recalculated (when someone clicks a link, and possibly on a timer).
  A duration that can't be read as a number is flagged, not guessed.
- **Attendance**: a checklist of the circle's members, from its Google
  Group, named as in the Gather directory (a file that
  `update_groups_in_google_and_hierarchy.py` writes to `admin@`'s Drive;
  Google often gives only the start of the address), or else as Drive gives
  them, closely spaced.
  Names start unchecked; checking one marks that member absent, and Docs
  strikes the name through (an instruction above the names says so; Docs
  strikes through only checked checklist items, or text formatted by hand).
  Below it, "Guests:", for their names.
- **Tabs**: Apps Script can't make a tab, and doesn't see one the Docs API
  made in the same run (`runNewTabProbe`), but does in a later run
  (`runNewTabProbeLater`). So adding a meeting takes two runs: the link's
  run adds the tab, and the page it returns runs the second, which builds
  the meeting in it. Each tab has its own dropdowns' choices. Docs' Find
  searches all tabs.
- **About this circle** is filled in when the doc is created: its email
  address and Drive folder from the group's footer, and its parent and
  sub-circles from the Circle Structure page. The Members and roles table
  keeps its role rows but has no Member rows.
- The SoFA credit lines, and its page header and footer, are gone.

## How it works

One central Apps Script web app, running as `admin@berkeleymoshav.org` and
open to anyone with a Google account, rather than a script in each doc:
members need not approve an "unverified app" screen for each doc, members
with personal Gmail accounts can use it, and there's one copy of the code
to fix. The doc's links call the web app with the doc's ID (and the
meeting's, for a new item); each opens a tab that says when it's done.

A meeting's ID is in its links' addresses (in their row, just above Action
Items Recap), which is how the web app finds its table wherever it is in
the doc.

A link names its doc by ID, so a doc copied by hand from the template
(File → Make a copy, or a `/copy` link) points at the template until its
links are rewritten. Docs made by the scripts get theirs rewritten as
they're made; for one made by hand, the web app could, when a link names
the template, ask for the copy's address and rewrite its links then. Still
to decide.

The web app works out which circle a doc belongs to from the folder or
shared drive it's in, by finding the Google Group whose footer links that
item (as `groups_drive_sync` does), so a doc needs no other setup. A doc
outside any circle's Drive item gets a message saying so.

The time arithmetic and text parsing go in a separate file with no Apps
Script calls, tested under Node like `group_meetings/event_rules.gs`.

## Creating a circle's doc

- `add_new_group.py` copies the template into the new group's Drive item,
  and `update_groups_in_google_and_hierarchy.py` can do the same for
  existing groups.
- From Drive: a link to the template ending in `/copy` (or File → Make a
  copy) asks where to put the copy; put it in the circle's folder. The first
  use of one of its links fills in About this circle.

## The probes

`circle_agendas/agenda_probe.gs` tests, on a copy of the template, whether
copying a row or a whole meeting keeps its dropdown and date chips and its
checked boxes, whether the Docs API can add an unchecked checklist, person
chips, and date chips, and how checked boxes look to the API. See
`circle_agendas/README.md`.

Results (run 2026-10-31 on a copy of the SoFA-based doc):
- Copying a table row or a whole meeting table with Apps Script's
  `copy()` keeps its dropdown chips, date chips, and checked boxes. Apps
  Script itself sees dropdowns only as `UNSUPPORTED` elements; the Docs API
  sees them as `dropdown` and dates as `dateElement`.
- The Docs API can add an unchecked checklist (`BULLET_CHECKBOX`), a person
  chip (`insertPerson`), and a date chip (`insertDate`).
- Checking a checklist item strikes it through, person chips included, for
  every account. But the API can't tell a checked item from an unchecked one:
  checked boxes aren't readable, so a script can't see who was absent or which
  action items are done.
- Each meeting is one table: its date chip in the first row, "Time" and
  "Place" in the second, then the agenda rows, ending with "end". The
  meeting's total duration is in the admin block's Duration row (in hours).

`runTabProbe` (run 2026-11-01) found:
- The Docs API can add a tab (`addDocumentTab`), but DocumentApp, opened
  again in the same run, didn't see it, so the copy into the tab failed.
- A meeting copied from another doc kept its chips. But that doc was a copy
  of the same template, so its dropdowns had the same definition IDs as
  the doc they were copied into; whether chips survive a copy between
  unrelated docs is still unknown.
- A doc keeps its dropdowns' lists of choices in `dropdownDefinitions`, and
  each chip refers to one by ID.
- Pageless docs hide headers and footers, and show a "some features are
  hidden" notice while a doc has any.

Since then, the Docs API's reference (its discovery document, at
https://docs.googleapis.com/$discovery/rest?version=v1) shows it can create
dropdowns' lists of choices (`createDropdownDefinition`, with IDs of our
choosing) and the chips (`insertDropdown`), and set a doc to pageless
(`documentStyle.documentFormat.documentMode`). So nothing in the doc needs
copying from another doc: new meetings are built by code, in the circle
doc itself, with that doc's own dropdowns. Where a circle's own standing agenda items would live
is still to decide.
