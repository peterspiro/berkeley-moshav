# Circle Agendas

This folder will hold the web app behind the circle agenda docs (see
`../circle-agendas-plan.md`). For now it holds the scripts used while
designing them:

- `build_template.gs` — builds the agenda template from a blank doc,
  entirely from code
- `agenda_rules.gs` — the agenda's time arithmetic, with no Apps Script
  calls, tested under Node by `tests/test_circle_agenda_rules.py`
- `agenda_probe.gs` — tests, already run, of what Apps Script can do to an
  agenda doc
- `doc_helpers.gs` — the template's ID (`TEMPLATE_DOC_ID`), and helpers the
  others share
- `appsscript.json` — the Docs and Drive advanced services and OAuth scopes

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
choose which of them to allow: tick **Select all**. If you don't, a run
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

## Building the template

The template is built from a blank doc by `build_template.gs`, so it
depends on no other document and can be rebuilt at any time; every change
to it is a change to that file. Building has two passes: DocumentApp lays
out the text and tables, writing a token such as `⟦dropdown outcome
decision⟧` wherever a smart chip or list goes, and then the Docs API creates
the dropdowns' choices, replaces each token with its chip or list, sets the
font and pageless format, and merges and pins the meeting table's top rows.

1. **`previewTemplate`** builds a new "Template preview" doc in your My
   Drive and logs its link. Each run moves the previous preview to the
   trash, so there's only ever one.
2. **`createTemplate`**, once: builds a new "Circle Agenda Template" doc.
   Move it to a folder of the Shared Drive that `admin@berkeleymoshav.org`
   can edit, and set `TEMPLATE_DOC_ID` in `doc_helpers.gs` to its ID (the
   part of its link between `/d/` and `/edit`).
3. **`buildTemplate`** empties the template and builds it again in place,
   so it keeps its ID, link, and sharing. Anything typed into the template
   by hand is lost; File → Version history can restore an earlier version.
4. **`describeTemplate`** logs the template's structure: each element, each
   table row's text, and its chips.
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
   Decision and Selection chips, and the ticked, struck-through "None as
   first meeting!" all as in the original below it?
3. **Attendance**, in the copied meeting: are Alice, Bob, and Carol
   unticked? Does ticking one strike its name through, and unticking it
   remove the strikethrough? Is the person chip before Alice struck
   through too?
4. **Another account**: share the copy with a second account (ideally a
   personal Gmail one) and tick a name there. Is it struck through for
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
