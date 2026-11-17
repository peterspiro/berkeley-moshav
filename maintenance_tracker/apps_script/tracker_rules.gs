/**
 * The maintenance tracker's rules: what to do about a request or an update,
 * and what each email says. Pure functions, with no Apps Script or Airtable
 * calls, so they can be tested under Node (tests/test_maintenance_tracker.py).
 *
 * A record is Airtable's { id, fields }. `ctx` is what the emails need from
 * outside the record:
 *   { peopleById, locationNames (record ID -> name), timeZone, formUrl,
 *     statusPageUrl, triageUrl, emergencyNote }
 * of which the last four may be empty.
 */

const URGENT = ['Emergency', 'Urgent (days)'];
// The statuses from which a visit's time moves a request to Scheduled.
const SCHEDULABLE = ['New', 'Triaged', 'Waiting on builder', 'Waiting on vendor', 'Waiting on volunteer'];
// What each Airtable plan allows, and how often the script runs to stay
// inside it. Free allows 1,000 API calls a month for the whole workspace,
// so there the script looks for requests every 2 hours, for updates to
// pass on every 6, and does its housekeeping weekly: about 700 calls a
// month. Limits are per base.
const PLANS = {
  free: { checkEveryHours: 2, updatesEveryHours: 6, housekeepingWeekly: true,
          recordLimit: 1000, attachmentLimitBytes: 1024 * 1024 * 1024 },
  team: { checkEveryMinutes: 10, updatesEveryHours: 0, housekeepingWeekly: false,
          recordLimit: 50000, attachmentLimitBytes: 20 * 1024 * 1024 * 1024 },
};
// The weekly digest (see digest).
const OPEN_STATUSES = ['New', 'Triaged', 'Waiting on builder', 'Waiting on vendor', 'Waiting on volunteer',
                       'Scheduled', 'Done, to confirm'];
const DAY_MS = 24 * 60 * 60 * 1000;
const QUIET_DAYS = 14;          // an open request with nothing happening for this long has gone quiet
const CONFIRM_DAYS = 7;         // "Done, to confirm" for this long can probably be closed
const VISIT_PASSED_DAYS = 2;    // a visit this long ago should have been marked done
const REPEAT_WINDOW_DAYS = 180; // repeats are looked for among requests this recent
const REPEAT_PLACES = 3;        // the same thing wrong in this many places is a repeat
const STACK_PLACES = 2;         // or in this many places in one stack, for STACK_SYSTEMS
const STACK_SYSTEMS = ['Plumbing', 'Roof & envelope'];
const DEADLINE_WARN_DAYS = 90;  // a warranty deadline this near, with no claim made, is listed
const UNACKNOWLEDGED_DAYS = 14; // a claim the builder hasn't acknowledged in this long is listed
// A claim in one of these statuses has been made (not just drafted, or given up).
const CLAIM_MADE = ['Submitted', 'Acknowledged', 'Scheduled', 'Work done', 'Accepted', 'Disputed'];
// And in one of these, the builder still owes something.
const CLAIM_PENDING = ['Submitted', 'Acknowledged', 'Scheduled'];
// The circle is warned as the base passes each of these shares of a limit.
const USAGE_LEVELS = [70, 85, 95];
// The warranty report goes out by itself while a request with no claim made
// has a deadline within WARRANTY_REPORT_DAYS, every WARRANTY_REPORT_EVERY_DAYS.
const WARRANTY_REPORT_DAYS = 120;
const WARRANTY_REPORT_EVERY_DAYS = 28;
const MAINTENANCE_AHEAD_DAYS = 30; // maintenance work due within this long is listed as coming up
// A Maintenance work row in one of these statuses is finished with, and its
// plan's next occurrence can be made (see maintenanceWorkDue).
const WORK_FINISHED = ['Done', 'Skipped'];
// What the status page says for each Status. A status that isn't here
// (Duplicate, Declined) isn't on the page.
const MEMBER_STATUS = {
  'New': 'Received',
  'Triaged': 'Being looked into',
  'Waiting on builder': 'Waiting on the builder',
  'Waiting on vendor': 'Waiting on a contractor',
  'Waiting on volunteer': 'Waiting on a volunteer',
  'Scheduled': 'Visit scheduled',
  'Done, to confirm': 'Done, waiting for the reporter to confirm',
  'Closed': 'Closed',
};

/** The email telling a reporter their new request arrived; null if they gave no address. */
function acknowledgmentEmail(request, ctx) {
  const who = reporterOf(request, ctx.peopleById);
  if (!who.email) return null;
  const f = request.fields;
  const lines = [
    `Hi ${firstName(who.name)},`,
    '',
    `Thanks. The Maintenance & Warranty Circle has your request. Its number is ${requestNumber(request)}.`,
    '',
    ...requestSummary(request, ctx).map(line => '    ' + line),
    '',
    "We'll email you as it moves along. To add anything, or to ask about it, reply to this email.",
  ];
  if (ctx.statusPageUrl) lines.push('', `Where every request stands: ${ctx.statusPageUrl}`);
  const link = who.person && who.person.fields['Request form link'];
  if (link) lines.push('', `Your own link to the form, with your name and unit filled in: ${link}`);
  if (ctx.emergencyNote) lines.push('', ctx.emergencyNote);
  return { to: who.email, subject: `Request ${requestNumber(request)} received`, body: lines.join('\n') };
}

/**
 * A date ("2026-01-31") that many months on, as a date: the same day of
 * the month, or the month's last day if it has no such day ("2026-02-28").
 */
function addMonths(isoDate, months) {
  const month = +isoDate.slice(0, 4) * 12 + (+isoDate.slice(5, 7) - 1) + months;
  const lastDay = new Date(Date.UTC(Math.floor(month / 12), month % 12 + 1, 0)).getUTCDate();
  const two = n => String(n).padStart(2, '0');
  return `${Math.floor(month / 12)}-${two(month % 12 + 1)}-${two(Math.min(+isoDate.slice(8, 10), lastDay))}`;
}

