/**
 * Circle Structure
 *
 * The tree of circles and working groups. Its one source is the Circle
 * Structure sheet, in the Circle Structure folder at the top of the Shared
 * Drive, whose rows are the groups: the
 * Coordinating Circle (STRUCTURE_EDITORS) can change only each group's
 * Parent, a dropdown of the groups it may be under (structure_rules.gs has
 * the rules), and the script fills in everything else from the Google
 * Groups' footers. Everyone else sees the structure in the Circle Structure
 * doc, which the script rewrites whenever the structure changes, and in each
 * circle's agenda doc, whose About this circle's Parent Circle and
 * Sub-Circles rows the script writes, and puts back if they're edited.
 *
 * - setupCircleStructure (run once, in the editor) makes the sheet and the
 *   doc, shares them, and installs the triggers (see installTriggers,
 *   circle_docs.gs).
 * - syncCircleStructureGroups, hourly, brings the sheet's rows up to date
 *   with the circles and working groups (clubs and other groups have no
 *   rows): adds new groups (not yet placed, unless PLACEMENTS_FILE gives
 *   them a parent), renames, relinks, retypes, reads who holds each role
 *   from the groups' agenda docs, and removes groups that are gone.
 * - applyStructure, on each edit of the sheet (onCircleStructureEdit)
 *   and every BUILD_EVERY_MINUTES (from buildNewCircleDocs), refuses
 *   parents that break the rules (putting back the one from before, with a
 *   note saying why), updates the dropdowns and the Problems column, and,
 *   for whatever changed, rewrites the doc and the agenda docs' rows.
 * - refreshStructureRows writes one agenda doc's rows, if they don't say
 *   what they should: for the docs whose place changed, each doc edited in
 *   the last BUILD_EVERY_MINUTES (checkChangedCircleDocs, circle_docs.gs),
 *   and every doc daily.
 */

// Who can change the structure: they can edit the sheet's Parent column.
const STRUCTURE_EDITORS = 'coordinating-circle@berkeleymoshav.org';
// The community's Shared Drive (DEFAULT_DRIVE_ID in util/gdrive_config.py),
// and the folder at its top that holds the sheet and the doc: outside any
// group's folder, so its files can be deleted or moved only by the Shared
// Drive's own managers, not by a circle's members.
const SHARED_DRIVE_ID = '0AFqC2xo9aTgPUk9PVA';
const STRUCTURE_FOLDER_NAME = 'Circle Structure';
const STRUCTURE_SHEET_NAME = 'Circle Structure (editing)';
const STRUCTURE_DOC_NAME = 'Circle Structure';
const STRUCTURE_TAB_NAME = 'Groups';
// Script properties: the sheet's and doc's IDs, each group's parent as last
// applied (see resolveParents), and hashes of what was last written to the
// doc and of each group's place, to tell what's changed.
const STRUCTURE_SHEET_KEY = 'STRUCTURE_SHEET_ID';
const STRUCTURE_DOC_KEY = 'STRUCTURE_DOC_ID';
const STRUCTURE_APPLIED_KEY = 'circleStructureApplied';
const STRUCTURE_PLACES_KEY = 'circleStructurePlaces';
const STRUCTURE_DOC_HASH_KEY = 'circleStructureDocHash';
// Parents for groups, by Gather group ID ({"<id>": "<parent's id>"}), in
// admin@'s My Drive, written by the Python scripts (seed_circle_structure.py,
// to bring over the old wiki page's parents, and add_new_group.py, for a new
// group; see util/circle_structure_placements.py, which must match). Each is
// given to its group when the group's Parent is empty, and then removed.
const PLACEMENTS_FILE = 'Circle structure placements.json';
// The Shared Drive's folder of clubs' folders.
const CLUBS_FOLDER_NAME = 'Clubs';
const SYNC_EVERY_HOURS = 1;

// The roles in an agenda doc's Roles table, each of which has a column
// showing who holds it (the names in ROLE_LABELS, build_template.gs).
const STRUCTURE_ROLES = ['Leader', 'Secretary', 'Facilitator', 'Feedback Link'];
// The sheet's columns, in order: [key, heading]. Email list shows the
// group's shortest address (see listAddress); Address, its Google Group's
// primary address, which the script matches on. Gather ID, the Gather
// group's, is the row's key. The last two are hidden. Rows are read by
// heading (see readGroups), so a sheet laid out before the columns last
// changed is still read, and then laid out afresh (see layOutSheet).
const STRUCTURE_LAYOUT = [
  ['group', 'Group'], ['parent', 'Parent'], ['email', 'Email list'], ['gather', 'Gather group'],
  ['guests', 'Guests'], ['meetings', 'Meeting times'], ['folder', 'Drive folder'], ['agenda', 'Agenda doc'],
  ...STRUCTURE_ROLES.map((role, i) => ['role' + i, role]),
  ['type', 'Type'], ['problems', 'Problems'], ['id', 'Gather ID'], ['address', 'Address'],
];
// Each column's number (1-based), by key.
const SCOL = {};
STRUCTURE_LAYOUT.forEach(([key], i) => { SCOL[key] = i + 1; });
const STRUCTURE_HEADINGS = STRUCTURE_LAYOUT.map(([, heading]) => heading);
const STRUCTURE_COLUMNS = STRUCTURE_HEADINGS.length;
// The columns only as wide as their headings, their text cut off at their
// edge (an Email list address is a link); the others fit their text and
// heading (see fitColumns).
const HEADING_WIDE_COLUMNS = ['email', 'guests'];
// The community's calendar of every group's meetings (ALL_EVENTS_CALENDAR_ID
// in util/meeting_rooms.py, and in group_meetings), which the Meeting times
// cells link: each line opens the next meeting it's about there, with its
// details (see eventUrl).
const ALL_EVENTS_CALENDAR_ID =
  'c_301c808f640b3a04f5344b1bbf266955d51fecaabd7835936b7591f9cff09222@group.calendar.google.com';
// The sheet's first row, above the headings (HEAD_ROW): a line linking the
// doc, and saying how the structure is changed. The rows start at
// FIRST_ROW.
const STRUCTURE_INTRO_LINK = 'View the Circle Structure';
const STRUCTURE_INTRO = STRUCTURE_INTRO_LINK +
  '. Coordinating Circle: change a group\u2019s Parent here to move it within the structure.';
const HEAD_ROW = 2;
const FIRST_ROW = 3;
const PARENT_HELP = 'The circle it\'s under (for a working group, the circle or working group).';

// ── Setting up ───────────────────────────────────────────────────────────────

