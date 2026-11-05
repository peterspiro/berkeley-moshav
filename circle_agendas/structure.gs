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
 *   with the groups: adds new groups (not yet placed, unless
 *   PLACEMENTS_FILE gives them a parent), renames, relinks, retypes, and
 *   removes groups that are gone.
 * - applyStructure, on each edit of the sheet (onCircleStructureEdit)
 *   and every BUILD_EVERY_MINUTES (from buildNewCircleDocs), refuses
 *   parents that break the rules (putting back the one from before, with a
 *   note saying why), updates the dropdowns and the Problems column, and,
 *   for whatever changed, rewrites the doc and the agenda docs' rows.
 * - refreshStructureRows writes one agenda doc's rows, if they don't say
 *   what they should: for the docs whose place changed, each doc edited in
 *   the last BUILD_EVERY_MINUTES (checkChangedCircleDocs, circle_docs.gs),
 *   and every doc daily (for the next meetings they list).
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

// The sheet's columns (1-based); ID, the Gather group's, is the row's key,
// and hidden.
const SCOL = {group: 1, type: 2, parent: 3, problems: 4, email: 5, gather: 6, folder: 7,
  agenda: 8, id: 9};
const STRUCTURE_HEADINGS = ['Group', 'Type', 'Parent', 'Problems', 'Email list', 'Gather group',
  'Drive folder', 'Agenda doc', 'Gather ID'];
const STRUCTURE_COLUMNS = STRUCTURE_HEADINGS.length;
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
  sheet.getRange(1, 1, 1, STRUCTURE_COLUMNS).setValues([STRUCTURE_HEADINGS]).setFontWeight('bold');
  sheet.getRange(1, SCOL.parent).setNote(PARENT_HELP);
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(1);
  sheet.setColumnWidth(SCOL.group, 220);
  sheet.setColumnWidth(SCOL.type, 140);
  sheet.setColumnWidth(SCOL.parent, 220);
  sheet.setColumnWidth(SCOL.problems, 360);
  sheet.hideColumns(SCOL.id);
  for (const p of sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET)) p.remove();
  const protection = sheet.protect().setDescription('Filled in by the Circle Structure script');
  protection.setUnprotectedRanges([sheet.getRange('C2:C')]);
  // Only the script's account (admin@) can edit the rest. (A file in a
  // shared drive has no owner, whom protection always lets edit, so it
  // names admin@ itself.)
  const me = Session.getEffectiveUser().getEmail();
  protection.addEditor(me);
  protection.removeEditors(protection.getEditors().map(u => u.getEmail())
    .filter(e => e.toLowerCase() !== me.toLowerCase()));
  if (protection.canDomainEdit()) protection.setDomainEdit(false);

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

// Run from the editor: rewrites the doc and every agenda doc's rows that
// don't say what they should.
function refreshCircleStructureEverywhere() {
  PropertiesService.getScriptProperties().deleteProperty(STRUCTURE_DOC_HASH_KEY);
  applyStructure();
  refreshStructureInAllCircleDocs();
}

// ── The sheet's rows ─────────────────────────────────────────────────────────

// The sheet's rows: [{row, id, name, type, parent, problems, email,
// gatherUrl, folderUrl, agendaUrl}], row the sheet's row number.
function readGroups(sheet) {
  const n = sheet.getLastRow() - 1;
  if (n < 1) return [];
  const range = sheet.getRange(2, 1, n, STRUCTURE_COLUMNS);
  const values = range.getValues();
  const rich = range.getRichTextValues();
  const link = (r, c) => (rich[r][c - 1] && rich[r][c - 1].getLinkUrl()) || '';
  const groups = [];
  values.forEach((v, r) => {
    const id = String(v[SCOL.id - 1] || '').trim();
    if (!id) return;
    groups.push({
      row: r + 2, id, name: String(v[SCOL.group - 1]).trim(), type: String(v[SCOL.type - 1]).trim(),
      parent: String(v[SCOL.parent - 1]).trim(), problems: String(v[SCOL.problems - 1]),
      email: String(v[SCOL.email - 1]).trim().toLowerCase(),
      gatherUrl: link(r, SCOL.gather), folderUrl: link(r, SCOL.folder),
      agendaUrl: link(r, SCOL.agenda),
    });
  });
  return groups;
}

