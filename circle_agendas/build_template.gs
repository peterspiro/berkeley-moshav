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
 * The template's "+ New meeting" link runs the web app (web_app.gs), so
 * building needs the web app's address, in the WEB_APP_URL script property.
 * The template has no meetings: each meeting gets a tab of its own (see
 * meetings.gs), built by meetingTable here.
 *
 * Building has two passes. The first lays out the text and tables with
 * DocumentApp, writing a token like ⟦dropdown outcome decision⟧ wherever a
 * smart chip or list goes. The second, with the Docs API, which DocumentApp
 * can't do, creates the dropdowns' lists of choices, replaces each token
 * with its chip or list, sets the pageless format, and names the first
 * tab.
 *
 * Setup: see README.md in this folder.
 */

const TIME_ZONE = 'America/Los_Angeles';

const COLOR = {
  title: '#4a7bc8',
  hint: '#5b6f99',
  muted: '#777777',
  border: '#c8c8c8',
  followUp: '#fff2cc', // Docs' "light yellow 3"
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

// The first tab's title; each meeting has a tab of its own, titled with its
// date, after it.
const MAIN_TAB_TITLE = 'Circle';
const MEETING_TAB_TITLE_RE = /^\d{4}-\d{2}-\d{2}$/;
// Each meeting's items before the closing round, in the template and in
// new meetings. kind: 'round' and 'report' items are shaded; round items
// get no presenter; actions: a checklist under the description, for action
// items; notes: false for no bulleted Notes.
const AGENDA = [
  {title: 'Opening Round / Check-in', note: ' (no notes)', minutes: 10, kind: 'round',
    notes: false},
  {admin: true, minutes: 3, kind: 'round', notes: false},
  {title: 'Consent to Agenda', minutes: 2, kind: 'report'},
  {title: 'Action Items Accountability', minutes: 3, actions: true,
    note: ' (the action items from the previous meeting\'s recap; check each one that\'s ' +
      'done, and its line is struck through)'},
  {title: 'Topic', minutes: 20},
  {title: 'Topic', minutes: 20},
  {title: 'Topic', minutes: 20},
  {title: 'Action Items Recap', minutes: 5, kind: 'report', actions: true,
    note: ' (one line for each action agreed in this meeting, with who\'ll do it and by ' +
      'when; each is copied to the next meeting\'s Action Items Accountability, where ' +
      'its box is checked once it\'s done)'},
];
const CLOSING_ROUND = {title: 'Closing Round / Check-out',
  note: ' (feedback on content, interpersonal, processes)', kind: 'round'};
// A meeting table's columns.
const COL = {time: 0, duration: 1, presenter: 2, description: 3, notes: 4, followUp: 5};
const MEETING_COLUMNS = 6;
const MEETING_HEADINGS = ['Time', 'Duration', 'Presenter', 'Description', 'Notes', 'Follow-up'];
// The rows above the agenda items: the column headings, which stay in view
// while scrolling through the meeting. (Docs shows pinned rows, while they
// stick, as a copy that can't be clicked into, so links there often can't
// be followed.)
const MEETING_HEADER_ROWS = 1;
const MEETING_PINNED_ROWS = 1;

// ── Entry points ─────────────────────────────────────────────────────────────

// Rebuilds the preview doc in place, wherever it's been moved to, or makes
// one if there's none (or it's been trashed).
function previewTemplate() {
  const props = PropertiesService.getScriptProperties();
  let docId = props.getProperty('previewDocId');
  try {
    if (docId && DriveApp.getFileById(docId).isTrashed()) docId = null;
  } catch (e) {
    docId = null; // Deleted.
  }
  if (docId) {
    clearDoc(docId);
  } else {
    docId = DocumentApp.create('Template preview').getId();
    props.setProperty('previewDocId', docId);
  }
  build(docId);
  logLink('Preview:', DocumentApp.openById(docId).getUrl());
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
// Table text is all Docs' default size; secondary text is grey.
const MUTED = {[A.FOREGROUND_COLOR]: COLOR.muted};
const SMALL = {...MUTED, [A.FONT_SIZE]: 8};
const HINT = {[A.FOREGROUND_COLOR]: COLOR.hint};

// Lays out the template's first tab. It has no meetings: each gets a tab of
// its own, added by "+ New meeting".
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
  body.appendParagraph('Each meeting has a tab of its own, titled with its date; the tabs ' +
    'are listed at the left (under ☰ on a phone), newest first.').editAsText().setAttributes(MUTED);
  writeRuns(body.appendParagraph(''),
    [['+ New meeting', {[A.LINK_URL]: actionUrl('meeting', docId)}]]);
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
    writeCell(labelCell, [[[label[0], BOLD]], ...(label[1] ? [[[label[1], MUTED]]] : [])]);
    alignCell(labelCell, DocumentApp.HorizontalAlignment.RIGHT);
    labelCell.setBackgroundColor(COLOR.aboutLabel);
    writeCell(table.getCell(r, 1), [value]);
    table.getCell(r, 1).setBackgroundColor(COLOR.aboutValue);
  });
  table.setColumnWidth(0, 150);
}

