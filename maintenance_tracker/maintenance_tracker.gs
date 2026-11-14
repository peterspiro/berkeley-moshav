/**
 * The maintenance tracker: what happens to the Maintenance & Warranty
 * Circle's Airtable base ("Building Maintenance") without anyone doing it.
 * Runs as admin@berkeleymoshav.org. maintenance-tracker-plan.md is the
 * design; README.md here says how to deploy it and what to set.
 *
 * Every 10 minutes (processRequests):
 *  - A new request: its reporter is linked, if they typed an email address
 *    that's a member's; its location is set to the reporter's unit, if they
 *    ticked "In my unit"; the reporter is emailed that it arrived; and the
 *    circle is emailed at once if it's an emergency or urgent.
 *  - A request whose status changed (or whose visit moved): the change is
 *    added to Updates, the status page's wording is set, the day it closed
 *    is recorded, and the reporter is emailed about a visit, about work
 *    that's done, or that it's closed.
 *  - An update marked "Tell the reporter" is emailed to them.
 *
 * Every day (daily):
 *  - Each person's own link to the request form is written to People.
 *  - Every table, and every photo and attachment not yet copied, is saved
 *    to Drive (backup).
 *
 * The rules, and the emails' wording, are in tracker_rules.gs. Each record
 * carries its own marker of what's been done to it ("Acknowledged on",
 * "Status noted", "Told reporter on"), written after the work is done, so a
 * run that fails partway misses nothing, and repeats only the step it was on.
 *
 * Script Properties (Project Settings): see README.md. None is in the
 * repo, which is public.
 */

const TIME_ZONE = 'America/Los_Angeles';
const SENDER_NAME = 'Berkeley Moshav Maintenance';
const AIRTABLE_API = 'https://api.airtable.com/v0';
// Every table, for the backup. The script's token can't list them.
const TABLES = ['Locations', 'People', 'Requests', 'Updates', 'Claims', 'Warranties',
                'Organizations', 'Assets', 'Maintenance plans', 'Maintenance work', 'Guides'];
// Requests changed since the last run are looked at again, with this much
// overlap between runs.
const OVERLAP_MS = 5 * 60 * 1000;
// Attachments copied to Drive in one run of the backup; the rest wait for
// the next, to stay inside a run's time limit.
const MAX_ATTACHMENTS_PER_RUN = 100;
const BATCH = 10;  // records per Airtable write

const PROP_LAST_RUN = 'lastRun';  // ISO time the last processRequests began

/** Calls Airtable's API; returns the parsed response, or throws with what it said. */
function airtable(method, path, payload) {
  const options = {
    method: method,
    headers: { Authorization: 'Bearer ' + setting('AIRTABLE_TOKEN') },
    muteHttpExceptions: true,
  };
  if (payload) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(payload);
  }
  const response = UrlFetchApp.fetch(`${AIRTABLE_API}/${setting('AIRTABLE_BASE_ID')}/${path}`, options);
  if (response.getResponseCode() >= 400) {
    throw new Error(`Airtable ${method} ${path}: ${response.getResponseCode()} ${response.getContentText().slice(0, 300)}`);
  }
  return JSON.parse(response.getContentText());
}

/**
 * Saves every table to Drive, as a JSON file each in a folder named for
 * today, and copies each photo and attachment not already there into an
 * "Attachments" folder (Airtable's own links to them expire). The folder is
 * BACKUP_FOLDER_ID; without it, nothing is saved.
 */
function backup() {
  const folderId = setting('BACKUP_FOLDER_ID', true);
  if (!folderId) {
    console.log('No BACKUP_FOLDER_ID: nothing backed up.');
    return;
  }
  const root = DriveApp.getFolderById(folderId);
  const today = Utilities.formatDate(new Date(), TIME_ZONE, 'yyyy-MM-dd');
  const day = subfolder(root, today);
  const attachments = subfolder(root, 'Attachments');
  let copied = 0, waiting = 0;
  TABLES.forEach(table => {
    const records = listRecords(table);
    const name = `${table}.json`;
    const old = day.getFilesByName(name);
    while (old.hasNext()) old.next().setTrashed(true);
    day.createFile(name, JSON.stringify(records, null, 1), 'application/json');
    records.forEach(record => attachmentsIn(record).forEach(a => {
      const fileName = `${a.id} ${a.filename}`;
      if (attachments.getFilesByName(fileName).hasNext()) return;
      if (copied >= MAX_ATTACHMENTS_PER_RUN) { waiting++; return; }
      attachments.createFile(UrlFetchApp.fetch(a.url).getBlob().setName(fileName));
      copied++;
    }));
  });
  console.log(`Backed up ${TABLES.length} tables to ${today}; ${copied} attachment(s) copied` +
              (waiting ? `, ${waiting} left for the next run.` : '.'));
}

