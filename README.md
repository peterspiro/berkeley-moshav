# berkeley-moshav

Administration scripts for the Berkeley Moshav community's online setup.

Their main job is keeping these things linked together and consistent:

- **[Gather groups](https://berkeley-moshav.gather.coop/groups)** (circles,
  working groups, clubs, and community calendars' admin groups, with their
  members and managers)
- **Google Drive** folders and shared drives in the community's
  [Shared Drive](https://drive.google.com/drive/folders/0AFqC2xo9aTgPUk9PVA),
  linked to Gather groups through Gather's
  [Google Drive settings](https://berkeley-moshav.gather.coop/gdrive/config)
- **[Google Groups](https://groups.google.com/all-groups)** in the
  `berkeleymoshav.org` Workspace, which serve as each Gather group's email list
- The **Circle Structure**, the tree of the circles and working groups: a
  Google Sheet where the Coordinating Circle
  (`coordinating-circle@berkeleymoshav.org`) changes a group's parent and
  nothing else, and a Google Doc, which anyone with the link can view, that
  shows the tree with links to each group's agenda doc, members, documents,
  and email list. The `circle_agendas` Apps Script fills in both from the
  Google Groups' footers, and writes each circle's place (its parents,
  sub-circles, and links to its parent) into its agenda doc. Both are in a
  "Circle Structure" folder at the top of the Shared Drive. (It replaced the
  [Circle Structure](https://berkeley-moshav.gather.coop/wiki/circle-structure)
  wiki page in Gather.)
- Each group's **Google calendar**, owned by `admin@berkeleymoshav.org` and
  shared with the group's Google Group so its members can add meetings, and
  its permanent **Zoom meeting room**, owned by the
  `circle-zoom-hosts@berkeleymoshav.org` Zoom user. A room's link never
  changes, and meetings in it can run with no host present. There's also one "All Berkeley
  Moshav Events" calendar for every meeting, which everyone can view.
- **Community calendars**, for community-wide events (such as community
  meetings), each run by its own admin group (Gather's "Squad" type, named
  "<Calendar> Admins"): its calendar, its email list, its folder in the
  Shared Drive's "Meeting Admin" folder, and its own Zoom room
  ("<Calendar> Zoom Room"), owned by the
  `community-zoom-hosts@berkeleymoshav.org` Zoom user. Their events invite
  the whole community (`all.members@berkeleymoshav.org`).
  `community-calendar-admins-guide.md` is the guide for their admins, to
  make into a Google Doc (see "Setup").

Three Google Apps Script projects run inside Google Workspace alongside the
Python scripts:

- **`groups_drive_sync`** keeps each Google Group's membership in step with its
  Drive folder or shared drive, hourly, and emails a report of any changes.
- **`calendar_notifications`** looks after the shared community calendar: it
  emails a community group when one of its members adds an event, and removes
  invitations from anyone who isn't allowed to add events.
- **`group_meetings`** looks after the groups' calendars and the community
  calendars: it gives each group's meetings its Zoom room and invites the
  group (for a community calendar, the community) and the all-events
  calendar, emails each group (or the community) a reminder before its
  meetings, and emails
  warnings about meetings that would clash over a Zoom
  user, and about meetings set up as invitations from someone's own calendar.
  It also invites the all-events calendar to the events created on the admin
  account's other calendars, including the shared community calendar.

A fourth, **`circle_agendas`**, will run the circle agenda docs (see
`circle-agendas-plan.md`): it builds each circle's agenda doc from a blank
doc, and runs the web app that the links in an agenda doc open (so far, to add
a meeting, add an agenda item, and update a meeting's times); a meeting's
"✉ Announce agenda" link opens an email to the circle's list in Gmail,
announcing that its agenda is ready. It also keeps the Circle Structure (see
above and `circle_agendas/structure.gs`).

The repo also keeps, in `archive/`, the one-off scripts that were used to
migrate the community into Gather (members, households, groups, photos) and
to set up the Google side, along with cleanup scripts from that migration.

Gather has no API for any of this, so most scripts drive its web UI with
[Playwright](https://playwright.dev/python/). Google is reached through its
APIs.

## How the group linking fits together

This section covers the linking of Gather groups, Drive, Google Groups,
calendars, and Zoom rooms; the calendar notifications script is separate.

```
Gather group members ──(Gather's Drive sync)──▶ folder Content managers / shared-drive Viewers
                                                   │
                                (groups_drive_sync.gs, hourly)
                                                   ▼
                                          Google Group (email list)
```

Each Google Group's custom footer carries an auto-managed block linking to its
Gather group and its Drive item, and, once the group has them, its Google
calendar and Zoom meeting room:

```
--- Auto-managed links ---
Technology Circle's:
Gather group: https://berkeley-moshav.gather.coop/groups/1093
Google Docs folder: https://drive.google.com/drive/folders/...
Google calendar: https://calendar.google.com/calendar/u/0?cid=...
Zoom meeting: https://us06web.zoom.us/j/...
Meeting ID 812 3456 7890, passcode 123456
```

A community calendar's admin group's footer also links the doc holding the
community rooms' host key (`COMMUNITY_HOST_KEY_DOC_URL` in
`util/meeting_rooms.py`), and names the calendar's **audience**, the
everybody group its events invite and its reminders go to. The audience line
is how the `group_meetings` Apps Script, which can't see Gather's group
types, knows a community calendar from a group's:

```
Zoom host key: https://docs.google.com/document/d/.../edit
Calendar audience: all.members@berkeleymoshav.org
```

The host key doc can be opened by the Shared Drive's members (the
community), which is judged safe enough, but not by anyone with the link; the
link itself gives nothing away. It's not put in calendar events, which are
emailed to members' own accounts and may be forwarded.

A community calendar made without a Zoom room (`add_new_group.py -z`, for
in-person events) has `Zoom meeting: none` in its footer instead of its
room's lines, and no host key line. Neither script then makes it a room, and
`group_meetings`, which finds no room link, adds no Zoom details to its
events. To give it a room later, delete that line and run
`update_groups.py -g` for it.

The meeting ID and passcode are for members who type them into Zoom instead
of clicking the link; since the link already lets anyone in, showing the
passcode exposes nothing more. Passcodes are six digits, to be easy to type
and read out.

The footer is the only record of which calendar and Zoom room belong to a
group; `update_groups.py` and `add_new_group.py`
create them and write the links. A new calendar is shared with the group's
Google Group as "Make changes to events", so membership of the Google Group
(kept in step with Drive above) decides who can use it. The scripts only set
that starting access: a circle can be restricted so only some members edit
(see "Letting only some members edit a group's calendar" below).

A group linked to a shared drive has a "Shared Drive:" line instead of
"Google Docs folder:". `groups_drive_sync.gs` reads that footer to know which
item's permissions to copy into the group, so a new group starts syncing as
soon as its footer is written, with no redeploy. Rewriting the block keeps any
calendar and Zoom links already in it, along with anything written above it.

The footers are also where the `circle_agendas` Apps Script finds the groups
for the Circle Structure sheet: each circle and working group whose footer
links a Gather group and a Drive item gets a row, keyed by its Gather group
ID, so a rename keeps its place.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

- **Gather login**: put your Gather admin credentials in `~/.gather`
  (`chmod 600`):
  ```
  email = you@example.com
  password = secret
  ```
- **Google APIs**: create a Desktop OAuth 2.0 client in Google Cloud Console,
  save its JSON as `client_secret.json` in the repo root (or pass `-c`), and
  enable the Drive, Admin SDK (Directory), Groups Settings, Google
  Calendar, and Gmail APIs (Gmail sends the email telling a group about its
  new calendar). On first run, a script prints a sign-in URL; open it signed
  in as `admin@berkeleymoshav.org`, which owns the group calendars (the
  scripts that make calendars refuse to run as anyone else). The sign-in is
  saved to `~/.google_setup_token.pkl`; delete that file to sign in as a
  different account. A script that needs a scope the saved sign-in lacks asks
  you to sign in again.
- **Zoom API**: in the Zoom App Marketplace, create a **Server-to-Server
  OAuth** app (Develop → Build App), signed in as the account owner or as an
  admin whose role has View and Edit for "Server-to-Server OAuth app" (Role
  Settings → Advanced features) and for Users. Give it these scopes, then
  activate it:

  | Scope | Zoom's name for it | Used for |
  |---|---|---|
  | `meeting:read:meeting:admin` | View a meeting | Checking a group's room still exists, and reading its settings and link |
  | `meeting:write:meeting:admin` | Create a meeting for a user | Creating a group's room |
  | `meeting:update:meeting:admin` | Update a meeting | Fixing a room's topic or settings; new passcodes (`reset_zoom_room.py`) |
  | `meeting:read:list_meetings:admin` | View a user's meetings | Listing the account's meetings, to offer ones matching a group's name |
  | `meeting:delete:meeting:admin` | Delete a meeting | Deleting a meeting that matches a group's name, when you choose to |
  | `meeting:read:list_past_instances:admin` | View a past meeting's instances | Warning about rooms unused for 300 days, before Zoom deletes them at 365 |
  | `user:read:list_users:admin` | View users | Finding every Zoom user's scheduled meetings, to offer ones matching a group's name, and to check for overlaps (`group_meetings`) |

  If its creator's role is later reduced, Zoom removes those scopes, so
  transfer the app to another admin first. Put its credentials in a file
  named `zoom_credentials` in the repo root, beside `client_secret.json`
  (git-ignored; `chmod 600`). Unlike `~/.gather`, which holds each admin's
  own login, these belong to the community's app and are the same for
  everyone:
  ```
  account_id = ...
  client_id = ...
  client_secret = ...
  ```
  Finally, in the Zoom web portal's account settings (under Security, the
  passcode requirements), allow passcodes made only of digits, and preferably
  require them so rooms made by hand match. The scripts give every room a
  six-digit passcode, which Zoom rejects if passcodes must contain letters.

Run scripts from the repo root, e.g. `python add_new_group.py ...` or
`python archive/data_import/parse_households.py ...`. Every script
supports `-h` for its options, and most support `-n`/`--dry-run`. Logs and
screenshots go to `debug/` (git-ignored).

### Changing the host key

All the group rooms share one host key: the
`circle-zoom-hosts@berkeleymoshav.org` Zoom user's. It's given privately to each group's facilitators, so they can
claim host in their group's room. Change it only when it has leaked or a
facilitator who knows it shouldn't any more, since every facilitator in every
group then needs the new one. It's changed by hand, not by a script: sign in
to the Zoom web portal as `circle-zoom-hosts@berkeleymoshav.org`, open
**Profile**, and edit **Host Key** (6 to 10 digits; Zoom rejects easy ones like 123456).

The community calendars' rooms have their own host key, the
`community-zoom-hosts@berkeleymoshav.org` Zoom user's, changed the same way.
It's kept in the "Community Events Zoom Host Key" doc in the Shared Drive's
Meeting Admin folder, linked from every admin group's footer; after changing
the key, update the doc.

### Setting up community calendars

Before making the first community calendar's admin group:

1. Make a top-level folder named **Meeting Admin** in the Shared Drive (the
   scripts put admin groups' folders in it, and the `circle_agendas` Apps
   Script leaves groups whose folders are there out of the Circle Structure).
2. Check that Gather's "Squad" group type is `squad` in its group form
   (`ADMIN_GROUP_KIND` in `util/gdrive_config.py`); `add_new_group.py -t a -n`
   doesn't check it, but a real run fails to create the Gather group if it's
   wrong.
3. Check that the "Community Events Zoom Host Key" doc's General access is
   "Restricted", not "Anyone with the link": the Shared Drive's members (the
   community, admins included) can open it, and no one else should.
4. Make sure `all.members@berkeleymoshav.org` (the audience) lets
   `admin@berkeleymoshav.org` post, or the reminders it sends there bounce.
5. Push the `group_meetings` and `circle_agendas` Apps Scripts (`clasp push`
   in each folder).
6. The admins' guide, `community-calendar-admins-guide.md`, is also a
   [Google Doc](https://docs.google.com/document/d/1CO85_1BlMX_ONiSGp9g6B2svbi-ZGEa89YtMj8H6tkE/edit)
   linked from the emails to admins
   (`ADMIN_GUIDE_URL` in `util/calendar_announcement.py`). The repo's copy is
   the source: when it changes, update the doc to match.

Then, for each calendar:

```bash
python add_new_group.py -N Community Meetings -t a -m Dana
```

#### Trying one out

To try a community calendar without emailing the whole community, make a
small Google Group to stand in for it (say `calendar-test@`, with a few
willing members, letting `admin@` post), and a test calendar whose audience
it is:

```bash
python add_new_group.py -N Calendar Test -t a -m Dana -A calendar-test
```

`add_new_group.py` stops if the audience isn't a Google Group, and warns if
it doesn't let everyone in the organization post (so `admin@`'s reminders
would bounce); it checks `all.members@` the same way for a real calendar.
Its events then invite, and remind, only `calendar-test@`. The rest of the
community isn't emailed, but test events do show on the All Berkeley Moshav
Events calendar, which everyone can view, so give them titles like "TEST" and
delete them afterwards. Clash emails go to the test admin list and
`calendaring-alerts@`; a test event that overlaps a real community-wide event
also emails that calendar's admins, so pick free times. Afterwards, delete the
test group (in Gather, its Google Group, folder, calendar, and Zoom room), or
change its footer's "Calendar audience:" line by hand to make it real.


### Letting only some members edit a group's calendar

Every member of a group can add and change events on its calendar to begin
with. If a circle wants only a few designated members to do that, change the
calendar's sharing by hand; the scripts leave it alone afterwards (they only
share a calendar with its group when it isn't shared with the group at all).

1. Sign in to Google Calendar as `admin@berkeleymoshav.org` and open the
   group calendar's **Settings and sharing**.
2. Under **Share with specific people or groups**, change the group's email
   list to **See all event details**.
3. Add each designated member with **Make changes to events**. They need a
   Google account (a personal Gmail address is fine).
4. Optionally, give the circle's admin **Make changes and manage sharing**, so
   they can change who edits without asking the Technology Circle.

Members who can't edit still get every meeting's invitation.
`update_groups.py` shows a restricted calendar in its
status line as "calendar (members can view, not edit)".

## Directories

| Directory | Used for |
|---|---|
| `.` (root) | The scripts used day to day to create and maintain groups, plus setup files and design notes |
| `groups_drive_sync/` | Apps Script project that syncs each Google Group's membership from its Drive item |
| `calendar_notifications/` | Apps Script project that announces and gatekeeps events on the shared community calendar |
| `group_meetings/` | Apps Script project that adds Zoom rooms and guests to group calendar events, and warns about overlapping meetings |
| `circle_agendas/` | Apps Script project for the circle agenda docs (building them, and the web app their links run) and the Circle Structure sheet and doc |
| `util/` | Shared Python modules used by the scripts |
| `tests/` | Unit tests (`pytest`) |
| `archive/` | One-off scripts and plans that are no longer in use, kept for reference |
| `archive/data_import/` | Scripts that were used to populate Gather from the community's spreadsheets and website |
| `archive/google_setup/` | Scripts that were used to set up the Google Groups and Drive links for the existing groups |
| `archive/circle_agendas/` | The probes that were run to see what Apps Script can do to an agenda doc, before the docs were built |
| `archive/cleanup/` | Scripts that were used to undo earlier approaches (old wiki pages, Mailman lists, Drive group permissions) |
| `debug/` | Created at run time for logs and screenshots; not in git |

## Scripts

### Root: group creation and maintenance

- **`add_new_group.py`** creates a complete new group in one step: the Drive
  folder, the Google Group (optionally with a shorter address), the Gather
  group with its manager, and the group's calendar and Zoom room; links them
  all together; unless it's a club or an admin group, has the group put under its parent
  (`-p`, a circle, or for a working group a circle or working group) in the
  Circle Structure, by writing the parent to "Circle structure
  placements.json" in `admin@`'s My Drive for the `circle_agendas` Apps
  Script, which adds the group to the sheet from its footer within the hour; with
  `--agenda-doc`, for a circle or working group, creates its agenda doc,
  for the `circle_agendas` Apps Script to build; and opens a welcome email to the new list as a draft in
  your mail program. With `-t a` (`--type admin`), it makes a community
  calendar's admin group instead (Gather type "Squad";
  `-N Community Meetings -t a -m Dana`, or a name ending in "Admins"): its
  Gather group, folder (in the Shared Drive's "Meeting Admin" folder), and
  Google Group named "Community Meetings Admins"
  (`community-meetings-admins@`); its calendar "Community Meetings"; its
  room "Community Meetings Zoom Room", under `community-zoom-hosts@`; the
  host key doc's link and the calendar's audience, `all.members@`, in its
  footer (`-A`/`--audience` names another, for trying a calendar out); no parent
  and no agenda doc; and a welcome email telling the admins how
  community-wide events are scheduled. `-z`/`--no-zoom` makes it with no
  Zoom room, for a calendar of in-person events (see "How the group linking
  fits together").
- **`update_groups.py`** brings every group (or one,
  with `-g`) into line: offers to find or create a missing Drive folder or
  email list, enforces the Google Group settings, gives each group a calendar
  and Zoom room, and writes the footer links. (It was
  `update_groups_in_google_and_hierarchy` while it also kept the Circle
  Structure wiki page; the `circle_agendas` Apps Script now keeps the Circle
  Structure, from the footers.) A community calendar's admin group (Gather
  type "Squad") is handled like `add_new_group.py -t a` makes it: a new
  folder for it goes in "Meeting Admin", and it and a new Google Group are
  named "<Calendar> Admins"; its calendar is named without "Admins", and
  its room "<Calendar> Zoom Room", under `community-zoom-hosts@` (only a
  no-fixed-time meeting of that user can be used); its footer gets the host
  key link and the audience line (`all.members@` unless it names another);
  one whose footer says `Zoom meeting: none` gets no room and no host key
  link; the email about its new calendar is the admins' version
  (how to schedule community-wide events); and it gets no agenda doc. Its first run also creates the
  "All Berkeley Moshav Events" calendar, which it shares view-only with the
  everybody groups (with `-g`, only if `--share-all-events` is given, since
  finding them means reading every group from Gather). When it creates a group's calendar, it emails the
  group's list from `admin@` (replies going to `calendaring-alerts@`), once
  the footer links the calendar and Zoom room: a link for everyone to add
  the calendar, and steps for meeting organizers to move the group's
  meetings onto it. The email names the group by its full name
  ("Membership Circle", not "Membership"; a pod's name, like "Garden Pod",
  is left as it is). A dry run shows each email it would send.
  `--announce-calendar` sends it even when the calendar already existed
  (with `-g`, to one group; without, it asks before emailing every group).
  With `-g`, `-e`/`--email-draft` opens the email as a draft in your own
  mail program instead, for you to review and send from your own address;
  a draft is plain text, without the Reply-To or the calendar icon.
  A group's calendar is named after its Gather group; with `-g`,
  `-f`/`--full-calendar-name` names it by the group's full name instead, ending
  with its type ("Technology Circle", "Garden Working Group"), creating or
  renaming it so (a pod's name, like "Garden Pod", gets no type added).
  Later runs leave a calendar named that way as it is.
  Calendars are shared with groups without Google's
  "shared a calendar" email, which for a group goes to the group's address
  and has a button that does nothing for members; members add a calendar
  from the link in the group's footer.

  With `-a`/`--agenda-docs` (with `-g`, for that group alone), it also gives
  every circle and working group (not everybody groups) that has a Drive item and an email list an agenda doc,
  "<Group> Agenda", created empty in its Drive item and labelled for the
  `circle_agendas` Apps Script to build within minutes; a group that has
  one keeps it. Each run (with `-g` too) writes every Gather member's
  short name
  (first name, with the last initial, without a period, if another member
  shares it), by
  their Gather email and their Google Email, to "Circle agenda member
  names.json" in `admin@`'s My Drive, so the circle agenda docs list
  members by name (see `circle_agendas/README.md`).

  Calendars, Zoom meetings, and events match a group when their names are
  like the group's, or contain its acronym (for the "Development, Finance,
  & Legal Circle", "DF&L", "DFL", or "D.F.L."). When a group's footer links
  no calendar, it asks which of the matching calendars is the group's, or
  creates one. When it links no
  Zoom room, it lists every scheduled meeting in the Zoom account whose name
  matches the group's (any user's, of any kind, with its owner and schedule,
  and the matching calendar events held in it; if those events weren't
  offered for moving, because the group's calendar already existed, it says
  so and suggests `-m`/`--move-events`),
  then asks of each one in turn whether to use it as the room, ignore it, or
  delete it. Only a no-fixed-time meeting of `circle-zoom-hosts@` or
  `berkeley@urbanmoshav.org` (the second Zoom user) can be used, since Zoom
  can't change a meeting's type; choosing a second one to use
  asks to confirm the switch, and what to do with the first. Nothing changes
  until the end, when it shows what it will do and, if it will delete any
  meeting (whose link then stops working), asks you to type `delete`. Unless
  a meeting is used, it creates a room. A dry run asks the same questions but
  changes nothing. `add_new_group.py` doesn't look for matches: a new
  group's calendar and room are made new (or, on a re-run with `-i`, are
  the ones its footer links), and it offers no events to move; run
  `update_groups.py -g ... -m` afterwards for that.

  When it creates a group's calendar, or for every group with
  `-m`/`--move-events`, it looks for the group's meetings on other calendars:
  every calendar on `admin@`'s calendar list (its own, including its primary
  calendar, and the ones it's subscribed to) except the group calendars, the
  all-events calendar, and Google's holiday and birthday calendars. It lists
  the events in the next 12 months whose titles match the group's name
  (each repeating series once, leaving out series that have ended, and
  naming the matching Zoom meeting an event is held in, if any), then asks
  of each whether to copy it to the group's calendar, delete it, both, or
  skip it. A copy keeps the title, description, location, times, and repeat
  rule, but not any Zoom or Meet link (so the group's room is
  added); occurrences of a series that were moved or changed become one-off
  events. For each of a copied event's guests who isn't in the group, it
  asks whether to invite them to the copy (they're emailed along with the
  group, once the copy gets the group's room), share the group's calendar
  with them (view only, with Google's email), both, or neither. A series'
  copy starts at its next occurrence (the list shows both
  the original's first date and the copy's), and ends when the original
  would. Copies are tagged with the original, so they aren't offered again.
  Deleting needs edit access to the event's calendar: an event organized
  there is cancelled for everyone, with Google's cancellation emails; an
  invitation is removed from that calendar only.

  For an event organized on a calendar `admin@` can edit, "both" is
  replaced by "move", which uses Google's own move: the group's calendar
  becomes the organizer, and the event keeps its ID, guests, and past
  occurrences, so nobody's invitation is cancelled. Its Zoom or Meet link
  and any rooms are then removed, from its changed occurrences too.
  Its guests who aren't in the group stay invited, and it asks only
  whether to share the group's calendar with each. Invitations can't be
  moved (Google moves only the organizer's event), so they're offered
  "both" as before. As with Zoom meetings,
  nothing changes until the summary, and deleting asks you to type
  `delete`. `add_new_group.py` does this when it creates the calendar.
  Calendars `admin@` can't see (other members' own calendars) aren't
  searched; to include one, share it with `admin@` and add it to
  `admin@`'s calendar list.
- **`reset_zoom_room.py`** gives a group's Zoom room a new passcode, and so a
  new link, both of which it writes into the group's footer, for when a link has
  leaked.
- **`audit_group_memberships.py`** is a read-only report of where a group's
  Gather membership, Drive permissions, and Google Group membership disagree,
  plus setup problems and anyone else with access to its Drive item.
- **`add_group_wiki_page.py`** creates a wiki page for one group.
- **`seed_circle_structure.py`** copies each group's parent from the old
  Circle Structure wiki page in Gather into the Circle Structure sheet, once,
  before `setupCircleStructure` makes the sheet (see
  `circle_agendas/README.md`): it writes the parents, by Gather group ID, to
  "Circle structure placements.json" in `admin@`'s My Drive, and the
  `circle_agendas` Apps Script gives them to the groups whose Parent is
  still empty, if the rules allow it, as soon as the groups have rows. Rows without a
  `[Members]` link, and the rows under them, are listed and skipped.
- **`change_group_email_alias.py`** gives a group's Google Group a new, usually
  shorter, address and moves the Gather group's email list over to it.
- **`setup.sh`** installs and starts a local Gather development instance, for
  trying scripts out safely before running them against the real site.

### Apps Script projects

All are deployed to Google with [`clasp`](https://github.com/google/clasp)
(`clasp push` from the project's directory).

- **`groups_drive_sync/groups_drive_sync.gs`** runs hourly. For every Google
  Group whose footer links a Drive item, it makes the group's members match
  the item's Content managers (folder) or Viewers (shared drive), and emails
  any membership changes to group-sync-alerts@berkeleymoshav.org. Its
  directory also holds `gdrive_item_map.json`, a cache of Drive IDs to
  Gather's linked-item IDs that the Python scripts use.
- **`calendar_notifications/calendar_notifications.gs`** watches the shared
  community calendar (`share@berkeleymoshav.org`): it emails a community
  group when one of its members adds an event, and removes invitations from
  anyone who isn't allowed to add events. See
  `calendar_notifications/README.md` for deployment.
- **`group_meetings/group_meetings.gs`** runs every 5 minutes, as
  `admin@berkeleymoshav.org`, over every group calendar linked from a footer.
  Each event the group calendar organizes gets the group's Zoom room (link,
  meeting ID, passcode, and the number and name of the Zoom user that owns
  the room) with a link to the "BM Meetings FAQ", in place of
  any "Zoom link to follow" line, and the group and the all-events calendar as guests, unless it's marked `[no zoom]`
  or has another meeting link typed in (then it gets only the guests).
  An event of a group with a circle agenda doc also gets a link to the doc,
  removed again if the doc is trashed or deleted. An event marked
  `[no invite]` doesn't get the group as a guest, nor a reminder unless it
  has a reminder tag.
  A community calendar (one whose admin group's footer names an audience)
  is handled like a group's, except that its events invite the audience,
  not the admin group, and get no FAQ line; their reminders go to the
  audience, with replies to the admin group; and its clash emails go to
  the admin groups, naming the calendars, without the option of moving a
  room to another Zoom user (all community rooms are under
  `community-zoom-hosts@`, Zoom user 3, so they clash only with each other).
  An event that's only a copy of someone's invitation gets its organizer an
  email asking them to create it on the group calendar instead. Each group
  gets a reminder email before each of its meetings: 24 hours before, unless
  its agenda doc's Remind before meetings row says another time (or "none"), or
  the event says `[remind 2 hours]` or `[no reminder]`. It also
  checks the next 8 weeks for meetings in rooms of the same Zoom user that
  overlap or are less than 15 minutes apart, including ones scheduled
  directly in Zoom, and emails the groups involved (and, for a meeting
  scheduled directly in Zoom, calendaring-alerts@berkeleymoshav.org, for the
  Technology Circle), listing their options: moving one meeting to another
  time (with a link to the all-events calendar for finding a free time),
  holding one on a personal or work Zoom account, and, where a group's room
  could help, having it moved to the second Zoom user. On every other
  calendar `admin@` owns (its primary, which is the shared community calendar,
  and any others; re-read hourly), each event the calendar organizes gets the
  all-events calendar as a guest, with no email; invitation copies there are
  left alone. Its rules are
  in `group_meetings/event_rules.gs`, tested under Node. See
  `group_meetings/README.md` for deployment, including the Zoom credentials
  it reads from Script Properties.

- **`circle_agendas/`** builds each circle's agenda doc (on a timer, and a
  preview by hand from the Apps Script editor), is the web app behind the links in an agenda
  doc, and keeps the Circle Structure. See `circle_agendas/README.md`.
  - **`circle_agendas/web_app.gs`** is the web app, deployed to run as
    `admin@berkeleymoshav.org` for anyone with a Google account: a link in
    an agenda doc names an action, the doc, and a meeting, and the web app
    shows a page saying what it did. It acts only on built circle agenda
    docs (and the latest preview).
  - **`circle_agendas/meetings.gs`** holds those actions: adding the
    circle's next meeting from its calendar, in a tab of its own (with its
    Zoom room, its
    members as an attendance checklist, a link to the previous meeting,
    and the previous meeting's action items); updating a meeting's times
    (each item's start, the closing round's share of what's left, and the
    end, flagging durations it can't read), which also rewrites the
    meeting's "✉ Announce agenda" link (a Gmail compose link, not a web app
    action, addressed to the circle's list, announcing the agenda, with a
    link to it and its Zoom room) from the meeting's date, or adds one to a meeting that lacks it; and adding an
    agenda item just above the links, which sit above Action Items Recap.
  - **`circle_agendas/circles.gs`** finds the circle a doc belongs to: the
    Google Group whose footer links the Drive folder (or shared drive) the
    doc is in. It reads the circle's calendar events, and its members'
    names (from the Gather directory's, which the Python scripts write to
    Drive, or else its Drive item's sharing).
  - **`circle_agendas/circle_docs.gs`** builds each circle's and working
    group's agenda doc, every 10 minutes: each doc the Python scripts
    created empty and labelled "new" is built, gets its group's name,
    calendar, email address, Drive folder, and place in the Circle
    Structure in About this circle, and is
    relabelled "built", with its layout version. On the same timer, it
    applies any change to the Circle Structure (see `structure.gs`), and puts
    back the Parent Circle and Sub-Circles rows of each doc changed since
    if they no longer say what they should. Below
    About this circle's Zoom room row come a link to the circle's
    calendar and the Remind before meetings row, which says when the circle's
    meetings get reminders (`group_meetings` reads it); run by hand, it
    adds those rows to docs built before them. Each day, it brings the Roles
    table's Name dropdowns up to date with the circle's members (as adding
    a meeting also does) and colors red its Term ends dates that are past; on the 10-minute
    timer, it does that to each doc changed since.
  - **`circle_agendas/structure.gs`** keeps the Circle Structure:
    `setupCircleStructure`, run once, makes the sheet (shared with
    `coordinating-circle@`, who can edit only its Parent column, a dropdown
    of the groups each may be under) and the doc. Hourly, it brings the
    sheet's rows up to date with the Google Groups' footers (new groups,
    renames, links, types, groups that are gone, and the parents waiting in
    "Circle structure placements.json"), with a row for each circle and
    working group (everybody groups only if named as circles; no clubs, nor
    community calendars' admin groups, whose folders are in "Meeting Admin"),
    who holds each role in its agenda doc's Roles table, its guests
    (members of its Google Group who aren't in Full Community's), and when
    its meetings are, from its calendar's repeating meetings ("2nd & 4th Tue
    7–8:30 pm"). On each edit, and every 10
    minutes, it refuses a parent the rules don't allow (putting back the one
    from before, with a note on the cell saying why), updates the dropdowns
    and the Problems column, and rewrites the doc and the Parent Circle and
    Sub-Circles rows of the agenda docs whose place changed: its parent, and
    its sub-circles and working groups, each linked to its agenda doc. Each
    day it rewrites those rows in every doc where they've gone out of date.
  - **`circle_agendas/structure_rules.gs`** holds the Circle Structure's
    rules (a circle only under a circle, a working group under a circle or
    working group, no loops; a group's type from its Drive folder's name: "Circle", or "Working Group", "Work Group", "Team", or "Pod" for a working group),
    the tree, what an agenda doc's rows say, and how the sheet words a
    group's meeting times, tested under Node.
  - **`circle_agendas/build_template.gs`** builds an agenda doc from a
    blank doc, entirely from code (so it depends on no other document):
    each circle's doc, and a preview, rebuilt in place, for trying changes.
  - **`circle_agendas/agenda_rules.gs`** holds the agenda's time arithmetic
    (each item's start, and the closing round's share of what's left) and
    the parsing of times and durations, tested under Node.
  - **`circle_agendas/doc_helpers.gs`** holds the helpers the others
    share.

### `util/`: shared modules

- **`gather_utils.py`**: Gather login, page scraping and form helpers, the
  directory CSV, logging
- **`gdrive_config.py`**: Gather's `/gdrive/config` page, Shared Drive folder
  lookup and creation, and folder naming
- **`google_group_utils.py`**: Google sign-in, group naming and addresses,
  and Directory and Groups Settings API helpers
- **`google_group_footer.py`**: reading and writing the auto-managed footer
  links
- **`hierarchy_wiki.py`**: reading the old Circle Structure wiki page (for
  `seed_circle_structure.py`), and writing it (for
  `archive/data_import/import_groups.py`)
- **`circle_structure_placements.py`**: the parents the Python scripts give
  groups in the Circle Structure sheet, written to "Circle structure
  placements.json" for the `circle_agendas` Apps Script, and which kinds of
  group can be under which
- **`wiki_utils.py`**: per-group wiki pages
- **`folder_matching.py`**: fuzzy matching of group names to folder and
  Google Group names (and, including acronyms, to calendars, Zoom meetings,
  and events)
- **`meeting_rooms.py`**: finding, choosing, or creating each group's
  calendar and Zoom room (and deleting matching meetings you don't want),
  and keeping them named, shared, and configured
- **`calendar_announcement.py`**: the email telling a group about its new
  calendar and Zoom room, the admins' version for a community calendar, and
  the text it shares with `add_new_group.py`'s welcome email to admins
- **`calendar_event_moves.py`**: finding a group's meetings on other
  calendars, and copying them to its calendar or deleting them
- **`zoom_utils.py`**: the Zoom API, the standard room settings, and which
  Zoom users own rooms
- **`credentials.py`**: reading `~/.gather` and `zoom_credentials`
- **`member_names.py`**: members' short names by email address, from the Gather
  directory, written to `admin@`'s Drive for the circle agenda docs
- **`circle_agenda_docs.py`**: creating a circle's or working group's agenda
  doc, empty and labelled for the `circle_agendas` Apps Script to build

### `archive/`: no longer in use

#### `archive/data_import/`

- **`import_directory.py`** created households and members in Gather from a
  spreadsheet of community members.
- **`parse_households.py`** grouped the members spreadsheet into households;
  it was used by `import_directory.py`, and could be run alone to check the
  grouping.
- **`import_groups.py`** created Gather groups from the circle spreadsheet and
  built the Circle Structure page from it.
- **`import_photos.py`** added profile photos to Gather members from the
  community website.
- **`sync_group_memberships_from_sheet.py`** set each member's Gather group
  memberships to match the circles listed for them in the member roster.

#### `archive/google_setup/`

- **`match_google_groups_to_drive_folders.py`** matched existing Google Groups
  to Shared Drive folders and renamed them to the standard naming scheme.
- **`init_google_groups_from_gather_gdrive_config.py`** created a Google Group
  for each folder linked in Gather, wrote its footer link, and set it as the
  Gather group's email list.
- **`find_drive_folders_for_groups_without_mailing_list.py`** searched the
  Shared Drive for folders matching groups that had no email list, and linked
  the ones you picked.

#### `archive/circle_agendas/`

- **`agenda_probe.gs`** held tests, run by hand before the circle agenda
  docs were built, of what Apps Script can do to an agenda doc, each on a
  copy of Sociocracy For All's template: copying agenda rows and whole
  meetings (from the same tab, another tab, or another doc), adding an
  attendance checklist, inserting person and date chips, and adding tabs.
  Each reported which smart chips and checked boxes survived. See
  `archive/circle_agendas/README.md`.

#### `archive/cleanup/`

- **`cleanup_wiki.py`** removed the wiki links an older version of the group
  import had added to group descriptions, and deleted the per-circle wiki pages
  it had created.
- **`delete_mailman_lists.py`** deleted the Gather-hosted (Mailman) email lists
  from every group, before the switch to Google Groups.
- **`remove_drive_group_permissions.py`** removed `@berkeleymoshav.org` group
  permissions from every folder in a Shared Drive. It used its own Google
  sign-in, saved to `~/.google_drive_token.json`.

## Design notes

- `calendar-notifications-plan.md`: design of the calendar notifications
  script
- `meeting-infrastructure-plan.md`: decisions behind the group calendars,
  community calendars, Zoom rooms, and `group_meetings`, and the meeting
  features not built yet (recording archive, and more)
- `community-calendar-admins-guide.md`: the guide for community calendars'
  admins (not a design note: it's to be made into a Google Doc)
- `circle-agendas-plan.md`: design of the circle agenda docs, and what
  the tests found
- `archive/gather-sandbox-plan.md` and `archive/gather-import-plan.md`: plans
  for the local Gather test instance and the member import

## Tests

```bash
python -m pytest
```

The tests of `group_meetings/event_rules.gs` and
`circle_agendas/agenda_rules.gs` run them under Node, and are
skipped if `node` isn't installed.
