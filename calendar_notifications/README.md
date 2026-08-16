# Calendar Notifications

Apps Script that watches the admin account's primary calendar (aliased by
`share@berkeleymoshav.org`), emails a community Google Group when one of its
members adds an event, and removes invitations from anyone who isn't allowed to
add events. See `../calendar-notifications-plan.md` for the full design.

- `calendar_notifications.gs` — the script
- `appsscript.json` — advanced services + OAuth scopes

## Deploy with clasp

[`clasp`](https://github.com/google/clasp) pushes these files into an Apps
Script project and keeps them in sync with the repo.

**Everything must run as the admin account** (the one with the `share@` alias),
since the script needs both that account's calendar and Admin SDK access. Sign
in as the admin for every step below.

```bash
# 1. Install clasp
npm install -g @google/clasp

# 2. Authorize as the ADMIN account (opens a browser)
clasp login

# 3. From this folder, create a standalone project.
#    NOTE: clasp create pulls the new (blank) project's manifest and OVERWRITES
#    the local appsscript.json with a bare default — losing our advanced
#    services and scopes. Restore ours in step 4 before pushing.
cd calendar_notifications
clasp create --type standalone --title "Calendar Notifications"

# 4. Restore our manifest, then push the .gs + appsscript.json.
#    -f skips the "manifest changed" confirmation prompt.
git checkout -- appsscript.json
clasp push -f

# 5. Open the project, then run installTrigger() once (see below)
clasp open
```

`clasp create` writes a `.clasp.json` (containing the new project's script ID)
into this folder. **Don't commit it** — it's per-deployment, not source. Add it
to `.gitignore`:

```bash
echo "calendar_notifications/.clasp.json" >> ../.gitignore
```

### One-time authorization + install

`clasp` can push code but can't grant OAuth consent or run a function
interactively. After `clasp open`:

1. In the editor's function dropdown, select **`installTrigger`** and click
   **Run**.
2. Approve the authorization prompt (calendar, directory-read, send email,
   manage triggers).

`installTrigger()` seeds the sync token (so pre-existing events aren't
retro-notified) and registers the `onEventUpdated` trigger. Confirm it under the
**Triggers** (⏰) panel: one `onCalendarUpdate` trigger, source "From calendar."

### Updating later

After editing the `.gs` locally, redeploy with:

```bash
clasp push
```

The trigger persists across pushes — no need to re-run `installTrigger()` unless
you deleted the trigger or changed `CALENDAR_ID`.

## Account settings this depends on

Two Workspace settings (not code) must be in place, or nothing is delivered:

- **Calendar sharing** — the admin's primary calendar must be shared with the
  community group as **"See all event details"** (ACL role `reader`). The script
  reads the ACL to decide which group to notify.
- **Group posting** — the community group must allow the admin to post
  (Group settings → *Who can post*), or notification email bounces.

## Smoke test

1. From a Gmail account that **is** a member of the reader group, create an event
   and invite `share@berkeleymoshav.org` → expect a notification to the group
   within a minute or two.
2. From an address that is **not** a member, do the same → the invitation should
   be removed from the calendar and produce no email.

Run **`processChanges`** manually from the editor to force a pass, and read the
**Executions** log to see what happened.
