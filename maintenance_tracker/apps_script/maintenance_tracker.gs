/**
 * The maintenance tracker: what happens to the Maintenance & Warranty
 * Circle's Airtable base ("Building Maintenance") without anyone doing it.
 * Runs as admin@. ../maintenance-tracker-plan.md is
 * the design; README.md here says how to deploy it and what to set.
 *
 * Every 10 minutes, or every 2 hours on Airtable's Free plan, which
 * rations calls to its API (processRequests; see PLANS in tracker_rules.gs):
 *  - A new request: its reporter is linked, if they typed an email address
 *    that's a member's; its location is set to the reporter's unit, if they
 *    ticked "In my unit"; the reporter is emailed that it arrived; and the
 *    circle is emailed at once if it's an emergency or urgent.
 *  - A request that's been given a visit time is set to Scheduled.
 *  - A request given a warranty, with no Responsibility (or Unknown), is
 *    given its warranty's: Builder warranty or Manufacturer warranty.
 *  - A request whose status changed (or whose visit moved): the change is
 *    added to Updates, the status page's wording is set, the day it closed
 *    is recorded, and the reporter is emailed about a visit, about work
 *    that's done, or that it's closed.
 *  - An update marked "Tell the reporter" is emailed to them.
 *  - An update that doesn't say who wrote it, made by someone signed in to
 *    Airtable, is given its author (By), from the account that made it.
 *
 * Every day, or every week on the Free plan (housekeeping):
 *  - Each person's own link to the request form is written to People,
 *    and each circle member's to the "Log an update" form.
 *  - Each maintenance plan with no occurrence waiting to be done is given
 *    its next, in Maintenance work (createMaintenanceWork).
 *  - Every table, and every photo and attachment not yet copied, is saved
 *    to Drive (backup), and the circle is warned if the base is nearing
 *    what its plan allows.
 *  - On Sundays, the circle is emailed the weekly digest (sendDigest).
 *  - On a Sunday, every four weeks while a request with no claim made
 *    has a warranty deadline within 120 days, the circle is emailed where
 *    every request under each warranty stands (sendWarrantyReport, which
 *    can also be run by hand).
 *
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
                'Organizations', 'Assets', 'Maintenance plans', 'Maintenance work', 'Guides', 'Links'];
// Requests changed since the last run are looked at again, with this much
// overlap between runs.
const OVERLAP_MS = 5 * 60 * 1000;
// Attachments copied to Drive in one run of the backup; the rest wait for
// the next, to stay inside a run's time limit.
const MAX_ATTACHMENTS_PER_RUN = 100;
const BATCH = 10;  // records per Airtable write

// The script's notes about the base it's working on, each kept by base
// (see baseNote), so that pointing the script at another base starts afresh.
const PROP_LAST_RUN = 'lastRun';  // ISO time the last processRequests began
const PROP_LAST_UPDATES_CHECK = 'lastUpdatesCheck';  // ISO time updates were last looked for
const PROP_LAST_DIGEST = 'lastDigest';  // ISO time the weekly digest last went out
const PROP_LAST_WARRANTY_REPORT = 'lastWarrantyReport';  // ISO time the warranty report last went out
const PROP_USAGE_WARNED = 'usageWarned';  // the USAGE_LEVELS level the circle was last warned of
const CACHE_PEOPLE = 'people', CACHE_LOCATIONS = 'locations', CACHE_LINKS = 'links';
const CACHE_SECONDS = 6 * 60 * 60;  // the longest the cache keeps anything
const PROP_BACKUP_FOLDER = 'backupFolderId';  // set by backupFolder
// The base whose notes were kept, before notes were kept by base, under
// their plain names: the one the script was working on when that changed.
const PROP_FIRST_BASE = 'firstBase';
const BACKUP_FOLDER_NAME = 'Building Maintenance backup';
const FOLDER_TYPE = 'application/vnd.google-apps.folder';

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
 * "Attachments" folder (Airtable's own links to them expire). It all goes
 * in a folder the script makes for itself (see backupFolder). Then checks
 * how full the base is (checkUsage). `data` is every table's records, if
 * the caller has read them; run by hand, it reads them itself.
 */
