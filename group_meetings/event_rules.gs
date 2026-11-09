/**
 * Group Meetings — pure rules (no Apps Script services), so they can be
 * tested under Node (tests/test_group_meetings_rules.py).
 *
 *   - reading a group's footer (calendar ID, Zoom link, passcode, and, for
 *     a community-wide calendar's admin group, the calendar's audience);
 *   - deciding what to change on a group-calendar event (Zoom details,
 *     invitations);
 *   - recognizing invitation-only copies;
 *   - finding overlapping meetings in rooms owned by the same Zoom user;
 *   - meeting reminders: reading how long before a meeting they go out
 *     (from the group's agenda doc, or a tag in the event), deciding when
 *     one is due, and writing the email.
 */

// Must match util/google_group_footer.py.
const FOOTER_MARKER = '--- Auto-managed links ---';
const CALENDAR_LINK_RE =
  /Google calendar:\s*https:\/\/calendar\.google\.com\/calendar\/u\/0\?cid=([A-Za-z0-9%+\/_=-]+)/;
const ZOOM_LINK_RE = /Zoom meeting:\s*(https:\/\/\S+)/;
const ZOOM_DETAILS_RE = /^Meeting ID [\d ]+, passcode (\S+)$/m;
const AUDIENCE_RE = /Calendar audience:\s*(\S+@\S+)/;
// More addresses every event on a community calendar invites.
const ALSO_INVITE_RE = /^Also invite:(.*)$/m;
// The end of a community-wide calendar's admin group's name (see
// ADMIN_LIST_SUFFIX in util/gdrive_config.py): "Community Meetings Admins"
// runs the Community Meetings calendar.
const ADMIN_NAME_RE = /\s+Admins\s*$/i;
const MEETING_ID_IN_URL_RE = /\/j\/(\d+)/;
const ANY_ZOOM_ID_RE = /zoom\.us\/j\/(\d+)/g;
const OTHER_MEETING_LINK_RE = /(?:[\w.-]*zoom\.us\/j\/\d+|meet\.google\.com\/[a-z-]+)/gi;

const BASE64_ALPHABET =
  'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

