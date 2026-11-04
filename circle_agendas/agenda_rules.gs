/**
 * Agenda Rules
 *
 * The arithmetic of a meeting's agenda, with no Apps Script calls, so it
 * can be tested under Node (tests/test_circle_agenda_rules.py). Times are
 * minutes since midnight.
 */

// Each item's start time, given the meeting's start, the durations of the
// items before the closing round, and the meeting's total length. The
// closing round gets whatever time is left: closingMinutes is negative
// when the other items already run past the end.
function scheduleTimes(startMinutes, durations, totalMinutes) {
  const starts = [];
  let t = startMinutes;
  for (const d of durations) {
    starts.push(t);
    t += d;
  }
  const end = startMinutes + totalMinutes;
  return {starts, closingStart: t, closingMinutes: end - t, end};
}

// "7:30" from 1170 (19:30): a 12-hour time, without AM or PM, which the
// agenda's Time column has no room for; wraps past midnight.
function formatTime(minutes) {
  const m = ((minutes % 1440) + 1440) % 1440;
  return ((Math.floor(m / 60) + 11) % 12 + 1) + ':' + String(m % 60).padStart(2, '0');
}

// 1170 from "19:30" or "7:30 pm"; null if it isn't a time.
function parseTime(text) {
  const m = /^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*m?\.?\s*$/i.exec(text || '');
  if (!m) return null;
  let hours = Number(m[1]);
  const minutes = Number(m[2] || 0);
  if (minutes > 59) return null;
  if (m[3]) {
    if (hours < 1 || hours > 12) return null;
    hours = (hours % 12) + (m[3].toLowerCase() === 'p' ? 12 : 0);
  } else if (hours > 23 || m[2] === undefined) {
    return null;
  }
  return hours * 60 + minutes;
}

// A whole number of minutes from "20" or "20 min"; null otherwise.
function parseMinutes(text) {
  const m = /^\s*(\d{1,3})\s*(?:m|min|mins|minutes)?\.?\s*$/i.exec(text || '');
  return m ? Number(m[1]) : null;
}

// Minutes from a number of hours, "1.5" or "1.5 h", or from hours and
// minutes, "1:30"; null otherwise.
function parseHours(text) {
  const clock = /^\s*(\d+):([0-5]\d)\s*$/.exec(text || '');
  if (clock) return Number(clock[1]) * 60 + Number(clock[2]);
  const m = /^\s*(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)?\.?\s*$/i.exec(text || '');
  return m ? Math.round(Number(m[1]) * 60) : null;
}

// The time typed after "Time" in a meeting's
// "Time 19:30   Duration (h:mm or h.hh) 1.5   Place …" line.
function headerTimeText(line) {
  const m = /Time\s*(.*?)\s*(?:Duration\b|Place\b|$)/i.exec(line || '');
  return m ? m[1] : '';
}

// The meeting's length typed after "Duration" and its parenthesized hint
// (in older meetings, "(hours)") in the same line.
function headerHoursText(line) {
  const m = /Duration\s*(?:\([^)]*\))?\s*(.*?)\s*(?:Place\b|$)/i.exec(line || '');
  return m ? m[1] : '';
}

// The meeting ID in one of a meeting's web app links, or null.
function meetingIdFromUrl(url) {
  const m = /[?&]meeting=([\w-]+)/.exec(url || '');
  return m ? m[1] : null;
}

// ── Announcing a meeting's agenda ────────────────────────────────────────────

// The address of Gmail's compose window, which an agenda's "✉ Announce
// agenda" link opens, filled in (see announceUrl).
const GMAIL_COMPOSE_URL = 'https://mail.google.com/mail/?view=cm&fs=1';
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov',
  'Dec'];

// Whether a link's address is an "✉ Announce agenda" link's.
function isAnnounceUrl(url) {
  return String(url || '').startsWith(GMAIL_COMPOSE_URL);
}

