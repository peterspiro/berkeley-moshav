# Plan: Meeting infrastructure (calendars, Zoom rooms, recordings)

How the community's online meetings are set up, what's built, the decisions
behind it, and what's still to do. README.md describes the scripts as they
are; this note keeps the reasoning and the remaining work.

## Goals

- Members, many with limited Zoom skills, only ever click a link: each group
  has one permanent Zoom room whose link never changes.
- Meetings run with no host present; designated facilitators claim host with
  a host key when they need host controls (recording, muting, etc.).
- Meetings are scheduled on Google calendars, and the room details and
  invitations are added automatically.
- Clashes between meetings that need the same licensed Zoom user are caught
  before they happen.

## Built so far

- **Footer links** (`util/google_group_footer.py`): each group's email footer
  links its Gather group, Drive item, Google calendar, and Zoom room, with the
  room's meeting ID and passcode. The footer is the only record of which
  calendar and room are a group's; there's no separate configuration.
- **Calendars and rooms** (`util/meeting_rooms.py`, step 0d of
  `update_groups.py`, and `add_new_group.py`): every
  circle, working group, and club (not everybody groups) with a Drive item and
  an email list gets a calendar and a room. Existing ones are found through
  the footer, or offered by name, before anything is created. For a room,
  every meeting in the Zoom account whose name matches is listed, and for
  each you choose to use it (only a no-fixed-time meeting of either room
  user, since Zoom can't change a meeting's type), ignore it, or delete
  it; deletions are confirmed by typing `delete`.
- **Moving existing meetings** (`util/calendar_event_moves.py`): when a
  group's calendar is created, or with `update_groups_... --move-events`,
  events in the next 12 months matching the group's name, on any calendar
  `admin@` can see (except group calendars), can be copied to the group's
  calendar (without meeting links; each guest outside the group can be
  invited to the copy, given view access to the calendar, or both), deleted (cancelled for
  everyone, with emails, when organized there), both, or skipped. A
  series' copy starts at its next occurrence. Calendars
  `admin@` can't see would need domain-wide delegation, so their owners
  share them with `admin@` instead.
- **Calendar announcement** (`util/calendar_announcement.py`): when
  `update_groups_...` creates a group's calendar, it emails the group's list
  (from `admin@`, replies to `calendaring-alerts@`) once the footer links the
  calendar and room. Groups made by `add_new_group.py` get its welcome email
  instead. Google's own "shared a calendar" email isn't sent: for a group it
  goes to the group's address and its button does nothing for members.
- **`reset_zoom_room.py`**: a new passcode (and so link) for a leaked room.
- **`group_meetings` Apps Script**: adds the room and guests to group
  calendar events, alerts about invitation-only copies, and warns about
  clashes.

## Decisions

### Accounts
- Calendars are owned by `admin@berkeleymoshav.org`, which also runs the Apps
  Scripts. The Python scripts refuse to work on calendars signed in as anyone
  else, since other admins can't see calendars they don't own.
- New rooms are made under the `circle-zoom-hosts@berkeleymoshav.org` Zoom
  user. The account's second licensed user, `berkeley@urbanmoshav.org`, can take a
  group's room, transferred to it in Zoom, when the group's meetings keep
  clashing with others' (see "Open decisions"). Both are `ROOM_USERS` in
  `util/zoom_utils.py`.
- A third licensed Zoom user, `community-zoom-hosts@berkeleymoshav.org`,
  hosts the community-wide meetings (see "Not built yet", 1). It isn't one
  of `ROOM_USERS`: group rooms aren't moved to it.