const NO_ZOOM_MARKER = '[no zoom]';
// An event that shouldn't invite the group (or a community-wide calendar's
// audience), nor, unless it has a reminder tag, remind them.
const NO_INVITE_MARKER = '[no invite]';
const ZOOM_BLOCK_END = '(Added automatically from the group\'s Zoom room.)';
// The block, whether written with plain-text or HTML line breaks.
const ZOOM_BLOCK_RE =
  /(?:\n|<br\s*\/?>)*Join Zoom meeting: [\s\S]*?\(Added automatically from the group's Zoom room\.\)/i;
const STAMP_KEY = 'groupZoomUrl';
const ZOOM_USER_NOTE = "(meetings with the same Zoom user can't overlap)";
const ZOOM_USER_RE = /Zoom user: (.+?) \(meetings with the same Zoom user/;
const FAQ_TEXT = 'Questions about joining? See the BM Meetings FAQ:';
const AGENDA_TEXT = 'Agenda and minutes:';
const GOOGLE_DOC_ID_RE = /\/document\/d\/([\w-]+)/;
// The rest of an "Agenda and minutes:" line, up to its line break or the
// end of its HTML element.
const AGENDA_LINE_RE = /(Agenda and minutes:)((?:(?!\n|<br\s*\/?>|<\/(?:p|div|li)>).)*)/gi;
// The same, with the line breaks before it.
const AGENDA_LINE_WITH_BREAKS_RE =
  new RegExp(String.raw`((?:\n|<br\s*\/?>)*)` + AGENDA_LINE_RE.source, 'gi');
const LEADING_BREAKS_RE = /^(?:\s|<br\s*\/?>)+/i;
const HTML_LINK_RE = /<a\b[^>]*>[\s\S]*?<\/a>/gi;
// A Google Doc's address, as written in a description (bare or in a link).
const GOOGLE_DOC_URL_RE = /https?:\/\/docs\.google\.com\/document\/d\/[\w-]+[^\s"'<>]*/g;
// A line saying the Zoom link is still to come ("Zoom link to follow",
// "(Zoom details will follow.)"), removed once the block is added. The
// line may hold formatting tags around the words; those are kept.
const TO_FOLLOW = String.raw`[(\[]?\s*zoom (?:link|details|info) (?:to follow|will follow|to come|coming soon)\s*[.!)\]]*`;
const LINE_BREAK = String.raw`(?:\n|<br\s*\/?>)`;
const TAGS = String.raw`((?:[^\S\n]|&nbsp;|<(?!br\b)[^>]*>)*)`;
const TO_FOLLOW_LINE_RE = new RegExp(`${LINE_BREAK}+${TAGS}${TO_FOLLOW}${TAGS}(?=${LINE_BREAK}|$)`, 'gi');
const TO_FOLLOW_FIRST_LINE_RE = new RegExp(`^${TAGS}${TO_FOLLOW}${TAGS}(?:${LINE_BREAK}+|$)`, 'i');
// The words as the whole text of an HTML element, as in <p>Zoom link to follow</p>.
const TO_FOLLOW_ELEMENT_RE = new RegExp(`>(?:\s|&nbsp;)*${TO_FOLLOW}(?:\s|&nbsp;)*<`, 'gi');

// The About this circle row in a group's agenda doc that says when the
// group's meetings get reminders (see circle_agendas/agenda_rules.gs).
const REMINDER_ROW_LABEL = 'Remind before meetings';
const DEFAULT_REMINDER_LEADS = [24 * 60 * 60 * 1000];
// Longer lead times aren't read: reminders are looked for this far ahead.
const MAX_REMINDER_LEAD_MS = 14 * 24 * 60 * 60 * 1000;
// "[remind 2 hours]", "[reminders: 2 days, 1 hour]", "[no reminder]" in an
// event's title or description.
const REMINDER_TAG_RE = /\[\s*(no\s+reminders?|remind(?:ers?)?\b\s*:?([^\]]*))\]/i;

const NUMBER_WORDS = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
  eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
};
const UNIT_MS = { minute: 60 * 1000, hour: 60 * 60 * 1000, day: 24 * 60 * 60 * 1000,
                  week: 7 * 24 * 60 * 60 * 1000 };
const DURATION_PART_RE = new RegExp(
  String.raw`\s*(\d+(?:\.\d+)?|half an?|${Object.keys(NUMBER_WORDS).join('|')})\s*` +
  String.raw`(minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w)(?![a-z])\s*`, 'y');

/**
 * The name of the community-wide calendar a Google Group named name runs
 * ("Community Meetings Admins" -> "Community Meetings").
 */
function adminCalendarName(name) {
  return (name || '').replace(ADMIN_NAME_RE, '');
}

/** Decode base64 (standard or URL-safe, padding optional) to a string, or null. */
function base64ToString(text) {
  const s = text.replace(/-/g, '+').replace(/_/g, '/').replace(/=+$/, '');
  let bits = 0, value = 0, out = '';
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

/**
 * What to change on an event organized by a group's own calendar, or null
 * if nothing. ctx: { zoomUrl, passcode, zoomUser, groupEmail,
 * allEventsCalendarId, faqUrl, agendaUrl, staleAgendaUrl }, where zoomUser names the Zoom user that
 * owns the room (null if none is known, undefined to keep the one the
 * description already names).
 *
 * Returns { patch, sendUpdates } where patch is an Events.patch body:
 *   - Zoom details: location set to the room link if empty (or still the
 *     link this script wrote before), and a marked block in the
 *     description, unless the event is marked "[no zoom]" or already has
 *     another Zoom or Meet link. The block names the room's Zoom user, so
 *     anyone can see which meetings can't overlap. The link written is
 *     remembered in a
 *     private extended property, so a changed room link replaces it.
 *     Such an event also gets a line linking the meetings FAQ
 *     (ctx.faqUrl), just above the block, unless the description already
 *     links that document anywhere, and loses any "Zoom link to follow"
 *     line.
 *   - Agenda: a line linking the group's agenda doc (ctx.agendaUrl), just
 *     above the FAQ line (or, without Zoom details, at the end), unless the
 *     description already links that doc anywhere; an "Agenda and minutes:"
 *     line linking another doc (the group's previous one) is relinked
 *     instead. A group with no agenda doc (agendaUrl null) loses its
 *     "Agenda and minutes:" lines that link its last one (staleAgendaUrl),
 *     since that doc was trashed or deleted; lines linking any other doc
 *     are kept.
 *   - Guests: the all-events calendar and the group's Google Group (for a
 *     community-wide calendar, ctx.groupEmail is its audience), and the
 *     addresses in ctx.alsoInvite, unless the event is marked "[no invite]"
 *     (then neither the group nor those; a guest already invited stays).
 * sendUpdates is 'all' when the group or one of those is newly invited (so
 * they get the invitation), else 'none'.
 */
function computeEventPatch(event, ctx) {
  const patch = {};
  const description = event.description || '';
  const location = event.location || '';
  const stamped = ((event.extendedProperties || {}).private || {})[STAMP_KEY] || null;

  if (ctx.zoomUrl && !hasNoZoomMarker(event) && !hasOtherMeetingLink(event, ctx.zoomUrl, stamped)) {
    if (!location || location === stamped) {
      if (location !== ctx.zoomUrl) patch.location = ctx.zoomUrl;
    } else if (stamped && location.includes(stamped) && stamped !== ctx.zoomUrl) {
      patch.location = location.split(stamped).join(ctx.zoomUrl);
    }

    const br = /<[a-z][^>]*>/i.test(description) ? '<br>' : '\n';
    let text = removeToFollowLines(description.replace(ZOOM_BLOCK_RE, '')).replace(/\s+$/, '');
    if (ctx.agendaUrl) text = relinkAgendaLines(text, ctx.agendaUrl);
    else if (ctx.staleAgendaUrl) text = removeAgendaLines(text, ctx.staleAgendaUrl);
    if (ctx.agendaUrl && !linksDocument(text, ctx.agendaUrl)) {
      const agenda = `${AGENDA_TEXT} ${ctx.agendaUrl}`;
      text = text ? `${text}${br}${br}${agenda}` : agenda;
    }
    if (ctx.faqUrl && !linksDocument(text, ctx.faqUrl)) {
      const faq = `${FAQ_TEXT} ${ctx.faqUrl}`;
      text = text ? `${text}${br}${br}${faq}` : faq;
    }
    let zoomUser = ctx.zoomUser;
    if (zoomUser === undefined) {
      const old = (description.match(ZOOM_BLOCK_RE) || [''])[0].match(ZOOM_USER_RE);
      zoomUser = old ? old[1] : null;
    }
    const block = zoomBlock(Object.assign({}, ctx, { zoomUser }), br);
    const desired = text ? `${text}${br}${br}${block}` : block;
    if (desired !== description) patch.description = desired;

    if (stamped !== ctx.zoomUrl) {
      patch.extendedProperties = { private: { [STAMP_KEY]: ctx.zoomUrl } };
    }
  } else if (ctx.agendaUrl) {
    const relinked = relinkAgendaLines(description, ctx.agendaUrl);
    if (!linksDocument(relinked, ctx.agendaUrl)) {
      const br = /<[a-z][^>]*>/i.test(description) ? '<br>' : '\n';
      const agenda = `${AGENDA_TEXT} ${ctx.agendaUrl}`;
      const text = relinked.replace(/\s+$/, '');
      patch.description = text ? `${text}${br}${br}${agenda}` : agenda;
    } else if (relinked !== description) {
      patch.description = relinked;
    }
  } else if (ctx.staleAgendaUrl) {
    const removed = removeAgendaLines(description, ctx.staleAgendaUrl);
    if (removed !== description) patch.description = removed;
  }

  const attendees = event.attendees || [];
  const emails = new Set(attendees.map(a => lower(a.email)));
  const added = [];
  const invitees = hasNoInviteMarker(event) ? [] : [ctx.groupEmail].concat(ctx.alsoInvite || []);
  for (const email of [ctx.allEventsCalendarId].concat(invitees)) {
    if (email && !emails.has(lower(email))) {
      added.push({ email });
      emails.add(lower(email));
    }
  }
  if (added.length) patch.attendees = attendees.concat(added);

  if (!Object.keys(patch).length) return null;
  const invites = added.some(a => lower(a.email) !== lower(ctx.allEventsCalendarId));
  return { patch, sendUpdates: invites ? 'all' : 'none' };
}

function conflictFingerprint(c) {
  return [c.a.start, c.a.end, c.b.start, c.b.end].join(',');
}

/** Stable ID for a conflict, and a fingerprint that changes with its times. */
function conflictId(c) {
  return [c.a.key, c.b.key].sort().join(' | ');
}

/**
 * The text of the row labelled label (its first line, ignoring case) in
 * the first table holding one, in a Docs API document (Docs.Documents.get),
 * as the text of its second cell, its lines joined by commas (so lead
 * times on separate lines read as separate reminders); '' if that cell is
 * empty, null if there's no such row.
 */
function docRowValue(doc, label) {
  const cellText = cell => (cell.content || []).map(se => se.paragraph
    ? (se.paragraph.elements || []).map(pe => (pe.textRun ? pe.textRun.content : '')).join('')
    : '').join('');
  for (const se of ((doc || {}).body || {}).content || []) {
    for (const row of (se.table || {}).tableRows || []) {
      const cells = row.tableCells || [];
      if (cells.length < 2) continue;
      if (lower(cellText(cells[0]).split('\n')[0].trim()) !== lower(label)) continue;
      return cellText(cells[1]).split('\n').map(l => l.trim()).filter(Boolean).join(', ');
    }
  }
  return null;
}

/**
 * The lead times (of leads) whose reminder for a meeting starting at
 * startMs is due now: its time (startMs - lead) has come, at most graceMs
 * ago (so a reminder missed for long, or one due before the event was
 * created, when its invitation went out, isn't sent late), and the meeting
 * hasn't started. createdMs is when the event was created (null if not
 * known). Shortest first: the first is the one to send, the rest are
 * marked sent with it.
 */
function dueReminderLeads(startMs, createdMs, leads, nowMs, graceMs) {
  return leads.filter(lead => reminderTiming(startMs, createdMs, lead, nowMs, graceMs) === 'due')
    .sort((x, y) => x - y);
}

/**
 * Pairs of meetings in rooms owned by the same Zoom user that overlap, or
 * where the second starts less than minGapMs after the first ends (so the
 * first running over would block it).
 *
 * meetings: [{ key, owner, start, end, ... }] with start/end in ms. A
 * meeting that appears more than once (the same calendar event on several
 * calendars, or a Zoom meeting also on a calendar) shares its key and is
 * merged first.
 *
 * Returns [{ a, b }] with a.start <= b.start.
 */
function findOverlaps(meetings, minGapMs = 0) {
  const byKey = new Map();
  for (const m of meetings) {
    const prev = byKey.get(m.key);
    byKey.set(m.key, prev ? mergeMeetings(prev, m) : m);
  }
  const byOwner = new Map();
  for (const m of byKey.values()) {
    if (!byOwner.has(m.owner)) byOwner.set(m.owner, []);
    byOwner.get(m.owner).push(m);
  }
  const conflicts = [];
  for (const list of byOwner.values()) {
    list.sort((x, y) => x.start - y.start || x.end - y.end);
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length && list[j].start < list[i].end + minGapMs; j++) {
        conflicts.push({ a: list[i], b: list[j] });
      }
    }
  }
  return conflicts;
}

