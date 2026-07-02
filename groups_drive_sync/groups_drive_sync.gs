/**
 * Groups–Drive Sync
 *
 * Keeps each Google Group's membership in sync with the "Content manager"
 * (fileOrganizer) permissions on its corresponding Google Drive folder.
 *
 * Setup:
 *   1. Edit FOLDER_IDS in folder_ids.gs (group email -> Drive folder ID).
 *   2. In the GAS editor: Extensions > Advanced Services > enable "Admin SDK Directory API" and "Drive API".
 *   3. Run syncAll() once manually to authorize and test.
 *   4. Run installTrigger() once to schedule daily execution.
 */

// ── Configuration ────────────────────────────────────────────────────────────

const SYNC_ROLE = 'fileOrganizer'; // Drive role for "Content manager"

// FOLDER_IDS is defined in folder_ids.gs (shared with other scripts)

// ── Main entry point ─────────────────────────────────────────────────────────

function syncAll() {
  const runner = Session.getActiveUser().getEmail();
  console.log(`syncAll started — runner: ${runner}`);

  const missing = [];

  for (const groupEmail of Object.keys(FOLDER_IDS)) {
    const folderId = FOLDER_IDS[groupEmail];
    try {
      const skipped = syncFolder(folderId, groupEmail, runner);
      if (skipped) missing.push(groupEmail);
    } catch (err) {
      console.error(`Error processing ${groupEmail} (folder ${folderId}): ${err}`);
    }
  }

  if (missing.length > 0) {
    throw new Error(`No matching group found for: ${missing.join(', ')}`);
  }

  console.log('syncAll complete.');
}

function syncFolder(folderId, groupEmail, runnerEmail) {
  const folder = DriveApp.getFolderById(folderId);
  const folderName = folder.getName();

  console.log(`Folder: "${folderName}" → group: ${groupEmail}`);

  try {
    AdminDirectory.Groups.get(groupEmail);
  } catch (err) {
    if (err.message && (err.message.includes('404') || err.message.includes('Resource Not Found'))) {
      console.warn(`  No matching group found — skipping`);
      return true;
    }
    throw err;
  }

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
