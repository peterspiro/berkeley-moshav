# Maintenance Tracker

Apps Script that does what happens to the Maintenance & Warranty Circle's
Airtable base ("Building Maintenance") without anyone doing it. It runs as
`admin@berkeleymoshav.org`. `../maintenance-tracker-plan.md` is the design;
`../maintenance/README.md` describes the base.

- **Every 10 minutes**, or every 2 hours on Airtable's Free plan (see "The
  Airtable plan") (`processRequests`):
  - **A new request** is acknowledged. If its reporter typed their details
    and the email address is a member's (either of their addresses in
    People), Reporter is linked to them. If "In my unit" is ticked and no
    location was chosen, Location is set to the reporter's unit. The
    reporter is emailed that it arrived, with its number, what they said, the
    status page, and their own link to the form. If it's an emergency or
    urgent, the circle's list is emailed at once, with the reporter's phone
    number and what they said about access.
  - **A request given a visit time** is scheduled: filling in Visit is all
    anyone has to do, and the script sets Status to Scheduled itself, if
    the request is open and the time is still to come. (A time left on a
    request that's since been moved back from Scheduled doesn't schedule
    it again; a new time does.)
  - **A request whose Status changed**, or whose visit was moved: the change
    is added to Updates ("Status changed from Triaged to Scheduled…"); the
    status page's wording (Member-visible status) is set, or cleared for a
    request that's Duplicate or Declined; Closed on is filled in when it's
    closed; and the reporter is emailed when a visit is scheduled or moved
    (once Visit has a time), when the work is done ("is it fixed?"), and
    when it's closed, unless they were just asked whether it's fixed.
  - **An update marked "Tell the reporter"** is emailed to the request's
    reporter, once (on the Free plan, within 6 hours).
- **Every day**, at about 3 am, or every Sunday on the Free plan
  (`housekeeping`):
  - **Form links** (`writeFormLinks`): each person's own link to the request
    form, which fills in their name and unit, is written to Request form
    link in People, and rewritten when their name or unit changes. Someone
    who's moved out has none.
  - **Backup** (`backup`): every table is saved as a JSON file in a folder
    named for the day, and each photo and attachment not yet copied is
    saved to an "Attachments" folder (Airtable's own links to them expire),
    up to 100 a run. This is the evidence file if a claim is disputed. It
    goes in a folder the script makes for itself, "Building Maintenance
    backup", in `admin@`'s My Drive (see "The backup folder").
  - **How full the base is**: having read every table for the backup, it
    emails the circle's list when the base's records, or its photos and
    attachments, pass 70%, 85%, and 95% of what the plan allows, once for
    each. When either is full, Airtable accepts no more, and the request
    form stops working for members.

Emails go from `admin@`, named "Berkeley Moshav Maintenance", with replies
going to the circle's list. A member is emailed at the address Gather has
for them; someone who typed their details, at the address they typed.

Each record carries the script's own note of what's been done to it:
Acknowledged on and Status noted in Requests, Told reporter on in Updates.
Don't edit those. To have the script treat a request as new again, clear
its Acknowledged on.

Not built yet: the weekly digest and patterns, claim deadlines, the
maintenance calendar, and the check that the base's views are all there
(run `python maintenance_base.py diff` for that).

## Files

- `maintenance_tracker.gs`: the timers, and everything that calls Airtable,
  Gmail, and Drive.
- `tracker_rules.gs`: the rules and the emails' wording, as pure functions.
  `tests/test_maintenance_tracker.py` runs both files under Node, against
  stand-ins for Airtable, Gmail, and Drive.

## Script Properties

Set in the Apps Script editor, under Project Settings. None is in the repo,
which is public.

| Property | What it is |
|---|---|
| `AIRTABLE_TOKEN` | A personal access token for the base, with only the scopes `data.records:read` and `data.records:write` |
| `AIRTABLE_BASE_ID` | The base's ID: the part of its address in Airtable that starts with `app` |
| `AIRTABLE_PLAN` | `team` once the workspace is on Airtable's Team plan. Unset, or anything else, means the Free plan (see "The Airtable plan") |
| `CIRCLE_EMAIL` | The circle's email list: urgent requests go there, and replies to every email |
| `REQUEST_FORM_URL` | The published request form's link. Optional: without it, no form links are written |
| `STATUS_PAGE_URL` | The public link to the Requests table's "Status page" view, which members open to see where requests stand: in Airtable, open that view, choose **Share and sync**, and create a link to the view. Optional: emails then leave it out |
| `TRIAGE_URL` | The Triage view's address in Airtable, for the circle's alerts. Optional |
| `EMERGENCY_NOTE` | A line added to the end of each acknowledgment, saying what to do in an emergency. Optional |
| `SEND_ALL_MAIL_TO` | For trying it out: while it's set, every email goes to this address instead, with who it was for in the subject. Delete it to go live |

## What it's allowed to do

`appsscript.json` asks for as little as the script can run with:

| Permission | Why |
|---|---|
| Connect to an external service | To call Airtable. `urlFetchWhitelist` narrows it to Airtable's API and the host its attachments are downloaded from, so the script can't reach anywhere else |
| Send email as you | To send the emails. It can't read mail |
| See, edit, create, and delete only the specific Google Drive files you use with this app | For the backup: only files the script made |
| Allow this application to run when you are not present | For the timers `installTriggers` sets |

If the backup starts failing to copy attachments with an error about a
URL that isn't allowed, Airtable has moved them to another host: add it to
`urlFetchWhitelist`, as the address in the error up to and including the
first single `/`.

## The backup folder

The script asks for permission only for the Drive files it creates itself,
not for the rest of `admin@`'s Drive. So it can't be pointed at a folder a
person made: the first time `backup` runs, it makes "Building Maintenance
backup" at the top of `admin@`'s My Drive, and remembers it (in the Script
Property `backupFolderId`, which it sets itself).

