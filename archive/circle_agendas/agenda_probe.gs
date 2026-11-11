/**
 * Agenda Probe
 *
 * Tests, run by hand before building the circle agenda web app (see
 * circle-agendas-plan.md), of what Apps Script can do to an agenda doc, run
 * on Sociocracy For All's template (PROBE_SOURCE_DOC_ID), which they only
 * read: each makes a copy, works on the copy, and writes what it found at
 * the end of the copy, under "Probe report". Both have been run; their
 * results are in the plan. Archived: it's no longer part of the
 * circle_agendas Apps Script project.
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
 *      date chip, dropdown chips, and checked boxes survive;
 *   4. shows how the Docs API describes the checklists, including whether
 *      a checked item can be told apart from an unchecked one;
 *   5. adds three names to the first Attendance cell as an unchecked
 *      checklist, and tries to put a person chip in front of the first;
 *   6. tries to insert a date chip at the end of the doc.
 *
 * runNewTabProbe tests what giving each meeting a tab of its own would
 * need; see it below.
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
 * runChecklistProbe and runChecklistProbeLater test whether a script can
 * tell a checked checklist item (or one struck through by hand) from an
 * unchecked one, in a copy of a circle agenda doc (CHECKLIST_PROBE_DOC_ID),
 * and whether copy() keeps the check and the strikethrough; see them below.
 *
 * Each step that inserts something marks it with PROBE_MARK. A step that
 * fails is reported and the rest still run.
 *
 * Setup and what to check by hand: see README.md in this folder.
 */