// "Dec 4" (or, with the weekday, "Fri Dec 4") from '2026-12-04'; '' if it
// isn't such a date.
function formatShortDate(date, withWeekday) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || '');
  if (!m) return '';
  const day = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return (withWeekday ? WEEKDAYS[day.getUTCDay()] + ' ' : '') +
    MONTH_NAMES[day.getUTCMonth()] + ' ' + day.getUTCDate();
}

// The address of an "✉ Announce agenda" link: Gmail's compose window,
// addressed to the circle's email list (to), saying to the circle
// (circleName) that the agenda of its meeting on date ('yyyy-MM-dd'; left
// out if it can't be read) is ready, with a link to it (agendaUrl, the
// meeting's tab), and its Zoom room (zoomUrl), if it has one. The time is
// left out, as it may change after the link is written.
function announceUrl({to, circleName, date, zoomUrl, agendaUrl}) {
  const subjectDate = formatShortDate(date, false);
  const bodyDate = formatShortDate(date, true);
  const subject = 'Agenda ready: ' + (subjectDate ? subjectDate + ' ' : '') + circleName + ' meeting';
  const lines = [
    'Hello ' + circleName + ',', '',
    'The agenda for our ' + (bodyDate ? bodyDate + ' ' : '') + 'meeting is ready:', agendaUrl, '',
    'Please review it, and let me know of any change requests as soon as possible.', '',
    'Thank you!',
  ];
  if (zoomUrl) lines.push('', 'Zoom: ' + zoomUrl);
  return GMAIL_COMPOSE_URL + '&to=' + encodeURIComponent(to) +
    '&su=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(lines.join('\n'));
}

// "1.5" from 90 minutes, "2" from 120: a meeting's length as written after
// its "Duration".
function formatHours(minutes) {
  return String(Math.round(minutes / 60 * 100) / 100);
}

// ── Roles ────────────────────────────────────────────────────────────────────

// A role dropdown's choices (the Roles table's Name, a meeting's Facilitator
// and Secretary): ROLE_PLACEHOLDER, which new dropdowns show, then each of
// names, then each of holders not among them (so a holder typed by hand
// can still be chosen), then ROLE_VACANT; as Docs API DropdownOptions,
// at most Docs' 50.
// The placeholder is a dash, since a choice can't be blank (docs built
// before showed "Name"); it and Vacant always have the same IDs.
const ROLE_PLACEHOLDER = '—';
const ROLE_PLACEHOLDER_ID = 'dropdownItem.none';
const ROLE_VACANT = 'Vacant';
const ROLE_VACANT_ID = 'dropdownItem.vacant';
function roleChoices(names, holders) {
  const values = [];
  for (const n of [...(names || []), ...(holders || [])]) {
    const v = String(n || '').trim();
    if (v && v !== ROLE_PLACEHOLDER && v !== ROLE_VACANT && !values.includes(v)) values.push(v);
  }
  return [
    {optionId: ROLE_PLACEHOLDER_ID, displayValue: ROLE_PLACEHOLDER},
    ...values.slice(0, 48).map((v, i) => ({optionId: 'dropdownItem.m' + i, displayValue: v})),
    {optionId: ROLE_VACANT_ID, displayValue: ROLE_VACANT},
  ];
}

// The ID of the choice (from roleChoices) showing holder, or the
// placeholder's.
function roleOptionId(options, holder) {
  const v = String(holder || '').trim();
  const found = options.find(o => o.displayValue === v);
  return found ? found.optionId : ROLE_PLACEHOLDER_ID;
}

