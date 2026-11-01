/**
 * Build Template
 *
 * Builds the circle agenda template from a blank document, entirely from
 * this code, so it never depends on another document's content and can be
 * rebuilt at any time. Every change to the template is a change here.
 *
 * Run:
 *   - previewTemplate: builds a new "Template preview" doc in your My Drive
 *     and logs its link (each run moves the previous preview to the trash);
 *   - createTemplate: builds a new doc to become the template; move it to
 *     the Shared Drive and put its ID in TEMPLATE_DOC_ID (doc_helpers.gs);
 *   - buildTemplate: empties the template (TEMPLATE_DOC_ID) and builds it
 *     again in place, keeping its ID, link, and sharing;
 *   - describeTemplate: logs the template's structure;
 *   - checkTemplateAccess: logs what the running account may do to it.
 *
 * The template's sample meeting has the links that run the web app
 * (web_app.gs), so building needs the web app's address, in the
 * WEB_APP_URL script property.
 *
 * Building has two passes. The first lays out the text and tables with
 * DocumentApp, writing a token like ⟦dropdown outcome decision⟧ wherever a
 * smart chip or list goes. The second, with the Docs API, which DocumentApp
 * can't do, creates the dropdowns' lists of choices, replaces each token
 * with its chip or list, sets the pageless format, and merges and
 * pins the meeting table's top rows.
 *
 * Setup: see README.md in this folder.
 */

const TIME_ZONE = 'America/Los_Angeles';

const COLOR = {
  title: '#4a7bc8',
  hint: '#5b6f99',
  muted: '#777777',
  border: '#c8c8c8',
  aboutLabel: '#ffd580',
  aboutValue: '#fff3d1',
  rolesHeader: '#7cc896',
  rolesBody: '#e9f6ee',
  policiesHeader: '#9cb8e6',
  policiesBody: '#eef3fb',
  backlogHeader: '#ffd580',
  backlogBody: '#fff3d1',
  roundRow: '#dce6f6',
  reportRow: '#f2f2f2',
};

// The dropdown chips' lists of choices. Each option's key names it in
// tokens and makes its ID; colors are [background, text].
const DROPDOWNS = {
  outcome: {id: 'kix.outcome', title: 'Desired outcome', options: [
    {key: 'report', label: 'Report', colors: ['#c9daf8', '#1c4587']},
    {key: 'exploration', label: 'Exploration', colors: ['#b7e1cd', '#0b5d36']},
    {key: 'decision', label: 'Decision', colors: ['#fce8b2', '#7f6000']},
    {key: 'selection', label: 'Selection', colors: ['#f4c7c3', '#a61c00']},
  ]},
  priority: {id: 'kix.priority', title: 'Priority', options: [
    {key: 'urgent', label: '!!!', colors: ['#a61c00', '#ffffff']},
    {key: 'high', label: '!!', colors: ['#f4c7c3', '#a61c00']},
    {key: 'normal', label: '!', colors: ['#b7e1cd', '#0b5d36']},
    {key: 'someday', label: '💤', colors: ['#c9daf8', '#1c4587']},
  ]},
  prepared: {id: 'kix.prepared', title: 'Prepared?', options: [
    {key: 'yes', label: 'Yes', colors: ['#b7e1cd', '#0b5d36']},
    {key: 'no', label: 'No', colors: ['#f4c7c3', '#a61c00']},
  ]},
};

