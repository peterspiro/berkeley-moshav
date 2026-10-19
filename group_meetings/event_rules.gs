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
 * if nothing. ctx: { zoomUrl, passcode, groupEmail, allEventsCalendarId }.
 *
 * Returns { patch, sendUpdates } where patch is an Events.patch body:
 *   - Zoom details: location set to the room link if empty (or still the
 *     link this script wrote before), and a marked block in the
 *     description, unless the event is marked "[no zoom]" or already has
 *     another Zoom or Meet link. The link written is remembered in a
 *     private extended property, so a changed room link replaces it.
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
    const without = description.replace(ZOOM_BLOCK_RE, '').replace(/\s+$/, '');
    const block = zoomBlock(ctx, br);
    const desired = without ? `${without}${br}${br}${block}` : block;
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
 * Pairs of meetings that overlap in rooms owned by the same Zoom user.
 *
 * meetings: [{ key, owner, start, end, ... }] with start/end in ms. A
 * meeting that appears more than once (the same calendar event on several
 * calendars, or a Zoom meeting also on a calendar) shares its key and is
 * merged first. Back-to-back meetings (one ends as the other starts) are fine.
 *
 * Returns [{ a, b }] with a.start <= b.start.
 */
function findOverlaps(meetings) {
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
      for (let j = i + 1; j < list.length && list[j].start < list[i].end; j++) {
        conflicts.push({ a: list[i], b: list[j] });
      }
    }
  }
  return conflicts;
}

/** Combine two sightings of the same meeting, keeping every group named. */
function mergeMeetings(x, y) {
  const groups = [...new Set([...(x.groups || []), ...(y.groups || [])])];
  return Object.assign({}, x, { groups, title: x.title || y.title });
}

/** Stable ID for a conflict, and a fingerprint that changes with its times. */
function conflictId(c) {
  return [c.a.key, c.b.key].sort().join(' | ');
}

function conflictFingerprint(c) {
  return [c.a.start, c.a.end, c.b.start, c.b.end].join(',');
}
