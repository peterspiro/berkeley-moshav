/**
 * Group Meetings
 *
 * Looks after every group's Google calendar (see util/meeting_rooms.py,
 * which creates them). Runs as admin@berkeleymoshav.org, which owns the
 * calendars.
 *
 * Every 5 minutes (syncAll), for each event that changed on a group's
 * calendar:
 *   1. An event the group calendar organizes gets the group's Zoom room —
 *      its link in the location, and the link, meeting ID, and passcode in
 *      the description — plus the all-events calendar and the group's
 *      Google Group as guests (see computeEventPatch in event_rules.gs).
 *   2. An event that's only a copy of someone's invitation (made on their
 *      own calendar, with the group calendar invited) can't be given those,
 *      since changes to a copy don't reach the other guests; its organizer
 *      and ALERT_EMAIL get one email asking them to create it on the group
 *      calendar instead.
 * Then, if anything changed, and every hour regardless (hourly), it looks for
 * meetings in the next OVERLAP_WEEKS weeks that overlap in rooms owned by the
 * same Zoom user (a Zoom user can run only one meeting at a time) and emails
 * ALERT_EMAIL and the groups involved, once per conflict.
 *
 * Which calendar and room are a group's comes from the group's own email
 * footer (written by update_groups_in_google_and_hierarchy.py), read hourly,
 * so new groups are picked up without redeploying.
 *
 * Setup: see README.md in this folder.
 */

// ── Configuration ────────────────────────────────────────────────────────────

const DOMAIN = 'berkeleymoshav.org';
const ALL_EVENTS_CALENDAR_ID =
  'c_301c808f640b3a04f5344b1bbf266955d51fecaabd7835936b7591f9cff09222@group.calendar.google.com';
const ALERT_EMAIL = 'calendaring-alerts@berkeleymoshav.org';
const TIME_ZONE = 'America/Los_Angeles';
const OVERLAP_WEEKS = 8;

// Script Properties keys.
const PROP_GROUPS = 'groups';                // JSON [{ email, name, calendarId, zoomUrl, passcode }]
const PROP_SYNC_PREFIX = 'syncToken:';       // + calendar ID
const PROP_COPY_ALERTS = 'copyAlerts';       // JSON { iCalUID: forgetAfterMs }
const PROP_OVERLAP_ALERTS = 'overlapAlerts'; // JSON { conflictId: fingerprint }
const PROP_ZOOM_MEETINGS = 'zoomMeetings';   // JSON, fixed-time Zoom meetings
const PROP_ROOM_OWNER_PREFIX = 'roomOwner:'; // + Zoom meeting ID -> JSON { owner, type } or ''
// Zoom app credentials (Script Properties, set by hand; see README.md).
const PROP_ZOOM_ACCOUNT_ID = 'ZOOM_ACCOUNT_ID';
const PROP_ZOOM_CLIENT_ID = 'ZOOM_CLIENT_ID';
const PROP_ZOOM_CLIENT_SECRET = 'ZOOM_CLIENT_SECRET';

const DAY_MS = 24 * 60 * 60 * 1000;

// ── Entry points ─────────────────────────────────────────────────────────────

/** Every 5 minutes: fix changed events; check overlaps if anything changed. */
function syncAll() {
  withLock(() => {
    const groups = loadGroups();
    let changed = 0;
    for (const group of groups) {
      try {
        changed += syncCalendar(group, groups);
      } catch (err) {
        console.error(`Error syncing ${group.email}: ${err}`);
      }
    }
    if (changed) checkOverlaps(groups);
  });
}

/** Every hour: re-read footers and Zoom's meetings, then check overlaps. */
function hourly() {
  withLock(() => {
    const groups = refreshGroups();
    refreshZoomMeetings();
    checkOverlaps(groups);
  });
}

/** Run once from the editor: authorize, then install both triggers. */
function installTriggers() {
  ScriptApp.getProjectTriggers()
    .filter(t => ['syncAll', 'hourly'].includes(t.getHandlerFunction()))
    .forEach(t => ScriptApp.deleteTrigger(t));
  hourly();
  syncAll();
  ScriptApp.newTrigger('syncAll').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('hourly').timeBased().everyHours(1).create();
  console.log('Triggers installed: syncAll every 5 minutes, hourly every hour.');
}