// The sample meeting at the top of MEETINGS.
const SAMPLE_START = '19:30';
const SAMPLE_HOURS = '1.5';
// Each item before the closing round. kind: 'round' and 'report' items are
// shaded; body: 'checkbox' or 'bullet' adds an empty list item under the
// title; type: a plain label, or outcome, a dropdown chip.
const SAMPLE_AGENDA = [
  {title: 'Opening Round / Check-in', note: ' (no notes)', minutes: 10, kind: 'round'},
  {admin: true, minutes: 3, kind: 'round'},
  {title: 'Consent to Agenda', minutes: 2, type: 'Decision', kind: 'report'},
  {title: 'Action Items Accountability', note: ' (noted from previous meeting)', minutes: 3,
    type: 'Report', body: 'checkbox'},
  {title: 'Topic', minutes: 20, outcome: 'decision', body: 'bullet'},
  {title: 'Topic', minutes: 20, outcome: 'selection', body: 'bullet'},
  {title: 'Topic', minutes: 20, outcome: 'decision', body: 'bullet'},
  {title: 'Action Items Recap', minutes: 5, type: 'Report', body: 'checkbox', kind: 'report',
    note: ' (to paste on next meeting\'s Action Items Accountability section)'},
];
const CLOSING_ROUND = {title: 'Closing Round / Check-out',
  note: ' (feedback on content, interpersonal, processes)', body: 'bullet', kind: 'round'};
// The rows above the agenda items (date, time and place, "Agenda"), each
// merged across the table.
const MEETING_HEADER_ROWS = 3;
// Of those, the date and the time and place stay in view while scrolling
// through the meeting. The "Agenda" row, with the links, isn't pinned:
// Docs shows pinned rows, while they stick, as a copy that can't be
// clicked into, so links there often can't be followed.
const MEETING_PINNED_ROWS = 2;

// ── Entry points ─────────────────────────────────────────────────────────────

function previewTemplate() {
  const props = PropertiesService.getScriptProperties();
  const previous = props.getProperty('previewDocId');
  if (previous) {
    try {
      DriveApp.getFileById(previous).setTrashed(true);
    } catch (e) {
      // Already deleted.
    }
  }
  const doc = DocumentApp.create('Template preview');
  props.setProperty('previewDocId', doc.getId());
  build(doc.getId());
  logLink('Preview:', doc.getUrl());
}

function createTemplate() {
  const doc = DocumentApp.create('Circle Agenda Template');
  build(doc.getId());
  logLink('Built:', doc.getUrl());
  Logger.log('Move it to the Shared Drive, and set TEMPLATE_DOC_ID in doc_helpers.gs to:');
  Logger.log(doc.getId());
}

function buildTemplate() {
  if (!TEMPLATE_DOC_ID) throw new Error('TEMPLATE_DOC_ID is not set; run createTemplate first.');
  clearDoc(TEMPLATE_DOC_ID);
  build(TEMPLATE_DOC_ID);
  logLink('Rebuilt:', DocumentApp.openById(TEMPLATE_DOC_ID).getUrl());
}

function describeTemplate() {
  Logger.log(describeStructure(TEMPLATE_DOC_ID).join('\n'));
}

function describePreview() {
  const docId = PropertiesService.getScriptProperties().getProperty('previewDocId');
  if (!docId) throw new Error('No preview; run previewTemplate first.');
  Logger.log(describeStructure(docId).join('\n'));
  const links = [];
  const body = firstTabBody(DocumentApp.openById(docId));
  let search = body.findElement(DocumentApp.ElementType.TEXT);
  while (search) {
    const text = search.getElement().asText();
    for (const i of text.getTextAttributeIndices()) {
      const url = text.getLinkUrl(i);
      if (url) links.push('"' + snippet(text.getText().slice(i), 20) + '" → ' + url);
    }
    search = body.findElement(DocumentApp.ElementType.TEXT, search);
  }
  Logger.log('Links:\n' + (links.join('\n') || '(none)'));
}

