# Circle Agendas

This folder will hold the web app behind the circle agenda docs (see
`../circle-agendas-plan.md`). For now it holds only `agenda_probe.gs`, a
one-time test of what Apps Script can do to a doc based on Sociocracy For
All's "Session Circle Agenda" template. Its results decide how the web app
builds new meetings and agenda items.

- `agenda_probe.gs` — the test
- `appsscript.json` — the Docs advanced service and OAuth scopes

## Running the probe

The probe only reads the source doc (`SOURCE_DOC_ID` at the top of the
script). It makes a copy in your My Drive, called "Agenda probe" and the
date, changes only the copy, and writes what it found at the end of the
copy under "Probe report". Run it as any account that can open the source
doc.

Either paste it into a new project:

1. Open https://script.google.com and click **New project**.
2. Replace `Code.gs` with the contents of `agenda_probe.gs`.
3. Click **Services** (+), choose **Google Docs API**, and click **Add**.
4. Choose `runProbe` in the function menu and click **Run**. Allow access
   when asked.

Or push it with clasp:

```bash
cd circle_agendas
clasp create --type standalone --title "Agenda Probe"
git checkout -- appsscript.json  # clasp create overwrites it with a bare default
clasp push -f
clasp open                       # then run runProbe
```

## What to check by hand

The execution log gives the copy's link. Open it and look at the places
marked `⟦probe⟧`:

1. **Copied Topic row**, just below the first Topic: does it still have its
   Decision or Selection chip?
2. **Copied meeting**, at the top of MEETINGS: are its date chip, its
   Decision and Selection chips, and the ticked, struck-through "None as
   first meeting!" all as in the original below it?
3. **Attendance**, in the copied meeting: are Alice, Bob, and Carol
   unticked? Does ticking one strike its name through, and does unticking
   it remove the strikethrough? If a person chip was added before Alice,
   is it struck through too?
4. **Another account**: share the copy with a second account (ideally a
   personal Gmail one) and tick a name there. Is it struck through for
   them, and for you?
5. **Date chip** at the end of the doc, if the report says one was
   inserted: is it a chip (grey, with a calendar picker when clicked), not
   plain text?

Then send the "Probe report" section and your answers to these questions.
The report's counts (such as `DATE×1, UNSUPPORTED×4`) are the chips and
other elements Apps Script found in each place. Apps Script calls anything
it can't represent `UNSUPPORTED`, which is probably what the dropdown chips
are. If a copy's counts differ from its original's, something was lost.