function withLock(fn) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30 * 1000)) {
    console.warn('Another run is still going; skipping this one.');
    return;
  }
  try {
    fn();
  } finally {
    lock.releaseLock();
  }
}

// ── Stored state ─────────────────────────────────────────────────────────────

// A Script Property holds at most 9 KB, so larger JSON values are split
// across "<key>#0", "<key>#1", ..., with the number of parts in "<key>#n".
const PROPERTY_CHUNK = 8000;

function getJson(key, fallback) {
  const props = PropertiesService.getScriptProperties();
  const n = Number(props.getProperty(key + '#n'));
  if (!n) return fallback;
  let text = '';
  for (let i = 0; i < n; i++) text += props.getProperty(`${key}#${i}`) || '';
  return JSON.parse(text);
}

function setJson(key, value) {
  const props = PropertiesService.getScriptProperties();
  const text = JSON.stringify(value);
  const old = Number(props.getProperty(key + '#n')) || 0;
  const parts = {};
  let n = 0;
  for (let i = 0; i < text.length || n === 0; i += PROPERTY_CHUNK) {
    parts[`${key}#${n++}`] = text.slice(i, i + PROPERTY_CHUNK);
  }
  parts[key + '#n'] = String(n);
  props.setProperties(parts);
  for (let i = n; i < old; i++) props.deleteProperty(`${key}#${i}`);
}

// ── Groups, from their footers ───────────────────────────────────────────────

function loadGroups() {
  return getJson(PROP_GROUPS, null) || refreshGroups();
}

/**
 * Every Google Group whose footer links a calendar. A group whose Zoom link
 * or passcode changed (say, after reset_zoom_room.py) has its sync token
 * dropped, so the next syncAll rechecks all its upcoming events.
 */
function refreshGroups() {
  const previous = new Map((getJson(PROP_GROUPS, null) || []).map(g => [g.email, g]));
  const groups = [];
  let pageToken;
  do {
    const resp = AdminDirectory.Groups.list({ domain: DOMAIN, maxResults: 200, pageToken });
    for (const g of resp.groups || []) {
      let footer;
      try {
        footer = GroupsSettings.Groups.get(g.email).customFooterText;
      } catch (err) {
        // Keep what we knew, rather than forgetting the group's calendar.
        console.error(`Couldn't read ${g.email}'s footer: ${err}`);
        const old = previous.get(g.email.toLowerCase());
        if (old) groups.push(old);
        continue;
      }
      const links = parseFooter(footer);
      if (links.calendarId) {
        groups.push(Object.assign({ email: g.email.toLowerCase(), name: g.name }, links));
      }
    }
    pageToken = resp.nextPageToken;
  } while (pageToken);
  for (const g of groups) {
    const old = previous.get(g.email);
    if (old && (old.zoomUrl !== g.zoomUrl || old.passcode !== g.passcode ||
                old.calendarId !== g.calendarId)) {
      PropertiesService.getScriptProperties().deleteProperty(PROP_SYNC_PREFIX + old.calendarId);
      console.log(`${g.email}: Zoom room or calendar changed; rechecking its events.`);
    }
  }
  setJson(PROP_GROUPS, groups);
  console.log(`${groups.length} group calendar(s).`);
  return groups;
}

function managedCalendarIds(groups) {
  return groups.map(g => g.calendarId).concat([ALL_EVENTS_CALENDAR_ID]);
}

// ── Calendar sync ────────────────────────────────────────────────────────────

/**
 * Handle every event that changed on the group's calendar since the last
 * run (on the first run, every event that hasn't ended). Returns how many
 * events changed.
 */