// Run once, in the editor: makes the STRUCTURE_FOLDER_NAME folder at the top
// of the Shared Drive, and in it the sheet (which STRUCTURE_EDITORS can edit,
// but only its Parent column) and the doc (anyone with the link can view
// it); fills the sheet in, installs the triggers, and logs both links. The
// folder's other access is the Shared Drive's: its members can see both.
// Safe to run again: it keeps the folder, sheet, and doc it made (moving
// either file into the folder if it's elsewhere), and protects and shares
// them again.
function setupCircleStructure() {
  const props = PropertiesService.getScriptProperties();
  const folderId = structureFolderId();
  const sheetId = structureFile(STRUCTURE_SHEET_KEY, STRUCTURE_SHEET_NAME,
    'application/vnd.google-apps.spreadsheet', folderId);
  const docId = structureFile(STRUCTURE_DOC_KEY, STRUCTURE_DOC_NAME,
    'application/vnd.google-apps.document', folderId);

  const sheet = structureSheet();
  sheet.setFrozenRows(HEAD_ROW);
  sheet.setFrozenColumns(1);
  // (syncGroups, below, lays out the columns, if they aren't already.)
  protectStructureSheet(sheet);

  // Editors of a file in a shared drive (unlike its managers) can't delete
  // or move it.
  Drive.Permissions.create({role: 'writer', type: 'group', emailAddress: STRUCTURE_EDITORS},
    sheetId, {sendNotificationEmail: false, supportsAllDrives: true});
  try {
    DriveApp.getFileById(docId).setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  } catch (e) {
    console.warn('Couldn\'t let anyone with the link view the doc (' + e.message + '); letting ' +
      'anyone in ' + DOMAIN + ' with the link view it instead.');
    DriveApp.getFileById(docId).setSharing(DriveApp.Access.DOMAIN_WITH_LINK, DriveApp.Permission.VIEW);
  }

  installTriggers();
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    syncGroups();
    applyStructure();
  } finally {
    lock.releaseLock();
  }
  logLink('The folder, at the top of the Shared Drive:',
    'https://drive.google.com/drive/folders/' + folderId);
  logLink('The sheet, which ' + STRUCTURE_EDITORS + ' can edit (send them the link):',
    'https://docs.google.com/spreadsheets/d/' + sheetId + '/edit');
  logLink('The doc, which anyone with the link can view:', structureDocUrl());
}

// Protects the sheet: only the script's account (admin@) can edit it, but
// for its Parent column. (A file in a shared drive has no owner, whom
// protection always lets edit, so it names admin@ itself.)
function protectStructureSheet(sheet) {
  for (const p of sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET)) p.remove();
  const protection = sheet.protect().setDescription('Filled in by the Circle Structure script');
  protection.setUnprotectedRanges([sheet.getRange(FIRST_ROW, SCOL.parent,
    sheet.getMaxRows() - HEAD_ROW, 1)]);
  const me = Session.getEffectiveUser().getEmail();
  protection.addEditor(me);
  protection.removeEditors(protection.getEditors().map(u => u.getEmail())
    .filter(e => e.toLowerCase() !== me.toLowerCase()));
  if (protection.canDomainEdit()) protection.setDomainEdit(false);
}

// Whether the sheet has STRUCTURE_INTRO in its first row and its heading
// row (HEAD_ROW) is STRUCTURE_HEADINGS, and nothing more.
function sheetLaidOut(sheet) {
  const width = Math.max(sheet.getLastColumn(), STRUCTURE_COLUMNS);
  const rows = sheet.getRange(1, 1, HEAD_ROW, width).getValues()
    .map(row => row.map(v => String(v).trim()));
  return rows[0][0] === STRUCTURE_INTRO && rows[0].slice(1).every(v => !v) &&
    rows[HEAD_ROW - 1].every((v, i) => v === (STRUCTURE_HEADINGS[i] || ''));
}

// Writes STRUCTURE_INTRO in the first row's first cell, its start linking
// the doc.
function writeStructureIntro(sheet) {
  // Plain, so it keeps no look (bold, a link's blue) from what was there.
  sheet.getRange(1, 1, 1, sheet.getMaxColumns()).clearFormat();
  const url = structureDocUrl();
  const intro = SpreadsheetApp.newRichTextValue().setText(STRUCTURE_INTRO);
  if (url) intro.setLinkUrl(0, STRUCTURE_INTRO_LINK.length, url);
  sheet.getRange(1, 1).setRichTextValue(intro.build());
}

// Lays the sheet out in STRUCTURE_LAYOUT, emptying it first: its rows have
// to be read (readGroups) before, and written after. Moves the Parent
// column's heading note and its protection's gap with it.
function layOutSheet(sheet) {
  const rows = sheet.getMaxRows();
  const cols = sheet.getMaxColumns();
  if (cols < STRUCTURE_COLUMNS) sheet.insertColumnsAfter(cols, STRUCTURE_COLUMNS - cols);
  const all = sheet.getRange(1, 1, rows, sheet.getMaxColumns());
  all.clearContent().clearNote().setDataValidation(null);
  sheet.showColumns(1, sheet.getMaxColumns());
  writeStructureIntro(sheet);
  sheet.getRange(HEAD_ROW, 1, 1, STRUCTURE_COLUMNS).setValues([STRUCTURE_HEADINGS]);
  sheet.getRange(HEAD_ROW, SCOL.parent).setNote(PARENT_HELP);
  sheet.setFrozenRows(HEAD_ROW);
  sheet.hideColumns(SCOL.id, 2);
  protectStructureSheet(sheet);
  console.log('Circle Structure: laid the sheet\'s columns out afresh.');
}

// The STRUCTURE_FOLDER_NAME folder at the top of the Shared Drive, made if
// there's none.
function structureFolderId() {
  const resp = Drive.Files.list({
    q: '\'' + SHARED_DRIVE_ID + '\' in parents and name = \'' + STRUCTURE_FOLDER_NAME +
      '\' and mimeType = \'application/vnd.google-apps.folder\' and trashed = false',
    corpora: 'drive', driveId: SHARED_DRIVE_ID, includeItemsFromAllDrives: true,
    supportsAllDrives: true, fields: 'files(id)',
  });
  if ((resp.files || []).length) return resp.files[0].id;
  return Drive.Files.create({name: STRUCTURE_FOLDER_NAME, mimeType: 'application/vnd.google-apps.folder',
    parents: [SHARED_DRIVE_ID]}, null, {supportsAllDrives: true, fields: 'id'}).id;
}

// The ID (kept in the script property key) of the sheet or doc, made in the
// folder if there's none (or it's in the trash), or moved into it if it's
// elsewhere (such as admin@'s My Drive, where an earlier version made it).
function structureFile(key, name, mimeType, folderId) {
  const props = PropertiesService.getScriptProperties();
  const id = liveFileId(props.getProperty(key));
  if (!id) {
    const made = Drive.Files.create({name, mimeType, parents: [folderId]}, null,
      {supportsAllDrives: true, fields: 'id'}).id;
    props.setProperty(key, made);
    if (key === STRUCTURE_DOC_KEY) props.deleteProperty(STRUCTURE_DOC_HASH_KEY);
    return made;
  }
  const parents = Drive.Files.get(id, {fields: 'parents', supportsAllDrives: true}).parents || [];
  if (!parents.includes(folderId)) {
    Drive.Files.update({}, id, null, {addParents: folderId, removeParents: parents.join(','),
      supportsAllDrives: true});
    console.log('Moved ' + name + ' into the ' + STRUCTURE_FOLDER_NAME + ' folder.');
  }
  return id;
}

