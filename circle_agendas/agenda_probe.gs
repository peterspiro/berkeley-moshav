/**
 * Agenda Probe
 *
 * Tests, run by hand before building the circle agenda web app (see
 * circle-agendas-plan.md), of what Apps Script can do to an agenda doc. They
 * never change the template (TEMPLATE_DOC_ID in doc_helpers.gs): each makes
 * a copy, works on the copy, and writes what it found at the end of the
 * copy, under "Probe report".
 *
 * runProbe, on the copy:
 *   1. lists the doc's structure: its top-level elements, its tables, and
 *      the kinds of smart chip (date, person, dropdown, ...) Apps Script
 *      and the Docs API see in it;
 *   2. copies a Topic row of the agenda and inserts the copy below it, to
 *      see whether the row's dropdown chip (Decision / Selection / Report)
 *      survives;
 *   3. copies the whole MEETINGS section and inserts the copy at the top
 *      of it (the way a new meeting would be added), to see whether the
 *      date chip, dropdown chips, and checklist ticks survive;
 *   4. shows how the Docs API describes the checklists, including whether
 *      a ticked item can be told apart from an unticked one;
 *   5. adds three names to the first Attendance cell as an unticked
 *      checklist, and tries to put a person chip in front of the first;
 *   6. tries to insert a date chip at the end of the doc.
 *
 * runTabProbe tests where the blank meeting that new meetings are copied
 * from can be kept. On the copy, it:
 *   1. adds a "Blank meeting" tab (if the copy has none) and copies the
 *      first meeting into it;
 *   2. copies the meeting in that tab back to the top of MEETINGS in the
 *      first tab;
 *   3. makes a second copy of the template, standing in for a central
 *      template doc, and copies its first meeting to the top of MEETINGS;
 *   4. shows how the Docs API describes the dropdown chips in each place,
 *      to see whether a copied chip still refers to a definition its doc
 *      has.
 *
 * Each step that inserts something marks it with PROBE_MARK. A step that
 * fails is reported and the rest still run.
 *
 * Setup and what to check by hand: see README.md in this folder.
 */

const BLANK_MEETING_TAB = 'Blank meeting';
const TEST_NAMES = ['Alice Example', 'Bob Example', 'Carol Example'];

function runProbe() {
  runSteps('Agenda probe', [
    describeStructure, copyTopicRow, copyMeetings, describeChecklists,
    addAttendanceChecklist, insertDateChip,
  ]);
}

function runTabProbe() {
  runSteps('Agenda tab probe', [
    addBlankMeetingTab, copyMeetingFromTab, copyMeetingFromOtherDoc, describeDropdowns,
  ]);
}

// Copies the template, runs each step on the copy, and writes their report.
function runSteps(name, steps) {
  const copy = DriveApp.getFileById(TEMPLATE_DOC_ID)
    .makeCopy(name + ' ' + new Date().toISOString().slice(0, 16));
  const docId = copy.getId();
  Logger.log('Working on a copy: ' + copy.getUrl());

  const report = ['Copy of ' + TEMPLATE_DOC_ID + ', made ' + new Date()];
  for (const step of steps) {
    report.push('', '== ' + step.name);
    try {
      report.push(...step(docId));
    } catch (e) {
      report.push('FAILED: ' + e.message);
    }
  }

  writeReport(docId, report);
  Logger.log(report.join('\n'));
  Logger.log('Report written at the end of ' + copy.getUrl());
}

// ── runProbe ─────────────────────────────────────────────────────────────────
// Step 1, describeStructure, is in doc_helpers.gs.

// ── Step 2: copy a Topic row ─────────────────────────────────────────────────

