/**
 * Recording links
 *
 * Puts links to Zoom's cloud recordings where members look (see "Recording
 * links" in ../meeting-infrastructure-plan.md; the rules, with no Apps
 * Script calls, are in recording_rules.gs).
 *
 * Every hour (syncRecordings, from hourly), each recording of the last
 * RECORDINGS_LOOKBACK_DAYS days that has finished processing, and hasn't
 * been handled, gets:
 *   1. a "Recording:" line in the description of the calendar event it
 *      came from (the one occurrence of a series), sending no email, if the
 *      event's calendar is one this account owns;
 *   2. for a group with an agenda doc, a "Recording:" line in the doc's
 *      tab for that meeting (titled with its date), below its "Time" line;
 *      a meeting with no tab yet is tried again hourly for DOC_RETRY_DAYS;
 *   3. a row in its sheet (see categorizeRecording): the group's
 *      "<Group> Meeting Recordings" in the group's Drive item, or
 *      "Community Meeting Recordings" in the Shared Drive's Meeting Admin
 *      folder, each made when first needed; one that can't be categorized
 *      goes on the community sheet's Unassigned tab, and ALERT_EMAIL gets
 *      an email about it.
 * Then a group typed in an Unassigned row's Group column moves that row to
 * the group's sheet, and the choice is kept for later runs.
 *
 * backfillRecordings, run by hand, lists every older recording (from
 * BACKFILL_FROM) in the sheets only, a month at a time, carrying on where
 * it stopped when run again; backfillRecordingsDryRun only logs where each
 * would go.
 */

// The community's Shared Drive (DEFAULT_DRIVE_ID in util/gdrive_config.py)
// and its folder of community calendars' admin groups' folders, where the
// community sheet goes (MEETING_ADMIN_FOLDER_NAME there).
const SHARED_DRIVE_ID = '0AFqC2xo9aTgPUk9PVA';
const MEETING_ADMIN_FOLDER_NAME = 'Meeting Admin';
// The Drive property a recordings sheet is found by: the group's address,
// or COMMUNITY.
const SHEET_PROPERTY = 'meetingRecordingsFor';
const SPREADSHEET_MIME = 'application/vnd.google-apps.spreadsheet';
// The rows of a sheet's Unassigned tab are kept under this key.
const UNASSIGNED = ':unassigned';
const RECORDINGS_LOOKBACK_DAYS = 7;
// Longer than the lookback, so no recording is handled twice.
const RECORDINGS_SEEN_DAYS = 30;
const DOC_RETRY_DAYS = 14;
// The first month the backfill lists.
const BACKFILL_FROM = '2020-01-01';
// A backfill run stops after this long, before Apps Script's 6 minutes.
const BACKFILL_RUN_MS = 4.5 * 60 * 1000;
const PROP_RECORDINGS_SEEN = 'recordingsSeen';     // JSON { recording ID: forgetAfterMs }
const PROP_RECORDING_DOCS = 'recordingDocs';       // JSON [{ docId, calendarId, date, url,
                                                   //         passcode, until }]
const PROP_RECORDING_CHOICES = 'recordingChoices'; // JSON { recording ID: destination }
const PROP_BACKFILL = 'recordingsBackfill';        // JSON { month, user, listed, unassigned, done }
const PROP_BACKFILL_DRY_RUN = 'recordingsBackfillDryRun';

/**
 * Add a recording's line to its meeting's tab in an agenda doc. Returns
 * 'done' (added, or there already), 'no tab' (none titled with the date),
 * or 'ambiguous' (more than one tab, or more than one meeting on the
 * group's calendar that day).
 */
function addRecordingToDoc(entry) {
  const doc = DocumentApp.openById(entry.docId);
  const tabs = allDocTabs(doc).filter(t => t.getTitle().trim() === entry.date);
  if (!tabs.length) return 'no tab';
  if (tabs.length > 1 || meetingsOnDay(entry.calendarId, entry.date) > 1) return 'ambiguous';
  const body = tabs[0].asDocumentTab().getBody();
  if (body.getText().includes(shareBase(entry.url))) return 'done';
  let index = 0;
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() === DocumentApp.ElementType.PARAGRAPH &&
        /^\s*Time\b/.test(el.asParagraph().getText())) {
      index = i + 1;
      break;
    }
  }
  const para = body.insertParagraph(index, RECORDING_TEXT + ' ');
  para.appendText(entry.url).setLinkUrl(entry.url);
  if (entry.passcode) para.appendText(` (passcode ${entry.passcode})`).setLinkUrl(null);
  doc.saveAndClose();
  return 'done';
}

