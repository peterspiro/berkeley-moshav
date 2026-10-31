/**
 * Agenda Probe
 *
 * A one-time test, run before building the circle agenda web app (see
 * circle-agendas-plan.md), of what Apps Script can do to an agenda doc
 * based on Sociocracy For All's "Session Circle Agenda" template. It never
 * changes the source doc: it makes a copy, works on the copy, and writes
 * what it found at the end of the copy, under "Probe report".
 *
 * On the copy, it:
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
 * Each step that inserts something marks it with PROBE_MARK. A step that
 * fails is reported and the rest still run.
 *
 * Setup and what to check by hand: see README.md in this folder.
 */

// The doc to test. Only read; the probe works on a copy of it.
const SOURCE_DOC_ID = '18sHlQd8IzQlJ-2rEwO9undQSg79-_MCjCnKhmHm81M4';
const PROBE_MARK = '⟦probe⟧';
const TEST_NAMES = ['Alice Example', 'Bob Example', 'Carol Example'];

function runProbe() {
  const copy = DriveApp.getFileById(SOURCE_DOC_ID)
    .makeCopy('Agenda probe ' + new Date().toISOString().slice(0, 16));
  const docId = copy.getId();
  Logger.log('Working on a copy: ' + copy.getUrl());

  const report = ['Copy of ' + SOURCE_DOC_ID + ', made ' + new Date()];
  const steps = [
    describeStructure, copyTopicRow, copyMeetings, describeChecklists,
    addAttendanceChecklist, insertDateChip,
  ];
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

// ── Step 1: structure ────────────────────────────────────────────────────────

function describeStructure(docId) {
  const lines = [];
  const body = DocumentApp.openById(docId).getBody();
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    const type = el.getType();
    if (type === DocumentApp.ElementType.TABLE) {
      lines.push(i + ': TABLE, ' + el.getNumRows() + ' rows, ' + countLeaves(el));
      for (let r = 0; r < el.getNumRows(); r++) {
        const row = el.getRow(r);
        const cells = [];
        for (let c = 0; c < row.getNumCells(); c++) {
          cells.push(snippet(row.getCell(c).getText(), 25));
        }
        lines.push('    row ' + r + ': ' + cells.join(' | ') + '   ' + countLeaves(row));
      }
    } else {
      const text = el.getText ? snippet(el.getText(), 50) : '';
      lines.push(i + ': ' + type + ' "' + text + '"   ' + countLeaves(el));
    }
  }

  const kinds = {};
  const examples = {};
  walkParagraphs(Docs.Documents.get(docId).body.content, para => {
    for (const pe of para.elements || []) {
      for (const k of Object.keys(pe)) {
        if (k === 'startIndex' || k === 'endIndex') continue;
        kinds[k] = (kinds[k] || 0) + 1;
        if (k !== 'textRun' && !examples[k]) {
          examples[k] = JSON.stringify(pe[k]).slice(0, 200) + '  in "' + snippet(paraText(para), 40) + '"';
        }
      }
    }
  });
  lines.push('', 'Docs API paragraph element kinds: ' + JSON.stringify(kinds));
  for (const k of Object.keys(examples)) lines.push('  ' + k + ' e.g. ' + examples[k]);
  return lines;
}

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

// ── Report ───────────────────────────────────────────────────────────────────

function writeReport(docId, lines) {
  const doc = DocumentApp.openById(docId);
  const body = doc.getBody();
  body.appendParagraph('Probe report').setHeading(DocumentApp.ParagraphHeading.HEADING2);
  for (const line of lines) {
    body.appendParagraph(line).setHeading(DocumentApp.ParagraphHeading.NORMAL);
  }
  doc.saveAndClose();
}

// ── Helpers ──────────────────────────────────────────────────────────────────

// Counts the non-text leaf elements (chips and anything Apps Script can't
// represent, which it calls UNSUPPORTED) under an element, as a string.
function countLeaves(el) {
  const counts = {};
  addLeafCounts(el, counts);
  return formatCounts(counts);
}

function mergeCounts(elements) {
  const counts = {};
  for (const el of elements) addLeafCounts(el, counts);
  return formatCounts(counts);
}

function addLeafCounts(el, counts) {
  const type = String(el.getType());
  if (el.getNumChildren) {
    for (let i = 0; i < el.getNumChildren(); i++) addLeafCounts(el.getChild(i), counts);
  } else if (type !== 'TEXT') {
    counts[type] = (counts[type] || 0) + 1;
  }
}

function formatCounts(counts) {
  const keys = Object.keys(counts).sort();
  return keys.length ? '[' + keys.map(k => k + '×' + counts[k]).join(', ') + ']' : '[no chips]';
}

// The first table row (at any depth) one of whose cells' text, trimmed,
// passes test.
function findRow(body, test) {
  let result = null;
  let search = body.findElement(DocumentApp.ElementType.TABLE);
  while (search && !result) {
    const table = search.getElement().asTable();
    for (let r = 0; r < table.getNumRows() && !result; r++) {
      const row = table.getRow(r);
      for (let c = 0; c < row.getNumCells(); c++) {
        if (test(row.getCell(c).getText().trim())) result = {table, row};
      }
    }
    search = body.findElement(DocumentApp.ElementType.TABLE, search);
  }
  return result;
}

// Calls fn on every paragraph in Docs API content, including inside tables.
function walkParagraphs(content, fn) {
  for (const se of content || []) {
    if (se.paragraph) fn(se.paragraph);
    if (se.table) {
      for (const row of se.table.tableRows) {
        for (const cell of row.tableCells) walkParagraphs(cell.content, fn);
      }
    }
  }
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

function paraText(para) {
  return (para.elements || []).map(pe => (pe.textRun ? pe.textRun.content : '')).join('');
}

function cellText(cell) {
  return (cell.content || []).filter(se => se.paragraph).map(se => paraText(se.paragraph)).join('');
}

function snippet(text, n) {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > n ? flat.slice(0, n - 1) + '…' : flat;
}