// The file's ID, if it's there and not in the trash; else null.
function liveFileId(id) {
  if (!id) return null;
  try {
    return DriveApp.getFileById(id).isTrashed() ? null : id;
  } catch (e) {
    return null;
  }
}

// The sheet's Groups tab, or null if setupCircleStructure hasn't been run.
function structureSheet() {
  const id = PropertiesService.getScriptProperties().getProperty(STRUCTURE_SHEET_KEY);
  if (!id) return null;
  const ss = SpreadsheetApp.openById(id);
  let sheet = ss.getSheetByName(STRUCTURE_TAB_NAME);
  if (!sheet) sheet = ss.getSheets()[0].setName(STRUCTURE_TAB_NAME);
  return sheet;
}

// The sheet, where a group's parent is set: what an agenda doc's "(Set in
// the Circle Structure)" links.
function structureSheetUrl() {
  const id = PropertiesService.getScriptProperties().getProperty(STRUCTURE_SHEET_KEY);
  return id ? 'https://docs.google.com/spreadsheets/d/' + id + '/edit' : null;
}

function structureDocUrl() {
  const id = PropertiesService.getScriptProperties().getProperty(STRUCTURE_DOC_KEY);
  return id ? 'https://docs.google.com/document/d/' + id + '/edit' : null;
}

// ── Entry points ─────────────────────────────────────────────────────────────

// Run hourly by the trigger installTriggers makes (or by hand).
function syncCircleStructureGroups() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(60000)) return;
  try {
    if (!structureSheet()) return;
    syncGroups();
    applyStructure();
  } finally {
    lock.releaseLock();
  }
}

// Run on each edit of the sheet, by the trigger installTriggers makes (as
// admin@, whoever made the edit).
function onCircleStructureEdit() {
  const lock = LockService.getScriptLock();
  lock.waitLock(60000);
  try {
    applyStructure();
  } finally {
    lock.releaseLock();
  }
}

// Run from the editor: does at once what the timers do, bringing the
// sheet's rows up to date with the groups (as the hourly
// syncCircleStructureGroups does: names, links, roles, guests, meeting
// times, column widths), and rewriting the doc and every agenda doc's rows
// that don't say what they should.
function refreshCircleStructureEverywhere() {
  const lock = LockService.getScriptLock();
  lock.waitLock(60000);
  try {
    if (!structureSheet()) return;
    syncGroups();
    PropertiesService.getScriptProperties().deleteProperty(STRUCTURE_DOC_HASH_KEY);
    applyStructure();
    refreshStructureInAllCircleDocs();
  } finally {
    lock.releaseLock();
  }
}

// ── The sheet's rows ─────────────────────────────────────────────────────────

// The sheet's rows: [{row, id, name, type, parent, problems, email (the
// Google Group's primary address), listAddress (the one shown), gatherUrl,
// folderUrl, agendaUrl, roles (who holds each of STRUCTURE_ROLES, in
// order)}], row the sheet's row number. Each column is found by its heading
// (see columnsByHeading), so this reads a sheet laid out before too.
function readGroups(sheet) {
  const width = sheet.getLastColumn();
  const last = sheet.getLastRow();
  if (last < 2 || width < 1) return [];
  // The heading row: HEAD_ROW, or the first row in a sheet laid out before
  // the intro was added.
  const top = sheet.getRange(1, 1, Math.min(HEAD_ROW, last), width).getValues();
  const headRow = top.findIndex(row => columnsByHeading(row, ['Gather ID'])['Gather ID']) + 1;
  const n = last - headRow;
  if (headRow < 1 || n < 1) return [];
  const col = columnsByHeading(top[headRow - 1], STRUCTURE_HEADINGS);
  const range = sheet.getRange(headRow + 1, 1, n, width);
  const values = range.getValues();
  const rich = range.getRichTextValues();
  const at = (key, heading) => col[heading || STRUCTURE_HEADINGS[SCOL[key] - 1]];
  const value = (r, key, heading) => {
    const c = at(key, heading);
    return c ? String(values[r][c - 1] || '').trim() : '';
  };
  const link = (r, key) => {
    const c = at(key);
    return (c && rich[r][c - 1] && rich[r][c - 1].getLinkUrl()) || '';
  };
  const groups = [];
  values.forEach((v, r) => {
    const id = value(r, 'id');
    if (!id) return;
    groups.push({
      row: r + headRow + 1, id, name: value(r, 'group'), type: value(r, 'type'),
      parent: value(r, 'parent'), problems: value(r, 'problems'),
      email: (value(r, 'address') || value(r, 'email')).toLowerCase(),
      listAddress: value(r, 'email').toLowerCase(),
      gatherUrl: link(r, 'gather'), folderUrl: link(r, 'folder'), agendaUrl: link(r, 'agenda'),
      roles: STRUCTURE_ROLES.map(role => value(r, null, role)),
      guests: value(r, 'guests').split(/\s+/).filter(Boolean),
      meetingsRich: at('meetings') ? rich[r][at('meetings') - 1] : null,
    });
  });
  return groups;
}