/** The size of every attachment in the base, in bytes, counting one linked from two records once. */
function attachmentBytes(data) {
  const sizes = {};
  Object.keys(data).forEach(table => data[table].forEach(record =>
    attachmentsIn(record).forEach(a => { sizes[a.id] = a.size; })));
  return Object.keys(sizes).reduce((sum, id) => sum + sizes[id], 0);
}

/** A record's attachments, from every attachment field: [{ id, url, filename, size }]. */
function attachmentsIn(record) {
  const found = [];
  Object.keys(record.fields).forEach(name => {
    const value = record.fields[name];
    if (Array.isArray(value)) {
      value.forEach(v => { if (v && v.url && v.filename) found.push({ id: v.id, url: v.url, filename: v.filename, size: v.size || 0 }); });
    }
  });
  return found;
}

/** A date without a time ("2026-11-15"), as text: "Nov 15, 2026". */
function dateText(isoDate) {
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' })
    .format(new Date(dayNumber(String(isoDate)) * DAY_MS));
}

/** A date without a time, as a count of days, for taking one from another. */
function dayNumber(isoDate) {
  return Date.UTC(+isoDate.slice(0, 4), +isoDate.slice(5, 7) - 1, +isoDate.slice(8, 10)) / DAY_MS;
}

/** How many days from today, in the time zone, a date without a time is: negative if it's past. */
function daysFromToday(isoDate, now, timeZone) {
  return dayNumber(String(isoDate)) - dayNumber(new Intl.DateTimeFormat('en-CA', { timeZone: timeZone }).format(now));
}

/**
 * The weekly digest for the circle: what came in, what needs someone, and
 * what's repeating across the building, as { subject, body }; null if
 * no request is open or new and no maintenance work is listed. `data` is every table's records,
 * { table: [records] }; `ctx` needs timeZone, triageUrl, and plan.
 *
 * Its sections, each left out when empty:
 *  - New this week: requests submitted in the last 7 days.
 *  - To triage: requests still New.
 *  - No owner: open, triaged requests with nobody assigned.
 *  - Scheduled without a time, and visits that have passed (by
 *    VISIT_PASSED_DAYS) with the request still Scheduled.
 *  - Waiting to be confirmed: "Done, to confirm" for CONFIRM_DAYS.
 *  - Gone quiet: other open requests (triaged, or waiting on someone)
 *    with no update or change of status for QUIET_DAYS.
 *  - Warranty deadlines: open requests whose Claim deadline is within
 *    DEADLINE_WARN_DAYS, or past, and that no claim has been made for.
 *  - Claims the builder hasn't acknowledged in UNACKNOWLEDGED_DAYS, and
 *    claims past the date the builder promised.
 *  - Repeats (see digestRepeats).
 *  - Maintenance work that's overdue, whose scheduled time has passed,
 *    and that's coming up (see digestMaintenance).
 * Then how many requests are open, by status, and how full the base is.
 */
