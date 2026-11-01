/**
 * Web App
 *
 * The links in an agenda doc open this web app, which changes the doc and
 * shows a page saying what it did. It runs as the account that deployed it
 * (admin@berkeleymoshav.org), so members need no permission of their own
 * and see no "unverified app" screen; anyone signed in to a Google account
 * can use it. Each link's address names the action, the doc, and the
 * meeting:
 *   ?action=times&doc=<doc ID>&meeting=<meeting ID>   updates the times
 *   ?action=item&doc=<doc ID>&meeting=<meeting ID>    adds an agenda item
 *
 * The links are written with the web app's address, from the WEB_APP_URL
 * script property, and the doc's own ID; a doc copied by hand still has
 * its original's ID in its links.
 *
 * tryUpdateTimes and tryAddItem run the same actions on the latest preview
 * from the editor, without the web app.
 *
 * Setup: see README.md in this folder.
 */

const ACTIONS = {times: updateTimes, item: addItem};

function doGet(e) {
  const p = e.parameter || {};
  const action = ACTIONS[p.action];
  if (!action || !/^[\w-]{20,}$/.test(p.doc || '') || !/^[\w-]+$/.test(p.meeting || '')) {
    return page({done: false, title: 'Not a link this app knows',
      messages: ['Use the links in a circle\'s agenda doc.']});
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    return page(action(p.doc, p.meeting));
  } catch (err) {
    return page({done: false, title: 'Something went wrong', messages: [err.message]});
  } finally {
    lock.releaseLock();
  }
}

function page(result) {
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c]);
  const close = '<p style="font-size: 1.6em; font-weight: bold; margin: 1em 0;">' +
    'Close this tab to return to the agenda.</p>';
  const html = '<div style="font-family: Arial, sans-serif; max-width: 40em; margin: 2em auto;">' +
    '<h2>' + (result.done ? '✓ ' : '') + esc(result.title) + '</h2>' +
    (result.done ? close : '') +
    result.messages.map(m => '<p>' + esc(m) + '</p>').join('') +
    (result.done ? '<p>The agenda already shows the change.</p>' : close) + '</div>';
  return HtmlService.createHtmlOutput(html).setTitle('Circle agenda');
}

// The address of a link that runs action on a meeting in a doc.
function actionUrl(action, docId, meetingId) {
  return webAppUrl() + '?action=' + action + '&doc=' + docId + '&meeting=' + meetingId;
}

function webAppUrl() {
  const url = PropertiesService.getScriptProperties().getProperty('WEB_APP_URL');
  if (!url) {
    throw new Error('Set the WEB_APP_URL script property to the web app\'s address ' +
      '(see "Deploying the web app" in README.md).');
  }
  return url;
}

// ── Trying the actions from the editor ───────────────────────────────────────

function tryUpdateTimes() {
  tryOnPreview(updateTimes);
}

function tryAddItem() {
  tryOnPreview(addItem);
}

// Runs an action on the first meeting in the latest preview, and logs what
// it returned.
function tryOnPreview(action) {
  const docId = PropertiesService.getScriptProperties().getProperty('previewDocId');
  if (!docId) throw new Error('No preview; run previewTemplate first.');
  const body = firstTabBody(DocumentApp.openById(docId));
  let meetingId = null;
  for (let i = 0; i < body.getNumChildren() && !meetingId; i++) {
    const el = body.getChild(i);
    if (el.getType() === DocumentApp.ElementType.TABLE) meetingId = meetingIdOf(el.asTable());
  }
  if (!meetingId) throw new Error('The preview has no meeting with links.');
  Logger.log(JSON.stringify(action(docId, meetingId), null, 2));
  logLink('Preview:', DocumentApp.openById(docId).getUrl());
}
