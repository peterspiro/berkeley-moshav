/**
 * Group Meetings — the recording links' rules (no Apps Script services), so
 * they can be tested under Node (tests/test_recording_rules.py), with
 * event_rules.gs, whose helpers they use.
 *
 *   - reading a recording from Zoom's API, and whether it's finished;
 *   - deciding which sheet lists it: a group's, or the community one
 *     (categorizeRecording);
 *   - finding the calendar event it came from;
 *   - the "Recording:" line for an event's description, and a sheet's rows;
 *   - the email about recordings that couldn't be categorized;
 *   - the months a backfill steps through.
 */

// The destination of a community-wide meeting's recording.
const COMMUNITY = 'community';
// Titles holding one of these (in any case) are community-wide meetings.
const COMMUNITY_KEYWORDS = ['listening session', 'ask me anything'];
const RECORDING_TEXT = 'Recording:';
// A recording belongs to an event that starts at most this long after the
// recording does (people join early), and hasn't ended when it starts.
const EARLY_START_MS = 45 * 60 * 1000;
const COMMUNITY_SHEET_NAME = 'Community Meeting Recordings';
const RECORDINGS_TAB = 'Recordings';
const UNASSIGNED_TAB = 'Unassigned';
// Each tab's columns; the last, Zoom's ID for the recording, is hidden.
const RECORDINGS_HEADINGS = ['When', 'Title', 'Recording', 'Passcode', 'Length', 'Zoom topic',
                             'Recording ID'];
const UNASSIGNED_HEADINGS = ['Group', 'When', 'Title', 'Recording', 'Passcode', 'Length',
                             'Zoom topic', 'Zoom user', 'Why', 'Recording ID'];
const UNASSIGNED_WHY_COLUMN = UNASSIGNED_HEADINGS.indexOf('Why');

/**
 * The line an event's description gets for a recording, written at its top
 * (below any other recordings' lines), with the description's kind of line
 * break; null if the description already links the recording.
 */
function addRecordingLine(description, rec) {
  const text = description || '';
  if (!rec.url || text.includes(shareBase(rec.url))) return null;
  const br = /<[a-z][^>]*>/i.test(text) ? '<br>' : '\n';
  const line = recordingLine(rec);
  if (!text) return line;
  // Another recording's line is already at the top: this one goes with it.
  if (text.startsWith(RECORDING_TEXT)) return `${line}${br}${text}`;
  return `${line}${br}${br}${text}`;
}

/**
 * Which sheet lists a recording: { dest, rule }, dest being a group's
 * address, COMMUNITY, or null (uncategorized, with rule saying why). The
 * first rule that decides:
 *   0. a choice made on the Unassigned tab (ctx.choices, by recording ID);
 *   1. 'room': held in a group's room (a community calendar's: COMMUNITY);
 *   2. 'guests': its event invites an audience (COMMUNITY), or else
 *      exactly one group (its address, an alias, or its calendar);
 *   3. 'recurring title': a title (the event's, then the Zoom topic) is
 *      the title of a recurring meeting on group or community calendars
 *      (ctx.recurringTitles, from recurringTitleIndex, or a function
 *      returning it, called only if needed); one on calendars leading to
 *      different sheets leaves it uncategorized;
 *   4. 'group name': a title holds a group's name (a community calendar's
 *      admin group's: its calendar's name) as whole words; the longest
 *      wins, and a tie between groups decides nothing;
 *   5. 'keyword': a title holds a COMMUNITY_KEYWORDS phrase.
 * ctx: { groups (as group_meetings.gs stores them), event (the recording's
 * calendar event, or null), recurringTitles, choices }.
 */