// The Roles dropdown's choices brought up to date with the circle's
// members (names), from its choices now (existing, Docs API
// DropdownOptions) and the IDs of the choices its chips show (selected):
// as roleChoices gives, but with the members who've left, and choices
// added by hand, kept only while a chip shows them, and each choice that
// stays keeping its ID, so the chips showing it still do. The placeholder
// and Vacant keep their IDs whatever they showed (so an old "Name"
// placeholder becomes the dash). Returns
// {options, replacements}: replacements, for updateDropdownDefinition-
// PropertiesRequest, gives the placeholder to any chip whose choice is
// gone (only past Docs' limit of 50).
function updatedRoleChoices(existing, names, selected) {
  const fixed = [ROLE_PLACEHOLDER_ID, ROLE_VACANT_ID];
  const others = existing.filter(o => !fixed.includes(o.optionId));
  const shown = others.filter(o => (selected || []).includes(o.optionId)).map(o => o.displayValue);
  const fresh = roleChoices(names, shown);
  const idOf = {};
  for (const o of others) if (!(o.displayValue in idOf)) idOf[o.displayValue] = o.optionId;
  // Every ID there is now is taken, so no new choice gets the ID of one
  // that's gone (which a chip might still show).
  const used = new Set(existing.map(o => o.optionId));
  let next = 0;
  const newId = () => {
    while (used.has('dropdownItem.m' + next)) next++;
    used.add('dropdownItem.m' + next);
    return 'dropdownItem.m' + next;
  };
  const options = fresh.map(o => fixed.includes(o.optionId) ? o
    : {optionId: idOf[o.displayValue] || newId(), displayValue: o.displayValue});
  const placeholder = ROLE_PLACEHOLDER_ID;
  const replacements = {};
  for (const id of selected || []) {
    if (!options.some(o => o.optionId === id)) replacements[id] = placeholder;
  }
  return {options, replacements};
}

// Whether two lists of dropdown choices are the same: the same IDs and
// values, in the same order.
function sameChoices(a, b) {
  const key = list => JSON.stringify(list.map(o => [o.optionId, o.displayValue]));
  return key(a) === key(b);
}

// The date ('yyyy-MM-dd') a date chip shows, from its text: yyyy-mm-dd,
// m/d/yyyy (what Docs shows, in the US, once someone picks a new date), or
// "Nov 3, 2026" (the Docs API's default format); or null.
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
function dateFromDisplayText(text) {
  const t = String(text || '').trim();
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t);
  const us = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(t);
  const named = /^([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})$/i.exec(t);
  const month = named ? MONTHS.indexOf(named[1].toLowerCase()) + 1 : 0;
  const [y, m, d] = iso ? [iso[1], iso[2], iso[3]] : us ? [us[3], us[1], us[2]]
    : month ? [named[3], String(month), named[2]] : [];
  if (!y || Number(m) < 1 || Number(m) > 12 || Number(d) < 1 || Number(d) > 31) return null;
  return y + '-' + m.padStart(2, '0') + '-' + d.padStart(2, '0');
}

// Whether a term ending on date ('yyyy-MM-dd') is over, on today
// ('yyyy-MM-dd'); false if date can't be read.
function termEnded(date, today) {
  return /^\d{4}-\d{2}-\d{2}$/.test(date || '') && date < today;
}

// ── A circle's Google Group footer ───────────────────────────────────────────

// Must match util/google_group_footer.py (and group_meetings/event_rules.gs,
// which reads the same footer).
const FOOTER_MARKER = '--- Auto-managed links ---';
const FOOTER_DRIVE_ITEM_RE =
  /(?:Google Docs folder|Shared Drive):\s*https:\/\/drive\.google\.com\/drive\/folders\/([\w-]+)/;
const FOOTER_CALENDAR_RE =
  /Google calendar:\s*https:\/\/calendar\.google\.com\/calendar\/u\/0\?cid=([A-Za-z0-9%+\/_=-]+)/;
const FOOTER_ZOOM_RE = /Zoom meeting:\s*(https:\/\/\S+)/;
const FOOTER_NAME_RE = /^(.+)'s:$/m;

