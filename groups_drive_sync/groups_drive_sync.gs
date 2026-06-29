/**
 * Groups–Drive Sync
 *
 * Keeps each Google Group's membership in sync with the "Content manager"
 * (fileOrganizer) permissions on its corresponding Google Drive folder.
 *
 * Setup:
 *   1. Edit DOMAIN and FOLDER_IDS below.
 *   2. In the GAS editor: Extensions > Advanced Services > enable "Admin SDK Directory API".
 *   3. Run syncAll() once manually to authorize and test.
 *   4. Run installTrigger() once to schedule daily execution.
 */

// ── Configuration ────────────────────────────────────────────────────────────

const DOMAIN = 'berkeleymoshav.org';

const SYNC_ROLE = 'fileOrganizer'; // Drive role for "Content manager"

// FOLDER_IDS is defined in folder_ids.gs (shared with other scripts)

// ── Main entry point ─────────────────────────────────────────────────────────

function syncAll() {
  const runner = Session.getActiveUser().getEmail();
  console.log(`syncAll started — runner: ${runner}`);

  for (const folderId of FOLDER_IDS) {
    try {
      syncFolder(folderId, runner);
    } catch (err) {
      console.error(`Error processing folder ${folderId}: ${err}`);
    }
  }

  console.log('syncAll complete.');
}

function syncFolder(folderId, runnerEmail) {
  const folder = DriveApp.getFolderById(folderId);
  const folderName = folder.getName();
  const groupEmail = folderNameToGroupEmail(folderName);

  console.log(`Folder: "${folderName}" → group: ${groupEmail}`);

  try {
    AdminDirectory.Groups.get(groupEmail);
  } catch (err) {
    if (err.message && err.message.includes('404')) {
      console.warn(`  No matching group found — skipping`);
      return;
    }
    throw err;
  }

  const targetEmails = getContentManagerEmails(folder);
  const { added, removed } = syncGroupMembership(groupEmail, targetEmails, runnerEmail);

  console.log(`  Done — added: ${added}, removed: ${removed}`);
}

// ── Drive helpers ─────────────────────────────────────────────────────────────

function getContentManagerEmails(folder) {
  const emails = new Set();
  const permissions = folder.getPermissions();
  for (const p of permissions) {
    if (p.getRole() === SYNC_ROLE && p.getType() === 'user') {
      emails.add(p.getEmail().toLowerCase());
    }
  }
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

// ── Naming helpers ────────────────────────────────────────────────────────────

function folderNameToGroupEmail(folderName) {
  return `${folderNameToSlug(folderName)}@${DOMAIN}`;
}

function folderNameToSlug(folderName) {
  return folderName
    .replace(/ *\([^)]*\)/g, '') // remove parenthesized expressions
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-') // non-alphanumeric runs → hyphen
    .replace(/^-+|-+$/g, '');    // strip leading/trailing hyphens
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
    .everyHours(24)
    .create();

  console.log('Daily trigger installed.');
}