function categorizeRecording(rec, ctx) {
  const groups = ctx.groups || [];
  const destOf = g => (g.audience ? COMMUNITY : g.email);
  const chosen = (ctx.choices || {})[rec.id];
  if (chosen) return { dest: chosen, rule: 'chosen on the Unassigned tab' };

  const room = groups.find(g => g.zoomUrl && meetingIdFromUrl(g.zoomUrl) === rec.meetingId);
  if (room) return { dest: destOf(room), rule: 'room' };

  const event = ctx.event || null;
  if (event) {
    const guests = new Set((event.attendees || []).map(a => lower(a.email)));
    if (groups.some(g => g.audience && guests.has(lower(g.audience)))) {
      return { dest: COMMUNITY, rule: 'guests' };
    }
    const invited = new Set(groups
      .filter(g => [g.email, g.calendarId, ...(g.aliases || [])].some(a => a && guests.has(lower(a))))
      .map(destOf));
    if (invited.size === 1) return { dest: [...invited][0], rule: 'guests' };
  }

  const titles = [...new Set([event && event.summary, rec.topic].filter(Boolean))];
  let index = null;
  for (const title of titles) {
    if (index === null) {
      index = (typeof ctx.recurringTitles === 'function' ? ctx.recurringTitles()
        : ctx.recurringTitles) || {};
    }
    const dests = index[normalizeTitle(title)];
    if (!dests) continue;
    if (dests.length === 1) return { dest: dests[0], rule: 'recurring title' };
    return { dest: null, rule: `"${title}" is the title of recurring meetings that go to ` +
                               'different sheets' };
  }

  for (const title of titles) {
    const words = ` ${titleWords(title)} `;
    let best = null, bestLength = 0, tie = false;
    for (const g of groups) {
      const name = titleWords(g.audience ? g.calendarName : g.name);
      if (!name || !words.includes(` ${name} `)) continue;
      if (name.length > bestLength) {
        best = destOf(g); bestLength = name.length; tie = false;
      } else if (name.length === bestLength && destOf(g) !== best) {
        tie = true;
      }
    }
    if (best && !tie) return { dest: best, rule: 'group name' };
  }

  for (const title of titles) {
    const text = normalizeTitle(title);
    if (COMMUNITY_KEYWORDS.some(k => text.includes(k))) return { dest: COMMUNITY, rule: 'keyword' };
  }
  return { dest: null, rule: 'no rule matched its title' };
}

/** "1:05", the length of a meeting of this many minutes. */
function formatLength(minutes) {
  const m = Math.max(0, Math.round(minutes || 0));
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
}

/**
 * The months from fromDate ('yyyy-mm-dd') to the month holding nowMs, as
 * [{ month: 'yyyy-mm', from, to }] with from and to the first and last
 * days ('yyyy-mm-dd'), as Zoom's recording list takes them (at most a
 * month at a time), oldest first. In UTC, as Zoom's dates are.
 */
function monthWindows(fromDate, nowMs) {
  const [y0, m0] = fromDate.split('-').map(Number);
  const now = new Date(nowMs);
  const out = [];
  for (let y = y0, m = m0; y < now.getUTCFullYear() ||
       (y === now.getUTCFullYear() && m <= now.getUTCMonth() + 1);) {
    const mm = String(m).padStart(2, '0');
    const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
    out.push({ month: `${y}-${mm}`, from: `${y}-${mm}-01`, to: `${y}-${mm}-${last}` });
    if (++m > 12) { m = 1; y++; }
  }
  return out;
}

/** A title as compared with recurring meetings': its text, in lower case,
 * with runs of spaces (non-breaking ones too) made one, and trimmed. */
function normalizeTitle(title) {
  return lower(plainText(title || '')).replace(/\s+/g, ' ').trim();
}

/**
 * The destination someone typed in an Unassigned row's Group column: a
 * group's name or address (or alias), a community calendar's name or its
 * admin group's, or "community"; null if it's none of those.
 */
function overrideDestination(text, groups) {
  const t = normalizeTitle(text);
  if (!t) return null;
  if (t === COMMUNITY || t === 'community-wide') return COMMUNITY;
  for (const g of groups) {
    const names = [g.email, g.name, ...(g.aliases || []), g.calendarName].filter(Boolean);
    if (names.some(n => normalizeTitle(n) === t)) return g.audience ? COMMUNITY : g.email;
  }
  return null;
}

/**
 * The calendar event a recording came from, among events (Calendar API
 * events, from any calendars): a timed event linking its Zoom meeting, that
 * starts at most EARLY_START_MS after the recording does and hasn't ended
 * by then; the one starting closest to it. null if none.
 */
function recordingEvent(rec, events) {
  let best = null, bestGap = Infinity;
  for (const e of events || []) {
    if (e.status === 'cancelled' || !(e.start || {}).dateTime || !(e.end || {}).dateTime) continue;
    const start = Date.parse(e.start.dateTime), end = Date.parse(e.end.dateTime);
    if (rec.start < start - EARLY_START_MS || rec.start >= end) continue;
    if (!zoomIdsInEvent(e).includes(rec.meetingId)) continue;
    const gap = Math.abs(start - rec.start);
    if (gap < bestGap) { best = e; bestGap = gap; }
  }
  return best;
}

