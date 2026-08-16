/**
 * Community Calendar → Group Notifications & Invitation Gatekeeper
 *
 * Runs AS the Workspace admin account, whose primary calendar is aliased by
 * share@berkeleymoshav.org. Community members invite share@ to social events
 * they create on their personal Gmail calendars; those invitations land on the
 * admin's primary calendar as guest copies.
 *
 * On every calendar change this script:
 *   1. Reads the calendar's ACL to learn, self-configuring, who may add events
 *      (writer/owner principals) and which groups are "community" reader groups.
 *   2. GATEKEEPS: removes any invitation whose organizer is not allowed.
 *   3. NOTIFIES: emails a reader group when one of ITS members adds a new event.
 *
 * Setup:
 *   1. Create a standalone Apps Script project owned by the admin account.
 *   2. Extensions > Advanced Services > enable "Google Calendar API" and
 *      "Admin SDK Directory API".
 *   3. Confirm the calendar is shared with the community group as "See all
 *      event details" (ACL role = reader).
 *   4. Ensure the community group accepts posts from the admin sender (Group
 *      settings > Who can post), or notifications will bounce.
 *   5. Run installTrigger() once to authorize, seed the sync token, and register
 *      the onEventUpdated trigger. (Seeding means pre-existing events are not
 *      retro-notified.)
 *
 * Manual test entry point: run processChanges() after inviting share@ from a
 * test account.
 */

// ── Configuration ────────────────────────────────────────────────────────────

const DOMAIN = 'berkeleymoshav.org';

// The admin's primary calendar. The script runs as the admin, so this is also
// the calendar share@ (an alias of the admin) receives invitations on.
const CALENDAR_ID = Session.getEffectiveUser().getEmail();

// Organizers always allowed regardless of ACL (belt-and-suspenders).
const SELF_ALLOWLIST = [CALENDAR_ID.toLowerCase(), `share@${DOMAIN}`];

// Notification behavior. New events only by default; the rest are ready to flip.
const NOTIFY_NEW = true;
const NOTIFY_UPDATES = false;
const NOTIFY_CANCELLATIONS = false;

// If a hard delete of a non-member invitation fails, fall back to declining it.
const DECLINE_IF_DELETE_FAILS = true;

// PropertiesService keys.
const PROP_SYNC_TOKEN = 'calendarSyncToken';
const PROP_NOTIFIED = 'notifiedEvents';

// ── Trigger handler & main loop ──────────────────────────────────────────────

/** Installable onEventUpdated trigger target. */
function onCalendarUpdate(e) {
  processChanges();
}

/**
 * Fetch the incremental set of changed events via a stored sync token and act
 * on each. Safe to run manually.
 */
function processChanges(isRetry) {
  const props = PropertiesService.getScriptProperties();
  let syncToken = props.getProperty(PROP_SYNC_TOKEN);
  const seeding = !syncToken;

  // Classify the calendar's sharing once per run.
  const acl = classifyAcl(CALENDAR_ID);
  const notified = JSON.parse(props.getProperty(PROP_NOTIFIED) || '{}');

  let pageToken;
  let newSyncToken;
  let kept = 0, removed = 0, emailed = 0;

  do {
    const params = { maxResults: 250, singleEvents: true, showDeleted: true, pageToken };
    if (syncToken) {
      params.syncToken = syncToken;
    } else {
      // Initial seed: look forward only, establish a token, notify nothing.
      params.timeMin = new Date().toISOString();
    }

    let resp;
    try {
      resp = Calendar.Events.list(CALENDAR_ID, params);
    } catch (err) {
      if (isExpiredSyncToken(err) && !isRetry) {
        // Reset once and reseed. The retry runs without a sync token (a seed),
        // which cannot raise this error again; the isRetry guard caps it at a
        // single re-entry so a misclassified/persistent error can't loop.
        props.deleteProperty(PROP_SYNC_TOKEN);
        console.warn('Sync token expired; resetting and reseeding.');
        return processChanges(true);
      }
      throw err;
    }

    for (const event of (resp.items || [])) {
      if (seeding) continue; // seed run: record token only
      const outcome = handleEvent(event, acl, notified);
      if (outcome === 'kept') kept++;
      else if (outcome === 'removed') removed++;
      if (outcome === 'emailed') { kept++; emailed++; }
    }

    pageToken = resp.nextPageToken;
    if (resp.nextSyncToken) newSyncToken = resp.nextSyncToken;
  } while (pageToken);

  if (newSyncToken) props.setProperty(PROP_SYNC_TOKEN, newSyncToken);
  pruneNotified(notified);
  props.setProperty(PROP_NOTIFIED, JSON.stringify(notified));

  console.log(
    seeding
      ? 'Seed run complete — sync token established, no notifications sent.'
      : `Done — kept ${kept}, removed ${removed}, emailed ${emailed}.`);
}

