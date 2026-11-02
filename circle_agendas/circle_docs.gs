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
 * existing docs.
 */

// Must match util/circle_agenda_docs.py.
const AGENDA_KEYS = {
  group: 'circleAgendaGroup', parent: 'circleAgendaParent', state: 'circleAgendaState',
  layout: 'circleAgendaLayout',
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
  const circle = loadCircles().find(c => c.email === email) ||
    refreshCircles().find(c => c.email === email);
  build(file.id);
  fillAbout(file.id, file.name.replace(/ Agenda$/, ''), email, circle, props[AGENDA_KEYS.parent]);
  Drive.Files.update({properties: {
    [AGENDA_KEYS.state]: AGENDA_STATE_BUILT, [AGENDA_KEYS.layout]: String(LAYOUT_VERSION),
  }}, file.id, null, {supportsAllDrives: true});
}

// The doc's title becomes the group's name, and About this circle gets its
// email address, Drive item, and parent.
function fillAbout(docId, name, email, circle, parentName) {
  const doc = DocumentApp.openById(docId);
  const body = firstTabBody(doc);
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() === DocumentApp.ElementType.PARAGRAPH &&
        el.asParagraph().getHeading() === DocumentApp.ParagraphHeading.TITLE) {
      el.asParagraph().setText(name);
      break;
    }
  }
  const values = {
    'Zoom room': circle && circle.zoomUrl ? [['Zoom room', {[A.LINK_URL]: circle.zoomUrl}]] : [],
    'Circle email address': [[email, {[A.LINK_URL]: 'mailto:' + email}]],
    'Circle Drive Folder': circle && circle.driveItemId
      ? [['Drive folder', {[A.LINK_URL]: 'https://drive.google.com/drive/folders/' + circle.driveItemId}]]
      : [],
    'Parent Circle': parentName ? [[parentName]] : [],
  };
  const found = findRow(body, text => text === 'Circle email address');
  if (found) {
    const table = found.table;
    for (let r = 0; r < table.getNumRows(); r++) {
      const label = table.getCell(r, 0).getText().split('\n')[0].trim();
      if (values[label] && values[label].length) writeCell(table.getCell(r, 1), [values[label]]);
    }
  }
  doc.saveAndClose();
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
