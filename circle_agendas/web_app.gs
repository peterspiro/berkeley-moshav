/**
 * Web App
 *
 * The links in an agenda doc open this web app, which changes the doc and
 * shows a page saying what it did. It runs as the account that deployed it
 * (admin@berkeleymoshav.org), so members need no permission of their own
 * and see no "unverified app" screen; anyone signed in to a Google account
 * can use it. Each link's address names the action, the doc, and (but for
 * a new meeting) the meeting:
 *   ?action=times&doc=<doc ID>&meeting=<meeting ID>   updates the times
 *   ?action=item&doc=<doc ID>&meeting=<meeting ID>    adds an agenda item
 *   ?action=meeting&doc=<doc ID>                      adds the next meeting
 *
 * It acts only on circle agenda docs and the latest preview (isAgendaDoc).
 *
 * A meeting's "✉ Announce agenda" link doesn't come here: it opens Gmail's
 * compose window directly (see announceUrl in agenda_rules.gs).
 *
 * The links are written with the web app's address, from the WEB_APP_URL
 * script property, and the doc's own ID; a doc copied by hand still has
 * its original's ID in its links.
 *
 * A new meeting takes two runs (see meetings.gs): the link's run adds the
 * meeting's tab, and returns a page saying it's adding the meeting, whose
 * script then calls finishNewMeetingPage, the second run, and shows what it
 * returns.
 *
 * tryUpdateTimes, tryAddItem, and tryAddMeeting (then tryFinishMeeting) run
 * the same actions on the latest preview from the editor, without the web
 * app.
 *
 * Setup: see README.md in this folder.
 */

// Each action, and whether it acts on one meeting (and so needs its ID).
const ACTIONS = {
  times: {run: updateTimes, perMeeting: true},
  item: {run: addItem, perMeeting: true},
  meeting: {run: startNewMeeting, perMeeting: false},
};

function doGet(e) {
  const p = e.parameter || {};
  const action = ACTIONS[p.action];
  if (!action || !/^[\w-]{20,}$/.test(p.doc || '') ||
      (action.perMeeting && !/^[\w-]+$/.test(p.meeting || ''))) {
    return page({done: false, title: 'Not a link this app knows',
      messages: ['Use the links in a circle\'s agenda doc.']});
  }
  if (!isAgendaDoc(p.doc)) {
    return page({done: false, title: 'Not a circle agenda doc',
      messages: ['These links work only in a circle\'s agenda doc.']});
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const result = action.run(p.doc, p.meeting);
    return result.pending ? pendingPage(result) : page(result);
  } catch (err) {
    return page({done: false, title: 'Something went wrong', messages: [err.message]});
  } finally {
    lock.releaseLock();
  }
}

// Whether the app may change a doc: a circle agenda doc (labelled built;
// see circle_docs.gs) or the latest preview. The app runs as admin, who can
// edit many other docs, and a link's doc ID can be edited by hand.
function isAgendaDoc(docId) {
  if (docId === PropertiesService.getScriptProperties().getProperty('previewDocId')) return true;
  try {
    const file = Drive.Files.get(docId, {fields: 'properties,trashed', supportsAllDrives: true});
    return !file.trashed && (file.properties || {})[AGENDA_KEYS.state] === AGENDA_STATE_BUILT;
  } catch (e) {
    return false;  // no such doc, or admin can't see it
  }
}

// The second run of adding a meeting, called from pendingPage's script:
// the HTML of the page saying how it went.
function finishNewMeetingPage(key) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    return pageHtml(finishNewMeeting(key));
  } catch (err) {
    return pageHtml({done: false, title: 'Something went wrong', messages: [err.message]});
  } finally {
    lock.releaseLock();
  }
}