function copyTopicRow(docId) {
  const doc = DocumentApp.openById(docId);
  const found = findRow(doc.getBody(), text => text.startsWith('Topic'));
  if (!found) return ['No row starting "Topic" found.'];
  const {table, row} = found;
  const index = table.getChildIndex(row);
  const inserted = table.insertTableRow(index + 1, row.copy());
  inserted.getCell(inserted.getNumCells() > 2 ? 2 : 0).editAsText().insertText(0, PROBE_MARK + ' ');
  doc.saveAndClose();
  return [
    'Copied row ' + index + ' of its table and inserted the copy below it (marked ' + PROBE_MARK + ').',
    'original: ' + countLeaves(row),
    'copy:     ' + countLeaves(inserted),
    'CHECK BY HAND: does the copy still have its Decision/Selection chip?',
  ];
}

// ── Step 3: copy the MEETINGS section ────────────────────────────────────────

function copyMeetings(docId) {
  const doc = DocumentApp.openById(docId);
  const body = doc.getBody();
  let start = -1;
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getText && el.getText().trim().toUpperCase() === 'MEETINGS') start = i;
  }
  if (start < 0) return ['No "MEETINGS" heading found.'];

  const originals = [];
  for (let i = start + 1; i < body.getNumChildren(); i++) originals.push(body.getChild(i));

  const T = DocumentApp.ElementType;
  let pos = start + 1;
  body.insertParagraph(pos++, PROBE_MARK + ' Copy of the meetings below starts here');
  const copies = [];
  const skipped = [];
  for (const el of originals) {
    const type = el.getType();
    if (type === T.PARAGRAPH) copies.push(body.insertParagraph(pos++, el.copy()));
    else if (type === T.TABLE) copies.push(body.insertTable(pos++, el.copy()));
    else if (type === T.LIST_ITEM) copies.push(body.insertListItem(pos++, el.copy()));
    else skipped.push(String(type));
  }
  body.insertParagraph(pos, PROBE_MARK + ' Copy ends here');
  doc.saveAndClose();

  return [
    'Copied ' + copies.length + ' elements after MEETINGS to the top of the section' +
      (skipped.length ? '; skipped ' + skipped.join(', ') : '') + '.',
    'originals: ' + mergeCounts(originals),
    'copies:    ' + mergeCounts(copies),
    'CHECK BY HAND: in the copy, are the date chip, the Decision/Selection chips, ' +
      'and the ticked "None as first meeting!" all as in the original?',
  ];
}

// ── Step 4: how checklists look to the Docs API ──────────────────────────────

function describeChecklists(docId) {
  const doc = Docs.Documents.get(docId);
  const lines = [];
  walkParagraphs(doc.body.content, para => {
    if (!para.bullet) return;
    const list = doc.lists[para.bullet.listId];
    const level = list && list.listProperties.nestingLevels[para.bullet.nestingLevel || 0];
    const run = (para.elements || []).find(pe => pe.textRun);
    lines.push('"' + snippet(paraText(para), 30) + '"' +
      '  bullet=' + JSON.stringify(para.bullet).slice(0, 150) +
      '  level=' + JSON.stringify(level).slice(0, 200) +
      '  strikethrough=' + Boolean(run && run.textRun.textStyle.strikethrough));
  });
  return lines.length ? lines : ['No bulleted paragraphs found.'];
}

// ── Step 5: attendance checklist, and a person chip ──────────────────────────

