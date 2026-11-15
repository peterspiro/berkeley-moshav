# The maintenance tracker's Airtable base

The Maintenance & Warranty Circle's tracker is an Airtable base, "Building
Maintenance". `../maintenance-tracker-plan.md` is its design, and
`../apps_script/README.md` describes the script that sends its emails.
This folder holds what the base is made from:

- **`schema.json`**: every table, field, and select option, and the views
  each table should have. `util/airtable_schema.py` describes the format.
- **`seed.json`**: starting rows: the building's 36 units and its common
  areas, for the Locations table.

`maintenance_base.py`, in the repo root, makes a base match these files.
There are two bases: the real one, and "Building Maintenance (test)" for
trying changes, which is the one the script works on unless given `--real`.
`maintenance_demo.py` fills the test base with demo data, keeping its
Locations and People: tick the circle members and the coordinator in People
first.

`../pages/` holds **`maintenance-tracker-members-page.md`** and
**`maintenance-tracker-start-here.md`**: the text of the two pages that hold the tracker's links. The first is for
members, pasted into a Gather wiki page titled "Building maintenance"; the
second is for the circle, made into a Google Doc titled "Maintenance
tracker: start here" in the circle's Drive folder, which only the circle
can open. These copies are the sources: when one changes, update its page
to match. Each `[[...]]` in them stands for a link, a name, or a phone
number to fill in on the page itself; the links aren't kept in the repo,
since it's public. (The files hold nothing but the page's text: Google
Docs won't open a Markdown file that starts with a comment.)

This file has three parts: changing the base's structure; setting a base
up, with everything that has to be made by hand; and how the circle uses
it.

## Changing the base's structure

Change `schema.json`, not the base, so the two bases stay alike:

```bash
python maintenance_base.py diff
```

```bash
python maintenance_base.py apply
```

Try it on the test base, then repeat both with `--real`. To add a table,
field, or option, add it to the file. Airtable's API can't delete or rename
a table or field, change a field's type, or remove an option: do those by
hand in both bases, and change the file to match, so `diff` is clean again.

A field or option added in Airtable by hand shows in `diff` under "Notes"
until it's added to the file.

A formula is the exception both ways: the API can't change one, and `diff`
doesn't compare one. To change a formula, change it in the file and paste
the same formula into the field in each base.

## Setting up a base

1. In Airtable, signed in as the workspace's owner, create an empty base
   with the base's name. Delete its starting "Table 1" once the script has
   made the others. (Duplicating a base that's already set up, in Airtable,
   may bring its views, forms, and interfaces with it, and save most of
   steps 4 to 6: check each afterwards, delete the copied requests, updates,
   and claims, and carry on from step 2. The copy's forms and status page
   have links of their own.)
2. Give the script's personal access token access to the base (see
   README.md, "Setup").
3. Run `python maintenance_base.py apply`, then
   `python maintenance_base.py seed`, then
   `python sync_maintenance_people.py` (each with `--real` for the real
   base).
4. Make the changes to fields that the API can't ("Fields to change by
   hand").
5. Make the views ("Views") and the two forms ("Forms").
6. Make the interfaces the circle wants ("Interfaces").
7. Run `python maintenance_base.py diff`: it lists what's still to do of
   steps 4 and 5, except the forms, which the API can't see. Interfaces
   aren't checked either.
8. Point the `maintenance_tracker` script at the base (see
   `../apps_script/README.md`).
9. Put the base's links into the members' page and the circle's page
   (`../pages/maintenance-tracker-members-page.md`, `../pages/maintenance-tracker-start-here.md`).

### Fields to change by hand

**Primary fields.** Airtable shows a record by its primary field wherever
it's linked or picked, so a request should show as "R-3 Bath fan rattles",
not "3". The API can create a table only with a plain primary field, so
Requests and Claims are made with Number as theirs. In each, open the Label
field's menu and choose **Change primary field**. Do this before making the
views. Until triage gives a request a Title, its Label is its number and
location ("R-3 Unit 411").

**A new request's status.** A request arrives from the form with no Status,
since the form doesn't ask for one. In Requests, edit the Status field and
set its default to New. (Triage also shows requests whose Status is empty,
so that none can be missed if the default is ever lost.)

**Links that hold one record.** Airtable lets a link field hold several
records unless told otherwise. A request has one owner, one location, and
one warranty (its deadline is worked out from it), and so on: `schema.json`
marks those links `single`, and `apply` makes them so. If `diff` lists any
all the same, open the field's settings and turn off "Allow linking to
multiple records".