// DocumentApp says only "Action not allowed" when it can't save a change;
// this shows whether the running account can edit the template at all.
function checkTemplateAccess() {
  const file = Drive.Files.get(TEMPLATE_DOC_ID, {
    fields: 'name,owners(emailAddress),driveId,capabilities(canEdit,canModifyContent),' +
      'contentRestrictions',
    supportsAllDrives: true,
  });
  Logger.log([
    'Running as: ' + Session.getActiveUser().getEmail(),
    'Template: ' + file.name,
    'Owners: ' + (file.owners || []).map(o => o.emailAddress).join(', ') +
      (file.driveId ? ' (in a shared drive)' : ''),
    'Can edit: ' + file.capabilities.canEdit +
      ', can change content: ' + file.capabilities.canModifyContent,
    'Locked: ' + JSON.stringify(file.contentRestrictions || 'no'),
  ].join('\n'));
}

// ── Building ─────────────────────────────────────────────────────────────────

function build(docId) {
  const doc = DocumentApp.openById(docId);
  layOut(doc.getTabs()[0].asDocumentTab().getBody(), docId);
  doc.saveAndClose();
  finishWithApi(docId);
}

// Empties a doc: its first tab's body, its other tabs, its headers and
// footers, and its dropdowns' lists of choices.
function clearDoc(docId) {
  const doc = DocumentApp.openById(docId);
  const body = doc.getTabs()[0].asDocumentTab().getBody();
  body.clear();
  body.getChild(0).asParagraph().setHeading(DocumentApp.ParagraphHeading.NORMAL);
  doc.saveAndClose();

  const api = Docs.Documents.get(docId, {includeTabsContent: true});
  const first = api.tabs[0].documentTab;
  const requests = [
    ...api.tabs.slice(1).map(tab => ({deleteTab: {tabId: tab.tabProperties.tabId}})),
    ...Object.keys(first.headers || {}).map(headerId => ({deleteHeader: {headerId}})),
    ...Object.keys(first.footers || {}).map(footerId => ({deleteFooter: {footerId}})),
    ...Object.keys(first.dropdownDefinitions || {})
      .map(dropdownDefinitionId => ({deleteDropdownDefinition: {dropdownDefinitionId}})),
  ];
  if (requests.length) Docs.Documents.batchUpdate({requests}, docId);
}

// ── Pass 1: layout, with DocumentApp ─────────────────────────────────────────

const A = DocumentApp.Attribute;
const BOLD = {[A.BOLD]: true};
const SMALL = {[A.FONT_SIZE]: 8, [A.FOREGROUND_COLOR]: COLOR.muted};
const HINT = {[A.FOREGROUND_COLOR]: COLOR.hint};

function layOut(body, docId) {
  const leftover = body.getChild(0);

  const title = body.appendParagraph('Circle Agenda');
  title.setHeading(DocumentApp.ParagraphHeading.TITLE)
    .setAlignment(DocumentApp.HorizontalAlignment.CENTER);
  title.editAsText().setForegroundColor(COLOR.title).setBold(true);

  section(body, 'About this circle');
  aboutTable(body);
  body.appendHorizontalRule();

  section(body, 'Members and roles');
  rolesTable(body);
  body.appendHorizontalRule();

  section(body, 'Other policies');
  policiesTable(body);
  body.appendHorizontalRule();

  section(body, 'Backlog');
  backlogTable(body);
  body.appendParagraph('You can sort this table by due date with Format → Table → Sort ascending.')
    .editAsText().setAttributes(SMALL);
  body.appendHorizontalRule();

  section(body, 'MEETINGS');
  meetingTable(body, docId);
  body.appendParagraph('');

  leftover.removeFromParent();
}

function section(body, text) {
  body.appendParagraph(text).setHeading(DocumentApp.ParagraphHeading.HEADING2)
    .editAsText().setBold(true);
}

