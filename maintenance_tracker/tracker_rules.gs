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
// The circle is warned as the base passes each of these shares of a limit.
const USAGE_LEVELS = [70, 85, 95];
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
    ? "Upgrade the workspace to the Team plan before then. Afterwards, set the script's AIRTABLE_PLAN property to team and run installTriggers again (see maintenance_tracker/README.md)."
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