function rolesTable(body) {
  const header = label => [[[label, BOLD]], [['Name, surname, email address', BOLD]],
    [['Term', BOLD], ['\nyyyy-mm-dd', MUTED]]];
  const role = (name, what) => [[[name, BOLD], [' (' + what + ')', MUTED]], [], []];
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
    [['Policy Title', BOLD], ['\nwith link to policy', MUTED]],
    [['Brief Description', BOLD]],
    [['Holder', BOLD], ['\ncircle or role', MUTED]],
    [['Term', BOLD], ['\nmm/yyyy', MUTED]],
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

// Appends a meeting to a tab's body, and returns its ID. Above its table:
// its date; its time and place; and its attendance. In its table: the
// column headings; one row per item, each with its start time and
// duration; just above Action Items Recap (where new items go), a row of
// links that add an item and update the times (which also identify the
// meeting; see meetings.gs); the closing round, which gets the time left;
// and the end time.
//
// details: date ('yyyy-MM-dd'), start ('19:30'), hours ('1.5'), and
// optionally place (the Zoom link), attendees (names, which also make the
// Presenter dropdown's choices), minutes (runs, for the Minutes row), next
// (text, for Next meeting), and carryOver (list items to copy into Action
// Items Accountability).
function meetingTable(body, docId, details) {
  const start = parseTime(details.start);
  const total = parseHours(details.hours);
  const times = scheduleTimes(start, AGENDA.map(item => item.minutes), total);

  const items = AGENDA.map((item, i) => ({...item, start: times.starts[i]}));
  items.push({...CLOSING_ROUND, start: times.closingStart, minutes: times.closingMinutes,
    computed: true});
  const recap = items.findIndex(item => item.title === 'Action Items Recap');
  items.splice(recap < 0 ? items.length - 1 : recap, 0, {links: true});

  const meetingId = newMeetingId();
  const presenters = (details.attendees || []).length ? presenterDropdownId(meetingId) : null;

  // A date chip without a time is shown in UTC, so it's given noon UTC on
  // the local date, which is that date everywhere.
  body.appendParagraph(token('date', details.date + 'T12:00:00Z'))
    .setHeading(DocumentApp.ParagraphHeading.HEADING2);
  writeRuns(body.appendParagraph(''), [['Time  ', BOLD], [details.start], ['     Place  ', BOLD],
    ...(details.place ? [['Zoom room', {[A.LINK_URL]: details.place}]] : [])]);
  attendance(body, details.attendees || []);

  const rowCount = MEETING_HEADER_ROWS + items.length + 1;
  const table = newTable(body, rowCount, MEETING_COLUMNS);
  MEETING_HEADINGS.forEach((h, c) => writeCell(table.getCell(0, c), [[[h, BOLD]]]));

  items.forEach((item, i) => {
    const r = MEETING_HEADER_ROWS + i;
    const cell = c => table.getCell(r, c);
    if (item.links) {
      linksRow(table.getRow(r), docId, meetingId);
      cell(COL.followUp).setBackgroundColor(COLOR.followUp);
      return;
    }
    writeCell(cell(COL.time), [[[formatTime(item.start), MUTED]]]);
    writeCell(cell(COL.duration), [[[String(item.minutes),
      item.computed ? {...MUTED, [A.ITALIC]: true} : MUTED]]]);
    writeCell(cell(COL.presenter),
      [presenters && item.kind !== 'round' ? [[token('presenter', presenters)]] : []]);
    const description = cell(COL.description);
    if (item.admin) {
      adminBlock(description, details);
    } else {
      const carryOver = item.title === 'Action Items Accountability' ? details.carryOver || [] : [];
      const lines = [[[item.title, BOLD], ...(item.note ? [[item.note, MUTED]] : [])]];
      if (item.actions && !carryOver.length) lines.push([[token('checkbox')]]);
      writeCell(description, lines);
      for (const el of carryOver) {
        if (el.getType() === DocumentApp.ElementType.LIST_ITEM) description.appendListItem(el.copy());
        else description.appendParagraph(el.copy());
      }
    }
    writeCell(cell(COL.notes), [item.notes === false ? [] : [[token('bullet')]]]);
    writeCell(cell(COL.followUp), [[]]);
    shadeRow(table, r, item.kind);
    cell(COL.followUp).setBackgroundColor(COLOR.followUp);
  });

  const last = rowCount - 1;
  for (let c = 0; c < MEETING_COLUMNS; c++) writeCell(table.getCell(last, c), [[]]);
  writeCell(table.getCell(last, COL.time), [[[formatTime(times.end), MUTED]]]);
  writeCell(table.getCell(last, COL.duration), [[['end', {...MUTED, [A.ITALIC]: true}]]]);
  shadeRow(table, last, 'round');
  table.getCell(last, COL.followUp).setBackgroundColor(COLOR.followUp);

  table.setColumnWidth(COL.time, 45);
  table.setColumnWidth(COL.duration, 55);
  table.setColumnWidth(COL.presenter, 90);
  return meetingId;
}

// The meeting's attendance, above its table, closely spaced: the attendees
// as a checklist, and a bulleted list for guests.
function attendance(body, attendees) {
  const paragraphs = [
    [['Attendance', BOLD], ['   Check the box next to anyone who isn\'t here.', MUTED]],
    ...(attendees.length
      ? attendees.map(name => [[token('checkbox')], [name]])
      : [[[token('checkbox')]]]),
    [['Guests:', BOLD]],
    [[token('bullet')]],
  ];
  for (const runs of paragraphs) {
    const p = body.appendParagraph('');
    writeRuns(p, runs);
    p.setSpacingBefore(0).setSpacingAfter(0).setLineSpacing(1);
  }
}

// The ID of a meeting's Presenter dropdown's list of choices (see
// presenterDropdownRequest).
function presenterDropdownId(meetingId) {
  return 'kix.p' + meetingId;
}

// The row of links that add an item and update the times.
function linksRow(row, docId, meetingId) {
  for (let c = 0; c < MEETING_COLUMNS; c++) writeCell(row.getCell(c), [[]]);
  writeCell(row.getCell(COL.description), [[
    ['+ Add item', {[A.LINK_URL]: actionUrl('item', docId, meetingId)}], ['        '],
    ['↻ Update times', {[A.LINK_URL]: actionUrl('times', docId, meetingId)}]]]);
}

// The meeting's admin details, as a table inside its agenda row. Each row's
// value is a list of paragraphs, each a list of runs.
function adminBlock(cell, details) {
  const rows = [
    ['Duration (hours)', [[[details.hours]]]],
    ['Minutes', [details.minutes || [['No previous minutes']]]],
    ['Information', [[['(announcements)', HINT]]]],
    ['Next meeting', [[[details.next || 'TBD']]]],
  ];
  writeCell(cell, [[]]);
  const table = cell.appendTable(rows.map(() => ['', '']));
  rows.forEach(([label, value], r) => {
    writeCell(table.getCell(r, 0), [[[label, BOLD]]]);
    writeCell(table.getCell(r, 1), value);
  });
  table.setBorderWidth(0);
  table.setColumnWidth(0, 120);
  // The empty paragraph a cell starts with, now above the table.
  if (cell.getNumChildren() > 2) cell.getChild(0).removeFromParent();
}

// ── Layout helpers ───────────────────────────────────────────────────────────

// A new table, appended to the body, or inserted at position at.
function newTable(body, rows, columns, at) {
  const cells = [];
  for (let r = 0; r < rows; r++) cells.push(new Array(columns).fill(''));
  const table = at === undefined ? body.appendTable(cells) : body.insertTable(at, cells);
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
  const firstTabId = Docs.Documents.get(docId, {includeTabsContent: true}).tabs[0].tabProperties.tabId;
  Docs.Documents.batchUpdate({requests: [{updateDocumentTabProperties: {
    tabProperties: {tabId: firstTabId, title: MAIN_TAB_TITLE}, fields: 'title',
  }}]}, docId);

  replaceTokens(docId);
}

// A tab's Docs API content, and its ID for requests (undefined for the
// first tab, which requests without one apply to).
function apiTab(docId, tabId) {
  const tabs = Docs.Documents.get(docId, {includeTabsContent: true}).tabs;
  const tab = tabId ? tabs.find(t => t.tabProperties.tabId === tabId) : tabs[0];
  if (!tab) throw new Error('No tab ' + tabId);
  return {content: tab.documentTab.body.content, tabId: tabId || undefined};
}

// The Docs API's part of making a meeting table, in its tab: its column
// headings pinned, so they stay in view.
function finishMeeting(docId, meetingId, tabId) {
  const content = apiTab(docId, tabId).content;
  const meeting = content.find(se => se.table && apiMeetingId(se) === meetingId);
  Docs.Documents.batchUpdate({requests: [{pinTableHeaderRows: {
    tableStartLocation: {index: meeting.startIndex, tabId}, pinnedHeaderRowsCount: MEETING_PINNED_ROWS,
  }}]}, docId);
}

// Replaces each token in a tab (by default, the first) with its chip or
// list, last first so the earlier ones' positions don't move.
function replaceTokens(docId, tabId) {
  const requests = [];
  for (const t of findTokens(apiTab(docId, tabId).content)) {
    requests.push({deleteContentRange: {range: {startIndex: t.start, endIndex: t.end, tabId}}});
    requests.push(tokenRequest(t, tabId));
  }
  if (requests.length) Docs.Documents.batchUpdate({requests}, docId);
}

// The request that puts a token's chip or list at its place.
function tokenRequest(t, tabId) {
  const location = {index: t.start, tabId};
  const [kind, ...args] = t.parts;
  if (kind === 'dropdown') {
    const def = DROPDOWNS[args[0]];
    if (!def || !def.options.some(o => o.key === args[1])) throw new Error('Unknown dropdown ' + t.text);
    return {insertDropdown: {location, dropdownDefinitionId: def.id, selectedOptionId: optionId(args[1])}};
  }
  if (kind === 'presenter') {
    return {insertDropdown: {location, dropdownDefinitionId: args[0], selectedOptionId: optionId('none')}};
  }
  if (kind === 'date') {
    return {insertDate: {location, dateElementProperties: {
      timestamp: args[0],
      dateFormat: 'DATE_FORMAT_ISO8601', timeFormat: 'TIME_FORMAT_DISABLED',
    }}};
  }
  if (kind === 'checkbox' || kind === 'bullet') {
    return {createParagraphBullets: {
      range: {startIndex: t.start, endIndex: t.start + 1, tabId},
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

// The meeting ID in the links in a Docs API table element, or null.
function apiMeetingId(se) {
  let id = null;
  walkParagraphs([se], para => {
    for (const pe of para.elements || []) {
      const link = pe.textRun && pe.textRun.textStyle.link;
      if (!id && link) id = meetingIdFromUrl(link.url);
    }
  });
  return id;
}

// The request that creates a meeting's Presenter dropdown's choices, in its
// tab (each tab has its own): none ("—"), then each attendee (up to Docs'
// limit of 50 choices).
function presenterDropdownRequest(meetingId, attendees, tabId) {
  return {createDropdownDefinition: {tabId, dropdownDefinition: {
    dropdownDefinitionId: presenterDropdownId(meetingId),
    dropdownDefinitionProperties: {title: 'Presenter', options: [
      {optionId: optionId('none'), displayValue: '—'},
      ...attendees.slice(0, 49).map((name, i) => ({optionId: optionId('p' + i), displayValue: name})),
    ]},
  }}};
}

function optionId(key) {
  return 'dropdownItem.' + key;
}

function apiColor(hex) {
  const n = parseInt(hex.slice(1), 16);
  return {color: {rgbColor: {red: (n >> 16) / 255, green: ((n >> 8) & 255) / 255, blue: (n & 255) / 255}}};
}