function aboutTable(body) {
  const rows = [
    [['Aim(s)'], [['What do we want to accomplish?', HINT]]],
    [['Domain'], [['What do we have responsibility for?', HINT]]],
    [['Parent Circle', '& link to circle document'], []],
    [['Sub-Circles', '& link to circle documents'], []],
    [['Circle email address'], []],
    [['Circle Drive Folder'], []],
    [['Objectives & Key Results', 'with review term'], []],
    [['Other Key Links'], []],
  ];
  const table = newTable(body, rows.length, 2);
  rows.forEach(([label, value], r) => {
    const labelCell = table.getCell(r, 0);
    writeCell(labelCell, [[[label[0], BOLD]], ...(label[1] ? [[[label[1], SMALL]]] : [])]);
    alignCell(labelCell, DocumentApp.HorizontalAlignment.RIGHT);
    labelCell.setBackgroundColor(COLOR.aboutLabel);
    writeCell(table.getCell(r, 1), [value]);
    table.getCell(r, 1).setBackgroundColor(COLOR.aboutValue);
  });
  table.setColumnWidth(0, 150);
}

function rolesTable(body) {
  const header = label => [[[label, BOLD]], [['Name, surname, email address', BOLD]],
    [['Term', BOLD], ['\nyyyy-mm-dd', SMALL]]];
  const role = (name, what) => [[[name, BOLD], [' (' + what + ')', SMALL]], [], []];
  const rows = [
    header('Circle Roles'),
    role('Leader', 'supports circle operations'),
    role('Delegate', 'second voice in parent circle'),
    role('Facilitator', 'facilitates meetings'),
    role('Secretary', 'minutes and circle docs'),
    header('Operational Roles'),
    [[], [], []], [[], [], []], [[], [], []], [[], [], []], [[], [], []],
  ];
  const table = newTable(body, rows.length, 3);
  rows.forEach((cells, r) => {
    const isHeader = r === 0 || r === 5;
    cells.forEach((runs, c) => {
      const cell = table.getCell(r, c);
      writeCell(cell, [runs]);
      cell.setBackgroundColor(isHeader ? COLOR.rolesHeader : COLOR.rolesBody);
      if (c === 2) alignCell(cell, DocumentApp.HorizontalAlignment.CENTER);
    });
  });
  table.setColumnWidth(2, 90);
}

function policiesTable(body) {
  const headers = [
    [['Policy Title', BOLD], ['\nwith link to policy', SMALL]],
    [['Brief Description', BOLD]],
    [['Holder', BOLD], ['\ncircle or role', SMALL]],
    [['Term', BOLD], ['\nmm/yyyy', SMALL]],
  ];
  const table = newTable(body, 3, headers.length);
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < headers.length; c++) {
      const cell = table.getCell(r, c);
      writeCell(cell, [r === 0 ? headers[c] : []]);
      cell.setBackgroundColor(r === 0 ? COLOR.policiesHeader : COLOR.policiesBody);
    }
  }
  table.setColumnWidth(3, 80);
}

function backlogTable(body) {
  const headers = ['Due Date', 'Priority', 'Description', 'Duration', 'Desired Outcome',
    'Prepared?', 'Holder'];
  const samples = [
    ['urgent', 'report', 'yes'], ['high', 'exploration', 'yes'],
    ['someday', 'decision', 'no'], ['normal', 'selection', 'yes'],
  ];
  const table = newTable(body, samples.length + 1, headers.length);
  headers.forEach((h, c) => {
    const cell = table.getCell(0, c);
    writeCell(cell, [[[h, BOLD]]]);
    cell.setBackgroundColor(COLOR.backlogHeader);
  });
  samples.forEach(([priority, outcome, prepared], i) => {
    const values = ['', token('dropdown', 'priority', priority), '', '',
      token('dropdown', 'outcome', outcome), token('dropdown', 'prepared', prepared), ''];
    values.forEach((v, c) => {
      const cell = table.getCell(i + 1, c);
      writeCell(cell, [v ? [[v]] : []]);
      cell.setBackgroundColor(COLOR.backlogBody);
    });
  });
  table.setColumnWidth(0, 70);
  table.setColumnWidth(1, 55);
  table.setColumnWidth(3, 55);
  table.setColumnWidth(5, 65);
}