// ── Per-event logic ──────────────────────────────────────────────────────────

/**
 * Returns 'kept', 'removed', 'emailed', or 'skipped'.
 * ('emailed' implies kept; counted separately by the caller.)
 */
function handleEvent(event, acl, notified) {
  const id = event.id;
  const organizer = organizerEmail(event);

  // Cancellations / deletions.
  if (event.status === 'cancelled') {
    if (notified[id]) {
      if (NOTIFY_CANCELLATIONS) sendCancellation(event, notified[id]);
      delete notified[id];
    }
    return 'skipped';
  }

  if (!organizer) return 'skipped';

  // Decision 1 — gatekeeper.
  if (!isAllowed(organizer, acl)) {
    removeInvitation(id, organizer);
    return 'removed';
  }

  // Decision 2 — notification (reader-group members only).
  const groups = readerGroupsFor(organizer, acl);
  if (groups.length && NOTIFY_NEW && !notified[id]) {
    for (const groupEmail of groups) sendNewEvent(event, groupEmail);
    notified[id] = { at: Date.now(), endMs: eventEndMs(event), hash: eventHash(event) };
    return 'emailed';
  }

  if (groups.length && NOTIFY_UPDATES && notified[id]) {
    const h = eventHash(event);
    if (notified[id].hash !== h) {
      for (const groupEmail of groups) sendUpdate(event, groupEmail);
      notified[id].hash = h;
      return 'emailed';
    }
  }

  return 'kept';
}

/** Allowed to keep an event: writer/owner principal, allowed domain, reader-group member, or self. */
function isAllowed(organizer, acl) {
  if (SELF_ALLOWLIST.indexOf(organizer) !== -1) return true;
  if (acl.allowedEmails.has(organizer)) return true;
  const domain = organizer.split('@')[1];
  if (domain && acl.allowedDomains.has(domain)) return true;
  return false;
}

/** Reader groups (by email) that this organizer is a member of. */
function readerGroupsFor(organizer, acl) {
  const hits = [];
  for (const g of acl.notifyGroups) {
    if (g.members.has(organizer)) hits.push(g.email);
  }
  return hits;
}

// ── ACL classification ───────────────────────────────────────────────────────

/**
 * Read the calendar's ACL and build:
 *   notifyGroups   [{ email, members:Set }]  — reader groups
 *   allowedEmails  Set                        — may keep events
 *   allowedDomains Set                        — may keep events (domain rule)
 */
function classifyAcl(calendarId) {
  const notifyGroups = [];
  const allowedEmails = new Set(SELF_ALLOWLIST);
  const allowedDomains = new Set();

  const rules = listAcl(calendarId);
  for (const rule of rules) {
    const scope = rule.scope || {};
    const type = scope.type;
    const value = (scope.value || '').toLowerCase();
    const role = rule.role;
    const modifies = role === 'writer' || role === 'owner';

    if (type === 'group' && role === 'reader') {
      const members = listMemberSet(value);
      notifyGroups.push({ email: value, members });
      // Reader-group members may also keep events.
      members.forEach(m => allowedEmails.add(m));
    } else if (type === 'group' && modifies) {
      listMemberSet(value).forEach(m => allowedEmails.add(m));
    } else if (type === 'user' && modifies) {
      allowedEmails.add(value);
    } else if (type === 'domain' && modifies) {
      allowedDomains.add(value);
    }
    // role === 'none' / 'freeBusyReader' → intentionally ignored.
  }

  console.log(
    `ACL: ${notifyGroups.length} reader group(s), ` +
    `${allowedEmails.size} allowed email(s), ${allowedDomains.size} allowed domain(s).`);
  return { notifyGroups, allowedEmails, allowedDomains };
}

