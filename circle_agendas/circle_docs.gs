/**
 * Circle Docs
 *
 * Builds each circle's and working group's agenda doc. The Python scripts
 * (util/circle_agenda_docs.py, run by update_groups_in_google_and_hierarchy.py
 * and add_new_group.py) create the doc empty, in the group's Drive item,
 * labelled (Drive file properties, public ones: the Python scripts are a
 * different app, and can't share appProperties) with the group's address, its parent's
 * name, and the state "new". buildNewCircleDocs, run every
 * BUILD_EVERY_MINUTES by the trigger installTriggers makes (or by hand in
 * the editor), builds each doc so labelled, the same way previewTemplate
 * builds the preview, fills in About this circle, and relabels it "built",
 * with the layout version it was built with, for scripts that later update
 * existing docs. It also rewrites the Parent Circle and Sub-Circles rows of
 * each built doc that change_parent_circle.py or add_new_group.py has
 * relabelled with a new parent or sub-circles (and labelled AGENDA_REFRESH_STRUCTURE).
 * addNewAboutRowsToCircleDocs, run by hand, gives docs built before the
 * Circle calendar and Meeting reminders rows were added those rows.
 */

// Must match util/circle_agenda_docs.py.
const AGENDA_KEYS = {
  group: 'circleAgendaGroup', parent: 'circleAgendaParent', state: 'circleAgendaState',
  layout: 'circleAgendaLayout', sub: 'circleAgendaSub', refresh: 'circleAgendaRefresh',
};
// The refresh label's value for a doc whose parent or sub-circles changed.
const AGENDA_REFRESH_STRUCTURE = 'structure';
const STRUCTURE_ROWS = ['Parent Circle', 'Sub-Circles'];
const AGENDA_STATE_NEW = 'new';
// Set before building, so a build that fails partway is never retried on
// top of itself; such a doc is left for a person to look at.
const AGENDA_STATE_BUILDING = 'building';
const AGENDA_STATE_BUILT = 'built';
// Set instead of building, when a doc labelled new turns out not to be
// empty (someone wrote in it first): it's left as it is, for a person to
// look at.
const AGENDA_STATE_NOT_EMPTY = 'not empty';
// Bump when the layout of what build() makes changes, so scripts updating
// existing docs can tell which they're looking at.
// 2: About this circle's rows reordered (ABOUT_LABELS), and its Drive
// folder row renamed Google Drive Folder and linked by the circle's name.
// 3: About this circle's Circle calendar and Meeting reminders rows added,
// below Zoom room.
const LAYOUT_VERSION = 3;
// The first layout version with About this circle in ABOUT_LABELS' order.
const ABOUT_ORDER_LAYOUT = 2;
// The first layout version with the Circle calendar and Meeting reminders rows.
const NEW_ROWS_LAYOUT = 3;
const BUILD_EVERY_MINUTES = 10;

// Run once, in the editor: builds new circle docs every BUILD_EVERY_MINUTES.
function installTriggers() {
  for (const t of ScriptApp.getProjectTriggers()) {
    if (t.getHandlerFunction() === 'buildNewCircleDocs') ScriptApp.deleteTrigger(t);
  }
  ScriptApp.newTrigger('buildNewCircleDocs').timeBased().everyMinutes(BUILD_EVERY_MINUTES).create();
  Logger.log('buildNewCircleDocs will run every ' + BUILD_EVERY_MINUTES + ' minutes.');
}

function buildNewCircleDocs() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    const resp = Drive.Files.list({
      q: 'properties has { key=\'' + AGENDA_KEYS.state + '\' and value=\'' +
        AGENDA_STATE_NEW + '\' } and trashed = false',
      corpora: 'allDrives', includeItemsFromAllDrives: true, supportsAllDrives: true,
      fields: 'files(id,name,properties)',
    });
    if (!(resp.files || []).length) console.log('No circle docs waiting to be built.');
    for (const file of resp.files || []) {
      try {
        buildCircleDoc(file);
        console.log('Built ' + file.name);
        logLink('Built ' + file.name + ':', 'https://docs.google.com/document/d/' + file.id + '/edit');
      } catch (e) {
        console.error('Couldn\'t build ' + file.name + ': ' + e.message);
      }
    }
    refreshCircleDocStructures();
  } finally {
    lock.releaseLock();
  }
}