/** "24 hours", "2 days", "1 hour 30 minutes", "15 minutes". */
function formatLead(ms) {
  const plural = (n, unit) => `${n} ${unit}${n === 1 ? '' : 's'}`;
  if (ms >= 2 * UNIT_MS.day && ms % UNIT_MS.day === 0) return plural(ms / UNIT_MS.day, 'day');
  const hours = Math.floor(ms / UNIT_MS.hour);
  const minutes = Math.round((ms % UNIT_MS.hour) / UNIT_MS.minute);
  return [hours ? plural(hours, 'hour') : '', minutes ? plural(minutes, 'minute') : '']
    .filter(Boolean).join(' ') || plural(0, 'minute');
}

/** "81234567890" -> "812 3456 7890", as Zoom shows IDs (and the footer does). */
function formatMeetingId(id) {
  if (id.length === 11) return `${id.slice(0, 3)} ${id.slice(3, 7)} ${id.slice(7)}`;
  if (id.length === 10) return `${id.slice(0, 3)} ${id.slice(3, 6)} ${id.slice(6)}`;
  return id;
}

function hasNoInviteMarker(event) {
  return plainText(lower(event.summary) + ' ' + lower(event.description)).includes(NO_INVITE_MARKER);
}

/** True if the event's title or description says "[no zoom]", in any
 * case, and whatever formatting or non-breaking spaces the description's
 * HTML has (see plainText). */
