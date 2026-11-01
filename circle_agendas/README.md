# Circle Agendas

The circle agenda docs (see `../circle-agendas-plan.md`): the template, and
the web app that the links in an agenda doc run.

- `build_template.gs` — builds the agenda template from a blank doc,
  entirely from code
- `web_app.gs` — the web app: takes a link's action, doc, and meeting, and
  shows a page saying what it did
- `meetings.gs` — what the web app does: add the circle's next meeting,
  and, to a meeting, update its times or add an agenda item
- `circles.gs` — which circle a doc belongs to (from the Google Groups'
  footers), and its calendar's events and its members' names
- `agenda_rules.gs` — the agenda's time arithmetic, with no Apps Script
  calls, tested under Node by `tests/test_circle_agenda_rules.py`
- `agenda_probe.gs` — tests, already run, of what Apps Script can do to an
  agenda doc
- `doc_helpers.gs` — the template's ID (`TEMPLATE_DOC_ID`), and helpers the
  others share
- `appsscript.json` — the advanced services (Docs, Drive, Calendar, Admin
  Directory, Groups Settings), OAuth scopes, and web app settings

All of them are one Apps Script project, so they share their functions and
constants.

## Setting up the project

Use an account that can edit the template. With
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

(The first probe was run by pasting `agenda_probe.gs` into a project made
at https://script.google.com instead, with the Google Docs API added under
**Services**. That still works, but the editor then works out which
permissions to ask for from the code, and can miss some: if a run stops
with "You do not have permission to call DriveApp.File.makeCopy", click the
link in the error and run it again. The clasp project asks for them all at
once, from `appsscript.json`. A pasted project can be deleted once the
clasp one works.)

## Deploying the web app