/** Add a recording's line to its calendar event, if the event's organizer
 * is a calendar this account owns; sends no email. */
function addRecordingToEvent(event, rec, ctx) {
  const organizer = (event.organizer || {}).email || '';
  if (!ctx.ownedCalendarIds.has(lower(organizer))) {
    console.log(`"${event.summary}": organized by ${organizer || 'someone else'}, so its ` +
                'event gets no recording link.');
    return;
  }
  const description = addRecordingLine(event.description, rec);
  if (description === null) return;
  Calendar.Events.patch({ description }, organizer, event.id, { sendUpdates: 'none' });
  console.log(`"${event.summary}": added its recording's link.`);
}

/** Every tab of a doc (DocumentApp), child tabs included. */
function allDocTabs(doc) {
  const flatten = tabs => tabs.flatMap(tab => [tab, ...flatten(tab.getChildTabs())]);
  return flatten(doc.getTabs());
}

/**
 * Move each Unassigned row whose Group column names a group (or
 * "community") to that sheet, keeping the choice (so the backfill lists
 * the recording there too); a row naming no group is told so in its Why
 * column.
 */
function applyUnassignedChoices(ctx) {
  const book = findRecordingsBook(COMMUNITY);
  const sheet = book && book.getSheetByName(UNASSIGNED_TAB);
  if (!sheet || sheet.getLastRow() < 2) return;
  const n = UNASSIGNED_HEADINGS.length;
  const values = sheet.getRange(2, 1, sheet.getLastRow() - 1, n).getValues();
  const moved = [];
  values.forEach((row, i) => {
    const typed = String(row[0] || '').trim();
    if (!typed) return;
    const id = String(row[n - 1]);
    const dest = overrideDestination(typed, ctx.groups);
    let tab = null, why = null;
    if (!dest) {
      why = `"${typed}" isn't a group's name or address, or "community"`;
    } else {
      try {
        tab = recordingsTab(dest, ctx);
      } catch (err) {
        why = `Couldn't use the sheet for "${typed}": ${err.message}`;
      }
    }
    if (!tab) {
      if (row[UNASSIGNED_WHY_COLUMN] !== why) {
        sheet.getRange(i + 2, UNASSIGNED_WHY_COLUMN + 1).setValue(why);
      }
      return;
    }
    if (!tab.ids.has(id)) {
      tab.ids.add(id);
      // When, Title, Recording, Passcode, Length, Zoom topic, Recording ID.
      (ctx.rows[dest] = ctx.rows[dest] || []).push(row.slice(1, 7).concat([id]));
    }
    ctx.choices[id] = dest;
    moved.push(i + 2);
  });
  if (!moved.length) return;
  flushRecordingRows(ctx);
  setJson(PROP_RECORDING_CHOICES, ctx.choices);
  for (const r of moved.sort((a, b) => b - a)) sheet.deleteRow(r);
  console.log(`Moved ${moved.length} recording(s) off the ${UNASSIGNED_TAB} tab.`);
}

/** Run by hand: list older recordings in the sheets (see runBackfill). */
function backfillRecordings() {
  runBackfill(false);
}

/** Run by hand: log where each older recording would be listed, changing
 * nothing (see runBackfill). */
function backfillRecordingsDryRun() {
  runBackfill(true);
}