function hasNoZoomMarker(event) {
  return plainText(lower(event.summary) + ' ' + lower(event.description)).includes(NO_ZOOM_MARKER);
}

/** True if the event's location or description links some meeting
 * other than the group's room. */
function hasOtherMeetingLink(event, zoomUrl, stamped) {
  const ours = new Set([meetingIdFromUrl(zoomUrl), meetingIdFromUrl(stamped)].filter(Boolean));
  const text = `${event.location || ''} ${(event.description || '').replace(ZOOM_BLOCK_RE, '')}`;
  for (const link of text.match(OTHER_MEETING_LINK_RE) || []) {
    const id = meetingIdFromUrl(link);
    if (!id || !ours.has(id)) return true;
  }
  // A Meet conference Calendar attached on its own (hangoutLink /
  // conferenceData) doesn't count: only links someone typed in.
  return false;
}

/**
 * True if the event on calendarId is only a copy of an invitation from
 * someone else's calendar (so changes to it don't reach other guests), and
 * that someone isn't another of our managed calendars.
 */
function isInvitationCopy(event, calendarId, managedCalendarIds) {
  const organizer = lower((event.organizer || {}).email);
  if (!organizer || organizer === lower(calendarId)) return false;
  return !managedCalendarIds.some(id => lower(id) === organizer);
}