// One meeting: date; time and place; "Agenda", with the links that add an
// item and update the times (which also identify the meeting; see
// meetings.gs); one row per item, each with its start time and duration;
// the closing round, which gets the time left; and the end time.
function meetingTable(body, docId) {
  const start = parseTime(SAMPLE_START);
  const total = parseHours(SAMPLE_HOURS);
  const times = scheduleTimes(start, SAMPLE_AGENDA.map(item => item.minutes), total);

  const items = SAMPLE_AGENDA.map((item, i) => ({...item, start: times.starts[i]}));
  items.push({...CLOSING_ROUND, start: times.closingStart, minutes: times.closingMinutes,
    computed: true});

  const rowCount = MEETING_HEADER_ROWS + items.length + 1;
  const table = newTable(body, rowCount, 4);

  // A date chip without a time is shown in UTC, so it's given noon UTC on
  // the local date, which is that date everywhere.
  const today = Utilities.formatDate(new Date(), TIME_ZONE, 'yyyy-MM-dd');
  writeCell(table.getCell(0, 0), [[[token('date', today + 'T12:00:00Z')]]]);
  table.getCell(0, 0).editAsText().setBold(true).setFontSize(13);
  writeCell(table.getCell(1, 0), [[['Time  ', BOLD], [SAMPLE_START], ['     Place  ', BOLD]]]);
  const meetingId = newMeetingId();
  writeCell(table.getCell(2, 0), [[['Agenda', BOLD], ['      '],
    ['+ Add item', {[A.LINK_URL]: actionUrl('item', docId, meetingId)}], ['      '],
    ['↻ Update times', {[A.LINK_URL]: actionUrl('times', docId, meetingId)}]]]);
  for (let r = 0; r < MEETING_HEADER_ROWS; r++) {
    for (let c = 1; c < 4; c++) writeCell(table.getCell(r, c), [[]]);
  }

  items.forEach((item, i) => {
    const r = MEETING_HEADER_ROWS + i;
    writeCell(table.getCell(r, 0), [[[formatTime(item.start), SMALL]]]);
    writeCell(table.getCell(r, 1), [[[String(item.minutes),
      item.computed ? {...SMALL, [A.ITALIC]: true} : SMALL]]]);
    const content = table.getCell(r, 2);
    if (item.admin) {
      adminBlock(content);
    } else {
      const lines = [[[item.title, BOLD], ...(item.note ? [[item.note]] : [])]];
      if (item.body) lines.push([[token(item.body)]]);
      writeCell(content, lines);
    }
    const type = table.getCell(r, 3);
    if (item.outcome) writeCell(type, [[[token('dropdown', 'outcome', item.outcome)]]]);
    else writeCell(type, [item.type ? [[item.type, {...SMALL, [A.BOLD]: true}]] : []]);
    alignCell(type, DocumentApp.HorizontalAlignment.RIGHT);
    shadeRow(table, r, item.kind);
  });

  const last = rowCount - 1;
  writeCell(table.getCell(last, 0), [[[formatTime(times.end), SMALL]]]);
  writeCell(table.getCell(last, 1), [[['end', {...SMALL, [A.ITALIC]: true}]]]);
  writeCell(table.getCell(last, 2), [[]]);
  writeCell(table.getCell(last, 3), [[]]);
  shadeRow(table, last, 'round');

  table.setColumnWidth(0, 45);
  table.setColumnWidth(1, 35);
  table.setColumnWidth(3, 85);
}