function backup(data) {
  data = tableData(data);
  const root = backupFolder();
  const today = Utilities.formatDate(new Date(), TIME_ZONE, 'yyyy-MM-dd');
  const day = driveFolder(root, today);
  const attachments = driveFolder(root, 'Attachments');
  const saved = driveChildren(day), copied = driveChildren(attachments);
  const counts = {};
  let added = 0, waiting = 0;
  TABLES.forEach(table => {
    const records = data[table];
    counts[table] = records.length;
    const name = `${table}.json`;
    // A second run in a day replaces the first's file.
    if (saved[name]) Drive.Files.update({ trashed: true }, saved[name]);
    Drive.Files.create({ name: name, parents: [day], mimeType: 'application/json' },
                       Utilities.newBlob(JSON.stringify(records, null, 1), 'application/json', name));
    records.forEach(record => attachmentsIn(record).forEach(a => {
      const fileName = `${a.id} ${a.filename}`;
      if (copied[fileName]) return;
      if (added >= MAX_ATTACHMENTS_PER_RUN) { waiting++; return; }
      Drive.Files.create({ name: fileName, parents: [attachments] },
                         UrlFetchApp.fetch(a.url).getBlob().setName(fileName));
      copied[fileName] = true;
      added++;
    }));
  });
  console.log(`Backed up ${TABLES.length} tables to ${today}; ${added} attachment(s) copied` +
              (waiting ? `, ${waiting} left for the next run.` : '.'));
  checkUsage(counts, attachmentBytes(data));
}

/**
 * The ID of the folder the backup goes in: one the script makes, the first
 * time, in the My Drive of the account it runs as, and remembers. The
 * script is allowed only the Drive files it creates itself
 * (appsscript.json's drive.file scope), not the rest of the account's
 * Drive, so it can't use a folder a person made.
 */
function backupFolder() {
  let id = baseNote(PROP_BACKUP_FOLDER);
  if (!id) {
    id = Drive.Files.create({ name: BACKUP_FOLDER_NAME, mimeType: FOLDER_TYPE }).id;
    setBaseNote(PROP_BACKUP_FOLDER, id);
  }
  return id;
}

/**
 * One of the script's notes about the base it's working on (when it last
 * ran, its backup folder, and so on), or null. Each is a Script Property
 * named for the note and the base ("lastRun:app..."), so that the script,
 * pointed at another base, doesn't go by what it did to the last one, and
 * finds its notes again if it's pointed back. A note from before they were
 * kept by base, under its plain name, is read for the base the script was
 * working on then (see PROP_FIRST_BASE), until it's next set. (Which base
 * that was is taken from the first run after the change, so the script
 * has to run once before it's pointed elsewhere.)
 */
function baseNote(name) {
  const props = PropertiesService.getScriptProperties();
  const base = setting('AIRTABLE_BASE_ID');
  const kept = props.getProperty(`${name}:${base}`);
  if (kept !== null) return kept;
  return firstBase() === base ? props.getProperty(name) : null;
}

/**
 * A table's records, from the cache if they're there (for up to 6 hours),
 * to spare Airtable a call. Only for People and Locations, which change
 * seldom; a change made in Airtable can take that long to be noticed.
 */
function cachedRecords(table, key) {
  const cache = CacheService.getScriptCache();
  key = cacheKey(key);
  const hit = cache.get(key);
  if (hit) return JSON.parse(hit);
  const records = listRecords(table);
  try {
    cache.put(key, JSON.stringify(records), CACHE_SECONDS);
  } catch (e) {
    console.log(`${table} is too big to cache: ${e}`);
  }
  return records;
}

/** A cache key for the base the script is working on: another base's rows are never read from the cache. */
function cacheKey(key) {
  return `${key}:${setting('AIRTABLE_BASE_ID')}`;
}

/**
 * Emails the circle when the base passes 70%, 85%, or 95% of the records
 * or attachment storage its plan allows, once for each level.
 */
function checkUsage(counts, attachmentBytes) {
  const report = usageReport(counts, attachmentBytes, plan());
  const warning = usageWarning(report, Number(baseNote(PROP_USAGE_WARNED)) || 0, plan());
  if (warning) sendMail(Object.assign({ to: setting('CIRCLE_EMAIL') }, warning));
  setBaseNote(PROP_USAGE_WARNED, String(report.level));
  console.log(`${report.records} of ${report.recordLimit} records; ` +
              `${Math.round(report.bytes / 1048576)} MB of attachments.`);
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
    formUrl: link('request-form'),
    statusPageUrl: link('status-page'),
    triageUrl: link('triage-view'),
    emergencyNote: setting('EMERGENCY_NOTE', true),
  };
}

/**
 * Creates each maintenance plan's next occurrence in Maintenance work,
 * where it has none waiting to be done (see maintenanceWorkDue in
 * tracker_rules.gs): the first from the plan's First due, and the next
 * once the last is Done or Skipped. `data` is every table's records, if
 * the caller has read them; the new rows are added to it, for the steps
 * that follow.
 */