// Builds one labelled doc, fills in About this circle, and relabels it.
// Building only ever adds to a doc, so it's done only to one that's still
// empty: one that isn't is relabelled AGENDA_STATE_NOT_EMPTY and left alone.
function buildCircleDoc(file) {
  const props = file.properties || {};
  const email = props[AGENDA_KEYS.group];
  if (!isEmptyDoc(file.id)) {
    setAgendaState(file.id, AGENDA_STATE_NOT_EMPTY);
    throw new Error('it isn\'t empty, so it was left as it is (labelled "' +
      AGENDA_STATE_NOT_EMPTY + '")');
  }
  setAgendaState(file.id, AGENDA_STATE_BUILDING);
  build(file.id);
  const circle = circleOfDoc(file.id, email);
  const subNames = subNamesOf(props);
  fillAbout(file.id, file.name.replace(/ Agenda$/, ''), email, circle, props[AGENDA_KEYS.parent],
    subNames);
  Drive.Files.update({properties: {
    [AGENDA_KEYS.state]: AGENDA_STATE_BUILT, [AGENDA_KEYS.layout]: String(LAYOUT_VERSION),
  }}, file.id, null, {supportsAllDrives: true});
}

// Rewrites the Parent Circle and Sub-Circles rows of each built doc
// labelled AGENDA_REFRESH_STRUCTURE, from its labels, and removes that
// label. Run by buildNewCircleDocs.
function refreshCircleDocStructures() {
  const resp = Drive.Files.list({
    q: 'properties has { key=\'' + AGENDA_KEYS.refresh + '\' and value=\'' +
      AGENDA_REFRESH_STRUCTURE + '\' } and trashed = false',
    corpora: 'allDrives', includeItemsFromAllDrives: true, supportsAllDrives: true,
    fields: 'files(id,name,properties)',
  });
  for (const file of resp.files || []) {
    const props = file.properties || {};
    if (props[AGENDA_KEYS.state] !== AGENDA_STATE_BUILT) continue;
    try {
      const email = props[AGENDA_KEYS.group];
      fillAbout(file.id, null, email, circleOfDoc(file.id, email), props[AGENDA_KEYS.parent],
        subNamesOf(props), false, STRUCTURE_ROWS);
      Drive.Files.update({properties: {[AGENDA_KEYS.refresh]: null}}, file.id, null,
        {supportsAllDrives: true});
      console.log('Updated the parent and sub-circles of ' + file.name);
      logLink('Updated the parent and sub-circles of ' + file.name + ':',
        'https://docs.google.com/document/d/' + file.id + '/edit');
    } catch (e) {
      console.error('Couldn\'t update ' + file.name + ': ' + e.message);
    }
  }
}

// A doc's sub-circles' names, from its labels.
function subNamesOf(props) {
  const subNames = [];
  for (let i = 1; props[AGENDA_KEYS.sub + i]; i++) subNames.push(props[AGENDA_KEYS.sub + i]);
  return subNames;
}