The folder holds every table in full: members' contact details, photos
taken in their homes, and the circle's notes. Leave it where it is, shared
with nobody, or with the particular people who need it. Don't move it into
the community's Shared Drive, which every member can view. It can be
renamed, or moved within My Drive.

To start a fresh backup, as when changing from the test base to the real
one, delete the `backupFolderId` property; the next run makes a new folder
of the same name, so rename or delete the old one first.

## Deploying

Once, signed in to `clasp` as `admin@berkeleymoshav.org`:

```bash
cd maintenance_tracker
```

```bash
clasp create --type standalone --title "Maintenance Tracker"
```

That writes `.clasp.json`, naming the new project; commit it, as the other
projects' are. It also replaces `appsscript.json` with a blank one, which
lacks the permissions the script needs: put the repo's back with
`git restore appsscript.json`. Then, and after every change:

```bash
clasp push
```

Then in the Apps Script editor:

1. Set the Script Properties above. Start with the test base and
   `SEND_ALL_MAIL_TO` set to your own address.
2. Run `processRequests` by hand, and approve what it asks permission for.
   Each request in the base that hasn't been acknowledged gets its email
   (to you, while `SEND_ALL_MAIL_TO` is set).
3. Run `housekeeping` by hand, to check the form links and the backup (look for
   "Building Maintenance backup" in `admin@`'s My Drive).
4. Run `installTriggers` to start the timers, at the pace the plan allows.

To move to the real base, change `AIRTABLE_TOKEN` and `AIRTABLE_BASE_ID`
(and the form, status page, and Triage links, which are the real base's
own), delete `backupFolderId` (see "The backup folder"), and delete `SEND_ALL_MAIL_TO` when the circle is ready for members to
be emailed.

## The Airtable plan

Airtable's Free plan allows a workspace 1,000 calls to its API a month,
every base and script together, and a base 1,000 records and 1 GB of
attachments. The script keeps inside the first and warns of the others.
What it does on each plan (`PLANS` in `tracker_rules.gs`):

| | Free (`AIRTABLE_PLAN` unset) | Team (`AIRTABLE_PLAN` = `team`) |
|---|---|---|
| Looks for new requests and changes of status | every 2 hours | every 10 minutes |
| Looks for updates to pass on | every 6 hours | every 10 minutes |
| Form links, backup, and the check of how full the base is | Sundays | daily |
| Warns the circle at 70%, 85%, 95% of | 1,000 records, 1 GB | 50,000 records, 20 GB |

On the Free plan, then, a reporter's acknowledgment and the circle's alert
about an urgent request can come up to 2 hours after the request. The
form's own page should say what to do in an emergency.

With nothing happening, the Free pace is about 540 calls a month. Each new
request or change of status adds one or two, and the first of them in any
6 hours two more (People and Locations are then kept for 6 hours, so a
change made to them in Airtable can take that long to be noticed).
`maintenance_base.py`, `sync_maintenance_people.py`, and anything done to
the test base, if it's in the same workspace, come out of the same 1,000.
Running a function by hand costs the same as the timer running it.

A request uses more than one record: the script adds a row to Updates for
each change of status, so allow five to seven records a request.

**When the workspace moves to the Team plan**, set `AIRTABLE_PLAN` to
`team` and run `installTriggers` again.
