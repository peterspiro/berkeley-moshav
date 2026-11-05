/**
 * Circle Docs
 *
 * Builds each circle's and working group's agenda doc. The Python scripts
 * (util/circle_agenda_docs.py, run by update_groups.py
 * and add_new_group.py) create the doc empty, in the group's Drive item,
 * labelled (Drive file properties, public ones: the Python scripts are a
 * different app, and can't share appProperties) with the group's address
 * and the state "new". buildNewCircleDocs, run every
 * BUILD_EVERY_MINUTES by the trigger installTriggers makes (or by hand in
 * the editor), builds each doc so labelled, the same way previewTemplate
 * builds the preview, fills in About this circle (its Parent Circle and
 * Sub-Circles rows from the Circle Structure; see structure.gs), and
 * relabels it "built", with the layout version it was built with, for
 * scripts that later update existing docs. It also applies any change to
 * the Circle Structure, and, in each built doc changed since its last run,
 * recolors past Term ends dates and puts back the structure rows if
 * they've been edited.
 * addNewAboutRowsToCircleDocs, run by hand, gives docs built before the
 * Circle calendar and Remind before meetings rows were added those rows.
 */

// Must match util/circle_agenda_docs.py.
const AGENDA_KEYS = {
  group: 'circleAgendaGroup', state: 'circleAgendaState', layout: 'circleAgendaLayout',
};
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
// 3: About this circle's Circle calendar and Remind before meetings rows added,
// below Zoom room.
// 4: the Roles table's Name a dropdown of the circle's members, and its Term
// ends a date chip.
const LAYOUT_VERSION = 4;
// The first layout version with About this circle in ABOUT_LABELS' order.
const ABOUT_ORDER_LAYOUT = 2;
// The first layout version with the Circle calendar and Remind before meetings rows.
const NEW_ROWS_LAYOUT = 3;
// The first layout version with the Roles' dropdowns and date chips.
const ROLES_CHIPS_LAYOUT = 4;
const BUILD_EVERY_MINUTES = 10;

// The hour (in the script's time zone) updateRolesInCircleDocs runs, each
// day.
const ROLES_UPDATE_HOUR = 1;