// The doc's title becomes the group's name, and About this circle gets its
// Zoom room, calendar, email address, Drive item (linked by the circle's name), and
// parent and sub-circles (each named, and linked to its Drive folder when
// it's a group with one; the sub-circles as a bulleted list).
// With onlyEmpty, only rows that are empty are filled in; with onlyRows,
// only those rows are written, and emptied when there's nothing to say;
// without a name, the title is left as it is.
function fillAbout(docId, name, email, circle, parentName, subNames, onlyEmpty, onlyRows) {
  const doc = DocumentApp.openById(docId);
  const body = firstTabBody(doc);
  for (let i = 0; i < body.getNumChildren() && name; i++) {
    const el = body.getChild(i);
    if (el.getType() === DocumentApp.ElementType.PARAGRAPH &&
        el.asParagraph().getHeading() === DocumentApp.ParagraphHeading.TITLE) {
      el.asParagraph().setText(name);
      break;
    }
  }
  const circles = loadCircles();
  const folderLink = id => ({[A.LINK_URL]: 'https://drive.google.com/drive/folders/' + id});
  const linkedName = groupName => {
    const group = circleNamed(circles, groupName);
    return [groupName, group ? folderLink(group.driveItemId) : null];
  };
  // Each row's paragraphs, each a list of runs.
  const values = {
    'Zoom room': circle && circle.zoomUrl ? [[['Zoom room', {[A.LINK_URL]: circle.zoomUrl}]]] : [],
    [CALENDAR_ROW_LABEL]: circle && circle.calendarId
      ? [[['Circle calendar', {[A.LINK_URL]: calendarUrl(circle.calendarId)}]]] : [],
    'Circle email address': email ? [[[email, {[A.LINK_URL]: 'mailto:' + email}]]] : [],
    'Google Drive Folder': circle && circle.driveItemId
      ? [[[name || circle.name || 'Google Drive folder', folderLink(circle.driveItemId)]]] : [],
    'Parent Circle': parentName ? [[linkedName(parentName)]] : [],
    'Sub-Circles': (subNames || []).map(sub => [[token('bullet')], linkedName(sub)]),
  };
  const found = findRow(body, text => text === 'Circle email address');
  let bullets = false;
  if (found) {
    const table = found.table;
    for (let r = 0; r < table.getNumRows(); r++) {
      // aboutLabel: docs built before the Drive folder row was renamed.
      const label = aboutLabel(table.getCell(r, 0).getText());
      const cell = table.getCell(r, 1);
      const write = onlyRows ? onlyRows.includes(label)
        : values[label] && values[label].length && !(onlyEmpty && cell.getText().trim());
      if (!write) continue;
      const paragraphs = values[label] || [];
      resetCell(cell);
      writeCell(cell, paragraphs.length ? paragraphs : [[]]);
      if (label === 'Sub-Circles' && paragraphs.length) bullets = true;
    }
  }
  doc.saveAndClose();
  // The ⟦bullet⟧ tokens become the sub-circles' bullets.
  if (bullets) replaceTokens(docId);
}

// Leaves a cell holding one plain, empty paragraph, for writeCell: what was
// there may be a bulleted list, which writeCell can't write over.
function resetCell(cell) {
  cell.insertParagraph(0, '');
  while (cell.getNumChildren() > 1) cell.getChild(1).removeFromParent();
}

// The group of circles (from their footers) with this name, or null: the
// same name, ignoring case, or else one whose name starts the other's (so
// "Full Community (Circle of Everyone / …)" in the Circle Structure finds
// the "Full Community" group).
function circleNamed(circles, name) {
  const n = name.toLowerCase();
  return circles.find(c => (c.name || '').toLowerCase() === n) ||
    circles.find(c => c.name && (n.startsWith(c.name.toLowerCase()) ||
      c.name.toLowerCase().startsWith(n))) || null;
}

// A circle doc's circle: the one whose Drive item holds it, or else the one
// with its labelled address (which can be an alias of the group's own).
function circleOfDoc(docId, email) {
  return circleForDoc(docId) || loadCircles().find(c => c.email === email) ||
    refreshCircles().find(c => c.email === email) || null;
}

// Run from the editor: fills in the empty rows of About this circle (Zoom
// room, calendar, email address, Drive folder, parent, sub-circles) in every built
// circle doc, for docs built before those rows were filled in. Rows that
// already say something are left alone.
function fillAboutForCircleDocs() {
  for (const file of builtCircleDocs()) {
    const props = file.properties || {};
    const email = props[AGENDA_KEYS.group];
    const subNames = subNamesOf(props);
    fillAbout(file.id, null, email, circleOfDoc(file.id, email), props[AGENDA_KEYS.parent],
      subNames, true);
    logLink('Filled in ' + file.name + ':', 'https://docs.google.com/document/d/' + file.id + '/edit');
  }
}

// Run from the editor: rewrites the Parent Circle and Sub-Circles rows of
// every built circle doc from its labels, the sub-circles as a bulleted
// list (for docs built when they were a comma-separated line). Anything
// typed into those rows by hand is replaced.
function rewriteStructureRowsInCircleDocs() {
  for (const file of builtCircleDocs()) {
    const props = file.properties || {};
    const email = props[AGENDA_KEYS.group];
    fillAbout(file.id, null, email, circleOfDoc(file.id, email), props[AGENDA_KEYS.parent],
      subNamesOf(props), false, STRUCTURE_ROWS);
    logLink('Rewrote the parent and sub-circles of ' + file.name + ':',
      'https://docs.google.com/document/d/' + file.id + '/edit');
  }
}