/** A new recordings sheet, laid out, for a group (by address) or COMMUNITY. */
function createRecordingsBook(owner, ctx) {
  const group = owner === COMMUNITY ? null : ctx.byEmail.get(owner);
  if (owner !== COMMUNITY && !group) throw new Error(`${owner} isn't a group with a calendar`);
  const parent = group ? group.driveId : meetingAdminFolderId();
  if (!parent) {
    throw new Error(group ? `${group.name} has no Drive folder in its footer`
      : `the Shared Drive has no "${MEETING_ADMIN_FOLDER_NAME}" folder`);
  }
  const name = group ? recordingsSheetName(group.name) : COMMUNITY_SHEET_NAME;
  const file = Drive.Files.create(
    { name, mimeType: SPREADSHEET_MIME, parents: [parent], properties: { [SHEET_PROPERTY]: owner } },
    null, { supportsAllDrives: true, fields: 'id' });
  const book = SpreadsheetApp.openById(file.id);
  book.setSpreadsheetTimeZone(TIME_ZONE);
  const first = book.getSheets()[0];
  first.setName(RECORDINGS_TAB);
  layOutRecordingsTab(first, RECORDINGS_HEADINGS);
  if (!group) layOutRecordingsTab(book.insertSheet(UNASSIGNED_TAB), UNASSIGNED_HEADINGS);
  console.log(`Created the sheet "${name}".`);
  return book;
}

/**
 * The calendar event a recording came from (see recordingEvent): for a
 * group's room, on the group's calendar; otherwise on the all-events
 * calendar (which every managed calendar's events invite) or another
 * calendar this account owns (where older meetings are). null if none.
 */
function findRecordingEvent(rec, ctx) {
  const room = ctx.groups.find(g => g.zoomUrl && meetingIdFromUrl(g.zoomUrl) === rec.meetingId);
  const calendars = room ? [room.calendarId] : [ALL_EVENTS_CALENDAR_ID, ...ctx.otherCalendarIds];
  const events = [];
  for (const id of calendars) {
    try {
      const resp = Calendar.Events.list(id, {
        singleEvents: true, maxResults: 250,
        timeMin: new Date(rec.start).toISOString(),
        timeMax: new Date(rec.start + EARLY_START_MS + 1000).toISOString(),
      });
      events.push(...(resp.items || []));
    } catch (err) {
      console.error(`Couldn't read calendar ${id}, to find "${rec.topic}": ${err}`);
    }
  }
  return recordingEvent(rec, events);
}

/** The recordings sheet for a group (by address) or COMMUNITY, or null. */
function findRecordingsBook(owner) {
  const resp = Drive.Files.list({
    q: `properties has { key='${SHEET_PROPERTY}' and value='${owner}' } and ` +
       `mimeType = '${SPREADSHEET_MIME}' and trashed = false`,
    corpora: 'allDrives', includeItemsFromAllDrives: true, supportsAllDrives: true,
    fields: 'files(id)',
  });
  const file = (resp.files || [])[0];
  return file ? SpreadsheetApp.openById(file.id) : null;
}

/** Write the rows waiting in ctx.rows to their sheets, newest first. */
function flushRecordingRows(ctx) {
  for (const key of Object.keys(ctx.rows)) {
    const rows = ctx.rows[key];
    if (!rows.length) continue;
    const tab = recordingsTab(key, ctx);
    const width = tab.headings.length;
    const first = tab.sheet.getLastRow() + 1;
    const short = first + rows.length - 1 - tab.sheet.getMaxRows();
    // New rows take the formats of the last one.
    if (short > 0) tab.sheet.insertRowsAfter(tab.sheet.getMaxRows(), short);
    tab.sheet.getRange(first, 1, rows.length, width).setValues(rows);
    tab.sheet.getRange(2, 1, tab.sheet.getLastRow() - 1, width)
      .sort({ column: tab.headings.indexOf('When') + 1, ascending: false });
    ctx.rows[key] = [];
  }
}

/** 'yyyy-MM-dd' in UTC, as Zoom's recording list takes dates. */
function isoDay(ms) {
  return Utilities.formatDate(new Date(ms), 'UTC', 'yyyy-MM-dd');
}

/** Headings, frozen; dates and times shown in full; passcodes, lengths,
 * and IDs kept as text; the ID column hidden. */
