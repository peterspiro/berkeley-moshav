# berkeley-moshav

Administration scripts for the Berkeley Moshav community's online setup.
They keep four things linked together and consistent:

- **Gather groups** at [berkeley-moshav.gather.coop](https://berkeley-moshav.gather.coop)
  (circles, working groups, and clubs, with their members and managers)
- **Google Drive** folders and shared drives in the community's Shared Drive,
  linked to Gather groups through Gather's Google Drive settings (`/gdrive/config`)
- **Google Groups** in the `berkeleymoshav.org` Workspace, which serve as each
  Gather group's email list
- The **Circle Structure** wiki page in Gather, a tree of the circles and working
  groups with links to each one's members, documents, and wiki page

The repo also holds the one-off scripts used to migrate the community into
Gather (members, households, groups, photos), cleanup scripts from that
migration, and two Google Apps Script projects that run on a schedule inside
Google Workspace.

Gather has no API for any of this, so most scripts drive its web UI with
[Playwright](https://playwright.dev/python/). Google is reached through its
APIs.

## How the pieces fit together

```
Gather group members ──(Gather's Drive sync)──▶ folder Content managers / shared-drive Viewers
                                                   │
                                (groups_drive_sync.gs, hourly)
                                                   ▼
                                          Google Group (email list)
```

Each Google Group's custom footer carries an auto-managed block linking to its
Gather group and its Drive item. `groups_drive_sync.gs` reads that footer to
know which item's permissions to copy into the group, so a new group starts
syncing as soon as its footer is written, with no redeploy.

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
  enable the Drive, Admin SDK (Directory), and Groups Settings APIs. On first
  run, a script prints a sign-in URL; open it signed in as a Workspace
  super-admin. The sign-in is saved to `~/.google_setup_token.pkl`.

Run scripts from the repo root, e.g. `python add_new_group.py ...` or
`python google_setup/match_google_groups_to_drive_folders.py ...`. Every script
supports `-h` for its options, and most support `-n`/`--dry-run`. Logs and
screenshots go to `debug/` (git-ignored).

## Directories

| Directory | Used for |
|---|---|
| `.` (root) | The scripts used day to day to create and maintain groups, plus setup files and design notes |
| `data_import/` | One-off scripts that populated Gather from the community's spreadsheets and website |
| `google_setup/` | One-off scripts that set up the Google Groups and Drive links for the existing groups |
| `cleanup/` | One-off scripts that undid earlier approaches (old wiki pages, Mailman lists, Drive group permissions) |
| `groups_drive_sync/` | Apps Script project that syncs each Google Group's membership from its Drive item |
| `calendar_notifications/` | Apps Script project that announces and gatekeeps events on the shared community calendar |
| `util/` | Shared Python modules used by the scripts |
| `tests/` | Unit tests (`pytest`) |
| `debug/` | Created at run time for logs and screenshots; not in git |

## Scripts

### Root: group creation and maintenance

- **`add_new_group.py`** creates a complete new group in one step: the Drive
  folder, the Google Group (optionally with a shorter address), and the Gather
  group with its manager; links them all together; adds the group to the
  Circle Structure page; and opens a welcome email to the new list as a draft
  in your mail program.
- **`update_groups_in_google_and_hierarchy.py`** brings every group (or one,
  with `-g`) into line: offers to find or create a missing Drive folder or
  email list, enforces the Google Group settings, writes the footer links,
  and updates the Circle Structure page (renames, links, missing and stale
  entries).
- **`audit_group_memberships.py`** is a read-only report of where a group's
  Gather membership, Drive permissions, and Google Group membership disagree,
  plus setup problems and anyone else with access to its Drive item.
- **`add_group_wiki_page.py`** creates a wiki page for one group and adds a
  `[Wiki]` link to its row on the Circle Structure page.
- **`change_group_email_alias.py`** gives a group's Google Group a new, usually
  shorter, address and moves the Gather group's email list over to it.
- **`setup.sh`** installs and starts a local Gather development instance, for
  trying scripts out safely before running them against the real site.

### `data_import/`

- **`import_directory.py`** creates households and members in Gather from a
  spreadsheet of community members.
- **`parse_households.py`** groups the members spreadsheet into households;
  used by `import_directory.py`, and runnable alone to check the grouping.
- **`import_groups.py`** creates Gather groups from the circle spreadsheet and
  builds the Circle Structure page from it.
- **`import_photos.py`** adds profile photos to Gather members from the
  community website.
- **`sync_group_memberships_from_sheet.py`** sets each member's Gather group
  memberships to match the circles listed for them in the member roster.

### `google_setup/`

- **`match_google_groups_to_drive_folders.py`** matched existing Google Groups
  to Shared Drive folders and renamed them to the standard naming scheme.
- **`init_google_groups_from_gather_gdrive_config.py`** created a Google Group
  for each folder linked in Gather, wrote its footer link, and set it as the
  Gather group's email list.
- **`find_drive_folders_for_groups_without_mailing_list.py`** searched the
  Shared Drive for folders matching groups that had no email list, and linked
  the ones you picked.

### `cleanup/`

- **`cleanup_wiki.py`** removed the wiki links an older version of the group
  import added to group descriptions, and deleted the per-circle wiki pages it
  created.
- **`delete_mailman_lists.py`** deleted the Gather-hosted (Mailman) email lists
  from every group, before the switch to Google Groups.
- **`remove_drive_group_permissions.py`** removed `@berkeleymoshav.org` group
  permissions from every folder in a Shared Drive. It uses its own Google
  sign-in, saved to `~/.google_drive_token.json`.

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
- **`credentials.py`**: reading `~/.gather`

## Design notes

- `gather-sandbox-plan.md` and `gather-import-plan.md`: plans for the local
  Gather test instance and the member import
- `calendar-notifications-plan.md`: design of the calendar notifications
  script

## Tests

```bash
python -m pytest
```
