/**
 * Template Edits
 *
 * Changes to the circle agenda template (TEMPLATE_DOC_ID in
 * doc_helpers.gs), kept as code so each one can be tried on a copy first and
 * the template's history is in git. The template itself, not this file, is
 * the master copy: scripts can't build its dropdown chips from scratch.
 *
 * Each edit in EDITS has an id and a function that changes the doc whose
 * ID it's given (editBody gives it the doc's first tab, for DocumentApp
 * edits; Docs API edits use the ID). Edits are applied once each, in order; Script Properties record
 * which ones the template has had. To add one, append it to EDITS, push,
 * and run:
 *   - previewEdits: copies the template, applies the edits it hasn't had to
 *     the copy, and logs the copy's link, for checking;
 *   - applyEdits: applies them to the template itself;
 *   - describeTemplate: logs the template's structure, to send back when
 *     an edit needs to know more about it;
 *   - checkTemplateAccess: logs what the running account may do to the
 *     template, for when an edit is refused.
 * An edit's function returns lines saying what it did; one that finds
 * nothing to change should say so, not fail.
 *
 * Setup: see README.md in this folder.
 */

const EDITS = [
  {id: '001-remove-sofa-credit', apply: removeSofaCredit},
  {id: '002-remove-member-rows', apply: removeMemberRows},
  {id: '003-remove-headers-footers', apply: removeHeadersAndFooters},
];

function previewEdits() {
  const pending = pendingEdits();
  if (!pending.length) return Logger.log('The template has had every edit; nothing to preview.');
  const copy = DriveApp.getFileById(TEMPLATE_DOC_ID)
    .makeCopy('Template preview ' + new Date().toISOString().slice(0, 16));
  applyTo(copy.getId(), pending);
  Logger.log('Preview: ' + copy.getUrl());
}

function applyEdits() {
  const pending = pendingEdits();
  if (!pending.length) return Logger.log('The template has had every edit; nothing to apply.');
  applyTo(TEMPLATE_DOC_ID, pending, edit => {
    const applied = appliedEditIds();
    applied.push(edit.id);
    PropertiesService.getScriptProperties()
      .setProperty(appliedKey(), JSON.stringify(applied));
  });
  Logger.log('Applied to the template: ' + DocumentApp.openById(TEMPLATE_DOC_ID).getUrl());
}

function describeTemplate() {
  Logger.log(describeStructure(TEMPLATE_DOC_ID).join('\n'));
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

// ── Edits ────────────────────────────────────────────────────────────────────

// Sociocracy For All's credit lines, under the title at the top.
function removeSofaCredit(docId) {
  return editBody(docId, body => {
    const paragraphs = paragraphsMatching(body, /Sociocracy For All|SoFA/);
    for (const p of paragraphs) removeParagraph(p);
    return [paragraphs.length
      ? 'Removed ' + paragraphs.length + ' paragraph(s) crediting Sociocracy For All.'
      : 'No paragraph credits Sociocracy For All.'];
  });
}

// The Member row(s) of the Members and roles table; the role rows stay.
function removeMemberRows(docId) {
  return editBody(docId, body => {
    let removed = 0;
    let found = findRow(body, text => text === 'Member');
    while (found) {
      found.row.removeFromParent();
      removed++;
      found = findRow(body, text => text === 'Member');
    }
    return [removed ? 'Removed ' + removed + ' Member row(s).' : 'No Member row found.'];
  });
}

// Every tab's headers and footers (the template's footer numbers its
// pages). A pageless doc doesn't show them, and shows a notice saying some
// features are hidden while it has any, even empty ones.
function removeHeadersAndFooters(docId) {
  const doc = Docs.Documents.get(docId, {includeTabsContent: true});
  const requests = [];
  allApiTabs(doc.tabs).forEach((tab, i) => {
    // Without a tabId, a request is for the first tab.
    const tabId = i ? {tabId: tab.tabProperties.tabId} : {};
    const dt = tab.documentTab;
    for (const headerId of Object.keys(dt.headers || {})) {
      requests.push({deleteHeader: {headerId, ...tabId}});
    }
    for (const footerId of Object.keys(dt.footers || {})) {
      requests.push({deleteFooter: {footerId, ...tabId}});
    }
  });
  if (!requests.length) return ['No headers or footers.'];
  Docs.Documents.batchUpdate({requests}, docId);
  return ['Removed ' + requests.length + ' header(s) and footer(s).'];
}

// ── Running edits ────────────────────────────────────────────────────────────

// Applies edits to the doc in order, calling done(edit) after each.
// Stops at the first that fails.
function applyTo(docId, edits, done) {
  for (const edit of edits) {
    let lines;
    try {
      lines = edit.apply(docId);
    } catch (e) {
      Logger.log(edit.id + ' FAILED, so it and the edits after it were not applied: ' + e.message);
      throw e;
    }
    Logger.log(edit.id + ': ' + lines.join(' '));
    if (done) done(edit);
  }
}

// Runs fn on the doc's first tab with DocumentApp, saves, and returns what
// fn returns. DocumentApp changes are sent when the doc is saved, so a
// refused change fails at the save.
function editBody(docId, fn) {
  const doc = DocumentApp.openById(docId);
  const lines = fn(doc.getTabs()[0].asDocumentTab().getBody());
  doc.saveAndClose();
  return lines;
}

function pendingEdits() {
  const applied = new Set(appliedEditIds());
  return EDITS.filter(edit => !applied.has(edit.id));
}

function appliedEditIds() {
  return JSON.parse(PropertiesService.getScriptProperties().getProperty(appliedKey()) || '[]');
}

// Keyed by the template's ID, so pointing TEMPLATE_DOC_ID at another doc
// starts its record afresh.
function appliedKey() {
  return 'appliedEdits:' + TEMPLATE_DOC_ID;
}

// ── Edit helpers ─────────────────────────────────────────────────────────────

// The paragraphs (at any depth, table cells included) whose text matches.
function paragraphsMatching(body, regex) {
  const result = [];
  const T = DocumentApp.ElementType;
  const visit = el => {
    const type = el.getType();
    if (type === T.PARAGRAPH || type === T.LIST_ITEM) {
      if (regex.test(el.getText())) result.push(el);
    } else if (el.getNumChildren) {
      for (let i = 0; i < el.getNumChildren(); i++) visit(el.getChild(i));
    }
  };
  visit(body);
  return result;
}

// Removes a paragraph, or empties it if it's the last element of its table
// cell or body, which Docs can't remove.
function removeParagraph(p) {
  const parent = p.getParent();
  if (parent.getChildIndex(p) < parent.getNumChildren() - 1) {
    p.removeFromParent();
  } else {
    p.clear();
  }
}