// The meeting's admin details, as a table inside its agenda row.
function adminBlock(cell) {
  const rows = [
    ['Attendance', [[token('checkbox')]]],
    ['Duration (hours)', [[SAMPLE_HOURS]]],
    ['Minutes', [['No previous minutes']]],
    ['Information', [['(announcements)', HINT]]],
    ['Next meeting', [['TBD']]],
  ];
  writeCell(cell, [[]]);
  const table = cell.appendTable(rows.map(() => ['', '']));
  rows.forEach(([label, value], r) => {
    writeCell(table.getCell(r, 0), [[[label, BOLD]]]);
    writeCell(table.getCell(r, 1), [value]);
  });
  table.setBorderWidth(0);
  table.setColumnWidth(0, 120);
  // The empty paragraph a cell starts with, now above the table.
  if (cell.getNumChildren() > 2) cell.getChild(0).removeFromParent();
}

// ── Layout helpers ───────────────────────────────────────────────────────────

function newTable(body, rows, columns) {
  const cells = [];
  for (let r = 0; r < rows; r++) cells.push(new Array(columns).fill(''));
  const table = body.appendTable(cells);
  table.setBorderColor(COLOR.border).setBorderWidth(0.5);
  return table;
}

// Sets a cell's paragraphs. Each is a list of runs, [text, attributes];
// an empty list is an empty paragraph.
function writeCell(cell, paragraphs) {
  cell.setText('');
  paragraphs.forEach((runs, i) => {
    const p = i === 0 ? cell.getChild(0).asParagraph() : cell.appendParagraph('');
    writeRuns(p, runs);
  });
}

function writeRuns(paragraph, runs) {
  const text = runs.map(run => run[0]).join('');
  // Paragraphs here start empty, and setText('') is refused.
  if (!text) return;
  paragraph.setText(text);
  const edit = paragraph.editAsText();
  let at = 0;
  for (const [t, attrs] of runs) {
    if (t && attrs) edit.setAttributes(at, at + t.length - 1, attrs);
    at += t.length;
  }
}

function alignCell(cell, alignment) {
  for (let i = 0; i < cell.getNumChildren(); i++) {
    const child = cell.getChild(i);
    if (child.getType() === DocumentApp.ElementType.PARAGRAPH) child.asParagraph().setAlignment(alignment);
  }
}

function shadeRow(table, r, kind) {
  const color = kind === 'round' ? COLOR.roundRow : kind === 'report' ? COLOR.reportRow : null;
  if (!color) return;
  for (let c = 0; c < table.getRow(r).getNumCells(); c++) table.getCell(r, c).setBackgroundColor(color);
}

// A placeholder for something only the Docs API can add; see finishWithApi.
function token(kind, ...args) {
  return '⟦' + [kind, ...args].join(' ') + '⟧';
}

// ── Pass 2: the Docs API ─────────────────────────────────────────────────────

function finishWithApi(docId) {
  // The dropdowns' lists of choices, and the pageless format.
  Docs.Documents.batchUpdate({requests: [
    ...Object.values(DROPDOWNS).map(def => ({createDropdownDefinition: {dropdownDefinition: {
      dropdownDefinitionId: def.id,
      dropdownDefinitionProperties: {title: def.title, options: def.options.map(o => ({
        optionId: optionId(o.key),
        displayValue: o.label,
        textStyle: {backgroundColor: apiColor(o.colors[0]), foregroundColor: apiColor(o.colors[1])},
      }))},
    }}})),
    {updateDocumentStyle: {
      documentStyle: {documentFormat: {documentMode: 'PAGELESS'}},
      fields: 'documentFormat.documentMode',
    }},
  ]}, docId);

  replaceTokens(docId);

  // The meeting table's top rows: each merged into one cell, and the first
  // MEETING_PINNED_ROWS pinned, so they stay in view.
  let content = Docs.Documents.get(docId).body.content;
  const meeting = content.find(se => se.table && tableText(se.table).includes('Opening Round'));
  const tableStartLocation = {index: meeting.startIndex};
  const merges = [];
  for (let r = MEETING_HEADER_ROWS - 1; r >= 0; r--) {
    merges.push({mergeTableCells: {tableRange: {
      tableCellLocation: {tableStartLocation, rowIndex: r, columnIndex: 0},
      rowSpan: 1, columnSpan: 4,
    }}});
  }
  Docs.Documents.batchUpdate({requests: [
    {pinTableHeaderRows: {tableStartLocation, pinnedHeaderRowsCount: MEETING_PINNED_ROWS}},
    ...merges,
  ]}, docId);

  // Merging keeps each merged cell's (empty) paragraphs; remove them.
  content = Docs.Documents.get(docId).body.content;
  const merged = content.find(se => se.table && se.startIndex === meeting.startIndex).table;
  const tidy = [];
  for (let r = MEETING_HEADER_ROWS - 1; r >= 0; r--) {
    const paras = merged.tableRows[r].tableCells[0].content;
    const extra = paras.slice(1);
    if (extra.length && extra.every(se => se.paragraph && !paraText(se.paragraph).trim())) {
      tidy.push({deleteContentRange: {range: {
        startIndex: paras[0].endIndex - 1, endIndex: paras[paras.length - 1].endIndex - 1,
      }}});
    }
  }
  if (tidy.length) Docs.Documents.batchUpdate({requests: tidy}, docId);
}