// Run once, in the editor (setupCircleStructure runs it too): builds new
// circle docs every BUILD_EVERY_MINUTES, updates their Roles and structure
// rows every day, brings the Circle Structure sheet's groups up to date
// every SYNC_EVERY_HOURS, and applies each edit of the sheet (once
// setupCircleStructure has made it).
function installTriggers() {
  const handlers = ['buildNewCircleDocs', 'updateRolesInCircleDocs', 'syncCircleStructureGroups',
    'onCircleStructureEdit'];
  for (const t of ScriptApp.getProjectTriggers()) {
    if (handlers.includes(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  }
  ScriptApp.newTrigger('buildNewCircleDocs').timeBased().everyMinutes(BUILD_EVERY_MINUTES).create();
  ScriptApp.newTrigger('updateRolesInCircleDocs').timeBased().everyDays(1)
    .atHour(ROLES_UPDATE_HOUR).create();
  Logger.log('buildNewCircleDocs will run every ' + BUILD_EVERY_MINUTES + ' minutes, and ' +
    'updateRolesInCircleDocs every day at ' + ROLES_UPDATE_HOUR + ':00.');
  const sheetId = PropertiesService.getScriptProperties().getProperty(STRUCTURE_SHEET_KEY);
  if (sheetId) {
    ScriptApp.newTrigger('syncCircleStructureGroups').timeBased().everyHours(SYNC_EVERY_HOURS).create();
    ScriptApp.newTrigger('onCircleStructureEdit').forSpreadsheet(sheetId).onEdit().create();
    Logger.log('syncCircleStructureGroups will run every ' + SYNC_EVERY_HOURS + ' hour(s), and ' +
      'onCircleStructureEdit on each edit of the Circle Structure sheet.');
  }
}

// Run every day by the trigger installTriggers makes (or by hand): updates
// the Roles in every built doc (see updateRoles), and logs what changed;
// then writes each doc's structure rows that are out of date (the next
// meetings they list, say).
function updateRolesInCircleDocs() {
  try {
    refreshStructureInAllCircleDocs();
  } catch (e) {
    console.error('Couldn\'t update the docs\' structure rows: ' + e.message);
  }
  for (const file of builtCircleDocs()) {
    let circle = null;
    try {
      circle = circleOfDoc(file.id, (file.properties || {})[AGENDA_KEYS.group]);
    } catch (e) {
      console.error('Couldn\'t find ' + file.name + '\'s circle: ' + e.message);
    }
    for (const line of updateRoles(file.id, circle)) console.log(file.name + ': ' + line);
  }
}

// What the daily run does to a doc: brings the Roles table's Name
// dropdowns' choices up to date with the circle's members (see
// updateRoleDropdowns; not without a circle), and marks its past Term ends
// dates red (see markPastTermEnds). Returns lines saying what changed, or what
// couldn't be done; each step is tried even if one before it failed.
function updateRoles(docId, circle) {
  const lines = [];
  const step = (what, fn) => {
    try {
      fn();
    } catch (e) {
      lines.push('Couldn\'t ' + what + ': ' + e.message);
    }
  };
  step('update the Roles choices', () => {
    if (circle && updateRoleDropdowns(docId, circleMemberNames(circle))) {
      lines.push('Updated the Roles choices.');
    }
  });
  step('check the terms', () => {
    const marked = markPastTermEnds(docId);
    if (marked) lines.push('Recolored ' + marked + ' Term ends date(s).');
  });
  return lines;
}

// Brings the Roles table's Name dropdowns' choices up to date with the
// circle's members (names), keeping what each dropdown shows (see
// updatedRoleChoices). The dropdowns start sharing ROLES_DROPDOWN_ID's
// choices, but Docs may give one whose choices someone edits a list of its
// own, so each list a Name dropdown uses is updated. Returns whether
// anything changed. Does nothing in docs built before the Roles had
// dropdowns, or with no members (which more likely means they couldn't be
// read than that everyone left).
function updateRoleDropdowns(docId, names) {
  if (!names.length) return false;
  const tab = Docs.Documents.get(docId, {includeTabsContent: true}).tabs[0];
  const definitions = tab.documentTab.dropdownDefinitions || {};
  // The options each list's dropdowns show, by the list's ID.
  const selected = {};
  for (const pe of rolesColumnElements(tab.documentTab.body.content, 1, 'dropdown')) {
    const props = pe.dropdown.dropdownProperties || {};
    (selected[props.dropdownDefinitionId] = selected[props.dropdownDefinitionId] || [])
      .push(props.selectedOptionId);
  }
  const requests = [];
  for (const id of Object.keys(selected)) {
    if (!definitions[id]) continue;
    const existing = definitions[id].dropdownDefinitionProperties.options || [];
    const {options, replacements} = updatedRoleChoices(existing, names, selected[id]);
    if (sameChoices(existing, options)) continue;
    requests.push({updateDropdownDefinitionProperties: {
      tabId: tab.tabProperties.tabId, dropdownDefinitionId: id,
      dropdownDefinitionProperties: {options}, fields: 'options',
      selectedOptionIdReplacements: replacements,
    }});
  }
  if (requests.length) Docs.Documents.batchUpdate({requests}, docId);
  return requests.length > 0;
}

// Colors each Term ends date chip in the Roles table red if its date is
// past, and back to the default once it's changed to one that isn't.
// Docs can't run a script when a chip is edited, so this runs daily.
// Returns how many it recolored.
function markPastTermEnds(docId) {
  const today = Utilities.formatDate(new Date(), TIME_ZONE, 'yyyy-MM-dd');
  const requests = [];
  for (const pe of termEndChips(apiTab(docId).content)) {
    const past = termEnded(chipDate(pe.dateElement), today);
    const style = pe.dateElement.textStyle || {};
    const red = !!(style.foregroundColor && style.foregroundColor.color);
    if (past === red) continue;
    requests.push({updateTextStyle: {
      range: {startIndex: pe.startIndex, endIndex: pe.endIndex},
      textStyle: past ? {foregroundColor: apiColor(PROBLEM_COLOR)} : {},
      fields: 'foregroundColor',
    }});
  }
  if (requests.length) Docs.Documents.batchUpdate({requests}, docId);
  return requests.length;
}

// The Term ends date chips (Docs API paragraph elements) in a tab's
// content: those in the third column of the Roles table.
function termEndChips(content) {
  return rolesColumnElements(content, 2, 'dateElement');
}

// The paragraph elements of a kind ('dropdown', 'dateElement') in a column
// of the Roles table, below its headings, in a tab's Docs API content.
function rolesColumnElements(content, column, kind) {
  const found = [];
  for (const se of content) {
    const rows = se.table ? se.table.tableRows : [];
    if (!rows.length || !apiCellText(rows[0].tableCells[0]).trim().startsWith('Circle Roles')) continue;
    for (const row of rows.slice(1)) {
      const cell = row.tableCells[column];
      if (!cell) continue;
      walkParagraphs(cell.content, para => {
        for (const pe of para.elements || []) if (pe[kind]) found.push(pe);
      });
    }
  }
  return found;
}

// Lines describing what the Docs API sees of the Roles table: each Name
// dropdown's list and choice, each list's choices, and each Term ends
// chip's properties. For tryUpdateRoles.
function describeRoles(docId) {
  const tab = Docs.Documents.get(docId, {includeTabsContent: true}).tabs[0];
  const content = tab.documentTab.body.content;
  const definitions = tab.documentTab.dropdownDefinitions || {};
  const lines = ['Name dropdowns:'];
  const ids = [];
  for (const pe of rolesColumnElements(content, 1, 'dropdown')) {
    const props = pe.dropdown.dropdownProperties || {};
    lines.push('  list ' + props.dropdownDefinitionId + ', showing ' + props.selectedOptionId +
      ' "' + props.displayValue + '"');
    if (!ids.includes(props.dropdownDefinitionId)) ids.push(props.dropdownDefinitionId);
  }
  for (const id of ids) {
    const options = definitions[id] ? definitions[id].dropdownDefinitionProperties.options || [] : null;
    lines.push('List ' + id + ': ' + (options
      ? options.map(o => o.optionId + ' "' + o.displayValue + '"').join(', ') : '(not found)'));
  }
  lines.push('Term ends chips:');
  for (const pe of termEndChips(content)) {
    lines.push('  ' + JSON.stringify(pe.dateElement.dateElementProperties) +
      ' color ' + JSON.stringify((pe.dateElement.textStyle || {}).foregroundColor || null));
  }
  return lines;
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
    try {
      applyStructure();
    } catch (e) {
      console.error('Couldn\'t apply the Circle Structure: ' + e.message);
    }
    try {
      checkChangedCircleDocs();
    } catch (e) {
      console.error('Couldn\'t check the changed docs: ' + e.message);
    }
  } finally {
    lock.releaseLock();
  }
}

// The script property holding when checkChangedCircleDocs last started.
const DATES_CHECKED_KEY = 'circleDocDatesCheckedAt';
// How far before its last start each run looks, in case Drive notes a
// change a little after it's made.
const DATES_CHECK_OVERLAP_MS = 2 * 60 * 1000;

// Run by buildNewCircleDocs, every BUILD_EVERY_MINUTES: in each built doc
// changed since the last run, recolors the Term ends dates
// (markPastTermEnds), so a date someone changes is colored within minutes
// rather than at the daily run, and writes the structure rows
// (refreshStructureRows) if they don't say what they should: someone's
// edited them, or chosen a new Leader or Feedback Link. (A doc a run
// changes counts as changed for the next one, which finds nothing to do.)
function checkChangedCircleDocs() {
  const props = PropertiesService.getScriptProperties();
  const started = Date.now();
  const last = Number(props.getProperty(DATES_CHECKED_KEY)) ||
    started - BUILD_EVERY_MINUTES * 60 * 1000;
  const since = new Date(last - DATES_CHECK_OVERLAP_MS).toISOString();
  const resp = Drive.Files.list({
    q: 'properties has { key=\'' + AGENDA_KEYS.state + '\' and value=\'' + AGENDA_STATE_BUILT +
      '\' } and trashed = false and modifiedTime > \'' + since + '\'',
    corpora: 'allDrives', includeItemsFromAllDrives: true, supportsAllDrives: true,
    fields: 'files(id,name,properties)',
  });
  const files = resp.files || [];
  const ctx = files.length ? loadStructureContext() : null;
  for (const file of files) {
    try {
      const marked = markPastTermEnds(file.id);
      if (marked) console.log(file.name + ': recolored ' + marked + ' Term ends date(s).');
    } catch (e) {
      console.error('Couldn\'t check the dates in ' + file.name + ': ' + e.message);
    }
    if (ctx) refreshStructureRowsOf(file, ctx);
  }
  props.setProperty(DATES_CHECKED_KEY, String(started));
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
  const circle = circleOfDoc(file.id, email);
  let members = [];
  try {
    members = circle ? circleMemberNames(circle) : [];
  } catch (e) {
    console.error('Couldn\'t list ' + file.name + '\'s members, for its Roles: ' + e.message);
  }
  build(file.id, members);
  fillAbout(file.id, file.name.replace(/ Agenda$/, ''), email, circle);
  const ctx = loadStructureContext();
  if (ctx) refreshStructureRowsOf(file, ctx);
  Drive.Files.update({properties: {
    [AGENDA_KEYS.state]: AGENDA_STATE_BUILT, [AGENDA_KEYS.layout]: String(LAYOUT_VERSION),
  }}, file.id, null, {supportsAllDrives: true});
}

// The doc's title becomes the group's name, and About this circle gets its
// Zoom room, calendar, email address, and Drive item (linked by the
// circle's name). (Its Parent Circle and Sub-Circles rows come from the
// Circle Structure: see refreshStructureRows, structure.gs.)
// With onlyEmpty, only rows that are empty are filled in; with onlyRows,
// only those rows are written, and emptied when there's nothing to say;
// without a name, the title is left as it is.
function fillAbout(docId, name, email, circle, onlyEmpty, onlyRows) {
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
  const folderLink = id => ({[A.LINK_URL]: 'https://drive.google.com/drive/folders/' + id});
  // Each row's paragraphs, each a list of runs.
  const values = {
    'Zoom room': circle && circle.zoomUrl ? [[['Zoom room', {[A.LINK_URL]: circle.zoomUrl}]]] : [],
    [CALENDAR_ROW_LABEL]: circle && circle.calendarId
      ? [[['Circle calendar', {[A.LINK_URL]: calendarUrl(circle.calendarId)}]]] : [],
    'Circle email address': email ? [[[email, {[A.LINK_URL]: 'mailto:' + email}]]] : [],
    'Google Drive Folder': circle && circle.driveItemId
      ? [[[name || circle.name || 'Google Drive folder', folderLink(circle.driveItemId)]]] : [],
  };
  const found = findRow(body, text => text === 'Circle email address');
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
    }
  }
  doc.saveAndClose();
}