The template's links run the web app, so it must be deployed, and its
address recorded, before the template is built. Once, in the editor (as
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

- **+ New meeting**, under the MEETINGS heading in the first tab, adds the
  circle's next meeting in a tab of its own, titled with its date, just
  after the first tab (so the newest is first): the first timed event on
  the circle's calendar
  after the day of the newest meeting in the doc (or after now, if there's
  none). It refuses while the newest meeting hasn't started yet. Its date,
  start time, and length come from the event; Place links the circle's
  Zoom room; Attendance lists the circle's members, unchecked, with space
  for guests below; each item's Presenter is a dropdown of the members;
  Minutes links the previous meeting's tab; Next meeting is the event after; and
  the previous meeting's Action Items Recap is copied into its Action Items
  Accountability. The meetings before it lose their links, so they're no
  longer changed. The circle is the Google Group whose footer links the
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
  start time after "Time", the meeting's Duration (hours) (as 1.5 or 1:30), and the other
  items' durations. A duration it can't read turns red and counts as 0; a
  closing round with no time left turns red. The page says why.

Each opens a tab that says what it did, and to close it to return to the
agenda, which already shows the change.
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

## Building the template

The template is built from a blank doc by `build_template.gs`, so it
depends on no other document and can be rebuilt at any time; every change
to it is a change to that file. Building has two passes: DocumentApp lays
out the text and tables, writing a token such as `⟦dropdown outcome
decision⟧` wherever a smart chip or list goes, and then the Docs API creates
the dropdowns' choices, replaces each token with its chip or list, sets the
pageless format, and names the first tab "Circle". The template has no
meetings; each is added, in a tab of its own, by "+ New meeting".

Building needs `WEB_APP_URL` (see above), for the "+ New meeting" link.
Rebuilding a doc deletes its meeting tabs.

1. **`previewTemplate`** builds the "Template preview" doc and logs its
   link. The first run makes it, in your My Drive; later runs empty it and
   build it again in place, so it keeps its link and stays wherever you've
   moved it (such as into a circle's folder, to try new meetings). If it's
   been deleted or trashed, a new one is made.
2. **`createTemplate`**, once: builds a new "Circle Agenda Template" doc.
   Move it to a folder of the Shared Drive that `admin@berkeleymoshav.org`
   can edit, and set `TEMPLATE_DOC_ID` in `doc_helpers.gs` to its ID (the
   part of its link between `/d/` and `/edit`).
3. **`buildTemplate`** empties the template and builds it again in place,
   so it keeps its ID, link, and sharing. Anything typed into the template
   by hand is lost; File → Version history can restore an earlier version.
4. **`describeTemplate`** logs the template's structure: each element, each
   table row's text, and its chips. **`describePreview`** logs the same for
   the latest preview, and every link in it.
5. **`checkTemplateAccess`** logs which account the script runs as, and
   whether it can edit the template (and whether the template is locked).
   Run it if a build fails with "Action not allowed", which is all
   DocumentApp says when a change is refused.

## The probes (done)

The probes in `agenda_probe.gs` were run on copies of Sociocracy For All's
public template (`PROBE_SOURCE_DOC_ID`), which they only read; their
results are in the plan. Each copies it to your My Drive, changes only the
copy, and writes what it found at the end of the copy, under "Probe
report". What they insert is marked `⟦probe⟧`.

### `runProbe` (run 2026-10-31)

Copies a Topic row and a whole meeting, adds an attendance checklist, and
tries person and date chips. To check by hand:

1. **Copied Topic row**, just below the first Topic: does it still have its
   Decision or Selection chip?
2. **Copied meeting**, at the top of MEETINGS: are its date chip, its
   Decision and Selection chips, and the checked, struck-through "None as
   first meeting!" all as in the original below it?
3. **Attendance**, in the copied meeting: are Alice, Bob, and Carol
   unchecked? Does checking one strike its name through, and unchecking it
   remove the strikethrough? Is the person chip before Alice struck
   through too?
4. **Another account**: share the copy with a second account (ideally a
   personal Gmail one) and check a name there. Is it struck through for
   them, and for you?
5. **Date chip** at the end of the doc: is it a chip, not plain text?

### `runTabProbe` (run 2026-11-01)

Tests where the blank meeting that new meetings are copied from can be
kept: in a "Blank meeting" tab of each circle's doc, or in one central doc.
It adds the tab to the copy (or uses the template's, if it has one; if the
Docs API can't add tabs, the report says to add one to the template by
hand), copies the first meeting into it, and copies that back to the top of
MEETINGS. It also makes a second copy of the template, called "Agenda tab
probe, other doc", standing in for a central template, and copies its first
meeting to the top of MEETINGS too. To check by hand, in each copied
meeting (the notes above them say where each came from), and in the
Blank meeting tab:

1. Are the Decision and Selection chips there?
2. Click one: does it open with the same choices as the original, and can
   you pick a different one?
3. In the original meeting, change one chip's choices (click it, then the
   pencil to edit the dropdown, and add a choice). Does the new choice show
   up in any of the copies too? That would mean they share one list of
   choices.

Then send the "Probe report" section and your answers. Both copies can be
deleted afterwards.

The report's counts (such as `DATE×1, UNSUPPORTED×4`) are the chips and
other elements Apps Script found in each place; it calls the dropdown chips
`UNSUPPORTED`. If a copy's counts differ from its original's, something was
lost.

### `runNewTabProbe`

Tests what giving each meeting a tab of its own would need, on a new
scratch doc in your My Drive: it adds a tab with the Docs API, checks
whether DocumentApp can see it (with the doc opened before the tab was
added, opened again at once, and after 5 seconds), writes a paragraph and a
table into it with DocumentApp, and adds a dropdown in it with the Docs
API. The log, and the end of the scratch doc's first tab, say how each step
went; check by hand that the "Meeting test" tab shows the paragraph, the
table, and a working dropdown.

Then run **`runNewTabProbeLater`**, a separate run on the same scratch doc,
to see whether DocumentApp sees the tab once the run that added it is over.