// The links in a group's footer that a circle's agenda doc uses:
// {name, driveItemId, calendarId, zoomUrl}, each null if absent. Only the
// auto-managed block is read.
function parseCircleFooter(footerText) {
  const text = footerText || '';
  const idx = text.indexOf(FOOTER_MARKER);
  if (idx < 0) return {name: null, driveItemId: null, calendarId: null, zoomUrl: null};
  const block = text.slice(idx);
  const match = re => { const m = re.exec(block); return m ? m[1] : null; };
  const cid = match(FOOTER_CALENDAR_RE);
  return {
    name: match(FOOTER_NAME_RE),
    driveItemId: match(FOOTER_DRIVE_ITEM_RE),
    calendarId: cid ? base64ToString(decodeURIComponent(cid)) : null,
    zoomUrl: match(FOOTER_ZOOM_RE),
  };
}

const BASE64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

// Decodes base64 (standard or URL-safe, padding optional), or null.
function base64ToString(text) {
  const s = text.replace(/-/g, '+').replace(/_/g, '/').replace(/=+$/, '');
  let bits = 0;
  let value = 0;
  let out = '';
  for (const ch of s) {
    const v = BASE64_ALPHABET.indexOf(ch);
    if (v < 0) return null;
    value = (value << 6) | v;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out += String.fromCharCode((value >> bits) & 0xff);
    }
  }
  return out;
}

// ── About this circle ────────────────────────────────────────────────────────

// About this circle's rows, in order (aboutTable, build_template.gs, makes
// them).
const CALENDAR_ROW_LABEL = 'Circle calendar';
// The Remind before meetings row, which group_meetings/group_meetings.gs reads
// (its first line) to know when to email the circle a reminder of each
// meeting (see parseReminderLeads in group_meetings/event_rules.gs).
const REMINDER_ROW_LABEL = 'Remind before meetings';
// The row's value cell holds only the setting; how to write it is the
// grey second line of its label cell.
const REMINDER_DEFAULT_TEXT = '24 hours';
const REMINDER_HINT = 'e.g. 2 days; 90 minutes; 2 days, 1 hour; none';
const ABOUT_LABELS = ['Aims & Domain', 'Policies folder', 'Zoom room', CALENDAR_ROW_LABEL,
  REMINDER_ROW_LABEL, 'Circle email address', 'Other Key Links', 'Google Drive Folder',
  'Parent Circle', 'Sub-Circles'];
// Rows added since docs were first built, which older docs may lack
// (addNewAboutRowsToCircleDocs, circle_docs.gs, adds them).
const ABOUT_ADDED_LATER = [CALENDAR_ROW_LABEL, REMINDER_ROW_LABEL];
// The label of the row each of ABOUT_ADDED_LATER goes just below.
const ABOUT_ADDED_AFTER = {[CALENDAR_ROW_LABEL]: 'Zoom room', [REMINDER_ROW_LABEL]: CALENDAR_ROW_LABEL};

// The link in a group's footer to its Google calendar, which offers to add
// the calendar to the clicker's own (as calendar_url, in
// util/google_group_footer.py, writes it): the ID, base64-encoded without
// padding, percent-encoded.
function calendarUrl(calendarId) {
  const bytes = unescape(encodeURIComponent(calendarId));
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const n = (bytes.charCodeAt(i) << 16) | ((bytes.charCodeAt(i + 1) || 0) << 8) |
      (bytes.charCodeAt(i + 2) || 0);
    const chars = Math.min(4, Math.ceil((bytes.length - i) * 4 / 3));
    for (let k = 0; k < chars; k++) out += BASE64_ALPHABET[(n >> (18 - 6 * k)) & 63];
  }
  return 'https://calendar.google.com/calendar/u/0?cid=' + encodeURIComponent(out);
}

