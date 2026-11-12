# Plan: Maintenance & warranty tracker (Airtable)

A design for the system the Maintenance & Warranty Circle will use to
track the new 36-unit building: members' repair requests, warranty claims,
subcontractor contacts, equipment, maintenance and inspection schedules, and
how-to and emergency knowledge. Nothing here is built yet.

It will be developed in Claude Code on the web, which shapes the design more
than anything else: see "What goes where" and "Developing it in Claude Code
on the web".

## Goals

- **One intake channel.** A member reports a problem, or asks how to work
  something, on one form, from a phone, tablet, or computer, with photos,
  without an account.
- **Evidence from day one.** Every defect is logged with its date, place,
  and photos, and what was said and done about it afterwards, in a record
  that survives a disputed claim (and survives Airtable).
- **Who is looking into what** is always visible: every open request has one
  owner and a status.
- **One contact per vendor.** Many volunteers share the work, but the
  builder and each subcontractor hear from one named person (Cambridge
  Cohousing's rule).
- **Claims are tracked to sign-off**, against each warranty's deadline.
- **Patterns show up**: the same component failing in several units, or
  along one plumbing stack, is noticed and claimed as one systemic problem.
- **Members can see status themselves**, without asking the circle.
- **Scheduled maintenance and inspections** come due on their own, including
  the inspection before the one-year warranty ends.

Not goals: dues, reserves, or budgets; replacing Gather (directory, meals,
groups); work orders for a property manager.

## What goes where

| Thing | Kept in | Why |
|---|---|---|
| Records (requests, claims, contacts, equipment, schedules) | Airtable | Relational, with forms, kanban and grouped views the circle can change themselves |
| Views, the member form, interfaces | Airtable, built by hand from a checklist | Airtable's API can't create them (to confirm, see "Phase 0") |
| The base's tables and fields | `maintenance/schema.json` in this repo, applied by a script | So a session can change the schema, review it as a diff, and try it on a test base |
| All logic: emails, reminders, calendar, pattern reports, backups | A `maintenance_tracker` Apps Script project, running as `admin@berkeleymoshav.org` | Airtable automations can't be kept in git, tested, or made by the API, and are rationed by plan; Apps Script is what the repo's other timers already use, and it can send mail and write calendars and Drive as `admin@` |
| Documents (O&M manuals, as-builts, warranties, claim letters) | The circle's Drive folder | The community's access rules already apply there; Airtable holds links |
| How-to and emergency guides | Google Docs in a "Building Handbook" folder everyone can view | Members already have Drive access; Airtable indexes them |
| Members and their units | Gather, copied to Airtable | Gather stays the directory |
| The circle, its email list, folder, and calendar | A Gather group made by `add_new_group.py`, like every other | Its Google Group is where alerts go |

The rule: **Airtable holds data and the screens people use; everything that
acts on the data is code in this repo.** No Airtable automations, and no
formulas that carry logic a test should cover.

## The base

One base, "Building Maintenance". Ten tables. Field names here are the ones
the schema file will use; select options are listed where they matter.

### Locations

One row per unit and per common area (roof, garage, each corridor, common
house rooms, mechanical rooms, site).

- `Name` ("Unit 304", "Roof"), `Kind` (Unit / Common), `Floor`
- `Stack`: the vertical line of units sharing risers ("04"). This is what
  lets leaks be grouped up and down the building.
- `Plan type` (units with the same layout share fixtures and so defects)
- `Closing date`: when the unit's warranty clock started, if it runs per unit

### People

Members and circle volunteers, copied from Gather's directory (see
"Links to the rest of the setup"). `Name`, `Email`, `Phone`, `Location`
(link), `Circle member` (checkbox), `Gather ID`.

Owners and contacts are links to People, not Airtable's collaborator field,
so a volunteer can own a request without holding a paid Airtable seat.

### Requests

Everything a member reports or asks. One row per problem per place.