// Brings the rows up to date with the Google Groups whose footers link a
// Gather group and a Drive item, and are circles or working groups (see
// guessGroupType): each group's name (renaming it in the Parent cells that
// name it), type, email address, links, and role holders (from its agenda
// doc's Roles table). A new group gets a row of its own, with an empty
// Parent; a group that's gone (only if every footer could be read), or
// isn't a circle or working group any more, loses its row. The rows are
// kept in order by name. A sheet laid out before the columns last changed
// is laid out afresh (see layOutSheet).
function syncGroups() {
  const sheet = structureSheet();
  const {groups: footers, complete} = readGroupFooters();
  const docs = agendaDocsByGroup(footers);
  const existing = readGroups(sheet);
  const byId = {};
  for (const g of existing) byId[g.id] = g;
  const seen = {};
  const renames = [];
  const rows = [];
  for (const f of footers) {
    if (!f.gatherId || !f.driveItemId || seen[f.gatherId]) continue;
    seen[f.gatherId] = true;
    const old = byId[f.gatherId];
    const item = driveItemInfo(f.driveItemId);
    const {type, reason} = guessGroupType(f.name, item);
    if (type === TYPE_OUT) {
      // A Drive item that couldn't be read this time keeps its row as it was.
      if (!item && old) rows.push(Object.assign({}, old, {isNew: false}));
      else if (old) console.log('Circle Structure: removed ' + old.name + ', as ' + reason + '.');
      // A circle whose folder is misnamed would be missed without a word.
      else if (item && !item.isSharedDrive && !/\bclub\b/i.test(reason)) {
        console.warn('Circle Structure: left out ' + f.name + ', as ' + reason + '.');
      }
      continue;
    }
    if (old && old.name !== f.name) renames.push([old.name, f.name]);
    const doc = docs[f.gatherId];
    rows.push({
      id: f.gatherId, name: f.name, type, problems: old ? old.problems : '',
      parent: old ? old.parent : '', isNew: !old, email: f.email, listAddress: shortestAddress(f.email, f.aliases, DOMAIN),
      gatherUrl: f.gatherUrl,
      folderUrl: 'https://drive.google.com/drive/folders/' + f.driveItemId,
      agendaUrl: doc ? 'https://docs.google.com/document/d/' + doc + '/edit' : '',
      roles: doc ? roleHolders(doc, old) : STRUCTURE_ROLES.map(() => ''),
      guests: old ? old.guests : [],
      ...meetingTimesCell(f.calendarId, old),
    });
  }
  for (const g of existing) {
    if (seen[g.id]) continue;
    if (complete) {
      console.log('Circle Structure: removed ' + g.name + ', whose group is gone.');
    } else {
      rows.push(Object.assign({}, g, {isNew: false}));
    }
  }
  for (const r of rows) {
    for (const [from, to] of renames) if (sameName(r.parent, from)) r.parent = to;
  }
  rows.sort((a, b) => a.name.localeCompare(b.name));
  findGuests(rows);

  // A sheet laid out before is emptied and laid out afresh, and every cell
  // written.
  let old = existing.slice();
  if (!sheetLaidOut(sheet)) {
    layOutSheet(sheet);
    old = [];
  }

  // Rewrite the rows, all but the Parent cells that haven't changed (so an
  // edit made meanwhile isn't lost).
  const n = rows.length;
  const extra = sheet.getLastRow() - HEAD_ROW - n;
  if (extra > 0) {
    sheet.getRange(n + FIRST_ROW, 1, extra, STRUCTURE_COLUMNS).clearContent().clearNote()
      .setDataValidation(null);
  }
  if (!n) return;
  // Plain formatting, so no cell keeps a look (a link's blue, say) from
  // what was there before.
  sheet.getRange(FIRST_ROW, 1, n, STRUCTURE_COLUMNS).clearFormat();
  // The headings too, but bold (fitColumns, below, cuts off the narrow
  // ones' text again).
  sheet.getRange(HEAD_ROW, 1, 1, sheet.getMaxColumns()).clearFormat();
  sheet.getRange(HEAD_ROW, 1, 1, STRUCTURE_COLUMNS).setFontWeight('bold');
  const text = (t, url) => SpreadsheetApp.newRichTextValue().setText(t || '').setLinkUrl(url || null).build();
  sheet.getRange(FIRST_ROW, SCOL.group, n, 1).setValues(rows.map(r => [r.name]));
  sheet.getRange(FIRST_ROW, SCOL.email, n, 1).setRichTextValues(rows.map(r => [
    text(r.listAddress, r.listAddress ? 'mailto:' + r.listAddress : null)]));
  sheet.getRange(FIRST_ROW, SCOL.gather, n, 1).setRichTextValues(rows.map(r => [
    text(r.gatherUrl ? 'Members' : '', r.gatherUrl)]));
  sheet.getRange(FIRST_ROW, SCOL.guests, n, 1).setValues(rows.map(r => [(r.guests || []).join('\n')]));
  sheet.getRange(FIRST_ROW, SCOL.meetings, n, 1).setRichTextValues(rows.map(r => [
    r.meetingsRich || meetingTimesRich(r.meetingLines || [])]));
  sheet.getRange(FIRST_ROW, SCOL.folder, n, 2).setRichTextValues(rows.map(r => [
    text('Documents', r.folderUrl), text(r.agendaUrl ? 'Agenda' : '', r.agendaUrl)]));
  sheet.getRange(FIRST_ROW, SCOL.role0, n, STRUCTURE_ROLES.length).setNumberFormat('@')
    .setValues(rows.map(r => STRUCTURE_ROLES.map((_, i) => (r.roles || [])[i] || '')));
  sheet.getRange(FIRST_ROW, SCOL.type, n, 2).setValues(rows.map(r => [r.type, r.problems]));
  sheet.getRange(FIRST_ROW, SCOL.id, n, 2).setNumberFormat('@').setValues(rows.map(r => [r.id, r.email]));
  rows.forEach((r, i) => {
    const before = old[i];
    if (!before || before.id !== r.id || before.parent !== r.parent) {
      const cell = sheet.getRange(i + FIRST_ROW, SCOL.parent);
      cell.setDataValidation(null).setValue(r.parent);
    }
  });
  fitColumns(sheet);
  const added = rows.filter(r => r.isNew).map(r => r.name);
  if (added.length) console.log('Circle Structure: added ' + added.join(', ') + '.');
}

// How many weeks ahead the Meeting times column looks for meetings: far
// enough for a monthly one.
const MEETING_TIMES_WEEKS = 8;

// A row's Meeting times cell, from the group's calendar (calendarId, from
// its footer; or none): {meetingLines} (see meetingTimesLines; a "None
// scheduled" line, unlinked, if there's nothing to come), or, if the
// calendar can't be read, {meetingsRich}, what the row (old, or none) said
// before.
function meetingTimesCell(calendarId, old) {
  if (!calendarId) return {meetingLines: []};
  try {
    const lines = meetingTimesOf(calendarId);
    return {meetingLines: lines.length ? lines : [{text: 'None scheduled', eventId: null}]};
  } catch (e) {
    console.warn('Couldn\'t read calendar ' + calendarId + ', for its meeting times: ' + e.message);
    return old && old.meetingsRich ? {meetingsRich: old.meetingsRich} : {meetingLines: []};
  }
}

// The Meeting times cell: each line linking the next meeting it's about,
// as the all-events calendar shows it (a line without one, unlinked).
function meetingTimesRich(lines) {
  const builder = SpreadsheetApp.newRichTextValue().setText(lines.map(l => l.text).join('\n'));
  let at = 0;
  for (const l of lines) {
    if (l.eventId && l.text) {
      builder.setLinkUrl(at, at + l.text.length, eventUrl(l.eventId, ALL_EVENTS_CALENDAR_ID));
    }
    at += l.text.length + 1;
  }
  return builder.build();
}