function digest(data, now, ctx) {
  const requests = data['Requests'] || [];
  const names = {}, people = {};
  (data['Locations'] || []).forEach(l => { names[l.id] = l.fields['Name']; });
  (data['People'] || []).forEach(p => { people[p.id] = p.fields['Name']; });
  const lastTouched = {};
  (data['Updates'] || []).forEach(u => {
    const when = new Date(u.fields['Logged'] || u.fields['When'] || 0).getTime();
    (u.fields['Request'] || []).forEach(id => { lastTouched[id] = Math.max(lastTouched[id] || 0, when); });
  });
  const status = r => r.fields['Status'] || 'New';
  const days = ms => Math.floor((now.getTime() - ms) / DAY_MS);
  const submitted = r => new Date(r.fields['Submitted'] || 0).getTime();
  const idle = r => days(Math.max(submitted(r), lastTouched[r.id] || 0));
  const line = (r, note) => {
    const place = (r.fields['Location'] || []).map(id => names[id]).filter(Boolean).join(', ');
    const what = r.fields['Title'] || String(r.fields['Description'] || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    const owner = people[(r.fields['Owner'] || [])[0]];
    return `  ${requestNumber(r)}${place ? ' ' + place : ''}${what ? ': ' + what : ''}` +
           `${owner ? ' (' + owner + ')' : ''}${note ? ' - ' + note : ''}`;
  };
  const ago = n => (n === 0 ? 'today' : n === 1 ? '1 day' : `${n} days`);
  const byAge = (a, b) => submitted(a) - submitted(b);

  const open = requests.filter(r => OPEN_STATUSES.indexOf(status(r)) >= 0).sort(byAge);
  const fresh = requests.filter(r => days(submitted(r)) < 7).sort(byAge);
  const maintenance = digestMaintenance(data, now, ctx.timeZone);
  if (!open.length && !fresh.length && !maintenance.overdue.length && !maintenance.passed.length &&
      !maintenance.coming.length) return null;

  const visit = r => (r.fields['Visit'] ? new Date(r.fields['Visit']).getTime() : null);
  const triage = open.filter(r => status(r) === 'New');
  const unowned = open.filter(r => status(r) !== 'New' && !(r.fields['Owner'] || []).length);
  const noTime = open.filter(r => status(r) === 'Scheduled' && !visit(r));
  const passed = open.filter(r => status(r) === 'Scheduled' && visit(r) && days(visit(r)) >= VISIT_PASSED_DAYS);
  const confirm = open.filter(r => status(r) === 'Done, to confirm' && idle(r) >= CONFIRM_DAYS);
  // New, Scheduled, and "Done, to confirm" requests are each covered above.
  const quiet = open.filter(r => ['New', 'Scheduled', 'Done, to confirm'].indexOf(status(r)) < 0 &&
    idle(r) >= QUIET_DAYS);

  // Dates without times (deadlines, promises), as days from today in the time zone.
  const daysUntil = iso => daysFromToday(iso, now, ctx.timeZone);
  const claims = data['Claims'] || [];
  const claimStatus = {};
  claims.forEach(c => { claimStatus[c.id] = c.fields['Status'] || 'Drafting'; });
  const claimLine = (c, note) => `  C-${c.fields['Number']}${c.fields['Title'] ? ': ' + c.fields['Title'] : ''} - ${note}`;
  const deadlines = open.filter(r => r.fields['Claim deadline'] &&
      daysUntil(r.fields['Claim deadline']) <= DEADLINE_WARN_DAYS &&
      !(r.fields['Claims'] || []).some(id => CLAIM_MADE.indexOf(claimStatus[id]) >= 0))
    .sort((a, b) => daysUntil(a.fields['Claim deadline']) - daysUntil(b.fields['Claim deadline']));
  const deadlineNote = r => {
    const left = daysUntil(r.fields['Claim deadline']);
    return `claim by ${dateText(r.fields['Claim deadline'])} ` +
      (left < 0 ? `(PASSED ${ago(-left)} ago)` : left === 0 ? '(TODAY)' : `(${ago(left)} left)`);
  };
  const unacknowledged = claims.filter(c => c.fields['Status'] === 'Submitted' && c.fields['Submitted on'] &&
    -daysUntil(c.fields['Submitted on']) >= UNACKNOWLEDGED_DAYS);
  const overdue = claims.filter(c => CLAIM_PENDING.indexOf(c.fields['Status']) >= 0 && c.fields['Promised by'] &&
    daysUntil(c.fields['Promised by']) < 0);

  const sections = [
    ['New this week', fresh.map(r => line(r, status(r) === 'New' ? '' : status(r)))],
    ['To triage', triage.map(r => line(r, days(submitted(r)) ? `waiting ${ago(days(submitted(r)))}` : 'arrived today'))],
    ['No owner', unowned.map(r => line(r, status(r)))],
    ['Scheduled, but with no visit time', noTime.map(r => line(r))],
    ['Visit has passed: mark it done, or give it a new time',
     passed.map(r => line(r, `visit was ${formatVisit(r.fields['Visit'], ctx.timeZone)}`))],
    ['Done, and waiting to be confirmed: close them?', confirm.map(r => line(r, `for ${ago(idle(r))}`))],
    [`Gone quiet: nothing for ${QUIET_DAYS} days or more`, quiet.map(r => line(r, `${status(r)}, ${ago(idle(r))}`))],
    [`Warranty deadline within ${DEADLINE_WARN_DAYS} days, and no claim made`, deadlines.map(r => line(r, deadlineNote(r)))],
    [`Claims the builder hasn't acknowledged in ${UNACKNOWLEDGED_DAYS} days`,
     unacknowledged.map(c => claimLine(c, `sent ${dateText(c.fields['Submitted on'])}`))],
    ['Claims past the date the builder promised',
     overdue.map(c => claimLine(c, `promised by ${dateText(c.fields['Promised by'])}`))],
    ['Repeats: the same thing in several places', digestRepeats(data, now)],
    ['Maintenance overdue', maintenance.overdue],
    ['Maintenance scheduled, and the time has passed: mark it done, or give it a new time', maintenance.passed],
    [`Maintenance coming up in the next ${MAINTENANCE_AHEAD_DAYS} days`, maintenance.coming],
  ].filter(section => section[1].length);

  const lines = [];
  sections.forEach(section => { lines.push(`${section[0]} (${section[1].length})`); section[1].forEach(l => lines.push(l)); lines.push(''); });
  if (!sections.length) lines.push('Nothing needs attention this week.', '');
  const counts = OPEN_STATUSES.map(st => [st, open.filter(r => status(r) === st).length]).filter(c => c[1]);
  lines.push(`Open requests: ${open.length}` + (counts.length ? ` (${counts.map(c => `${c[1]} ${c[0]}`).join(', ')})` : ''));
  const tableCounts = {};
  Object.keys(data).forEach(t => { tableCounts[t] = data[t].length; });
  const usage = usageReport(tableCounts, attachmentBytes(data), ctx.plan);
  lines.push(`The base holds ${usage.records.toLocaleString('en-US')} of the ${usage.recordLimit.toLocaleString('en-US')} records its plan allows` +
             `, and ${Math.round(usage.bytes / 1048576).toLocaleString('en-US')} MB of ${Math.round(usage.byteLimit / 1048576).toLocaleString('en-US')} MB of attachments.`);
  if (ctx.triageUrl) lines.push('', `Triage: ${ctx.triageUrl}`);
  const head = [fresh.length ? `${fresh.length} new` : '', triage.length ? `${triage.length} to triage` : '',
                `${open.length} open`,
                maintenance.overdue.length ? `${maintenance.overdue.length} maintenance overdue` : '']
    .filter(Boolean).join(', ');
  return { subject: `Maintenance digest: ${head}`, body: lines.join('\n') };
}

/** Whether the weekly digest is due: it's Sunday in the time zone, and it hasn't gone out today. */
function digestDue(now, lastSentIso, timeZone) {
  const local = d => new Intl.DateTimeFormat('en-CA', { timeZone: timeZone }).format(d);
  const weekday = new Intl.DateTimeFormat('en-US', { timeZone: timeZone, weekday: 'long' }).format(now);
  return weekday === 'Sunday' && !(lastSentIso && local(new Date(lastSentIso)) === local(now));
}

/**
 * The digest's lines about maintenance work that isn't Done or Skipped,
 * as { overdue, passed, coming }, each soonest first:
 *  - overdue: its Due date has passed, and it has no time scheduled;
 *  - passed: it's Scheduled, for a time VISIT_PASSED_DAYS or more ago;
 *  - coming: it's due, or scheduled for a time, within
 *    MAINTENANCE_AHEAD_DAYS. One scheduled later than it was due is here,
 *    not in overdue: it's in hand.
 * Each line names the plan's Task, who does it (the plan's Vendor, if it
 * has one, and its Owner), and the date. Work with neither a Due date
 * nor a time isn't listed.
 */
function digestMaintenance(data, now, timeZone) {
  const plans = {}, people = {}, vendors = {};
  (data['Maintenance plans'] || []).forEach(p => { plans[p.id] = p.fields; });
  (data['People'] || []).forEach(p => { people[p.id] = p.fields['Name']; });
  (data['Organizations'] || []).forEach(o => { vendors[o.id] = o.fields['Company']; });
  const lists = { overdue: [], passed: [], coming: [] };
  const add = (list, when, w, note) => {
    const plan = plans[(w.fields['Plan'] || [])[0]] || {};
    const who = [vendors[(plan['Vendor'] || [])[0]], people[(plan['Owner'] || [])[0]]].filter(Boolean).join(', ');
    lists[list].push([when, `  ${plan['Task'] || `Maintenance work ${w.fields['Number'] || ''}`.trim()}` +
                            `${who ? ' (' + who + ')' : ''} - ${note}`]);
  };
  const days = n => (n === 1 ? '1 day' : `${n} days`);
  (data['Maintenance work'] || []).forEach(w => {
    const f = w.fields;
    if (WORK_FINISHED.indexOf(f['Status']) >= 0) return;
    const time = f['Status'] === 'Scheduled' && f['Scheduled for'] ? new Date(f['Scheduled for']).getTime() : null;
    if (time !== null) {
      const ahead = (time - now.getTime()) / DAY_MS;
      const note = `scheduled for ${formatVisit(f['Scheduled for'], timeZone)}`;
      if (ahead <= -VISIT_PASSED_DAYS) add('passed', time, w, note);
      else if (ahead <= MAINTENANCE_AHEAD_DAYS) add('coming', time, w, note);
    } else if (f['Due']) {
      const left = daysFromToday(f['Due'], now, timeZone);
      const when = dayNumber(String(f['Due'])) * DAY_MS;
      if (left < 0) add('overdue', when, w, `was due ${dateText(f['Due'])} (${days(-left)} ago)`);
      else if (left <= MAINTENANCE_AHEAD_DAYS) add('coming', when, w, `due ${dateText(f['Due'])}`);
    }
  });
  Object.keys(lists).forEach(k => { lists[k] = lists[k].sort((a, b) => a[0] - b[0]).map(entry => entry[1]); });
  return lists;
}

/**
 * The digest's lines about repeats: among repairs and defects reported in
 * the last REPEAT_WINDOW_DAYS (not duplicates or declined ones), the same
 * component, or else the same system and room, in REPEAT_PLACES or more
 * places; and, for STACK_SYSTEMS, the same system in STACK_PLACES or more
 * places in one stack of units. Each is a sign of a defect to claim once,
 * for the building, not place by place; requests already under such a
 * claim (one whose Scope is Systemic, unless it was denied or withdrawn)
 * are left out.
 */
function digestRepeats(data, now) {
  const locations = {}, assets = {};
  (data['Locations'] || []).forEach(l => { locations[l.id] = l.fields; });
  (data['Assets'] || []).forEach(a => { assets[a.id] = a.fields['Name']; });
  const systemic = {};
  (data['Claims'] || []).forEach(c => {
    if (c.fields['Scope'] === 'Systemic' && ['Denied', 'Withdrawn'].indexOf(c.fields['Status']) < 0) systemic[c.id] = true;
  });
  const recent = (data['Requests'] || []).filter(r => {
    const f = r.fields;
    return (f['Type'] || 'Repair or defect') === 'Repair or defect' &&
      !(f['Claims'] || []).some(id => systemic[id]) &&
      ['Duplicate', 'Declined'].indexOf(f['Status']) < 0 && (f['Location'] || []).length &&
      now.getTime() - new Date(f['Submitted'] || 0).getTime() < REPEAT_WINDOW_DAYS * DAY_MS;
  });
  const groups = {};  // label -> { location ID: [request numbers] }
  const add = (label, r) => {
    const place = r.fields['Location'][0];
    groups[label] = groups[label] || {};
    (groups[label][place] = groups[label][place] || []).push(requestNumber(r));
  };
  recent.forEach(r => {
    const f = r.fields;
    const component = assets[(f['Component'] || [])[0]];
    if (component) add(component, r);
    else if (f['System'] && f['Room']) add(`${f['System']}, ${f['Room']}`, r);
  });
  const stacks = {};
  recent.forEach(r => {
    const f = r.fields, place = locations[f['Location'][0]] || {};
    if (STACK_SYSTEMS.indexOf(f['System']) < 0 || !place['Stack']) return;
    const label = `${f['System']} in stack ${place['Stack']}`;
    stacks[label] = stacks[label] || {};
    (stacks[label][f['Location'][0]] = stacks[label][f['Location'][0]] || []).push(requestNumber(r));
  });
  const lines = [];
  const show = (found, least) => Object.keys(found).sort().forEach(label => {
    const places = Object.keys(found[label]);
    if (places.length < least) return;
    const where = places.map(id => `${(locations[id] || {})['Name'] || '?'} (${found[label][id].join(', ')})`).sort();
    lines.push(`  ${label}: ${places.length} places - ${where.join('; ')}`);
  });
  show(groups, REPEAT_PLACES);
  show(stacks, STACK_PLACES);
  return lines;
}

/** "Dana" from "Dana S" or "Dana Smith"; "there" if there's no name. */
function firstName(name) {
  return String(name || '').trim().split(/\s+/)[0] || 'there';
}

/** "Thu, Nov 20, 10:00 AM" for an ISO time, in the building's time zone. */
function formatVisit(iso, timeZone) {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: timeZone, weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  }).format(new Date(iso));
}