function layOutRecordingsTab(sheet, headings) {
  const n = headings.length;
  sheet.getRange(1, 1, 1, n).setValues([headings]).setFontWeight('bold');
  sheet.setFrozenRows(1);
  const column = heading => sheet.getRange(1, headings.indexOf(heading) + 1, sheet.getMaxRows(), 1);
  column('When').setNumberFormat('ddd yyyy-mm-dd h:mm am/pm');
  for (const h of ['Passcode', 'Length', 'Recording ID']) column(h).setNumberFormat('@');
  sheet.hideColumns(n);
}

/**
 * Queue a recording's row for its sheet (where.dest's, or the Unassigned
 * tab's), unless the sheet lists it already. One for the Unassigned tab is
 * added to ctx.uncategorized, for the alert. A group whose sheet can't be
 * made has its recording put on the Unassigned tab instead, saying why.
 */
function listRecording(rec, title, when, where, ctx) {
  const key = where.dest || UNASSIGNED;
  let tab;
  try {
    tab = recordingsTab(key, ctx);
  } catch (err) {
    if (key === UNASSIGNED) throw err;
    console.error(`Couldn't use ${key}'s recordings sheet: ${err.message}`);
    return listRecording(rec, title, when,
                         { dest: null, rule: `couldn't use ${key}'s sheet: ${err.message}` }, ctx);
  }
  if (tab.ids.has(rec.id)) return;
  tab.ids.add(rec.id);
  const row = recordingRow(rec, title);
  if (key === UNASSIGNED) {
    // Group, When, Title, Recording, Passcode, Length, Zoom topic, Zoom user, Why, Recording ID.
    (ctx.rows[key] = ctx.rows[key] || []).push(
      [''].concat(row.slice(0, 6), [rec.host, where.rule, rec.id]));
    ctx.uncategorized.push({ title, when, topic: rec.topic, host: rec.host, url: rec.url,
                             why: where.rule });
  } else {
    (ctx.rows[key] = ctx.rows[key] || []).push(row);
  }
}

/** The ID of the Shared Drive's Meeting Admin folder, or null. */
function meetingAdminFolderId() {
  const resp = Drive.Files.list({
    q: `'${SHARED_DRIVE_ID}' in parents and name = '${MEETING_ADMIN_FOLDER_NAME}' and ` +
       "mimeType = 'application/vnd.google-apps.folder' and trashed = false",
    corpora: 'drive', driveId: SHARED_DRIVE_ID, includeItemsFromAllDrives: true,
    supportsAllDrives: true, fields: 'files(id)',
  });
  const file = (resp.files || [])[0];
  return file ? file.id : null;
}

/** How many timed meetings a calendar organizes on a day ('yyyy-MM-dd'). */
function meetingsOnDay(calendarId, date) {
  const start = Utilities.parseDate(date, TIME_ZONE, 'yyyy-MM-dd').getTime();
  const resp = Calendar.Events.list(calendarId, {
    singleEvents: true, maxResults: 50,
    timeMin: new Date(start).toISOString(), timeMax: new Date(start + DAY_MS).toISOString(),
  });
  return (resp.items || []).filter(e => e.status !== 'cancelled' && (e.start || {}).dateTime &&
    lower((e.organizer || {}).email) === lower(calendarId)).length;
}

/** What one run's recordings share: the groups, which calendars this
 * account owns, the choices made on the Unassigned tab, and the sheets and
 * rows so far. */
function newRecordingsContext(groups) {
  const other = loadOtherCalendars(groups).map(c => c.id);
  return {
    groups,
    byEmail: new Map(groups.map(g => [g.email, g])),
    otherCalendarIds: other,
    ownedCalendarIds: new Set(groups.map(g => lower(g.calendarId)).concat(other.map(lower))),
    choices: getJson(PROP_RECORDING_CHOICES, {}),
    titles: null,     // see recurringTitlesFor
    sheets: {},       // destination or UNASSIGNED -> { sheet, headings, ids }
    rows: {},         // destination or UNASSIGNED -> rows waiting to be written
    docs: [],         // agenda doc lines to add
    uncategorized: [],
    counts: {},       // a dry run's: destination's name -> recordings
  };
}

/**
 * Handle one recording (an item of a Zoom user's recordings, hosted by
 * host): find its event, decide its sheet, and queue its row; unless
 * opts.backfill, add its line to its event and queue its agenda doc line.
 * With opts.dryRun, only log where it would go.
 */
