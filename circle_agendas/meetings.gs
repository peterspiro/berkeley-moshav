/**
 * Meetings
 *
 * What the web app (web_app.gs) does to an agenda doc: add the circle's
 * next meeting, and, to a meeting, update its times or add an agenda item.
 * A meeting is one table (see meetingTable in build_template.gs):
 * MEETING_HEADER_ROWS rows of date, time and place, and column headings;
 * then one row per item, in the columns COL names (start time, duration in
 * minutes, presenter, description, notes, follow-up), and among them (just
 * above Action Items Recap) the row of the meeting's links; then the
 * closing round; then the end time.
 *
 * A meeting is found by its ID, which is in its links' addresses, so the
 * links keep working wherever the meeting moves in the doc.
 *
 * Each returns {done, title, messages}: whether it changed the doc, a
 * heading for the page the web app shows, and anything the reader should
 * know, such as a duration that couldn't be read.
 */

const PROBLEM_COLOR = '#cc0000';
const NEW_ITEM_MINUTES = 5;

function updateTimes(docId, meetingId) {
  const doc = DocumentApp.openById(docId);
  const table = findMeeting(firstTabBody(doc), meetingId);
  if (!table) return notFound();
  const messages = recalculate(table);
  doc.saveAndClose();
  return {done: true, title: 'Times updated', messages};
}

// Adds a topic, with a duration of NEW_ITEM_MINUTES, just above the links
// (or, without them, Action Items Recap, or the closing round), so the links
// stay just below the new item, and updates the times.
function addItem(docId, meetingId) {
  const doc = DocumentApp.openById(docId);
  const table = findMeeting(firstTabBody(doc), meetingId);
  if (!table) return notFound();

  const closing = table.getNumRows() - 2;
  let at = linksRowIndex(table);
  for (let r = MEETING_HEADER_ROWS; r < closing && at < 0; r++) {
    if (table.getCell(r, COL.description).getText().trim().startsWith('Action Items Recap')) at = r;
  }
  if (at < 0) at = closing;
  const presenters = presenterDropdownId(meetingId);
  const hasPresenters = presenters in (dropdownDefinitions(docId));
  const row = table.insertTableRow(at);
  for (let c = 0; c < MEETING_COLUMNS; c++) row.appendTableCell();
  for (let c = 0; c < MEETING_COLUMNS; c++) writeCell(row.getCell(c), [[]]);
  writeCell(row.getCell(COL.duration), [[[String(NEW_ITEM_MINUTES), MUTED]]]);
  if (hasPresenters) writeCell(row.getCell(COL.presenter), [[[token('presenter', presenters)]]]);
  writeCell(row.getCell(COL.description), [[['Topic', BOLD]]]);
  writeCell(row.getCell(COL.notes), [[[token('bullet')]]]);
  row.getCell(COL.followUp).setBackgroundColor(COLOR.followUp);

  const messages = recalculate(table);
  doc.saveAndClose();
  replaceTokens(docId);
  return {done: true, title: 'Item added', messages};
}

// Adds the circle's next meeting at the top of MEETINGS: the first timed
// event on the circle's calendar after the day of the newest meeting in the
// doc (or after now, if there's none). Refuses while the newest meeting
// hasn't started yet. The new meeting's date, start, and length come from
// the event; its Place is the circle's Zoom room; its attendance list, and
// its Presenter dropdowns' choices, are the circle's members; its Minutes
// link to the previous meeting; its Next meeting is the event after; and
// the previous meeting's Action Items Recap is copied to its Action Items
// Accountability. The links that change a meeting are removed from the
// meetings before it, which are then no longer to be changed.
function addMeeting(docId) {
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

  const content = Docs.Documents.get(docId).body.content;
  const newest = content.find(se => se.table && apiMeetingId(se));
  const newestDate = newest ? apiMeetingDate(newest) : null;
  if (newestDate) {
    const startText = headerTimeText(apiCellText(newest.table.tableRows[1].tableCells[0]));
    const startsAt = Utilities.parseDate(newestDate, TIME_ZONE, 'yyyy-MM-dd').getTime() +
      (parseTime(startText) || 0) * 60000;
    if (startsAt > Date.now()) {
      return {done: false, title: 'Next meeting not started yet', messages: [
        'The newest meeting in the agenda, on ' + newestDate + (startText ? ' at ' + startText : '') +
        ', hasn\'t started yet. The one after it can be added once it has.']};
    }
  }
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
    start: Utilities.formatDate(start, TIME_ZONE, 'H:mm'),
    hours: formatHours((end - start) / 60000),
    place: circle.zoomUrl,
    attendees: circleMemberNames(circle),
    next: following ? Utilities.formatDate(new Date(following.start.dateTime), TIME_ZONE,
      'EEE yyyy-MM-dd, H:mm') : 'Not on the calendar yet',
  };

  const doc = DocumentApp.openById(docId);
  const body = firstTabBody(doc);
  const previous = newest ? findMeeting(body, apiMeetingId(newest)) : null;
  if (previous) {
    const bookmark = doc.addBookmark(doc.newPosition(previous.getCell(1, 0).getChild(0), 0));
    details.minutes = [['Previous meeting (' + newestDate + ')',
      {[A.LINK_URL]: '#bookmark=' + bookmark.getId()}]];
    details.carryOver = recapItems(previous);
  }
  const at = newMeetingPosition(body);
  const meetingId = meetingTable(body, at, docId, details);
  body.insertParagraph(at + 1, '');
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() !== DocumentApp.ElementType.TABLE) continue;
    const links = linksRowIndex(el.asTable());
    if (links >= 0 && meetingIdOf(el.asTable()) !== meetingId) el.asTable().removeRow(links);
  }
  doc.saveAndClose();
  if (details.attendees.length) {
    Docs.Documents.batchUpdate({requests: [presenterDropdownRequest(meetingId, details.attendees)]},
      docId);
  }
  replaceTokens(docId);
  finishMeeting(docId, meetingId);
  return {done: true, title: 'Meeting added',
    messages: ['Added the meeting on ' + details.date + ' at ' + details.start + '.']};
}