// meetingTimesLines for a calendar's timed meetings in the next
// MEETING_TIMES_WEEKS: a repeating one described by its series' rule and
// usual times (so a meeting moved for one week changes nothing), each
// other one by its own.
function meetingTimesOf(calendarId) {
  const now = new Date();
  const resp = Calendar.Events.list(calendarId, {
    timeMin: now.toISOString(),
    timeMax: new Date(now.getTime() + MEETING_TIMES_WEEKS * 7 * 86400000).toISOString(),
    singleEvents: true, orderBy: 'startTime', maxResults: 250,
  });
  const nextOf = {};
  const oneOffs = [];
  for (const e of resp.items || []) {
    if (e.status === 'cancelled' || !e.start || !e.start.dateTime) continue;
    if (!e.recurringEventId) oneOffs.push(localTimes(e));
    else if (!nextOf[e.recurringEventId]) nextOf[e.recurringEventId] = e;
  }
  const series = Object.keys(nextOf).map(id => {
    const next = localTimes(nextOf[id]);
    let master = null;
    try {
      master = Calendar.Events.get(calendarId, id);
    } catch (e) {
      // Described by its next meeting alone.
    }
    const rule = master && (master.recurrence || []).find(r => /^RRULE:/i.test(r));
    const usual = rule && master.start && master.start.dateTime ? localTimes(master) : next;
    return {rule: rule || null, weekday: usual.weekday, start: usual.start, end: usual.end, next};
  });
  return meetingTimesLines(series, oneOffs);
}

// An event's {id, date, weekday, start, end}, as meetingTimesLines takes
// them, in TIME_ZONE.
function localTimes(event) {
  const at = (when, pattern) => Utilities.formatDate(new Date(when.dateTime), TIME_ZONE, pattern);
  const minutes = when => Number(at(when, 'H')) * 60 + Number(at(when, 'm'));
  return {id: event.id, date: at(event.start, 'yyyy-MM-dd'), weekday: Number(at(event.start, 'u')) % 7,
    start: minutes(event.start), end: minutes(event.end)};
}

// Gives each row (but the root group's) its guests (see guestsOf): the
// members of its Google Group who aren't in the root group's (Full
// Community's), whose members are everyone in the community. So the root
// group itself shows none. A group whose members can't be read keeps the
// guests it had; if the root group's can't be, every group does.
function findGuests(rows) {
  const root = rows.find(isRootGroup);
  let everyone = null;
  try {
    everyone = root ? groupMemberEmails(root.email) : null;
  } catch (e) {
    console.warn('Couldn\'t read ' + root.name + '\'s members, to find guests: ' + e.message);
  }
  const me = Session.getEffectiveUser().getEmail();
  for (const r of rows) {
    if (!everyone || r === root) {
      r.guests = everyone ? [] : r.guests || [];
      continue;
    }
    try {
      r.guests = guestsOf(groupMemberEmails(r.email), everyone, [me]);
    } catch (e) {
      console.warn('Couldn\'t read ' + r.name + '\'s members, to find guests: ' + e.message);
      r.guests = r.guests || [];
    }
  }
}

// Who holds each of STRUCTURE_ROLES, in order, from the Roles table of an
// agenda doc (in its first tab; '' for a role no one holds, or a doc not
// built yet). If the doc can't be read, what the group's row (old, or
// none) said before.
function roleHolders(docId, old) {
  try {
    const content = (Docs.Documents.get(docId).body || {}).content || [];
    return STRUCTURE_ROLES.map(role => roleHolderIn(content, role));
  } catch (e) {
    console.warn('Couldn\'t read the roles in agenda doc ' + docId + ': ' + e.message);
    return old && old.roles ? old.roles : STRUCTURE_ROLES.map(() => '');
  }
}

// Fits each shown column's width to its text and its heading, but for
// HEADING_WIDE_COLUMNS, which are only as wide as their headings, cutting
// their text off. (Sheets has no lasting "fit to data" setting: its own
// resizing, which this uses, sets a width once, and it leaves bold
// headings cut off, so each column is made at least as wide as
// headingWidth says its heading needs.)
function fitColumns(sheet) {
  const narrow = HEADING_WIDE_COLUMNS.map(k => SCOL[k]);
  // The intro, which overflows into the cells beside it, is taken out
  // while resizing, so the first column isn't made as wide as it.
  sheet.getRange(1, 1).clearContent();
  for (let c = 1; c < SCOL.id; c++) {
    if (!narrow.includes(c)) sheet.autoResizeColumn(c);
  }
  SpreadsheetApp.flush();
  for (let c = 1; c < SCOL.id; c++) {
    const heading = headingWidth(STRUCTURE_HEADINGS[c - 1]);
    if (narrow.includes(c)) {
      sheet.setColumnWidth(c, heading);
      sheet.getRange(HEAD_ROW, c, sheet.getMaxRows() - HEAD_ROW + 1, 1)
        .setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP);
    } else if (sheet.getColumnWidth(c) < heading) {
      sheet.setColumnWidth(c, heading);
    }
  }
  writeStructureIntro(sheet);
}

// What guessGroupType needs to know of a Drive item: {name, isSharedDrive,
// inClubs}, or null if it can't be read.
function driveItemInfo(id) {
  try {
    const file = Drive.Files.get(id, {fields: 'id,name,parents,driveId', supportsAllDrives: true});
    if (file.driveId && file.driveId === id) {
      let name = file.name;
      try {
        name = Drive.Drives.get(id).name;
      } catch (e) {
        // The root folder's name will do.
      }
      return {name, isSharedDrive: true, inClubs: false};
    }
    const parent = (file.parents || [])[0];
    let inClubs = false;
    if (parent && parent !== file.driveId) {
      inClubs = Drive.Files.get(parent, {fields: 'name', supportsAllDrives: true}).name ===
        CLUBS_FOLDER_NAME;
    }
    return {name: file.name, isSharedDrive: false, inClubs};
  } catch (e) {
    console.warn('Couldn\'t read Drive item ' + id + ': ' + e.message);
    return null;
  }
}

// The circle agenda docs (built, or waiting to be), by their groups' Gather
// group IDs: {id: doc ID}. A doc's group is the one with its labelled
// address, or else the one whose Drive folder holds it most closely (see
// nearestGroupFolder).
function agendaDocsByGroup(footers) {
  const byEmail = {};
  for (const f of footers) {
    if (!f.gatherId || !f.driveItemId) continue;
    for (const a of [f.email, ...(f.aliases || [])]) byEmail[a] = f;
  }
  const folders = Object.values(byEmail).map(f => f.driveItemId);
  const docs = {};
  for (const file of agendaDocFiles()) {
    const email = ((file.properties || {})[AGENDA_KEYS.group] || '').toLowerCase();
    let f = byEmail[email];
    if (!f) {
      const folder = nearestGroupFolder(file.id, folders);
      f = folder ? Object.values(byEmail).find(x => x.driveItemId === folder) : null;
    }
    if (f && !docs[f.gatherId]) docs[f.gatherId] = file.id;
  }
  return docs;
}