- `ID` (autonumber, shown as R-0123), `Description` and `Photos` (the
  member's own words and pictures)
- `Title`: written at triage, not by the member, because it's the only
  description the status page shows. It names the thing and the fault
  ("Bath fan rattles"), never a person, a health matter, or when anyone is
  away. Until triage it's empty, and the status page shows only that the
  request was received.
- `Location` (link), `Room` (Kitchen, Bath, Bedroom, Living, Entry, Balcony,
  Laundry, n/a)
- `Type`: Repair or defect / How do I…? / Suggestion
- `System`: Plumbing, HVAC & ventilation, Electrical & lighting, Windows &
  exterior doors, Interior doors & hardware, Appliances, Cabinets &
  counters, Floors, Walls & paint, Roof & envelope, Fire & life safety,
  Elevator, Access & intercom, Site & landscape, Other
- `Component` (link to a short Components list kept in Assets, see below;
  filled in at triage, not by the member)
- `Urgency`: Emergency / Urgent (days) / Routine
- `Reporter name`, `Reporter email`, `Reporter phone` (typed on the form),
  `Reporter` (link to People, matched by email by the script)
- `May enter if I'm out` (checkbox), `Access notes`
- `Status`: New → Triaged → Waiting on builder / Waiting on vendor /
  Waiting on volunteer → Scheduled → Done, to confirm → Closed. Also
  Duplicate, Declined.
- `Owner` (link to People), `Responsibility`: Builder warranty /
  Manufacturer warranty / HOA / Unit owner / Unknown
- `Claim` (link), `Vendor` (link to Organizations), `Visit` (date and time)
- `Warranty` (link, set at triage) and `Claim by` (a date the script works
  out from it; see Warranties)
- `Duplicate of` (link to Requests), `Resolution`, `Closed on`
- `Submitted` (created time; never edited)
- `Member-visible status` (text the script writes; what the public view shows)

### Updates

The history of each request and claim: one row per thing that happened.
`Request` or `Claim` (link), `When`, `By` (link to People), `Kind` (Note /
Call / Email / Visit / Status change / Photo), `Text`, `Attachments`,
`Tell the reporter` (checkbox: the script emails it to the member).

A table rather than Airtable's comments, because comments need a seat, can't
be entered from a form, and don't export. The script adds a "Status change"
row whenever a request's or claim's status changes, so the timeline is
complete without anyone remembering to write it.

### Claims

One row per claim made to a warrantor. A claim can cover many requests (a
systemic problem) and a request can be re-claimed.

- `ID` (C-0012), `Title`, `Requests` (links), `Warranty` (link),
  `Warrantor` (link to Organizations)
- `Scope`: Unit / Common element / Systemic
- `Status`: Drafting → Submitted → Acknowledged → Scheduled → Work done →
  Accepted. Also Disputed, Denied, Withdrawn.
- `Submitted on`, `Submitted how`, `Sent to` (the person it went to),
  `Their reference`, `Acknowledged on`, `Response`, `Promised by`,
  `Work done on`, `Accepted by` (link to People), `Accepted on`
- `Claim by` (the earliest of its requests' dates, set by the script; the
  date that must not be missed), `Folder` (Drive link to the letter and
  evidence)

The builder has no claims process of its own, so the circle's record is the
process, and has to show by itself that notice was given and when. Every
claim goes in writing, by email from one address (the circle's list, or a
`warranty@` address) to the person the builder names, with the claim's
number in the subject and its requests and photos attached; the script
drafts that email from the claim's rows, and the sent message is filed in
the claim's folder. Ask the builder early, in writing, to name that person
and agree that email to them counts as notice (see "For the attorney").

### Warranties

Every warranty and statutory period, each with its own clock: the builder's
workmanship warranty, each manufacturer's (roof, HVAC, elevator,
appliances, windows), and the longer periods state law gives for particular
defects.

