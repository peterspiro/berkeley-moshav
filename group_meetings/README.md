# Group Meetings

Apps Script that looks after every group's Google calendar (the calendars
`update_groups_in_google_and_hierarchy.py` creates):

- **Every 5 minutes**, for each changed event on a group calendar:
  - an event the group calendar organizes gets the group's Zoom room (link in
    the location; link, meeting ID, passcode, and which Zoom user owns the
    room (its number, `1` for `zoomhosts@berkeleymoshav.org` or `2` for
    `berkeley@urbanmoshav.org`, and its name in Zoom, so anyone looking at
    the all-events calendar can tell which meetings can't overlap) in the
    description, with a
    line linking the "BM Meetings FAQ" just above them, unless the
    description already links it; a "Zoom link to follow" line in the
    description is removed), and the group's Google Group and the "All
    Berkeley Moshav Events" calendar as guests. The group's members get an
    invitation; adding the room details or the all-events calendar sends no
    email. Events marked `[no zoom]`, or with another Zoom or Meet link typed
    in, keep their own link and get no FAQ line.
  - an event that's only a copy of someone's invitation (made on their own
    calendar, with the group calendar invited) can't be changed that way, so
    its organizer and `calendaring-alerts@berkeleymoshav.org` get one email
    asking them to create it on the group calendar instead.
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
  scheduled directly in Zoom, asked to agree too.

Which calendar and room are a group's comes from the group's email footer,
read hourly, so new groups need no redeploy. When a group's room link changes
(say, after `reset_zoom_room.py`), or its room moves to the other Zoom user,
or that user is renamed in Zoom, its upcoming events are updated within the
hour.

- `group_meetings.gs` — triggers, Calendar/Groups/Zoom calls, emails
- `event_rules.gs` — the rules themselves, with no Apps Script calls, tested
  under Node by `tests/test_group_meetings_rules.py`
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
for this, to find every Zoom user's scheduled meetings (see the main
README.md).

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
every upcoming event once, sending no email.

## Smoke test

1. On a test group's calendar (one whose footer has calendar and Zoom links),
   create an event. Within 5 minutes it should show the Zoom link as its
   location, the FAQ line and the Zoom details in its description, and the
   group and the all-events calendar as guests; the group's members should
   get an invitation.
2. Create an event on your own calendar and invite the test group's calendar:
   you and `calendaring-alerts@` should get one email about it.
3. Create two overlapping events on group calendars: `calendaring-alerts@` and
   both groups should get one overlap email. Then move one so they're 10
   minutes apart: one "too close together" email. At 15 minutes apart or
   more: no email.

Run **`syncAll`** or **`hourly`** from the editor to force a pass, and read
**Executions** for the log.