/**
 * A person's own link to the request form, with Reporter and Location
 * filled in: the form's link with prefill_ parameters naming the person's
 * and the unit's rows by their primary fields.
 */
function formLink(formUrl, personName, unitName) {
  let link = prefilled(formUrl, 'Reporter', personName);
  if (unitName) link = prefilled(link, 'Location', unitName);
  return link;
}

/**
 * The People rows whose own links to the forms are missing or out of date,
 * as [{ id, fields }] to write: Request form link, for everyone who hasn't
 * moved out, and Update form link (the "Log an update" form with By filled
 * in), for those of them who are circle members. A link whose form's
 * address isn't given (null) is left as it is.
 */
function formLinkChanges(people, locationNames, formUrl, updateFormUrl) {
  const changes = [];
  people.forEach(person => {
    const f = person.fields;
    const here = f['Name'] && !f['Moved out'];
    const unit = (f['Location'] || []).map(id => locationNames[id]).filter(Boolean)[0];
    const fields = {};
    const want = (field, link) => { if ((f[field] || null) !== link) fields[field] = link; };
    if (formUrl) want('Request form link', here ? formLink(formUrl, f['Name'], unit) : null);
    if (updateFormUrl) {
      want('Update form link', here && f['Circle member'] ? prefilled(updateFormUrl, 'By', f['Name']) : null);
    }
    if (Object.keys(fields).length) changes.push({ id: person.id, fields: fields });
  });
  return changes;
}

