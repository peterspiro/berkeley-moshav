# Maintenance Tracker

Apps Script that does what happens to the Maintenance & Warranty Circle's
Airtable base ("Building Maintenance") without anyone doing it. It runs as
`admin@berkeleymoshav.org`. `../maintenance-tracker-plan.md` is the design;
`../maintenance/README.md` describes the base.

- **Every 10 minutes** (`processRequests`):
  - **A new request** is acknowledged. If its reporter typed their details
    and the email address is a member's (either of their addresses in
    People), Reporter is linked to them. If "In my unit" is ticked and no
    location was chosen, Location is set to the reporter's unit. The
    reporter is emailed that it arrived, with its number, what they said, the
    status page, and their own link to the form. If it's an emergency or
    urgent, the circle's list is emailed at once, with the reporter's phone
    number and what they said about access.
  - **A request whose Status changed**, or whose visit was moved: the change
    is added to Updates ("Status changed from Triaged to Scheduled…"); the
    status page's wording (Member-visible status) is set, or cleared for a
    request that's Duplicate or Declined; Closed on is filled in when it's
    closed; and the reporter is emailed when a visit is scheduled or moved
    (once Visit has a time), when the work is done ("is it fixed?"), and
    when it's closed, unless they were just asked whether it's fixed.
  - **An update marked "Tell the reporter"** is emailed to the request's
    reporter, once.
- **Every day**, at about 3 am (`daily`):
  - **Form links** (`writeFormLinks`): each person's own link to the request
    form, which fills in their name and unit, is written to Request form
    link in People, and rewritten when their name or unit changes. Someone
    who's moved out has none.
  - **Backup** (`backup`): every table is saved as a JSON file in a folder
    named for the day, and each photo and attachment not yet copied is
    saved to an "Attachments" folder (Airtable's own links to them expire),
    up to 100 a run. This is the evidence file if a claim is disputed.

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
| `CIRCLE_EMAIL` | The circle's email list: urgent requests go there, and replies to every email |
| `REQUEST_FORM_URL` | The published request form's link. Optional: without it, no form links are written |
| `STATUS_PAGE_URL` | The Status page view's shared link. Optional: emails then leave it out |
| `TRIAGE_URL` | The Triage view's address in Airtable, for the circle's alerts. Optional |
| `EMERGENCY_NOTE` | A line added to the end of each acknowledgment, saying what to do in an emergency. Optional |
| `BACKUP_FOLDER_ID` | The ID of the Drive folder the backup goes in. Optional: without it, nothing is backed up |
| `SEND_ALL_MAIL_TO` | For trying it out: while it's set, every email goes to this address instead, with who it was for in the subject. Delete it to go live |

## Deploying

Once, signed in to `clasp` as `admin@berkeleymoshav.org`:

```bash
cd maintenance_tracker
```

```bash
clasp create --type standalone --title "Maintenance Tracker"
```

That writes `.clasp.json`, naming the new project; commit it, as the other
projects' are. Then, and after every change:

```bash
clasp push
```

Then in the Apps Script editor:

1. Set the Script Properties above. Start with the test base and
   `SEND_ALL_MAIL_TO` set to your own address.
2. Run `processRequests` by hand, and approve what it asks permission for.
   Each request in the base that hasn't been acknowledged gets its email
   (to you, while `SEND_ALL_MAIL_TO` is set).
3. Run `daily` by hand, to check the form links and the backup.
4. Run `installTriggers` to start the timers.

To move to the real base, change `AIRTABLE_TOKEN` and `AIRTABLE_BASE_ID`
(and the form, status page, and Triage links, which are the real base's
own), and delete `SEND_ALL_MAIL_TO` when the circle is ready for members to
be emailed.

## How much it asks of Airtable

A run with nothing to do makes two calls to Airtable's API; one with work
to do makes about four more, plus one or two for each request or update.
At every 10 minutes that's some 9,000 calls a month, and the backup adds a
dozen a day. Airtable's Free plan is reported to allow 1,000 calls a month,
so on that plan run it less often (change `everyMinutes(10)` in
`installTriggers` to `everyHours(1)`, and expect to go over even so) or by
hand; the plan the real base needs has no such limit.
