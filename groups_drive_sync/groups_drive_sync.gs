/**
 * Groups–Drive Sync
 *
 * Keeps each Google Group's membership in sync with the permissions on its
 * corresponding Google Drive item. A group can be tied to one of two kinds
 * of item, and the permission role that defines membership differs:
 *
 *   - a Drive **folder**: membership = the folder's "Content manager"
 *     (fileOrganizer) users.
 *   - a **shared drive**: membership = the shared drive's "Viewer" (reader)
 *     users.
 *
 * The item <-> group mapping isn't stored in this project at all: it
 * enumerates every group in the workspace and reads the item ID (and which
 * kind it is) straight out of each group's own custom footer (the
 * "Google Docs folder" / "Shared Drive" link in the "--- Auto-managed
 * links ---" block — see util/google_group_footer.py, which writes it).
 * Groups with no such link are skipped, and a group whose footer links to
 * both kinds at once is skipped with an error (a group is meant to map to a
 * single item). This means adding/removing a group never requires
 * redeploying this Apps Script project.
 *
 * Change alerts: after each run, one email goes to ALERT_EMAIL listing every
 * membership change since the previous run, in two sections:
 *   - changes made outside the sync (someone edited the group directly) —
 *     detected by comparing the group's membership against a snapshot saved
 *     at the end of the previous run (Script Properties); the sync then
 *     reverts them, and
 *   - changes the sync itself made to match the Drive item's permissions.
 * No email is sent when nothing changed. A group's first run only records a
 * snapshot, so it reports no outside changes.
 *
 * Setup:
 *   1. In the GAS editor: Extensions > Advanced Services > enable
 *      "Admin SDK Directory API", "Drive API", and "Groups Settings API".
 *   2. Run syncAll() once manually to authorize and test.
 *   3. Run installTrigger() once to schedule daily execution.
 */

// ── Configuration ────────────────────────────────────────────────────────────

const DOMAIN = 'berkeleymoshav.org';
const FOLDER_SYNC_ROLE = 'fileOrganizer'; // Drive role for a folder's "Content manager"
const SHARED_DRIVE_SYNC_ROLE = 'reader';  // Drive role for a shared drive's "Viewer"
const ALERT_EMAIL = 'group-sync-alerts@berkeleymoshav.org';

// Script Properties key prefix for each group's end-of-run membership snapshot.
const SNAPSHOT_KEY_PREFIX = 'members:';

// Item kinds a footer can encode (matches util.google_group_footer).
const ITEM_TYPE_FOLDER = 'folder';
const ITEM_TYPE_SHARED_DRIVE = 'shared_drive';

// Per-kind footer link matchers — the label, not the URL, tells folder from
// shared drive (both use the same drive.google.com/drive/folders/<id> shape).
// Matches util.google_group_footer's build_footer_block() output.
const FOOTER_LINK_RES = {
  [ITEM_TYPE_FOLDER]: /Google Docs folder:\s*https:\/\/drive\.google\.com\/drive\/folders\/([a-zA-Z0-9_-]+)/,
  [ITEM_TYPE_SHARED_DRIVE]: /Shared Drive:\s*https:\/\/drive\.google\.com\/drive\/folders\/([a-zA-Z0-9_-]+)/,
};

// Drive role -> which item kind's membership it defines.
const SYNC_ROLE_FOR_TYPE = {
  [ITEM_TYPE_FOLDER]: FOLDER_SYNC_ROLE,
  [ITEM_TYPE_SHARED_DRIVE]: SHARED_DRIVE_SYNC_ROLE,
};

// ── Main entry point ─────────────────────────────────────────────────────────