function processRecording(m, host, ctx, opts) {
  const rec = recordingFromZoom(opts.dryRun ? m : recordingDetails(m), host);
  const event = findRecordingEvent(rec, ctx);
  const where = categorizeRecording(rec, {
    groups: ctx.groups, event, choices: ctx.choices,
    recurringTitles: () => recurringTitlesFor(ctx),
  });
  const title = (event && event.summary) || rec.topic || '(untitled)';
  const when = formatRange(rec.start, rec.start + rec.minutes * 60 * 1000);
  if (opts.dryRun) {
    const group = where.dest && ctx.byEmail.get(where.dest);
    const name = where.dest === COMMUNITY ? COMMUNITY_SHEET_NAME
      : group ? recordingsSheetName(group.name) : UNASSIGNED_TAB;
    ctx.counts[name] = (ctx.counts[name] || 0) + 1;
    console.log(`${when} "${title}" (${rec.host}) -> ${name} (${where.rule})`);
    return;
  }
  if (!rec.url) {
    console.warn(`The recording of "${title}" (${when}) has no share link; is cloud recording ` +
                 'sharing on in Zoom?');
  }
  if (!opts.backfill && event && rec.url) addRecordingToEvent(event, rec, ctx);
  const group = where.dest && where.dest !== COMMUNITY ? ctx.byEmail.get(where.dest) : null;
  const docId = group && group.agendaUrl ? (group.agendaUrl.match(GOOGLE_DOC_ID_RE) || [])[1] : null;
  if (!opts.backfill && docId && rec.url) {
    const start = event ? Date.parse(event.start.dateTime) : rec.start;
    ctx.docs.push({
      docId, calendarId: group.calendarId, url: rec.url, passcode: rec.passcode,
      date: Utilities.formatDate(new Date(start), TIME_ZONE, 'yyyy-MM-dd'),
      until: Date.now() + DOC_RETRY_DAYS * DAY_MS,
    });
  }
  listRecording(rec, title, when, where, ctx);
}

/** A recording with its share link and passcode, from Zoom's details of
 * that session's recording. */
function recordingDetails(m) {
  const d = zoomGet(`/meetings/${uuidPath(m.uuid)}/recordings`) || {};
  return Object.assign({}, m, {
    share_url: d.share_url || m.share_url,
    password: d.password !== undefined ? d.password : m.password,
    recording_play_passcode: d.recording_play_passcode || m.recording_play_passcode,
  });
}

/**
 * The tab rows go to, for a destination (a group's address, or COMMUNITY)
 * or UNASSIGNED: { sheet, headings, ids (the recording IDs it lists) },
 * making the sheet (and tab) if there's none.
 */
function recordingsTab(key, ctx) {
  if (ctx.sheets[key]) return ctx.sheets[key];
  const owner = key === UNASSIGNED ? COMMUNITY : key;
  const book = findRecordingsBook(owner) || createRecordingsBook(owner, ctx);
  const name = key === UNASSIGNED ? UNASSIGNED_TAB : RECORDINGS_TAB;
  const headings = key === UNASSIGNED ? UNASSIGNED_HEADINGS : RECORDINGS_HEADINGS;
  let sheet = book.getSheetByName(name);
  if (!sheet) {
    sheet = book.insertSheet(name);
    layOutRecordingsTab(sheet, headings);
  }
  const last = sheet.getLastRow();
  const ids = new Set(last > 1
    ? sheet.getRange(2, headings.length, last - 1, 1).getValues().map(r => String(r[0])) : []);
  ctx.sheets[key] = { book, sheet, headings, ids };
  return ctx.sheets[key];
}

/** The recurring meetings' titles on the group and community calendars
 * (see recurringTitleIndex), read once a run, when first needed. */
