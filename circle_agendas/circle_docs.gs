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
 * Circle calendar and Remind before meetings rows were added those rows.
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
// 3: About this circle's Circle calendar and Remind before meetings rows added,
// below Zoom room.
// 4: the Roles table's Name a dropdown of the circle's members, and its Term
// ends a date chip.
const LAYOUT_VERSION = 4;
// The first layout version with About this circle in ABOUT_LABELS' order.
const ABOUT_ORDER_LAYOUT = 2;
// The first layout version with the Circle calendar and Remind before meetings rows.
const NEW_ROWS_LAYOUT = 3;
const BUILD_EVERY_MINUTES = 10;

// The hour (in the script's time zone) updateRolesInCircleDocs runs, each
// day.
const ROLES_UPDATE_HOUR = 1;

// Run once, in the editor: builds new circle docs every BUILD_EVERY_MINUTES,
// and updates their Roles every day.
function installTriggers() {
  const handlers = ['buildNewCircleDocs', 'updateRolesInCircleDocs'];
  for (const t of ScriptApp.getProjectTriggers()) {
    if (handlers.includes(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  }
  ScriptApp.newTrigger('buildNewCircleDocs').timeBased().everyMinutes(BUILD_EVERY_MINUTES).create();
  ScriptApp.newTrigger('updateRolesInCircleDocs').timeBased().everyDays(1)
    .atHour(ROLES_UPDATE_HOUR).create();
  Logger.log('buildNewCircleDocs will run every ' + BUILD_EVERY_MINUTES + ' minutes, and ' +
    'updateRolesInCircleDocs every day at ' + ROLES_UPDATE_HOUR + ':00.');
}

// Run every day by the trigger installTriggers makes (or by hand): updates
// the Roles in every built doc (see updateRoles), and logs what changed.
function updateRolesInCircleDocs() {
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
// updateRoleDropdowns; not without a circle), gives its dates back their
// format (see fixDateChipFormats), and marks its past Term ends dates red
// (see markPastTermEnds). Returns lines saying what changed, or what
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
  step('fix the dates\' format', () => {
    const fixed = fixDateChipFormats(docId);
    if (fixed) lines.push('Gave ' + fixed + ' date(s) back their yyyy-mm-dd format.');
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

// Whether a date chip (its properties) is shown as yyyy-mm-dd: in that
// format, and, when the API gives its text, showing that. (The API may
// report the format a chip was made with after Docs has changed it.)
function showsIsoDate(props) {
  return props.dateFormat === 'DATE_FORMAT_ISO8601' &&
    (!props.displayText || /^\d{4}-\d{2}-\d{2}$/.test(props.displayText));
}

// Gives the dates the scripts read (the Roles table's Term ends, and each
// meeting's date, its tab's first) back their yyyy-mm-dd format, which
// Docs changes (to mm/dd/yyyy, say) when someone picks a new date. The
// Docs API can't change a chip's format, so each is replaced by a new chip
// of the same date (losing its color, which markPastTermEnds gives back).
// Returns how many.
function fixDateChipFormats(docId) {
  const flatten = tabs => tabs.flatMap(tab => [tab, ...flatten(tab.childTabs || [])]);
  const tabs = flatten(Docs.Documents.get(docId, {includeTabsContent: true}).tabs);
  let count = 0;
  tabs.forEach((tab, i) => {
    const tabId = tab.tabProperties.tabId;
    const content = tab.documentTab.body.content;
    let chips = [];
    if (i === 0) {
      chips = termEndChips(content);
    } else if (MEETING_TAB_TITLE_RE.test(tab.tabProperties.title)) {
      walkParagraphs(content, para => {
        for (const pe of para.elements || []) if (!chips.length && pe.dateElement) chips.push(pe);
      });
    }
    const requests = [];
    chips.filter(pe => !showsIsoDate(pe.dateElement.dateElementProperties || {}))
      .sort((a, b) => b.startIndex - a.startIndex)
      .forEach(pe => {
        const date = chipDate(pe.dateElement);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return;
        requests.push(
          {deleteContentRange: {range: {startIndex: pe.startIndex, endIndex: pe.endIndex, tabId}}},
          {insertDate: {location: {index: pe.startIndex, tabId}, dateElementProperties:
            isoDateChip(date)}});
      });
    if (requests.length) {
      Docs.Documents.batchUpdate({requests}, docId);
      count += requests.length / 2;
    }
  });
  return count;
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
  const circle = circleOfDoc(file.id, email);
  let members = [];
  try {
    members = circle ? circleMemberNames(circle) : [];
  } catch (e) {
    console.error('Couldn\'t list ' + file.name + '\'s members, for its Roles: ' + e.message);
  }
  build(file.id, members);
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