// Where a new meeting goes: just after the "+ New meeting" link, or the
// MEETINGS heading, or else before the first meeting.
function newMeetingPosition(body) {
  let heading = -1;
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() === DocumentApp.ElementType.TABLE) {
      if (meetingIdOf(el.asTable())) return heading >= 0 ? heading + 1 : i;
      continue;
    }
    if (el.getType() !== DocumentApp.ElementType.PARAGRAPH) continue;
    const text = el.asParagraph().editAsText();
    if (text.getText().trim().toUpperCase() === 'MEETINGS') heading = i;
    for (const j of text.getTextAttributeIndices()) {
      if (/[?&]action=meeting\b/.test(text.getLinkUrl(j) || '')) return i + 1;
    }
  }
  return heading >= 0 ? heading + 1 : body.getNumChildren() - 1;
}

// The non-empty items under a meeting's Action Items Recap.
function recapItems(table) {
  for (let r = MEETING_HEADER_ROWS; r < table.getNumRows(); r++) {
    const cell = table.getCell(r, COL.description);
    if (!cell.getText().trim().startsWith('Action Items Recap')) continue;
    const items = [];
    for (let i = 1; i < cell.getNumChildren(); i++) {
      const el = cell.getChild(i);
      const type = el.getType();
      if ((type === DocumentApp.ElementType.LIST_ITEM || type === DocumentApp.ElementType.PARAGRAPH) &&
          el.getText().trim()) {
        items.push(el);
      }
    }
    return items;
  }
  return [];
}

// The date ('yyyy-MM-dd') of the chip in a Docs API meeting table's first
// row, or null.
function apiMeetingDate(se) {
  for (const cell of se.table.tableRows[0].tableCells) {
    for (const part of cell.content) {
      for (const pe of (part.paragraph && part.paragraph.elements) || []) {
        if (pe.dateElement) return pe.dateElement.dateElementProperties.timestamp.slice(0, 10);
      }
    }
  }
  return null;
}

// The text of a Docs API table cell.
function apiCellText(cell) {
  return cell.content.filter(se => se.paragraph).map(se => paraText(se.paragraph)).join('');
}

// The doc's first tab's dropdowns' lists of choices, by ID.
function dropdownDefinitions(docId) {
  return Docs.Documents.get(docId, {includeTabsContent: true}).tabs[0].documentTab
    .dropdownDefinitions || {};
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
  const startText = headerTimeText(table.getCell(1, 0).getText());
  const start = parseTime(startText);
  if (start === null) {
    return ['Couldn\'t read the start time "' + startText + '" after "Time"; ' +
      'nothing was changed. Write it like 19:30 or 7:30 pm.'];
  }
  const lengthCell = durationCell(table);
  const lengthText = lengthCell ? lengthCell.getText().trim() : '';
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
  const overrun = times.closingMinutes < 0;
  writeMuted(table.getCell(closing, COL.duration), String(times.closingMinutes),
    {[A.ITALIC]: true, [A.FOREGROUND_COLOR]: overrun ? PROBLEM_COLOR : COLOR.muted});
  writeMuted(table.getCell(last, COL.time), formatTime(times.end));
  if (overrun) {
    messages.push('The items before the closing round run ' + -times.closingMinutes +
      ' minutes past the end of the meeting, so its duration is negative.');
  }
  return messages;
}

function writeMuted(cell, text, extra) {
  writeCell(cell, [[[text, {...MUTED, ...extra}]]]);
}

// The cell holding the meeting's length, beside "Duration" in the admin
// details (a table inside one of the item rows), or null.
function durationCell(table) {
  for (let r = MEETING_HEADER_ROWS; r < table.getNumRows(); r++) {
    if (table.getRow(r).getNumCells() <= COL.description) continue;
    const cell = table.getCell(r, COL.description);
    for (let i = 0; i < cell.getNumChildren(); i++) {
      const child = cell.getChild(i);
      if (child.getType() !== DocumentApp.ElementType.TABLE) continue;
      const inner = child.asTable();
      for (let ir = 0; ir < inner.getNumRows(); ir++) {
        const row = inner.getRow(ir);
        if (row.getNumCells() > 1 && row.getCell(0).getText().trim().startsWith('Duration')) {
          return row.getCell(1);
        }
      }
    }
  }
  return null;
}

function itemTitle(table, r) {
  return snippet(table.getCell(r, COL.description).getText().split('\n')[0], 40);
}

// ── Finding meetings ─────────────────────────────────────────────────────────

// The top-level table whose links carry this meeting ID, or null.
function findMeeting(body, meetingId) {
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() === DocumentApp.ElementType.TABLE && meetingIdOf(el.asTable()) === meetingId) {
      return el.asTable();
    }
  }
  return null;
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