// Where to insert the rows ABOUT_ADDED_LATER that a doc's About this circle
// (labels, in order, from aboutLabel) lacks: [{label, index}], in the order
// to insert them, each just below the row it follows; or {error} if a row
// it follows is missing.
function aboutRowsToAdd(labels) {
  const now = labels.slice();
  const adds = [];
  for (const label of ABOUT_ADDED_LATER) {
    if (now.includes(label)) continue;
    const after = now.indexOf(ABOUT_ADDED_AFTER[label]);
    if (after < 0) return {error: 'it has no ' + ABOUT_ADDED_AFTER[label] + ' row'};
    now.splice(after + 1, 0, label);
    adds.push({label, index: after + 1});
  }
  return adds;
}
// What the Google Drive Folder row was called in docs built before.
const OLD_DRIVE_LABELS = ['Circle Google Drive Folder', 'Circle Drive Folder'];
// The rows people fill in by hand, which reordering never moves (they may
// hold smart chips, which Apps Script can't copy).
const ABOUT_HAND_FILLED = ['Aims & Domain', 'Policies folder', 'Other Key Links'];

// A row's label (its label cell's first line), with old names of the Drive
// folder row given their new one.
function aboutLabel(text) {
  const label = text.split('\n')[0].trim();
  return OLD_DRIVE_LABELS.includes(label) ? 'Google Drive Folder' : label;
}

// How to put About this circle's rows, labelled labels (in their order,
// from aboutLabel), in ABOUT_LABELS' order (leaving out any rows
// ABOUT_ADDED_LATER it lacks): {move, order}, the labels of the rows to
// take out and put back in their places in order (none if they're in order
// already), or {error}, why it can't be done.
function aboutReorder(labels) {
  const target = ABOUT_LABELS.filter(l => labels.includes(l) || !ABOUT_ADDED_LATER.includes(l));
  const missing = target.filter(l => !labels.includes(l));
  const extra = labels.filter(l => !target.includes(l));
  if (missing.length || extra.length || labels.length !== target.length) {
    return {error: 'its rows aren\'t the expected ones' +
      (missing.length ? '; missing: ' + missing.join(', ') : '') +
      (extra.length ? '; unexpected: ' + extra.join(', ') : '')};
  }
  if (labels.join('|') === target.join('|')) return {move: [], order: target};
  const kept = labels.filter(l => ABOUT_HAND_FILLED.includes(l));
  if (kept.join('|') !== ABOUT_HAND_FILLED.join('|')) {
    return {error: 'its ' + ABOUT_HAND_FILLED.join(', ') + ' rows are in another order'};
  }
  return {move: labels.filter(l => !ABOUT_HAND_FILLED.includes(l)), order: target};
}

// Bulleted and numbered lists' indents, in points: at the outer level, the
// bullet 0.08" from the margin and the text a 0.19" hanging indent beyond
// it (Format → Bullets & numbering → Indentation options: left 0.08",
// hanging 0.19"); each nested level LIST_LEVEL_STEP_PT further in. Docs
// keeps a list's own indent for each level, which scripts can't change, so
// these are set on each paragraph.
const LIST_INDENT_PT = 0.08 * 72;
const LIST_HANGING_PT = 0.19 * 72;
const LIST_LEVEL_STEP_PT = 0.25 * 72;
function listIndents(level) {
  const firstLine = LIST_INDENT_PT + (level || 0) * LIST_LEVEL_STEP_PT;
  return {firstLine, start: firstLine + LIST_HANGING_PT};
}

// The list paragraphs (each {start, end, level, firstLine, indentStart},
// its range, nesting level and indents in points, if set) whose indents
// aren't listIndents' for their level, each with the indents it should
// have: [{start, end, firstLine, indentStart}].
function listIndentFixes(paragraphs) {
  const near = (a, b) => typeof a === 'number' && Math.abs(a - b) < 0.01;
  const fixes = [];
  for (const p of paragraphs) {
    const want = listIndents(p.level);
    if (near(p.firstLine, want.firstLine) && near(p.indentStart, want.start)) continue;
    fixes.push({start: p.start, end: p.end, firstLine: want.firstLine, indentStart: want.start});
  }
  return fixes;
}