function addAttendanceChecklist(docId) {
  const doc = Docs.Documents.get(docId);
  let cell = null;
  walkTableRows(doc.body.content, row => {
    if (cell) return;
    const cells = row.tableCells;
    for (let c = 0; c + 1 < cells.length; c++) {
      if (cellText(cells[c]).trim() === 'Attendance') cell = cells[c + 1];
    }
  });
  if (!cell) return ['No "Attendance" cell found.'];

  const index = cell.content[0].startIndex;
  const text = TEST_NAMES.join('\n');
  Docs.Documents.batchUpdate({requests: [
    {insertText: {location: {index}, text}},
    {createParagraphBullets: {
      range: {startIndex: index, endIndex: index + text.length},
      bulletPreset: 'BULLET_CHECKBOX',
    }},
  ]}, docId);
  const lines = [
    'Added ' + TEST_NAMES.join(', ') + ' to the first Attendance cell as a checklist.',
    'CHECK BY HAND: are they unticked? Does ticking one strike it through? ' +
      'Does it for another account too?',
  ];

  const email = Session.getActiveUser().getEmail();
  try {
    Docs.Documents.batchUpdate({requests: [
      {insertPerson: {location: {index}, personProperties: {email}}},
    ]}, docId);
    lines.push('Person chip for ' + email + ' inserted before "' + TEST_NAMES[0] + '".',
      'CHECK BY HAND: does ticking that line strike through the chip too?');
  } catch (e) {
    lines.push('Person chip FAILED: ' + e.message);
  }
  return lines;
}

// ── Step 6: date chip ────────────────────────────────────────────────────────

function insertDateChip(docId) {
  const content = Docs.Documents.get(docId).body.content;
  const end = content[content.length - 1].endIndex - 1;
  const label = '\n' + PROBE_MARK + ' Date chip test: ';
  Docs.Documents.batchUpdate({requests: [{insertText: {location: {index: end}, text: label}}]}, docId);
  try {
    Docs.Documents.batchUpdate({requests: [{insertDate: {
      location: {index: end + label.length},
      dateElementProperties: {timestamp: new Date().toISOString()},
    }}]}, docId);
    return ['Date chip inserted at the end of the doc. CHECK BY HAND that it is a chip, not text.'];
  } catch (e) {
    return ['Date chip FAILED: ' + e.message];
  }
}

// ── runTabProbe, step 1: a Blank meeting tab ─────────────────────────────────

function addBlankMeetingTab(docId) {
  const lines = [];
  if (!findTab(DocumentApp.openById(docId), BLANK_MEETING_TAB)) {
    try {
      Docs.Documents.batchUpdate({requests: [
        {addDocumentTab: {tabProperties: {title: BLANK_MEETING_TAB}}},
      ]}, docId);
      lines.push('Added a "' + BLANK_MEETING_TAB + '" tab with the Docs API.');
    } catch (e) {
      return ['Could not add a tab with the Docs API: ' + e.message,
        'To test copying from a tab anyway, add a tab named "' + BLANK_MEETING_TAB +
        '" to the template by hand and run runTabProbe again.'];
    }
  } else {
    lines.push('The template already has a "' + BLANK_MEETING_TAB + '" tab.');
  }

  const doc = DocumentApp.openById(docId);
  const tabBody = findTab(doc, BLANK_MEETING_TAB).asDocumentTab().getBody();
  const meeting = firstMeetingTable(firstTabBody(doc));
  if (!meeting) return lines.concat('No meeting table (one containing "Closing Round") in the first tab.');
  tabBody.insertParagraph(0, PROBE_MARK + ' Copied here from the first tab');
  const copy = tabBody.insertTable(1, meeting.copy());
  doc.saveAndClose();
  return lines.concat(
    'Copied the first meeting into it.',
    'original: ' + countLeaves(meeting),
    'copy:     ' + countLeaves(copy));
}

// ── runTabProbe, step 2: copy from the tab ───────────────────────────────────

function copyMeetingFromTab(docId) {
  const doc = DocumentApp.openById(docId);
  const tab = findTab(doc, BLANK_MEETING_TAB);
  if (!tab) return ['No "' + BLANK_MEETING_TAB + '" tab; skipped.'];
  const meeting = firstMeetingTable(tab.asDocumentTab().getBody());
  if (!meeting) return ['No meeting table in the "' + BLANK_MEETING_TAB + '" tab; skipped.'];
  const copy = insertMeetingAtTop(firstTabBody(doc), meeting,
    'Copied from the "' + BLANK_MEETING_TAB + '" tab');
  doc.saveAndClose();
  return [
    'Copied the meeting in the "' + BLANK_MEETING_TAB + '" tab to the top of MEETINGS.',
    'original: ' + countLeaves(meeting),
    'copy:     ' + countLeaves(copy),
  ];
}