function syncAll() {
  const runner = Session.getActiveUser().getEmail();
  console.log(`syncAll started — runner: ${runner}`);

  const groups = listAllGroups();
  console.log(`Found ${groups.length} group(s) in ${DOMAIN}.`);

  let synced = 0;
  let skipped = 0;
  const reports = [];

  for (const group of groups) {
    try {
      const link = getFooterItemLink(group.email);
      if (!link) {
        skipped++;
        continue;
      }
      const report = syncItem(link.itemId, link.itemType, group.email, runner);
      if (hasChanges(report)) reports.push(report);
      synced++;
    } catch (err) {
      console.error(`Error processing ${group.email}: ${err}`);
    }
  }

  console.log(`syncAll complete — synced ${synced}, skipped ${skipped} (no item link in footer).`);

  if (reports.length) sendChangeAlert(reports);
}

function listAllGroups() {
  const groups = [];
  let pageToken;
  do {
    const response = AdminDirectory.Groups.list({
      domain: DOMAIN,
      maxResults: 200,
      pageToken,
    });
    if (response.groups) groups.push(...response.groups);
    pageToken = response.nextPageToken;
  } while (pageToken);
  return groups;
}

/**
 * Parse a group's footer and return { itemId, itemType }, or null if the
 * footer carries no auto-managed Drive link. Throws if it ambiguously
 * links to both a folder and a shared drive — a group must map to exactly
 * one item.
 */
function getFooterItemLink(groupEmail) {
  const settings = GroupsSettings.Groups.get(groupEmail);
  const footer = settings.customFooterText || '';

  const found = [];
  for (const itemType of Object.keys(FOOTER_LINK_RES)) {
    const match = footer.match(FOOTER_LINK_RES[itemType]);
    if (match) found.push({ itemId: match[1], itemType });
  }

  if (found.length > 1) {
    throw new Error(
      `footer links to more than one Drive item ` +
      `(${found.map(f => f.itemType).join(', ')}); expected exactly one`);
  }
  return found.length ? found[0] : null;
}

function syncItem(itemId, itemType, groupEmail, runnerEmail) {
  const label = itemType === ITEM_TYPE_SHARED_DRIVE ? 'Shared Drive' : 'Folder';
  console.log(`${label}: ${itemId} → group: ${groupEmail}`);

  const targetEmails = getSyncTargetEmails(itemId, itemType);
  const report = syncGroupMembership(groupEmail, targetEmails, runnerEmail);

  console.log(`  Done — added: ${report.syncAdded.length}, removed: ${report.syncRemoved.length}`);
  return report;
}

// ── Drive helpers ─────────────────────────────────────────────────────────────

/**
 * Return the set of lowercased user email addresses whose permission on the
 * item defines the group's membership: a folder's "Content manager"
 * (fileOrganizer) users, or a shared drive's "Viewer" (reader) users. The
 * Permissions.list call is the same for both — only the role we keep
 * differs, per SYNC_ROLE_FOR_TYPE.
 */
function getSyncTargetEmails(itemId, itemType) {
  const syncRole = SYNC_ROLE_FOR_TYPE[itemType];
  if (!syncRole) throw new Error(`unknown item type: ${itemType}`);

  // Listing a shared drive's own memberships needs domain-admin access
  // (the runner is a Workspace super-admin); this flag only takes effect
  // when the ID refers to a shared drive, so the folder path is unchanged.
  const useDomainAdminAccess = itemType === ITEM_TYPE_SHARED_DRIVE;

  const emails = new Set();
  let pageToken;
  do {
    const response = Drive.Permissions.list(itemId, {
      supportsAllDrives: true,
      useDomainAdminAccess,
      fields: 'nextPageToken,permissions(emailAddress,role,type)',
      pageToken,
    });
    for (const p of (response.permissions || [])) {
      if (p.role === syncRole && p.type === 'user') {
        emails.add(p.emailAddress.toLowerCase());
      }
    }
    pageToken = response.nextPageToken;
  } while (pageToken);
  return emails;
}

// ── Group helpers ─────────────────────────────────────────────────────────────

/**
 * Bring the group's membership in line with targetEmails, and return a
 * report of { groupEmail, outsideAdded, outsideRemoved, syncAdded,
 * syncRemoved } (arrays of emails). The outside* lists compare the group's
 * current membership against the snapshot saved at the end of the previous
 * run; the resulting membership is saved as the new snapshot even if a
 * change fails partway, so the sync's own edits are never later mistaken
 * for outside ones.
 */
