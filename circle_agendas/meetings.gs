/**
 * Meetings
 *
 * What the web app (web_app.gs) does to an agenda doc: add the circle's
 * next meeting, and, to a meeting, update its times or add an agenda item.
 *
 * Each meeting has a tab of its own, titled with its date (newest first,
 * after the first tab), holding its date, its "Time … Place …" line, and
 * its attendance, then one table (see meetingTable in build_template.gs):
 * a row of column headings; one row per item, in the columns COL names
 * (start time, duration in minutes, description, notes), and among them (just above Action Items Recap) the row of the
 * meeting's links; then the closing round; then the end time.
 *
 * A meeting is found by its ID, which is in its links' addresses. Adding a
 * meeting removes the links from the meetings that are over.
 *
 * Adding a meeting takes two runs, startNewMeeting and finishNewMeeting:
 * the first adds its tab with the Docs API, and DocumentApp, which builds
 * the meeting, doesn't see a tab added in the same run.
 *
 * Each returns {done, title, messages}: whether it changed the doc, a
 * heading for the page the web app shows, and anything the reader should
 * know, such as a duration that couldn't be read.
 */

const NEW_ITEM_MINUTES = 5;
const PENDING_MEETING_SECONDS = 600;

// Updates a meeting's times, and its tab's title, if its date chip has been
// changed.
function updateTimes(docId, meetingId) {
  const doc = DocumentApp.openById(docId);
  const found = findMeeting(doc, meetingId);
  if (!found) return notFound();
  const messages = recalculate(found.table);
  doc.saveAndClose();
  const retitled = matchTabTitleToDate(docId, found.tabId);
  if (retitled) messages.push('The meeting\'s tab is now titled ' + retitled + ', its new date.');
  return {done: true, title: 'Times updated', messages};
}

// Retitles a meeting's tab with the date in its date chip, if they differ;
// returns the new title, or null.
function matchTabTitleToDate(docId, tabId) {
  const tab = Docs.Documents.get(docId, {includeTabsContent: true}).tabs
    .find(t => t.tabProperties.tabId === tabId);
  if (!tab) return null;
  let date = null;
  walkParagraphs(tab.documentTab.body.content, para => {
    for (const pe of para.elements || []) {
      if (date || !pe.dateElement) continue;
      const props = pe.dateElement.dateElementProperties;
      date = /^\d{4}-\d{2}-\d{2}$/.test(props.displayText || '')
        ? props.displayText : (props.timestamp || '').slice(0, 10);
    }
  });
  if (!date || !MEETING_TAB_TITLE_RE.test(date) || date === tab.tabProperties.title) return null;
  Docs.Documents.batchUpdate({requests: [{updateDocumentTabProperties: {
    tabProperties: {tabId, title: date}, fields: 'title',
  }}]}, docId);
  return date;
}

// Adds a topic (with a Presenter dropdown, if the meeting has one, and
// bulleted lists), with a duration of NEW_ITEM_MINUTES, just above the links
// (or, without them, Action Items Recap, or the closing round), so the links
// stay just below the new item, and updates the times.
function addItem(docId, meetingId) {
  const doc = DocumentApp.openById(docId);
  const found = findMeeting(doc, meetingId);
  if (!found) return notFound();
  const {table, tabId} = found;

  const closing = table.getNumRows() - 2;
  let at = linksRowIndex(table);
  for (let r = MEETING_HEADER_ROWS; r < closing && at < 0; r++) {
    if (table.getCell(r, COL.description).getText().trim().startsWith('Action Items Recap')) at = r;
  }
  if (at < 0) at = closing;
  const presenters = presenterDropdownId(meetingId);
  const hasPresenters = presenters in dropdownDefinitions(docId, tabId);
  // As many cells as the meeting's own columns (older meetings have fewer).
  const columns = table.getRow(0).getNumCells();
  const row = table.insertTableRow(at);
  for (let c = 0; c < columns; c++) row.appendTableCell();
  for (let c = 0; c < columns; c++) writeCell(row.getCell(c), [[]]);
  writeCell(row.getCell(COL.duration), [[[String(NEW_ITEM_MINUTES), MUTED]]]);
  padRow(row);
  writeCell(row.getCell(COL.description), topicLines(hasPresenters ? presenters : null));
  writeCell(row.getCell(COL.notes), [[[token('bullet')]]]);
  shadeFollowUp(table, at);

  const messages = recalculate(table);
  doc.saveAndClose();
  replaceTokens(docId, tabId);
  return {done: true, title: 'Item added', messages};
}