function syncCalendar(group, groups) {
  const props = PropertiesService.getScriptProperties();
  const tokenKey = PROP_SYNC_PREFIX + group.calendarId;
  const syncToken = props.getProperty(tokenKey);
  const now = Date.now();
  let pageToken, nextSyncToken, changed = 0;
  do {
    const params = { maxResults: 250, singleEvents: false, showDeleted: true, pageToken };
    if (syncToken) params.syncToken = syncToken;
    else params.timeMin = new Date(now).toISOString();
    let resp;
    try {
      resp = Calendar.Events.list(group.calendarId, params);
    } catch (err) {
      if (syncToken && isExpiredSyncToken(err)) {
        props.deleteProperty(tokenKey);
        console.warn(`Sync token for ${group.email} expired; starting over.`);
        return syncCalendar(group, groups);
      }
      throw err;
    }
    for (const event of resp.items || []) {
      changed++;
      if (event.status === 'cancelled' || eventEndMs(event) < now) continue;
      handleEvent(event, group, groups);
    }
    pageToken = resp.nextPageToken;
    if (resp.nextSyncToken) nextSyncToken = resp.nextSyncToken;
  } while (pageToken);
  if (nextSyncToken) props.setProperty(tokenKey, nextSyncToken);
  return changed;
}

function handleEvent(event, group, groups) {
  if (isInvitationCopy(event, group.calendarId, managedCalendarIds(groups))) {
    alertInvitationCopy(event, group);
    return;
  }
  if (lower((event.organizer || {}).email) !== lower(group.calendarId)) return;

  const change = computeEventPatch(event, {
    zoomUrl: group.zoomUrl,
    passcode: group.passcode,
    groupEmail: group.email,
    allEventsCalendarId: ALL_EVENTS_CALENDAR_ID,
  });
  if (!change) return;
  Calendar.Events.patch(change.patch, group.calendarId, event.id,
                        { sendUpdates: change.sendUpdates });
  console.log(`${group.email}: updated "${event.summary}" (${Object.keys(change.patch).join(', ')})`);
}

function eventEndMs(event) {
  // A recurring series' own end is its first instance's; keep it in play.
  if (event.recurrence) return Infinity;
  const end = event.end || {};
  return new Date(end.dateTime || end.date || 0).getTime();
}

function isExpiredSyncToken(err) {
  const m = (err && err.message) || '';
  return (err && err.details && err.details.code === 410) || /sync token|410|full sync/i.test(m);
}

// ── Invitation-copy alerts ───────────────────────────────────────────────────

function alertInvitationCopy(event, group) {
  const alerted = getJson(PROP_COPY_ALERTS, {});
  const now = Date.now();
  for (const uid of Object.keys(alerted)) if (alerted[uid] < now) delete alerted[uid];
  const uid = event.iCalUID || event.id;
  if (alerted[uid]) {
    setJson(PROP_COPY_ALERTS, alerted);
    return;
  }

  const organizer = event.organizer.email;
  const when = formatWhen(event.start);
  MailApp.sendEmail({
    to: organizer,
    cc: ALERT_EMAIL,
    subject: `Your meeting "${event.summary || '(untitled)'}" on ${when} won't get ${group.name}'s Zoom room`,
    body:
      `Hi,\n\n` +
      `You created "${event.summary || '(untitled)'}" (${when}) on your own calendar and ` +
      `invited the ${group.name} calendar. The group's calendar only gets a copy of an ` +
      `invitation like that, so its Zoom room link can't be added and the group isn't ` +
      `invited.\n\n` +
      `To fix it, create the meeting on the ${group.name} calendar itself (there's a link ` +
      `to it at the bottom of every message to the group's email list), or, if you can't ` +
      `add events to that calendar, ask someone who can to create it there. Then delete ` +
      `your original or remove the group calendar from its guests.\n\n` +
      `This message was sent automatically.\n`,
  });
  console.log(`${group.email}: alerted ${organizer} about invitation copy "${event.summary}"`);
  alerted[uid] = event.recurrence ? now + 365 * DAY_MS : eventEndMs(event);
  setJson(PROP_COPY_ALERTS, alerted);
}

function formatWhen(start) {
  if (!start) return 'an unknown date';
  if (start.date) return start.date;
  return Utilities.formatDate(new Date(start.dateTime), TIME_ZONE, 'EEE MMM d, h:mm a');
}

// ── Overlaps ─────────────────────────────────────────────────────────────────

/**
 * Collect the next OVERLAP_WEEKS weeks of meetings — events on group
 * calendars whose Zoom link is a room in our account, and meetings
 * scheduled in Zoom at a fixed time — and alert about overlaps in rooms
 * owned by the same Zoom user.
 */
