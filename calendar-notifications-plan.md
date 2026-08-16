# Plan: Community Calendar → Group Notifications & Invitation Gatekeeper

## Goal

A Google Apps Script, **owned by the Workspace admin account**, that watches the
admin's primary calendar — the one aliased by `share@berkeleymoshav.org` — and,
whenever a community member invites `share@` to an event they created on their
personal Gmail calendar:

1. **Notifies** the community Google Group by email that a new event was added to
   the shared calendar.
2. **Gatekeeps** the calendar so that only invitations from principals who are
   *supposed* to add events are kept; every other invitation is removed.

The whole thing is **self-configuring from the calendar's sharing settings**
(its ACL), the same spirit as `groups_drive_sync.gs`, which reads its
configuration out of each group's footer rather than a hardcoded map.

---

## How the pieces fit

- Community members create social events on their **personal (non-Workspace)
  Gmail** calendars and invite `share@berkeleymoshav.org`.
- `share@` is an **alias of the admin account**, so the invitation lands on the
  admin account's **primary calendar** as a guest copy. On that copy, the
  `organizer` field is the community member's personal Gmail address.
- The admin's primary calendar is **shared with a community Google Group** as a
  reader ("See all event details"). **The group's members are external personal
  Gmail accounts**, added as members of the Workspace group.
- Because the script runs *as the admin*, it can read/modify that calendar
  (`Calendar` advanced service) and expand groups into members (`AdminDirectory`),
  including the external Gmail members.

---

## Two independent decisions per event

Both are derived from a single `Calendar.Acl.list(CALENDAR_ID)` call each run.

### ACL classification (once per run)

| ACL rule | Meaning | Role in this script |
|---|---|---|
| `scope.type = group`, `role = reader` | **Community / reader group** | Notification target **and** allowed-to-keep |
| `scope.type = user`,  `role = writer`/`owner` | Individual with modification rights | Allowed-to-keep |
| `scope.type = group`, `role = writer`/`owner` | Group with modification rights (members expanded) | Allowed-to-keep |
| `scope.type = domain`, `role = writer`/`owner` | Whole domain with modification rights | Allowed-to-keep (domain match) |

- **Reader means `role = reader` only.** Google's "see only free/busy" is a
  different role (`freeBusyReader`) and is deliberately **not** treated as a
  community group.
- `role = none` / `freeBusyReader` groups are **neither** allowed **nor**
  notified — their members' invitations are removed. This is the intended
  strictness.

From the classification the script builds:

- **`notifyGroups`** — each reader-group email plus its member set.
- **`allowedEmails`** — union of: reader-group members + writer/owner user
  emails + members of writer/owner groups + a small self allowlist
  (`admin` / `share@`).
- **`allowedDomains`** — any domain granted writer/owner.

### Decision 1 — Gatekeeper (keep vs. remove)

Keep the event if the **organizer** is in `allowedEmails`, or the organizer's
domain is in `allowedDomains`. Otherwise remove it from the admin calendar with
`Calendar.Events.remove(...)` (fallback: set the admin's response to *declined*
if a hard delete errors on a guest copy).

> Anyone with **modification permission** — a writer/owner user, a member of a
> writer/owner group, or the admin itself — can create/keep events. So can a
> **reader-group community member**.

### Decision 2 — Notification (email vs. silence)

Send an email **only if** the organizer is a member of one or more **reader
groups**, and send it **only to the reader group(s) they belong to** — one
notification per matching reader group.

> A writer-only person's event is kept but generates **no** email. A community
> member's event is kept **and** emailed to their group.

---

## Detecting new/changed events

- An installable **`onEventUpdated` trigger** bound to the admin's own calendar:
  `ScriptApp.newTrigger('onCalendarUpdate').forUserCalendar(CALENDAR_ID).onEventUpdated().create()`.
- The trigger only signals "something changed," so the handler pulls the delta
  with the **Advanced Calendar service using a stored sync token**
  (`Calendar.Events.list({ syncToken })`), persisted in `PropertiesService`.
- **First run seeds a token from "now" forward**, so pre-existing events are not
  retro-notified. A `410 GONE` (expired token) resets and reseeds.
- **New events only.** A `notifiedEvents` map in `PropertiesService` dedupes
  (the trigger can fire repeatedly), and entries are pruned once the event's end
  time passes. Updates and cancellations are wired as **off-by-default flags** so
  they can be enabled later without a rewrite.
- **Fallback (noted, not enabled):** a time-driven trigger running the same sync
  function every few minutes, in case `onEventUpdated` is delayed or dropped.

---

## The notification email

Plain-text body **plus a light HTML version**, built entirely from the event
object, formatted in the calendar's timezone. Sent from the **admin account's
default sender**, addressed **to the reader group** (never in the `To:` line,
so no personal Gmail addresses are exposed there). The **`Reply-To` is set to
the event's organizer**, so when a group member replies, the reply goes to the
community member who created the event rather than to the admin account.

**Subject:** `New community event: {title}`

**Body (example):**

> A community member added a new event to the shared calendar.
>
> **Tu BiShvat Seder**
> 🗓 Sunday, February 1, 2026 · 5:30–7:30 PM
> 📍 Community Room
> 👤 Added by Dana Cohen
>
> Bring a fruit to share. Kids welcome.
>
> View on the shared calendar: https://calendar.google.com/…
>
> —
> You're receiving this because members@berkeleymoshav.org can see the Berkeley Moshav shared calendar.