// ── New meetings ─────────────────────────────────────────────────────────────

// The first of the two runs that add the circle's next meeting: the first
// timed event on the circle's calendar after the day of the newest meeting
// in the doc (or after now, if there's none), so clicking again adds the
// one after. Adds the meeting's tab, and keeps what the
// second run needs for PENDING_MEETING_SECONDS; returns {pending: key} for
// finishNewMeeting, or a result saying why it didn't.
//
// The meeting's date, start, and length come from the event; its Place is
// the circle's Zoom room; its attendance list is the circle's members; its
// Administrative matters link the previous meeting's tab (its minutes) and
// give the event after as the next meeting; and the
// previous meeting's Action Items Recap is copied to its Action Items
// Accountability.
function startNewMeeting(docId) {
  const circle = circleForDoc(docId);
  if (!circle) {
    return {done: false, title: 'Circle not found', messages: [
      'This doc isn\'t in a circle\'s Drive folder, so there\'s no calendar to take the ' +
      'meeting from. Move it into the circle\'s folder and try again.']};
  }
  if (!circle.calendarId) {
    return {done: false, title: 'No calendar', messages: [
      circle.name + ' has no calendar yet; ask the Technology Circle to set one up.']};
  }

  const api = Docs.Documents.get(docId, {includeTabsContent: true});
  const meetingTabs = api.tabs.filter(t => MEETING_TAB_TITLE_RE.test(t.tabProperties.title))
    .sort((a, b) => b.tabProperties.title.localeCompare(a.tabProperties.title));
  const newest = meetingTabs[0];
  const newestDate = newest ? newest.tabProperties.title : null;
  // Meeting tabs go after the others (Circle, Backlog), newest first.
  const firstMeetingIndex = api.tabs.length - meetingTabs.length;

  const after = newestDate
    ? new Date(Utilities.parseDate(newestDate, TIME_ZONE, 'yyyy-MM-dd').getTime() + 24 * 3600 * 1000)
    : new Date();
  const [event, following] = nextEvents(circle, after, 2);
  if (!event) {
    return {done: false, title: 'No meeting to add', messages: [
      'There\'s no meeting on ' + circle.name + '\'s calendar after ' +
      (newestDate || 'today') + '. Add it to the calendar first.']};
  }
  const start = new Date(event.start.dateTime);
  const end = new Date(event.end.dateTime);
  const details = {
    date: Utilities.formatDate(start, TIME_ZONE, 'yyyy-MM-dd'),
    start: Utilities.formatDate(start, TIME_ZONE, 'h:mm a'),
    hours: formatHours((end - start) / 60000),
    place: circle.zoomUrl,
    attendees: circleMemberNames(circle),
    next: following ? Utilities.formatDate(new Date(following.start.dateTime), TIME_ZONE,
      'EEE yyyy-MM-dd, h:mm a') : 'not on the calendar yet',
  };
  if (newest) {
    details.previousTabId = newest.tabProperties.tabId;
    details.previousUrl = tabUrl(docId, details.previousTabId);
  }

  const reply = Docs.Documents.batchUpdate({requests: [{addDocumentTab: {
    tabProperties: {title: details.date, index: firstMeetingIndex},
  }}]}, docId);
  details.tabId = reply.replies[0].addDocumentTab.tabProperties.tabId;
  details.docId = docId;
  const key = 'newMeeting:' + Utilities.getUuid();
  CacheService.getScriptCache().put(key, JSON.stringify(details), PENDING_MEETING_SECONDS);
  return {pending: key, date: details.date};
}

