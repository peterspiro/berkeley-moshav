# Plan: Circle agenda docs

A Google Doc for each circle holding its meetings' agendas, based loosely on
Sociocracy For All's "Session Circle Agenda" template (a starting point
only; the design is expected to diverge from it), with scripts doing the
arithmetic and the setting up. Nothing is built yet except a test,
`circle_agendas/agenda_probe.gs`.

## What a circle's doc does

- **New meeting**: a link at the top of MEETINGS adds a meeting above the
  others (newest first). Its date, start time, and total duration come from
  the next event on the circle's calendar that isn't in the doc yet, and can
  then be edited. Its Place is the circle's Zoom room, its Minutes a link to
  the previous meeting, and its Next meeting the following calendar event.
  Unticked items of the previous meeting's Action Items Recap are copied to
  its Action Items Accountability.
- **New agenda item**: a link in each meeting adds a topic after its last
  topic (before Action Items Recap), with a duration of 5 minutes.
- **Times**: every item's duration can be edited except the closing
  round's, which is the meeting's end minus the time the closing round
  starts. Each item's time, and the final "end" row, are calculated from the
  start time and the durations before it. Docs can't lock cells, so the
  calculated cells are shaded and overwritten whenever times are
  recalculated (when someone clicks a link, and possibly on a timer).
  A duration that can't be read as a number is flagged, not guessed.
- **Attendance**: a checklist of the circle's members, from its Google
  Group, with names from the Drive item's permissions. Names start
  unticked; ticking one marks that member absent, and Docs strikes the name
  through.
- **About this circle** is filled in when the doc is created: its email
  address and Drive folder from the group's footer, and its parent and
  sub-circles from the Circle Structure page. The Members and roles table
  keeps its role rows but has no Member rows.
- The SoFA credit lines are removed.

## How it works

One central Apps Script web app, running as `admin@berkeleymoshav.org` and
open to anyone with a Google account, rather than a script in each doc:
members need not approve an "unverified app" screen for each doc, members
with personal Gmail accounts can use it, and there's one copy of the code
to fix. The doc's links call the web app with the doc's ID (and the
meeting's, for a new item); each opens a tab that says when it's done.

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

## First step: the probe

`circle_agendas/agenda_probe.gs` tests, on a copy of the template, whether
copying a row or a whole meeting keeps its dropdown and date chips and its
checklist ticks, whether the Docs API can add an unticked checklist, person
chips, and date chips, and how ticks look to the API. See
`circle_agendas/README.md`.

Depending on the results:
- If copies keep the chips, a new meeting is a copy of a blank meeting kept
  in the doc (or in the template), and a new item a copy of a blank topic
  row.
- If not, the item-type column becomes plain text, or members add the chip
  themselves by typing `@`, and dates are plain text the script parses.
