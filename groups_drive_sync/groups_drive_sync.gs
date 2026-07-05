/**
 * Groups–Drive Sync
 *
 * Keeps each Google Group's membership in sync with the "Content manager"
 * (fileOrganizer) permissions on its corresponding Google Drive folder.
 *
 * The folder <-> group mapping isn't stored in this project at all: it
 * enumerates every group in the workspace and reads the Drive folder ID
 * straight out of each group's own custom footer (the "Google Docs
 * folder" link in the "--- Auto-managed links ---" block — see
 * util/google_group_footer.py, which writes it). Groups with no such link
 * are skipped. This means adding/removing a group never requires
 * redeploying this Apps Script project.
 *
 * Setup:
 *   1. In the GAS editor: Extensions > Advanced Services > enable
 *      "Admin SDK Directory API", "Drive API", and "Groups Settings API".
 *   2. Run syncAll() once manually to authorize and test.
 *   3. Run installTrigger() once to schedule daily execution.
 */

// ── Configuration ────────────────────────────────────────────────────────────

const DOMAIN = 'berkeleymoshav.org';
const SYNC_ROLE = 'fileOrganizer'; // Drive role for "Content manager"

// Matches util.google_group_footer.drive_folder_url()'s output.
const FOOTER_FOLDER_URL_RE = /https:\/\/drive\.google\.com\/drive\/folders\/([a-zA-Z0-9_-]+)/;

// ── Main entry point ─────────────────────────────────────────────────────────

function syncAll() {
  const runner = Session.getActiveUser().getEmail();
  console.log(`syncAll started — runner: ${runner}`);

  const groups = listAllGroups();
  console.log(`Found ${groups.length} group(s) in ${DOMAIN}.`);

  let synced = 0;
  let skipped = 0;

  for (const group of groups) {
    try {
      const folderId = getFooterFolderId(group.email);
      if (!folderId) {
        skipped++;
        continue;
      }
      syncFolder(folderId, group.email, runner);
      synced++;
    } catch (err) {
      console.error(`Error processing ${group.email}: ${err}`);
    }
  }

  console.log(`syncAll complete — synced ${synced}, skipped ${skipped} (no folder link in footer).`);
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

function getFooterFolderId(groupEmail) {
  const settings = GroupsSettings.Groups.get(groupEmail);
  const footer = settings.customFooterText || '';
  const match = footer.match(FOOTER_FOLDER_URL_RE);
  return match ? match[1] : null;
}

function syncFolder(folderId, groupEmail, runnerEmail) {
  const folder = DriveApp.getFolderById(folderId);
  const folderName = folder.getName();

  console.log(`Folder: "${folderName}" → group: ${groupEmail}`);

  const targetEmails = getContentManagerEmails(folderId);
  const { added, removed } = syncGroupMembership(groupEmail, targetEmails, runnerEmail);

  console.log(`  Done — added: ${added}, removed: ${removed}`);
}

// ── Drive helpers ─────────────────────────────────────────────────────────────

function getContentManagerEmails(folderId) {
  const emails = new Set();
  let pageToken;
  do {
    const response = Drive.Permissions.list(folderId, {
      supportsAllDrives: true,
      fields: 'nextPageToken,permissions(emailAddress,role,type)',
      pageToken,
    });
    for (const p of (response.permissions || [])) {
      if (p.role === SYNC_ROLE && p.type === 'user') {
        emails.add(p.emailAddress.toLowerCase());
      }
    }
    pageToken = response.nextPageToken;
  } while (pageToken);
  return emails;
}

// ── Group helpers ─────────────────────────────────────────────────────────────

function syncGroupMembership(groupEmail, targetEmails, runnerEmail) {
  const currentMembers = listAllMembers(groupEmail);
  const currentEmails = new Set(currentMembers.map(m => m.email.toLowerCase()));

  let added = 0;
  let removed = 0;

  for (const email of targetEmails) {
    if (!currentEmails.has(email)) {
      AdminDirectory.Members.insert({ email, role: 'MEMBER' }, groupEmail);
      console.log(`  + ${email}`);
      added++;
    }
  }

  for (const email of currentEmails) {
    // Never remove the script runner — they may be an owner/manager
    if (email === runnerEmail.toLowerCase()) continue;
    if (!targetEmails.has(email)) {
      AdminDirectory.Members.remove(groupEmail, email);
      console.log(`  - ${email}`);
      removed++;
    }
  }

  return { added, removed };
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
