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
const LAYOUT_VERSION = 1;
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
// Zoom room, email address, Drive item, and parent and sub-circles (each
// named, and linked to its Drive folder when it's a group with one).
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
  const subRuns = [];
  (subNames || []).forEach((sub, i) => {
    if (i) subRuns.push([', ']);
    subRuns.push(linkedName(sub));
  });
  const values = {
    'Zoom room': circle && circle.zoomUrl ? [['Zoom room', {[A.LINK_URL]: circle.zoomUrl}]] : [],
    'Circle email address': email ? [[email, {[A.LINK_URL]: 'mailto:' + email}]] : [],
    'Circle Google Drive Folder': circle && circle.driveItemId
      ? [['Google Drive folder', folderLink(circle.driveItemId)]] : [],
    'Parent Circle': parentName ? [linkedName(parentName)] : [],
    'Sub-Circles': subRuns,
  };
  // Docs built before the row was renamed.
  values['Circle Drive Folder'] = values['Circle Google Drive Folder'];
  const found = findRow(body, text => text === 'Circle email address');
  if (found) {
    const table = found.table;
    for (let r = 0; r < table.getNumRows(); r++) {
      const label = table.getCell(r, 0).getText().split('\n')[0].trim();
      const cell = table.getCell(r, 1);
      const write = onlyRows ? onlyRows.includes(label)
        : values[label] && values[label].length && !(onlyEmpty && cell.getText().trim());
      if (write) writeCell(cell, [values[label] || []]);
    }
  }
  doc.saveAndClose();
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
// room, email address, Drive folder, parent, sub-circles) in every built
// circle doc, for docs built before those rows were filled in. Rows that
// already say something are left alone.
function fillAboutForCircleDocs() {
  const resp = Drive.Files.list({
    q: 'properties has { key=\'' + AGENDA_KEYS.state + '\' and value=\'' + AGENDA_STATE_BUILT +
      '\' } and trashed = false',
    corpora: 'allDrives', includeItemsFromAllDrives: true, supportsAllDrives: true,
    fields: 'files(id,name,properties)',
  });
  for (const file of resp.files || []) {
    const props = file.properties || {};
    const email = props[AGENDA_KEYS.group];
    const subNames = subNamesOf(props);
    fillAbout(file.id, null, email, circleOfDoc(file.id, email), props[AGENDA_KEYS.parent],
      subNames, true);
    logLink('Filled in ' + file.name + ':', 'https://docs.google.com/document/d/' + file.id + '/edit');
  }
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