function recurringTitlesFor(ctx) {
  if (ctx.titles) return ctx.titles;
  const entries = [];
  for (const g of ctx.groups) {
    const dest = g.audience ? COMMUNITY : g.email;
    let pageToken;
    try {
      do {
        const resp = Calendar.Events.list(g.calendarId, {
          singleEvents: false, maxResults: 2500, pageToken,
          fields: 'nextPageToken,items(summary,recurrence,status)',
        });
        for (const e of resp.items || []) {
          if (e.recurrence && e.status !== 'cancelled' && e.summary) {
            entries.push({ title: e.summary, dest });
          }
        }
        pageToken = resp.nextPageToken;
      } while (pageToken);
    } catch (err) {
      console.error(`Couldn't read ${g.email}'s recurring meetings: ${err}`);
    }
  }
  ctx.titles = recurringTitleIndex(entries);
  return ctx.titles;
}

/** Run by hand: forget how far the backfill (and its dry run) got, so the
 * next run starts again from BACKFILL_FROM. Recordings already listed
 * aren't listed again. */
function resetRecordingsBackfill() {
  setJson(PROP_BACKFILL, null);
  setJson(PROP_BACKFILL_DRY_RUN, null);
  console.log(`The recordings backfill will start again from ${BACKFILL_FROM}.`);
}

/**
 * Add the agenda doc lines waiting from earlier runs and this one (see
 * addRecordingToDoc); a meeting whose tab isn't there yet waits until its
 * entry's time is up.
 */
function retryRecordingDocs(ctx) {
  const now = Date.now();
  const waiting = [];
  for (const entry of getJson(PROP_RECORDING_DOCS, []).concat(ctx.docs)) {
    let result;
    try {
      result = addRecordingToDoc(entry);
    } catch (err) {
      console.error(`Couldn't add a recording to agenda doc ${entry.docId}: ${err}`);
      result = 'no tab';
    }
    if (result === 'done') console.log(`Agenda doc ${entry.docId}: added the ${entry.date} recording.`);
    if (result === 'ambiguous') {
      console.log(`Agenda doc ${entry.docId}: more than one meeting on ${entry.date}, so its ` +
                  'recording is only on the calendar and in the sheet.');
    }
    if (result === 'no tab' && entry.until > now) waiting.push(entry);
  }
  setJson(PROP_RECORDING_DOCS, waiting);
}

/**
 * The backfill: steps through each month from BACKFILL_FROM, and each Zoom
 * user, listing the user's recordings (Zoom gives at most a month at a
 * time) in the sheets only (no events or docs), saving its place after
 * each; stops after BACKFILL_RUN_MS, or at a list Zoom won't give, to be
 * run again. At the end of a run,
 * ALERT_EMAIL gets one email listing the recordings it put on the
 * Unassigned tab. With dryRun, only logs where each would go (with its own
 * place).
 */