function syncGroupMembership(groupEmail, targetEmails, runnerEmail) {
  const currentMembers = listAllMembers(groupEmail);
  const currentEmails = new Set(currentMembers.map(m => m.email.toLowerCase()));

  const previous = loadSnapshot(groupEmail);
  const report = {
    groupEmail,
    outsideAdded: previous ? [...currentEmails].filter(e => !previous.has(e)) : [],
    outsideRemoved: previous ? [...previous].filter(e => !currentEmails.has(e)) : [],
    syncAdded: [],
    syncRemoved: [],
  };

  const finalEmails = new Set(currentEmails);
  try {
    for (const email of targetEmails) {
      if (!currentEmails.has(email)) {
        AdminDirectory.Members.insert({ email, role: 'MEMBER' }, groupEmail);
        console.log(`  + ${email}`);
        finalEmails.add(email);
        report.syncAdded.push(email);
      }
    }

    for (const email of currentEmails) {
      // Never remove the script runner — they may be an owner/manager
      if (email === runnerEmail.toLowerCase()) continue;
      if (!targetEmails.has(email)) {
        AdminDirectory.Members.remove(groupEmail, email);
        console.log(`  - ${email}`);
        finalEmails.delete(email);
        report.syncRemoved.push(email);
      }
    }
  } finally {
    saveSnapshot(groupEmail, finalEmails);
  }

  return report;
}

function listAllMembers(groupEmail) {
  const members = [];
  let pageToken;
  do {
    const response = AdminDirectory.Members.list(groupEmail, {
      maxResults: 200,
      pageToken,
    });
    if (response.members) {
      members.push(...response.members);
    }
    pageToken = response.nextPageToken;
  } while (pageToken);
  return members;
}

// ── Change alerts ─────────────────────────────────────────────────────────────

function loadSnapshot(groupEmail) {
  const json = PropertiesService.getScriptProperties()
    .getProperty(SNAPSHOT_KEY_PREFIX + groupEmail.toLowerCase());
  return json ? new Set(JSON.parse(json)) : null;
}

function saveSnapshot(groupEmail, emails) {
  PropertiesService.getScriptProperties().setProperty(
    SNAPSHOT_KEY_PREFIX + groupEmail.toLowerCase(),
    JSON.stringify([...emails].sort()));
}

function hasChanges(report) {
  return report.outsideAdded.length || report.outsideRemoved.length ||
    report.syncAdded.length || report.syncRemoved.length;
}

function sendChangeAlert(reports) {
  const lines = [];
  for (const r of reports) {
    lines.push(r.groupEmail);
    if (r.outsideAdded.length || r.outsideRemoved.length) {
      lines.push('  Changed outside the sync (being reverted to match Drive):');
      r.outsideAdded.forEach(e => lines.push(`    + ${e}`));
      r.outsideRemoved.forEach(e => lines.push(`    - ${e}`));
    }
    if (r.syncAdded.length || r.syncRemoved.length) {
      lines.push('  Changed by the sync (to match Drive permissions):');
      r.syncAdded.forEach(e => lines.push(`    + ${e}`));
      r.syncRemoved.forEach(e => lines.push(`    - ${e}`));
    }
    lines.push('');
  }

  const n = reports.length;
  MailApp.sendEmail({
    to: ALERT_EMAIL,
    subject: `Group membership changes (${n} group${n === 1 ? '' : 's'})`,
    body: lines.join('\n'),
  });
  console.log(`Sent change alert for ${n} group(s) to ${ALERT_EMAIL}.`);
}

// ── Trigger setup (run once manually) ────────────────────────────────────────

/**
 * Run this function once from the GAS editor to install a daily trigger.
 * Do not run it more than once or duplicate triggers will be created.
 */
function installTrigger() {
  // Remove any existing syncAll triggers first to avoid duplicates
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'syncAll')
    .forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('syncAll')
    .timeBased()
    .everyHours(1)
    .create();

  console.log('Hourly trigger installed.');
}