// Leaves a cell holding one plain, empty paragraph, for writeCell: what was
// there may be a bulleted list, which writeCell can't write over.
function resetCell(cell) {
  cell.insertParagraph(0, '');
  while (cell.getNumChildren() > 1) cell.getChild(1).removeFromParent();
}

// A circle doc's circle: the one whose Drive item holds it, or else the one
// with its labelled address (which can be an alias of the group's own).
function circleOfDoc(docId, email) {
  return circleForDoc(docId) || loadCircles().find(c => c.email === email) ||
    refreshCircles().find(c => c.email === email) || null;
}

// Run from the editor: fills in the empty rows of About this circle (Zoom
// room, calendar, email address, Drive folder) in every built circle doc,
// for docs built before those rows were filled in, and writes the parent
// and sub-circles (see refreshStructureRows). Rows that already say
// something are left alone, but for those two.
function fillAboutForCircleDocs() {
  const ctx = loadStructureContext();
  for (const file of builtCircleDocs()) {
    const email = (file.properties || {})[AGENDA_KEYS.group];
    fillAbout(file.id, null, email, circleOfDoc(file.id, email), true);
    if (ctx) refreshStructureRowsOf(file, ctx);
    logLink('Filled in ' + file.name + ':', 'https://docs.google.com/document/d/' + file.id + '/edit');
  }
}

