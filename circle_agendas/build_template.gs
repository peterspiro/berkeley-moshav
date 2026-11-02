/**
 * Build Template
 *
 * Builds a circle agenda doc from a blank document, entirely from this
 * code, so it never depends on another document's content. Every change to
 * the layout is a change here. build() builds each circle's doc (see
 * circle_docs.gs) and the preview.
 *
 * Run:
 *   - previewTemplate: builds the "Template preview" doc (again in place,
 *     after the first run) and logs its link;
 *   - describePreview: logs the preview's structure and links.
 *
 * The "+ New meeting" link runs the web app (web_app.gs), so building needs
 * the web app's address, in the WEB_APP_URL script property. A new doc has
 * no meetings: each meeting gets a tab of its own (see meetings.gs), built
 * by meetingTable here.
 *
 * Building has two passes. The first lays out the text and tables with
 * DocumentApp, writing a token like ⟦checkbox⟧ wherever a smart chip or
 * list goes. The second, with the Docs API, which DocumentApp can't do,
 * replaces each token with its chip or list, sets the pageless format,
 * names the first tab, and adds the Backlog tab.
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
  roundRow: '#d0e0e3', // Docs' "light cyan 3"
  reportRow: '#f2f2f2',
  followUp: '#ffe599', // Docs' "light yellow 2"
  // Follow-up cells in shaded rows: halfway between the two colors.
  followUpRoundRow: '#e8e3be',
  followUpReportRow: '#f9ecc6',
};


// The first tab's title; each meeting has a tab of its own, titled with its
// date, after it.
const MAIN_TAB_TITLE = 'Circle Info';
const BACKLOG_TAB_TITLE = 'Backlog';
const MEETING_TAB_TITLE_RE = /^\d{4}-\d{2}-\d{2}$/;
// Each meeting's items before the closing round, in the template and in
// new meetings. kind: 'round' and 'report' items are shaded; admin: the
// Administrative matters item (see adminLines); actions: 'checkbox' or
// 'bullet', a list under the description, for action items. The Notes
// column starts empty.
// Every item has a bulleted list in Notes, but those with notes: false.
// topic: a Presenter dropdown and a bulleted list under the title.
// Items with followUp start their Follow-up cell with a bullet (as do
// topics added later).
const AGENDA = [
  {title: 'Opening Round / Check-in', note: ' (no notes)', minutes: 10, kind: 'round',
    notes: false},
  {admin: true, minutes: 5, kind: 'round', followUp: true},
  {title: 'Action Items Accountability', minutes: 3, actions: 'checkbox', followUp: true,
    note: ' (the action items from the previous meeting\'s recap; check each one that\'s ' +
      'done, and its line is struck through)'},
  {title: 'Topic', minutes: 5, topic: true, followUp: true},
  {title: 'Action Items Recap', minutes: 5, kind: 'report', actions: 'bullet'},
];
// The closing round's Notes are a bulleted line for each attendee: their
// name and a colon.
const CLOSING_ROUND = {title: 'Closing Round / Check-out', kind: 'round', perAttendee: true,
  note: ' What went well? What can we improve? (Evaluate meeting, facilitation, group ' +
    'effectiveness.)'};
// A meeting table's columns.
// Every column has a fixed width (in points), the narrow ones with narrow
// padding. In a pageless doc, a table wider than the window scrolls
// sideways, so Notes is wide regardless of the window.
// Follow-up is shaded light yellow, below its heading. Meetings added
// before it had only the first four columns.
const COL = {time: 0, duration: 1, description: 2, notes: 3, followUp: 4};
const MEETING_COLUMNS = 5;
const MEETING_HEADINGS = ['Time', 'Min', 'Description', 'Notes', 'Follow-up'];
const COLUMN_WIDTHS = {[COL.time]: 33, [COL.duration]: 27, [COL.description]: 262,
  [COL.notes]: 375, [COL.followUp]: 210};
const CELL_PADDING = 3;
const PROBLEM_COLOR = '#cc0000';
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
  // In a circle's folder, the preview gets that circle's details, as a
  // circle's own doc does (without its parent or sub-circles, which come
  // from the Circle Structure).
  const circle = circleForDoc(docId);
  if (circle) fillAbout(docId, 'Circle Agenda', circle.email, circle, null, []);
  logLink('Preview:', DocumentApp.openById(docId).getUrl());
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
const HINT = {[A.FOREGROUND_COLOR]: COLOR.hint};

// Lays out the template's first tab. It has no meetings: each gets a tab of
// its own, added by "+ New meeting".
function layOut(body, docId) {
  const leftover = body.getChild(0);

  const title = body.appendParagraph('Circle Agenda');
  title.setHeading(DocumentApp.ParagraphHeading.TITLE)
    .setAlignment(DocumentApp.HorizontalAlignment.CENTER);
  title.editAsText().setForegroundColor(COLOR.title).setBold(true);

  section(body, 'Create a new meeting');
  writeRuns(body.appendParagraph(''), [
    ['+ New meeting', {[A.LINK_URL]: actionUrl('meeting', docId)}],
    ['   (each meeting gets a tab of its own, listed at the left, titled with its date)', MUTED]]);

  section(body, 'Previous agendas');
  writeRuns(body.appendParagraph(''), [
    ['Paste a link to the circle\'s previous agenda doc, if it had one', HINT]]);

  section(body, 'About this circle');
  aboutTable(body);

  section(body, 'Roles');
  rolesTable(body);

  leftover.removeFromParent();
}

function section(body, text) {
  body.appendParagraph(text).setHeading(DocumentApp.ParagraphHeading.HEADING2)
    .editAsText().setBold(true);
}

function aboutTable(body) {
  const rows = [
    [['Aims & Domain'], [['Paste a link to circle\'s aims and domain document', HINT]]],
    [['Policies folder'], [['Paste a link to the circle\'s policies folder', HINT]]],
    [['Zoom room'], []],
    [['Circle email address'], []],
    [['Other Key Links'], []],
    [['Google Drive Folder'], []],
    [['Parent Circle'], []],
    [['Sub-Circles'], []],
    [[REMINDER_ROW_LABEL], [[REMINDER_DEFAULT_TEXT]], [[REMINDER_HINT, HINT]]],
  ];
  // Must match ABOUT_LABELS (agenda_rules.gs).
  if (rows.map(row => row[0][0]).join('|') !== ABOUT_LABELS.join('|')) {
    throw new Error('aboutTable\'s rows don\'t match ABOUT_LABELS');
  }
  const table = newTable(body, rows.length, 2);
  rows.forEach(([label, ...value], r) => writeAboutRow(table.getRow(r), label, value));
  table.setColumnWidth(0, 150);
}

// Writes one About this circle row: its label cell (label: the label, and
// a second, grey line, if any) and its value (paragraphs, each a list of
// runs, or none).
function writeAboutRow(row, label, paragraphs) {
  const labelCell = row.getCell(0);
  writeCell(labelCell, [[[label[0], BOLD]], ...(label[1] ? [[[label[1], MUTED]]] : [])]);
  alignCell(labelCell, DocumentApp.HorizontalAlignment.RIGHT);
  labelCell.setBackgroundColor(COLOR.aboutLabel);
  writeCell(row.getCell(1), paragraphs.length ? paragraphs : [[]]);
  row.getCell(1).setBackgroundColor(COLOR.aboutValue);
}

function rolesTable(body) {
  const header = label => [[[label, BOLD]], [['Name', BOLD]],
    [['Term ends', BOLD], ['\nyyyy-mm-dd', MUTED]]];
  const role = (name, what) => [[[name, BOLD], [' (' + what + ')', MUTED]], [], []];
  const rows = [
    header('Circle Roles'),
    role('Leader', 'supports circle operations'),
    role('Feedback Link', 'second voice in parent circle'),
    role('Facilitator', 'facilitates meetings'),
    role('Secretary', 'minutes and circle docs'),
  ];
  const table = newTable(body, rows.length, 3);
  rows.forEach((cells, r) => {
    const isHeader = r === 0;
    cells.forEach((runs, c) => {
      const cell = table.getCell(r, c);
      writeCell(cell, [runs]);
      cell.setBackgroundColor(isHeader ? COLOR.rolesHeader : COLOR.rolesBody);
      if (c === 2) alignCell(cell, DocumentApp.HorizontalAlignment.CENTER);
    });
  });
  table.setColumnWidth(2, 90);
}

// Appends a meeting to a tab's body, and returns its ID. Above its table:
// its date; its time, length, and place; and its attendance. In its table: the
// column headings; one row per item, each with its start time and
// duration; just above Action Items Recap (where new items go), a row of
// links that add an item and update the times (which also identify the
// meeting; see meetings.gs); the closing round, which gets the time left;
// and the end time.
//
// details: date ('yyyy-MM-dd'), start ('19:30'), hours ('1.5'), and
// optionally place (the Zoom link), attendees (names), facilitator and
// secretary (names, from the Circle Info tab's roles), previousUrl (a link
// to the previous meeting's tab), next (text, the next meeting's date and
// time), and carryOver (the text of the previous
// meeting's Action Items Recap's items, for Action Items Accountability's
// checklist).
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
  writeRuns(body.appendParagraph(''), [['Time  ', BOLD], [details.start],
    ['     Duration (hours)  ', BOLD], [details.hours], ['     Place  ', BOLD],
    ...(details.place ? [['Zoom room', {[A.LINK_URL]: details.place}]] : [])]);
  const spacer = attendance(body, details);

  const rowCount = MEETING_HEADER_ROWS + items.length + 1;
  const table = newTable(body, rowCount, MEETING_COLUMNS);
  // Only once the table after it exists: a table appended after a paragraph
  // takes on its text style.
  shrinkSpacer(spacer);
  MEETING_HEADINGS.forEach((h, c) => writeCell(table.getCell(0, c), [[[h, BOLD]]]));

  items.forEach((item, i) => {
    const r = MEETING_HEADER_ROWS + i;
    const cell = c => table.getCell(r, c);
    if (item.links) {
      linksRow(table.getRow(r), docId, meetingId);
      shadeFollowUp(table, r);
      return;
    }
    writeCell(cell(COL.time), [[[formatTime(item.start), MUTED]]]);
    writeCell(cell(COL.duration), [[[String(item.minutes), item.computed
      ? {...MUTED, [A.ITALIC]: true, ...(item.minutes <= 0 ? {[A.FOREGROUND_COLOR]: PROBLEM_COLOR} : {})}
      : MUTED]]]);
    const description = cell(COL.description);
    if (item.admin) {
      writeCell(description, adminLines(details));
    } else {
      const carryOver = item.title === 'Action Items Accountability' ? details.carryOver || [] : [];
      const lines = [[[item.title, BOLD], ...(item.note ? [[item.note, MUTED]] : [])]];
      if (carryOver.length) lines.push(...carryOver.map(text => [[token(item.actions)], [text]]));
      else if (item.actions) lines.push([[token(item.actions)]]);
      if (item.topic) lines.push(...topicLines(presenters).slice(1));
      writeCell(description, lines);
    }
    const attendees = details.attendees || [];
    writeCell(cell(COL.notes), item.notes === false ? [[]]
      : item.perAttendee && attendees.length
        ? attendees.map(name => [[token('bullet')], [name + ': ']])
        : [[[token('bullet')]]]);
    if (item.followUp) writeCell(cell(COL.followUp), [[[token('bullet')]]]);
    shadeRow(table, r, item.kind);
    shadeFollowUp(table, r, item.kind);
  });

  const last = rowCount - 1;
  for (let c = 0; c < MEETING_COLUMNS; c++) writeCell(table.getCell(last, c), [[]]);
  writeCell(table.getCell(last, COL.time), [[[formatTime(times.end), MUTED]]]);
  writeCell(table.getCell(last, COL.duration), [[['end', {...MUTED, [A.ITALIC]: true}]]]);
  shadeRow(table, last, 'round');
  shadeFollowUp(table, last, 'round');

  for (const c of Object.keys(COLUMN_WIDTHS)) table.setColumnWidth(Number(c), COLUMN_WIDTHS[c]);
  for (let r = 0; r < rowCount; r++) padRow(table.getRow(r));
  return meetingId;
}

// Narrow left and right padding in a meeting table's row, so its narrow
// columns hold their text.
function padRow(row) {
  for (let c = 0; c < row.getNumCells(); c++) {
    row.getCell(c).setPaddingLeft(CELL_PADDING).setPaddingRight(CELL_PADDING);
  }
}

// The most attendees in one column of the attendance checklist.
const ATTENDEES_PER_COLUMN = 4;

// The meeting's attendance, above its table: a line saying to check anyone
// who isn't there, then, side by side and closely spaced, the attendees as
// a checklist in columns of up to ATTENDEES_PER_COLUMN, and the meeting's
// Facilitator, Secretary (from details, or left to fill in), and Guests;
// then a little space before the agenda table.
function attendance(body, details) {
  const attendees = details.attendees || [];
  writeRuns(body.appendParagraph(''), [['Attendance', BOLD],
    ['   Check the box next to anyone who isn\'t here.', MUTED]]);

  // As few columns as hold everyone, as evenly filled as can be (5 names
  // make columns of 3 and 2, not 4 and 1).
  const columns = [];
  const count = Math.ceil(Math.max(attendees.length, 1) / ATTENDEES_PER_COLUMN);
  const perColumn = Math.ceil(attendees.length / count);
  for (let c = 0; c < count; c++) {
    columns.push(attendees.length
      ? attendees.slice(c * perColumn, (c + 1) * perColumn).map(name => [[token('checkbox')], [name]])
      : [[[token('checkbox')]]]);
  }
  columns.push([
    [['Facilitator: ', BOLD], ...(details.facilitator ? [[details.facilitator]] : [])],
    [['Secretary: ', BOLD], ...(details.secretary ? [[details.secretary]] : [])],
    [['Guests: ', BOLD]],
  ]);
  const table = body.appendTable([columns.map(() => '')]);
  table.setBorderWidth(0);
  columns.forEach((paragraphs, c) => {
    const cell = table.getCell(0, c);
    writeCell(cell, paragraphs);
    for (let i = 0; i < cell.getNumChildren(); i++) {
      cell.getChild(i).asParagraph().setSpacingBefore(0).setSpacingAfter(0).setLineSpacing(1);
    }
    if (c < columns.length - 1) table.setColumnWidth(c, 130);
  });
  // Docs needs a paragraph between two tables (Apps Script can't delete it,
  // though a person can); meetingTable makes it as small as it can be.
  return body.appendParagraph('').setSpacingBefore(0).setSpacingAfter(0);
}

// A topic's description: its title, a Presenter dropdown (if presenters,
// its list of choices, is given), and a bulleted list.
function topicLines(presenters) {
  return [
    [['Topic', BOLD]],
    ...(presenters ? [[[token('presenter', presenters)]]] : []),
    [[token('bullet')]],
  ];
}

// The ID of a meeting's Presenter dropdown's list of choices (see
// presenterDropdownRequest), in its tab.
function presenterDropdownId(meetingId) {
  return 'kix.p' + meetingId;
}

// The row of links that add an item and update the times.
function linksRow(row, docId, meetingId) {
  for (let c = 0; c < row.getNumCells(); c++) writeCell(row.getCell(c), [[]]);
  writeCell(row.getCell(COL.description), [[
    ['+ Add item', {[A.LINK_URL]: actionUrl('item', docId, meetingId)}], ['        '],
    ['↻ Update times', {[A.LINK_URL]: actionUrl('times', docId, meetingId)}]]]);
}

// The Administrative matters item's description: its title and a bulleted
// list, with the next meeting's date and time, and a link to the previous
// meeting's minutes, when there are any.
function adminLines(details) {
  const bullet = runs => [[token('bullet')], ...runs];
  return [
    [['Administrative matters', BOLD]],
    bullet([['Announcements']]),
    bullet([['Brief Reports']]),
    bullet([['Next meeting date & time: '], ...(details.next ? [[details.next]] : [])]),
    bullet([details.previousUrl
      ? ['Minutes of last meeting', {[A.LINK_URL]: details.previousUrl}]
      : ['Minutes of last meeting'], [' - consented by default within 48 hrs']]),
    bullet([['Modifications if any and consent to agenda for this meeting']]),
  ];
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

// Shades a meeting table row's Follow-up cell, if the table has that
// column, blending it with the row's own shade (kind, as for shadeRow).
function shadeFollowUp(table, r, kind) {
  if (table.getRow(r).getNumCells() <= COL.followUp) return;
  const color = kind === 'round' ? COLOR.followUpRoundRow
    : kind === 'report' ? COLOR.followUpReportRow : COLOR.followUp;
  table.getCell(r, COL.followUp).setBackgroundColor(color);
}

// A placeholder for something only the Docs API can add; see finishWithApi.
function token(kind, ...args) {
  return '⟦' + [kind, ...args].join(' ') + '⟧';
}

// ── Pass 2: the Docs API ─────────────────────────────────────────────────────

function finishWithApi(docId) {
  const firstTabId = Docs.Documents.get(docId, {includeTabsContent: true}).tabs[0].tabProperties.tabId;
  Docs.Documents.batchUpdate({requests: [
    {updateDocumentStyle: {
      documentStyle: {documentFormat: {documentMode: 'PAGELESS'}},
      fields: 'documentFormat.documentMode',
    }},
    {updateDocumentTabProperties: {
      tabProperties: {tabId: firstTabId, title: MAIN_TAB_TITLE}, fields: 'title',
    }},
  ]}, docId);
  replaceTokens(docId);
  backlogTab(docId);
}

// ── The Backlog tab, with the Docs API ───────────────────────────────────────

// The Backlog tab's paragraphs: headings, notes, and a list under each list
// heading, starting with one empty item. style: a named paragraph style;
// list: 'bullet' or 'numbered'.
const BACKLOG = [
  {text: 'Backlog', style: 'HEADING_1'},
  {text: 'Requested for prioritization (any member can add)', style: 'HEADING_2'},
  {text: 'Please include your name and the date.', muted: true},
  {text: 'Policy item requests', style: 'HEADING_3'},
  {text: '', list: 'bullet'},
  {text: 'Task requests', style: 'HEADING_3'},
  {text: '', list: 'bullet'},
  {text: 'Prioritized backlog (lists managed by secretary / op. lead)', style: 'HEADING_2'},
  {text: 'Discussion underway', style: 'HEADING_3'},
  {text: '', list: 'bullet'},
  {text: 'Prioritized for initial discussion', style: 'HEADING_3'},
  {text: '', list: 'numbered'},
  {text: 'Assigned tasks', style: 'HEADING_3'},
  {text: '', list: 'bullet'},
  {text: 'Tasks to assign', style: 'HEADING_3'},
  {text: '', list: 'bullet'},
];

// The Backlog tab, after the first. DocumentApp can't see a tab added in the
// same run, so it's built with the Docs API.
function backlogTab(docId) {
  const reply = Docs.Documents.batchUpdate({requests: [{addDocumentTab: {
    tabProperties: {title: BACKLOG_TAB_TITLE, index: 1},
  }}]}, docId);
  const tabId = reply.replies[0].addDocumentTab.tabProperties.tabId;

  // All the text at once, then each paragraph's style or list, by its
  // position in that text.
  const text = BACKLOG.map(p => p.text + '\n').join('');
  const requests = [{insertText: {location: {index: 1, tabId}, text}}];
  let at = 1;
  for (const p of BACKLOG) {
    const range = {startIndex: at, endIndex: at + p.text.length + 1, tabId};
    requests.push({updateParagraphStyle: {range,
      paragraphStyle: {namedStyleType: p.style || 'NORMAL_TEXT'}, fields: 'namedStyleType'}});
    if (p.muted) {
      requests.push({updateTextStyle: {range: {...range, endIndex: range.endIndex - 1},
        textStyle: {foregroundColor: apiColor(COLOR.muted)}, fields: 'foregroundColor'}});
    }
    if (p.list) {
      requests.push({createParagraphBullets: {range, bulletPreset: p.list === 'numbered'
        ? 'NUMBERED_DECIMAL_ALPHA_ROMAN' : 'BULLET_DISC_CIRCLE_SQUARE'}});
      requests.push(listIndentRequest(range));
    }
    at = range.endIndex;
  }
  Docs.Documents.batchUpdate({requests}, docId);
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
// headings pinned, so they stay in view, and the empty paragraph between it
// and the attendance table above it made a point high, to leave no gap.
function finishMeeting(docId, meetingId, tabId) {
  const content = apiTab(docId, tabId).content;
  const at = content.findIndex(se => se.table && apiMeetingId(se) === meetingId);
  const meeting = content[at];
  const requests = [{pinTableHeaderRows: {
    tableStartLocation: {index: meeting.startIndex, tabId}, pinnedHeaderRowsCount: MEETING_PINNED_ROWS,
  }}];
  Docs.Documents.batchUpdate({requests}, docId);

}

// The empty paragraph Docs needs between the attendance and agenda tables,
// made as small as it can be: a 1-point font, at half line spacing.
function shrinkSpacer(paragraph) {
  paragraph.setAttributes({[A.FONT_SIZE]: 1});
  paragraph.setLineSpacing(0.5).setSpacingBefore(0).setSpacingAfter(0);
}

// Replaces each token in a tab (by default, the first) with its chip or
// list, last first so the earlier ones' positions don't move.
function replaceTokens(docId, tabId) {
  const requests = [];
  for (const t of findTokens(apiTab(docId, tabId).content)) {
    requests.push({deleteContentRange: {range: {startIndex: t.start, endIndex: t.end, tabId}}});
    requests.push(tokenRequest(t, tabId));
    if (t.parts[0] === 'bullet') {
      requests.push(listIndentRequest({startIndex: t.start, endIndex: t.start + 1, tabId}));
    }
  }
  if (requests.length) Docs.Documents.batchUpdate({requests}, docId);
}

// The request that puts a token's chip or list at its place.
function tokenRequest(t, tabId) {
  const location = {index: t.start, tabId};
  const [kind, ...args] = t.parts;
  if (kind === 'presenter') {
    return {insertDropdown: {location, dropdownDefinitionId: args[0],
      selectedOptionId: 'dropdownItem.presenter'}};
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
// tab (each tab has its own): "Presenter", the default, then each attendee
// (up to Docs' limit of 50 choices).
function presenterDropdownRequest(meetingId, attendees, tabId) {
  return {createDropdownDefinition: {tabId, dropdownDefinition: {
    dropdownDefinitionId: presenterDropdownId(meetingId),
    dropdownDefinitionProperties: {title: 'Presenter', options: [
      {optionId: 'dropdownItem.presenter', displayValue: 'Presenter'},
      ...attendees.slice(0, 49).map((name, i) =>
        ({optionId: 'dropdownItem.p' + i, displayValue: name})),
    ]},
  }}};
}

// Bulleted and numbered lists' indents: the bullet 0.08" from the margin,
// and the text a 0.19" hanging indent beyond it (Format → Bullets &
// numbering → Indentation options: left 0.08", hanging 0.19").
const LIST_INDENT_PT = 0.08 * 72;
const LIST_HANGING_PT = 0.19 * 72;

function listIndentRequest(range) {
  return {updateParagraphStyle: {range, paragraphStyle: {
    indentFirstLine: {magnitude: LIST_INDENT_PT, unit: 'PT'},
    indentStart: {magnitude: LIST_INDENT_PT + LIST_HANGING_PT, unit: 'PT'},
  }, fields: 'indentFirstLine,indentStart'}};
}

function apiColor(hex) {
  const n = parseInt(hex.slice(1), 16);
  return {color: {rgbColor: {red: (n >> 16) / 255, green: ((n >> 8) & 255) / 255, blue: (n & 255) / 255}}};
}
