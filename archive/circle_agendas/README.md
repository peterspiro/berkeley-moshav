# Circle agenda probes

The probes in `agenda_probe.gs` were run, before the circle agenda docs
were built, on copies of Sociocracy For All's
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

## Running them again

`agenda_probe.gs` is no longer part of the `circle_agendas` Apps Script
project. To run a probe again, copy it into that project (beside
`doc_helpers.gs`, whose describing helpers it uses) and `clasp push`; take
it out again afterwards.