/** "A", "A and B", "A, B and C". */
function joinNames(names) {
  return names.length < 3
    ? names.join(' and ')
    : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** True if text links the Google Doc at docUrl, in any form (a section
 * link, an /edit link, inside formatted text): it contains the doc's ID. */
function linksDocument(text, docUrl) {
  const m = docUrl.match(GOOGLE_DOC_ID_RE);
  return (text || '').includes(m ? m[1] : docUrl);
}

function lower(s) { return (s || '').toLowerCase(); }

function meetingIdFromUrl(url) {
  const m = (url || '').match(MEETING_ID_IN_URL_RE);
  return m ? m[1] : null;
}

/** Combine two sightings of the same meeting, keeping every group named. */
function mergeMeetings(x, y) {
  const union = key => [...new Set([...(x[key] || []), ...(y[key] || [])])];
  const merged = Object.assign({}, x, { groups: union('groups'), names: union('names'),
                                        title: x.title || y.title });
  if (x.calendars || y.calendars) merged.calendars = union('calendars');
  if (x.community || y.community) merged.community = true;
  return merged;
}

/**
 * [{ id, name }] for the calendars in the account's calendar list
 * (Calendar.CalendarList.list items) that it owns, other than those in
 * skipIds (the group calendars and the all-events calendar): its primary
 * calendar and its other calendars. Another person's primary calendar
 * shared with it as an owner is left out, since its ID is that person's
 * address.
 */
function otherOwnedCalendars(items, account, skipIds) {
  const skip = new Set(skipIds.map(lower));
  return items
    .filter(c => c.accessRole === 'owner' && !skip.has(lower(c.id)) &&
                 (c.primary || lower(c.id) === lower(account) ||
                  lower(c.id).endsWith('@group.calendar.google.com')))
    .map(c => ({ id: c.id, name: c.summaryOverride || c.summary || c.id }));
}

/**
 * The email about a conflict from findOverlaps: { to, cc, subject, body }.
 *
 * A meeting with groups ([emails]) and names ([group names]) is on group
 * calendars; one with neither was scheduled directly in Zoom, and the
 * Technology Circle (opts.alertEmail, opts.techName) is asked about it:
 *   - two group meetings: the groups are asked to agree which moves (cc the
 *     Technology Circle);
 *   - a group meeting and a Zoom meeting: the group and the Technology
 *     Circle are asked to agree which moves;
 *   - two Zoom meetings: the Technology Circle is asked to move one.
 * Each email lists the options: moving a group's room to another Zoom user
 * (unless both meetings are one group's or neither is, or one is a
 * community-wide calendar's), holding one meeting on a personal or work
 * Zoom account, and moving one to another time.
 *
 * A community-wide calendar's meeting (community: true) names its admin
 * group in names ("Community Meetings admins", greeted) and its calendar
 * in calendars ("Community Meetings", where the meeting is).
 * Each email links the all-events calendar (opts.allEventsUrl) for finding
 * a free time. opts.formatRange(startMs, endMs) formats a meeting's time;
 * opts.minGapMinutes is the gap meetings should leave.
 */
function overlapMessage(c, opts) {
  const overlap = c.b.start < c.a.end;
  const gap = Math.round((c.b.start - c.a.end) / 60000);
  const groupEmails = [...new Set([...(c.a.groups || []), ...(c.b.groups || [])])];
  const names = [...new Set([...(c.a.names || []), ...(c.b.names || [])])];
  const zoomOnly = [c.a, c.b].filter(m => !(m.groups || []).length).length;
  const community = [c.a, c.b].some(m => m.community);
  // Where each group's meetings are: its calendar, named like the group, or
  // a community-wide calendar's own name.
  const calendarsOf = m => m.calendars || m.names || [];
  const cals = [...new Set([...calendarsOf(c.a), ...calendarsOf(c.b)])];
  const groupCalendar = community ? 'calendar' : 'group calendar';
  const spacing = overlap ? '' : `, leaving at least ${opts.minGapMinutes} minutes between them`;

  const findTime = `To find a free time, check the All Berkeley Moshav Events calendar, ` +
    `which shows every group's meetings:\n${opts.allEventsUrl}`;
  const moveRoom = rooms => community ? null : `- Request the Tech Circle to move ${rooms} ` +
    `to our second Zoom user account.  This may or may not be possible, depending on your ` +
    `meeting schedule.`;
  const ownAccount = how => `- Hold one of these meetings on a personal or work Zoom ` +
    `account instead: ${how}.`;
  const onCalendar = `replace the Zoom link in its calendar event with that account's ` +
    `link, and delete the ${community ? '' : 'group '}room's "Join Zoom meeting" details ` +
    `from the event's description`;
  const inZoom = 'schedule it on that account and delete it from our Zoom account';

  let to, cc, greeting, options;
  if (zoomOnly === 2) {
    to = [opts.alertEmail]; cc = [];
    greeting = opts.techName;
    options = [
      ownAccount(inZoom),
      `- Move one of them to another time in Zoom${spacing}. ${findTime}`,
    ];
  } else if (zoomOnly === 1) {
    to = groupEmails.concat([opts.alertEmail]); cc = [];
    greeting = joinNames([opts.techName].concat(names));
    const group = joinNames(cals);
    const where = cals.length === 1 ? `the ${cals[0]} calendar` : `its ${groupCalendar}`;
    options = [
      moveRoom(names.length === 1 ? `the ${names[0]}'s Zoom room` : "one of the groups' Zoom rooms"),
      ownAccount(`for the ${group} meeting, ${onCalendar}; for the other meeting, ${inZoom}`),
      `- Agree between you which meeting moves to another time${spacing}. The ${group} ` +
        `meeting can be moved on ${where}; the other meeting can be moved in Zoom. ` +
        findTime,
    ];
  } else if (names.length === 1) {
    to = groupEmails; cc = [opts.alertEmail];
    greeting = names[0];
    options = [
      ownAccount(onCalendar),
      `- Move one of them to another time on the ${cals[0]} calendar${spacing}. ${findTime}`,
    ];
  } else {
    to = groupEmails; cc = [opts.alertEmail];
    greeting = joinNames(names);
    options = [
      moveRoom(`one of your ${names.length === 2 ? 'two ' : ''}groups' Zoom rooms`),
      ownAccount(onCalendar),
      `- Agree between you to move one of these meetings, and change its time on its ` +
        `${groupCalendar}${spacing}. ${findTime}`,
    ];
  }
  options = `Your options are:\n${options.filter(Boolean).join('\n')}`;

  const line = m => {
    const where = m.calendars ? `${joinNames(m.calendars)} calendar`
      : (m.names || []).length ? joinNames(m.names) : 'a meeting scheduled in our Zoom account';
    return `  - "${m.title}" (${where}), ${opts.formatRange(m.start, m.end)}`;
  };
  const what = overlap ? 'overlap'
    : gap === 0 ? 'are back to back'
    : `are only ${gap} minute${gap === 1 ? '' : 's'} apart`;
  const why = overlap
    ? "so the second meeting won't be able to start while the first is still going."
    : "so if the first runs over, the second won't be able to start until it ends.";
  return {
    to, cc,
    subject: `Zoom meetings ${overlap ? 'overlap' : 'too close together'}: ` +
      `"${c.a.title}" and "${c.b.title}"`,
    body:
      `Hi ${greeting},\n\n` +
      `These two meetings ${what}:\n${line(c.a)}\n${line(c.b)}\n\n` +
      `Both meetings use the same Zoom user account, which can host only one meeting ` +
      `at a time, ${why}\n\n` +
      `${options}\n\n` +
      `This message was sent automatically. Questions? Reply to ${opts.alertEmail}.\n`,
  };
}

/**
 * The managed links in a group's footer: { calendarId, zoomUrl, passcode,
 * audience, alsoInvite }, each null if absent ([] for alsoInvite). Only the
 * auto-managed block is read. An audience (the everybody group a
 * community-wide calendar's events invite and its reminders go to) marks a
 * community-wide calendar's admin group; alsoInvite are more addresses its
 * events invite (but don't remind).
 */
function parseFooter(footerText) {
  const text = footerText || '';
  const idx = text.indexOf(FOOTER_MARKER);
  const block = idx >= 0 ? text.slice(idx) : text;
  const cal = block.match(CALENDAR_LINK_RE);
  const zoom = block.match(ZOOM_LINK_RE);
  const details = block.match(ZOOM_DETAILS_RE);
  const audience = block.match(AUDIENCE_RE);
  const also = block.match(ALSO_INVITE_RE);
  const alsoInvite = [];
  for (const part of (also ? also[1] : '').split(/[,\s]+/)) {
    if (part.includes('@') && !alsoInvite.includes(lower(part))) alsoInvite.push(lower(part));
  }
  return {
    calendarId: cal ? base64ToString(decodeURIComponent(cal[1])) : null,
    zoomUrl: zoom ? zoom[1] : null,
    passcode: details ? details[1] : null,
    audience: audience ? lower(audience[1]) : null,
    alsoInvite,
  };
}

/**
 * How long before a meeting its reminders go out, as people write it:
 * "24 hours", "2 days before", "1h30m", "an hour", or several separated by
 * commas, semicolons, or "and" ("2 days, 1 hour"), quoted or not; "none"
 * (or "no", "off", "never", "no reminders") for none. Returns { leads } (milliseconds,
 * longest first, [] for none) or { error } if it can't be read, or a lead
 * time is longer than MAX_REMINDER_LEAD_MS.
 */
function parseReminderLeads(text) {
  const raw = (text || '').trim();
  const s = lower(raw).replace(/["“”'‘’]/g, ' ').replace(/[.!]+\s*$/, '')
    .replace(/\b(?:before(?:\s+(?:each|the|every)\s+meetings?)?|ahead|in advance|beforehand|prior)\b/g, ' ')
    .replace(/\s+/g, ' ').trim();
  if (/^(?:none|no|off|never|no reminders?|no emails?|-)$/.test(s)) return { leads: [] };
  if (!s) return { error: 'it\'s empty' };
  const leads = [];
  for (const part of s.split(/\s*(?:,|;|&|\band\b)\s*/).filter(Boolean)) {
    let ms = 0, pos = 0, m;
    DURATION_PART_RE.lastIndex = 0;
    while (pos < part.length && (m = DURATION_PART_RE.exec(part)) !== null) {
      const n = /^half/.test(m[1]) ? 0.5 : NUMBER_WORDS[m[1]] || Number(m[1]);
      ms += n * unitMs(m[2]);
      pos = DURATION_PART_RE.lastIndex;
    }
    if (pos < part.length || ms <= 0) return { error: `"${raw}" isn't a length of time` };
    if (ms > MAX_REMINDER_LEAD_MS) return { error: `"${raw}" is more than two weeks` };
    leads.push(Math.round(ms));
  }
  return { leads: [...new Set(leads)].sort((x, y) => y - x) };
}

/** An event description's text without its HTML: tags removed (a line
 * break kept as one), and the entities Calendar writes (such as the
 * &nbsp; its editor puts between words) decoded, a non-breaking space
 * becoming an ordinary one. */
function plainText(html) {
  return (html || '').replace(/<br\s*\/?>|<\/(?:p|div|li)>/gi, '\n').replace(/<[^>]*>/g, '')
    .replace(/&nbsp;|&#160;|\u00a0/gi, ' ').replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'").replace(/&amp;/gi, '&');
}

/** text with every doc linked on its "Agenda and minutes:" lines replaced
 * by agendaUrl, so a group whose agenda doc is replaced has its events'
 * line point at the new doc rather than gain a second line. */
function relinkAgendaLines(text, agendaUrl) {
  return (text || '').replace(AGENDA_LINE_RE, (line, label, rest) =>
    label + rest.replace(GOOGLE_DOC_URL_RE, agendaUrl));
}

/** Stable key for one reminder of one meeting (an instance of a recurring
 * event has its own ID); a meeting moved to another time gets new ones. */
function reminderKey(calendarId, event, startMs, lead) {
  return [calendarId, event.id, startMs, lead].join('|');
}

/**
 * When an event's reminders go out: { leads, source, error }. A tag in the
 * event ("[remind 2 hours]", "[no reminder]") wins; then the group's
 * setting (the text of its agenda doc's Remind before meetings row, null if it
 * has none, '' if it's empty); then DEFAULT_REMINDER_LEADS. An event
 * marked "[no invite]" with no reminder tag gets none (source 'event'), as
 * the group (or audience) wasn't invited. source is 'event', 'group', or
 * 'default'; error says why a tag or setting that couldn't be read was
 * passed over, naming which.
 */
function reminderLeads(event, groupSetting) {
  const errors = [];
  const tag = plainText(`${event.summary || ''} ${event.description || ''}`).match(REMINDER_TAG_RE);
  if (tag) {
    const parsed = /^no/i.test(tag[1]) ? { leads: [] } : parseReminderLeads(tag[2]);
    if (parsed.leads) return { leads: parsed.leads, source: 'event' };
    errors.push(`the event's ${tag[0]} tag: ${parsed.error}`);
  }
  if (hasNoInviteMarker(event)) {
    return Object.assign({ leads: [], source: 'event' },
                         errors.length ? { error: errors.join('; ') } : {});
  }
  if (groupSetting !== null && groupSetting !== undefined && groupSetting.trim()) {
    const parsed = parseReminderLeads(groupSetting);
    if (parsed.leads) {
      return Object.assign({ leads: parsed.leads, source: 'group' },
                           errors.length ? { error: errors.join('; ') } : {});
    }
    errors.push(`the agenda doc's ${REMINDER_ROW_LABEL} row: ${parsed.error}`);
  }
  return Object.assign({ leads: DEFAULT_REMINDER_LEADS, source: 'default' },
                       errors.length ? { error: errors.join('; ') } : {});
}

/**
 * A reminder email to a group about one of its meetings: { to, subject,
 * body }. opts: { groupEmail, groupName, when (the meeting's time, as
 * text), lead (ms), agendaUrl, zoomUrl, passcode (the group's room),
 * source (from reminderLeads), error, audience, calendarName }. With
 * audience, the meeting is a community-wide calendar's (calendarName), and
 * the reminder goes to its audience instead.
 */
function reminderMessage(event, opts) {
  const title = event.summary || '(untitled)';
  const lines = [`  "${title}"`, `  ${opts.when}`];
  const location = (event.location || '').trim();
  if (location) {
    lines.push(/^https?:\/\//.test(location) ? `  Join: ${location}` : `  Where: ${location}`);
    const id = meetingIdFromUrl(location);
    if (id && opts.zoomUrl && id === meetingIdFromUrl(opts.zoomUrl) && opts.passcode) {
      lines.push(`  Meeting ID ${formatMeetingId(id)}, passcode ${opts.passcode}`);
    }
  } else if (event.hangoutLink) {
    lines.push(`  Join: ${event.hangoutLink}`);
  }
  if (opts.agendaUrl && linksDocument(event.description || '', opts.agendaUrl)) {
    lines.push(`  ${AGENDA_TEXT} ${opts.agendaUrl}`);
  }
  if (event.htmlLink) lines.push(`  Calendar event: ${event.htmlLink}`);

  if (opts.audience) {
    const problem = opts.error
      ? `\n\n(Couldn't read ${opts.error}, so this reminder went out at the usual time.)` : '';
    return {
      to: opts.audience,
      subject: `Reminder: "${title}", ${opts.when}`,
      body:
        `Hi everyone,\n\n` +
        `A reminder of this community event:\n\n${lines.join('\n')}\n\n` +
        `This reminder was sent automatically, ${formatLead(opts.lead)} before the ` +
        `event, for the ${opts.calendarName} calendar. Replies go to its admins.${problem}\n`,
    };
  }

  const how = opts.agendaUrl
    ? `To change when ${opts.groupName} meetings get reminders, or to turn them off, ` +
      `edit "${REMINDER_ROW_LABEL}" under About this circle in the agenda doc ` +
      `(${opts.agendaUrl}). For one meeting, put [remind 2 hours] or [no reminder] in ` +
      `its calendar event's description.`
    : `For a different reminder time for one meeting, or none, put [remind 2 hours] or ` +
      `[no reminder] in its calendar event's description.`;
  const problem = opts.error
    ? `\n\n(Couldn't read ${opts.error}, so this reminder went out ` +
      `${opts.source === 'group' ? 'at the group\'s usual time' : 'at the usual time'}.)`
    : '';
  return {
    to: opts.groupEmail,
    subject: `Reminder: "${title}", ${opts.when}`,
    body:
      `Hi ${opts.groupName},\n\n` +
      `A reminder of your meeting:\n\n${lines.join('\n')}\n\n` +
      `This reminder was sent automatically, ${formatLead(opts.lead)} before the ` +
      `meeting. ${how}${problem}\n`,
  };
}

/**
 * Where one reminder (lead ms before a meeting starting at startMs) stands
 * now: 'due' (send it), 'upcoming' (its time hasn't come), 'missed' (due
 * more than graceMs ago), 'created after due' (the event was created after
 * its time, so its invitation went out instead), or 'started' (the
 * meeting has begun). See dueReminderLeads.
 */
function reminderTiming(startMs, createdMs, lead, nowMs, graceMs) {
  const due = startMs - lead;
  if (nowMs >= startMs) return 'started';
  if (createdMs !== null && createdMs >= due) return 'created after due';
  if (nowMs < due) return 'upcoming';
  return nowMs < due + graceMs ? 'due' : 'missed';
}

/** text without the links to the doc at docUrl on its "Agenda and
 * minutes:" lines, and their label. A line left with nothing else on it is
 * removed, with the line breaks before it (or, for a line at the start,
 * after it); anything else typed on the line, such as "[no zoom]", is kept. */
function removeAgendaLines(text, docUrl) {
  let atStart = false;
  const out = (text || '').replace(AGENDA_LINE_WITH_BREAKS_RE, (line, breaks, label, rest, offset) => {
    if (!linksDocument(rest, docUrl)) return line;
    const left = rest
      .replace(HTML_LINK_RE, a => (linksDocument(a, docUrl) ? '' : a))
      .replace(GOOGLE_DOC_URL_RE, url => (linksDocument(url, docUrl) ? '' : url))
      .replace(/^(?:\s|&nbsp;)+/, '');
    if (left.replace(/&nbsp;|<[^>]*>/g, '').trim()) return breaks + left;
    if (offset === 0) atStart = true;
    return '';
  });
  return atStart ? out.replace(LEADING_BREAKS_RE, '') : out;
}

/** text without its "Zoom link to follow" lines. */
function removeToFollowLines(text) {
  return text
    .replace(TO_FOLLOW_FIRST_LINE_RE, '$1$2')
    .replace(TO_FOLLOW_LINE_RE, '$1$2')
    .replace(TO_FOLLOW_ELEMENT_RE, '><');
}

/**
 * An email's plain text as HTML, so mail programs reflow it to fit rather
 * than showing the line breaks plain text gets on the way: blank-line-
 * separated paragraphs, lines starting "- " as a bulleted list (a line
 * after one continuing that item), and web links clickable.
 */
function textToHtml(text) {
  const escape = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const linked = s => escape(s).replace(/https?:\/\/[^\s<]+/g, url => `<a href="${url}">${url}</a>`);
  return text.trim().split(/\n{2,}/).map(block => {
    const lines = [];
    let items = null;
    for (const line of block.split('\n')) {
      const item = line.match(/^\s*- (.*)$/);
      if (item) (items = items || []).push(linked(item[1]));
      else if (items) items[items.length - 1] += `<br>${linked(line)}`;
      else lines.push(linked(line));
    }
    return (lines.length ? `<p>${lines.join('<br>')}</p>` : '') +
      (items ? `<ul>${items.map(i => `<li>${i}</li>`).join('')}</ul>` : '');
  }).join('\n');
}

function unitMs(unit) {
  const u = unit[0];
  return u === 'm' ? UNIT_MS.minute : u === 'h' ? UNIT_MS.hour
    : u === 'd' ? UNIT_MS.day : UNIT_MS.week;
}

/** The Zoom details block for an event description. */
function zoomBlock(room, br) {
  const lines = [`Join Zoom meeting: ${room.zoomUrl}`];
  const id = meetingIdFromUrl(room.zoomUrl);
  if (id && room.passcode) lines.push(`Meeting ID ${formatMeetingId(id)}, passcode ${room.passcode}`);
  if (room.zoomUser) lines.push(`Zoom user: ${room.zoomUser} ${ZOOM_USER_NOTE}`);
  lines.push(ZOOM_BLOCK_END);
  return lines.join(br);
}

/** Zoom meeting IDs linked anywhere in an event's location or description. */
function zoomIdsInEvent(event) {
  const text = `${event.location || ''} ${event.description || ''}`;
  const ids = new Set();
  let m;
  ANY_ZOOM_ID_RE.lastIndex = 0;
  while ((m = ANY_ZOOM_ID_RE.exec(text)) !== null) ids.add(m[1]);
  return [...ids];
}

/** "2 (Jane Doe)": how an event description names the Zoom user with this
 * email, by its number and its name in Zoom (numbers and names map email
 * to each), whichever are known, else by email. */
function zoomUserLabel(email, numbers, names) {
  const number = numbers[email], name = names[email];
  if (number && name) return `${number} (${name})`;
  return number || name || email;
}