// Of the folders (groups' Drive items), the one holding a doc most closely,
// or null. The Shared Drive itself (which an everybody group, such as Full
// Community, is linked to) never counts, as it holds every group's folder.
function nearestGroupFolder(docId, folders) {
  for (const id of driveAncestors(docId)) {
    if (id === SHARED_DRIVE_ID) continue;
    if (folders.includes(id)) return id;
  }
  return null;
}

function agendaDocFiles() {
  const state = s => 'properties has { key=\'' + AGENDA_KEYS.state + '\' and value=\'' + s + '\' }';
  const files = [];
  let pageToken;
  do {
    const resp = Drive.Files.list({
      q: '(' + state(AGENDA_STATE_BUILT) + ' or ' + state(AGENDA_STATE_NEW) + ') and trashed = false',
      corpora: 'allDrives', includeItemsFromAllDrives: true, supportsAllDrives: true,
      pageSize: 1000, pageToken, fields: 'nextPageToken,files(id,name,parents,properties)',
    });
    files.push(...(resp.files || []));
    pageToken = resp.nextPageToken;
  } while (pageToken);
  return files;
}

// ── Applying the structure ───────────────────────────────────────────────────

// See the top of this file. Callers hold the script lock.
function applyStructure() {
  const sheet = structureSheet();
  if (!sheet) return;
  // The columns below are where they are now; a sheet laid out before
  // waits for syncGroups to lay it out afresh.
  if (!sheetLaidOut(sheet)) syncGroups();
  const props = PropertiesService.getScriptProperties();
  const lastApplied = JSON.parse(props.getProperty(STRUCTURE_APPLIED_KEY) || '{}');
  const groups = readGroups(sheet);
  applyPlacements(sheet, groups);

  const {parentOf, refused} = resolveParents(groups, lastApplied);
  const byId = {};
  for (const g of groups) byId[g.id] = g;
  const when = Utilities.formatDate(new Date(), TIME_ZONE, 'MMM d, h:mm a');
  for (const r of refused) {
    const g = byId[r.id];
    const kept = parentCellText(r.kept, byId);
    sheet.getRange(g.row, SCOL.parent).setValue(kept).setNote(when + ': couldn\'t put it under "' +
      r.tried + '": ' + r.reason + '. ' + (kept ? 'It\'s still under ' + kept + '.' : 'It isn\'t placed.'));
    g.parent = kept;
  }
  const refusedIds = refused.map(r => r.id);
  for (const g of groups) {
    const want = inStructure(g) ? parentCellText(parentOf[g.id], byId) : '';
    const cell = sheet.getRange(g.row, SCOL.parent);
    if (!refusedIds.includes(g.id) && want !== g.parent && (isRootGroup(g) || !inStructure(g))) {
      cell.setValue(want);
      g.parent = want;
    }
    if (inStructure(g) && !refusedIds.includes(g.id) && (parentOf[g.id] || '') !== (lastApplied[g.id] || '')) {
      cell.clearNote();
    }
  }

  const tree = buildTree(groups, parentOf);
  const problems = groups.map(g => [inStructure(g) ? groupProblem(g, '', parentOf, tree, byId) : g.problems]);
  if (groups.length) {
    // The rows readGroups skipped (no ID) would shift these; there are none,
    // as only the script writes IDs.
    const rows = groups[groups.length - 1].row - HEAD_ROW;
    if (rows === groups.length) {
      const current = groups.map(g => [g.problems]);
      if (JSON.stringify(current) !== JSON.stringify(problems)) {
        sheet.getRange(FIRST_ROW, SCOL.problems, rows, 1).setValues(problems);
        fitColumns(sheet);
      }
      sheet.getRange(FIRST_ROW, SCOL.parent, rows, 1).setDataValidations(groups.map(g => {
        const choices = parentChoices(g, groups, tree.children);
        return [choices.length ? SpreadsheetApp.newDataValidation()
          .requireValueInList(choices, true).setAllowInvalid(false).setHelpText(PARENT_HELP).build()
          : null];
      }));
    }
  }
  props.setProperty(STRUCTURE_APPLIED_KEY, JSON.stringify(parentOf));

  const ctx = structureContext(groups, parentOf, tree);
  writeStructureDocIfChanged(ctx);
  // Each group's place, as its agenda doc shows it but for meetings and
  // roles; the docs of the groups whose place changed are rewritten.
  const oldPlaces = JSON.parse(props.getProperty(STRUCTURE_PLACES_KEY) || '{}');
  const places = {};
  for (const g of groups) places[g.id] = shortHash(JSON.stringify(placeOf(ctx, g)));
  for (const g of groups) {
    if (places[g.id] === oldPlaces[g.id] || !g.agendaUrl) continue;
    const docId = docIdOf(g.agendaUrl);
    try {
      if (refreshStructureRows(docId, ctx, g)) console.log('Updated the place in ' + g.name + '\'s agenda doc.');
    } catch (e) {
      console.error('Couldn\'t update the place in ' + g.name + '\'s agenda doc: ' + e.message);
      delete places[g.id];
    }
  }
  props.setProperty(STRUCTURE_PLACES_KEY, JSON.stringify(places));
}

// Gives each group whose Parent is empty the parent PLACEMENTS_FILE has for
// it (by Gather group ID), and removes from the file each placement given,
// or whose group already has a parent; the others wait for their groups'
// rows.
function applyPlacements(sheet, groups) {
  const files = DriveApp.getRootFolder().getFilesByName(PLACEMENTS_FILE);
  if (!files.hasNext()) return;
  const file = files.next();
  let placements;
  try {
    placements = JSON.parse(file.getBlob().getDataAsString('UTF-8') || '{}');
  } catch (e) {
    console.error('Couldn\'t read ' + PLACEMENTS_FILE + ': ' + e.message);
    return;
  }
  const byId = {};
  for (const g of groups) byId[g.id] = g;
  let changed = false;
  for (const id of Object.keys(placements)) {
    const g = byId[id];
    const parent = byId[placements[id]];
    if (!g) continue;
    if (!g.parent && parent && !isRootGroup(g)) {
      sheet.getRange(g.row, SCOL.parent).setValue(parent.name);
      g.parent = parent.name;
    }
    delete placements[id];
    changed = true;
  }
  if (changed) file.setContent(JSON.stringify(placements, null, 1));
}

// What the doc and the agenda docs' rows are written from.
function structureContext(groups, parentOf, tree) {
  const byId = {};
  for (const g of groups) byId[g.id] = g;
  return {groups, byId, parentOf, tree, structureUrl: structureSheetUrl(),
    structureDocUrl: structureDocUrl()};
}

// The sheet's groups and parents, for refreshing agenda docs outside
// applyStructure; null if there's no sheet yet.
function loadStructureContext() {
  const sheet = structureSheet();
  if (!sheet) return null;
  const groups = readGroups(sheet);
  const parentOf = JSON.parse(PropertiesService.getScriptProperties()
    .getProperty(STRUCTURE_APPLIED_KEY) || '{}');
  return structureContext(groups, parentOf, buildTree(groups, parentOf));
}