// Run from the editor: puts About this circle's rows in their new order
// (ABOUT_LABELS) in every built doc with an older layout, renames its
// Circle Google Drive Folder row Google Drive Folder, and rewrites that row
// (linked by the circle's name) and the Parent Circle and Sub-Circles rows
// (from the Circle Structure); then labels it with ABOUT_ORDER_LAYOUT. Only the rows
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
// row (if its circle has one), and its Parent Circle and Sub-Circles rows
// (from the Circle Structure), for the group labelled in props (the doc's
// own labels, or those of the doc it was copied from).
function reorderAboutAndFill(docId, props) {
  reorderAbout(docId);
  const email = props[AGENDA_KEYS.group];
  const circle = circleOfDoc(docId, email);
  if (circle && circle.driveItemId) fillAbout(docId, null, email, circle, false, ['Google Drive Folder']);
  const ctx = loadStructureContext();
  if (ctx) refreshStructureRows(docId, ctx, ctx.groups.find(g => g.email === (email || '').toLowerCase()) || null);
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
// circle's calendar, and Remind before meetings, saying 24 hours, the default,
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
      fillAbout(file.id, null, email, circleOfDoc(file.id, email), false, [CALENDAR_ROW_LABEL]);
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
// makes them, empty but for Remind before meetings' default (and, in its label
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

// Run from the editor: in every built doc from before the Roles had
// dropdowns and date chips (layout version 3), makes its Roles table as
// rolesTable makes it (see upgradeRoles), and labels the doc layout version
// ROLES_CHIPS_LAYOUT, so the daily and 10-minute runs look after it. Docs
// on an older layout are skipped: run addNewAboutRowsToCircleDocs first.
function upgradeRolesInCircleDocs() {
  for (const file of builtCircleDocs()) {
    const props = file.properties || {};
    const layout = Number(props[AGENDA_KEYS.layout] || 1);
    if (layout >= ROLES_CHIPS_LAYOUT) continue;
    if (layout < NEW_ROWS_LAYOUT) {
      console.warn('Skipped ' + file.name + ': run addNewAboutRowsToCircleDocs first.');
      continue;
    }
    try {
      const notes = upgradeRolesOf(file.id, props);
      Drive.Files.update({properties: {[AGENDA_KEYS.layout]: String(ROLES_CHIPS_LAYOUT)}},
        file.id, null, {supportsAllDrives: true});
      logLink('Upgraded the Roles in ' + file.name + ':',
        'https://docs.google.com/document/d/' + file.id + '/edit');
      for (const note of notes) Logger.log('  ' + note);
    } catch (e) {
      console.error('Couldn\'t upgrade the Roles in ' + file.name + ': ' + e.message);
    }
  }
}

// Run from the editor: copies the first built doc that
// upgradeRolesInCircleDocs would change into your My Drive, unlabelled (so
// no script acts on it), upgrades the copy's Roles as that would, and logs
// its link, to look at before running upgradeRolesInCircleDocs. Delete the
// copy after.
function tryUpgradeRolesOnCopy() {
  const file = builtCircleDocs().find(f => {
    const layout = Number((f.properties || {})[AGENDA_KEYS.layout] || 1);
    return layout >= NEW_ROWS_LAYOUT && layout < ROLES_CHIPS_LAYOUT;
  });
  if (!file) return Logger.log('No built doc needs its Roles upgraded.');
  const copy = DriveApp.getFileById(file.id).makeCopy('Roles test: ' + file.name,
    DriveApp.getRootFolder());
  const unlabel = {};
  for (const key of Object.keys(file.properties || {})) unlabel[key] = null;
  Drive.Files.update({properties: unlabel}, copy.getId(), null, {supportsAllDrives: true});
  // The copy isn't in the circle's folder, so its circle is the original's.
  const notes = upgradeRolesOf(copy.getId(), file.properties || {}, file.id);
  logLink('Upgraded the Roles in a copy of ' + file.name + ':',
    'https://docs.google.com/document/d/' + copy.getId() + '/edit');
  for (const note of notes) Logger.log('  ' + note);
}

// Upgrades a doc's Roles with its circle's members: the circle of the doc
// (or of circleDocId, the doc a copy was made from) or of its labels props.
// Returns notes, starting with whose members the dropdowns list.
function upgradeRolesOf(docId, props, circleDocId) {
  const circle = circleOfDoc(circleDocId || docId, props[AGENDA_KEYS.group]);
  const names = circle ? circleMemberNames(circle) : [];
  const notes = upgradeRoles(docId, names);
  notes.unshift(circle
    ? 'The Name dropdowns list ' + names.length + ' members of ' + circle.name + '.'
    : 'Couldn\'t find the doc\'s circle, so the Name dropdowns list no members.');
  return notes;
}

// Makes a doc's Roles table as rolesTable makes it, keeping what's in it:
// each Name cell becomes a dropdown of the circle's members (names) showing
// what was written there (a choice of its own, if it isn't a member's
// name; the dash, if it was empty), and each Term ends cell a date chip of
// the date written there (today's, if it was empty). A Term ends that
// can't be read as a date is left as it is. Its heading loses its grey
// "yyyy-mm-dd". Returns notes on what was left as it was. Throws, changing
// nothing, if the doc has no Roles table or its Roles already have
// dropdowns.
function upgradeRoles(docId, names) {
  const tab = Docs.Documents.get(docId, {includeTabsContent: true}).tabs[0];
  if ((tab.documentTab.dropdownDefinitions || {})[ROLES_DROPDOWN_ID]) {
    throw new Error('its Roles already have dropdowns');
  }
  const apiTable = tab.documentTab.body.content.find(se => se.table &&
    apiCellText(se.table.tableRows[0].tableCells[0]).trim().startsWith('Circle Roles'));
  if (!apiTable) throw new Error('it has no Roles table');
  // What's written in each role's Name and Term ends, chips included.
  const written = apiTable.table.tableRows.slice(1).map(row => ({
    role: apiCellText(row.tableCells[0]).trim(),
    name: row.tableCells[1] ? apiCellValue(row.tableCells[1]) : '',
    term: row.tableCells[2] ? apiCellValue(row.tableCells[2]) : '',
  }));
  const options = roleChoices(names, written.map(w => w.name));
  const today = Utilities.formatDate(new Date(), TIME_ZONE, 'yyyy-MM-dd');
  const notes = [];

  const doc = DocumentApp.openById(docId);
  const found = findRow(firstTabBody(doc), text => text.startsWith('Circle Roles'));
  if (!found || found.table.getNumRows() !== written.length + 1) {
    throw new Error('its Roles table couldn\'t be read');
  }
  const table = found.table;
  const rewrite = (cell, runs, align) => {
    resetCell(cell);
    writeCell(cell, [runs]);
    if (align) alignCell(cell, DocumentApp.HorizontalAlignment.CENTER);
  };
  if (table.getRow(0).getNumCells() > 2) rewrite(table.getCell(0, 2), [['Term ends', BOLD]], true);
  written.forEach((w, i) => {
    const row = table.getRow(i + 1);
    if (row.getNumCells() < 3) {
      notes.push('Left the ' + snippet(w.role, 30) + ' row as it was: it has too few cells.');
      return;
    }
    rewrite(row.getCell(1), [[token('dropdown', ROLES_DROPDOWN_ID, roleOptionId(options, w.name))]]);
    const date = w.term ? dateFromDisplayText(w.term) : today;
    if (date) {
      rewrite(row.getCell(2), [[token('date', date)]], true);
    } else {
      notes.push('Left ' + snippet(w.role, 30) + '\'s Term ends, "' + w.term + '", as it was: ' +
        'it isn\'t a date the script can read. Replace it with a date chip (@date) by hand.');
    }
  });
  doc.saveAndClose();

  Docs.Documents.batchUpdate({requests: [
    roleDropdownRequest(ROLES_DROPDOWN_ID, options, tab.tabProperties.tabId)]}, docId);
  replaceTokens(docId);
  markPastTermEnds(docId);
  return notes;
}

// What a Docs API table cell says, chips included (a person chip by name, a
// dropdown by its choice, a date as shown), its lines joined by spaces.
function apiCellValue(cell) {
  const parts = [];
  walkParagraphs(cell.content, para => {
    for (const pe of para.elements || []) {
      if (pe.textRun) parts.push(pe.textRun.content);
      else if (pe.person) parts.push((pe.person.personProperties || {}).name ||
        (pe.person.personProperties || {}).email || '');
      else if (pe.dropdown) parts.push((pe.dropdown.dropdownProperties || {}).displayValue || '');
      else if (pe.dateElement) parts.push((pe.dateElement.dateElementProperties || {}).displayText || '');
    }
    parts.push(' ');
  });
  return parts.join('').replace(/\s+/g, ' ').trim();
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

// Run from the editor: puts back the Roles table's role labels (column 1)
// in every built doc where a bulleted list has got into one. (An earlier
// version, making the Circle Structure's Sub-Circles bullets with the Docs
// API from an out-of-date read of the doc, deleted 8 characters of some of
// those cells and bulleted them.) Logs each doc it changed.
function repairRoleLabelsInCircleDocs() {
  for (const file of builtCircleDocs()) {
    try {
      const fixed = repairRoleLabels(file.id);
      if (fixed.length) {
        logLink('Put back ' + fixed.join(', ') + ' in the Roles of ' + file.name + ':',
          'https://docs.google.com/document/d/' + file.id + '/edit');
      }
    } catch (e) {
      console.error('Couldn\'t check the Roles of ' + file.name + ': ' + e.message);
    }
  }
}

// Rewrites each role label cell of a doc's Roles table that holds a list
// item, as rolesTable writes it (the table's rows are ROLE_LABELS, in
// order). Returns the names of the roles put back.
function repairRoleLabels(docId) {
  const doc = DocumentApp.openById(docId);
  const found = findRow(firstTabBody(doc), text => text.startsWith('Circle Roles'));
  if (!found || found.table.getNumRows() !== ROLE_LABELS.length + 1) return [];
  const fixed = [];
  ROLE_LABELS.forEach(([name, what], i) => {
    const cell = found.table.getCell(i + 1, 0);
    let listed = false;
    for (let k = 0; k < cell.getNumChildren(); k++) {
      if (cell.getChild(k).getType() === DocumentApp.ElementType.LIST_ITEM) listed = true;
    }
    if (!listed) return;
    resetCell(cell);
    writeCell(cell, [roleLabelRuns(name, what)]);
    fixed.push(name);
  });
  doc.saveAndClose();
  return fixed;
}