// Brings the rows up to date with the Google Groups whose footers link a
// Gather group and a Drive item: each group's name (renaming it in the
// Parent cells that name it), type (see guessGroupType), email address,
// and links, and, for a group not in the structure, why, in Problems. A
// new group gets a row of its own, with an empty Parent; a group that's
// gone (only if every footer could be read) loses its row. The rows are
// kept in order by name.
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
    const {type, reason} = guessGroupType(f.name, driveItemInfo(f.driveItemId));
    const old = byId[f.gatherId];
    if (old && old.name !== f.name) renames.push([old.name, f.name]);
    const doc = docs[f.gatherId];
    rows.push({
      id: f.gatherId, name: f.name, type, problems: type === TYPE_OUT ? 'Not in the structure: ' + reason
        : (old ? old.problems : ''),
      parent: old ? old.parent : '', isNew: !old, email: f.email, gatherUrl: f.gatherUrl,
      folderUrl: 'https://drive.google.com/drive/folders/' + f.driveItemId,
      agendaUrl: doc ? 'https://docs.google.com/document/d/' + doc + '/edit' : '',
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

  // Rewrite the rows, all but the Parent cells that haven't changed (so an
  // edit made meanwhile isn't lost).
  const n = rows.length;
  const extra = sheet.getLastRow() - 1 - n;
  if (extra > 0) {
    sheet.getRange(n + 2, 1, extra, STRUCTURE_COLUMNS).clearContent().clearNote()
      .setDataValidation(null);
  }
  if (!n) return;
  const old = existing.slice();
  const text = (t, url) => SpreadsheetApp.newRichTextValue().setText(t || '').setLinkUrl(url || null).build();
  sheet.getRange(2, SCOL.group, n, 2).setValues(rows.map(r => [r.name, r.type]));
  sheet.getRange(2, SCOL.problems, n, 2).setValues(rows.map(r => [r.problems, r.email]));
  sheet.getRange(2, SCOL.gather, n, 3).setRichTextValues(rows.map(r => [
    text(r.gatherUrl ? 'Members' : '', r.gatherUrl), text('Documents', r.folderUrl),
    text(r.agendaUrl ? 'Agenda' : '', r.agendaUrl)]));
  sheet.getRange(2, SCOL.id, n, 1).setNumberFormat('@').setValues(rows.map(r => [r.id]));
  rows.forEach((r, i) => {
    const before = old[i];
    if (!before || before.id !== r.id || before.parent !== r.parent) {
      const cell = sheet.getRange(i + 2, SCOL.parent);
      cell.setDataValidation(null).setValue(r.parent);
    }
  });
  const added = rows.filter(r => r.isNew).map(r => r.name);
  if (added.length) console.log('Circle Structure: added ' + added.join(', ') + '.');
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
  for (const f of footers) if (f.gatherId && f.driveItemId) byEmail[f.email] = f;
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
    const rows = groups[groups.length - 1].row - 1;
    if (rows === groups.length) {
      const current = groups.map(g => [g.problems]);
      if (JSON.stringify(current) !== JSON.stringify(problems)) {
        sheet.getRange(2, SCOL.problems, rows, 1).setValues(problems);
      }
      sheet.getRange(2, SCOL.parent, rows, 1).setDataValidations(groups.map(g => {
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
  for (const g of groups) places[g.id] = shortHash(JSON.stringify(placeOf(ctx, g, true)));
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
  const circles = {};
  for (const c of loadCircles()) circles[c.email] = c;
  return {groups, byId, parentOf, tree, circles, structureUrl: structureDocUrl(), meetings: {}};
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
  const hash = shortHash(JSON.stringify(content));
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
  const subtitle = text => writeRuns(
    body.appendParagraph('').setHeading(DocumentApp.ParagraphHeading.SUBTITLE), [[text, MUTED]]);
  subtitle('Kept up to date automatically. To move a group, ask the Coordinating Circle.');
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
// gives each item its level's narrow indents (listIndents).
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
  const indents = paragraphs().filter(se => se.paragraph.bullet).map(se =>
    listIndentRequest({startIndex: se.startIndex, endIndex: se.endIndex},
      se.paragraph.bullet.nestingLevel || 0));
  if (indents.length) Docs.Documents.batchUpdate({requests: indents}, docId);
}

// A group's line in the doc: its name (linked; see groupUrl), and links to
// its members, documents, and email list.
function structureDocRuns(g) {
  const runs = [[g.name, Object.assign({}, BOLD, groupUrl(g) ? {[A.LINK_URL]: groupUrl(g)} : {})]];
  const links = [];
  if (g.gatherUrl) links.push(['Members', g.gatherUrl]);
  if (g.folderUrl) links.push(['Documents', g.folderUrl]);
  if (g.email) links.push(['Email', 'mailto:' + g.email]);
  links.forEach(([text, url], i) => {
    runs.push([i ? ' · ' : '    ', MUTED]);
    runs.push([text, {[A.LINK_URL]: url, [A.FOREGROUND_COLOR]: COLOR.muted}]);
  });
  return runs;
}

// ── A circle's place, in its agenda doc ──────────────────────────────────────

// What structureRows needs to write a group's rows (see there); without
// meetings if bare (for telling whether its place has changed).
function placeOf(ctx, g, bare) {
  const item = id => ({name: ctx.byId[id].name, url: groupUrl(ctx.byId[id]),
    next: bare ? null : nextMeetingOf(ctx, ctx.byId[id])});
  let status = 'placed';
  if (!g || !inStructure(g)) status = 'out';
  else if (ctx.parentOf[g.id] === TOP) status = 'top';
  else if (!ctx.tree.placed[g.id]) status = 'unplaced';
  const place = {status, parent: null, children: [], structureUrl: ctx.structureUrl};
  if (status === 'out') return place;
  if (status === 'placed') place.parent = item(ctx.parentOf[g.id]);
  place.children = (ctx.tree.children[g.id] || []).map(item);
  return place;
}

// The calendar every group's meetings are invited to (as
// ALL_EVENTS_CALENDAR_ID in util/meeting_rooms.py and group_meetings.gs),
// which everyone can view, unlike most groups' own calendars.
const ALL_EVENTS_CALENDAR_ID =
  'c_301c808f640b3a04f5344b1bbf266955d51fecaabd7835936b7591f9cff09222@group.calendar.google.com';

// A link to an event's copy on the all-events calendar, which shares its
// ID: an eid, as an event's htmlLink has, of its ID and that calendar's.
function allEventsEventUrl(eventId) {
  const eid = Utilities.base64EncodeWebSafe(eventId + ' ' + ALL_EVENTS_CALENDAR_ID).replace(/=+$/, '');
  return 'https://www.google.com/calendar/event?eid=' + eid;
}

// A group's next meeting, from its calendar: {text, url}, or null; the url
// opens it on the all-events calendar (see allEventsEventUrl).
function nextMeetingOf(ctx, g) {
  if (!g) return null;
  if (g.id in ctx.meetings) return ctx.meetings[g.id];
  let meeting = null;
  const circle = ctx.circles[g.email];
  if (circle && circle.calendarId) {
    try {
      const event = nextEvents(circle, new Date(), 1)[0];
      if (event) {
        meeting = {text: Utilities.formatDate(new Date(event.start.dateTime), TIME_ZONE, 'EEE, MMM d'),
          url: allEventsEventUrl(event.id)};
      }
    } catch (e) {
      console.warn('Couldn\'t read ' + g.name + '\'s calendar: ' + e.message);
    }
  }
  ctx.meetings[g.id] = meeting;
  return meeting;
}

// The group a circle agenda doc belongs to: by the doc linked in the sheet,
// or else its labelled address, or else the group whose Drive folder holds
// it most closely (see nearestGroupFolder); or null.
function groupOfDoc(ctx, docId, email) {
  const url = g => docIdOf(g.agendaUrl) === docId;
  let g = ctx.groups.find(url);
  if (!g && email) g = ctx.groups.find(x => x.email === email.toLowerCase());
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
  const place = placeOf(ctx, g, false);
  const want = structureRows(place);
  const doc = DocumentApp.openById(docId);
  const found = findRow(firstTabBody(doc), text => text === 'Circle email address');
  if (!found) return false;
  const table = found.table;
  let wrote = false;
  let bullets = false;
  for (let r = 0; r < table.getNumRows(); r++) {
    const label = aboutLabel(table.getCell(r, 0).getText());
    const parts = label === 'Parent Circle' ? [want.parentLabel, want.parentValue]
      : label === 'Sub-Circles' ? [want.subLabel, want.subValue] : null;
    if (!parts) continue;
    parts.forEach((paragraphs, c) => {
      const cell = table.getCell(r, c);
      if (paragraphsSignature(cellParagraphs(cell)) === paragraphsSignature(paragraphs)) return;
      resetCell(cell);
      writeCell(cell, paragraphs.map(p => docRuns(p)));
      if (c === 0) alignCell(cell, DocumentApp.HorizontalAlignment.RIGHT);
      if (paragraphs.some(p => p.bullet)) bullets = true;
      wrote = true;
    });
  }
  doc.saveAndClose();
  if (bullets) replaceTokens(docId);
  return wrote;
}

// The point size of the label cells' "(Set in the Circle Structure)".
const STRUCTURE_HINT_SIZE = 8;

// A paragraph of structureRows as runs for writeCell: a bullet's paragraph
// starts with the token replaceTokens makes a bullet.
function docRuns(p) {
  const runs = p.runs.map(([text, style]) => {
    const attrs = {};
    if (style.bold) attrs[A.BOLD] = true;
    if (style.muted) attrs[A.FOREGROUND_COLOR] = COLOR.muted;
    if (style.small) attrs[A.FONT_SIZE] = STRUCTURE_HINT_SIZE;
    if (style.link) attrs[A.LINK_URL] = style.link;
    return [text, Object.keys(attrs).length ? attrs : null];
  });
  return p.bullet ? [[token('bullet')], ...runs] : runs;
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
// doesn't say what it should: run daily, for the next meetings they list.
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
