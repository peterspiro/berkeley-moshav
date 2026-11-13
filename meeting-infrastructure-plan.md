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
  the footer, or (by `update_groups.py`, not `add_new_group.py`, as a new
  group has none) offered by name, before anything is created. For a room,
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
- **Community calendars** (see "Community calendars" below): admin groups of
  Gather's Squad type, made by `add_new_group.py -t a` or set up by
  `update_groups.py`, each with a calendar, a room under
  `community-zoom-hosts@`, and its audience in its footer; `group_meetings`
  invites the audience, reminds it, and words clash emails for them; the
  `[no invite]` tag; the admins' emails and guide.

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
  hosts the community-wide meetings (see "Community calendars"). It isn't
  one of `ROOM_USERS`: group rooms aren't moved to it.
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

### Community calendars
Several calendars for community-wide events (for example "Community
Meetings"), each managed by a small set of admins (a different set for each
calendar). The shared community calendar (`share@`, see
`calendar-notifications-plan.md`) stays separate: it's where members put
their own social events.
- **An admin group per calendar.** Each calendar has a Gather admin group,
  of Gather's **Squad** type (`ADMIN_GROUP_KIND`, `squad`; the value
  Gather's form uses is still to check), which no other group uses (see
  "Why a distinct Gather group type" below). Its Drive folder is one level
  down, like a club's: under a top-level "Meeting Admin" folder in the
  Shared Drive.
- **Names.** The Gather group, its Drive folder, and its Google Group end
  in "Admins", as other groups' names end in their type ("Community
  Meetings Admins", `community-meetings-admins@`), so the list isn't
  mistaken for one of everyone who comes; the folder must still be under
  "Meeting Admin". Its calendar is named without "Admins" ("Community
  Meetings"), and its room "Community Meetings Zoom Room". `add_new_group.py`
  takes the type from `-t a`, or from a name ending in "Admins".
- **Calendar.** Each admin group gets one calendar, shared with its Google
  Group as editors ("Make changes to events"). Everyone else sees its events
  through the all-events calendar, which is shared view-only with the
  everybody groups; that's enough, so admin groups get no other read access.
- **Zoom room.** Each admin group gets its own no-fixed-time room (Zoom
  type 3, the standard room settings) under the
  `community-zoom-hosts@berkeleymoshav.org` Zoom user
  (`COMMUNITY_ROOMS_USER`), so each calendar's recordings can be told apart
  by the room's name (see "Not built yet", 1). A skilled host claims host
  with that user's host key for host features such as breakout rooms.
- **Host key.** It's kept in the "Community Events Zoom Host Key" doc in the
  Meeting Admin folder, linked from every admin group's footer ("Zoom host
  key:", `COMMUNITY_HOST_KEY_DOC_URL`). The doc can be opened by the
  Shared Drive's members, the whole community, which is judged safe enough,
  but not by anyone with the link, so a forwarded footer gives away only a
  link. It's not put in calendar events, which are emailed to members'
  personal accounts and may be forwarded outside the community.
- **Recorded in the admin group's footer**, as for other groups: its Gather
  group, Drive folder, "Google calendar" and "Zoom meeting" links, plus the
  host key link and a "Calendar audience:" line naming the everybody group its events invite
  and its reminders go to. For now there's only Gather's default Full
  Community group, `all.members@berkeleymoshav.org`
  (`COMMUNITY_CALENDAR_AUDIENCE`, written when a footer names none), but
  the footer records it in case there are more everybody groups later.
  `group_meetings` finds the calendars, rooms and audiences the way it
  finds groups', and tells an admin group's footer from a group's by the
  audience line, since the Apps Scripts don't see Gather types. The
  calendar's name is the Google Group's without "Admins".
- **Events.** The Apps Script treats their events like group events: the
  admin group's room's details, and the audience (not the admin group) and
  the all-events calendar invited, but **not** the BM Meetings FAQ line,
  which is for group meetings only.
- **`[no invite]`.** An event marked `[no invite]` (in its title or
  description) doesn't invite the audience, nor get a reminder unless it
  also has a reminder tag: for an event only some people should come to,
  so the whole community isn't emailed about it. It works on group
  calendars too (not inviting the group). An audience already invited
  stays a guest (the script can't tell who added it), so the tag must be
  there before the event is first saved; otherwise the admins remove the
  guest by hand.
- **Reminders.** Each event gets a reminder 24 hours before, sent to the
  audience ("Hi everyone"), with replies going to the admin group;
  `[remind …]` and `[no reminder]` on an event work as for group meetings.
- **More invitees.** An admin group's footer can have an "Also invite:"
  line of more addresses, of any domain (say, a mailing list outside the
  Workspace that overlaps the community), that every event on its calendar
  invites along with the audience. They aren't reminded (reminders go to
  the audience only), and `[no invite]` leaves them out too. Set with
  `add_new_group.py -I`, changed with `update_groups.py -g ... -I` (`-I
  none` removes them); a changed line has `group_meetings` recheck the
  calendar's upcoming events, adding the new ones (an address removed from
  the line stays a guest of events it was already invited to).