function createMaintenanceWork(data) {
  data = tableData(data);
  const made = createRecords('Maintenance work', maintenanceWorkDue(data));
  made.forEach(record => data['Maintenance work'].push(record));
  console.log(`${made.length} maintenance occurrence(s) created.`);
}

/** Creates records, [{ fields }], in a table, and returns them as created. */
function createRecords(table, records) {
  const made = [];
  for (let i = 0; i < records.length; i += BATCH) {
    const reply = airtable('post', encodeURIComponent(table), { records: records.slice(i, i + BATCH), typecast: true });
    (reply.records || []).forEach(record => made.push(record));
  }
  return made;
}

/** The files and folders in a Drive folder that aren't in the trash: { name: ID }. */
function driveChildren(folderId) {
  const children = {};
  let pageToken = null;
  do {
    const page = Drive.Files.list({
      q: `'${folderId}' in parents and trashed = false`,
      fields: 'nextPageToken, files(id, name)', pageSize: 1000, pageToken: pageToken,
    });
    (page.files || []).forEach(f => { children[f.name] = f.id; });
    pageToken = page.nextPageToken;
  } while (pageToken);
  return children;
}

/** The ID of the folder named `name` in a Drive folder, created if it isn't there. */
function driveFolder(parentId, name) {
  return driveChildren(parentId)[name] ||
    Drive.Files.create({ name: name, mimeType: FOLDER_TYPE, parents: [parentId] }).id;
}

/**
 * The base whose notes are the ones under plain names, from before notes
 * were kept by base (see baseNote): the base the script is working on the
 * first time this is asked, remembered from then on.
 */
function firstBase() {
  const props = PropertiesService.getScriptProperties();
  let first = props.getProperty(PROP_FIRST_BASE);
  if (!first) {
    first = setting('AIRTABLE_BASE_ID');
    props.setProperty(PROP_FIRST_BASE, first);
  }
  return first;
}

/**
 * Once a day, or once a week on the Free plan: the form links, the
 * maintenance plans' next occurrences, the backup, and, on a Sunday, the
 * digest and, when a warranty deadline is near, the warranty report. Every table is read once, for all of them.
 * Each step runs even if another fails.
 */
function housekeeping() {
  withLock(() => {
    const data = tableData();
    const steps = [writeFormLinks, createMaintenanceWork, backup];
    if (digestDue(new Date(), baseNote(PROP_LAST_DIGEST), TIME_ZONE)) steps.push(sendDigest);
    if (warrantyReportDue(data, new Date(), baseNote(PROP_LAST_WARRANTY_REPORT), TIME_ZONE)) {
      steps.push(sendWarrantyReport);
    }
    let failure = null;
    steps.forEach(step => {
      try {
        step(data);
      } catch (e) {
        console.error(`${step.name} failed: ${e && e.stack || e}`);
        failure = failure || e;
      }
    });
    if (failure) throw failure;
  });
}

/**
 * Run by hand after deploying, and again after changing AIRTABLE_PLAN:
 * (re)creates the timers, as often as the plan allows (see PLANS).
 */
function installTriggers() {
  const limits = PLANS[plan()];
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  const check = ScriptApp.newTrigger('processRequests').timeBased();
  (limits.checkEveryHours ? check.everyHours(limits.checkEveryHours)
                          : check.everyMinutes(limits.checkEveryMinutes)).create();
  const keeping = ScriptApp.newTrigger('housekeeping').timeBased().atHour(3).inTimezone(TIME_ZONE);
  (limits.housekeepingWeekly ? keeping.onWeekDay(ScriptApp.WeekDay.SUNDAY) : keeping.everyDays(1)).create();
  console.log(`Timers set for the ${plan()} plan.`);
}

/**
 * A link's Value in the Links table, by its key ("request-form" for the
 * row named "Request form"; see linksByKey), or '' if it has none. `data`,
 * if given, is every table's records; otherwise Links is read, through the
 * cache, so a link changed in the base is used within CACHE_SECONDS.
 */
