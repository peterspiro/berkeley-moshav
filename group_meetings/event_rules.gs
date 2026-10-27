/**
 * Group Meetings — pure rules (no Apps Script services), so they can be
 * tested under Node (tests/test_group_meetings_rules.py).
 *
 *   - reading a group's footer (calendar ID, Zoom link, passcode);
 *   - deciding what to change on a group-calendar event (Zoom details,
 *     invitations);
 *   - recognizing invitation-only copies;
 *   - finding overlapping meetings in rooms owned by the same Zoom user.
 */

// ── Footer ───────────────────────────────────────────────────────────────────

// Must match util/google_group_footer.py.
const FOOTER_MARKER = '--- Auto-managed links ---';
const CALENDAR_LINK_RE =
  /Google calendar:\s*https:\/\/calendar\.google\.com\/calendar\/u\/0\?cid=([A-Za-z0-9%+\/_=-]+)/;
const ZOOM_LINK_RE = /Zoom meeting:\s*(https:\/\/\S+)/;
const ZOOM_DETAILS_RE = /^Meeting ID [\d ]+, passcode (\S+)$/m;
const MEETING_ID_IN_URL_RE = /\/j\/(\d+)/;
const ANY_ZOOM_ID_RE = /zoom\.us\/j\/(\d+)/g;
const OTHER_MEETING_LINK_RE = /(?:[\w.-]*zoom\.us\/j\/\d+|meet\.google\.com\/[a-z-]+)/gi;

const BASE64_ALPHABET =
  'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

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
 * The managed links in a group's footer: { calendarId, zoomUrl, passcode },
 * each null if absent. Only the auto-managed block is read.
 */
function parseFooter(footerText) {
  const text = footerText || '';
  const idx = text.indexOf(FOOTER_MARKER);
  const block = idx >= 0 ? text.slice(idx) : text;
  const cal = block.match(CALENDAR_LINK_RE);
  const zoom = block.match(ZOOM_LINK_RE);
  const details = block.match(ZOOM_DETAILS_RE);
  return {
    calendarId: cal ? base64ToString(decodeURIComponent(cal[1])) : null,
    zoomUrl: zoom ? zoom[1] : null,
    passcode: details ? details[1] : null,
  };
}

function meetingIdFromUrl(url) {
  const m = (url || '').match(MEETING_ID_IN_URL_RE);
  return m ? m[1] : null;
}

/** "81234567890" -> "812 3456 7890", as Zoom shows IDs (and the footer does). */
function formatMeetingId(id) {
  if (id.length === 11) return `${id.slice(0, 3)} ${id.slice(3, 7)} ${id.slice(7)}`;
  if (id.length === 10) return `${id.slice(0, 3)} ${id.slice(3, 6)} ${id.slice(6)}`;
  return id;
}

// ── Event changes ────────────────────────────────────────────────────────────