function shortHash(text) {
  return Utilities.base64Encode(Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, text,
    Utilities.Charset.UTF_8)).slice(0, 12);
}

function docIdOf(url) {
  const m = /\/d\/([\w-]+)/.exec(url || '');
  return m ? m[1] : null;
}

// Where a group's name links: its agenda doc, or else its Drive folder.
function groupUrl(g) {
  return g.agendaUrl || g.folderUrl || null;
}

// ── The doc ──────────────────────────────────────────────────────────────────

// Rewrites the doc if what it shows has changed since it was last written
// (or it's never been).
function writeStructureDocIfChanged(ctx) {
  const props = PropertiesService.getScriptProperties();
  const docId = props.getProperty(STRUCTURE_DOC_KEY);
  if (!docId) return;
  const {placed, unplaced} = outline(ctx.tree);
  const entry = ({id, depth}) => ({depth, runs: structureDocRuns(ctx.byId[id])});
  const content = {placed: placed.map(entry), unplaced: unplaced.map(entry)};
  // The layout's settings count as what it shows, so changing one rewrites it.
  const layout = [STRUCTURE_DOC_LAYOUT, STRUCTURE_TEXT_SIZE, STRUCTURE_LINKS_SIZE,
    STRUCTURE_ITEM_SPACING, ctx.structureUrl];
  const hash = shortHash(JSON.stringify({layout, content}));
  if (props.getProperty(STRUCTURE_DOC_HASH_KEY) === hash) return;

  const doc = DocumentApp.openById(docId);
  const body = doc.getBody();
  // Empty it, leaving one plain paragraph for the title. (body.clear() fails
  // when the doc ends with a list item, as it does once written: Docs won't
  // remove a section's last paragraph, so a plain one is added at the end
  // and everything before it removed.)
  const title = body.appendParagraph('');
  while (body.getNumChildren() > 1) body.getChild(0).removeFromParent();
  // (Paragraph.setText returns nothing, so it can't be chained.)
  title.setText(STRUCTURE_DOC_NAME);
  title.setHeading(DocumentApp.ParagraphHeading.TITLE);
  // The intro, and the note when nothing's placed, are subtitles, so the
  // list's paragraphs are the only plain ones (see bulletStructureDoc).
  const grey = {[A.FOREGROUND_COLOR]: COLOR.muted, [A.FONT_SIZE]: STRUCTURE_TEXT_SIZE};
  const subtitle = runs => writeRuns(
    body.appendParagraph('').setHeading(DocumentApp.ParagraphHeading.SUBTITLE),
    typeof runs === 'string' ? [[runs, grey]] : runs);
  // The sheet's link (which only its editors, the Coordinating Circle, and
  // the Shared Drive's members can open).
  const sheetRun = ['Circle Structure sheet', ctx.structureUrl
    ? Object.assign({[A.LINK_URL]: ctx.structureUrl}, grey) : grey];
  subtitle([['Kept up to date automatically. To move a group, ask the Coordinating Circle, ' +
    'who change it in the ', grey], sheetRun, ['.', grey]]);
  // Each item is a plain paragraph, with a tab for each level it's nested
  // to, which bulletStructureDoc makes a bulleted list.
  const list = items => {
    for (const item of items) {
      const para = body.appendParagraph('').setHeading(DocumentApp.ParagraphHeading.NORMAL);
      writeRuns(para, [['\t'.repeat(item.depth), null], ...item.runs]);
    }
  };
  if (!content.placed.length) subtitle('Nothing is placed yet.');
  list(content.placed);
  if (content.unplaced.length) {
    body.appendParagraph('Not yet placed').setHeading(DocumentApp.ParagraphHeading.HEADING2);
    list(content.unplaced);
  }
  doc.saveAndClose();
  bulletStructureDoc(docId);
  props.setProperty(STRUCTURE_DOC_HASH_KEY, hash);
}

// Makes each run of plain paragraphs in the doc (its lists' items, each
// starting with a tab per level it's nested to) a bulleted list, with
// bullets at every level (DocumentApp can only number nested levels), and
// gives each item its level's narrow indents (listIndents) and
// STRUCTURE_ITEM_SPACING below it.
function bulletStructureDoc(docId) {
  const paragraphs = () => Docs.Documents.get(docId).body.content.filter(se => se.paragraph);
  const runs = [];
  let run = null;
  for (const se of paragraphs()) {
    const plain = (se.paragraph.paragraphStyle || {}).namedStyleType === 'NORMAL_TEXT' &&
      paraText(se.paragraph).trim();
    if (plain) {
      if (!run) runs.push(run = {startIndex: se.startIndex, endIndex: se.endIndex});
      run.endIndex = se.endIndex;
    } else {
      run = null;
    }
  }
  if (!runs.length) return;
  // Each request removes its items' tabs, so the last run goes first.
  Docs.Documents.batchUpdate({requests: runs.reverse().map(range => ({createParagraphBullets: {
    range, bulletPreset: 'BULLET_DISC_CIRCLE_SQUARE'}}))}, docId);
  // Bulleting resets a paragraph's spacing, so it's set here, after, with
  // the indents.
  const indents = paragraphs().filter(se => se.paragraph.bullet).map(se => {
    const request = listIndentRequest({startIndex: se.startIndex, endIndex: se.endIndex},
      se.paragraph.bullet.nestingLevel || 0);
    const style = request.updateParagraphStyle;
    // Between items of the same list, Docs collapses the space unless the
    // spacing mode says not to (as setting it in Format → Line & paragraph
    // spacing → Custom spacing does).
    style.paragraphStyle.spaceBelow = {magnitude: STRUCTURE_ITEM_SPACING, unit: 'PT'};
    style.paragraphStyle.spacingMode = 'NEVER_COLLAPSE';
    style.fields += ',spaceBelow,spacingMode';
    return request;
  });
  if (indents.length) Docs.Documents.batchUpdate({requests: indents}, docId);
}

// A group's line in the doc: its name, and smaller links to its members,
// documents, email list, and agenda doc (if it has one).
function structureDocRuns(g) {
  const runs = [[g.name, null]];
  const links = [];
  if (g.gatherUrl) links.push(['Members', g.gatherUrl]);
  if (g.folderUrl) links.push(['Documents', g.folderUrl]);
  const address = g.listAddress || g.email;
  if (address) links.push(['Email', 'mailto:' + address]);
  // Last, as some groups have none.
  if (g.agendaUrl) links.push(['Agenda', g.agendaUrl]);
  const small = {[A.FOREGROUND_COLOR]: COLOR.muted, [A.FONT_SIZE]: STRUCTURE_LINKS_SIZE};
  links.forEach(([text, url], i) => {
    runs.push([i ? ' · ' : '    ', small]);
    runs.push([text, Object.assign({[A.LINK_URL]: url}, small)]);
  });
  return runs;
}

