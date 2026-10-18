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
  Moshav Events" calendar for every meeting.

Two Google Apps Script projects run inside Google Workspace alongside the
Python scripts:

- **`groups_drive_sync`** keeps each Google Group's membership in step with its
  Drive folder or shared drive, hourly, and emails a report of any changes.
- **`calendar_notifications`** looks after the shared community calendar: it
  emails a community group when one of its members adds an event, and removes
  invitations from anyone who isn't allowed to add events.

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
```

The footer is the only record of which calendar and Zoom room belong to a
group; `update_groups_in_google_and_hierarchy.py` and `add_new_group.py`
create them and write the links. Each calendar is shared with the group's
Google Group as "Make changes to events", so membership of the Google Group
(kept in step with Drive above) decides who can use it.

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
  enable the Drive, Admin SDK (Directory), Groups Settings, and Google
  Calendar APIs. On first run, a script prints a sign-in URL; open it signed
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
  | `meeting:read:list_meetings:admin` | View a user's meetings | Listing the rooms, to offer ones matching a group's name |
  | `meeting:read:list_past_instances:admin` | View a past meeting's instances | Warning about rooms unused for 300 days, before Zoom deletes them at 365 |
  | `user:update:user:admin` | Update a user | New host keys (`reset_zoom_room.py --host-key`) only |

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

Run scripts from the repo root, e.g. `python add_new_group.py ...` or
`python archive/data_import/parse_households.py ...`. Every script
supports `-h` for its options, and most support `-n`/`--dry-run`. Logs and
screenshots go to `debug/` (git-ignored).

## Directories

| Directory | Used for |
|---|---|
| `.` (root) | The scripts used day to day to create and maintain groups, plus setup files and design notes |
| `groups_drive_sync/` | Apps Script project that syncs each Google Group's membership from its Drive item |
| `calendar_notifications/` | Apps Script project that announces and gatekeeps events on the shared community calendar |
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
  and Zoom room (asking which one is the group's when existing ones match its
  name), writes the footer links, and updates the Circle Structure page
  (renames, links, missing and stale entries). Its first run also creates the
  "All Berkeley Moshav Events" calendar.
- **`reset_zoom_room.py`** gives a group's Zoom room a new passcode, and so a
  new link, which it writes into the group's footer, for when a link has
  leaked. With `--host-key` it instead (or also) sets a new host key for
  every group room.
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

Both are deployed to Google with [`clasp`](https://github.com/google/clasp)
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
  Google Group names
- **`meeting_rooms.py`**: finding, choosing, or creating each group's
  calendar and Zoom room, and keeping them named, shared, and configured
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
- `archive/gather-sandbox-plan.md` and `archive/gather-import-plan.md`: plans
  for the local Gather test instance and the member import

## Tests

```bash
python -m pytest
```
