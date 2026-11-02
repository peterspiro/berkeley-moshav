/**
 * Circles
 *
 * Which circle an agenda doc belongs to, and that circle's calendar, Zoom
 * room, and members. A circle is a Google Group whose footer (written by
 * update_groups_in_google_and_hierarchy.py; see parseCircleFooter in
 * agenda_rules.gs) links its Drive folder or shared drive, calendar, and
 * Zoom room; a doc belongs to the circle whose Drive item it's in, at any
 * depth. The groups' footers are cached for CIRCLES_CACHE_SECONDS, and
 * read again when a doc's folder matches none of them.
 */

const DOMAIN = 'berkeleymoshav.org';
// Members' names by email address, from the Gather directory, written to
// admin@'s My Drive by update_groups_in_google_and_hierarchy.py (see
// util/member_names.py, whose MEMBER_NAMES_FILE this must match).
const MEMBER_NAMES_FILE = 'Circle agenda member names.json';
const MEMBER_NAMES_CACHE_SECONDS = 60 * 60;
const CIRCLES_CACHE_KEY = 'circles';
const CIRCLES_CACHE_SECONDS = 6 * 60 * 60;

// The circle whose Drive item holds the doc most closely: {email, name,
// driveItemId, calendarId, zoomUrl}, or null. Nearest first matters: a doc
// in a circle's folder is also in the shared drive that holds it, which an
// everybody group (such as Full Community) may be linked to.
function circleForDoc(docId) {
  const ids = driveAncestors(docId);
  const find = circles => {
    for (const id of ids) {
      const circle = circles.find(c => c.driveItemId === id);
      if (circle) return circle;
    }
    return null;
  };
  const cached = find(loadCircles());
  // Matching only the outermost item (such as the shared drive) may mean a
  // circle newer than the cache owns a nearer folder, so look again.
  return cached && cached.driveItemId !== ids[ids.length - 1] ? cached : find(refreshCircles());
}

// The IDs of the folders holding a file, nearest first, and of its shared
// drive, if it's in one.
function driveAncestors(fileId) {
  const file = Drive.Files.get(fileId, {fields: 'parents,driveId', supportsAllDrives: true});
  const ids = [];
  let parents = file.parents || [];
  while (parents.length && ids.length < 20) {
    const id = parents[0];
    ids.push(id);
    if (id === file.driveId) break;
    try {
      parents = Drive.Files.get(id, {fields: 'parents', supportsAllDrives: true}).parents || [];
    } catch (e) {
      break; // A folder the script can't see.
    }
  }
  if (file.driveId && !ids.includes(file.driveId)) ids.push(file.driveId);
  return ids;
}

function loadCircles() {
  const cached = CacheService.getScriptCache().get(CIRCLES_CACHE_KEY);
  return cached ? JSON.parse(cached) : refreshCircles();
}

// Every Google Group whose footer links a Drive item.
function refreshCircles() {
  const circles = [];
  let pageToken;
  do {
    const resp = AdminDirectory.Groups.list({domain: DOMAIN, maxResults: 200, pageToken});
    for (const g of resp.groups || []) {
      let footer;
      try {
        footer = GroupsSettings.Groups.get(g.email).customFooterText;
      } catch (e) {
        console.error('Couldn\'t read ' + g.email + '\'s footer: ' + e.message);
        continue;
      }
      const links = parseCircleFooter(footer);
      if (links.driveItemId) {
        circles.push({...links, email: g.email.toLowerCase(), name: links.name || g.name});
      }
    }
    pageToken = resp.nextPageToken;
  } while (pageToken);
  CacheService.getScriptCache().put(CIRCLES_CACHE_KEY, JSON.stringify(circles), CIRCLES_CACHE_SECONDS);
  return circles;
}

// The circle's members' short names, sorted: each member of its Google
// Group, by their first name (with their last initial if need be) from the
// Gather directory (MEMBER_NAMES_FILE), or else the first word of the name
// Drive gives them on the circle's Drive item (whose sharing
// groups_drive_sync keeps in step with the group), or else their email
// address.
function circleMemberNames(circle) {
  const emails = [];
  let pageToken;
  do {
    const resp = AdminDirectory.Members.list(circle.email, {maxResults: 200, pageToken});
    for (const m of resp.members || []) {
      if (m.type === 'USER' && m.email) emails.push(m.email.toLowerCase());
    }
    pageToken = resp.nextPageToken;
  } while (pageToken);

  const names = {};
  pageToken = undefined;
  do {
    const resp = Drive.Permissions.list(circle.driveItemId, {
      supportsAllDrives: true, pageSize: 100, pageToken,
      fields: 'nextPageToken,permissions(emailAddress,displayName)',
    });
    for (const p of resp.permissions || []) {
      if (p.emailAddress && p.displayName) names[p.emailAddress.toLowerCase()] = p.displayName;
    }
    pageToken = resp.nextPageToken;
  } while (pageToken);

  const gatherNames = memberNames();
  return emails.map(e => gatherNames[e] || (names[e] || e).split(/\s+/)[0])
    .sort((a, b) => a.localeCompare(b));
}

// MEMBER_NAMES_FILE's names by (lowercase) email address, or {} if there's
// no such file; cached for MEMBER_NAMES_CACHE_SECONDS.
function memberNames() {
  const cache = CacheService.getScriptCache();
  const cached = cache.get(MEMBER_NAMES_FILE);
  if (cached) return JSON.parse(cached);
  const files = DriveApp.getRootFolder().getFilesByName(MEMBER_NAMES_FILE);
  if (!files.hasNext()) {
    console.warn('No ' + MEMBER_NAMES_FILE + ' in My Drive; run ' +
      'update_groups_in_google_and_hierarchy.py to write it.');
    return {};
  }
  const text = files.next().getBlob().getDataAsString('UTF-8');
  try {
    cache.put(MEMBER_NAMES_FILE, text, MEMBER_NAMES_CACHE_SECONDS);
  } catch (e) {
    // Too big to cache (over 100 KB); read it each time.
  }
  return JSON.parse(text);
}

// The circle's next count timed (not all-day) events starting after a
// time.
function nextEvents(circle, after, count) {
  const resp = Calendar.Events.list(circle.calendarId, {
    timeMin: after.toISOString(), singleEvents: true, orderBy: 'startTime', maxResults: 20,
  });
  return (resp.items || [])
    .filter(e => e.status !== 'cancelled' && e.start && e.start.dateTime)
    .slice(0, count);
}