/** What the rules need from outside a record (see tracker_rules.gs). */
function context(people, locations) {
  const peopleById = {}, locationNames = {};
  people.forEach(p => { peopleById[p.id] = p; });
  locations.forEach(l => { locationNames[l.id] = l.fields['Name']; });
  return {
    peopleById: peopleById,
    locationNames: locationNames,
    timeZone: TIME_ZONE,
    formUrl: setting('REQUEST_FORM_URL', true),
    statusPageUrl: setting('STATUS_PAGE_URL', true),
    triageUrl: setting('TRIAGE_URL', true),
    emergencyNote: setting('EMERGENCY_NOTE', true),
  };
}

/** Creates records, [{ fields }], in a table. */
function createRecords(table, records) {
  for (let i = 0; i < records.length; i += BATCH) {
    airtable('post', encodeURIComponent(table), { records: records.slice(i, i + BATCH), typecast: true });
  }
}

/** Once a day: the form links, then the backup. Each runs even if the other fails. */
function daily() {
  let failure = null;
  [writeFormLinks, backup].forEach(step => {
    try {
      withLock(step);
    } catch (e) {
      console.error(`${step.name} failed: ${e && e.stack || e}`);
      failure = failure || e;
    }
  });
  if (failure) throw failure;
}

/** Run once, by hand, after deploying: (re)creates the timers. */
function installTriggers() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('processRequests').timeBased().everyMinutes(10).create();
  ScriptApp.newTrigger('daily').timeBased().everyDays(1).atHour(3).inTimezone(TIME_ZONE).create();
}

/** Every record of a table, or those matching an Airtable formula. */
function listRecords(table, formula) {
  const records = [];
  let offset = null;
  do {
    const params = ['pageSize=100'];
    if (formula) params.push('filterByFormula=' + encodeURIComponent(formula));
    if (offset) params.push('offset=' + encodeURIComponent(offset));
    const page = airtable('get', `${encodeURIComponent(table)}?${params.join('&')}`);
    page.records.forEach(r => records.push(r));
    offset = page.offset;
  } while (offset);
  return records;
}

/** Acknowledges a new request (see the file's comment). Returns the request as it now is. */
function processNewRequest(request, people, ctx) {
  const fields = newRequestFields(request, people);
  const current = { id: request.id, fields: Object.assign({}, request.fields, fields) };
  const ack = acknowledgmentEmail(current, ctx);
  if (ack) sendMail(ack);
  const alert = urgentAlert(current, ctx);
  if (alert) sendMail(Object.assign({ to: setting('CIRCLE_EMAIL') }, alert));
  fields['Acknowledged on'] = new Date().toISOString();
  fields['Status noted'] = statusKey(current);
  fields['Member-visible status'] = memberStatus(current, TIME_ZONE);
  updateRecords('Requests', [{ id: request.id, fields: fields }]);
  current.fields = Object.assign(current.fields, fields);
  return current;
}

/**
 * Every 10 minutes: new requests, changes of status, and updates to pass
 * on (see the file's comment). Looks at the requests not yet acknowledged
 * and those changed since the last run; with nothing to do, that's two
 * calls to Airtable.
 */
function processRequests() {
  withLock(() => {
    const props = PropertiesService.getScriptProperties();
    const began = new Date();
    const last = props.getProperty(PROP_LAST_RUN);
    const since = new Date(last ? new Date(last).getTime() - OVERLAP_MS : 0).toISOString();
    const requests = listRecords('Requests',
      `OR({Acknowledged on} = BLANK(), IS_AFTER(LAST_MODIFIED_TIME(), DATETIME_PARSE("${since}")))`);
    const updates = listRecords('Updates', 'AND({Tell the reporter}, {Told reporter on} = BLANK())')
      .filter(u => u.fields['Tell the reporter'] && !u.fields['Told reporter on']);
    const fresh = requests.filter(needsAcknowledging);
    const changed = requests.filter(r => !needsAcknowledging(r) && statusKey(r) !== r.fields['Status noted']);

    if (fresh.length || changed.length || updates.length) {
      const people = listRecords('People');
      const ctx = context(people, listRecords('Locations'));
      fresh.forEach(request => processNewRequest(request, people, ctx));
      changed.forEach(request => processStatusChange(request, ctx));
      updates.forEach(update => processUpdate(update, ctx));
      console.log(`${fresh.length} new request(s), ${changed.length} status change(s), ` +
                  `${updates.length} update(s) passed on.`);
    }
    props.setProperty(PROP_LAST_RUN, began.toISOString());
  });
}

