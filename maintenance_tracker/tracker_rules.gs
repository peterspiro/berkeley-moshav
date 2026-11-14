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

/** A record's attachments, from every attachment field: [{ id, url, filename }]. */
function attachmentsIn(record) {
  const found = [];
  Object.keys(record.fields).forEach(name => {
    const value = record.fields[name];
    if (Array.isArray(value)) {
      value.forEach(v => { if (v && v.url && v.filename) found.push({ id: v.id, url: v.url, filename: v.filename }); });
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
  const enc = s => encodeURIComponent(s).replace(/%20/g, '+');
  let link = `${formUrl}${formUrl.indexOf('?') < 0 ? '?' : '&'}prefill_Reporter=${enc(personName)}`;
  if (unitName) link += `&prefill_Location=${enc(unitName)}`;
  return link;
}

/**
 * The People rows whose Request form link is missing or out of date, as
 * [{ id, fields }] to write. Someone who's moved out gets none.
 */
function formLinkChanges(people, locationNames, formUrl) {
  const changes = [];
  people.forEach(person => {
    const f = person.fields;
    const unit = (f['Location'] || []).map(id => locationNames[id]).filter(Boolean)[0];
    const wanted = f['Name'] && !f['Moved out'] ? formLink(formUrl, f['Name'], unit) : null;
    if ((f['Request form link'] || null) !== wanted) {
      changes.push({ id: person.id, fields: { 'Request form link': wanted } });
    }
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
 * the Status, and for a scheduled request its visit's time, so that moving
 * the visit counts as a change.
 */
function statusKey(request) {
  const f = request.fields;
  const status = f['Status'] || 'New';
  return status === 'Scheduled' && f['Visit'] ? `${status}|${f['Visit']}` : status;
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