/** A Links row's key, from its Name (see linksByKey). */
function linkKey(name) {
  return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

/**
 * The Links table's values by key: each row's Name, lowercased, with each
 * run of other characters made a hyphen ("Request form" is "request-form"),
 * so that the script and the start-here page's [[placeholders]] name them
 * alike. Rows with no Value are left out.
 */
function linksByKey(records) {
  const links = {};
  records.forEach(r => {
    const key = linkKey(r.fields['Name']);
    const value = String(r.fields['Value'] || '').trim();
    if (key && value) links[key] = value;
  });
  return links;
}

/**
 * The Maintenance work rows to create, [{ fields }]: each maintenance
 * plan's next occurrence, when it has none waiting to be done.
 *  - A plan with no occurrence yet gets its first, due on its First due
 *    (none, while that's empty).
 *  - A plan whose occurrences are all Done or Skipped gets its next, due
 *    Every (months) after the latest of them was due (or was done, if it
 *    had no Due), whenever it was in fact done: the plan keeps to its
 *    dates. A plan with no Every (months) happens once, and gets no more.
 *  - A plan with an occurrence still Due or Scheduled (or with no Status)
 *    gets nothing: one made or moved by hand is left to stand.
 * Nothing is recorded on the plan: what's due is worked out from its
 * occurrences each time, so a run that fails is made up for by the next.
 * `data` is every table's records, { table: [records] }.
 */
function maintenanceWorkDue(data) {
  const workOf = {};
  (data['Maintenance work'] || []).forEach(w => (w.fields['Plan'] || []).forEach(id => {
    (workOf[id] = workOf[id] || []).push(w.fields);
  }));
  const due = [];
  (data['Maintenance plans'] || []).forEach(plan => {
    const work = workOf[plan.id] || [];
    let next = '';
    if (!work.length) {
      next = plan.fields['First due'] || '';
    } else if (work.every(w => WORK_FINISHED.indexOf(w['Status']) >= 0)) {
      const every = Math.round(Number(plan.fields['Every (months)']) || 0);
      const latest = work.map(w => w['Due'] || w['Done on'] || '').sort().pop();
      if (every >= 1 && latest) next = addMonths(latest, every);
    }
    if (next) due.push({ fields: { 'Plan': [plan.id], 'Due': next, 'Status': 'Due' } });
  });
  return due;
}

/** What the status page says about a request; null if it shouldn't be there. */
function memberStatus(request, timeZone) {
  const f = request.fields;
  const text = MEMBER_STATUS[f['Status'] || 'New'];
  if (!text) return null;
  return f['Status'] === 'Scheduled' && f['Visit']
    ? `${text} for ${formatVisit(f['Visit'], timeZone)}` : text;
}

/** Whether a request is new to the script. */
function needsAcknowledging(request) {
  return !request.fields['Acknowledged on'];
}

/**
 * What the script fills in on a new request, as fields: Reporter, for
 * someone who typed their details and whose email address is a member's
 * (either address; not a member who's moved out); and Location, the
 * reporter's unit, if "In my unit" is ticked and no location was chosen.
 */
function newRequestFields(request, people) {
  const f = request.fields;
  const fields = {};
  let person = people.filter(p => p.id === (f['Reporter'] || [])[0])[0] || null;
  const typed = String(f['Reporter email'] || '').trim().toLowerCase();
  if (!person && typed) {
    person = people.filter(p => !p.fields['Moved out'] &&
      [p.fields['Email'], p.fields['Other email']]
        .some(a => String(a || '').trim().toLowerCase() === typed))[0] || null;
    if (person) fields['Reporter'] = [person.id];
  }
  if (f['In my unit'] && !(f['Location'] || []).length && person && (person.fields['Location'] || []).length) {
    fields['Location'] = [person.fields['Location'][0]];
  }
  return fields;
}

/** A form's link with one more field filled in: "…?prefill_Reporter=Dana+S". */
function prefilled(formUrl, field, value) {
  const enc = v => encodeURIComponent(v).replace(/%20/g, '+');
  return `${formUrl}${formUrl.indexOf('?') < 0 ? '?' : '&'}prefill_${enc(field)}=${enc(value)}`;
}

/**
 * Who reported a request: { name, email, phone, person }. The member picked
 * in Reporter, by the details Gather has; or else what was typed on the
 * form. `person` is their People record, or null.
 */
function reporterOf(request, peopleById) {
  const f = request.fields;
  const person = peopleById[(f['Reporter'] || [])[0]] || null;
  if (person) {
    return {
      name: person.fields['Full name'] || person.fields['Name'] || '',
      email: person.fields['Email'] || f['Reporter email'] || '',
      phone: person.fields['Phone'] || f['Reporter phone'] || '',
      person: person,
    };
  }
  return { name: f['Reporter name'] || '', email: f['Reporter email'] || '',
           phone: f['Reporter phone'] || '', person: null };
}

/** "R-12". */
function requestNumber(request) {
  return `R-${request.fields['Number']}`;
}

/** A request as a few lines: where, what, and the reporter's own words. */
function requestSummary(request, ctx) {
  const f = request.fields;
  const place = (f['Location'] || []).map(id => ctx.locationNames[id]).filter(Boolean);
  if (f['Room']) place.push(f['Room']);
  const lines = [];
  if (place.length) lines.push(`Where: ${place.join(', ')}`);
  const what = [f['Type'], f['System'], f['Urgency']].filter(Boolean);
  if (what.length) lines.push(`What: ${what.join(', ')}`);
  if (f['Description']) lines.push(`"${String(f['Description']).trim()}"`);
  return lines;
}

/**
 * The Responsibility a request should be given from its warranty's (its
 * "Warranty responsibility", a lookup), or null: only when the warranty has
 * one, and the request's own is empty or Unknown, so that a choice someone
 * made is never overwritten.
 */
function responsibilityFromWarranty(request) {
  const f = request.fields;
  const own = f['Responsibility'];
  if (own && own !== 'Unknown') return null;
  const fromWarranty = [].concat(f['Warranty responsibility'] || []).filter(Boolean)[0];
  return fromWarranty && fromWarranty !== own ? fromWarranty : null;
}

/**
 * The email telling a reporter their request's status changed, or null if
 * this change sends none: a visit scheduled (or moved), the work done, or
 * the request closed without the work having been reported done first.
 */
function statusEmail(request, previousKey, ctx) {
  const who = reporterOf(request, ctx.peopleById);
  if (!who.email) return null;
  const f = request.fields;
  const number = requestNumber(request);
  const about = f['Title'] ? `${number} (${f['Title']})` : number;
  let subject, text;
  if (f['Status'] === 'Scheduled' && f['Visit']) {
    subject = `Request ${number}: visit scheduled`;
    text = [`A visit for your request ${about} is scheduled for ${formatVisit(f['Visit'], ctx.timeZone)}.`,
            '',
            f['May enter if I\'m out']
              ? "You said we may come in if you're out, so nobody needs to be home."
              : "Please make sure someone's home, or reply to tell us how to get in.",
            '',
            "If that time doesn't work, reply to this email."];
  } else if (f['Status'] === 'Done, to confirm') {
    subject = `Request ${number}: is it fixed?`;
    text = [`The work on your request ${about} has been done, as far as we know.`,
            '',
            "If it's fixed, there's nothing more to do. If it isn't, reply to this email and tell us what's still wrong."];
  } else if (f['Status'] === 'Closed' && String(previousKey || '').indexOf('Done, to confirm') !== 0) {
    subject = `Request ${number} closed`;
    text = [`Your request ${about} has been closed.`];
    if (f['Resolution']) text.push('', String(f['Resolution']).trim());
    text.push('', 'If the problem comes back, reply to this email or send a new request.');
  } else {
    return null;
  }
  const lines = [`Hi ${firstName(who.name)},`, ''].concat(text);
  if (ctx.statusPageUrl) lines.push('', `Where every request stands: ${ctx.statusPageUrl}`);
  return { to: who.email, subject: subject, body: lines.join('\n') };
}

/**
 * What the script last saw of a request's status, kept in "Status noted":
 * the Status, and the visit's time if it has one ("Scheduled|2026-11-20T…"),
 * so that setting or moving the visit counts as a change.
 */
function statusKey(request) {
  const f = request.fields;
  return (f['Status'] || 'New') + (f['Visit'] ? `|${f['Visit']}` : '');
}

/**
 * Whether a change from what was noted is one to record in Updates and
 * perhaps tell the reporter of: a change of Status, or a scheduled visit
 * being given a time or moved. A visit time set or changed on a request
 * that isn't scheduled (and that visitSchedules didn't schedule) is only
 * noted.
 */
function statusNoteworthy(request, previousKey) {
  const f = request.fields;
  const before = String(previousKey || 'New').split('|');
  const status = f['Status'] || 'New';
  return before[0] !== status ||
    (status === 'Scheduled' && !!f['Visit'] && (before[1] || '') !== f['Visit']);
}

/** The Updates row's text for a change of status. */
function statusUpdateText(previousKey, request, timeZone) {
  const f = request.fields;
  const before = String(previousKey || 'New').split('|')[0];
  const now = f['Status'] || 'New';
  const visit = now === 'Scheduled' && f['Visit'] ? `, for ${formatVisit(f['Visit'], timeZone)}` : '';
  return before === now ? `Visit moved to ${formatVisit(f['Visit'], timeZone)}.`
                        : `Status changed from ${before} to ${now}${visit}.`;
}

/**
 * Who wrote an update that names nobody, as a People record's ID, or null:
 * the person whose email address is that of the Airtable account that
 * created it (its "Created by"). That's known only for a row made by
 * someone signed in to Airtable, as on the Maintenance tracker interface;
 * one from a form's public link was made by nobody in particular, and
 * matches no one. An update that has By or By name is left alone.
 */
function updateAuthor(update, people) {
  const f = update.fields;
  if ((f['By'] || []).length || f['By name']) return null;
  const email = String((f['Created by'] || {}).email || '').trim().toLowerCase();
  if (!email) return null;
  const person = people.filter(p => !p.fields['Moved out'] &&
    [p.fields['Email'], p.fields['Other email']]
      .some(a => String(a || '').trim().toLowerCase() === email))[0];
  return person ? person.id : null;
}

/** The email passing an update on to the request's reporter; null if they gave no address. */
function updateEmail(update, request, ctx) {
  const who = reporterOf(request, ctx.peopleById);
  const text = String(update.fields['Text'] || '').trim();
  if (!who.email || !text) return null;
  const number = requestNumber(request);
  const about = request.fields['Title'] ? `${number} (${request.fields['Title']})` : number;
  const lines = [`Hi ${firstName(who.name)},`, '', `An update on your request ${about}:`, '', text,
                 '', 'To answer, reply to this email.'];
  if (ctx.statusPageUrl) lines.push('', `Where every request stands: ${ctx.statusPageUrl}`);
  return { subject: `Request ${number}: an update`, to: who.email, body: lines.join('\n') };
}

/**
 * Whether the updates to pass on are due a look: always, on a plan with no
 * limit on calls; otherwise every updatesEveryHours.
 */
function updatesDue(plan, lastCheckedIso, now) {
  const hours = PLANS[plan].updatesEveryHours;
  if (!hours || !lastCheckedIso) return true;
  return now.getTime() - new Date(lastCheckedIso).getTime() >= hours * 60 * 60 * 1000 - 60 * 1000;
}

/** The email telling the circle about a new emergency or urgent request; null for a routine one. */
function urgentAlert(request, ctx) {
  const f = request.fields;
  if (URGENT.indexOf(f['Urgency']) < 0) return null;
  const who = reporterOf(request, ctx.peopleById);
  const place = (f['Location'] || []).map(id => ctx.locationNames[id]).filter(Boolean).join(', ');
  const lines = requestSummary(request, ctx);
  lines.push('', `Reported by ${[who.name || 'someone who gave no name', who.phone, who.email].filter(Boolean).join(', ')}.`);
  if (f['In my unit'] && !place) lines.push('"In my unit" is ticked, but the unit isn\'t known: set the location at triage.');
  lines.push(f['May enter if I\'m out'] ? "May enter if they're out." : "Hasn't said we may enter if they're out.");
  if (f['Access notes']) lines.push(`Access: ${String(f['Access notes']).trim()}`);
  if ((f['Photos'] || []).length) lines.push(`${f['Photos'].length} photo(s) attached to the request.`);
  if (ctx.triageUrl) lines.push('', `Triage: ${ctx.triageUrl}`);
  const tag = f['Urgency'] === 'Emergency' ? 'Emergency' : 'Urgent';
  return {
    subject: `[${tag}] ${requestNumber(request)}${place ? ' ' + place : ''}${f['System'] ? ': ' + f['System'] : ''}`,
    body: lines.join('\n'),
  };
}

/**
 * How full the base is: { records, recordLimit, bytes, byteLimit, level },
 * where level is the highest of USAGE_LEVELS that the records or the
 * attachments have reached, as a share of the plan's limit; 0 if none.
 * `counts` is { table: its number of records }.
 */
function usageReport(counts, attachmentBytes, plan) {
  const limits = PLANS[plan];
  const records = Object.keys(counts).reduce((sum, t) => sum + counts[t], 0);
  const share = Math.max(records / limits.recordLimit, attachmentBytes / limits.attachmentLimitBytes) * 100;
  return {
    records: records, recordLimit: limits.recordLimit,
    bytes: attachmentBytes, byteLimit: limits.attachmentLimitBytes,
    level: USAGE_LEVELS.filter(l => share >= l).pop() || 0,
  };
}

/**
 * The email warning the circle that the base is filling up, or null if
 * it's at a level they've already been warned of (or none).
 */
function usageWarning(report, warnedLevel, plan) {
  if (!report.level || report.level <= (warnedLevel || 0)) return null;
  const mb = n => Math.round(n / (1024 * 1024)).toLocaleString('en-US');
  const pct = (n, limit) => Math.round(n / limit * 100);
  const lines = [
    `The maintenance tracker's Airtable base has passed ${report.level}% of what its plan allows:`,
    '',
    `    Records: ${report.records.toLocaleString('en-US')} of ${report.recordLimit.toLocaleString('en-US')} (${pct(report.records, report.recordLimit)}%)`,
    `    Photos and attachments: ${mb(report.bytes)} MB of ${mb(report.byteLimit)} MB (${pct(report.bytes, report.byteLimit)}%)`,
    '',
    'When either is full, Airtable accepts no more: the request form stops working for members.',
  ];
  lines.push('', plan === 'free'
    ? "Upgrade the workspace to the Team plan before then. Afterwards, set the script's AIRTABLE_PLAN property to team and run installTriggers again (see maintenance_tracker/apps_script/README.md)."
    : 'Before then, move to a larger plan, or remove what is no longer needed.');
  return { subject: `Maintenance tracker: the Airtable base is ${report.level}% full`, body: lines.join('\n') };
}

/**
 * Whether giving a request a visit time schedules it: the time is new or
 * changed since the script last looked, is still to come, and the request
 * is open and not yet scheduled. The script then sets Status to Scheduled
 * itself, so that filling in Visit is all anyone has to do. A visit time
 * left on a request that's been moved back from Scheduled doesn't
 * schedule it again.
 */
function visitSchedules(request, previousKey, now) {
  const f = request.fields;
  const noted = String(previousKey || '').split('|')[1] || '';
  return !!f['Visit'] && f['Visit'] !== noted && new Date(f['Visit']) > now &&
    SCHEDULABLE.indexOf(f['Status'] || 'New') >= 0;
}

/**
 * The warranty report for the circle, as { subject, body }: where every
 * request under each warranty stands, for going through before a warranty
 * ends. Sent when someone runs sendWarrantyReport, and by itself when
 * warrantyReportDue. `data` needs Requests,
 * Claims, Warranties, Organizations, and Locations; `ctx` needs timeZone.
 *
 * Requests that are Duplicate or Declined are left out. For each warranty,
 * soonest deadline first:
 *  - Open, and no claim made: each with its own Claim deadline (a unit's
 *    can differ from the building's), soonest first, and the claim being
 *    drafted for it, if one is. A claim that's Drafting, Denied, or
 *    Withdrawn isn't a claim made (see CLAIM_MADE).
 *  - Open, with a claim made: each with its claims and their statuses.
 *  - Closed with no claim made, as a list of numbers: fixed without the
 *    warrantor having been given notice.
 * Then open repairs and defects with no warranty chosen, unless their
 * Responsibility is the HOA's or the unit owner's: these have no deadline
 * shown anywhere, so one that's really a warranty item can be missed.
 */
function warrantyReport(data, now, ctx) {
  const places = {}, companies = {}, claims = {};
  (data['Locations'] || []).forEach(l => { places[l.id] = l.fields['Name']; });
  (data['Organizations'] || []).forEach(o => { companies[o.id] = o.fields['Company']; });
  (data['Claims'] || []).forEach(c => { claims[c.id] = c.fields; });
  const status = r => r.fields['Status'] || 'New';
  const isOpen = r => OPEN_STATUSES.indexOf(status(r)) >= 0;
  const claimsOf = r => (r.fields['Claims'] || []).map(id => claims[id]).filter(Boolean);
  const claimed = r => claimsOf(r).some(c => CLAIM_MADE.indexOf(c['Status']) >= 0);
  const claimText = c => `C-${c['Number']} ${c['Status'] || 'Drafting'}`;
  const deadline = r => (r.fields['Claim deadline'] ? daysFromToday(r.fields['Claim deadline'], now, ctx.timeZone) : Infinity);
  const days = n => (n === 1 ? '1 day' : `${n} days`);
  const deadlineText = r => {
    const left = deadline(r);
    if (left === Infinity) return 'NO DEADLINE: the warranty has no term or no start date';
    return `claim by ${dateText(r.fields['Claim deadline'])} ` +
      (left < 0 ? `(PASSED ${days(-left)} ago)` : left === 0 ? '(TODAY)' : `(${days(left)} left)`);
  };
  const line = (r, note) => {
    const place = (r.fields['Location'] || []).map(id => places[id]).filter(Boolean).join(', ');
    const what = r.fields['Title'] || String(r.fields['Description'] || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    return `  ${requestNumber(r)}${place ? ' ' + place : ''}${what ? ': ' + what : ''} - ${note}`;
  };
  const byDeadline = (a, b) => deadline(a) - deadline(b) || a.fields['Number'] - b.fields['Number'];

  const requests = (data['Requests'] || []).filter(r => ['Duplicate', 'Declined'].indexOf(status(r)) < 0);
  const under = {};
  requests.forEach(r => (r.fields['Warranty'] || []).forEach(id => { (under[id] = under[id] || []).push(r); }));
  let unclaimedCount = 0, passedCount = 0;
  const blocks = [], quiet = [];
  (data['Warranties'] || []).forEach(w => {
    const mine = under[w.id] || [];
    const unclaimed = mine.filter(r => isOpen(r) && !claimed(r)).sort(byDeadline);
    const inHand = mine.filter(r => isOpen(r) && claimed(r)).sort(byDeadline);
    const closed = mine.filter(r => !isOpen(r) && !claimed(r)).sort((a, b) => a.fields['Number'] - b.fields['Number']);
    const name = w.fields['Name'] || 'A warranty with no name';
    if (!unclaimed.length && !inHand.length && !closed.length) { quiet.push(name); return; }
    unclaimedCount += unclaimed.length;
    passedCount += unclaimed.filter(r => deadline(r) < 0).length;
    const warrantors = (w.fields['Warrantor'] || []).map(id => companies[id]).filter(Boolean).join(', ');
    const lines = [`${name}${warrantors ? ' (' + warrantors + ')' : ''}`];
    if (unclaimed.length) {
      lines.push(`Open, and no claim made (${unclaimed.length})`);
      unclaimed.forEach(r => {
        const drafts = claimsOf(r).filter(c => (c['Status'] || 'Drafting') === 'Drafting').map(c => `C-${c['Number']}`);
        lines.push(line(r, deadlineText(r) + (drafts.length ? `; ${drafts.join(', ')} is being drafted` : '')));
      });
    }
    if (inHand.length) {
      lines.push(`Open, with a claim made (${inHand.length})`);
      inHand.forEach(r => lines.push(line(r, claimsOf(r).map(claimText).join(', '))));
    }
    if (closed.length) lines.push(`Closed with no claim made (${closed.length}): ${closed.map(requestNumber).join(', ')}`);
    blocks.push([Math.min.apply(null, unclaimed.concat(inHand).map(deadline).concat([Infinity])), lines]);
  });
  const unsorted = requests.filter(r => isOpen(r) && !(r.fields['Warranty'] || []).length &&
      (r.fields['Type'] || 'Repair or defect') === 'Repair or defect' &&
      ['HOA', 'Unit owner'].indexOf(r.fields['Responsibility']) < 0)
    .sort((a, b) => a.fields['Number'] - b.fields['Number']);

  const body = [];
  blocks.sort((a, b) => a[0] - b[0]).forEach(block => { block[1].forEach(l => body.push(l)); body.push(''); });
  if (unsorted.length) {
    body.push(`Open repairs and defects with no warranty chosen (${unsorted.length})`);
    unsorted.forEach(r => body.push(line(r, `${status(r)}, Responsibility ${r.fields['Responsibility'] || 'not set'}`)));
    body.push('');
  }
  if (quiet.length) body.push(`Nothing to report under: ${quiet.join('; ')}.`, '');
  if (!blocks.length && !unsorted.length) body.unshift('No request is under a warranty, and no open repair is without one.', '');
  body.push('A claim that is being drafted, or was denied or withdrawn, does not count as made.');
  const head = [`${unclaimedCount} open with no claim made`, passedCount ? `${passedCount} past the deadline` : '',
                unsorted.length ? `${unsorted.length} with no warranty chosen` : ''].filter(Boolean).join(', ');
  return { subject: `Warranty report: ${head}`, body: body.join('\n') };
}

/**
 * Whether the warranty report should go out by itself: it's Sunday in the
 * time zone; it hasn't gone out, by itself or by hand, in the last
 * WARRANTY_REPORT_EVERY_DAYS; and some request under a warranty, open or
 * closed (but not Duplicate or Declined), has had no claim made and has a
 * Claim deadline that's today or within WARRANTY_REPORT_DAYS. So it goes
 * out every four weeks through a warranty's last months, and stops once
 * everything under it is claimed or its deadlines have passed (the weekly
 * digest goes on listing what's open and past its deadline). A warranty
 * has no one end: each request's deadline is its own. `data` needs
 * Requests and Claims.
 */
function warrantyReportDue(data, now, lastSentIso, timeZone) {
  const local = d => new Intl.DateTimeFormat('en-CA', { timeZone: timeZone }).format(d);
  if (new Intl.DateTimeFormat('en-US', { timeZone: timeZone, weekday: 'long' }).format(now) !== 'Sunday') return false;
  if (lastSentIso && -daysFromToday(local(new Date(lastSentIso)), now, timeZone) < WARRANTY_REPORT_EVERY_DAYS) return false;
  const made = {};
  (data['Claims'] || []).forEach(c => { made[c.id] = CLAIM_MADE.indexOf(c.fields['Status']) >= 0; });
  return (data['Requests'] || []).some(r => {
    const f = r.fields;
    if (['Duplicate', 'Declined'].indexOf(f['Status']) >= 0 || !f['Claim deadline']) return false;
    if ((f['Claims'] || []).some(id => made[id])) return false;
    const left = daysFromToday(f['Claim deadline'], now, timeZone);
    return left >= 0 && left <= WARRANTY_REPORT_DAYS;
  });
}