- Zoom is reached through a Server-to-Server OAuth app ("Berkeley Moshav Admin
  Scripts"). Its credentials are the project's, not a person's, so they live
  in the repo's git-ignored `zoom_credentials` file (Python) and the Apps
  Script's Script Properties.
- Alerts go to `calendaring-alerts@berkeleymoshav.org`, plus the groups
  involved.

### Rooms
- A room is a meeting with no fixed time (Zoom type 3), titled
  "<Group> Meeting Room": join before host at any time, no waiting room (a
  host key can't be used with one), no automatic recording.
- Passcodes are six digits, easy to type and read out. The link embeds the
  passcode; the footer and event descriptions also show the meeting ID and
  passcode for people who type them. That exposes nothing the link doesn't.
- Zoom deletes a room after 365 days unused; `update_groups_...` warns at 300
  days and replaces an expired room.
- Tested: Zoom never makes an early participant host in these rooms, so a
  facilitator's host key works whenever they arrive.
- The host key (one for all rooms) is changed by hand in Zoom's web portal,
  not by a script, since changing it affects every facilitator.

### Calendars and events
- Each calendar is shared with its group's Google Group as "Make changes to
  events" when it isn't shared with the group at all. Any other access is
  left alone, so a circle can be restricted by hand so only some members edit
  (README.md: "Letting only some members edit a group's calendar").
- Events must be created on the group calendar itself. An invitation from
  someone's own calendar is only a copy, which can't be given the room or
  the group's invitation; its organizer gets an email instead.
- Each event made on a group calendar gets the room (location, and a marked
  block in the description), and the group and the "All Berkeley Moshav
  Events" calendar as guests. Events that get the room also get a line
  linking the "BM Meetings FAQ" Google doc, just above the room's block,
  unless the description already links the doc (if someone deletes the
  line, it's added back the next time the event changes). `[no zoom]`, or another Zoom or Meet link typed
  in, opts out of the room. A Meet link Calendar adds on its own doesn't
  count, and is left in place.
- The all-events calendar is shared view-only with the everybody groups,
  without Google's "shared a calendar" email (the Calendar API can skip it;
  Calendar's own sharing page can't), so anyone can open its link, though it
  isn't added to their calendar lists.

### Clashes
- A Zoom user can run only one meeting at a time (tested: a second room of
  the same user can't start while one is running, even with no host).
- So meetings in rooms of the same Zoom user are flagged if they overlap or
  are less than 15 minutes apart (in case one runs over), over the next 8
  weeks, including meetings scheduled directly in Zoom by any user in the
  account. Meetings of different Zoom users never clash.
- Each clash is emailed once (again if its times change, or if it's resolved
  and recurs), with a link to the all-events calendar for finding a free time,
  replies going to `calendaring-alerts@` (the Technology Circle):
  - two group meetings: the groups are offered two options, cc the
    Technology Circle: ask the Technology Circle to move one group's room to
    the second Zoom user (if their schedules allow), or agree which meeting
    moves (a group clashing with itself is asked to move one);
  - a group meeting and a meeting scheduled directly in Zoom: the group and
    the Technology Circle are asked to agree which moves;
  - two meetings scheduled in Zoom: the Technology Circle is asked to move one.

## Not built yet

### 1. Community-wide calendars
Several calendars for community-wide events (for example "Community
Meetings"), each managed by a small set of admins (a different set for each
calendar). The shared community calendar (`share@`, see
`calendar-notifications-plan.md`) stays separate: it's where members put
their own social events. Agreed design:
- **An admin group per calendar.** Each calendar has a Gather admin group,
  of a Gather group type that no other group uses (see "Why a distinct
  Gather group type" below). Its Drive folder is one level down, like a
  club's: under a top-level "Meeting Admin" folder in the Shared Drive.
- **Calendar.** Each admin group gets one calendar, shared with its Google
  Group as editors ("Make changes to events"). Everyone else sees its events
  through the all-events calendar, which is shared view-only with the
  everybody groups; that's enough, so admin groups get no other read access.
- **Zoom room.** Each admin group gets its own no-fixed-time room (Zoom
  type 3, the standard room settings) under the `community-zoom-hosts@`
  Zoom user, so each calendar's recordings can be told apart by the room's
  name (see 2). A skilled host claims host with the host key for host
  features such as breakout rooms.
- **Recorded in the admin group's footer**, as for other groups: its Gather
  group, Drive folder, "Google calendar" and "Zoom meeting" links. So
  `group_meetings` finds the calendars and rooms the way it finds groups',
  and there's still no separate configuration.
- **Events.** The Apps Script treats their events like group events: the
  admin group's room's details, and the **everybody group** (not the admin
  group) and the all-events calendar invited, but **not** the BM Meetings
  FAQ line, which is for group meetings only.
- **Clashes.** A Zoom user hosts one meeting at a time, so community-wide
  meetings clash with each other (all rooms are under `community-zoom-hosts@`,
  even on different calendars), but no longer with group meetings, which
  are under the other two Zoom users. The existing check (overlapping or
  less than 15 minutes apart, next 8 weeks) covers them once it knows that
  `community-zoom-hosts@`'s rooms belong to admin groups; until then it
  would see them as meetings scheduled directly in Zoom. Clash emails, sent
  like the others, in an overlapping and a "too close together" version:
  - *Two community-wide meetings.* To: both calendars' admin groups (or the
    one, if both are on the same calendar); cc: `calendaring-alerts@`.

    > **Subject:** Zoom meetings overlap: "Community meeting" and
    > "Movie night"
    >
    > Hi Community Meetings admins and Social Events admins,
    >
    > These two community-wide meetings overlap: …
    >
    > All our community-wide meetings use one Zoom user, which can host
    > only one meeting at a time. Please agree which one moves. To find a
    > free time, check the All Berkeley Moshav Events calendar: <link>

  - *A community-wide meeting and one scheduled directly in Zoom under
    `community-zoom-hosts@`.* To: the admin group; cc:
    `calendaring-alerts@`; asking them to agree which moves.

  The "too close together" versions say how many minutes apart the meetings
  are, that the second can't start until the first ends if it runs over, and
  ask for at least 15 minutes between them. `overlapMessage` in
  `group_meetings/event_rules.gs` builds the group cases.

  (The earlier design, with one shared room under `circle-zoom-hosts@`, had
  community-wide meetings clash with group meetings too, and asked the group
  to move; with the separate Zoom user that case goes away.)

#### How admin groups differ from circles
| | Circles and working groups | Admin groups |
|---|---|---|
| Drive folder | Top level of the Shared Drive | Under "Meeting Admin", like clubs under "Clubs" |
| Folder and calendar name | "<Name> Circle" / "<Name> Working Group" | The calendar's name (suffix to decide) |
| Zoom room | Under `circle-zoom-hosts@` (or moved to `berkeley@urbanmoshav.org`) | Under `community-zoom-hosts@`, never moved |
| Who events invite | The group itself | The everybody group, not the admin group |
| BM Meetings FAQ line | Added | Not added |
| Clash emails | Group-to-group wording, offering to move a room to the second Zoom user | Admin-group wording, with no room to move |
| Agenda doc, Circle Structure sheet and doc | Yes | No: not part of the circle structure |
| Reminder before each meeting | To the group | To decide: the admin group, the everybody group, or none |
| Email when its calendar is made | Tells members to add the calendar and move meetings onto it | Different wording: tells the admins how to schedule community-wide events |

#### Why a distinct Gather group type
The scripts decide what a group gets from its Gather type (`kind`), not
from its name or a list: `ELIGIBLE_KINDS` in `update_groups.py` (calendar
and room), `FOLDER_TYPE_SUFFIXES` and `parent_folder_id_for_group_type` in
`util/gdrive_config.py` (folder name and place), `AGENDA_KINDS` in
`util/circle_agenda_docs.py` (agenda docs), and `STRUCTURE_KINDS` in
`util/circle_structure_placements.py` (Circle Structure). A type no other
group uses:
- marks a group as a calendar's admins without a naming convention or a
  hardcoded list, so a new one is set up just by choosing the type in Gather
  (or in `add_new_group.py`);
- keeps admin groups out of what's only for circles and working groups
  (agenda docs, Circle Structure), as the club type already does for clubs;
- gives them their own branches wherever they differ (folder place, Zoom
  user, who events invite, the FAQ line, clash and reminder emails),
  without mistaking a circle or a club for one.
Which of Gather's unused types to use is still to pick.

### 2. Recording archive
Copy Zoom cloud recordings into the matching group's Drive folder, then let
Zoom delete them.
- Match recordings to groups by meeting topic ("<Group> Meeting Room"); a
  community-wide calendar's go to its admin group's folder, by its room.
- Copy each file (video, audio, transcript, chat), recording which recordings
  are done so none is copied twice; optionally delete from Zoom after a
  verified upload and a grace period.
- Build it as a scheduled Python job reusing `util/zoom_utils.py`, not in Apps
  Script, whose download size and run-time limits are too small for videos.
  RecordFlow (single destination folder only), Zapier, or Make are the
  off-the-shelf alternatives considered.
- Needs the retention decision below, and more Zoom scopes (recordings).

### 3. Phone dial-in numbers
The original design wanted dial-in numbers alongside the link. Event
descriptions and footers show only the link, meeting ID, and passcode; the
numbers would come from Zoom with each room's details.

### 4. "How to join" sheets
One page per group: the link, what to click, the meeting ID and passcode,
dial-in, and who to contact if Zoom says "host has another meeting in
progress". Written by hand from a template, or generated from the footers by
a small script.

### 5. Room expiry outside script runs
Rooms unused for 300 days are only warned about, and expired ones replaced,
when someone runs `update_groups.py`. Either run it
regularly, or have the hourly Apps Script run send the warning.

## Non-code tasks

- Enable the Gmail API in the Google Cloud project, then sign in again on
  the next script run (the scripts now ask to send mail, for the email
  telling a group about its new calendar).

- Run `update_groups.py` for all groups (dry run
  first), then tell the community about the calendars and rooms.
- Facilitator training: claiming host with the host key (and asking an
  accidental host to "Make Host"), muting, screen sharing, recording.
- Give each group's facilitators the host key privately.
- Agree a recording-consent practice: circles decide by consent whether to
  record a meeting; Zoom's recording notice stays on (California requires
  all-party consent).
- If signing in to clasp again keeps interrupting, adjust the Workspace's
  reauthentication interval for admins (Admin console: Security → Access and
  data control → Google Cloud session control).

## Open decisions

- How long recordings are kept, in Zoom and in Drive.
- Nonprofit discounts: Zoom through TechSoup; Zapier or Make, if used.
- Moving a room to the second licensed Zoom user, which clash emails offer:
  Zoom can transfer a meeting (a room included) between users who both have
  scheduling privilege, keeping its ID and link, so the footer and events
  don't change. Done by hand in Zoom for now; a script could do it (the
  API's `schedule_for`).
- For community-wide calendars (see 1): which Gather group type admin
  groups use; their folders' and calendars' name suffix; and who gets the
  reminder before a community-wide meeting.