// Sociocracy For All's public "Session Circle Agenda" doc, which the probes
// were run on; they only read it.
const PROBE_SOURCE_DOC_ID = '18sHlQd8IzQlJ-2rEwO9undQSg79-_MCjCnKhmHm81M4';
const BLANK_MEETING_TAB = 'Blank meeting';
const TEST_NAMES = ['Alice Example', 'Bob Example', 'Carol Example'];
// The circle agenda doc runChecklistProbe copies (it only reads it): set it
// to one's ID before running it.
const CHECKLIST_PROBE_DOC_ID = '';

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
  const copy = copyDoc(PROBE_SOURCE_DOC_ID, name + ' ' + new Date().toISOString().slice(0, 16));
  const docId = copy.getId();
  logLink('Working on a copy:', copy.getUrl());

  const report = ['Copy of ' + PROBE_SOURCE_DOC_ID + ', made ' + new Date()];
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
  logLink('Report written at the end of the copy:', copy.getUrl());
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
      'and the checked "None as first meeting!" all as in the original?',
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
    'CHECK BY HAND: are they unchecked? Does checking one strike it through? ' +
      'Does it for another account too?',
  ];

  const email = Session.getActiveUser().getEmail();
  try {
    Docs.Documents.batchUpdate({requests: [
      {insertPerson: {location: {index}, personProperties: {email}}},
    ]}, docId);
    lines.push('Person chip for ' + email + ' inserted before "' + TEST_NAMES[0] + '".',
      'CHECK BY HAND: does checking that line strike through the chip too?');
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
  const other = copyDoc(PROBE_SOURCE_DOC_ID,
    'Agenda tab probe, other doc ' + new Date().toISOString().slice(0, 16));
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

// ── runNewTabProbe ───────────────────────────────────────────────────────────

// Tests what giving each meeting a tab would need: whether DocumentApp can
// see, and write into, a tab the Docs API has just added in the same run
// (runTabProbe found it couldn't, once), after waiting or not; and whether
// a dropdown can be added in that tab (each tab keeps its own dropdowns'
// choices). Works on a new scratch doc in your My Drive, whose link it logs.
function runNewTabProbe() {
  const scratch = DocumentApp.create('Agenda new-tab probe ' + new Date().toISOString().slice(0, 16));
  const docId = scratch.getId();
  scratch.getBody().appendParagraph('First tab: the report is below.');
  scratch.saveAndClose();
  const lines = [];
  const step = (name, fn) => {
    try {
      lines.push(name + ': ' + fn());
    } catch (e) {
      lines.push(name + ': FAILED: ' + e.message);
    }
  };

  // The doc open in DocumentApp before the tab is added, as in runTabProbe.
  const before = DocumentApp.openById(docId);
  let tabId = null;
  step('Add a tab with the Docs API', () => {
    const reply = Docs.Documents.batchUpdate({requests: [
      {addDocumentTab: {tabProperties: {title: 'Meeting test'}}},
    ]}, docId);
    tabId = reply.replies[0].addDocumentTab.tabProperties.tabId;
    return 'tab ' + tabId;
  });
  if (!tabId) return finishNewTabProbe(docId, lines);

  const sees = doc => {
    const titles = doc.getTabs().map(t => t.getTitle());
    let byId = 'no';
    try {
      byId = doc.getTab(tabId) ? 'yes' : 'no';
    } catch (e) {
      byId = 'no (' + e.message + ')';
    }
    return 'tabs ' + JSON.stringify(titles) + '; getTab(id): ' + byId;
  };
  step('1. The doc opened before the tab was added', () => sees(before));
  step('2. Opened again at once', () => sees(DocumentApp.openById(docId)));
  Utilities.sleep(5000);
  step('3. Opened again after 5 seconds', () => sees(DocumentApp.openById(docId)));

  step('4. Write a paragraph and a table into the tab with DocumentApp', () => {
    const doc = DocumentApp.openById(docId);
    const tab = doc.getTab(tabId);
    if (!tab) return 'tab not visible, so nothing written';
    const body = tab.asDocumentTab().getBody();
    body.appendParagraph(PROBE_MARK + ' Written by DocumentApp');
    body.appendTable([['Time', 'Description'], ['19:30', 'Opening round']]);
    doc.saveAndClose();
    return 'written';
  });

  step('5. Add a dropdown in the tab with the Docs API', () => {
    const content = Docs.Documents.get(docId, {includeTabsContent: true}).tabs
      .find(t => t.tabProperties.tabId === tabId).documentTab.body.content;
    const end = content[content.length - 1].endIndex - 1;
    const label = '\n' + PROBE_MARK + ' Dropdown: ';
    Docs.Documents.batchUpdate({requests: [
      {createDropdownDefinition: {tabId, dropdownDefinition: {
        dropdownDefinitionId: 'kix.probe',
        dropdownDefinitionProperties: {title: 'Probe', options: [
          {optionId: 'dropdownItem.first', displayValue: 'First'},
          {optionId: 'dropdownItem.second', displayValue: 'Second'},
        ]},
      }}},
      {insertText: {location: {index: end, tabId}, text: label}},
      {insertDropdown: {location: {index: end + label.length, tabId},
        dropdownDefinitionId: 'kix.probe'}},
    ]}, docId);
    return 'added';
  });

  step('6. The tab\'s own link', () =>
    'https://docs.google.com/document/d/' + docId + '/edit?tab=' + tabId);
  finishNewTabProbe(docId, lines);
}

function finishNewTabProbe(docId, lines) {
  PropertiesService.getScriptProperties().setProperty('newTabProbeDocId', docId);
  writeReport(docId, lines);
  Logger.log(lines.join('\n'));
  logLink('Scratch doc (report at the end of its first tab):',
    'https://docs.google.com/document/d/' + docId + '/edit');
}

// Run after runNewTabProbe, as a separate run: whether DocumentApp sees the
// tab runNewTabProbe added in its own run, and can write into it; and
// whether a dropdown can be added in it.
function runNewTabProbeLater() {
  const docId = PropertiesService.getScriptProperties().getProperty('newTabProbeDocId');
  if (!docId) throw new Error('Run runNewTabProbe first.');
  const lines = ['Later run:'];
  const step = (name, fn) => {
    try {
      lines.push(name + ': ' + fn());
    } catch (e) {
      lines.push(name + ': FAILED: ' + e.message);
    }
  };
  const tabId = Docs.Documents.get(docId, {includeTabsContent: true}).tabs
    .map(t => t.tabProperties).find(p => p.title === 'Meeting test').tabId;

  step('7. DocumentApp sees the tab', () => {
    const doc = DocumentApp.openById(docId);
    return 'tabs ' + JSON.stringify(doc.getTabs().map(t => t.getTitle())) +
      '; getTab(id): ' + (doc.getTab(tabId) ? 'yes' : 'no');
  });
  step('8. Write a paragraph and a table into the tab with DocumentApp', () => {
    const doc = DocumentApp.openById(docId);
    const tab = doc.getTab(tabId);
    if (!tab) return 'tab not visible, so nothing written';
    const body = tab.asDocumentTab().getBody();
    body.appendParagraph(PROBE_MARK + ' Written by DocumentApp in a later run');
    body.appendTable([['Time', 'Description'], ['19:30', 'Opening round']]);
    doc.saveAndClose();
    return 'written';
  });
  step('9. Add a dropdown in the tab with the Docs API', () => {
    const content = Docs.Documents.get(docId, {includeTabsContent: true}).tabs
      .find(t => t.tabProperties.tabId === tabId).documentTab.body.content;
    const end = content[content.length - 1].endIndex - 1;
    const label = '\n' + PROBE_MARK + ' Dropdown: ';
    Docs.Documents.batchUpdate({requests: [
      {createDropdownDefinition: {tabId, dropdownDefinition: {
        dropdownDefinitionId: 'kix.probe2',
        dropdownDefinitionProperties: {title: 'Probe', options: [
          {optionId: 'dropdownItem.first', displayValue: 'First'},
          {optionId: 'dropdownItem.second', displayValue: 'Second'},
        ]},
      }}},
      {insertText: {location: {index: end, tabId}, text: label}},
      {insertDropdown: {location: {index: end + label.length, tabId},
        dropdownDefinitionId: 'kix.probe2'}},
    ]}, docId);
    return 'added';
  });
  writeReport(docId, lines);
  Logger.log(lines.join('\n'));
  logLink('Scratch doc:', 'https://docs.google.com/document/d/' + docId + '/edit?tab=' + tabId);
}


// ── runChecklistProbe ────────────────────────────────────────────────────────

// Adds a checklist of three items, marked PROBE_MARK, to the Notes cell of
// the Action Items Accountability row of a copy of a circle agenda doc
// (CHECKLIST_PROBE_DOC_ID), with the Docs API's BULLET_CHECKBOX, as
// Accountability's checklist is made. Then says what to do to them by hand
// before running runChecklistProbeLater.
function runChecklistProbe() {
  if (!CHECKLIST_PROBE_DOC_ID) {
    throw new Error('Set CHECKLIST_PROBE_DOC_ID to the ID of a circle agenda doc first.');
  }
  const copy = copyDoc(CHECKLIST_PROBE_DOC_ID,
    'Agenda checklist probe ' + new Date().toISOString().slice(0, 16));
  const docId = copy.getId();
  logLink('Working on a copy:', copy.getUrl());

  const doc = Docs.Documents.get(docId, {includeTabsContent: true});
  let found = null;
  for (const tab of allApiTabs(doc.tabs)) {
    walkTableRows(tab.documentTab.body.content, row => {
      const description = row.tableCells[COL.description];
      if (!found && description && cellText(description).trim().startsWith('Action Items Accountability')) {
        found = {tab, row};
      }
    });
    if (found) break;
  }
  if (!found) throw new Error('No Action Items Accountability row found in the copy.');

  const tabId = found.tab.tabProperties.tabId;
  const index = found.row.tableCells[COL.notes].content[0].startIndex;
  const text = CHECKLIST_PROBE_ITEMS.map((item, i) => PROBE_MARK + ' ' + (i + 1) + ': ' + item)
    .join('\n') + '\n';
  // The inserted lines take the cell's first paragraph's bullet, which is
  // removed before the checklist is made; that paragraph, after them, is
  // left alone.
  const range = {startIndex: index, endIndex: index + text.length - 1, tabId};
  Docs.Documents.batchUpdate({requests: [
    {insertText: {location: {index, tabId}, text}},
    {deleteParagraphBullets: {range}},
    {createParagraphBullets: {range, bulletPreset: 'BULLET_CHECKBOX'}},
  ]}, docId);
  PropertiesService.getScriptProperties()
    .setProperty('checklistProbe', JSON.stringify({docId, tabId}));

  const url = 'https://docs.google.com/document/d/' + docId + '/edit?tab=' + tabId;
  const lines = [
    'Copy of ' + CHECKLIST_PROBE_DOC_ID + ', made ' + new Date(),
    'Added a checklist of three items (marked ' + PROBE_MARK + ') to the Notes cell of ' +
      'Action Items Accountability in the tab "' + found.tab.tabProperties.title + '": ' + url,
    'NOW BY HAND: check item 1; select all of item 2\'s text and strike it through ' +
      '(Format → Text → Strikethrough), leaving it unchecked; leave item 3 alone. ' +
      'Then run runChecklistProbeLater.',
  ];
  writeReport(docId, lines);
  Logger.log(lines.join('\n'));
  logLink('The checklist is in:', url);
}

// Run after runChecklistProbe and the changes by hand it asks for. Reports
// everything the Docs API and Apps Script return for each of the three
// items; then copies each, with copy(), into the Follow-up cell of the same
// row, and reports whether the copies came out the same.
function runChecklistProbeLater() {
  const saved = PropertiesService.getScriptProperties().getProperty('checklistProbe');
  if (!saved) throw new Error('Run runChecklistProbe first.');
  const {docId, tabId} = JSON.parse(saved);
  const lines = ['Later run, ' + new Date() + ':', '',
    'Expected: item 1 checked, item 2 struck through by hand (unchecked), item 3 neither.'];

  const originals = checklistProbeDescribe(docId, tabId, COL.notes);
  if (originals.length !== CHECKLIST_PROBE_ITEMS.length) {
    lines.push('Found ' + originals.length + ' of the ' + CHECKLIST_PROBE_ITEMS.length +
      ' items in the Notes cell, so stopped. Were they edited or moved?');
    writeReport(docId, lines);
    Logger.log(lines.join('\n'));
    return;
  }
  for (const item of originals) lines.push('', ...checklistProbeLines('Item ' + item.n, item));

  lines.push('', '== Copies');
  try {
    const doc = DocumentApp.openById(docId);
    const items = checklistProbeListItems(doc.getTab(tabId).asDocumentTab().getBody(), COL.notes);
    const target = items[0].getParent().asTableCell().getParentRow().getCell(COL.followUp);
    items.forEach((item, i) => target.insertListItem(i, item.copy()));
    doc.saveAndClose();
    lines.push('Copied each item with copy() into the Follow-up cell of the same row, ' +
      'at its top. CHECK BY HAND: is the copy of item 1 checked, and is the copy of item 2 ' +
      'struck through (and unchecked)?');

    const copies = checklistProbeDescribe(docId, tabId, COL.followUp);
    for (const copy of copies) {
      const original = originals.find(o => o.n === copy.n);
      lines.push('', ...checklistProbeLines('Copy of item ' + copy.n, copy));
      lines.push('Compared with item ' + copy.n + ':');
      for (const key of Object.keys(copy.compare)) {
        lines.push('  ' + key + ': ' +
          (copy.compare[key] === original.compare[key] ? 'same' : 'DIFFERENT'));
      }
    }
  } catch (e) {
    lines.push('FAILED: ' + e.message);
  }

  writeReport(docId, lines);
  Logger.log(lines.join('\n'));
  logLink('Report written at the end of the copy\'s first tab:',
    'https://docs.google.com/document/d/' + docId + '/edit');
}

// The text of the probe's three checklist items.
const CHECKLIST_PROBE_ITEMS = ['check this one', 'strike this one through', 'leave this one'];

// The probe's items in one column of their row (COL.notes, where they were
// added, or COL.followUp, where they were copied), sorted by number, as
// both APIs see them: {n, api, apps, compare}. api is what the Docs API
// returns (the paragraph's bullet, its list's nesting level, the paragraph
// style, and the text style of each run); apps is what Apps Script returns
// (the list item's glyph type, list ID, and attributes, and each text run's
// attributes); compare holds those as JSON, without the indices and IDs that
// differ wherever an item is, for comparing a copy with its original.
function checklistProbeDescribe(docId, tabId, column) {
  const tab = allApiTabs(Docs.Documents.get(docId, {includeTabsContent: true}).tabs)
    .find(t => t.tabProperties.tabId === tabId);
  const lists = tab.documentTab.lists || {};
  const api = {};
  walkTableRows(tab.documentTab.body.content, row => {
    const cell = row.tableCells[column];
    for (const se of (cell && cell.content) || []) {
      const n = se.paragraph && checklistProbeNumber(paraText(se.paragraph));
      if (!n) continue;
      const para = se.paragraph;
      const list = para.bullet && lists[para.bullet.listId];
      api[n] = {
        bullet: para.bullet || null,
        nestingLevel: list
          ? list.listProperties.nestingLevels[para.bullet.nestingLevel || 0] : null,
        paragraphStyle: para.paragraphStyle || null,
        runs: (para.elements || []).map(pe => ({
          content: pe.textRun ? pe.textRun.content : '(not a text run: ' +
            Object.keys(pe).filter(k => k !== 'startIndex' && k !== 'endIndex').join(', ') + ')',
          textStyle: pe.textRun ? pe.textRun.textStyle : null,
        })),
      };
    }
  });

  const body = DocumentApp.openById(docId).getTab(tabId).asDocumentTab().getBody();
  return checklistProbeListItems(body, column).map(item => {
    const n = checklistProbeNumber(item.getText());
    const text = item.editAsText();
    const value = text.getText();
    const starts = text.getTextAttributeIndices();
    const apps = {
      glyphType: String(item.getGlyphType()),
      listId: item.getListId(),
      attributes: item.getAttributes(),
      runs: starts.map((start, i) => ({
        text: value.slice(start, i + 1 < starts.length ? starts[i + 1] : value.length),
        attributes: text.getAttributes(start),
      })),
    };
    const a = api[n] || {};
    const bullet = a.bullet ? {...a.bullet} : null;
    if (bullet) delete bullet.listId;
    return {n, api: a, apps, compare: {
      'Docs API bullet (but its list ID)': JSON.stringify(bullet),
      'Docs API nesting level': JSON.stringify(a.nestingLevel),
      'Docs API paragraph style': JSON.stringify(a.paragraphStyle),
      'Docs API runs\' text styles': JSON.stringify((a.runs || []).map(r => r.textStyle)),
      'Apps Script glyph type': apps.glyphType,
      'Apps Script attributes': JSON.stringify(apps.attributes),
      'Apps Script runs\' attributes': JSON.stringify(apps.runs.map(r => r.attributes)),
    }};
  }).sort((x, y) => x.n - y.n);
}

// A described item (from checklistProbeDescribe) as report lines.
function checklistProbeLines(title, item) {
  const a = item.api;
  return [
    '== ' + title + (a.bullet ? '' : ' (the Docs API didn\'t find it)'),
    'Docs API bullet: ' + JSON.stringify(a.bullet),
    'Docs API nesting level: ' + JSON.stringify(a.nestingLevel),
    'Docs API paragraph style: ' + JSON.stringify(a.paragraphStyle),
    ...(a.runs || []).map((r, i) => 'Docs API run ' + (i + 1) + ' ' +
      JSON.stringify(r.content) + ': ' + JSON.stringify(r.textStyle)),
    'Apps Script glyph type: ' + item.apps.glyphType + ', list ID: ' + item.apps.listId,
    'Apps Script attributes: ' + JSON.stringify(item.apps.attributes),
    ...item.apps.runs.map((r, i) => 'Apps Script run ' + (i + 1) + ' ' +
      JSON.stringify(r.text) + ': ' + JSON.stringify(r.attributes)),
  ];
}

// The probe's list items in one column of their row, in Apps Script.
function checklistProbeListItems(body, column) {
  const items = [];
  let found = null;
  while ((found = body.findText(PROBE_MARK + ' [0-9]:', found))) {
    let el = found.getElement();
    while (el && el.getType() !== DocumentApp.ElementType.LIST_ITEM) el = el.getParent();
    if (!el) continue;
    const cell = el.getParent();
    if (cell.getType() !== DocumentApp.ElementType.TABLE_CELL) continue;
    const row = cell.asTableCell().getParentRow();
    if (row.getChildIndex(cell) === column) items.push(el.asListItem());
  }
  return items;
}

// An item's number, from the start of its text ("⟦probe⟧ 2: ..."), or 0.
function checklistProbeNumber(text) {
  const match = text.trim().match(/^⟦probe⟧ ([0-9]):/);
  return match ? Number(match[1]) : 0;
}

// ── Helpers only the probes used ─────────────────────────────────────────────
// (Moved here from circle_agendas/doc_helpers.gs when the probes were
// archived. The probes also used describeStructure, walkParagraphs,
// paraText, snippet, and the counting helpers, still in doc_helpers.gs.)

// Marks what the probes insert.
const PROBE_MARK = '⟦probe⟧';

// Copies a doc to the running account's My Drive (not beside the original,
// where everyone who can see its folder would see the copy).
function copyDoc(docId, name) {
  return DriveApp.getFileById(docId).makeCopy(name, DriveApp.getRootFolder());
}

// ── Writing a report ─────────────────────────────────────────────────────────

function writeReport(docId, lines) {
  const doc = DocumentApp.openById(docId);
  const body = doc.getBody();
  body.appendParagraph('Probe report').setHeading(DocumentApp.ParagraphHeading.HEADING2);
  for (const line of lines) {
    body.appendParagraph(line).setHeading(DocumentApp.ParagraphHeading.NORMAL);
  }
  doc.saveAndClose();
}

// Calls fn on every table row in Docs API content, including nested tables.
function walkTableRows(content, fn) {
  for (const se of content || []) {
    if (!se.table) continue;
    for (const row of se.table.tableRows) {
      fn(row);
      for (const cell of row.tableCells) walkTableRows(cell.content, fn);
    }
  }
}

function cellText(cell) {
  return (cell.content || []).filter(se => se.paragraph).map(se => paraText(se.paragraph)).join('');
}

// Every tab in a Docs API document, child tabs included.
function allApiTabs(tabs) {
  return (tabs || []).flatMap(tab => [tab, ...allApiTabs(tab.childTabs)]);
}

function mergeCounts(elements) {
  const counts = {};
  for (const el of elements) addLeafCounts(el, counts);
  return formatCounts(counts);
}