function runBackfill(dryRun) {
  withLock(() => {
    const started = Date.now();
    const key = dryRun ? PROP_BACKFILL_DRY_RUN : PROP_BACKFILL;
    const progress = getJson(key, null) || { month: 0, user: 0, listed: 0, unassigned: 0 };
    if (progress.done) {
      console.log(`The ${dryRun ? 'dry run' : 'backfill'} is done; run resetRecordingsBackfill ` +
                  'to start it again.');
      return;
    }
    const ctx = newRecordingsContext(loadGroups());
    const months = monthWindows(BACKFILL_FROM, started);
    const users = zoomList('/users', 'users')
      .sort((a, b) => lower(a.email).localeCompare(lower(b.email)));
    let stopped = false;
    while (progress.month < months.length && !stopped) {
      const w = months[progress.month];
      while (progress.user < users.length) {
        if (Date.now() - started > BACKFILL_RUN_MS) {
          stopped = true;
          break;
        }
        const user = users[progress.user];
        let list;
        try {
          list = zoomList(`/users/${encodeURIComponent(user.id)}/recordings?from=${w.from}` +
                          `&to=${w.to}`, 'meetings');
        } catch (err) {
          // Stop here rather than skip them; the next run tries again.
          console.error(`Couldn't list ${user.email}'s recordings for ${w.month}: ${err}`);
          stopped = true;
          break;
        }
        for (const m of list) {
          if (!recordingReady(m)) continue;
          try {
            processRecording(m, user.email, ctx, { backfill: true, dryRun });
          } catch (err) {
            console.error(`Couldn't handle the recording of "${m.topic}" (${m.start_time}): ${err}`);
          }
        }
        if (!dryRun) {
          const before = Object.values(ctx.rows).reduce((n, rows) => n + rows.length, 0);
          flushRecordingRows(ctx);
          progress.listed += before;
        }
        progress.user++;
        setJson(key, progress);
      }
      if (!stopped) {
        progress.user = 0;
        progress.month++;
        setJson(key, progress);
      }
    }
    progress.unassigned += ctx.uncategorized.length;
    if (!stopped) progress.done = true;
    setJson(key, progress);

    if (dryRun) {
      const counts = Object.keys(ctx.counts).sort()
        .map(name => `  ${name}: ${ctx.counts[name]}`).join('\n');
      console.log(`This run's recordings, by sheet:\n${counts || '  none'}`);
    } else if (ctx.uncategorized.length) {
      const message = uncategorizedMessage(ctx.uncategorized, {
        alertEmail: ALERT_EMAIL, backfill: true, sheetUrl: ctx.sheets[UNASSIGNED].book.getUrl(),
      });
      MailApp.sendEmail({ to: message.to, subject: message.subject, body: message.body,
                          htmlBody: textToHtml(message.body) });
    }
    const where = progress.month < months.length ? months[progress.month].month : 'the end';
    console.log(stopped
      ? `Stopped at ${where}; run ${dryRun ? 'backfillRecordingsDryRun' : 'backfillRecordings'} ` +
        'again to carry on.'
      : `The ${dryRun ? 'dry run' : 'backfill'} is done.` +
        (dryRun ? '' : ` ${progress.listed} recording(s) listed, ${progress.unassigned} of ` +
                       `them on the ${UNASSIGNED_TAB} tab.`));
  });
}

/**
 * Every hour (from hourly): handle each finished recording of the last
 * RECORDINGS_LOOKBACK_DAYS days not handled yet (see processRecording),
 * emailing ALERT_EMAIL about each that can't be categorized; then add
 * waiting agenda doc lines, and move the Unassigned rows given a group.
 * A recording that fails is tried again next hour.
 */
function syncRecordings(groups) {
  const ctx = newRecordingsContext(groups);
  const now = Date.now();
  const seen = getJson(PROP_RECORDINGS_SEEN, {});
  for (const id of Object.keys(seen)) if (seen[id] < now) delete seen[id];
  const from = isoDay(now - RECORDINGS_LOOKBACK_DAYS * DAY_MS);
  const to = isoDay(now + DAY_MS);
  const alerts = [];
  for (const user of zoomList('/users', 'users')) {
    for (const m of userRecordings(user, from, to)) {
      if (seen[m.uuid] || !recordingReady(m)) continue;
      try {
        processRecording(m, user.email, ctx, {});
        flushRecordingRows(ctx);
        alerts.push(...ctx.uncategorized);
        seen[m.uuid] = now + RECORDINGS_SEEN_DAYS * DAY_MS;
      } catch (err) {
        console.error(`Couldn't handle the recording of "${m.topic}" (${m.start_time}): ${err}`);
        ctx.rows = {};
      }
      ctx.uncategorized = [];
    }
  }
  setJson(PROP_RECORDINGS_SEEN, seen);
  for (const item of alerts) {
    const message = uncategorizedMessage([item], {
      alertEmail: ALERT_EMAIL, sheetUrl: ctx.sheets[UNASSIGNED].book.getUrl(),
    });
    MailApp.sendEmail({ to: message.to, subject: message.subject, body: message.body,
                        htmlBody: textToHtml(message.body) });
  }
  retryRecordingDocs(ctx);
  applyUnassignedChoices(ctx);
}

/** A Zoom user's recordings from one day to another ('yyyy-MM-dd', at most
 * a month apart), for the hourly run; [] (logging why) if they can't be
 * read, as for a user with no cloud recording. */
function userRecordings(user, from, to) {
  try {
    return zoomList(`/users/${encodeURIComponent(user.id)}/recordings?from=${from}&to=${to}`,
                    'meetings');
  } catch (err) {
    console.error(`Couldn't list ${user.email}'s recordings from ${from} to ${to}: ${err}`);
    return [];
  }
}