// Run from the editor: puts About this circle's rows in their new order
// (ABOUT_LABELS) in every built doc with an older layout, renames its
// Circle Google Drive Folder row Google Drive Folder, and rewrites that row
// (linked by the circle's name) and the Parent Circle and Sub-Circles rows
// from its labels; then labels it with ABOUT_ORDER_LAYOUT. Only the rows
// the scripts fill in move; the rows filled in by hand (ABOUT_HAND_FILLED)
// stay where they are, so their smart chips are kept. A doc it can't
// reorder (its rows aren't the expected ones, say) is left as it is, and
// the log says why. Try it first with tryReorderAboutOnCopy.
function reorderAboutInCircleDocs() {
  for (const file of builtCircleDocs()) {
    const props = file.properties || {};
    if (Number(props[AGENDA_KEYS.layout] || 1) >= ABOUT_ORDER_LAYOUT) continue;
    try {
      reorderAboutAndFill(file.id, props);
      Drive.Files.update({properties: {[AGENDA_KEYS.layout]: String(ABOUT_ORDER_LAYOUT)}},
        file.id, null, {supportsAllDrives: true});
      logLink('Reordered About this circle in ' + file.name + ':',
        'https://docs.google.com/document/d/' + file.id + '/edit');
    } catch (e) {
      console.error('Couldn\'t reorder About this circle in ' + file.name + ': ' + e.message);
    }
  }
}

// Run from the editor: copies the first built doc that reorderAboutInCircleDocs
// would change into your My Drive, unlabelled (so no script acts on it),
// reorders the copy's About this circle as that would, and logs its link,
// to look at before running reorderAboutInCircleDocs. Delete the copy after.
function tryReorderAboutOnCopy() {
  const file = builtCircleDocs().find(f =>
    Number((f.properties || {})[AGENDA_KEYS.layout] || 1) < ABOUT_ORDER_LAYOUT);
  if (!file) return Logger.log('No built doc needs reordering.');
  const copy = DriveApp.getFileById(file.id).makeCopy('Reorder test: ' + file.name,
    DriveApp.getRootFolder());
  const unlabel = {};
  for (const key of Object.keys(file.properties || {})) unlabel[key] = null;
  Drive.Files.update({properties: unlabel}, copy.getId(), null, {supportsAllDrives: true});
  reorderAboutAndFill(copy.getId(), file.properties || {});
  logLink('Reordered a copy of ' + file.name + ':',
    'https://docs.google.com/document/d/' + copy.getId() + '/edit');
}

// Reorders a doc's About this circle, and rewrites its Google Drive Folder
// (if its circle has one), Parent Circle, and Sub-Circles rows from labels
// props (the doc's own, or the doc it was copied from).
function reorderAboutAndFill(docId, props) {
  reorderAbout(docId);
  const email = props[AGENDA_KEYS.group];
  const circle = circleOfDoc(docId, email);
  const rows = (circle && circle.driveItemId ? ['Google Drive Folder'] : []).concat(STRUCTURE_ROWS);
  fillAbout(docId, null, email, circle, props[AGENDA_KEYS.parent], subNamesOf(props), false, rows);
}

// Puts a doc's About this circle rows in ABOUT_LABELS' order (moving only
// the ones aboutReorder says to, by copying them), and renames an old Drive
// folder row. Throws, changing nothing, if aboutReorder says it can't, or if
// a moved row that isn't rewritten afterwards (Zoom room, Circle email
// address) holds a chip, which copying could lose.
function reorderAbout(docId) {
  const doc = DocumentApp.openById(docId);
  const found = findRow(firstTabBody(doc), text => text === 'Circle email address');
  if (!found) throw new Error('it has no About this circle table');
  const table = found.table;
  const labelOf = r => aboutLabel(table.getCell(r, 0).getText());
  const labels = [];
  for (let r = 0; r < table.getNumRows(); r++) labels.push(labelOf(r));
  const plan = aboutReorder(labels);
  if (plan.error) throw new Error(plan.error);
  for (const label of ['Zoom room', 'Circle email address']) {
    const row = table.getRow(labels.indexOf(label));
    const leaves = {};
    addLeafCounts(row, leaves);
    if (Object.keys(leaves).length) {
      throw new Error('its ' + label + ' row holds ' + formatCounts(leaves) + ', which moving could lose');
    }
  }
  const copies = {};
  for (let r = labels.length - 1; r >= 0; r--) {
    if (!plan.move.includes(labels[r])) continue;
    copies[labels[r]] = table.getRow(r).copy();
    table.removeRow(r);
  }
  plan.order.forEach((label, i) => {
    if (copies[label]) table.insertTableRow(i, copies[label]);
  });
  const driveLabel = table.getCell(plan.order.indexOf('Google Drive Folder'), 0);
  if (driveLabel.getText().trim() !== 'Google Drive Folder') {
    writeCell(driveLabel, [[['Google Drive Folder', BOLD]]]);
    alignCell(driveLabel, DocumentApp.HorizontalAlignment.RIGHT);
  }
  doc.saveAndClose();
}