// The second run: builds the meeting in the tab startNewMeeting added, and
// removes the links from the meetings that are over.
function finishNewMeeting(key) {
  const cached = CacheService.getScriptCache().get(key);
  if (!cached) {
    return {done: false, title: 'Meeting not finished', messages: [
      'Its details were lost, so its tab was left empty. Delete the tab, and click ' +
      '"+ New meeting" again.']};
  }
  const details = JSON.parse(cached);
  const docId = details.docId;
  const doc = DocumentApp.openById(docId);
  const tab = doc.getTab(details.tabId);
  if (!tab) {
    return {done: false, title: 'Meeting not finished', messages: [
      'Its tab wasn\'t ready yet. Delete the empty tab dated ' + details.date + ', and click ' +
      '"+ New meeting" again.']};
  }
  if (details.previousTabId) {
    const previousTab = doc.getTab(details.previousTabId);
    const previous = previousTab && meetingTableIn(previousTab.asDocumentTab().getBody());
    if (previous) details.carryOver = recapItems(previous);
  }
  details.facilitator = roleHolder(doc, 'Facilitator');
  details.secretary = roleHolder(doc, 'Secretary');
  const body = tab.asDocumentTab().getBody();
  const leftover = body.getChild(0);
  const meetingId = meetingTable(body, docId, details);
  body.appendParagraph('');
  leftover.removeFromParent();

  // The meetings that are over lose their links.
  for (const other of allTabs(doc)) {
    if (other.getId() === details.tabId) continue;
    const otherBody = other.asDocumentTab().getBody();
    const table = meetingTableIn(otherBody);
    const links = table ? linksRowIndex(table) : -1;
    const end = meetingEnd(other.getTitle(), otherBody);
    if (links >= 0 && end !== null && end < Date.now()) table.removeRow(links);
  }
  doc.saveAndClose();

  if (details.attendees.length) {
    Docs.Documents.batchUpdate({requests: [
      presenterDropdownRequest(meetingId, details.attendees, details.tabId)]}, docId);
  }
  replaceTokens(docId, details.tabId);
  finishMeeting(docId, meetingId, details.tabId);
  CacheService.getScriptCache().remove(key);
  return {done: true, title: 'Meeting added', messages: [
    'Added the meeting on ' + details.date + ' at ' + details.start + ', in a tab of its own ' +
    'at the left, titled with its date.']};
}

// When a meeting ends (milliseconds since 1970), from its tab's title (its
// date) and its "Time … Duration (hours) …" line; null if they can't be
// read.
function meetingEnd(title, body) {
  if (!MEETING_TAB_TITLE_RE.test(title)) return null;
  const line = timeLineText(body);
  const start = parseTime(headerTimeText(line));
  const length = parseHours(headerHoursText(line));
  if (start === null || length === null) return null;
  return Utilities.parseDate(title, TIME_ZONE, 'yyyy-MM-dd').getTime() + (start + length) * 60000;
}

// Who holds a role, from the Circle tab's Members and roles table, or ''.
function roleHolder(doc, role) {
  const found = findRow(firstTabBody(doc), text => text.startsWith(role + ' ('));
  return found && found.row.getNumCells() > 1 ? found.row.getCell(1).getText().trim() : '';
}

function tabUrl(docId, tabId) {
  return 'https://docs.google.com/document/d/' + docId + '/edit?tab=' + tabId;
}

// The text of each non-empty item under a meeting's Action Items Recap.
// (Only the text: Accountability makes each a checklist item.)
function recapItems(table) {
  for (let r = MEETING_HEADER_ROWS; r < table.getNumRows(); r++) {
    const cell = table.getCell(r, COL.description);
    if (!cell.getText().trim().startsWith('Action Items Recap')) continue;
    const items = [];
    for (let i = 1; i < cell.getNumChildren(); i++) {
      const el = cell.getChild(i);
      const type = el.getType();
      if (type === DocumentApp.ElementType.LIST_ITEM || type === DocumentApp.ElementType.PARAGRAPH) {
        const text = el.getText().trim();
        if (text) items.push(text);
      }
    }
    return items;
  }
  return [];
}


// A tab's dropdowns' lists of choices, by ID (each tab has its own).
function dropdownDefinitions(docId, tabId) {
  const tab = Docs.Documents.get(docId, {includeTabsContent: true}).tabs
    .find(t => t.tabProperties.tabId === tabId);
  return (tab && tab.documentTab.dropdownDefinitions) || {};
}

function notFound() {
  return {done: false, title: 'Meeting not found', messages: [
    'This meeting isn\'t in the doc any more, or the row holding its links was changed.']};
}

// ── Times ────────────────────────────────────────────────────────────────────