`Name`, `Warrantor`, `Held by` (Association / Each owner / Unknown),
`Covers` (System, and links to Assets), `Scope` (Unit / Common), `Clock
starts` (One date for the building / Each unit's closing / Unknown),
`Building start date`, `Term` (months), `Document` (Drive link), `How to
claim`.

It isn't known yet whether each warranty runs from one date for the
building or from each unit's closing, and the design doesn't need to know.
The deadline is worked out per request, by the script: the warranty's start
for that request's location, plus its term. While `Clock starts` is
Unknown, it uses the earliest date it could be (the building's date or the
unit's closing, whichever is first), so a warning can come early but never
late. When the answer is known, changing that one field moves every
deadline.

### Organizations

The builder, subcontractors, manufacturers, service vendors, consultants,
the attorney, utilities.

`Company`, `Role` (General contractor / Subcontractor / Manufacturer /
Service vendor / Consultant / Utility), `Trades` (the System list),
`Contact name`, `Phone`, `Email`, `After-hours phone`, `Licence`,
`Our contact` (link to People: the one person who deals with them),
`Under contract until`, `Notes`.

Contacts will be collected over time, so nothing requires these fields.
While `Our contact` is empty, the coordinator is the contact. The
coordinator will rotate, so who it is now is data, a `Coordinator` checkbox
on People, not something written into the script or the views.

### Assets

What needs maintaining, taken from the O&M manuals and the handover: each
piece of equipment, each kind of fixture or appliance fitted in the units
(one row per model, not per unit), and each shutoff.

`Name`, `Kind` (Equipment / Unit fixture or appliance / Shutoff / Building
element), `System`, `Location` (link; empty for per-unit models), `Make`,
`Model`, `Serial`, `Installed by` (link), `Warranty` (link), `Manual` (Drive
link), `Photo`, `Where exactly` (for shutoffs: "garage, north wall, red
handle"), `Guides` (links).

The unit-fixture rows double as the `Component` list for Requests ("Bath
exhaust fan, Panasonic FV-…"), which is what makes "seven requests about the
same fan model" countable.

### Maintenance plans and Maintenance work

**Maintenance plans** says what recurs: `Task`, `Asset` (link), `Kind`
(Maintenance / Inspection / Test), `Every` (months), `Season`, `Done by`
(Volunteers / Vendor, with the Organization), `Required by` (Warranty /
Code / Manufacturer / Good practice), `Procedure` (link to a Guide),
`Owner` (link to People), `First due`.

**Maintenance work** is each occurrence: `Plan` (link), `Due`, `Status` (Due
/ Scheduled / Done / Skipped), `Scheduled for`, `Done on`, `Done by`,
`Notes`, `Photos`, `Found` (links to Requests raised by what was seen).

The script creates the next occurrence when one is done, and the first from
`First due`. The independent inspection in month 10 or 11 is a plan like
any other, as is each warranty's "claim by" review.

### Guides

An index of the handbook docs: `Title`, `Kind` (Emergency / How to /
Maintenance procedure), `Doc` (link), `Video` (link), `Systems`, `Assets`
(links), `Reviewed on`, `Requests answered` (links to the "How do I…?"
requests it settled, which shows what's worth writing next).

## What people use

**Members**

- The **request form** (an Airtable form on Requests), linked from Gather,
  the building handbook, and a QR code in each unit and the common house.
  It asks for name, email, phone, location, room, type, system, urgency,
  description, photos, and permission to enter. Its first lines say what to
  do in an emergency *instead of* filling in a form (shut off the water,
  call the after-hours number) and link the emergency guide.
  It picks the location from a Locations view, but takes the reporter's name
  as typed: linking to People would show the member list to anyone with the
  form's link.
- An **acknowledgment email** with their request's number, and an email
  whenever an update is marked "Tell the reporter", a visit is scheduled, or
  it's closed (asking them to reply if it isn't fixed).
- The **status page**: a read-only shared view of open and recently closed
  requests showing number, location, system, title, member-visible status,
  and dates. No reporter, no description, no photos, no internal notes.
  Anyone with its link can open it, which is accepted for now. What it
  shows of a request is the triager's `Title` and the script's
  `Member-visible status`, never anything a member typed, so personal
  details in a description don't reach it. The unit number beside a fault
  is the exposure that remains.
- The **Building Handbook** folder, and laminated one-page emergency cards
  in each unit and at each shutoff, printed from its docs.

**The circle**

- **Triage** (New requests, oldest first): set System, Component,
  Responsibility, Owner; mark duplicates. A named coordinator does this
  weekly, and the same day for anything urgent.
- **Board**: kanban of open requests by Status. **Mine**: by Owner.
- **Waiting on builder**: claims and requests by age, with promised dates.
- **Patterns**: all requests grouped by System and Component, and by Stack.
- **Calendar**: visits and maintenance work.
- **Contacts**: Organizations by trade, with after-hours numbers.
- A **quick update form** (on Updates), for a volunteer on a phone, or
  without a seat, to log a call or a visit against a request.
- The **weekly digest** email to the circle's list: new requests, ones
  with no owner, ones not touched in 14 days, claims past their promised
  date, warranties expiring in 90 days, maintenance due, and patterns (below).

## The `maintenance_tracker` Apps Script

One project, on a 10-minute timer and a daily one, as `admin@`. It reads and
writes Airtable with `UrlFetchApp` and a token kept in Script Properties.
Like `group_meetings`, its decisions are pure functions in a
`tracker_rules.gs` tested under Node; the `.gs` file that calls Airtable,
Gmail, Calendar, and Drive stays thin.

Every 10 minutes:

- **New requests**: match the reporter to People by email; email the
  acknowledgment; for Emergency or Urgent, email the circle's list at
  once. A "handled" field on each row, not a timestamp cursor, records what's
  been done, so a failed run repeats nothing and misses nothing.
- **Status changes**: add the Updates row, set `Member-visible status` and
  `Closed on`, email the reporter where the change calls for it.
- **Updates marked "Tell the reporter"**: email them.
- **Visits**: put each scheduled visit and maintenance occurrence on the
  maintenance calendar, and move or remove it when it changes.
- **Maintenance work**: create the next occurrence of a plan when one is done.

Daily:

- **Claim deadlines**: work out each warranty request's `Claim by` (see
  Warranties) and each claim's from its requests; warn the list of
  unclaimed warranty requests as a deadline nears (90, 30, 7 days).
- **Backup**: write every table as JSON, and every new photo and attachment,
  to a dated folder in the circle's Drive. Airtable's attachment links
  expire and its history is limited by plan; the Drive copy is the evidence
  file, and what makes leaving Airtable possible.
- **Checks**: email `calendaring-alerts@`-style warnings (to a
  `maintenance-alerts@` list) if the base's tables, fields, or named views
  no longer match what the script expects.

Weekly: the digest, including **patterns**. A pattern is three or more
requests in 180 days with the same Component (or the same System and Room)
in different units, or two or more in one Stack for Plumbing or Roof &
envelope, that aren't already under one Systemic claim. The thresholds are
constants in `tracker_rules.gs`.

## Links to the rest of the setup

- **The circle** is a Gather group made with `add_new_group.py`, so it
  has its list, folder, calendar, and Zoom room like any circle.
- **People** are copied from Gather by a Python script,
  `sync_maintenance_people.py`, run by hand when members change (Gather has
  no API, so it can't run on a timer). It reuses the directory CSV reader in
  `util/gather_utils.py`, and adds, updates, and marks as moved out; it never
  deletes a person, since requests link to them. To check: whether Gather's
  household records carry the unit number, or Locations needs a one-time
  mapping.
- **The maintenance calendar** is a separate calendar owned by `admin@`,
  not the circle's group calendar: `group_meetings` treats every event on
  a group calendar as a meeting, and would give a filter change a Zoom room
  and a reminder. But `group_meetings` also invites the all-events calendar
  to events on `admin@`'s *other* calendars, so it needs a small change to
  leave this one alone (a skip list, or a marker in the calendar's
  description). Work that affects everyone (a water shutoff, a fire alarm
  test) is announced by hand on the community calendar.
- **Emails** go from `admin@` with replies to the circle's list, as the
  calendar emails do.

## Developing it in Claude Code on the web

A cloud session has the repo and a network connection, and nothing else: no
browser signed in to Airtable, no Google sign-in. So:

- **The schema is a file.** `maintenance/schema.json` lists tables, fields,
  types, and select options. `maintenance_base.py` compares it with a base
  through Airtable's metadata API and prints the difference (`diff`), makes
  the base match by adding tables, fields, and options (`apply`; it never
  deletes or retypes, only reports), and loads starting rows such as the 36
  units and the System list (`seed`). `util/airtable.py` is the small client
  under it (paging, batches of 10, the rate limit).
- **Two bases.** "Building Maintenance" and "Building Maintenance (test)",
  made by the same script. Sessions get an Airtable personal access token
  that can reach *only the test base* (scopes `data.records:read`,
  `data.records:write`, `schema.bases:read`, `schema.bases:write`). A
  session can then create fields, load fake requests, and run the rules
  against real Airtable responses without being able to touch members' data.
- **The token is not in the repo or the chat.** It's saved on the cloud
  environment as a network secret for `api.airtable.com`, which the session
  asks for; the environment must also allow that host. Use an environment
  for this repo, not the default one.
- **The real base** is changed by running `maintenance_base.py apply` with a
  token for it: on your own machine, as the other scripts are run, with the
  token in a git-ignored `airtable_credentials` file beside
  `zoom_credentials`. The Apps Script has its own token for the real base,
  with the two `data.records` scopes only, in Script Properties.
- **Nothing that opens the base is in the repo**, which is public: no
  tokens, base IDs, form or status page links, calendar IDs, or phone
  numbers. The schema file names tables and fields, not IDs. The scripts
  read IDs and links from Script Properties and the git-ignored credentials
  file. Rows drafted from the handover (contacts, serial numbers) aren't
  committed either.
- **What a cloud session can't do** is listed in
  `maintenance_tracker/README.md` as a checklist: creating the workspace
  and tokens; building each view, the forms, and the status page's share
  link in Airtable's UI; `clasp push`; installing the timers. The script's
  daily check names any view the checklist calls for that's missing, so
  those parts can't silently drift. Most of the checklist can be done by
  Claude Code on your own computer instead of by hand (see below).
- **Tests** follow the repo's pattern: `tracker_rules.gs` under Node,
  the Python against a fake Airtable in `tests/`, all in `python -m pytest`.
  A session finishes a change by running them, then running the script's
  entry points against the test base.
- **Loading the handover.** Turning the subcontractor list, warranty
  binder, and O&M manuals into Organizations, Warranties, Assets, and
  Maintenance plans rows is itself a good session task: put the PDFs in the
  session, have it draft the rows as CSV in the test base, and review them
  before `seed` loads them into the real one.

### What's better done in Claude Code on your own computer

Claude Code run locally (the CLI or desktop app) has what a cloud session
lacks: a browser signed in to Airtable, the Google and Gather sign-ins the
other scripts use, and `clasp`. That covers most of the checklist above:

- **Views, forms, and the status page.** Claude can build them by driving
  Airtable's UI in your browser, working down the checklist while you
  watch. Do it that way, as a one-off, rather than as a Playwright script
  like the Gather ones: there are about a dozen views and two forms, made
  once, and a script against Airtable's UI would cost more to keep working
  than it saves. The daily check still tells you if one goes missing.
- **Deploying the Apps Script**: `clasp push`, running the function that
  installs the timers, and setting Script Properties.
- **Applying the schema to the real base**, with the real token on your
  machine.
- **Anything using Gather or Google**: `sync_maintenance_people.py` (Gather
  login), making the circle's group with `add_new_group.py`, and creating
  the maintenance calendar and handbook folder.
- **Loading the handover**, reading the PDFs where they are in Drive or on
  disk instead of uploading them to a session.
- **Trying it end to end**: submitting the real form in a browser and
  checking the acknowledgment arrives.

The cost is the safety the cloud gives for free. A local session can reach
the real base, so "sessions only touch the test base" becomes a habit
instead of a guarantee: keep the real token out of the environment except
when applying a reviewed change.

A workable split: write and test the code, schema, and rules in cloud
sessions against the test base; do setup and each deployment in a local
session.

## Plan and cost## Plan and cost

Start on Airtable's Free plan to build and try it. It won't last the
warranty year: its limit of about 1,000 records per base covers every table
together, and 36 units' walkthrough lists alone could exceed that. Since
this design uses no Airtable automations, the limits that matter are
records, attachment storage, editor seats, and API calls (the Free plan's
monthly API allowance, reported as 1,000 calls, is far too few for a
10-minute timer).

The Team plan is reported at about \$20 per editor a month, billed yearly,
with 50,000 records per base. Only people who edit the base directly need a
seat: the coordinator and two or three triagers, so roughly \$700 to \$1,000 a
year. Other volunteers use the quick update form and the emails. Check
current prices and limits on Airtable's pricing page before buying; the
figures above come from third-party summaries that don't fully agree.

The workspace should be owned by `admin@berkeleymoshav.org`, not a member's
own account.

## Phases

Move-in is soon, so the order is by what's lost if it's late.

0. **Set up and confirm** (a day). Book the attorney (see below). Make the
   workspace and both bases' tokens. Confirm against Airtable's API
   documentation, or by trying on the test base: which field types the
   metadata API can create (formula, rollup, and lookup fields may have to be
   made by hand; the design needs none of them); that it can't create views
   or forms; the plan's API call allowance.
1. **Intake, before move-in.** Schema file and `maintenance_base.py`;
   Locations, People, Requests, Updates, Organizations; the request form,
   Triage, Board, and Mine views; the status page. Ask the builder to name
   who receives claims. Usable by hand from here.
2. **Emails and backup.** The Apps Script's acknowledgments, urgent alerts,
   status emails, weekly digest, and the daily Drive backup.
3. **Claims.** Claims and Warranties, loaded from the warranty binder;
   deadline warnings; the Waiting on builder view.
4. **Maintenance.** Assets and plans from the O&M manuals; occurrences; the
   maintenance calendar and the `group_meetings` change.
5. **Knowledge and patterns.** Guides and the handbook folder; emergency
   cards; the patterns section of the digest.
6. **Before the warranty ends.** The month-10 inspection as a plan; a
   report of every open and unclaimed item per warranty; an export of the
   evidence file for the attorney.

## Decided

- **The status page is link-only** for now. If that proves too open, the
  script can write the same columns to a Google Sheet in the Shared Drive,
  which only the community can open.
- **The form is open to anyone with its link.** The risk is junk, not
  exposure; the script flags a reporter email that isn't in People.
- **The circle handles both** unit and common-element claims.
- **The builder has no claims process**, so the circle sets one (see
  Claims).
- **The coordinator will rotate**, and vendors' contacts will be filled in
  over time (see Organizations).
- **The repo is public** (see "Developing it in Claude Code on the web").

## Still unknown

- **When each warranty's clock starts.** The design works either way (see
  Warranties); the answer only moves the dates.
- **Whether email to a named person counts as notice** to the builder, and
  who that person is.

## For the attorney

This isn't legal advice, and which of these matter depends on documents
not seen here. They're the points where the tracker's design depends on a
legal answer, so they're worth an early consultation with a
construction-defect attorney, not one in month 11.

1. **Who holds each warranty, and against whom.** If the units were sold
   by a developer, each owner likely holds the unit warranties and the
   association the common-element ones. If the community's own entity was
   the developer, the claims run under its construction contract with the
   general contractor instead, with different terms and remedies. This
   fills in `Held by` and `Warrantor`.
2. **The circle acting for owners.** Since the circle will make unit claims
   too, whether it needs each owner's written authorization, and what the
   association's documents require before it claims for common elements.
3. **Every deadline, not only the one-year warranty.** California's Right
   to Repair Act (Civil Code section 895 onward) sets different periods for
   different kinds of defect in new homes sold to buyers, some much shorter
   than its ten-year outer limit, and manufacturers' warranties have their
   own. Each becomes a Warranties row.
4. **When each clock starts**: close of escrow for each unit, substantial
   completion, or the certificate of occupancy.
5. **What counts as notice**, given the builder has no process: to whom,
   in what form, and whether email is enough or the contracts require a
   letter. That statute also has a procedure a builder can require before
   any lawsuit (written notice, then fixed times for it to acknowledge,
   inspect, and offer a repair); whether it applies, and whether the
   circle's claim emails satisfy it.
6. **Maintenance the owners must do.** A builder can defend a claim by
   showing the owners didn't follow its or the manufacturers' maintenance
   recommendations. Which ones are required, and what record proves they
   were done, decides which Maintenance plans are marked `Required by`
   Warranty.
7. **What accepting a repair gives up.** Whether signing off a repair, or
   a form the builder asks for, waives anything; what `Accepted` should
   mean; and what to do about a repair that fails again.
8. **When the builder doesn't answer or refuses**: how long to wait, and
   what the next step is. This sets the digest's "past promised date" rule.
9. **What to keep, and what not to write.** What the evidence file should
   hold; that advice from the attorney stays out of Airtable and anything
   members can open; and whether volunteers' guesses about cause or fault in
   Updates could hurt a claim.
10. **Damage and insurance.** When a defect causes damage (a leak), what
    must be reported to which insurer and how soon, alongside the claim.

## Background

The recommendation this design follows, and what other communities use,
came from a survey of Cohousing-L: most run maintenance on a shared
spreadsheet or on Mosaic or Gather; Bellingham was weighing Redmine,
MaintainX, and Manage Casa; Cambridge built its own task system. A
spreadsheet was judged too weak for linking requests to claims and vendors
and for spotting patterns across units, and HOA suites too centred on dues.
If nobody will look after a database, the fallback is a Google Form feeding
a Sheet with the Requests columns above.