// Run from the editor: adds the rows added to About this circle since
// layout version 2 (ABOUT_ADDED_LATER: Circle calendar, linking the
// circle's calendar, and Meeting reminders, saying 24 hours, the default,
// with how to write it in its label cell), each in its place, to
// every built doc of layout version 2 that lacks them, and labels it
// NEW_ROWS_LAYOUT. A doc of layout version 1 is skipped (the log says so):
// run reorderAboutInCircleDocs first. Safe to run again.
function addNewAboutRowsToCircleDocs() {
  for (const file of builtCircleDocs()) {
    const props = file.properties || {};
    const layout = Number(props[AGENDA_KEYS.layout] || 1);
    if (layout >= NEW_ROWS_LAYOUT) continue;
    if (layout < ABOUT_ORDER_LAYOUT) {
      console.warn('Skipped ' + file.name + ': run reorderAboutInCircleDocs first.');
      continue;
    }
    try {
      const added = addNewAboutRows(file.id);
      const email = props[AGENDA_KEYS.group];
      fillAbout(file.id, null, email, circleOfDoc(file.id, email), props[AGENDA_KEYS.parent],
        subNamesOf(props), false, [CALENDAR_ROW_LABEL]);
      Drive.Files.update({properties: {[AGENDA_KEYS.layout]: String(NEW_ROWS_LAYOUT)}},
        file.id, null, {supportsAllDrives: true});
      logLink((added.length ? 'Added ' + added.join(' and ') + ' to ' : 'Relabelled ') +
        file.name + ':', 'https://docs.google.com/document/d/' + file.id + '/edit');
    } catch (e) {
      console.error('Couldn\'t add rows to ' + file.name + ': ' + e.message);
    }
  }
}

// Adds the rows ABOUT_ADDED_LATER that a doc's About this circle lacks,
// each just below the row it follows (see aboutRowsToAdd), as aboutTable
// makes them, empty but for Meeting reminders' default (and, in its label
// cell, how to write it).
// Returns the labels of the rows added.
function addNewAboutRows(docId) {
  const doc = DocumentApp.openById(docId);
  const found = findRow(firstTabBody(doc), text => text === 'Circle email address');
  if (!found) throw new Error('it has no About this circle table');
  const table = found.table;
  const labels = [];
  for (let r = 0; r < table.getNumRows(); r++) labels.push(aboutLabel(table.getCell(r, 0).getText()));
  const adds = aboutRowsToAdd(labels);
  if (adds.error) throw new Error(adds.error);
  for (const {label, index} of adds) {
    const row = table.insertTableRow(index);
    row.appendTableCell('');
    row.appendTableCell('');
    writeAboutRow(row, label === REMINDER_ROW_LABEL ? [label, REMINDER_HINT] : [label],
      label === REMINDER_ROW_LABEL ? [[[REMINDER_DEFAULT_TEXT]]] : []);
  }
  doc.saveAndClose();
  return adds.map(a => a.label);
}

// Every built circle doc ({id, name, properties}).
function builtCircleDocs() {
  const resp = Drive.Files.list({
    q: 'properties has { key=\'' + AGENDA_KEYS.state + '\' and value=\'' + AGENDA_STATE_BUILT +
      '\' } and trashed = false',
    corpora: 'allDrives', includeItemsFromAllDrives: true, supportsAllDrives: true,
    fields: 'files(id,name,properties)',
  });
  return resp.files || [];
}

// Whether a doc has a single tab holding nothing but empty paragraphs.
function isEmptyDoc(docId) {
  const doc = DocumentApp.openById(docId);
  if (allTabs(doc).length > 1) return false;
  const body = firstTabBody(doc);
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() !== DocumentApp.ElementType.PARAGRAPH || el.asParagraph().getNumChildren() > 1 ||
        el.asParagraph().getText().trim()) {
      return false;
    }
  }
  return true;
}

function setAgendaState(docId, state) {
  Drive.Files.update({properties: {[AGENDA_KEYS.state]: state}}, docId, null,
    {supportsAllDrives: true});
}