// ── runTabProbe, step 3: copy from another doc ───────────────────────────────

function copyMeetingFromOtherDoc(docId) {
  const other = DriveApp.getFileById(TEMPLATE_DOC_ID)
    .makeCopy('Agenda tab probe, other doc ' + new Date().toISOString().slice(0, 16));
  const meeting = firstMeetingTable(firstTabBody(DocumentApp.openById(other.getId())));
  if (!meeting) return ['No meeting table in the other doc; skipped.'];
  const doc = DocumentApp.openById(docId);
  const copy = insertMeetingAtTop(firstTabBody(doc), meeting,
    'Copied from another doc, ' + other.getName());
  doc.saveAndClose();
  return [
    'Copied the first meeting of another copy of the template (' + other.getUrl() +
      ') to the top of MEETINGS.',
    'original: ' + countLeaves(meeting),
    'copy:     ' + countLeaves(copy),
  ];
}

// ── runTabProbe, step 4: dropdowns ───────────────────────────────────────────

// For each tab, the first dropdown chip in each top-level table, labelled
// with the probe's note above the table, if any; and what else the API
// holds for the tab besides its body, in case dropdown definitions are kept
// there.
function describeDropdowns(docId) {
  const lines = [];
  const doc = Docs.Documents.get(docId, {includeTabsContent: true});
  for (const tab of allApiTabs(doc.tabs)) {
    const dt = tab.documentTab;
    lines.push('Tab "' + tab.tabProperties.title + '": parts ' + JSON.stringify(Object.keys(dt)));
    for (const key of Object.keys(dt)) {
      if (key !== 'body' && key !== 'lists' && key !== 'namedStyles' && key !== 'documentStyle') {
        lines.push('  ' + key + ': ' + JSON.stringify(dt[key]).slice(0, 300));
      }
    }
    let label = '';
    for (const se of dt.body.content) {
      if (se.paragraph) {
        const text = paraText(se.paragraph).trim();
        if (text) label = text;
      }
      if (!se.table) continue;
      const dropdowns = [];
      walkParagraphs([se], para => {
        for (const pe of para.elements || []) {
          if (pe.dropdown) dropdowns.push(pe.dropdown.dropdownProperties);
        }
      });
      if (dropdowns.length) {
        lines.push('  table after "' + snippet(label, 50) + '": ' + dropdowns.length +
          ' dropdowns; first: ' + JSON.stringify(dropdowns[0]).slice(0, 300));
      }
    }
  }
  return lines;
}

// ── runTabProbe helpers ──────────────────────────────────────────────────────

// Inserts a copy of a meeting table just below the MEETINGS heading, after
// a note saying where it came from, and returns the copy.
function insertMeetingAtTop(body, meeting, note) {
  let start = -1;
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getText && el.getText().trim().toUpperCase() === 'MEETINGS') start = i;
  }
  if (start < 0) throw new Error('No "MEETINGS" heading found.');
  body.insertParagraph(start + 1, PROBE_MARK + ' ' + note);
  return body.insertTable(start + 2, meeting.copy());
}

// The tab, at any depth, with the given title, or null.
function findTab(doc, title) {
  const search = tabs => {
    for (const tab of tabs) {
      if (tab.getTitle() === title) return tab;
      const found = search(tab.getChildTabs());
      if (found) return found;
    }
    return null;
  };
  return search(doc.getTabs());
}

function firstTabBody(doc) {
  return doc.getTabs()[0].asDocumentTab().getBody();
}

// The first top-level table containing "Closing Round", or null.
function firstMeetingTable(body) {
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() === DocumentApp.ElementType.TABLE && el.getText().includes('Closing Round')) {
      return el;
    }
  }
  return null;
}