// A page saying the meeting is being added, which finishes adding it.
function pendingPage(result) {
  const html = '<div id="page" style="font-family: Arial, sans-serif; max-width: 40em; ' +
    'margin: 2em auto;"><h2>Adding the meeting on ' + result.date + '…</h2>' +
    '<p>This takes a few seconds. Please leave this tab open until it says it\'s done.</p></div>' +
    '<script>google.script.run' +
    '.withSuccessHandler(html => { document.getElementById("page").outerHTML = html; })' +
    '.withFailureHandler(err => { document.getElementById("page").innerHTML = ' +
    '"<h2>Something went wrong</h2><p>" + String(err.message || err).replace(/</g, "&lt;") + ' +
    '"</p>"; })' +
    '.finishNewMeetingPage(' + JSON.stringify(result.pending) + ');</script>';
  return HtmlService.createHtmlOutput(html).setTitle('Circle agenda');
}

function page(result) {
  return HtmlService.createHtmlOutput(pageHtml(result)).setTitle('Circle agenda');
}

function pageHtml(result) {
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c]);
  const close = '<p style="font-size: 1.6em; font-weight: bold; margin: 1em 0;">' +
    'Close this tab to return to the agenda.</p>';
  const html = '<div style="font-family: Arial, sans-serif; max-width: 40em; margin: 2em auto;">' +
    '<h2>' + (result.done ? '✓ ' : '') + esc(result.title) + '</h2>' +
    (result.done ? close : '') +
    result.messages.map(m => '<p>' + esc(m) + '</p>').join('') +
    (result.done ? '<p>The agenda now shows the change.</p>' : close) + '</div>';
  return html;
}

// The address of a link that runs action on a doc (and, for most actions,
// on one meeting in it).
function actionUrl(action, docId, meetingId) {
  return webAppUrl() + '?action=' + action + '&doc=' + docId +
    (meetingId ? '&meeting=' + meetingId : '');
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

// The first of the two runs that add a meeting to the latest preview; run
// tryFinishMeeting next.
function tryAddMeeting() {
  const docId = PropertiesService.getScriptProperties().getProperty('previewDocId');
  if (!docId) throw new Error('No preview; run previewTemplate first.');
  const result = startNewMeeting(docId);
  Logger.log(JSON.stringify(result, null, 2));
  if (result.pending) {
    PropertiesService.getScriptProperties().setProperty('pendingMeeting', result.pending);
    Logger.log('Now run tryFinishMeeting.');
  }
}

function tryFinishMeeting() {
  const key = PropertiesService.getScriptProperties().getProperty('pendingMeeting');
  if (!key) throw new Error('Run tryAddMeeting first.');
  Logger.log(JSON.stringify(finishNewMeeting(key), null, 2));
}

// Does to the latest preview what the daily run (updateRolesInCircleDocs)
// does to each circle doc, and logs what changed.
function tryUpdateRoles() {
  const docId = PropertiesService.getScriptProperties().getProperty('previewDocId');
  if (!docId) throw new Error('No preview; run previewTemplate first.');
  const circle = circleForDoc(docId);
  if (!circle) Logger.log('The preview isn\'t in a circle\'s folder, so its Roles choices ' +
    'can\'t be updated; checking its dates only.');
  Logger.log('Before:\n' + describeRoles(docId).join('\n'));
  const lines = updateRoles(docId, circle);
  Logger.log(lines.join('\n') || 'Nothing needed changing.');
  if (lines.length) Logger.log('After:\n' + describeRoles(docId).join('\n'));
  logLink('Preview:', DocumentApp.openById(docId).getUrl());
}

// Runs an action on the newest meeting with links in the latest preview,
// and logs what it returned.
function tryOnPreview(action) {
  const docId = PropertiesService.getScriptProperties().getProperty('previewDocId');
  if (!docId) throw new Error('No preview; run previewTemplate first.');
  let meetingId = null;
  for (const tab of allTabs(DocumentApp.openById(docId))) {
    const table = meetingTableIn(tab.asDocumentTab().getBody());
    if (table && !meetingId) meetingId = meetingIdOf(table);
  }
  if (!meetingId) throw new Error('The preview has no meeting with links; add one first.');
  Logger.log(JSON.stringify(action(docId, meetingId), null, 2));
  logLink('Preview:', DocumentApp.openById(docId).getUrl());
}