Optional fields degrade gracefully: location/description lines drop out when
absent; all-day events show "All day" instead of a time range.

---

## Reuse from `groups_drive_sync.gs`

The new script deliberately matches the existing one:

- **`listAllMembers(groupEmail)`** — the paginated `AdminDirectory.Members.list`
  loop is lifted directly to expand each reader/writer group into its members.
- **`installTrigger()` structure** — the "delete existing triggers matching this
  handler name, then create one" duplicate guard is reused; only the trigger
  builder changes (`.forUserCalendar(...).onEventUpdated()` instead of
  `.timeBased()`).
- **Lowercase-into-a-`Set` matching idiom** and the **`do { … } while (pageToken)`
  pagination idiom**.
- **Config block + section banners + docstring-header-with-setup-steps** layout,
  and the **`console.log` summary-counter** logging style.

Genuinely new code: the ACL classification, the sync-token delta loop, the
keep/remove gate, and the per-group email.

---

## File: `calendar_notifications/calendar_notifications.gs`

```javascript
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
  const set = new Set();
  for (const m of listAllMembers(groupEmail)) {
    if (m.email) set.add(m.email.toLowerCase());
  }
  return set;
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
```

---

## File: `calendar_notifications/appsscript.json`

```json
{
  "timeZone": "America/Los_Angeles",
  "dependencies": {
    "enabledAdvancedServices": [
      {
        "userSymbol": "Calendar",
        "serviceId": "calendar",
        "version": "v3"
      },
      {
        "userSymbol": "AdminDirectory",
        "serviceId": "admin",
        "version": "directory_v1"
      }
    ]
  },
  "oauthScopes": [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
    "https://www.googleapis.com/auth/script.send_mail",
    "https://www.googleapis.com/auth/script.scriptapp"
  ],
  "exceptionLogging": "STACKDRIVER",
  "runtimeVersion": "V8"
}
```

---

## Setup steps

1. **Create the project.** In the admin account, create a standalone Apps Script
   project and add the two files above.
2. **Enable advanced services.** Extensions → Advanced Services → turn on
   *Google Calendar API* and *Admin SDK Directory API* (the `appsscript.json`
   above also declares them).
3. **Verify sharing.** Confirm the admin's primary calendar is shared with the
   community group as **"See all event details"** (`role = reader`). Any
   modification-rights users/groups you want to be able to add events should have
   `Make changes to events` (writer) or `Make changes and manage sharing`
   (owner).
4. **Group posting permission.** In the community group's settings, ensure the
   admin sender is permitted to post, or notification mail will bounce.
5. **Authorize + install.** Run `installTrigger()` once. Grant the requested
   scopes. This seeds the sync token (no retro-notifications) and registers the
   `onEventUpdated` trigger.

---

## Testing

| Scenario | Expected result |
|---|---|
| Reader-group member invites `share@` to a new event | Event kept; one email to that member's reader group. |
| Writer/owner user (not in any reader group) adds an event | Event kept; **no** email. |
| Non-member / stranger invites `share@` | Event removed from the calendar (or declined); no email. |
| `freeBusyReader` group member invites `share@` | Event removed; no email. |
| Trigger fires twice for the same new event | Exactly one email (dedupe map). |
| Sync token expires (`410`) | Script resets, reseeds, continues without error. |
| Member cancels their event | Removed from calendar naturally; email only if `NOTIFY_CANCELLATIONS` is enabled. |

Manual test: from a throwaway Gmail account that is (a) a group member, then
(b) not a member, create events inviting `share@`, and run `processChanges()`
after each.

---

## Gotchas & limitations

- **Removal is after-the-fact.** Google adds the invitation to the calendar the
  moment it arrives; the script removes it on the next `onEventUpdated` /
  sync-token pass. There is no per-organizer "auto-reject" native setting, so
  remove-on-detection is the real mechanism. Expect a brief window where a
  non-member invite is visible before it's cleaned up.
- **`onEventUpdated` latency.** The trigger can lag or, rarely, be dropped. The
  time-driven fallback (every few minutes, same `processChanges`) is the safety
  net if that becomes a problem.
- **External members must actually be in the group.** Membership is matched
  against `AdminDirectory.Members.list`; a community member whose personal Gmail
  isn't in the group's member list will be treated as a stranger (event removed,
  no email).
- **`includeDerivedMembership`** flattens nested groups so members of a subgroup
  are matched too.
- **Sender identity.** Mail is sent from the admin account's default address. To
  appear as `share@`, that alias must be a configured send-as alias and the
  group must accept posts from it (out of scope for the default setup).
- **Reply-To exposes the organizer's address.** Setting `Reply-To` to the
  organizer means their personal Gmail appears in that header (and on replies) —
  which is the point (replies reach the event creator), but it is a mild privacy
  trade-off. The organizer already chose to invite `share@`, so this is normally
  fine; drop the `replyTo` option if you'd rather keep organizer addresses out
  of the notification entirely.

---

## Deliverables (when implemented)

- `calendar_notifications/calendar_notifications.gs`
- `calendar_notifications/appsscript.json`

on branch `claude/workspace-calendar-notifications-ydc0vx`. No pull request
unless requested.
