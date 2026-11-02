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
// "Time 19:30   Duration (hours) 1.5   Place …" line.
function headerTimeText(line) {
  const m = /Time\s*(.*?)\s*(?:Duration\b|Place\b|$)/i.exec(line || '');
  return m ? m[1] : '';
}

// The meeting's length typed after "Duration (hours)" in the same line.
function headerHoursText(line) {
  const m = /Duration\s*(?:\(hours\))?\s*(.*?)\s*(?:Place\b|$)/i.exec(line || '');
  return m ? m[1] : '';
}

// The meeting ID in one of a meeting's web app links, or null.
function meetingIdFromUrl(url) {
  const m = /[?&]meeting=([\w-]+)/.exec(url || '');
  return m ? m[1] : null;
}

// "1.5" from 90 minutes, "2" from 120: a meeting's length as written after
// its "Duration (hours)".
function formatHours(minutes) {
  return String(Math.round(minutes / 60 * 100) / 100);
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