**The script's own fields.** Acknowledged on and Status noted in Requests,
and Told reporter on in Updates, are the script's notes of what it has
done. Hide them from the views people work in, and don't edit them.

### Views

The API can't create views. Each is named exactly as here, since `diff`
looks for them by name.

**Requests**

| View | Type | Set up as |
|---|---|---|
| Triage | Grid | Filter: Status is New, or Status is empty. Sort: Urgency (first option to last, so emergencies lead), then Submitted, oldest first. Fields in the order triage reads them, which the expanded record follows: Description, Photos, Urgency, Type, In my unit, Location, Room, Reporter, Reporter name, Reporter email, Reporter phone, May enter if I'm out, Access notes, then Title, System, Component, Responsibility, Warranty, Owner, Status. In the grid itself, hide all but Label, Urgency, Location, Submitted, and, as the screen has room, Description, Photos, Type, System |
| Board | Kanban | Stacked by Status. Filter: Status isn't Closed, Duplicate, or Declined. Cards show Location, Owner, Urgency |
| By owner | Grid | Grouped by Owner. Filter as Board |
| Patterns | Grid | Grouped by System, then Component. Show Location, Room, Submitted, Status. Sort by Location |
| Status page | Grid | Show only Label (the request's number and the title written at triage), Location, System, Member-visible status, Submitted, Closed on. Filter: Status isn't Duplicate or Declined, and Closed on is empty or within the past month. Then **Share and sync** to make its link. No other field may be shown: it's public to anyone with the link |

**Claims**

| View | Type | Set up as |
|---|---|---|
| Waiting on builder | Grid | Filter: Status is Submitted, Acknowledged, or Scheduled. Sort: Submitted on, oldest first. Show Requests, Status, Claim deadline, Submitted on, Promised by |

**Organizations**

| View | Type | Set up as |
|---|---|---|
| By trade | Grid | Grouped by Trades. Show Company, Role, Contact name, Phone, After-hours phone, Our contact |

### Forms

Two forms, made in Airtable's form builder and published. The labels and
help text are set there; the fields keep their names.

**The request form**, on Requests, titled "Report a building problem or ask
how something works". For members. Its questions, in this order:

| Field | Label on the form | Help text | Shown | Required |
|---|---|---|---|---|
| Reporter | Who's reporting? | Pick your name. Not in the list? Leave this empty and fill in your details below. | always | no |
| Reporter name | Your name | | when Reporter is empty | yes |
| Reporter email | Your email | We'll send updates here. | when Reporter is empty | yes |
| Reporter phone | Your phone | Optional. Helps us arrange access. | when Reporter is empty | no |
| In my unit | It's in my unit | If it's somewhere else, leave this unticked and choose where below. | when Reporter isn't empty | no |
| Location | Where is it? | A unit or a common area. | when In my unit isn't ticked | yes |
| Room | Which room? | | when In my unit is ticked, or Location contains "Unit" | yes |
| Type, System, Urgency, Description, Photos, May enter if I'm out, Access notes | | | always | Type and Description |

A member picks themselves in "Who's reporting?"; someone who isn't in the
list leaves it empty and types their details, and the tracker's script
links Reporter for them if their email address is a member's after all.
Someone who isn't in the list has no unit on record, so isn't offered "It's
in my unit" and always chooses a location. The script sets a ticked
request's Location to the reporter's unit when the request arrives.

Leave out every other field, Title above all: the circle writes it at
triage, and it's what the status page shows. The form's opening text says
what it's for (the building and its equipment: repairs, defects, and how
to operate things), and should say what to do in an emergency instead of
filling it in: on Airtable's Free plan the circle's alert about an urgent
request can come 2 hours after it.

**The update form**, on Updates, titled "Log an update on a request". For
circle volunteers only: its Request field shows each open request's number
and title to anyone with the link. Its questions:

| Field | Label on the form | Help text | Shown | Required |
|---|---|---|---|---|
| Request | Which request? | | always | yes |
| Kind | What kind of update? | | always | yes |
| Text | What happened? | | always | yes |
| By | Who's logging this? | Pick your name. | always | no |
| By name | Your name, if it's not in the list | So the record shows who logged this. | when By is empty | yes |
| When | When did it happen? | Leave blank if it just happened. | always | no |
| Attachments | | | always | no |
| Tell the reporter | Tell the reporter | Emails the text above to the person who reported it, word for word. | always | no |

An update with "Tell the reporter" ticked is emailed to the request's
reporter by the script.