// Rewrites each item's start time, the closing round's duration, and the end
// time, from the meeting's start time and length and the other items'
// durations. A duration that can't be read is shown in red and counted as
// 0. Returns what the reader should know.
function recalculate(table) {
  const messages = [];
  const timeLine = timeLineText(table.getParent());
  const startText = headerTimeText(timeLine);
  const start = parseTime(startText);
  if (start === null) {
    return ['Couldn\'t read the start time "' + startText + '" after "Time"; ' +
      'nothing was changed. Write it like 19:30 or 7:30 pm.'];
  }
  const lengthText = headerHoursText(timeLine);
  const total = parseHours(lengthText);
  if (total === null) {
    return ['Couldn\'t read the meeting\'s Duration (hours) "' + lengthText + '"; ' +
      'nothing was changed. Write it like 1.5 or 1:30.'];
  }

  const last = table.getNumRows() - 1;
  const closing = last - 1;
  const links = linksRowIndex(table);
  const itemRows = [];
  for (let r = MEETING_HEADER_ROWS; r < closing; r++) if (r !== links) itemRows.push(r);
  const durations = [];
  for (const r of itemRows) {
    const cell = table.getCell(r, COL.duration);
    const text = cell.getText().trim();
    const minutes = parseMinutes(text);
    if (minutes === null) {
      messages.push('Couldn\'t read the duration "' + text + '" of "' + itemTitle(table, r) +
        '", so it was counted as 0 and shown in red. Write it as a number of minutes.');
    }
    if (text) cell.editAsText().setForegroundColor(minutes === null ? PROBLEM_COLOR : COLOR.muted);
    durations.push(minutes || 0);
  }

  const times = scheduleTimes(start, durations, total);
  times.starts.forEach((t, i) => writeMuted(table.getCell(itemRows[i], COL.time), formatTime(t)));
  writeMuted(table.getCell(closing, COL.time), formatTime(times.closingStart));
  const noTimeLeft = times.closingMinutes <= 0;
  writeMuted(table.getCell(closing, COL.duration), String(times.closingMinutes),
    {[A.ITALIC]: true, [A.FOREGROUND_COLOR]: noTimeLeft ? PROBLEM_COLOR : COLOR.muted});
  writeMuted(table.getCell(last, COL.time), formatTime(times.end));
  if (times.closingMinutes < 0) {
    messages.push('The items before the closing round run ' + -times.closingMinutes +
      ' minutes past the end of the meeting, so its duration is negative.');
  } else if (noTimeLeft) {
    messages.push('The items before the closing round take the whole meeting, so it has ' +
      'no time left.');
  }
  return messages;
}

// The text of the "Time … Duration (hours) … Place …" line above a
// meeting's table, in the body (its tab's) that holds it, or ''.
function timeLineText(body) {
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() !== DocumentApp.ElementType.PARAGRAPH) continue;
    const text = el.asParagraph().getText();
    if (/^\s*Time\b/.test(text)) return text;
  }
  return '';
}

function writeMuted(cell, text, extra) {
  writeCell(cell, [[[text, {...MUTED, ...extra}]]]);
}

function itemTitle(table, r) {
  return snippet(table.getCell(r, COL.description).getText().split('\n')[0], 40);
}

// ── Finding meetings ─────────────────────────────────────────────────────────

// The meeting with this ID, in any tab: {table, tabId}, or null.
function findMeeting(doc, meetingId) {
  for (const tab of allTabs(doc)) {
    const table = meetingTableIn(tab.asDocumentTab().getBody());
    if (table && meetingIdOf(table) === meetingId) return {table, tabId: tab.getId()};
  }
  return null;
}

// The first meeting table (one with links, or with the column headings) in
// a body, or null.
function meetingTableIn(body) {
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() !== DocumentApp.ElementType.TABLE) continue;
    const table = el.asTable();
    if (meetingIdOf(table) || isMeetingTable(table)) return table;
  }
  return null;
}

function isMeetingTable(table) {
  if (table.getNumRows() <= MEETING_HEADER_ROWS) return false;
  const row = table.getRow(MEETING_HEADER_ROWS - 1);
  return row.getNumCells() > COL.notes &&
    row.getCell(COL.description).getText().trim() === MEETING_HEADINGS[COL.description];
}

// Every tab in a doc, child tabs included.
function allTabs(doc) {
  const flatten = tabs => tabs.flatMap(tab => [tab, ...flatten(tab.getChildTabs())]);
  return flatten(doc.getTabs());
}

// The ID in a meeting table's links, or null if the table isn't a meeting.
function meetingIdOf(table) {
  const r = linksRowIndex(table);
  return r < 0 ? null : linkedMeetingId(table.getCell(r, COL.description));
}

// The index of the row holding a meeting's links, or -1.
function linksRowIndex(table) {
  for (let r = MEETING_HEADER_ROWS; r < table.getNumRows(); r++) {
    if (table.getRow(r).getNumCells() > COL.description &&
        linkedMeetingId(table.getCell(r, COL.description))) {
      return r;
    }
  }
  return -1;
}

// The meeting ID in a link in a cell, or null.
function linkedMeetingId(cell) {
  const text = cell.editAsText();
  for (const i of text.getTextAttributeIndices()) {
    const id = meetingIdFromUrl(text.getLinkUrl(i));
    if (id) return id;
  }
  return null;
}

function newMeetingId() {
  return Utilities.getUuid().replace(/-/g, '').slice(0, 12);
}