function link(key, data) {
  const records = data && Array.isArray(data['Links']) ? data['Links'] : cachedRecords('Links', CACHE_LINKS);
  return linksByKey(records)[key] || '';
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

/**
 * The Airtable plan the base's workspace is on, 'free' or 'team', from the
 * Script Property AIRTABLE_PLAN. Anything else, or nothing, counts as
 * free, the plan that's safe to assume.
 */
function plan() {
  return setting('AIRTABLE_PLAN', true).trim().toLowerCase() === 'team' ? 'team' : 'free';
}

/** Acknowledges a new request (see the file's comment). Returns the request as it now is. */
function processNewRequest(request, people, ctx) {
  const fields = newRequestFields(request, people);
  const responsibility = responsibilityFromWarranty(request);
  if (responsibility) fields['Responsibility'] = responsibility;
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
 * On the timer: new requests, changes of status, and updates to pass on
 * (see the file's comment). Looks at the requests not yet acknowledged and
 * those changed since the last run, and, when they're due a look
 * (updatesDue), for updates to pass on; with nothing to do, that's one or
 * two calls to Airtable. Run by hand from the editor (when there's no
 * `event`), it looks for updates whether they're due or not.
 */
function processRequests(event) {
  withLock(() => {
    const began = new Date();
    const last = baseNote(PROP_LAST_RUN);
    const since = new Date(last ? new Date(last).getTime() - OVERLAP_MS : 0).toISOString();
    const requests = listRecords('Requests',
      `OR({Acknowledged on} = BLANK(), IS_AFTER(LAST_MODIFIED_TIME(), DATETIME_PARSE("${since}")))`);
    const lookForUpdates = !event || updatesDue(plan(), baseNote(PROP_LAST_UPDATES_CHECK), began);
    // Updates to pass on, and updates that don't say who wrote them.
    const listed = !lookForUpdates ? [] : listRecords('Updates',
      'OR(AND({Tell the reporter}, {Told reporter on} = BLANK()), AND(NOT({By}), NOT({By name})))');
    const updates = listed.filter(u => u.fields['Tell the reporter'] && !u.fields['Told reporter on']);
    const unsigned = listed.filter(u => !(u.fields['By'] || []).length && !u.fields['By name']);
    const fresh = requests.filter(needsAcknowledging);
    const changed = requests.filter(r => !needsAcknowledging(r) && statusKey(r) !== r.fields['Status noted']);
    // Responsibility from the warranty: written with the request's other
    // changes when it has some, and on its own when it hasn't.
    const responsibilities = requests.filter(r => !fresh.includes(r) && !changed.includes(r))
      .map(r => ({ id: r.id, fields: { 'Responsibility': responsibilityFromWarranty(r) } }))
      .filter(change => change.fields['Responsibility']);
    updateRecords('Requests', responsibilities);

    if (fresh.length || changed.length || updates.length || unsigned.length) {
      const people = cachedRecords('People', CACHE_PEOPLE);
      const authors = unsigned.map(u => ({ id: u.id, fields: { 'By': [updateAuthor(u, people)] } }))
        .filter(change => change.fields['By'][0]);
      updateRecords('Updates', authors);
      const ctx = context(people, cachedRecords('Locations', CACHE_LOCATIONS));
      fresh.forEach(request => processNewRequest(request, people, ctx));
      changed.forEach(request => processStatusChange(request, ctx));
      updates.forEach(update => processUpdate(update, ctx));
    }
    console.log(`${fresh.length} new request(s), ${changed.length} status change(s), ` +
                `${responsibilities.length} responsibility(ies) filled in, ` +
                (lookForUpdates ? `${updates.length} update(s) passed on.`
                                : 'updates to pass on not looked for this time.'));
    setBaseNote(PROP_LAST_RUN, began.toISOString());
    if (lookForUpdates) setBaseNote(PROP_LAST_UPDATES_CHECK, began.toISOString());
  });
}

/**
 * Deals with a request whose status or visit time isn't what was noted
 * (see the file's comment): schedules it, if it's been given a visit time;
 * then records the change and tells its reporter, if it's one to record.
 */
function processStatusChange(request, ctx) {
  const previous = request.fields['Status noted'];
  const fields = {};
  if (visitSchedules(request, previous, new Date())) {
    request.fields['Status'] = fields['Status'] = 'Scheduled';
  }
  fields['Status noted'] = statusKey(request);
  fields['Member-visible status'] = memberStatus(request, TIME_ZONE);
  const responsibility = responsibilityFromWarranty(request);
  if (responsibility) fields['Responsibility'] = responsibility;
  if (request.fields['Status'] === 'Closed' && !request.fields['Closed on']) {
    fields['Closed on'] = Utilities.formatDate(new Date(), TIME_ZONE, 'yyyy-MM-dd');
  }
  if (statusNoteworthy(request, previous)) {
    // The marker last: if a run fails here, the next sends the email again,
    // which is better than a visit nobody was told about.
    const email = statusEmail(request, previous, ctx);
    if (email) sendMail(email);
    createRecords('Updates', [{ fields: {
      'Request': [request.id], 'Kind': 'Status change', 'When': new Date().toISOString(),
      'By name': 'Maintenance tracker', 'Text': statusUpdateText(previous, request, TIME_ZONE),
    } }]);
  }
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
 * Emails the circle the weekly digest (see digest in tracker_rules.gs):
 * what came in, what needs someone, and what's repeating. On the timer it
 * goes out on Sundays; run by hand, it goes now. `data` is every table's
 * records, if the caller has read them.
 */
function sendDigest(data) {
  const all = tableData(data);
  const email = digest(all, new Date(), { timeZone: TIME_ZONE, triageUrl: link('triage-view', all), plan: plan() });
  if (email) sendMail(Object.assign({ to: setting('CIRCLE_EMAIL') }, email));
  else console.log('Nothing open, new, or due: no digest.');
  setBaseNote(PROP_LAST_DIGEST, new Date().toISOString());
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

/**
 * Emails the circle where every request under each warranty stands (see
 * warrantyReport in tracker_rules.gs), so that nothing is left unclaimed
 * when a warranty ends. The housekeeping runs it on a Sunday, every four
 * weeks, while a request with no claim made has a deadline coming (see
 * warrantyReportDue); run by hand, it goes now, and the next by itself
 * is four weeks on. By hand it reads the five tables it needs. `data` is every table's records, if a
 * caller has read them.
 */
function sendWarrantyReport(data) {
  const all = data && Array.isArray(data['Requests']) ? data : {};
  if (all !== data) {
    ['Requests', 'Claims', 'Warranties', 'Organizations', 'Locations'].forEach(table => { all[table] = listRecords(table); });
  }
  sendMail(Object.assign({ to: setting('CIRCLE_EMAIL') }, warrantyReport(all, new Date(), { timeZone: TIME_ZONE })));
  setBaseNote(PROP_LAST_WARRANTY_REPORT, new Date().toISOString());
}

/**
 * Sets one of the script's notes about the base it's working on (see
 * baseNote), and drops the note's copy from before notes were kept by
 * base, if it was this base's.
 */
function setBaseNote(name, value) {
  const props = PropertiesService.getScriptProperties();
  const base = setting('AIRTABLE_BASE_ID');
  const first = firstBase();
  props.setProperty(`${name}:${base}`, value);
  if (first === base) props.deleteProperty(name);
}

/** A Script Property's value. Throws if it isn't set, unless `optional` (then ''). */
function setting(name, optional) {
  const value = PropertiesService.getScriptProperties().getProperty(name) || '';
  if (!value && !optional) throw new Error(`Set the Script Property ${name} (see README.md).`);
  return value;
}

/**
 * Every table's records, { table: [records] }: `data` itself, if it's that
 * already; otherwise read from Airtable (as when a function that takes
 * `data` is run by hand, or by a timer, which passes its event instead).
 */
function tableData(data) {
  if (data && Array.isArray(data[TABLES[0]])) return data;
  const read = {};
  TABLES.forEach(table => { read[table] = listRecords(table); });
  return read;
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
 * Writes each person's own links to the forms into People, where they're
 * missing or out of date: to the request form (its link in Links), and,
 * for circle members, to the "Log an update" form (likewise). Without
 * a form's address, its links are left alone. `data` is every table's
 * records, if the caller has read them; its People are brought up to date
 * too.
 */
function writeFormLinks(data) {
  const formUrl = link('request-form', data), updateFormUrl = link('update-form', data);
  if (!formUrl && !updateFormUrl) {
    console.log('No Request form or Update form in Links: no form links written.');
    return;
  }
  const given = data && Array.isArray(data['People']);
  const people = given ? data['People'] : listRecords('People');
  const locationNames = {};
  (given ? data['Locations'] : listRecords('Locations')).forEach(l => { locationNames[l.id] = l.fields['Name']; });
  const changes = formLinkChanges(people, locationNames, formUrl, updateFormUrl);
  updateRecords('People', changes);
  changes.forEach(change => {
    const fields = people.filter(p => p.id === change.id)[0].fields;
    Object.keys(change.fields).forEach(k => {
      if (change.fields[k] === null) delete fields[k]; else fields[k] = change.fields[k];
    });
  });
  if (changes.length) CacheService.getScriptCache().remove(cacheKey(CACHE_PEOPLE));
  console.log(`Form links written for ${changes.length} person(s).`);
}