function checkOverlaps(groups) {
  const now = Date.now();
  const until = now + OVERLAP_WEEKS * 7 * DAY_MS;
  const meetings = [];

  for (const group of groups) {
    let pageToken;
    do {
      const resp = Calendar.Events.list(group.calendarId, {
        singleEvents: true, timeMin: new Date(now).toISOString(),
        timeMax: new Date(until).toISOString(), maxResults: 250, pageToken,
      });
      for (const event of resp.items || []) {
        if (event.status === 'cancelled' || !(event.start || {}).dateTime) continue;
        const start = new Date(event.start.dateTime).getTime();
        const end = new Date(event.end.dateTime).getTime();
        for (const id of zoomIdsInEvent(event)) {
          const room = roomOwner(id);
          if (!room) continue;  // not a meeting in our Zoom account
          meetings.push({
            // A fixed-time Zoom meeting is one meeting wherever it appears;
            // a room hosts many, told apart by their calendar events.
            key: room.type === 3 ? `${event.iCalUID}@${start}` : `zoom:${id}@${start}`,
            owner: room.owner, start, end,
            title: event.summary || '(untitled)',
            groups: [group.email], where: group.name,
          });
          break;
        }
      }
      pageToken = resp.nextPageToken;
    } while (pageToken);
  }

  for (const m of loadZoomMeetings()) {
    if (m.end > now && m.start < until) meetings.push(Object.assign({ groups: [] }, m));
  }

  alertOverlaps(findOverlaps(meetings));
}

function alertOverlaps(conflicts) {
  const previous = getJson(PROP_OVERLAP_ALERTS, {});
  const current = {};
  for (const c of conflicts) {
    const id = conflictId(c);
    const fingerprint = conflictFingerprint(c);
    current[id] = fingerprint;
    if (previous[id] === fingerprint) continue;
    sendOverlapAlert(c);
  }
  // Resolved conflicts are dropped, so they're reported again if they recur.
  setJson(PROP_OVERLAP_ALERTS, current);
}

function sendOverlapAlert(c) {
  const describe = m =>
    `"${m.title}" (${m.where}), ${formatRange(m.start, m.end)}`;
  const groups = [...new Set([...(c.a.groups || []), ...(c.b.groups || [])])];
  MailApp.sendEmail({
    to: [ALERT_EMAIL].concat(groups).join(','),
    subject: `Zoom meetings overlap: "${c.a.title}" and "${c.b.title}"`,
    body:
      `These two meetings overlap, and both use Zoom rooms belonging to ${c.a.owner}, ` +
      `which can run only one meeting at a time, so whichever starts second won't be ` +
      `able to start:\n\n` +
      `  - ${describe(c.a)}\n  - ${describe(c.b)}\n\n` +
      `Please move one of them to another time, or ask the Technology Circle to host ` +
      `one in a room belonging to a different Zoom user.\n\n` +
      `This message was sent automatically.\n`,
  });
  console.log(`Overlap alert: ${conflictId(c)}`);
}

function formatRange(startMs, endMs) {
  const day = Utilities.formatDate(new Date(startMs), TIME_ZONE, 'EEE MMM d');
  const t = ms => Utilities.formatDate(new Date(ms), TIME_ZONE, 'h:mm a');
  return `${day}, ${t(startMs)}–${t(endMs)}`;
}

// ── Zoom ─────────────────────────────────────────────────────────────────────

/** { owner, type } for a meeting in our Zoom account, or null; cached. */
function roomOwner(meetingId) {
  const props = PropertiesService.getScriptProperties();
  const key = PROP_ROOM_OWNER_PREFIX + meetingId;
  const cached = props.getProperty(key);
  if (cached !== null) return cached ? JSON.parse(cached) : null;
  const meeting = zoomGet(`/meetings/${meetingId}`);
  const value = meeting ? { owner: lower(meeting.host_email), type: meeting.type } : null;
  props.setProperty(key, value ? JSON.stringify(value) : '');
  return value;
}

function loadZoomMeetings() {
  return getJson(PROP_ZOOM_MEETINGS, []);
}