- **A calendar with no Zoom room.** `add_new_group.py -z` makes an admin
  group whose calendar has no room, for in-person events: its footer says
  `Zoom meeting: none` in place of the room's lines (and has no host key
  line), so `update_groups.py` never makes one, and `group_meetings`, which
  then has no room link, adds no Zoom details. Its events still invite the
  audience and get reminders. The emails to its admins say nothing of a
  room, its host key, or clashes. (Keeping a room that events get only on
  request, with a tag, was the alternative; it was left out because a
  rarely used room expires after a year unused, changing its link, and a
  forgotten tag sends an event out without Zoom details.)
- **No agenda docs for now.** An admin group that later has one will need a
  doc of its own format, not the circle agenda doc (which has the Circle
  Structure rows), so `update_groups.py -a` leaves them out.
- **Not in the Circle Structure.** The `circle_agendas` Apps Script leaves
  out a group whose folder is in "Meeting Admin", without a warning.
- **Clashes.** A Zoom user hosts one meeting at a time, so community-wide
  meetings clash with each other (all rooms are under `community-zoom-hosts@`,
  Zoom user 3, even on different calendars), but not with group meetings,
  which are under the other two Zoom users. The existing check (overlapping
  or less than 15 minutes apart, next 8 weeks) covers them. Their clash
  emails are the group ones (`overlapMessage`), with the admin group
  greeted ("Community Meetings admins"), the meeting named by its calendar
  ("Community Meetings calendar"), and no option to move a room to the
  second Zoom user:
  - *Two community-wide meetings.* To: both calendars' admin groups (or the
    one, if both are on the same calendar); cc: `calendaring-alerts@`.
    Options: hold one on a personal or work Zoom account, or agree which one
    moves on its calendar.
  - *A community-wide meeting and one scheduled directly in Zoom under
    `community-zoom-hosts@`.* To: the admin group and
    `calendaring-alerts@`, asking them to agree which moves.

  (The earlier design, with one shared room under `circle-zoom-hosts@`, had
  community-wide meetings clash with group meetings too, and asked the group
  to move; with the separate Zoom user that case went away.)
