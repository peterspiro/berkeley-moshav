# Circle Agendas

The circle agenda docs (see `../circle-agendas-plan.md`): building each
circle's doc, and the web app that the links in an agenda doc run; and the
Circle Structure, the tree of circles and working groups.

- `build_template.gs` — builds an agenda doc (a circle's, or a preview)
  from a blank doc, entirely from code
- `web_app.gs` — the web app: takes a link's action, doc, and meeting, and
  shows a page saying what it did
- `meetings.gs` — what the web app does: add the circle's next meeting,
  and, to a meeting, update its times or add an agenda item
- `circle_docs.gs` — builds each circle's and working group's agenda doc,
  which the Python scripts create empty and label
- `circles.gs` — which circle a doc belongs to (from the Google Groups'
  footers), and its calendar's events and its members' names
- `structure.gs` — the Circle Structure: its sheet, its doc, and each
  agenda doc's Parent Circle and Sub-Circles rows (see "The Circle
  Structure" below)
- `structure_rules.gs` — the Circle Structure's rules, with no Apps Script
  calls, tested under Node by `tests/test_circle_structure_rules.py`
- `agenda_rules.gs` — the agenda's time arithmetic, with no Apps Script
  calls, tested under Node by `tests/test_circle_agenda_rules.py`
- `doc_helpers.gs` — helpers the others share

The probes that were run before any of this was built, to see what Apps
Script can do to an agenda doc, are in `../archive/circle_agendas/`.
- `appsscript.json` — the advanced services (Docs, Drive, Calendar, Admin
  Directory, Groups Settings), OAuth scopes (Sheets too, for the Circle
  Structure), and web app settings

All of them are one Apps Script project, so they share their functions and
constants.

## Setting up the project

Use `admin@berkeleymoshav.org`. With
[clasp](https://github.com/google/clasp):

```bash
npm install -g @google/clasp
clasp login
cd circle_agendas
clasp create --type standalone --title "Circle Agendas"
git checkout -- appsscript.json  # clasp create overwrites it with a bare default
clasp push -f
clasp open
```

Commit the `.clasp.json` that `clasp create` writes, so later pushes go to
the same project. After any change here, `clasp push` again (and reload the
editor if it's open). In the editor, choose a function in the menu at the
top and click **Run**; its output is in the execution log below. The first
run asks for access to Docs and Drive. Google's consent screen lets you
choose which of them to allow: check **Select all**. If you don't, a run
stops with "You do not have permission to call …"; click the link in the
error, allow the rest, and run it again.

## Deploying the web app

A doc's links run the web app, so it must be deployed, and its address
recorded, before any doc is built. Once, in the editor (as
`admin@berkeleymoshav.org`):

1. **Deploy → New deployment**, type **Web app**. `appsscript.json` already
   sets it to run as you and to let anyone with a Google account use it;
   check the dialog says so ("Execute as: Me", "Who has access: Anyone with
   Google account"). Click **Deploy**, and allow access if asked.
2. Copy the **Web app URL** (ending in `/exec`).
3. **Project Settings → Script Properties → Add script property**: property
   `WEB_APP_URL`, value that URL. Save.

A deployment runs the code as it was when deployed. After `clasp push`ing
a change to the web app's code, update the deployment so it runs the new
code, keeping its URL: **Deploy → Manage deployments**, the pencil, then
**Version: New version** and **Deploy**. (Or, from the terminal,
`clasp deployments` to find its ID and `clasp deploy -i <ID>`.) Changes to
`build_template.gs` alone need no new deployment.

When a change adds permissions to `appsscript.json` (such as reading
calendars and groups, for new meetings), run any function in the editor
first, to allow them, and then deploy a new version: the web app runs with
the permissions you've allowed.

"Script function not found: doGet" from a link means the deployment's
version predates `web_app.gs`: deploy a new version as above.

The links:

- **+ New meeting**, under "Create a new meeting" at the top of the first
  tab ("Circle Info"), adds the
  circle's next meeting in a tab of its own, titled with its date, after
  the Circle Info and Backlog tabs (newest first): the first timed event on the
  circle's calendar after the day of the newest meeting in the doc (or
  after now, if there's none), so clicking again adds the one after. Its
  date, start time, and length come from the event, in the line above its
  table; Place links the circle's Zoom room; Attendance lists the circle's
  members, unchecked, in columns of up to four, beside Facilitator and
  Secretary (dropdowns of the members, set to the holders chosen in the
  Circle Info tab's Roles) and Guests (named as in the Gather directory,
  from "Circle agenda member names.json" in `admin@`'s My Drive, which
  `update_groups.py` writes on each run, or else
  as Google names them); its agenda has one 5-minute topic; its
  Administrative matters list gives the next event as the next meeting
  and links "Minutes of last meeting" to the previous meeting's tab; and
  the items of the previous meeting's
  Action Items Recap (a bulleted list) become the checklist of its Action
  Items Accountability (their text only). The meetings that are over
  lose their links, so they're no longer changed. Members are named by
  first name, with their last initial if another community member shares
  it; the closing round has a line for each attendee's feedback. The circle is the Google Group whose footer links the
  Drive folder (or shared drive) the doc is in, so this works only for a
  doc in a circle's folder; elsewhere the page says so.

  It takes two runs: Apps Script (DocumentApp) doesn't see a tab the Docs
  API added in the same run. The link's run adds the tab and returns a page
  saying it's adding the meeting; the page's script then runs the second
  step, which builds the meeting in the tab, and shows how it went. If the
  second step fails, the page says so, and the empty tab can be deleted.

Each meeting's own links are in a row just above its Action Items Recap,
so they stay just below where new items go.

- **+ Add item** adds a Topic of 5 minutes just above the links, and
  updates the times.
- **↻ Update times** rewrites each item's start time, the closing round's
  duration (the time left before the end), and the end time, from the
  start time and Duration (as 1.5 or 1:30) in the line above the
  table, and the other
  items' durations. A duration it can't read turns red and counts as 0; a
  closing round with no time left turns red. The page says why. It also
  retitles the meeting's tab, if its date chip has been changed (Docs
  can't run a script when the chip is edited). And it lines up the
  bulleted and numbered lists in the meeting's table: each nested level
  0.25" further in than the one outside it, rather than at Docs' own,
  wider, indents (a script can't change a list's indents, only each
  paragraph's, and can't run when someone presses Tab).
- **+ Add item** gives the new topic a Presenter dropdown (defaulting to
  "Presenter") of the meeting's attendees, and bulleted lists under it and
  in Notes.
- **✉ Announce agenda** opens Gmail's compose window (in a new tab, signed
  in to Gmail), with an email to the circle's list that's ready to send:
  "Agenda ready: Dec 4 Technology Circle meeting", greeting the circle
  ("Hello Technology Circle,"), linking the meeting's tab ("The agenda for
  our Fri Dec 4 meeting is ready:"), asking for a review and any change
  requests as soon as possible, thanking them, and giving its Zoom room. It leaves out the time, which
  may change after the link is written. It's sent
  from the clicker's own Gmail, and can be changed before it's sent. It
  doesn't go through the web app, so it's one click, but the email is
  written into the link: the link is written when the meeting is added, and
  **↻ Update times** rewrites it, from the tab's date and the Zoom room
  linked in the Time and Duration line. So after changing the meeting's
  date, click **↻ Update times** before announcing.
  Meetings added before this link existed get it the first time
  **↻ Update times** is clicked. The email links to the agenda rather than
  listing its items, so it never goes out of date as items are changed.
  Someone whose mail isn't Gmail can copy the agenda tab's link (the
  address bar, with that tab open) into an email of their own.

Each but **✉ Announce agenda** opens a tab that says what it did, and to
close it to return to the agenda, which already shows the change.
For anyone who can edit the doc, clicking a link shows a small box with its
address, and clicking that opens it (or Ctrl-click, ⌘-click on a Mac, opens
it directly).
A link's address names the doc by its ID, so a doc copied by hand (File →
Make a copy) still points at the doc it was copied from; the plan has how
that's to be handled.

To try the actions without the web app, run **`tryUpdateTimes`** or
**`tryAddItem`** (on the newest meeting of the latest preview), or
**`tryAddMeeting`** and then **`tryFinishMeeting`** (the two runs of a new
meeting), in the editor; each logs what it returned. For a new meeting,
the preview must be in a circle's folder.

## Building a doc

A doc is built from a blank doc by `build_template.gs`, so it depends on
no other document; every change to the layout is a change to that file. Building has two passes: DocumentApp lays
out the text and tables, writing a token such as `⟦checkbox⟧` wherever a
smart chip or list goes, and then the Docs API replaces each token with its
chip or list, sets the pageless format, names the first tab "Circle Info", and
adds the Backlog tab: headings for requests (policy items, tasks) and for
the prioritized backlog (discussion underway, prioritized for initial
discussion, assigned tasks, tasks to assign), each with a list. A new doc has no
meetings; each is added, in a tab of its own, by "+ New meeting".

Building needs `WEB_APP_URL` (see above), for the "+ New meeting" link.
Rebuilding a doc deletes its meeting tabs.

1. **`previewTemplate`** builds the "Template preview" doc and logs its
   link. The first run makes it, in your My Drive; later runs empty it and
   build it again in place, so it keeps its link and stays wherever you've
   moved it (such as into a circle's folder, to try new meetings). If it's
   been deleted or trashed, a new one is made.
2. **`describePreview`** logs the preview's structure (each element, each
   table row's text, and its chips) and every link in it.

To check the names new meetings use, run **`checkMemberNames`** in the
editor: it logs what "Circle agenda member names.json" holds, and makes the
web app read it (and the groups' footers) afresh.

## Circles' docs

Each circle and working group gets an agenda doc, "<Group> Agenda", in its
Drive folder. `update_groups.py --agenda-docs`
(with `-g`, for one group) and `add_new_group.py --agenda-doc` create it
empty and label it in Drive with the group's
address and "new" (`util/circle_agenda_docs.py`).
**`buildNewCircleDocs`** builds every doc so labelled, as
`previewTemplate` builds the preview, puts the group's name, email
address, Drive folder, and place in the Circle Structure (see below) in
About this circle, and relabels it "built", with the layout version
(`LAYOUT_VERSION`).

Docs built before About this circle's Zoom room and Drive folder were
filled in can get them with **`fillAboutForCircleDocs`**,
run in the editor: it fills in those rows where they're empty, in every
built doc, and leaves rows that already say something alone (but for the
Parent Circle and Sub-Circles rows, which it writes from the Circle
Structure). A doc's
circle is the group whose Drive folder holds it, or else the one with its
labelled address.

About this circle's rows are, in order: Aims & Domain, Policies folder,
Zoom room, Circle calendar (a link to the circle's Google calendar, which
offers to add it to the clicker's own), Remind before meetings, Circle email
address, Other Key Links, Google Drive Folder (linked by the circle's name,
), Parent Circle, and Sub-Circles. Docs built
before that order (layout version 1,
with a "Circle Google Drive Folder" row linked as "Google Drive folder")
can be changed over with **`reorderAboutInCircleDocs`**, run in the
editor: in every such doc, it moves the rows the scripts fill in (Zoom
room, email address, Drive folder, parent, sub-circles) into place,
leaving the rows filled in by hand (Aims & Domain, Policies folder, Other
Key Links) where they are, so whatever's in them, smart chips included,
is kept; renames the Drive folder row; rewrites it and the Parent Circle
and Sub-Circles rows; and labels the doc layout
version 2. A doc whose rows aren't the expected ones, or whose Zoom room
or email address row holds a chip (which moving could lose), is left as
it is, and the log says why. To see what it does first, run
**`tryReorderAboutOnCopy`**: it reorders an unlabelled copy of one such
doc, in your My Drive, and logs its link (delete the copy after).

The Remind before meetings row says when the circle's email list gets an email
reminder of each meeting (`group_meetings/` sends them, and reads the row's
value cell hourly): it holds only the setting, "24 hours" to start with
(empty means that default too). How to write it is the grey second line of
the label cell: "e.g. 2 days; 90 minutes; 2 days, 1 hour; none". (A meeting's own
`[remind 2 hours]` or `[no reminder]`, in its description, wins; each
reminder email says so.)

Docs built before the Circle calendar and Remind before meetings rows (layout
version 2) get them, below Zoom room, with
**`addNewAboutRowsToCircleDocs`**, run in the editor: it adds whichever
of the two a doc lacks, links the calendar, and labels the doc layout
version 3. It skips docs still on layout version 1 (the log says which):
run `reorderAboutInCircleDocs` first, then this. Docs without the
Remind before meetings row get reminders 24 hours before.

Once, in the editor, run **`installTriggers`**, which runs
`buildNewCircleDocs` every 10 minutes and `updateRolesInCircleDocs`
every day at 1 am (and asks to allow managing triggers), and, once
`setupCircleStructure` has made the Circle Structure sheet (which runs
`installTriggers` itself), `syncCircleStructureGroups` hourly and
`onCircleStructureEdit` on each edit of the sheet. The Roles
table's Name cells are dropdowns of the circle's members (showing a dash
until one is chosen, as do a meeting's Facilitator and Secretary when the
Roles have none), and its Term ends
cells date chips, starting at the day the doc was built. The daily run, and
adding a meeting, bring the dropdowns' choices up to date with the
circle's members: new members are added, and members who've left, and
choices added by hand in Docs, are removed unless a Name cell shows them,
so no cell's choice changes. The daily run also colors red each Term ends
date that's past. Docs can't run a script when a date is changed, so
`buildNewCircleDocs` also does that, every 10 minutes, in each doc changed
since its last run, so a changed date is colored within about 10 minutes.
Date chips are in Docs' default format ("Nov 3, 2026"), which, unlike the
others, Docs keeps when someone picks a new date. To try the daily run on the latest preview, run
**`tryUpdateRoles`** in the editor; it logs what it changed.
Docs built before layout version 4 have plain-text Roles (which new
meetings still read); **`upgradeRolesInCircleDocs`**, run in the editor,
changes them over: in every doc on layout version 3, each Name becomes a
dropdown of the circle's members showing what was written there (as a
choice of its own if it isn't a member's name, the dash if it was empty),
and each Term ends a date chip of the date written there (today's if it was
empty); a Term ends it can't read as a date is left as it is, and the log
says so, to replace by hand. It labels the doc layout version 4, and skips
docs on older layouts (run `addNewAboutRowsToCircleDocs` first). To see
what it does first, run **`tryUpgradeRolesOnCopy`**: it upgrades an
unlabelled copy of one such doc, in your My Drive, and logs its link
(delete the copy after). It can also be run by hand, to build waiting docs at once; the
log links each doc it built.

The Docs API part of building (`replaceTokens`) reads the doc's positions
and changes the doc at them. A read just after DocumentApp has saved the doc
can return an older version, whose positions are wrong for the new one, so
the changes are made only to the version read, and if the doc has moved on,
it's read again. (Before that, writing the Circle Structure's Sub-Circles
bullets this way deleted 8 characters of some docs' Roles labels, and
bulleted them. **`repairRoleLabelsInCircleDocs`**, run in the editor, puts
back each role label that has a bullet in it, in every built doc, and logs
the docs it changed.)

Building only ever adds to a doc, so it's done only to one that's still
empty: if someone has written in a doc before it's built, it's labelled
"not empty" and left as it is, and the log says so. A doc is labelled
"building" while it's built, so one whose build fails partway isn't built
again on top of itself; it's left for a person to look at (delete it, and
the next `--agenda-docs` run makes a new one). The Python scripts never
change a doc's contents, only create an empty one when a group has none.

A group's doc is found by its label, so a group never gets a second one;
if its doc is deleted or moved to the trash, the next run of
`update_groups.py --agenda-docs` makes a new one.
The flags keep docs from being made for every group before their layout
is settled: changes to the Circle Info and Backlog tabs don't reach docs
already made (see the plan).

## The Circle Structure

The tree of circles and working groups (`structure.gs`, with its rules in
`structure_rules.gs`, tested under Node by
`tests/test_circle_structure_rules.py`). Its sheet and doc are in a
"Circle Structure" folder at the top of the Shared Drive (`SHARED_DRIVE_ID`),
outside every group's folder, so the Shared Drive's members can see them,
and only its managers can move or delete them: the Coordinating Circle are
the sheet's editors, which in a shared drive can't. It has three parts:

- **The sheet**, "Circle Structure (editing)": a
  row per circle and working group (clubs, and groups whose type can't be
  told, have none), keyed by its Gather group ID (in a
  hidden column, so a rename keeps the row). The Coordinating Circle
  (`coordinating-circle@berkeleymoshav.org`, `STRUCTURE_EDITORS`) can edit
  only its **Parent** column: everything else is protected, editable only by
  `admin@`, which the script runs as. (So can anyone with edit access to the
  whole Shared Drive, its members with a Contributor role or above, as only
  the Parent column isn't protected.) Each Parent cell is a dropdown of the groups the
  row may be under: a circle under a circle, a working group under a circle
  or working group, leaving out the group itself and the groups beneath it.
  Full Community (`ROOT_GROUP_NAME`) is always at the top. The columns, in
  order (`STRUCTURE_LAYOUT`): **Group**, **Parent**, **Email list** (a
  mailto link to the group's shortest address, a shorter alias if it has
  one; only as wide as its heading, cutting the address off), **Gather
  group** (a link), **Guests** and **Meeting times** (see below), **Drive
  folder** and **Agenda doc** (links), one column for
  each role in the agenda doc's Roles table (**Leader**, **Feedback Link**,
  **Facilitator**, **Secretary**: who its Name dropdown shows, read
  hourly), **Type** (Circle or Working Group, from the name of the group's
  Drive folder, or else of the group: one ending in "Circle" is a circle's,
  and one ending in "Working Group", "Work Group", "Team", or "Pod" a
  working group's, perhaps followed by something in brackets, such as the
  group's acronym, as in "Development, Finance, & Legal Circle (DF&L)"; a
  club, and a group whose names don't say, are left out; an everybody
  group, linked to a shared drive, goes by its shared drive's name), and
  **Problems** (why a group isn't placed yet).
  **Guests** lists, one to a line, the members of the group's Google
  Group who aren't in Full Community's, read hourly. Guests are given
  access by adding them to the group's Drive folder (as Content managers)
  by hand, and `groups_drive_sync` puts them in the Google Group; Full
  Community's members are everyone in the community, so anyone else in a
  group is a guest. (Full Community's own row shows none; `admin@`, which
  `groups_drive_sync` never removes, is left out; and a community member
  who has left Full Community shows as a guest.)
  **Meeting times** says, read hourly from the group's calendar (the one its
  footer links), when its meetings in the next 8 weeks are, linking the
  calendar: a line for each repeating meeting, in the order of their next
  meetings, from its series' repeat rule and usual times (so a meeting
  moved for one week changes nothing): "Tue 7:30–9 pm", "Mon & Thu 7–8 pm",
  "Every other Tue 7–8 pm", "2nd & 4th Tue 7–8:30 pm", "Last Sun of month
  4–5:30 pm" (the month's last Sunday), "15th of month 6–7 pm"; for a rule
  it can't put in words, "Next: Nov 12, 7–8:30 pm". With no repeating
  meetings, it gives the next one-off that way; with nothing to come,
  "None scheduled"; with no calendar, nothing. Past meetings, cancelled
  ones, and all-day events never count. (`meetingTimesText`, in
  `structure_rules.gs`, words it, tested under Node.) The
  columns but Email list are fitted to their text whenever it changes.
  Rows are read by their columns' headings, so when the columns change, the
  next run reads the sheet as it was and lays it out afresh (losing only
  the notes on Parent cells).
- **The doc**, "Circle Structure", which anyone
  with the link can view (or, if the domain doesn't allow that, anyone in
  it): under a grey line saying it's kept up to date automatically, and
  to ask the Coordinating Circle to move a group, the tree as a bulleted
  list (bullets at every level, each level 0.25" further in, 2 pt apart),
  each group's name followed by small (8 pt) Members, Documents, Email,
  and Agenda (if it has an agenda doc) links, and then the groups not
  yet placed. It's rewritten whenever what it shows
  changes, so edits to it don't last. (Zoom rooms are left out, since
  anyone with the link can view it.)
- **Each agenda doc's Parent Circle and Sub-Circles rows**, in About this
  circle. Parent Circle: the parent; Sub-Circles: each sub-circle and
  working group. Each is linked to its agenda doc. Each label cell has a small grey
  "(Managed here. See Circle Structure.)" line, "here" linking the sheet
  and "Circle Structure" the doc. The bullets are
  written with DocumentApp, not the Docs API, so nothing depends on reading
  the doc's positions just after writing it (see below). A doc can't lock
  part of itself, so the script puts the rows back when they're edited:
  every 10 minutes, in each doc changed since (as for the Term ends dates),
  it writes them if they don't say what they should.

How it stays up to date:

- **`syncCircleStructureGroups`**, hourly, brings the rows up to date with
  the Google Groups whose footers link a Gather group and a Drive item:
  adds a row for a new circle or working group (with an empty Parent),
  renames (in the Parent cells too), relinks, retypes, and reads each
  agenda doc's role holders, the guests, and the meeting times, and removes the row of a group that's gone
  (only when every footer could be read) or is no longer a circle or
  working group (not when its Drive folder just couldn't be read). The rows stay in order by
  name.
- **`applyStructure`** runs on each edit
  of the sheet (`onCircleStructureEdit`, as `admin@`, whoever made the
  edit), every 10 minutes from `buildNewCircleDocs`, and after each sync.
  It refuses a Parent that breaks the rules, or makes a loop (pasting can
  get past a dropdown), putting back the parent from before, with a note
  on the cell saying what was refused and why; updates the dropdowns and
  the Problems column; rewrites the doc if it's changed; and rewrites the
  rows of each agenda doc whose place changed (its path, its parent, or its
  sub-circles, their names or links). The parents last applied, and hashes
  of each group's place and of the doc, are in Script Properties.
- **"Circle structure placements.json"**, in `admin@`'s My Drive, holds
  parents from the Python scripts, by Gather group ID: `add_new_group.py`
  writes a new group's, and `seed_circle_structure.py` every group's from
  the old wiki page (see `util/circle_structure_placements.py`). Each is
  given to its group when the group has a row whose Parent is empty (and
  the rules allow it), and then removed from the file.
- Daily, `updateRolesInCircleDocs` rewrites every doc's rows that are out of
  date (any the other runs missed).

### Setting it up

1. `clasp push`, then, in the editor, run any function to allow the new
   Sheets permission.
2. Run **`python seed_circle_structure.py`** (`-n` first, to see what it
   would write), to bring over the parents from the old wiki page: it
   writes them to "Circle structure placements.json", to wait for the
   sheet.
3. Run **`setupCircleStructure`**. It makes the Circle Structure folder and,
   in it, the sheet and the doc (or keeps the ones it made before, moving
   them into the folder if they're elsewhere), protects the sheet, shares
   it with `coordinating-circle@` as editors (without an email) and the doc
   with anyone with the link, installs the triggers
   (`installTriggers`), fills in the sheet, gives the groups their parents
   from the placements, writes the doc, rewrites every agenda doc's
   Parent Circle and Sub-Circles rows, and logs the three links. Check the
   sheet's Problems column, and the notes on its Parent cells, for anything
   left to place by hand, and the execution log for groups left out because
   their type couldn't be told.
4. Send the sheet's link to the Coordinating Circle, and put the doc's link
   where people look for the structure (in place of the old wiki page's
   tree, say).

**`refreshCircleStructureEverywhere`**, run in the editor, rewrites the doc
and every agenda doc's rows that don't say what they should, at once.