/**
 * A recording as Zoom's API gives it (an item of a user's recordings, with
 * its share link and passcode), hosted by the Zoom user host: { id (Zoom's
 * UUID for this session), meetingId, topic, start (ms), minutes, host, url,
 * passcode }. The link carries the passcode (Zoom's encoded form), when Zoom
 * gives one, so a click plays it without asking.
 */
function recordingFromZoom(m, host) {
  let url = m.share_url || '';
  const play = m.recording_play_passcode;
  if (url && play && !/[?&]pwd=/.test(url)) {
    url += (url.includes('?') ? '&' : '?') + 'pwd=' + encodeURIComponent(play);
  }
  return {
    id: m.uuid, meetingId: String(m.id), topic: m.topic || '',
    start: Date.parse(m.start_time), minutes: m.duration || 0, host: lower(host),
    url, passcode: m.password || '',
  };
}

/** "Recording: <link> (passcode …)". */
function recordingLine(rec) {
  return `${RECORDING_TEXT} ${rec.url}` + (rec.passcode ? ` (passcode ${rec.passcode})` : '');
}

/**
 * True if every file of a recording (an item of Zoom's list) has finished
 * processing, so its link works; a file Zoom gives no status counts as
 * finished.
 */
function recordingReady(m) {
  const files = m.recording_files || [];
  return files.length > 0 && files.every(f => !f.status || lower(f.status) === 'completed');
}

/** A Recordings tab's row: when (a Date), title, link, passcode, length,
 * Zoom topic, ID. */
function recordingRow(rec, title) {
  return [new Date(rec.start), title || rec.topic || '(untitled)', rec.url, rec.passcode,
          formatLength(rec.minutes), rec.topic, rec.id];
}

/** The name of a group's recordings sheet. */
function recordingsSheetName(groupName) {
  return `${groupName} Meeting Recordings`;
}

/**
 * { normalized title: [destinations] } from entries [{ title, dest }], the
 * recurring meetings on group calendars (dest a group's address) and
 * community calendars (COMMUNITY). A title on calendars leading to more
 * than one sheet lists each.
 */
function recurringTitleIndex(entries) {
  const index = {};
  for (const { title, dest } of entries) {
    const key = normalizeTitle(title);
    if (!key) continue;
    if (!index[key]) index[key] = [];
    if (!index[key].includes(dest)) index[key].push(dest);
  }
  return index;
}

/** A link without its query, which is how it's found in text that may
 * have its "&"s written as "&amp;". */
function shareBase(url) {
  return url.split('?')[0];
}

/** A title's words, in lower case, separated by single spaces. */
function titleWords(title) {
  return lower(plainText(title || '')).replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}

/**
 * The email about recordings that couldn't be categorized: { to, subject,
 * body }. items: [{ title, when, topic, host, url, why }]. opts: {
 * alertEmail, sheetUrl (the community sheet), backfill (true for a
 * backfill run's summary) }.
 */
function uncategorizedMessage(items, opts) {
  const lines = items.map(i =>
    `- "${i.title}", ${i.when}\n  Zoom topic: ${i.topic || '(none)'}; Zoom user: ${i.host}\n` +
    `  ${i.url}\n  Why: ${i.why}`);
  const one = items.length === 1;
  return {
    to: opts.alertEmail,
    subject: one
      ? `Recording not listed for any group: "${items[0].title}"`
      : `${items.length} recordings not listed for any group`,
    body:
      `Hi,\n\n` +
      (opts.backfill ? 'The recordings backfill found ' : 'Zoom has ') +
      (one ? 'a recording' : `${items.length} recordings`) +
      ` that couldn't be matched to a group or to the community-wide meetings, so ` +
      `${one ? 'it is' : 'they are'} on the ${UNASSIGNED_TAB} tab of the community recordings ` +
      `sheet:\n${opts.sheetUrl}\n\n${lines.join('\n')}\n\n` +
      `To list one in a group's sheet, type the group's name (or "community") in its Group ` +
      `column; it's moved within the hour.\n\n` +
      `This message was sent automatically.\n`,
  };
}

/** A Zoom recording UUID as a path segment: one starting with "/" or
 * holding "//" is encoded twice, as Zoom requires. */
function uuidPath(uuid) {
  const once = encodeURIComponent(uuid);
  return uuid.startsWith('/') || uuid.includes('//') ? encodeURIComponent(once) : once;
}