function listAcl(calendarId) {
  const rules = [];
  let pageToken;
  do {
    const resp = Calendar.Acl.list(calendarId, { pageToken, maxResults: 250 });
    if (resp.items) rules.push(...resp.items);
    pageToken = resp.nextPageToken;
  } while (pageToken);
  return rules;
}

// ── Group helpers (reused from groups_drive_sync.gs) ─────────────────────────

/** Lowercased Set of a group's member emails (external Gmail members included). */
function listMemberSet(groupEmail) {
  const emails = new Set();
  for (const m of listAllMembers(groupEmail)) {
    if (m.email) emails.add(m.email.toLowerCase());
  }
  return emails;
}

function listAllMembers(groupEmail) {
  const members = [];
  let pageToken;
  do {
    const response = AdminDirectory.Members.list(groupEmail, {
      maxResults: 200,
      includeDerivedMembership: true, // flatten nested groups
      pageToken,
    });
    if (response.members) members.push(...response.members);
    pageToken = response.nextPageToken;
  } while (pageToken);
  return members;
}

// ── Calendar write helpers ───────────────────────────────────────────────────

function removeInvitation(eventId, organizer) {
  try {
    Calendar.Events.remove(CALENDAR_ID, eventId);
    console.log(`Removed invitation from non-allowed organizer ${organizer} (${eventId}).`);
    return;
  } catch (err) {
    console.warn(`Delete failed for ${eventId} (${organizer}): ${err}`);
  }
  if (DECLINE_IF_DELETE_FAILS) {
    try {
      const event = Calendar.Events.get(CALENDAR_ID, eventId);
      const me = CALENDAR_ID.toLowerCase();
      (event.attendees || []).forEach(a => {
        if ((a.email || '').toLowerCase() === me || a.self) a.responseStatus = 'declined';
      });
      Calendar.Events.patch({ attendees: event.attendees }, CALENDAR_ID, eventId);
      console.log(`Declined invitation ${eventId} (${organizer}) as delete fallback.`);
    } catch (err2) {
      console.error(`Could not remove or decline ${eventId} (${organizer}): ${err2}`);
    }
  }
}

// ── Email ────────────────────────────────────────────────────────────────────

function sendNewEvent(event, groupEmail) {
  send_(groupEmail, `New community event: ${eventTitle(event)}`,
        'A community member added a new event to the shared calendar.', event);
}

function sendUpdate(event, groupEmail) {
  send_(groupEmail, `Updated community event: ${eventTitle(event)}`,
        'A community event on the shared calendar was updated.', event);
}

function sendCancellation(event, _prev) {
  // Cancellation events may be sparse; only title/id are reliable.
  // Left as a stub wired to NOTIFY_CANCELLATIONS.
}

function send_(groupEmail, subject, lead, event) {
  const { text, html } = renderEmail(lead, event, groupEmail);
  const options = { body: text, htmlBody: html, name: 'Berkeley Moshav Calendar' };
  // Replies go to the event's organizer, not the admin account.
  const organizer = organizerEmail(event);
  if (organizer) options.replyTo = organizer;
  MailApp.sendEmail(Object.assign({ to: groupEmail, subject }, options));
  console.log(`Emailed ${groupEmail} (reply-to ${organizer || 'default'}): ${subject}`);
}

