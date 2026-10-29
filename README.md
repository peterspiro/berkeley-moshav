# berkeley-moshav

Administration scripts for the Berkeley Moshav community's online setup.

Their main job is keeping these things linked together and consistent:

- **[Gather groups](https://berkeley-moshav.gather.coop/groups)** (circles,
  working groups, and clubs, with their members and managers)
- **Google Drive** folders and shared drives in the community's
  [Shared Drive](https://drive.google.com/drive/folders/0AFqC2xo9aTgPUk9PVA),
  linked to Gather groups through Gather's
  [Google Drive settings](https://berkeley-moshav.gather.coop/gdrive/config)
- **[Google Groups](https://groups.google.com/all-groups)** in the
  `berkeleymoshav.org` Workspace, which serve as each Gather group's email list
- The **[Circle Structure](https://berkeley-moshav.gather.coop/wiki/circle-structure)**
  wiki page in Gather, a tree of the circles and working groups with links to
  each one's members, documents, and wiki page
- Each group's **Google calendar**, owned by `admin@berkeleymoshav.org` and
  shared with the group's Google Group so its members can add meetings, and
  its permanent **Zoom meeting room**, owned by the
  `zoomhosts@berkeleymoshav.org` Zoom user. A room's link never changes, and
  meetings in it can run with no host present. There's also one "All Berkeley
  Moshav Events" calendar for every meeting, which everyone can view.

Three Google Apps Script projects run inside Google Workspace alongside the
Python scripts:

- **`groups_drive_sync`** keeps each Google Group's membership in step with its
  Drive folder or shared drive, hourly, and emails a report of any changes.
- **`calendar_notifications`** looks after the shared community calendar: it
  emails a community group when one of its members adds an event, and removes
  invitations from anyone who isn't allowed to add events.
- **`group_meetings`** looks after the groups' calendars: it gives each
  group's meetings its Zoom room and invites the group and the all-events
  calendar, and emails warnings about meetings that would clash over a Zoom
  user, and about meetings set up as invitations from someone's own calendar.

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

The meeting ID and passcode are for members who type them into Zoom instead
of clicking the link; since the link already lets anyone in, showing the
passcode exposes nothing more. Passcodes are six digits, to be easy to type
and read out.

The footer is the only record of which calendar and Zoom room belong to a
group; `update_groups_in_google_and_hierarchy.py` and `add_new_group.py`
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

All the group rooms share one host key: the `zoomhosts@berkeleymoshav.org`
Zoom user's. It's given privately to each group's facilitators, so they can
claim host in their group's room. Change it only when it has leaked or a
facilitator who knows it shouldn't any more, since every facilitator in every
group then needs the new one. It's changed by hand, not by a script: sign in
to the Zoom web portal as `zoomhosts@berkeleymoshav.org`, open **Profile**,
and edit **Host Key** (6 to 10 digits; Zoom rejects easy ones like 123456).


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
`update_groups_in_google_and_hierarchy.py` shows a restricted calendar in its
status line as "calendar (members can view, not edit)".

## Directories

| Directory | Used for |
|---|---|
| `.` (root) | The scripts used day to day to create and maintain groups, plus setup files and design notes |
| `groups_drive_sync/` | Apps Script project that syncs each Google Group's membership from its Drive item |
| `calendar_notifications/` | Apps Script project that announces and gatekeeps events on the shared community calendar |
| `group_meetings/` | Apps Script project that adds Zoom rooms and guests to group calendar events, and warns about overlapping meetings |
| `util/` | Shared Python modules used by the scripts |
| `tests/` | Unit tests (`pytest`) |
| `archive/` | One-off scripts and plans that are no longer in use, kept for reference |
| `archive/data_import/` | Scripts that were used to populate Gather from the community's spreadsheets and website |
| `archive/google_setup/` | Scripts that were used to set up the Google Groups and Drive links for the existing groups |
| `archive/cleanup/` | Scripts that were used to undo earlier approaches (old wiki pages, Mailman lists, Drive group permissions) |
| `debug/` | Created at run time for logs and screenshots; not in git |

## Scripts

### Root: group creation and maintenance

- **`add_new_group.py`** creates a complete new group in one step: the Drive
  folder, the Google Group (optionally with a shorter address), the Gather
  group with its manager, and the group's calendar and Zoom room; links them
  all together; adds the group to the Circle Structure page; and opens a
  welcome email to the new list as a draft in your mail program.
- **`update_groups_in_google_and_hierarchy.py`** brings every group (or one,
  with `-g`) into line: offers to find or create a missing Drive folder or
  email list, enforces the Google Group settings, gives each group a calendar
  and Zoom room, writes the footer links, and updates the Circle Structure page
  (renames, links, missing and stale entries). Its first run also creates the
  "All Berkeley Moshav Events" calendar, which it shares view-only with the
  everybody groups (with `-g`, only if `--share-all-events` is given, since
  finding them means reading every group from Gather). When it creates a group's calendar, it emails the
  group's list from `admin@` (replies going to `calendaring-alerts@`), once
  the footer links the calendar and Zoom room: a link for everyone to add
  the calendar, and steps for meeting organizers to move the group's
  meetings onto it. A dry run shows each email it would send.
  `--announce-calendar` sends it even when the calendar already existed
  (with `-g`, to one group; without, it asks before emailing every group).
  A group's calendar is named after its Gather group; with `-g`,
  `-f`/`--full-calendar-name` names it by the group's full name instead, ending
  with its type ("Technology Circle", "Garden Working Group"), creating or
  renaming it so. Later runs leave a calendar named that way as it is.
  Calendars are shared with groups without Google's
  "shared a calendar" email, which for a group goes to the group's address
  and has a button that does nothing for members; members add a calendar
  from the link in the group's footer.

  Calendars, Zoom meetings, and events match a group when their names are
  like the group's, or contain its acronym (for the "Development, Finance,
  & Legal Circle", "DF&L", "DFL", or "D.F.L."). When a group's footer links
  no calendar, it asks which of the matching calendars is the group's, or
  creates one. When it links no
  Zoom room, it lists every scheduled meeting in the Zoom account whose name
  matches the group's (any user's, of any kind, with its owner and schedule,
  and the matching calendar events held in it),
  then asks of each one in turn whether to use it as the room, ignore it, or
  delete it. Only a no-fixed-time meeting of `zoomhosts@` or
  `berkeley@urbanmoshav.org` (the second Zoom user) can be used, since Zoom
  can't change a meeting's type; choosing a second one to use
  asks to confirm the switch, and what to do with the first. Nothing changes
  until the end, when it shows what it will do and, if it will delete any
  meeting (whose link then stops working), asks you to type `delete`. Unless
  a meeting is used, it creates a room. A dry run asks the same questions but
  changes nothing. `add_new_group.py` does the same.

  When it creates a group's calendar, or for every group with
  `--move-events`, it looks for the group's meetings on other calendars:
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
  invitation is removed from that calendar only. As with Zoom meetings,
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
- **`add_group_wiki_page.py`** creates a wiki page for one group and adds a
  `[Wiki]` link to its row on the Circle Structure page.
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
  An event that's only a copy of someone's invitation gets its organizer an
  email asking them to create it on the group calendar instead. It also
  checks the next 8 weeks for meetings in rooms of the same Zoom user that
  overlap or are less than 15 minutes apart, including ones scheduled
  directly in Zoom, and emails the groups involved (and, for a meeting
  scheduled directly in Zoom, calendaring-alerts@berkeleymoshav.org, for the
  Technology Circle), asking them to agree which meeting moves, with a link to
  the all-events calendar for finding a free time (two groups are also offered
  having one group's room moved to the second Zoom user). Its rules are
  in `group_meetings/event_rules.gs`, tested under Node. See
  `group_meetings/README.md` for deployment, including the Zoom credentials
  it reads from Script Properties.

### `util/`: shared modules

- **`gather_utils.py`**: Gather login, page scraping and form helpers, the
  directory CSV, logging
- **`gdrive_config.py`**: Gather's `/gdrive/config` page, Shared Drive folder
  lookup and creation, and folder naming
- **`google_group_utils.py`**: Google sign-in, group naming and addresses,
  and Directory and Groups Settings API helpers
- **`google_group_footer.py`**: reading and writing the auto-managed footer
  links
- **`hierarchy_wiki.py`**: parsing, editing, and saving the Circle Structure
  page
- **`wiki_utils.py`**: per-group wiki pages
- **`folder_matching.py`**: fuzzy matching of group names to folder and
  Google Group names (and, including acronyms, to calendars, Zoom meetings,
  and events)
- **`meeting_rooms.py`**: finding, choosing, or creating each group's
  calendar and Zoom room (and deleting matching meetings you don't want),
  and keeping them named, shared, and configured
- **`calendar_announcement.py`**: the email telling a group about its new
  calendar and Zoom room
- **`calendar_event_moves.py`**: finding a group's meetings on other
  calendars, and copying them to its calendar or deleting them
- **`zoom_utils.py`**: the Zoom API, and the standard room settings
- **`credentials.py`**: reading `~/.gather` and `zoom_credentials`

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
  Zoom rooms, and `group_meetings`, and the meeting features not built yet
  (community-wide calendars, recording archive, and more)
- `archive/gather-sandbox-plan.md` and `archive/gather-import-plan.md`: plans
  for the local Gather test instance and the member import

## Tests

```bash
python -m pytest
```

The tests of `group_meetings/event_rules.gs` run it under Node, and are
skipped if `node` isn't installed.
