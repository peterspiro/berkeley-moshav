# The maintenance tracker's Airtable base

The Maintenance & Warranty Circle's tracker is an Airtable base, "Building
Maintenance". `../maintenance-tracker-plan.md` is its design. This folder
holds what the base is made from:

- **`schema.json`**: every table, field, and select option, and the views
  each table should have. `util/airtable_schema.py` describes the format.
- **`seed.json`**: starting rows: the building's 36 units and its common
  areas, for the Locations table.

`maintenance_base.py`, in the repo root, makes a base match these files.
There are two bases: the real one, and "Building Maintenance (test)" for
trying changes, which is the one the script works on unless given `--real`.

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

## Setting up a base

1. In Airtable, signed in as the workspace's owner, create an empty base
   with the base's name. Delete its starting "Table 1" once the script has
   made the others.
2. Give the script's personal access token access to the base (see
   README.md, "Setup").
3. Run `python maintenance_base.py apply`, then
   `python maintenance_base.py seed`, then
   `python sync_maintenance_people.py` (each with `--real` for the real
   base).
4. Make Label the primary field of Requests and of Claims, and New the
   default of Requests' Status (both below).
5. Make the views and the two forms below by hand. `diff` lists what's
   still to do, except the forms.

### Primary fields to change by hand

Airtable shows a record by its primary field wherever it's linked or
picked, so a request should show as "R-3 Bath fan rattles", not "3". The
API can create a table only with a plain primary field, so Requests and
Claims are made with Number as theirs. In each, open the Label field's menu
and choose **Change primary field** (or the table's primary field's menu,
if that's where Airtable offers it). Do this before making the views.

### A new request's status

A request arrives from the form with no Status, since the form doesn't ask
for one. In Requests, edit the Status field and set its default to New, so
that a new request starts there. Triage also shows requests whose Status is
empty, so that none can be missed if the default is ever lost.

### Views to make by hand

The API can't create views. Each is named exactly as here, since `diff`
(and later the tracker's daily check) looks for them by name. The two forms
are the exception: the API doesn't show forms at all, so `diff` can't tell
whether they exist, and says nothing about them.

**Requests**

| View | Type | Set up as |
|---|---|---|
| Triage | Grid | Filter: Status is New, or Status is empty. Sort: Submitted, oldest first. Show Description, Photos, Location, Room, Type, System, Urgency, Reporter name, Reporter email, Reporter phone, Reporter, then Title, Component, Responsibility, Owner, Status |
| Board | Kanban | Stacked by Status. Filter: Status isn't Closed, Duplicate, or Declined. Cards show Location, Owner, Urgency |
| By owner | Grid | Grouped by Owner. Filter as Board |
| Patterns | Grid | Grouped by System, then Component. Show Location, Room, Submitted, Status. Sort by Location |
| Status page | Grid | Show only Label (the request's number and the title written at triage), Location, System, Member-visible status, Submitted, Closed on. Filter: Status isn't Duplicate or Declined, and Closed on is empty or within the past month. Then **Share view** to make its link. No other field may be shown: it's public to anyone with the link |
| Request form | Form | See below |


#### What a form's link shows

A published form lets anyone with its link pick from the rows of each
table it links to, and Airtable warns of this when the form is published.
So in the form builder, each linked field is set to show only its table's
primary field: select the field, and in the sidebar's **Appearance**
section, cut its **Fields** list down to that one.

- The request form's Location field shows only Name ("Unit 411"), and
  offers every unit and common area.
- The request form's Reporter field shows only Name, which
  `sync_maintenance_people.py` keeps as short as tells people apart ("Dana
  S"), and is filtered there to people whose Moved out isn't ticked. Anyone
  with the form's link can read that list; nothing else about a person is
  shown.
- The quick update form's Request field shows only Label, and is filtered
  there to requests whose Status isn't Closed, Duplicate, or Declined.

After publishing or changing a form, open it in a private browser window
and check what each linked field's list shows.

Airtable's "Limit record selection to a view" isn't used: it's a setting
of the field, not of the form, so it would limit what the circle can pick
in the base too.

The **Request form** has, in order: Reporter, Reporter name, Reporter
email, Reporter phone, Location, Room, Type, System, Urgency, Description,
Photos, May enter if I'm out, Access notes. Location, Type, and Description
are required. A member picks themselves in Reporter and skips the three
fields after it, which are for someone who isn't in the list: show them
only when Reporter is empty, and require Reporter name and Reporter email
then, if the form builder allows; otherwise leave all four optional, and
say so in their help text. Leave out every other field, Title
above all: the circle writes it at triage, and it's what the status page
shows. Its opening text says what to do in an emergency instead of filling
in the form, with the after-hours number and a link to the emergency guide.

**Updates**

| View | Type | Set up as |
|---|---|---|
| Quick update form | Form | Request, Kind, By name, When, Text, Attachments, Tell the reporter. Request is filtered to open requests (see "What a form's link shows"). For circle volunteers only: its Request field shows each open request's number and title to anyone with the link |

**Claims**

| View | Type | Set up as |
|---|---|---|
| Waiting on builder | Grid | Filter: Status is Submitted, Acknowledged, or Scheduled. Sort: Submitted on, oldest first. Show Requests, Status, Submitted on, Promised by, Claim by |

**Organizations**

| View | Type | Set up as |
|---|---|---|
| By trade | Grid | Grouped by Trades. Show Company, Role, Contact name, Phone, After-hours phone, Our contact |

Reporter, in Triage, is the link to the member in People, which a member
picks on the form. Someone who isn't in the list types the other three
reporter fields instead; the tracker's script, once it's built, will link
Reporter for them if their email address is a member's after all.

The status page's and forms' links aren't kept in this repo, which is
public.