function renderEmail(lead, event, groupEmail) {
  const when = formatWhen(event);
  const title = eventTitle(event);
  const location = event.location || '';
  const organizer = organizerDisplay(event);
  const description = (event.description || '').trim();
  const link = event.htmlLink || '';

  const textLines = [lead, '', title, `When: ${when}`];
  if (location) textLines.push(`Where: ${location}`);
  textLines.push(`Added by: ${organizer}`);
  if (description) textLines.push('', description);
  if (link) textLines.push('', `View: ${link}`);
  textLines.push('', `— You're receiving this because ${groupEmail} can see the Berkeley Moshav shared calendar.`);

  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const html =
    `<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;line-height:1.5">` +
      `<p>${esc(lead)}</p>` +
      `<p style="font-size:16px;font-weight:bold;margin:0 0 6px">${esc(title)}</p>` +
      `<p style="margin:2px 0">🗓 ${esc(when)}</p>` +
      (location ? `<p style="margin:2px 0">📍 ${esc(location)}</p>` : '') +
      `<p style="margin:2px 0">👤 Added by ${esc(organizer)}</p>` +
      (description ? `<p style="white-space:pre-wrap;margin:10px 0">${esc(description)}</p>` : '') +
      (link ? `<p style="margin:12px 0"><a href="${esc(link)}">View on the shared calendar</a></p>` : '') +
      `<hr style="border:none;border-top:1px solid #ddd;margin:16px 0">` +
      `<p style="font-size:12px;color:#888">You're receiving this because ${esc(groupEmail)} can see the Berkeley Moshav shared calendar.</p>` +
    `</div>`;

  return { text: textLines.join('\n'), html };
}

// ── Small utilities ──────────────────────────────────────────────────────────

function organizerEmail(event) {
  return (((event.organizer && event.organizer.email) ||
           (event.creator && event.creator.email) || '')).toLowerCase();
}

function organizerDisplay(event) {
  const o = event.organizer || event.creator || {};
  return o.displayName || o.email || 'a community member';
}

function eventTitle(event) { return event.summary || '(untitled event)'; }

function eventStartMs(event) {
  const s = event.start || {};
  return new Date(s.dateTime || s.date).getTime();
}

function eventEndMs(event) {
  const e = event.end || {};
  return new Date(e.dateTime || e.date).getTime();
}

/** Human date/time in the calendar's timezone; handles all-day events. */
function formatWhen(event) {
  const tz = Session.getScriptTimeZone();
  const s = event.start || {}, e = event.end || {};
  if (s.date) { // all-day
    return Utilities.formatDate(new Date(s.date + 'T00:00:00'), tz, 'EEEE, MMMM d, yyyy') + ' · All day';
  }
  const start = new Date(s.dateTime), end = new Date(e.dateTime);
  const day = Utilities.formatDate(start, tz, 'EEEE, MMMM d, yyyy');
  const t1 = Utilities.formatDate(start, tz, 'h:mm a');
  const t2 = Utilities.formatDate(end, tz, 'h:mm a');
  return `${day} · ${t1}–${t2}`;
}

/** Salient-field hash to detect meaningful updates (used only if NOTIFY_UPDATES). */
function eventHash(event) {
  return [event.summary, event.location, event.description,
          (event.start || {}).dateTime || (event.start || {}).date,
          (event.end || {}).dateTime || (event.end || {}).date].join('|');
}

function pruneNotified(notified) {
  const cutoff = Date.now() - 24 * 60 * 60 * 1000; // 1 day past event end
  for (const id of Object.keys(notified)) {
    const endMs = notified[id] && notified[id].endMs;
    if (endMs && endMs < cutoff) delete notified[id];
  }
}

function isExpiredSyncToken(err) {
  const m = (err && err.message) || '';
  return err && (err.details && err.details.code === 410) ||
         /sync token|410|full sync/i.test(m);
}

// ── Trigger setup (run once manually) ────────────────────────────────────────

/**
 * Run once from the GAS editor to authorize, seed the sync token (so existing
 * events aren't retro-notified), and install the onEventUpdated trigger.
 */
function installTrigger() {
  // Remove any existing handler triggers first to avoid duplicates.
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'onCalendarUpdate')
    .forEach(t => ScriptApp.deleteTrigger(t));

  // Seed: establish a sync token without notifying on pre-existing events.
  PropertiesService.getScriptProperties().deleteProperty(PROP_SYNC_TOKEN);
  processChanges();

  ScriptApp.newTrigger('onCalendarUpdate')
    .forUserCalendar(CALENDAR_ID)
    .onEventUpdated()
    .create();

  console.log('onEventUpdated trigger installed and sync token seeded.');
}
