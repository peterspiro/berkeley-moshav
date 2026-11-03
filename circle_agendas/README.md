# Circle Agendas

The circle agenda docs (see `../circle-agendas-plan.md`): building each
circle's doc, and the web app that the links in an agenda doc run.

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
- `agenda_rules.gs` — the agenda's time arithmetic, with no Apps Script
  calls, tested under Node by `tests/test_circle_agenda_rules.py`
- `doc_helpers.gs` — helpers the others share

The probes that were run before any of this was built, to see what Apps
Script can do to an agenda doc, are in `../archive/circle_agendas/`.
- `appsscript.json` — the advanced services (Docs, Drive, Calendar, Admin
  Directory, Groups Settings), OAuth scopes, and web app settings

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
  `update_groups_in_google_and_hierarchy.py` writes on each run, or else
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
  "<Circle> meeting on Tuesday, November 10: agenda ready", linking the
  meeting's tab, with its time (start to end) and its Zoom room. It's sent
  from the clicker's own Gmail, and can be changed before it's sent. It
  doesn't go through the web app, so it's one click, but the email is
  written into the link: the link is written when the meeting is added, and
  **↻ Update times** rewrites it, from the tab's date, the Time and
  Duration line, and the Zoom room linked in it. So after changing the
  meeting's date or time, click **↻ Update times** before announcing.
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
Drive folder. `update_groups_in_google_and_hierarchy.py --agenda-docs`
(with `-g`, for one group) and `add_new_group.py --agenda-doc` create it
empty and label it in Drive with the group's
address, its parent, and "new" (`util/circle_agenda_docs.py`).
**`buildNewCircleDocs`** builds every doc so labelled, as
`previewTemplate` builds the preview, puts the group's name, email
address, Drive folder, and parent in About this circle, and relabels it
"built", with the layout version (`LAYOUT_VERSION`).

Docs built before About this circle's Zoom room, Drive folder, parent, and
sub-circles were filled in can get them with **`fillAboutForCircleDocs`**,
run in the editor: it fills in those rows where they're empty, in every
built doc, and leaves rows that already say something alone. A doc's
circle is the group whose Drive folder holds it, or else the one with its
labelled address.

When `change_parent_circle.py` moves a circle to another parent, it
relabels the docs of the circle and of its old and new parents with their
new parent and sub-circles; `add_new_group.py` relabels the new group's
parent's doc with its sub-circles; and
`update_groups_in_google_and_hierarchy.py --agenda-docs` relabels every
existing doc whose parent or sub-circles differ from the Circle Structure
page (after an entry was moved there by hand, say). Each labels a built doc
`circleAgendaRefresh: structure`. Each run of `buildNewCircleDocs` then
rewrites the Parent Circle and Sub-Circles rows of every doc so labelled
(**`refreshCircleDocStructures`**), emptying a row with nothing left in it,
and removes the label.

About this circle's rows are, in order: Aims & Domain, Policies folder,
Zoom room, Circle calendar (a link to the circle's Google calendar, which
offers to add it to the clicker's own), Remind before meetings, Circle email
address, Other Key Links, Google Drive Folder (linked by the circle's name,
like the parent and sub-circles), Parent Circle, and Sub-Circles. Docs built
before that order (layout version 1,
with a "Circle Google Drive Folder" row linked as "Google Drive folder")
can be changed over with **`reorderAboutInCircleDocs`**, run in the
editor: in every such doc, it moves the rows the scripts fill in (Zoom
room, email address, Drive folder, parent, sub-circles) into place,
leaving the rows filled in by hand (Aims & Domain, Policies folder, Other
Key Links) where they are, so whatever's in them, smart chips included,
is kept; renames the Drive folder row; rewrites it and the Parent Circle
and Sub-Circles rows from the doc's labels; and labels the doc layout
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

The sub-circles are a bulleted list, one per line. Docs built when they
were a comma-separated line can be changed over with
**`rewriteStructureRowsInCircleDocs`**, run in the editor: it rewrites the
Parent Circle and Sub-Circles rows of every built doc from its labels,
replacing anything typed into those two rows by hand. A doc whose row is
empty because the group has no sub-circles needs nothing: the list
appears when a sub-circle is added.

Once, in the editor, run **`installTriggers`**, which runs
`buildNewCircleDocs` every 10 minutes and `updateRolesInCircleDocs`
every day at 1 am (and asks to allow managing triggers). The Roles
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

Building only ever adds to a doc, so it's done only to one that's still
empty: if someone has written in a doc before it's built, it's labelled
"not empty" and left as it is, and the log says so. A doc is labelled
"building" while it's built, so one whose build fails partway isn't built
again on top of itself; it's left for a person to look at (delete it, and
the next `--agenda-docs` run makes a new one). The Python scripts never
change a doc's contents, only create an empty one when a group has none.

A group's doc is found by its label, so a group never gets a second one;
if its doc is deleted or moved to the trash, the next run of
`update_groups_in_google_and_hierarchy.py --agenda-docs` makes a new one.
The flags keep docs from being made for every group before their layout
is settled: changes to the Circle Info and Backlog tabs don't reach docs
already made (see the plan).