/** Records a request's change of status, and tells its reporter (see the file's comment). */
function processStatusChange(request, ctx) {
  const previous = request.fields['Status noted'];
  const fields = {
    'Status noted': statusKey(request),
    'Member-visible status': memberStatus(request, TIME_ZONE),
  };
  if (request.fields['Status'] === 'Closed' && !request.fields['Closed on']) {
    fields['Closed on'] = Utilities.formatDate(new Date(), TIME_ZONE, 'yyyy-MM-dd');
  }
  // The marker last: if a run fails here, the next sends the email again,
  // which is better than a visit nobody was told about.
  const email = statusEmail(request, previous, ctx);
  if (email) sendMail(email);
  createRecords('Updates', [{ fields: {
    'Request': [request.id], 'Kind': 'Status change', 'When': new Date().toISOString(),
    'By name': 'Maintenance tracker', 'Text': statusUpdateText(previous, request, TIME_ZONE),
  } }]);
  updateRecords('Requests', [{ id: request.id, fields: fields }]);
}

/** Emails an update marked "Tell the reporter" to its request's reporter. */
function processUpdate(update, ctx) {
  const requestId = (update.fields['Request'] || [])[0];
  if (requestId) {
    const request = airtable('get', `Requests/${requestId}`);
    const email = updateEmail(update, request, ctx);
    if (email) sendMail(email);
  }
  updateRecords('Updates', [{ id: update.id, fields: { 'Told reporter on': new Date().toISOString() } }]);
}

/**
 * Sends { to, subject, body }, from the account the script runs as, with
 * replies going to the circle. While SEND_ALL_MAIL_TO is set, every email
 * goes to that address instead, saying who it was for: for trying the
 * script out without emailing members.
 */
function sendMail(email) {
  const override = setting('SEND_ALL_MAIL_TO', true);
  MailApp.sendEmail({
    to: override || email.to,
    subject: override ? `[test, for ${email.to}] ${email.subject}` : email.subject,
    body: email.body,
    name: SENDER_NAME,
    replyTo: setting('CIRCLE_EMAIL'),
  });
}

/** A Script Property's value. Throws if it isn't set, unless `optional` (then ''). */
function setting(name, optional) {
  const value = PropertiesService.getScriptProperties().getProperty(name) || '';
  if (!value && !optional) throw new Error(`Set the Script Property ${name} (see README.md).`);
  return value;
}

/** The folder named `name` in `parent`, created if it isn't there. */
function subfolder(parent, name) {
  const found = parent.getFoldersByName(name);
  return found.hasNext() ? found.next() : parent.createFolder(name);
}

/** Changes records, [{ id, fields }], in a table. */
function updateRecords(table, records) {
  for (let i = 0; i < records.length; i += BATCH) {
    airtable('patch', encodeURIComponent(table), { records: records.slice(i, i + BATCH), typecast: true });
  }
}

/** Runs fn with the script's lock, so two runs can't overlap; a run that can't get it gives up. */
function withLock(fn) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30 * 1000)) {
    console.log('Another run is going; skipping this one.');
    return;
  }
  try {
    fn();
  } finally {
    lock.releaseLock();
  }
}

/**
 * Writes each person's own link to the request form into People, where
 * it's missing or out of date. Needs REQUEST_FORM_URL; without it, nothing
 * is written.
 */
function writeFormLinks() {
  const formUrl = setting('REQUEST_FORM_URL', true);
  if (!formUrl) {
    console.log('No REQUEST_FORM_URL: no form links written.');
    return;
  }
  const locationNames = {};
  listRecords('Locations').forEach(l => { locationNames[l.id] = l.fields['Name']; });
  const changes = formLinkChanges(listRecords('People'), locationNames, formUrl);
  updateRecords('People', changes);
  console.log(`${changes.length} form link(s) written.`);
}