// Replaces each token in the doc's first tab with its chip or list, last
// first so the earlier ones' positions don't move.
function replaceTokens(docId) {
  const requests = [];
  for (const t of findTokens(Docs.Documents.get(docId).body.content)) {
    requests.push({deleteContentRange: {range: {startIndex: t.start, endIndex: t.end}}});
    requests.push(tokenRequest(t));
  }
  if (requests.length) Docs.Documents.batchUpdate({requests}, docId);
}

// The request that puts a token's chip or list at its place.
function tokenRequest(t) {
  const location = {index: t.start};
  const [kind, ...args] = t.parts;
  if (kind === 'dropdown') {
    const def = DROPDOWNS[args[0]];
    if (!def || !def.options.some(o => o.key === args[1])) throw new Error('Unknown dropdown ' + t.text);
    return {insertDropdown: {location, dropdownDefinitionId: def.id, selectedOptionId: optionId(args[1])}};
  }
  if (kind === 'date') {
    return {insertDate: {location, dateElementProperties: {
      timestamp: args[0],
      dateFormat: 'DATE_FORMAT_ISO8601', timeFormat: 'TIME_FORMAT_DISABLED',
    }}};
  }
  if (kind === 'checkbox' || kind === 'bullet') {
    return {createParagraphBullets: {
      range: {startIndex: t.start, endIndex: t.start + 1},
      bulletPreset: kind === 'checkbox' ? 'BULLET_CHECKBOX' : 'BULLET_DISC_CIRCLE_SQUARE',
    }};
  }
  throw new Error('Unknown token ' + t.text);
}

// Every token in Docs API content, last first, with its start and end
// indices.
function findTokens(content) {
  const found = [];
  walkParagraphs(content, para => {
    let text = '';
    const pieces = [];
    for (const pe of para.elements || []) {
      if (!pe.textRun) continue;
      pieces.push({offset: text.length, index: pe.startIndex});
      text += pe.textRun.content;
    }
    const re = /⟦([^⟧]*)⟧/g;
    let m;
    while ((m = re.exec(text))) {
      const piece = pieces.filter(p => p.offset <= m.index).pop();
      const start = piece.index + m.index - piece.offset;
      found.push({text: m[0], parts: m[1].split(' '), start, end: start + m[0].length});
    }
  });
  return found.sort((a, b) => b.start - a.start);
}

function tableText(table) {
  let text = '';
  walkParagraphs([{table}], para => { text += paraText(para); });
  return text;
}

function optionId(key) {
  return 'dropdownItem.' + key;
}

function apiColor(hex) {
  const n = parseInt(hex.slice(1), 16);
  return {color: {rgbColor: {red: (n >> 16) / 255, green: ((n >> 8) & 255) / 255, blue: (n & 255) / 255}}};
}
