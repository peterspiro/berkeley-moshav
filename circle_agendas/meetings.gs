/**
 * Meetings
 *
 * What the web app (web_app.gs) does to a meeting in an agenda doc: update
 * its times, and add an agenda item. A meeting is one table (see
 * meetingTable in build_template.gs): MEETING_HEADER_ROWS rows of date,
 * time and place, and "Agenda"; then one row per item, with its start time,
 * its duration in minutes, its content, and its type, and among them (just
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
    if (table.getCell(r, 2).getText().trim().startsWith('Action Items Recap')) at = r;
  }
  if (at < 0) at = closing;
  const row = table.insertTableRow(at);
  for (let c = 0; c < 4; c++) row.appendTableCell();
  writeCell(row.getCell(0), [[]]);
  writeCell(row.getCell(1), [[[String(NEW_ITEM_MINUTES), MUTED]]]);
  writeCell(row.getCell(2), [[['Topic', BOLD]], [[token('bullet')]]]);
  writeCell(row.getCell(3), [[[token('dropdown', 'outcome', 'decision')]]]);
  alignCell(row.getCell(3), DocumentApp.HorizontalAlignment.RIGHT);

  const messages = recalculate(table);
  doc.saveAndClose();
  replaceTokens(docId);
  return {done: true, title: 'Item added', messages};
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
      'nothing was changed. Write it like 1.5.'];
  }

  const last = table.getNumRows() - 1;
  const closing = last - 1;
  const links = linksRowIndex(table);
  const itemRows = [];
  for (let r = MEETING_HEADER_ROWS; r < closing; r++) if (r !== links) itemRows.push(r);
  const durations = [];
  for (const r of itemRows) {
    const cell = table.getCell(r, 1);
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
  times.starts.forEach((t, i) => writeMuted(table.getCell(itemRows[i], 0), formatTime(t)));
  writeMuted(table.getCell(closing, 0), formatTime(times.closingStart));
  const overrun = times.closingMinutes < 0;
  writeMuted(table.getCell(closing, 1), String(times.closingMinutes),
    {[A.ITALIC]: true, [A.FOREGROUND_COLOR]: overrun ? PROBLEM_COLOR : COLOR.muted});
  writeMuted(table.getCell(last, 0), formatTime(times.end));
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
    const cell = table.getCell(r, 2);
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
  return snippet(table.getCell(r, 2).getText().split('\n')[0], 40);
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
  return r < 0 ? null : linkedMeetingId(table.getCell(r, 2));
}

// The index of the row holding a meeting's links, or -1.
function linksRowIndex(table) {
  for (let r = MEETING_HEADER_ROWS; r < table.getNumRows(); r++) {
    if (table.getRow(r).getNumCells() > 2 && linkedMeetingId(table.getCell(r, 2))) return r;
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