- **Emails to the admins.** `add_new_group.py -t a`'s welcome email (a
  draft from the person running it) and, for an admin group whose calendar
  `update_groups.py` creates, the announcement (from `admin@`, replies to
  `calendaring-alerts@`) tell the admins how community-wide events are
  scheduled, rather than how to move a group's meetings; they share that
  text (`admin_calendar_paragraphs` in `util/calendar_announcement.py`).
  The welcome email reads:

  > **To:** community-meetings-admins@berkeleymoshav.org
  > **Subject:** Your new community calendar: Community Meetings
  >
  > Hi Dana,
  >
  > I've created a Community Meetings calendar for community-wide events,
  > and this email list, community-meetings-admins@berkeleymoshav.org, for
  > the people who schedule them. Links to the calendar, its Zoom room, its
  > Drive folder, and its admin group in Gather are in the footer at the
  > bottom of this message.
  >
  > I've made you a manager of the Gather group, which allows you to add
  > members to it, and to make other members managers. Everyone in it
  > receives messages sent to this list, and can add and change events on
  > the Community Meetings calendar. The rest of the community sees these
  > events on their primary calendars.
  >
  > Scheduling an event:
  > Create the event on the Community Meetings calendar itself, not on your
  > own calendar. (To add the calendar to your own Google Calendar, click
  > its link in the footer.)
  >
  > A few minutes after you save it, the event automatically gets:
  > - a link to the calendar's Zoom room, if it doesn't already have another
  >   Zoom or Meet link (or says [no zoom] in the description);
  > - the whole community (all.members@berkeleymoshav.org) invited, each
  >   member getting the invitation by email, unless the event says
  >   [no invite].
  >
  > So there's no need to add Zoom details or guests yourself.
  >
  > Everyone invited is also emailed a reminder 24 hours before each event,
  > and replies to it come to this list. You can change the reminder time by
  > adding, for example, [remind 2 hours] or [no reminder] in its
  > description.
  >
  > The Zoom room:
  > The room is the calendar's permanent meeting place: its link never
  > changes, and anyone with the link can join, with or without a host. For
  > host features such as breakout rooms or recording, one can claim host
  > with the host key, which is in the doc linked from the footer ("Zoom
  > host key").
  >
  > We have several community-wide calendars, all of which have zoom rooms
  > which belong to the same Zoom user, which can host only one meeting at a
  > time. If an event overlaps another community-wide event, or is less than
  > 15 minutes from one, you'll get an email asking the admins involved to
  > agree which one moves.
  >
  > All of this, and more, is in the guide for community calendar admins:
  > https://docs.google.com/document/d/1CO85_1BlMX_ONiSGp9g6B2svbi-ZGEa89YtMj8H6tkE/edit
  >
  > If you'd like a different address for the list, or a different name for
  > the calendar, just let me know.
  >
  > Peter
- **The admins' guide.** `community-calendar-admins-guide.md` holds the same
  information at more length (adding the calendar, scheduling step by step,
  the tags, reminders, the host key, clashes), for the admins to keep
  coming back to. It's also a Google Doc, whose link is in `ADMIN_GUIDE_URL`
  so the emails link it. The repo's copy is the source; the doc is updated
  from it.

#### How admin groups differ from circles
| | Circles and working groups | Admin groups |
|---|---|---|
| Drive folder | Top level of the Shared Drive | Under "Meeting Admin", like clubs under "Clubs" |
| Gather group, folder, and Google Group names | "<Name> Circle" / "<Name> Working Group" | "<Calendar> Admins" |
| Calendar name | The group's | The calendar's, without "Admins" |
| Zoom room | "<Group> Meeting Room", under `circle-zoom-hosts@` (or moved to `berkeley@urbanmoshav.org`) | "<Calendar> Zoom Room", under `community-zoom-hosts@`, never moved |
| Host key | Given privately to facilitators | In a doc the community can open, linked from the footer |
| Who events invite | The group itself | The audience in its footer (`all.members@`), not the admin group |
| BM Meetings FAQ line | Added | Not added |
| Clash emails | Group-to-group wording, offering to move a room to the second Zoom user | Admin-group wording, with no room to move |
| Agenda doc | Yes | None for now; later, a format of their own |
| Circle Structure sheet and doc | Yes | No: not part of the circle structure |
| Reminder before each meeting | To the group; timing from the agenda doc or the event | To the audience, 24 hours before unless the event says otherwise |
| Email when its calendar is made | Tells members to add the calendar and move meetings onto it | Tells the admins how to schedule community-wide events, linking their guide |
| `[no invite]` | The group isn't invited, nor reminded | The audience isn't invited, nor reminded |

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
Squad is the type used. The Python scripts read it from Gather; the Apps
Scripts, which see only footers and Drive, recognise admin groups by the
footer's audience line (`group_meetings`) and by their folder being in
"Meeting Admin" (`circle_agendas`). `add_new_group.py` takes it from
`-t a`, since admin groups' names carry no type ending.

## Recording links

Until recordings are copied into Drive (the recording archive, below),
links to them in Zoom are put where members already look. Built in
`group_meetings` (`recordings.gs`, with its rules in `recording_rules.gs`),
since that project already signs in to Zoom and runs hourly; nothing is
downloaded, so Apps Script's size limits don't matter.

- **Hourly, for new recordings** (the last 7 days', once every file of a
  recording has finished processing, usually within an hour or two of the
  meeting ending), each Zoom session's share link and passcode go:
  - **on its calendar event**: a "Recording:" line at the top of the
    description of the one occurrence it came from (a recurring series'
    other occurrences are untouched), sending no email. The event is the
    one, on a group calendar or a calendar `admin@` owns, whose location or
    description links the recording's Zoom meeting and whose time covers
    the recording's start (from 45 minutes before the event to its end).
    Since the all-events calendar is a guest of these events, the line shows
    there too; that's fine, as few members subscribe to it.
  - **in the group's agenda doc**, if it has one: a "Recording:" line below
    the "Time …" line of the meeting's tab (the tab titled with its date).
    A meeting with no tab yet is retried hourly for 14 days, in case one is
    added. A group with more than one meeting that day is skipped, as the
    tab can't be told apart.
  - **in a Google Sheet**: a row (date, time, title, link, passcode,
    length, Zoom topic, and Zoom's ID for the recording, hidden, so no
    recording is listed twice).
- **Backfill** (`backfillRecordings`, run by hand): does the same for
  older recordings (events, agenda docs, and sheets, a meeting with no
  agenda tab not being tried again), stepping
  month by month (Zoom lists at most a month at a time) through every Zoom
  user's recordings from `BACKFILL_FROM`, saving its place so a run stopped
  by Apps Script's 6-minute limit carries on when run again. Past events
  are safe to change: `sendUpdates: none` sends no guest an update, Google
  account or not (unlike moving a series to another calendar in Google
  Calendar, which sends one per past occurrence to guests outside Google).
  `backfillRecordingsDryRun` logs where each recording would go and changes
  nothing. Recordings made on personal or work Zoom accounts can't be
  reached.
- **Sheets**: one per group (circle, working group, club, ...), "<Group>
  Meeting Recordings", made in the group's Drive item the first time it has
  a recording, so it's shared with the group like everything else there;
  and one "Community Meeting Recordings" in the Shared Drive's Meeting Admin
  folder (which gives `all.members@` view access), holding every
  community-wide meeting, plus an **Unassigned** tab.
- **Which sheet** (the first rule that decides; see `categorizeRecording`):
  1. Held in a group's room: that group; in a community calendar's room:
     the community sheet.
  2. Its calendar event invites an audience (`all.members@`): the community
     sheet; otherwise, if it invites one group (its email list or its
     calendar): that group.
  3. Its title (the event's, then the Zoom topic) is, ignoring case and
     extra spaces, the title of a recurring meeting on a group calendar
     (that group) or a community calendar (the community sheet). A title on
     calendars leading to different sheets decides nothing more:
     uncategorized.
  4. Its title contains a group's name (for a community calendar's admin
     group, the calendar's name, meaning the community sheet), as whole
     words; the longest name wins.
  5. Its title contains "listening session" or "ask me anything", in any
     case: the community sheet.
  6. Otherwise uncategorized: a row on the Unassigned tab. Typing a
     group's name or address, or "community", in a row's Group column
     moves it to that sheet (and its agenda doc) on the next hourly run.
- **Report**: each run (hourly or backfill) that processed at least one
  recording emails `calendaring-alerts@` how each was assigned, by which
  rule, and what happened to its event and agenda doc; unassigned ones
  first.
- **Zoom scopes**: `cloud_recording:read:list_user_recordings:admin` and
  `cloud_recording:read:list_recording_files:admin`. Recordings must be
  shareable (the account's cloud recording sharing setting); older ones
  may need sharing turned on one by one.

## Not built yet

### 1. Recording archive
Copy Zoom cloud recordings into the matching group's Drive folder, then let
Zoom delete them. Zapier or Make is the likely way; when it's done, the
recording links above should point to the Drive copies.
- Match recordings to groups by meeting topic ("<Group> Meeting Room"); a
  community-wide calendar's ("<Calendar> Zoom Room") go to its admin
  group's folder.
- Copy each file (video, audio, transcript, chat), recording which recordings
  are done so none is copied twice; optionally delete from Zoom after a
  verified upload and a grace period.
- Build it as a scheduled Python job reusing `util/zoom_utils.py`, not in Apps
  Script, whose download size and run-time limits are too small for videos.
  RecordFlow (single destination folder only), Zapier, or Make are the
  off-the-shelf alternatives considered.
- Needs the retention decision below, and more Zoom scopes (recordings).

### 2. Phone dial-in numbers
The original design wanted dial-in numbers alongside the link. Event
descriptions and footers show only the link, meeting ID, and passcode; the
numbers would come from Zoom with each room's details.

### 3. "How to join" sheets
One page per group: the link, what to click, the meeting ID and passcode,
dial-in, and who to contact if Zoom says "host has another meeting in
progress". Written by hand from a template, or generated from the footers by
a small script.

### 4. Room expiry outside script runs
Rooms unused for 300 days are only warned about, and expired ones replaced,
when someone runs `update_groups.py`. Either run it
regularly, or have the hourly Apps Script run send the warning.

## Non-code tasks

- Before the first community calendar: make the "Meeting Admin" folder at
  the top of the Shared Drive; check that Gather's Squad type is `squad`;
  make sure `all.members@` lets `admin@` post (for reminders); push
  `group_meetings` and `circle_agendas` (README.md: "Setting up community
  calendars"). (The admins' guide is already a Google Doc, linked from the
  emails.)
- Check that the "Community Events Zoom Host Key" doc isn't open to
  anyone with the link (README.md: "Setting up community calendars").

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