/**
 * Every Zoom user's meetings scheduled at a fixed time (types 2 and 8),
 * recurring ones expanded into occurrences, within the overlap window.
 */
function refreshZoomMeetings() {
  const now = Date.now();
  const until = now + OVERLAP_WEEKS * 7 * DAY_MS;
  const meetings = [];
  for (const user of zoomList('/users', 'users')) {
    for (const m of zoomList(`/users/${encodeURIComponent(user.id)}/meetings?type=upcoming`, 'meetings')) {
      if (m.type !== 2 && m.type !== 8) continue;
      const occurrences = m.type === 8
        ? ((zoomGet(`/meetings/${m.id}`) || {}).occurrences || [])
        : [{ start_time: m.start_time, duration: m.duration }];
      for (const o of occurrences) {
        const start = new Date(o.start_time).getTime();
        if (start > until) continue;
        const end = start + (o.duration || m.duration || 60) * 60 * 1000;
        if (end < now) continue;
        meetings.push({
          key: `zoom:${m.id}@${start}`, owner: lower(user.email), start, end,
          title: m.topic || '(untitled)', where: `scheduled in Zoom by ${user.email}`,
        });
      }
    }
  }
  setJson(PROP_ZOOM_MEETINGS, meetings);
  console.log(`${meetings.length} fixed-time Zoom meeting(s) in the next ${OVERLAP_WEEKS} weeks.`);
}

function zoomToken() {
  const cache = CacheService.getScriptCache();
  const cached = cache.get('zoomToken');
  if (cached) return cached;
  const creds = zoomCredentials();
  const resp = UrlFetchApp.fetch(
    'https://zoom.us/oauth/token?grant_type=account_credentials&account_id=' +
      encodeURIComponent(creds.accountId),
    {
      method: 'post',
      headers: {
        Authorization: 'Basic ' + Utilities.base64Encode(`${creds.clientId}:${creds.clientSecret}`),
      },
      muteHttpExceptions: true,
    });
  if (resp.getResponseCode() !== 200) {
    throw new Error(
      `Zoom sign-in failed (${resp.getResponseCode()}): ${resp.getContentText()}. Check the ` +
      `${PROP_ZOOM_ACCOUNT_ID}, ${PROP_ZOOM_CLIENT_ID}, and ${PROP_ZOOM_CLIENT_SECRET} ` +
      `Script Properties against the repo's zoom_credentials file.`);
  }
  const token = JSON.parse(resp.getContentText()).access_token;
  cache.put('zoomToken', token, 50 * 60);
  return token;
}

/** The Zoom app's credentials from Script Properties, trimmed; throws
 * naming any that are missing. */
function zoomCredentials() {
  const props = PropertiesService.getScriptProperties();
  const names = [PROP_ZOOM_ACCOUNT_ID, PROP_ZOOM_CLIENT_ID, PROP_ZOOM_CLIENT_SECRET];
  const values = names.map(n => (props.getProperty(n) || '').trim());
  const missing = names.filter((n, i) => !values[i]);
  if (missing.length) {
    throw new Error(`Missing Script Properties: ${missing.join(', ')} (see README.md)`);
  }
  return { accountId: values[0], clientId: values[1], clientSecret: values[2] };
}

/** GET a Zoom API path; null on 404. */
function zoomGet(path) {
  const resp = UrlFetchApp.fetch('https://api.zoom.us/v2' + path, {
    headers: { Authorization: 'Bearer ' + zoomToken() },
    muteHttpExceptions: true,
  });
  const code = resp.getResponseCode();
  if (code === 404) return null;
  if (code >= 400) throw new Error(`Zoom GET ${path} failed (${code}): ${resp.getContentText()}`);
  return JSON.parse(resp.getContentText());
}

/** Every item of a paginated Zoom list. */
function zoomList(path, field) {
  const items = [];
  let pageToken = '';
  do {
    const sep = path.includes('?') ? '&' : '?';
    const data = zoomGet(`${path}${sep}page_size=300` +
                         (pageToken ? `&next_page_token=${pageToken}` : '')) || {};
    items.push(...(data[field] || []));
    pageToken = data.next_page_token || '';
  } while (pageToken);
  return items;
}
