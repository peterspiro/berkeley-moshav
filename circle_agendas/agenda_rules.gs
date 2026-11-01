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

// "19:30" from 1170; wraps past midnight.
function formatTime(minutes) {
  const m = ((minutes % 1440) + 1440) % 1440;
  return Math.floor(m / 60) + ':' + String(m % 60).padStart(2, '0');
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

// Minutes from a number of hours, "1.5" or "1.5 h"; null otherwise.
function parseHours(text) {
  const m = /^\s*(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)?\.?\s*$/i.exec(text || '');
  return m ? Math.round(Number(m[1]) * 60) : null;
}

// The time typed after "Time" in a meeting's "Time 19:30  Place …" line.
function headerTimeText(line) {
  const m = /Time\s*(.*?)\s*(?:Place\b|$)/i.exec(line || '');
  return m ? m[1] : '';
}

// The meeting ID in one of a meeting's web app links, or null.
function meetingIdFromUrl(url) {
  const m = /[?&]meeting=([\w-]+)/.exec(url || '');
  return m ? m[1] : null;
}