// The point sizes of the doc's group names and intro (Docs' default text
// size), and of its Members · Documents · Email · Agenda links; and the
// space after each group's line, in points.
const STRUCTURE_TEXT_SIZE = 11;
const STRUCTURE_LINKS_SIZE = 8;
const STRUCTURE_ITEM_SPACING = 2;
// Bump when the doc's layout changes, so it's rewritten even if the
// structure hasn't.
const STRUCTURE_DOC_LAYOUT = 4;

// ── A circle's place, in its agenda doc ──────────────────────────────────────

// What structureRows needs to write a group's rows (see there).
function placeOf(ctx, g) {
  const item = id => ({name: ctx.byId[id].name, url: groupUrl(ctx.byId[id])});
  let status = 'placed';
  if (!g || !inStructure(g)) status = 'out';
  else if (ctx.parentOf[g.id] === TOP) status = 'top';
  else if (!ctx.tree.placed[g.id]) status = 'unplaced';
  const place = {status, parent: null, children: [], structureUrl: ctx.structureUrl,
    structureDocUrl: ctx.structureDocUrl};
  if (status === 'out') return place;
  if (status === 'placed') place.parent = item(ctx.parentOf[g.id]);
  place.children = (ctx.tree.children[g.id] || []).map(item);
  return place;
}

// The group a circle agenda doc belongs to: by the doc linked in the sheet,
// or else its labelled address, or else the group whose Drive folder holds
// it most closely (see nearestGroupFolder); or null.
function groupOfDoc(ctx, docId, email) {
  const url = g => docIdOf(g.agendaUrl) === docId;
  let g = ctx.groups.find(url);
  if (!g && email) {
    const e = email.toLowerCase();
    g = ctx.groups.find(x => x.email === e || x.listAddress === e);
  }
  if (!g) {
    const folderOf = x => (/folders\/([\w-]+)/.exec(x.folderUrl) || [])[1];
    const folder = nearestGroupFolder(docId, ctx.groups.map(folderOf).filter(Boolean));
    g = folder ? ctx.groups.find(x => folderOf(x) === folder) : null;
  }
  return g || null;
}

// Writes a circle agenda doc's Parent Circle and Sub-Circles rows (label
// and value) from the structure (see structureRows), each only if it
// doesn't already say that. g: the doc's group (null if it has none).
// Returns whether it wrote anything. Does nothing to a doc without the rows.
function refreshStructureRows(docId, ctx, g) {
  const place = placeOf(ctx, g);
  const want = structureRows(place);
  const doc = DocumentApp.openById(docId);
  const found = findRow(firstTabBody(doc), text => text === 'Circle email address');
  if (!found) return false;
  const table = found.table;
  let wrote = false;
  for (let r = 0; r < table.getNumRows(); r++) {
    const label = aboutLabel(table.getCell(r, 0).getText());
    const parts = label === 'Parent Circle' ? [want.parentLabel, want.parentValue]
      : label === 'Sub-Circles' ? [want.subLabel, want.subValue] : null;
    if (!parts) continue;
    parts.forEach((paragraphs, c) => {
      const cell = table.getCell(r, c);
      if (paragraphsSignature(cellParagraphs(cell)) === paragraphsSignature(paragraphs)) return;
      writeStructureCell(cell, paragraphs);
      if (c === 0) alignCell(cell, DocumentApp.HorizontalAlignment.RIGHT);
      wrote = true;
    });
  }
  doc.saveAndClose();
  return wrote;
}

// Writes structureRows' paragraphs into a cell, a bullet's as a bulleted
// list item with the outer level's indents (listIndents), all with
// DocumentApp: making bullets with the Docs API (replaceTokens) once
// mangled other parts of a doc, when it read the doc's positions from a
// version older than the one DocumentApp had just saved.
function writeStructureCell(cell, paragraphs) {
  resetCell(cell);
  const placeholder = cell.getChild(0).asParagraph();
  paragraphs.forEach((p, i) => {
    let para;
    if (p.bullet) {
      para = cell.appendListItem('');
      para.setGlyphType(DocumentApp.GlyphType.BULLET);
      const {firstLine, start} = listIndents(0);
      para.setIndentFirstLine(firstLine).setIndentStart(start);
    } else {
      para = i === 0 ? placeholder : cell.appendParagraph('');
    }
    writeRuns(para, docRuns(p));
  });
  // A cell starting with a list keeps no empty paragraph above it.
  if (paragraphs.length && paragraphs[0].bullet) placeholder.removeFromParent();
}

// A paragraph of structureRows as runs for writeRuns.
function docRuns(p) {
  const runs = p.runs.map(([text, style]) => {
    const attrs = {};
    if (style.bold) attrs[A.BOLD] = true;
    if (style.muted) attrs[A.FOREGROUND_COLOR] = COLOR.muted;
    if (style.small) attrs[A.FONT_SIZE] = LABEL_HINT_SIZE;
    if (style.link) attrs[A.LINK_URL] = style.link;
    return [text, Object.keys(attrs).length ? attrs : null];
  });
  return runs;
}

// A table cell's paragraphs as structureRows gives them ({runs}, each run
// [text, {link}]), for paragraphsSignature.
function cellParagraphs(cell) {
  const paragraphs = [];
  for (let i = 0; i < cell.getNumChildren(); i++) {
    const child = cell.getChild(i);
    const type = child.getType();
    if (type !== DocumentApp.ElementType.PARAGRAPH && type !== DocumentApp.ElementType.LIST_ITEM) continue;
    const text = child.editAsText();
    const s = text.getText();
    const starts = text.getTextAttributeIndices();
    const runs = starts.map((start, k) => {
      const end = k + 1 < starts.length ? starts[k + 1] : s.length;
      return [s.slice(start, end), {link: start < s.length ? text.getLinkUrl(start) : null}];
    });
    paragraphs.push({runs});
  }
  return paragraphs;
}

// Writes the rows (see refreshStructureRows) in every built circle doc that
// doesn't say what it should: run daily, to catch any the other runs missed.
function refreshStructureInAllCircleDocs() {
  const ctx = loadStructureContext();
  if (!ctx) return;
  for (const file of builtCircleDocs()) refreshStructureRowsOf(file, ctx);
}

// refreshStructureRows for a doc ({id, name, properties}), logging what it
// did.
function refreshStructureRowsOf(file, ctx) {
  try {
    const g = groupOfDoc(ctx, file.id, (file.properties || {})[AGENDA_KEYS.group]);
    if (refreshStructureRows(file.id, ctx, g)) console.log('Updated the place in ' + file.name + '.');
  } catch (e) {
    console.error('Couldn\'t update the place in ' + file.name + ': ' + e.message);
  }
}
