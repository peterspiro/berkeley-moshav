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

- **Tabs**: "Circle" ("+ New meeting" in a "Create a new meeting" section
  under the title; About this circle: a link to its aims and domain
  document, parent and sub-circles (each linked to its Drive folder), Zoom
  room, email address, Drive folder, policies folder, and other key links,
  filled in when the doc is built (and, for a preview in a circle's
  folder, by `previewTemplate`, without parent and sub-circles); no lines
  between sections; and Roles: Leader,
  Feedback Link, Facilitator, and Secretary, with names and when each term
  ends); "Backlog" (built with the
  Docs API alone, since DocumentApp can't see a tab added in the same run:
  "Requested for prioritization", with Policy item requests and Task
  requests, and "Prioritized backlog", with Discussion underway,
  Prioritized for initial discussion, Assigned tasks, and Tasks to assign,
  each a list, as in the Technology Circle's own agenda doc);
  then one tab per meeting, newest first. The template has no meetings.
- **New meeting**: "+ New meeting" adds a meeting in a tab of its own,
  titled with its date, after the Circle and Backlog tabs. Its date, start time, and total duration come from
  the next event on the circle's calendar that isn't in the doc yet, and can
  then be edited. Its Place is the circle's Zoom room, its Minutes a link to
  the previous meeting's tab, and its Next meeting the following calendar
  event.
  The previous meeting's Action Items Recap, a bulleted list, becomes its
  Action Items Accountability's checklist (each item's text only). Adding
  it removes the links from the meetings that are over, which are not to
  be changed.
- **Layout**: each meeting's tab has its date; a line with its start time,
  length, and place; and its attendance; then the agenda table.
- **Columns**: Time (12-hour, without AM/PM, for room), Min (the
  duration), Description, and Notes, each a fixed width; Notes is wide
  (300 points), and in a pageless doc a table wider than the window
  scrolls sideways.
- **Lists**: bulleted and numbered lists have a 0.08" left indent and a
  0.19" hanging indent.
- **Agenda**: Opening Round; Administrative matters (Announcements, Brief
  Reports, Next meeting date & time, Minutes of last meeting, and
  modifications and consent to the agenda, as a bulleted list; 5 minutes);
  Action Items Accountability; one 5-minute Topic (a Presenter dropdown,
  defaulting to "Presenter", of the attendees, then a bulleted list);
  Action Items Recap; Closing Round, whose duration is red when there's no
  time left. Notes start with a bulleted list, but for the opening round.
- **Tab titles**: a meeting's tab is titled with its date. Docs can't run
  a script when someone edits a date chip, so "↻ Update times" also
  retitles the tab when its date chip has been changed.
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
  Group, by first name (with their last initial if another member of the
  community has the same first name), from the Gather directory (a file
  that `update_groups_in_google_and_hierarchy.py` writes to `admin@`'s
  Drive; Google often gives only the start of the address), closely
  spaced, in columns of up to four, beside the meeting's Facilitator and
  Secretary (filled in from the Circle tab's roles, if they're there) and
  Guests. The closing round's Notes have a bulleted line for each of them,
  their name and a colon, for their feedback.
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
  sub-circles from the Circle Structure page. The Roles table has just the
  circle roles.
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

Each circle and working group (not clubs or everybody groups) gets an
agenda doc, built from code like the template rather than copied from it
(a copy's links would still point at the template):

- `update_groups_in_google_and_hierarchy.py --agenda-docs` and
  `add_new_group.py --agenda-doc` (opt-in until the layout is settled)
  create "<Group> Agenda",
  empty, in the group's Drive item, labelled with Drive file properties
  (hidden from users): the group's address, its parent's name in the
  Circle Structure (the Gather wiki page listing every circle, under its
  parent), and the state "new" (`util/circle_agenda_docs.py`). The label
  is also how a group's doc is found again, so none gets a second.
- The `circle_agendas` Apps Script's `buildNewCircleDocs`, on a timer
  every 10 minutes, builds each doc labelled "new", fills in About this
  circle (the group's name as the title, its email address, its Drive
  folder, and its parent), and relabels it "built", with the layout
  version it was built with (`circle_agendas/circle_docs.gs`). It builds
  only a doc that's still empty (one someone has written in is labelled
  "not empty" and left alone), and labels it "building" first, so a build
  that fails partway isn't repeated on top of itself. Nothing ever changes
  an existing doc's contents but the web app's links and update scripts.
- So no "Create doc" link is needed; making a copy of the template by
  hand isn't supported (its links point at the template).
- Still to do: each group calendar event links its group's agenda doc
  (the `group_meetings` Apps Script would add the link, finding the doc by
  its label), rather than the group's footer; and Sub-Circles in About
  this circle.

## Updating once circles have docs

- **The web app's behaviour** (times, new items, new meetings) changes for
  every doc at once when a new version is deployed: always update the
  existing deployment, never make a new one, whose new address would break
  every link. The code must keep working with docs built by older
  layouts, at least for their newest meetings (the ones with links); each
  doc's layout version is in its label.
- **What a new meeting looks like** applies from each doc's next new
  meeting, since the web app builds meetings. Past meetings keep their
  layout, as a record.
- **The rest of a doc** (the Circle and Backlog tabs) changes in existing
  docs only by an update script: numbered changes, each a small function
  that edits a doc in place (keeping the circle's own content), applied to
  every built doc that hasn't had it (found by their labels), recording
  which it has had; tried on a copy first, then run on all. Each change
  there means changing both the build code and writing an update, so the
  Circle and Backlog tabs are best settled before circles use their docs.

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