Requests and Updates each have a **Created by** field, which Airtable fills
in with the account that made the row, when it was made by someone signed
in (not through a form's link). An update added on the Maintenance
interface needn't say who's logging it: the script sets By from Created by.

### What a form's link shows

A published form lets anyone with its link pick from the rows of each
table it links to, and Airtable warns of this when the form is published.
So in the form builder, each linked field is set to show only its table's
primary field: select the field, and in the sidebar's **Appearance**
section, cut its **Fields** list down to that one.

- The request form's Location field shows only Name ("Unit 411"), and
  offers every unit and common area: the units first, then the common
  areas, each in alphabetical order (sorted by Kind, then Name).
- The request form's Reporter field shows only Name, which
  `sync_maintenance_people.py` keeps as short as tells people apart ("Dana
  S"), and is filtered there to people whose Moved out isn't ticked. Anyone
  with the form's link can read that list; nothing else about a person is
  shown.
- The update form's Request field shows only Label, and is filtered there
  to requests whose Status isn't Closed, Duplicate, or Declined; its By
  field shows only Name, filtered like Reporter.

**Personal links.** A member's own link to the request form fills in
Reporter and Location for them: the form's link with, for example,
`?prefill_Reporter=Dana+S&prefill_Location=Unit+411` added, naming the
person's and the unit's rows by their primary fields. The tracker's script
writes each person's to Request form link in People. A circle
member's own link to the update form is the same idea, with
`?prefill_By=Dana+S` added, written to Update form link for each person
whose Circle member box is ticked. A link shows
nothing the plain form doesn't.

After publishing or changing a form, open it in a private browser window
and check what each linked field's list shows.

Airtable's "Limit record selection to a view" isn't used: it's a setting
of the field, not of the form, so it would limit what the circle can pick
in the base too.

### Interfaces

Circle members work in interfaces (Airtable's Interface Designer), not in
the base. An interface shows chosen fields, lets only some be edited, and
offers no way to delete a row. The safety comes from who is given what:
circle members are shared the interfaces only, and the base itself is
shared with the one or two people who maintain it. Anyone who can edit the
base can delete rows in its grids, whatever the interfaces allow.

Interfaces are made by hand; the API can't see them, so `diff` doesn't
check them. In every one, turn off deleting records, and turn off creating
them except where it says otherwise.

They're pages of one interface, "Maintenance", each named as here, so the
circle has one thing to open; the circle's guide calls them by these names.

**Triage**, for the coordinator. A record review layout: the queue on the
left, the selected request on the right.

- Queue: Requests whose Status is New or empty, sorted by Urgency (first
  option to last, so emergencies lead) and then by Submitted, oldest
  first, showing Label, Urgency, Location.
- The request, in this order:

| Field | Editable | Why |
|---|---|---|
| Description | no | The member's own words |
| Photos | no | |
| Submitted | no | |
| Reporter, Reporter name, Reporter email, Reporter phone | no | Who to ask |
| May enter if I'm out, Access notes | no | |
| In my unit | no | |
| Location | yes | To set it where the script couldn't, or correct it |
| Room | yes | |
| Type, Urgency | yes | To correct the member's choice. An urgent request's alert has already gone |
| Title | yes | The summary the status page shows: the thing and the fault, nothing personal |
| System | yes | |
| Component | yes | Once Assets has rows |
| Responsibility | yes | |
| Warranty | yes | Once Warranties has rows |
| Duplicate of | yes | With Status set to Duplicate |
| Owner | yes | |
| Status | yes | Last: changing it takes the request out of the queue |

  Leave out Label, Number, Member-visible status, Acknowledged
  on, Status noted, and the links other tables make (Updates, Claims, and
  so on).

**Requests**, for whoever owns a request. A record review layout over
every request that isn't Closed, Duplicate, or Declined, grouped by Owner,
with a filter for Status.

- Editable: Status, Visit, Vendor, Resolution, Owner, Title, System,
  Component, Responsibility, Warranty. Claim deadline is shown, read-only.
  To schedule a visit, fill in Visit: the tracker's script then sets
  Status to Scheduled and tells the reporter.
- Read-only: everything the member entered, as in Triage, and Submitted.
- Its Updates, as a list within the page, newest first, where creating is
  turned on: Kind, Text, By (or By name), When, Attachments, Tell the
  reporter.
  Told reporter on is shown read-only. This does on a computer what the
  "Log an update" form does on a phone.

**Claims**, for whoever deals with the builder. A record review layout over
Claims, grouped by Status, oldest Submitted on first. Creating is turned
on. Editable: every field but Label, Number, and the ones worked out
(Claim deadline, Request count, Request lines, Warrantor contact, Draft,
which are shown). Shown with each claim: its
Requests (to link and unlink), and its Updates, with creating turned on as
above.

**Contacts**. A list of Organizations grouped by Trades, with creating
turned on and every field editable: the circle collects these over time.

**Patterns**, read-only. Charts counting requests by System, by Component,
and by Location, and a list grouped by System and Component, for the
monthly look for repeats.

No interface is needed for People (the sync keeps it), Locations (the seed
file does), or what members use (the request form and the status page).
Assets, Warranties, Maintenance plans, Maintenance work, and Guides get
theirs when those tables come into use.

For a linked field on an interface (Location, Component, Warranty,
Duplicate of, Owner, and the like), "editable" means turning on **Link /
unlink records** in its user actions, and leaving **Add records through a
form** off, so that nobody adds a location, person, or warranty from a
request's page. Updates, on the Requests and Claims pages, is the reverse:
adding through a form on, linking and unlinking off.

On Airtable's Free plan an interface can't be shared without the base, so
there it's a convenience, not a protection.

## Using the base

`../maintenance-tracker-guide.md` says the same for the circle's members,
without the setting up: keep the two in step.

### Triaging

The coordinator triages each new request: weekly, and the same day for
anything urgent (the script emails the circle's list about those). On the
Triage interface, or in the Triage view with the request's row expanded
(click its expand icon, or press space; the arrows at the top step to the
next), read what the member said, then fill in:

- **Title**: the thing and the fault ("Bath fan rattles"), nothing personal.
  It's what the status page shows.
- **Location**, if it's empty or wrong; **System**; **Component**, once
  Assets has rows.
- **Responsibility**, and, for a warranty item, its **Warranty**.
- **Owner**: the one person who'll see it through.
- **Status**, last: Triaged, or Duplicate (with Duplicate of) or Declined.
  Changing it takes the request out of Triage.

### Working a request

From the Board or By owner views, or the Requests interface. To schedule a
visit, fill in **Visit**: the script sets Status to Scheduled and emails
the reporter the time. When the work's done, set Status to "Done, to
confirm" (the script asks the reporter whether it's fixed), and then
Closed. Log each call and visit as an update, on the request's page or
with the update form; tick "Tell the reporter" to have the script email one
to the member. The script adds an update itself for every change of status.

### Warranty claims

A request becomes a claim in four steps, all in Airtable. Nothing waits
for the tracker's script: the dates and the letter are formulas, worked
out as the rows are filled in.

1. **Warranties** has a row for each warranty: its Warrantor, its Term
   (months), and when its clock starts. Clock starts says whether that's
   One date for the building (Building start date), Each unit's closing
   (the Closing date of the request's unit in Locations; a common area's is
   the building's date), or Unknown. While it's Unknown, the earlier of the
   two dates is used, so a deadline is never shown later than it is; when
   the answer is known, changing Clock starts moves every deadline.
2. **At triage**, a request that's the builder's or a manufacturer's to fix
   gets its Warranty (one). Its **Claim deadline** then shows: the
   warranty's start for that place, plus its term.
3. **A claim** is a row in Claims, linked to its Requests (several, when
   it's one defect in several places: set Scope to Systemic), its Warranty,
   and its Warrantor. Its Claim deadline is the earliest of its requests'.
   Its **Draft** is the letter: a subject line, the warrantor's contact
   (from Organizations), each request's number, place, title, and the date
   it was first reported, and a request to confirm receipt. Read it, since
   it uses each request's Title as written at triage; then copy it into an
   email from the circle's address to the person the builder named, and
   attach the requests' photos.
4. **Once sent**, set the claim's Status to Submitted, with Submitted on,
   Submitted how, and Sent to, and file the sent email in its Folder. As
   the builder answers: Acknowledged on, Response, Promised by, Work done
   on, and, when the circle accepts the work, Accepted by and Accepted on.

The weekly digest lists requests whose deadline is within 90 days (or
past) with no claim made, claims the builder hasn't acknowledged in 14
days, and claims past their promised date. A draft doesn't count as a claim
made.

The letter's wording is the Draft field's formula in `schema.json`; it
should be reviewed by the circle's attorney before the first claim goes
out. To change it, change the file and paste the formula into the field in
each base.

### The weekly digest

On Sundays the script emails the circle's list what came in, what's
waiting for triage, an owner, a visit time, or confirmation, what's gone
quiet, the warranty deadlines and claims above, and what's repeating across
units (see `../apps_script/README.md`).

The status page's and forms' links, the base's ID, and the circle's
address aren't kept in this repo, which is public.
