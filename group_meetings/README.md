# Group Meetings

Apps Script that looks after every group's Google calendar (the calendars
`update_groups.py` creates):

- **Every 5 minutes**, for each changed event on a group calendar:
  - an event the group calendar organizes gets the group's Zoom room (link in
    the location; link, meeting ID, passcode, and which Zoom user owns the
    room (its number, `1` for `circle-zoom-hosts@berkeleymoshav.org`, `2` for
    `berkeley@urbanmoshav.org`, or `3` for `community-zoom-hosts@berkeleymoshav.org`
    (the community calendars' rooms), and its name in Zoom, so anyone looking at
    the all-events calendar can tell which meetings can't overlap) in the
    description, with a
    line linking the "BM Meetings FAQ" just above them, unless the
    description already links it; a "Zoom link to follow" line in the
    description is removed), and the group's Google Group and the "All
    Berkeley Moshav Events" calendar as guests. The group's members get an
    invitation; adding the room details or the all-events calendar sends no
    email. Events marked `[no zoom]`, or with another Zoom or Meet link typed
    in, keep their own link and get no FAQ line.
  - and, if the group has a circle agenda doc (see `../circle_agendas/`),
    an "Agenda and minutes:" line linking it, just above the FAQ line (or
    at the end, for an event without Zoom details), unless the description
    already links the doc; if the group's doc is replaced, the line is
    relinked to the new one rather than repeated. If the doc is trashed or
    deleted (and the group has no other), the link and its label are removed
    from the line, and the line too if nothing else is on it. The doc is found by the label the Python scripts
    give it in Drive, which names one of the group's addresses; the groups
    are reread hourly, so a group's new or removed doc reaches its upcoming
    events within the hour.
  - an event that's only a copy of someone's invitation (made on their own
    calendar, with the group calendar invited) can't be changed that way, so
    its organizer and `calendaring-alerts@berkeleymoshav.org` get one email
    asking them to create it on the group calendar instead.
- **Every 5 minutes**, each group's meetings get their **reminder email**,
  sent to the group's Google Group (replies go to the group), with the
  meeting's time, Zoom link and passcode (or other location), agenda doc
  link, and a link to the event:
  - by default, 24 hours before;
  - a group with a circle agenda doc sets its own time in the **Remind
    before meetings** row of About this circle (see `../circle_agendas/`): any
    length of time ("24 hours", "2 days", "90 minutes", "1h30m"), several
    separated by commas or "and" ("2 days, 1 hour"), or "none" for no
    reminders (an empty cell means the default). The row's value cell is
    reread hourly. Up to two weeks before;
  - one meeting can have its own, in its title or description:
    `[remind 2 hours]`, `[remind 1 day, 1 hour]`, or `[no reminder]`.
    This wins over the group's setting.

  A setting that can't be read is passed over (a tag for the group's
  setting, the group's setting for 24 hours), and the reminder says so. Only
  timed events the group calendar organizes get reminders, not all-day
  events or invitation copies. Each reminder is sent once, up to an hour
  late if runs were missed, and not at all if the meeting was created after
  its reminder was due (its invitation has just gone out). A meeting moved
  to another time gets its reminders again.
- **Every 5 minutes**, for each changed event on the other calendars
  `admin@berkeleymoshav.org` owns (its primary, which is the shared community
  calendar, and any others, but not the all-events calendar; the list is
  re-read hourly): an event the calendar organizes gets the all-events
  calendar as a guest, sending no email. It gets no Zoom room and isn't
  checked for overlaps, and invitation copies there (such as the
  invitations people send the community calendar) are left alone.
- **Every hour**, and after any change, it looks for meetings in the next 8
  weeks in Zoom rooms belonging to the same Zoom user (a Zoom user can run
  only one meeting at a time) that overlap, or that are less than 15 minutes
  apart (in case the first runs over), including meetings scheduled
  directly in Zoom by any user in the account. It emails the groups involved,
  once per conflict, listing their options: moving one meeting to another
  time (with a link to the all-events calendar for finding a free time),
  holding one on a personal or work Zoom account instead, and, unless both
  meetings are one group's or neither is a group's, asking the Technology
  Circle to move a group's Zoom room to the second Zoom user. The email has an HTML version, so it isn't broken into
  short lines. `calendaring-alerts@`
  (the Technology Circle) is copied, or, when one of the meetings was
  scheduled directly in Zoom, asked to agree too (or, when the other is the
  Technology Circle's own meeting, greeted once and asked to move one).

An event on a group calendar marked **`[no invite]`** (in its title or
description) doesn't get the group as a guest, nor a reminder unless it also
has a reminder tag. A group already invited stays invited.

**Community calendars.** A community calendar's admin group (made by
`add_new_group.py -t a`) has a footer that also names the calendar's
audience (`Calendar audience: all.members@berkeleymoshav.org`). Its calendar
is handled like a group's, except that:
- its events invite the audience, not the admin group, and get no FAQ line;
- their reminders go to the audience ("Hi everyone"), with replies going to
  the admin group;
- in clash emails, its meetings are named by their calendar, the admin
  group is greeted ("Community Meetings admins"), and there's no option to
  move a room to the second Zoom user. Its room is under
  `community-zoom-hosts@berkeleymoshav.org` (Zoom user 3), so it clashes
  only with other community calendars' meetings, or ones scheduled directly
  in Zoom under that user;
- an invitation copy's email speaks of the calendar, not a group.

A community calendar's events also invite the addresses on its footer's
`Also invite:` line, if any, which get the invitation (and updates) but
not reminders; `[no invite]` leaves them out too. When that line changes,
the calendar's upcoming events are rechecked within the hour, and get the
new addresses.

A community calendar made without a Zoom room (its footer says
`Zoom meeting: none`) has no room link, so its events get only the audience
and the all-events calendar as guests, and their reminders.

The calendar's name is the admin group's name without "Admins". The
audience must let `admin@berkeleymoshav.org` post, or reminders bounce.

Which calendar and room are a group's comes from the group's email footer,
read hourly, so new groups need no redeploy. When a group's room link changes
(say, after `reset_zoom_room.py`), or its room moves to the other Zoom user,
or that user is renamed in Zoom, its upcoming events are updated within the
hour.

## Recording links

**Every hour**, each Zoom cloud recording of the last 7 days (in any Zoom
user's account) whose files have all finished processing gets its share
link and passcode put where members look. (Zoom usually finishes within an
hour or two of a meeting ending, so a link appears an hour or so after
that.) Each is handled once:

- **Its calendar event**: a `Recording: <link> (passcode …)` line at the
  top of the description of the one occurrence it came from (the series'
  others are untouched), sending no email. The event is the one whose
  location or description links the recording's Zoom meeting, and which
  starts at most 45 minutes after the recording does and hadn't ended by
  then: on the group's calendar, for a recording in a group's room; else on
  the all-events calendar or another calendar `admin@` owns. Only an event
  one of the account's calendars organizes can be changed. The all-events
  calendar shows the line too.
- **Its agenda doc**, for a group with one: the same line, linked, below
  the "Time …" line of the meeting's tab (the one titled with its date). A
  meeting with no tab yet is tried again every hour for 14 days. A group
  with more than one meeting that day gets none, since the tabs can't be
  told apart.
- **A sheet**: a row (when, title, link, passcode, length, Zoom topic, and,
  hidden, Zoom's ID for the recording, so none is listed twice), in
  - the group's **"<Group> Meeting Recordings"**, made in its Drive item
    (from its footer) when it first has a recording, so it's shared like
    the rest of the group's files; or
  - **"Community Meeting Recordings"**, made in the Shared Drive's Meeting
    Admin folder, which gives `all.members@` view access, for community-wide
    meetings.

  The sheets are found by a Drive property, so they can be renamed or
  moved.

Which sheet is the first of these that decides (`categorizeRecording` in
`recording_rules.gs`):

1. Held in a group's room: that group's. In a community calendar's room:
   the community sheet.
2. Its calendar event invites an audience (`all.members@`): the community
   sheet. Or it invites exactly one group (its email list, an alias, or its
   calendar): that group's.
3. Its title (the event's, then the Zoom topic), ignoring case and extra
   spaces, is the title of a recurring meeting on a group's calendar (that
   group's) or a community calendar (the community sheet). If it's on
   calendars leading to different sheets, the recording is uncategorized.
4. Its title holds a group's name as whole words (for a community
   calendar's admin group, the calendar's name: the community sheet). The
   longest name wins; a tie decides nothing.
5. Its title holds "listening session" or "ask me anything", in any case:
   the community sheet.

A recording none of these decide goes on the community sheet's
**Unassigned** tab, and `calendaring-alerts@` gets an email about it. Type a
group's name or address (or `community`) in its row's **Group** column: the
next hourly run moves the row to that sheet, and remembers the choice, so
the backfill puts it there too. A name it doesn't recognize is noted in
the row's Why column.

**Backfill.** To list older recordings (sheets only: events and agenda docs
aren't changed), run **`backfillRecordingsDryRun`** from the editor first:
it logs where each recording would go, and a count per sheet, changing
nothing. Then run **`backfillRecordings`**. Each steps month by month,
from January 2020 (`BACKFILL_FROM` in `recordings.gs`), through every Zoom
user's recordings, stopping after about 4½ minutes (Apps Script stops a
run at 6), or at a month Zoom won't list (it logs why); run it again until
it logs that it's done. At the end of each
run, `calendaring-alerts@` gets one email listing the recordings it put on
the Unassigned tab. Running it again lists nothing twice;
**`resetRecordingsBackfill`** makes the next run start from the beginning.
A backfill run holds the lock the triggers use, so they skip while it
runs. Recordings made on personal or work Zoom accounts can't be reached.

**Zoom settings.** Recordings must be shareable: in the Zoom web portal's
account settings, under Recording, turn on cloud recording sharing, and
leave "only authenticated users can view" off (members mostly have no
account in ours). Recordings made before sharing was turned on may need it
turned on one by one (Recordings, then Share). A recording with no share
link is logged, and listed without one.

- `group_meetings.gs` — triggers, Calendar/Groups/Zoom calls, emails
- `event_rules.gs` — the rules themselves, with no Apps Script calls, tested
  under Node by `tests/test_group_meetings_rules.py`
- `recordings.gs` — the recording links: Zoom's recordings, the events,
  agenda docs and sheets they're added to, and the backfill
- `recording_rules.gs` — the recording links' rules, with no Apps Script
  calls, tested under Node by `tests/test_recording_rules.py`
- `appsscript.json` — advanced services and OAuth scopes

## Deploy with clasp

**Everything runs as `admin@berkeleymoshav.org`**, which owns the group
calendars. Sign in as that account for every step.

```bash
npm install -g @google/clasp
clasp login                     # as admin@berkeleymoshav.org
cd group_meetings
clasp create --type standalone --title "Group Meetings"
git checkout -- appsscript.json # clasp create overwrites it with a bare default
clasp push -f
clasp open
```

### Zoom credentials

The script reads Zoom's meetings with the same Server-to-Server app as the
Python scripts. In the editor, open **Project Settings → Script Properties**
and add the three values from the repo's `zoom_credentials` file:

| Property | Value |
|---|---|
| `ZOOM_ACCOUNT_ID` | `account_id` |
| `ZOOM_CLIENT_ID` | `client_id` |
| `ZOOM_CLIENT_SECRET` | `client_secret` |

The Zoom app also needs the `user:read:list_users:admin` ("View users") scope
for this, to find every Zoom user's scheduled meetings, and, for the
recording links, `cloud_recording:read:list_user_recordings:admin` and
`cloud_recording:read:list_recording_files:admin` (see the main README.md).
Until it has those, the hourly run logs an error for the recordings and
carries on with everything else.

### One-time authorization and triggers

In the editor, select **`installTriggers`** and click **Run**, then approve
the authorization prompt. It reads the footers and Zoom's meetings, handles
every upcoming event once (so existing events get the room too), and installs
two triggers: `syncAll` every 5 minutes and `hourly` every hour. Check them
under **Triggers** (⏰).

### Updating later

```bash
clasp push
```

The triggers stay installed across pushes. When a change alters what events
get (it bumps `RULES_VERSION` in `group_meetings.gs`), the next run rechecks
every upcoming event once, sending no email. The first run that finds a
calendar (a new group's, or another calendar `admin@` owns) likewise handles
all of its upcoming events, so the community calendar's existing events get
the all-events calendar too.

When a change adds a permission to `appsscript.json` (reading Drive, for
the agenda docs' links, did, and so did reading the agenda docs, for their
Remind before meetings row, and so did the recording links, which create
sheets, edit them, and edit agenda docs), run any function in the editor after the
push, such as `hourly`, and allow it: the triggers run with the
permissions allowed so far, and fail without the new one.

## Smoke test

1. On a test group's calendar (one whose footer has calendar and Zoom links),
   create an event. Within 5 minutes it should show the Zoom link as its
   location, the FAQ line and the Zoom details in its description, and the
   group and the all-events calendar as guests; the group's members should
   get an invitation.
2. Create an event on your own calendar and invite the test group's calendar:
   you and `calendaring-alerts@` should get one email about it.
3. As `admin@`, create an event on the community calendar (admin's primary):
   within 5 minutes it should have the all-events calendar as a guest, and
   nothing else should change.
4. Create two overlapping events on group calendars: `calendaring-alerts@` and
   both groups should get one overlap email. Then move one so they're 10
   minutes apart: one "too close together" email. At 15 minutes apart or
   more: no email.
5. On a test group's calendar, create an event starting in 24 hours and 10
   minutes. Within 15 minutes the group should get one reminder email. Put
   `[remind 2 hours]` in an event's description, or set the group's
   agenda doc's Remind before meetings row to "none" (then wait for `hourly`, or
   run it), to check those.

6. Record a short meeting in a test group's room, with its event on the
   group's calendar. An hour or so after it ends, the event should have a
   "Recording:" line, the group's agenda doc (if it has a tab for that day)
   too, and the group's "Meeting Recordings" sheet a row.

Run **`syncAll`** or **`hourly`** from the editor to force a pass, and read
**Executions** for the log. A run that changed and sent nothing logs
nothing ("No logs are available for this execution").

To see why a reminder was or wasn't sent, run **`explainReminders`** from
the editor: for each group's meetings in the next 3 days, it logs the
setting it used (from the event's tag, the agenda doc, or the default),
when each reminder is due, and whether it's upcoming, due, sent, missed
(due over an hour ago), or skipped because the event was created after it
was due, plus the events passed over (all-day, or invitation copies) and
any tag or setting it couldn't read. It sends nothing. To look at one
group only, call it from another function, as
`explainReminders('tech')` (part of the group's address or name).
