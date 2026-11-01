# Circle Agendas

This folder will hold the web app behind the circle agenda docs (see
`../circle-agendas-plan.md`). For now it holds the scripts used while
designing them:

- `template_edits.gs` — changes to the agenda template, kept as code: each
  is tried on a copy, then applied to the template
- `agenda_probe.gs` — tests of what Apps Script can do to an agenda doc,
  each run on a copy of the template
- `doc_helpers.gs` — the template's ID (`TEMPLATE_DOC_ID`), and helpers the
  others share
- `appsscript.json` — the Docs advanced service and OAuth scopes

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
run asks for access to Docs and Drive.

(The first probe was run by pasting `agenda_probe.gs` into a project made
at https://script.google.com instead, with the Google Docs API added under
**Services**. That still works, but the editor then works out which
permissions to ask for from the code, and can miss some: if a run stops
with "You do not have permission to call DriveApp.File.makeCopy", click the
link in the error and run it again. The clasp project asks for them all at
once, from `appsscript.json`. A pasted project can be deleted once the
clasp one works.)

## Changing the template

Each change is a function in `template_edits.gs`, listed in `EDITS` with an
id. The project's Script Properties record which edits the template has
had, so each is applied once.

1. **`previewEdits`** copies the template to your My Drive as "Template
   preview" and the date, applies the edits the template hasn't had yet to
   the copy, and logs the copy's link. Check it, then delete it.
2. **`applyEdits`** applies those edits to the template itself. If one goes
   wrong, File → Version history in the template can restore it.
3. **`describeTemplate`** logs the template's structure: each element, each
   table row's text, and its chips. Send it back when an edit needs to know
   more about the template.

Each edit logs what it did, or that it found nothing to change.

## Running the probes

The probes never change the template. Each copies it to your My Drive,
changes only the copy, and writes what it found at the end of the copy,
under "Probe report"; the execution log gives the copy's link. What they
insert is marked `⟦probe⟧`.

### `runProbe` (done 2026-10-31; results in the plan)

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

### `runTabProbe`

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