const NO_ZOOM_MARKER = '[no zoom]';
const ZOOM_BLOCK_END = '(Added automatically from the group\'s Zoom room.)';
// The block, whether written with plain-text or HTML line breaks.
const ZOOM_BLOCK_RE =
  /(?:\n|<br\s*\/?>)*Join Zoom meeting: [\s\S]*?\(Added automatically from the group's Zoom room\.\)/i;
const STAMP_KEY = 'groupZoomUrl';
const FAQ_TEXT = 'Questions about joining? See the BM Meetings FAQ:';
const GOOGLE_DOC_ID_RE = /\/document\/d\/([\w-]+)/;
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

function lower(s) { return (s || '').toLowerCase(); }

/** The Zoom details block for an event description. */
function zoomBlock(room, br) {
  const lines = [`Join Zoom meeting: ${room.zoomUrl}`];
  const id = meetingIdFromUrl(room.zoomUrl);
  if (id && room.passcode) lines.push(`Meeting ID ${formatMeetingId(id)}, passcode ${room.passcode}`);
  lines.push(ZOOM_BLOCK_END);
  return lines.join(br);
}

/**
 * What to change on an event organized by a group's own calendar, or null
 * if nothing. ctx: { zoomUrl, passcode, groupEmail, allEventsCalendarId,
 * faqUrl }.
 *
 * Returns { patch, sendUpdates } where patch is an Events.patch body:
 *   - Zoom details: location set to the room link if empty (or still the
 *     link this script wrote before), and a marked block in the
 *     description, unless the event is marked "[no zoom]" or already has
 *     another Zoom or Meet link. The link written is remembered in a
 *     private extended property, so a changed room link replaces it.
 *     Such an event also gets a line linking the meetings FAQ
 *     (ctx.faqUrl), just above the block, unless the description already
 *     links that document anywhere, and loses any "Zoom link to follow"
 *     line.
 *   - Guests: the all-events calendar and the group's Google Group.
 * sendUpdates is 'all' when the group is newly invited (so members get the
 * invitation), else 'none'.
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
    if (ctx.faqUrl && !linksDocument(text, ctx.faqUrl)) {
      const faq = `${FAQ_TEXT} ${ctx.faqUrl}`;
      text = text ? `${text}${br}${br}${faq}` : faq;
    }
    const block = zoomBlock(ctx, br);
    const desired = text ? `${text}${br}${br}${block}` : block;
    if (desired !== description) patch.description = desired;

    if (stamped !== ctx.zoomUrl) {
      patch.extendedProperties = { private: { [STAMP_KEY]: ctx.zoomUrl } };
    }
  }

  const attendees = event.attendees || [];
  const emails = new Set(attendees.map(a => lower(a.email)));
  const added = [];
  for (const email of [ctx.allEventsCalendarId, ctx.groupEmail]) {
    if (email && !emails.has(lower(email))) added.push({ email });
  }
  if (added.length) patch.attendees = attendees.concat(added);

  if (!Object.keys(patch).length) return null;
  const invitesGroup = added.some(a => lower(a.email) === lower(ctx.groupEmail));
  return { patch, sendUpdates: invitesGroup ? 'all' : 'none' };
}

/** text without its "Zoom link to follow" lines. */
function removeToFollowLines(text) {
  return text
    .replace(TO_FOLLOW_FIRST_LINE_RE, '$1$2')
    .replace(TO_FOLLOW_LINE_RE, '$1$2')
    .replace(TO_FOLLOW_ELEMENT_RE, '><');
}

/** True if text links the Google Doc at docUrl, in any form (a section
 * link, an /edit link, inside formatted text): it contains the doc's ID. */
function linksDocument(text, docUrl) {
  const m = docUrl.match(GOOGLE_DOC_ID_RE);
  return (text || '').includes(m ? m[1] : docUrl);
}

function hasNoZoomMarker(event) {
  return (lower(event.summary) + ' ' + lower(event.description)).includes(NO_ZOOM_MARKER);
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

// ── Overlaps ─────────────────────────────────────────────────────────────────

/** Zoom meeting IDs linked anywhere in an event's location or description. */
function zoomIdsInEvent(event) {
  const text = `${event.location || ''} ${event.description || ''}`;
  const ids = new Set();
  let m;
  ANY_ZOOM_ID_RE.lastIndex = 0;
  while ((m = ANY_ZOOM_ID_RE.exec(text)) !== null) ids.add(m[1]);
  return [...ids];
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

/** Combine two sightings of the same meeting, keeping every group named. */
function mergeMeetings(x, y) {
  const groups = [...new Set([...(x.groups || []), ...(y.groups || [])])];
  const names = [...new Set([...(x.names || []), ...(y.names || [])])];
  return Object.assign({}, x, { groups, names, title: x.title || y.title });
}

/** "A", "A and B", "A, B and C". */
function joinNames(names) {
  return names.length < 3
    ? names.join(' and ')
    : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
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
  const spacing = overlap ? '' : `, leaving at least ${opts.minGapMinutes} minutes between them`;

  let to, cc, greeting, ask;
  if (zoomOnly === 2) {
    to = [opts.alertEmail]; cc = [];
    greeting = opts.techName;
    ask = `Please move one of them in Zoom${spacing}.`;
  } else if (zoomOnly === 1) {
    to = groupEmails.concat([opts.alertEmail]); cc = [];
    greeting = joinNames([opts.techName].concat(names));
    const where = names.length === 1 ? `the ${names[0]} calendar` : 'its group calendar';
    ask = `Please agree between you which meeting moves${spacing}. The ` +
      `${joinNames(names)} meeting can be moved on ${where}; the other meeting can be ` +
      `moved in Zoom.`;
  } else if (names.length === 1) {
    to = groupEmails; cc = [opts.alertEmail];
    greeting = names[0];
    ask = `Please move one of them to another time on the ${names[0]} calendar${spacing}.`;
  } else {
    to = groupEmails; cc = [opts.alertEmail];
    greeting = joinNames(names);
    ask = null;  // the options below
  }
  const findTime = `To find a free time, check the All Berkeley Moshav Events calendar, ` +
    `which shows every group's meetings:\n${opts.allEventsUrl}`;
  const options = ask ? `${ask} ${findTime}` :
    `Your options are:\n` +
    `- Request the Tech Circle to move one of your ${names.length === 2 ? 'two ' : ''}` +
    `groups' Zoom rooms to our second Zoom user account.  This may or may not be ` +
    `possible, depending on your meeting schedule.\n` +
    `- Agree between you to move one of these meetings, and change its time on its ` +
    `group calendar${spacing}. ${findTime}`;

  const line = m => {
    const where = (m.names || []).length
      ? joinNames(m.names) : 'a meeting scheduled in our Zoom account';
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

/** Stable ID for a conflict, and a fingerprint that changes with its times. */
function conflictId(c) {
  return [c.a.key, c.b.key].sort().join(' | ');
}

function conflictFingerprint(c) {
  return [c.a.start, c.a.end, c.b.start, c.b.end].join(',');
}
