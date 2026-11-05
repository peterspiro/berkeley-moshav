/**
 * Structure Rules
 *
 * The rules of the Circle Structure (see structure.gs), with no Apps
 * Script calls, so they can be tested under Node
 * (tests/test_circle_structure_rules.py): which groups are in it and as
 * what, which parents each may have, what to do with a parent that breaks
 * the rules, the tree the parents make, and what a circle's agenda doc
 * says about its place in it.
 *
 * A group is {id, name, type, parent, ...}: id its Gather group's ID (the
 * row's key, which a rename doesn't change), name its Gather name, type one
 * of GROUP_TYPES, and parent what its Parent cell says (a group's name,
 * PARENT_TOP, or ''). Parents, once resolved, are by ID: a parent's ID,
 * TOP, or '' (not yet placed).
 */

const TYPE_CIRCLE = 'Circle';
const TYPE_WORKING_GROUP = 'Working Group';
const TYPE_OUT = 'Not in the structure';
const GROUP_TYPES = [TYPE_CIRCLE, TYPE_WORKING_GROUP, TYPE_OUT];
// The group at the top of the structure, the everybody group (as
// FULL_COMMUNITY_GROUP_NAME in update_groups.py).
const ROOT_GROUP_NAME = 'Full Community';
// What the top group's Parent cell says, and its resolved parent.
const PARENT_TOP = 'Top of the structure';
const TOP = 'top';

function sameName(a, b) {
  return String(a || '').trim().toLowerCase() === String(b || '').trim().toLowerCase();
}

function inStructure(group) {
  return group.type === TYPE_CIRCLE || group.type === TYPE_WORKING_GROUP;
}

function isRootGroup(group) {
  return group.type === TYPE_CIRCLE && sameName(group.name, ROOT_GROUP_NAME);
}

// A group's type, from its name and its Drive item (as its footer links
// it): {type, reason}, reason saying why a group is TYPE_OUT. item:
// {name, isSharedDrive, inClubs (it's in the Shared Drive's Clubs
// folder)}, or null if it couldn't be read. Circles' and working groups'
// folders are named with their type ("Technology Circle", "Garden Working
// Group"; see folder_name_for_group_type in util/gdrive_config.py), as
// their Gather names often aren't: a name ending in one of
// WORKING_GROUP_WORDS is a working group's, one ending in "Circle" a
// circle's, and one ending in "Club" a club's, each word perhaps followed
// by something in brackets, often the group's acronym ("Development,
// Finance, & Legal Circle (DF&L)").
const WORKING_GROUP_WORDS = ['Working Group', 'Work Group', 'Team', 'Pod'];
function guessGroupType(name, item) {
  if (sameName(name, ROOT_GROUP_NAME)) return {type: TYPE_CIRCLE, reason: ''};
  if (!item) {
    return {type: TYPE_OUT, reason: 'its Drive folder couldn\'t be read, to tell its type'};
  }
  const endsWith = (text, words) => new RegExp('\\b(?:' +
    words.map(w => w.replace(/ /g, '\\s+')).join('|') + ')\\s*(?:\\([^()]*\\)\\s*)?$', 'i')
    .test(text || '');
  if (item.inClubs || endsWith(item.name, ['Club']) || endsWith(name, ['Club'])) {
    return {type: TYPE_OUT, reason: 'it\'s a club'};
  }
  for (const text of [item.name, name]) {
    if (endsWith(text, WORKING_GROUP_WORDS)) return {type: TYPE_WORKING_GROUP, reason: ''};
    if (endsWith(text, ['Circle'])) return {type: TYPE_CIRCLE, reason: ''};
  }
  // An everybody group (linked to a shared drive) is in the structure only
  // if it's named as a circle (or working group).
  const what = item.isSharedDrive ? 'its shared drive\'s name' : 'its Drive folder\'s name';
  return {type: TYPE_OUT, reason: what + ', "' + item.name + '", doesn\'t end ' +
    'with "Circle", "' + WORKING_GROUP_WORDS.join('", "') + '" (perhaps followed by something ' +
    'in brackets), to tell its type (rename the ' + (item.isSharedDrive ? 'shared drive' : 'folder') + ' to fix this)'};
}

// Whether a group of type childType may be under one of parentType: a
// circle only under a circle, a working group under either.
function canBeUnder(childType, parentType) {
  return parentType === TYPE_CIRCLE ||
    (childType === TYPE_WORKING_GROUP && parentType === TYPE_WORKING_GROUP);
}

// The parent a group's Parent cell asks for, by ID: {parent} (an ID, TOP,
// or ''), or {error}, why it can't have it.
function wantedParent(group, groups) {
  const text = String(group.parent || '').trim();
  if (isRootGroup(group)) return {parent: TOP};
  if (!text) return {parent: ''};
  if (sameName(text, PARENT_TOP)) {
    return {error: 'only ' + ROOT_GROUP_NAME + ' is at the top of the structure'};
  }
  const matches = groups.filter(g => inStructure(g) && sameName(g.name, text));
  if (matches.length !== 1) {
    return {error: matches.length ? 'more than one group is named "' + text + '"'
      : 'no circle or working group is named "' + text + '"'};
  }
  const parent = matches[0];
  if (parent.id === group.id) return {error: 'a group can\'t be its own parent'};
  if (!canBeUnder(group.type, parent.type)) {
    return {error: parent.name + ' is a working group, and a circle can only be under a circle'};
  }
  return {parent: parent.id};
}

// Each group's parent, by ID, from what the Parent cells ask for, keeping
// to the rules: a parent the rules don't allow, or one that would make a
// loop (a group under itself, or under a group beneath it), is refused,
// and the group keeps its parent from before (lastApplied, {id: parent},
// as this returned last time), if that's still allowed, or else is left
// not yet placed. Returns {parentOf: {id: parent}, refused: [{id, tried,
// reason, kept}]}, tried what the cell said, kept the parent it keeps.
// Only groups in the structure are given parents.
function resolveParents(groups, lastApplied) {
  lastApplied = lastApplied || {};
  const members = groups.filter(inStructure);
  const byId = {};
  for (const g of members) byId[g.id] = g;
  const parentOf = {};
  const refused = [];
  // Whether a parent from before is still allowed for a group.
  const stillAllowed = (g, parent) => parent === '' ||
    (parent === TOP ? isRootGroup(g) : !!byId[parent] && parent !== g.id &&
      !isRootGroup(g) && canBeUnder(g.type, byId[parent].type));
  const refuse = (g, reason) => {
    const before = Object.prototype.hasOwnProperty.call(lastApplied, g.id) ? lastApplied[g.id] : '';
    const kept = stillAllowed(g, before) ? before : '';
    refused.push({id: g.id, tried: String(g.parent || '').trim(), reason, kept});
    parentOf[g.id] = kept;
  };
  for (const g of members) {
    const wanted = wantedParent(g, members);
    if (wanted.error) refuse(g, wanted.error);
    else parentOf[g.id] = wanted.parent;
  }
  // Loops: refuse the parents that changed since last time, which made
  // them; a loop that was there before (which shouldn't happen) is broken
  // by leaving its groups not yet placed.
  for (let pass = 0; pass < members.length + 1; pass++) {
    const loop = findLoop(members, parentOf);
    if (!loop) break;
    const changed = loop.filter(id => parentOf[id] !== (lastApplied[id] || ''));
    for (const id of changed.length ? changed : loop) {
      const g = byId[id];
      const reason = 'that would put ' + g.name + ' beneath itself';
      if (changed.length) {
        refuse(g, reason);
      } else {
        refused.push({id, tried: String(g.parent || '').trim(), reason, kept: ''});
        parentOf[id] = '';
      }
    }
  }
  return {parentOf, refused};
}

// The IDs of the groups in a loop of parents, or null if there's none.
function findLoop(groups, parentOf) {
  const done = {};
  for (const g of groups) {
    const seen = [];
    let id = g.id;
    while (id && id !== TOP && !done[id]) {
      if (seen.includes(id)) return seen.slice(seen.indexOf(id));
      seen.push(id);
      id = parentOf[id];
    }
    for (const s of seen) done[s] = true;
  }
  return null;
}

// The tree the parents make: {rootId (or null), children: {id: [ids, by
// name]}, placed: {id: true} (the root and the groups beneath it),
// unplaced: [ids, by name] (each group not yet placed whose parent isn't
// one of those either: the tops of the subtrees that aren't placed)}.
function buildTree(groups, parentOf) {
  const members = groups.filter(inStructure);
  const byId = {};
  for (const g of members) byId[g.id] = g;
  const byName = (a, b) => byId[a].name.localeCompare(byId[b].name);
  const root = members.find(g => parentOf[g.id] === TOP);
  const children = {};
  for (const g of members) children[g.id] = [];
  const unplaced = [];
  for (const g of members) {
    const p = parentOf[g.id];
    if (p && p !== TOP && children[p]) children[p].push(g.id);
    else if (p !== TOP) unplaced.push(g.id);
  }
  for (const id of Object.keys(children)) children[id].sort(byName);
  unplaced.sort(byName);
  const placed = {};
  const mark = id => {
    placed[id] = true;
    children[id].forEach(mark);
  };
  if (root) mark(root.id);
  return {rootId: root ? root.id : null, children, placed, unplaced};
}

// The groups above one, from the top down to its parent: [ids].
function ancestorsOf(id, parentOf) {
  const path = [];
  let p = parentOf[id];
  while (p && p !== TOP && !path.includes(p)) {
    path.unshift(p);
    p = parentOf[p];
  }
  return path;
}

// The IDs of the groups beneath one (not counting itself).
function descendantsOf(id, children) {
  const found = [];
  const walk = g => (children[g] || []).forEach(c => {
    found.push(c);
    walk(c);
  });
  walk(id);
  return found;
}

// The choices of a group's Parent dropdown: the names of the groups it may
// be under (see canBeUnder), leaving out itself and the groups beneath it;
// for the top group, PARENT_TOP alone; for a group not in the structure,
// none.
function parentChoices(group, groups, children) {
  if (!inStructure(group)) return [];
  if (isRootGroup(group)) return [PARENT_TOP];
  const below = descendantsOf(group.id, children);
  return groups
    .filter(g => inStructure(g) && g.id !== group.id && !below.includes(g.id) &&
      canBeUnder(group.type, g.type))
    .map(g => g.name)
    .sort((a, b) => a.localeCompare(b));
}

// The structure in order, each group with its depth: {placed: [{id,
// depth}], unplaced: [{id, depth}]}, the tree from the top, and then each
// subtree not yet placed; each group's children by name.
function outline(tree) {
  const walk = (id, depth, out) => {
    out.push({id, depth});
    for (const c of tree.children[id] || []) walk(c, depth + 1, out);
  };
  const placed = [];
  if (tree.rootId) walk(tree.rootId, 0, placed);
  const unplaced = [];
  for (const id of tree.unplaced) walk(id, 0, unplaced);
  return {placed, unplaced};
}

// What the sheet's Problems column says about a group ('' if nothing): why
// it isn't in the structure (typeReason, from guessGroupType), or that it
// isn't placed yet.
function groupProblem(group, typeReason, parentOf, tree, groupsById) {
  if (!inStructure(group)) return 'Not in the structure: ' + (typeReason || 'not a circle or working group');
  if (tree.placed[group.id]) return '';
  if (!parentOf[group.id]) return 'Not yet placed: choose its parent.';
  const above = ancestorsOf(group.id, parentOf);
  const top = groupsById[above[0]];
  return 'Not yet placed: ' + (top ? top.name : 'its parent') + ', above it, isn\'t placed yet.';
}

// What the sheet's Parent cell says for a resolved parent.
function parentCellText(parent, groupsById) {
  if (parent === TOP) return PARENT_TOP;
  return parent && groupsById[parent] ? groupsById[parent].name : '';
}

// ── A circle's place, in its agenda doc ──────────────────────────────────────

// The Parent Circle and Sub-Circles rows of a circle's agenda doc's About
// this circle: {parentLabel, parentValue, subLabel, subValue}, each a list
// of paragraphs, {bullet, runs}, a run [text, {bold, muted, small, link}].
// place: {status: 'placed' | 'top' | 'unplaced' | 'out', parent: {name,
// url} (for 'placed'), children: [{name, url}], structureUrl (the sheet,
// where it's set), structureDocUrl (the doc, where it's shown)}.
// The hint under each label: "(Managed here. See Circle Structure.)", "here"
// linking the sheet (place.structureUrl) and "Circle Structure" the doc
// (place.structureDocUrl).
function structureRows(place) {
  const small = link => ({muted: true, small: true, link: link || null});
  const hint = label => [{runs: [[label, {bold: true}]]}, {runs: [
    ['(Managed ', small()], ['here', small(place.structureUrl)], ['. See ', small()],
    ['Circle Structure', small(place.structureDocUrl)], ['.)', small()]]}];
  const note = text => [{runs: [[text, {muted: true}]]}];
  // A group's name, linked.
  const line = item => [[item.name, item.url ? {link: item.url} : {}]];
  let parentValue;
  if (place.status === 'top') {
    parentValue = note('None: this is the top circle.');
  } else if (place.status === 'unplaced') {
    parentValue = note('Not yet placed in the Circle Structure.');
  } else if (place.status === 'out') {
    parentValue = note('Not in the Circle Structure.');
  } else {
    parentValue = [{runs: line(place.parent)}];
  }
  const children = place.children || [];
  const subValue = children.length && place.status !== 'out'
    ? children.map(c => ({bullet: true, runs: line(c)}))
    : note('None');
  return {parentLabel: hint('Parent Circle'), parentValue, subLabel: hint('Sub-Circles'), subValue};
}

// What a cell's paragraphs say, to tell whether a doc still says what the
// script wrote: {text, links}, its text with runs of white space made one
// space, and the links in it, in order. A cell read from a doc (see
// cellParagraphs, structure.gs) gives the same as the paragraphs written
// to it, whatever paragraphs and runs Docs has split them into.
function paragraphsSignature(paragraphs) {
  let text = '';
  const links = [];
  let last = null;
  for (const p of paragraphs) {
    for (const [t, style] of p.runs) {
      if (!t) continue;
      text += t;
      const link = (style && style.link) || null;
      if (link && link !== last) links.push(link);
      last = link;
    }
    text += ' ';
    last = null;
  }
  return JSON.stringify({text: text.replace(/\s+/g, ' ').trim(), links});
}

// A group's shortest address in the domain, of its primary address and its
// aliases (such as the shorter one change_group_email_alias.py adds); the
// primary if none is shorter. (Aliases outside the domain, such as Google's
// own test-domain ones, are left out.)
function shortestAddress(primary, aliases, domain) {
  return [primary, ...(aliases || []).filter(a => a.endsWith('@' + domain))]
    .reduce((best, a) => (a.length < best.length ? a : best));
}

// Where each of headings is in a sheet's heading row (row, its cells'
// values): {heading: its 1-based column, or 0 if it isn't there}. Lets the
// rows be read from a sheet laid out before its columns were last
// changed.
function columnsByHeading(row, headings) {
  const cols = {};
  const texts = (row || []).map(v => String(v || '').trim().toLowerCase());
  for (const h of headings) cols[h] = texts.indexOf(h.toLowerCase()) + 1;
  return cols;
}

// A group's guests: its Google Group's members (members, addresses) who
// aren't in the root group's (everyone, addresses) nor in leaveOut (such
// as the script's own account, which groups_drive_sync never removes),
// sorted. (Guests are added to a group's Drive folder by hand, and so
// groups_drive_sync puts them in its Google Group.)
function guestsOf(members, everyone, leaveOut) {
  const skip = new Set([...(everyone || []), ...(leaveOut || [])].map(e => String(e).trim().toLowerCase()));
  const guests = [];
  for (const m of members || []) {
    const e = String(m).trim().toLowerCase();
    if (e && !skip.has(e) && !guests.includes(e)) guests.push(e);
  }
  return guests.sort();
}

// ── Meeting times ────────────────────────────────────────────────────────────

const WEEKDAY_CODES = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'];
// (WEEKDAYS and MONTH_NAMES are agenda_rules.gs'.)

// What the sheet's Meeting times cell says about a group's meetings still
// to come (none past): a line for each repeating meeting, in the order of
// their next occurrences, saying when it usually is ("Tue 7:30–9 pm",
// "2nd & 4th Tue 7–8:30 pm", "Last Sun of month 4–5:30 pm"), or, for a
// rule it can't put in words, when it next is ("Next: Nov 12, 7–8:30 pm");
// or, with no repeating meetings, when the next one-off is; or '' if
// there's nothing to come. series: [{rule (its RRULE line, or null),
// weekday, start, end (its usual times), next}]; oneOffs: [{date, start,
// end}]; next the same, for its next occurrence. A date is 'yyyy-MM-dd',
// a weekday 0 (Sunday) to 6, a time minutes since midnight, all in the
// community's time zone.
function meetingTimesText(series, oneOffs) {
  const byDate = (a, b) => (a.date + clockKey(a.start)).localeCompare(b.date + clockKey(b.start));
  const lines = [];
  const sorted = (series || []).slice().sort((a, b) => byDate(a.next, b.next));
  for (const s of sorted) {
    const days = ruleDays(s.rule, s.weekday);
    const line = days ? days + ' ' + clockRange(s.start, s.end) : nextMeetingText(s.next);
    if (!lines.includes(line)) lines.push(line);
  }
  if (!lines.length && (oneOffs || []).length) {
    lines.push(nextMeetingText((oneOffs || []).slice().sort(byDate)[0]));
  }
  return lines.join('\n');
}

function clockKey(minutes) {
  return String(minutes).padStart(4, '0');
}

// "Next: Nov 12, 7–8:30 pm".
function nextMeetingText(m) {
  const [, month, day] = m.date.split('-').map(Number);
  return 'Next: ' + MONTH_NAMES[month - 1] + ' ' + day + ', ' + clockRange(m.start, m.end);
}

// "7:30–9 pm", "11 am–12:30 pm", "7–8 pm", from minutes since midnight.
function clockRange(start, end) {
  const part = minutes => {
    const m = ((minutes % 1440) + 1440) % 1440;
    const h = (Math.floor(m / 60) + 11) % 12 + 1;
    return {text: m % 60 ? h + ':' + String(m % 60).padStart(2, '0') : String(h),
      meridiem: m < 720 ? 'am' : 'pm'};
  };
  const s = part(start);
  const e = part(end);
  return s.meridiem === e.meridiem ? s.text + '–' + e.text + ' ' + e.meridiem
    : s.text + ' ' + s.meridiem + '–' + e.text + ' ' + e.meridiem;
}

// The days a repeating meeting's rule (an RRULE line) is on, in words
// ("Tue", "Mon & Thu", "Every other Tue", "2nd & 4th Tue", "Last Sun of
// month", "15th of month", "Daily"), weekday being its first meeting's;
// or null for a rule this can't put in words.
function ruleDays(rule, weekday) {
  if (!rule) return null;
  const parts = {};
  for (const p of String(rule).replace(/^RRULE:/i, '').split(';')) {
    const [k, v] = p.split('=');
    if (k) parts[k.trim().toUpperCase()] = String(v || '').trim().toUpperCase();
  }
  const known = ['FREQ', 'INTERVAL', 'BYDAY', 'BYSETPOS', 'BYMONTHDAY', 'UNTIL', 'COUNT', 'WKST'];
  if (Object.keys(parts).some(k => !known.includes(k))) return null;
  const interval = Number(parts.INTERVAL || 1);
  const list = words => words.length < 3 ? words.join(' & ')
    : words.slice(0, -1).join(', ') + ' & ' + words[words.length - 1];
  const byDay = parts.BYDAY ? parts.BYDAY.split(',') : [];
  if (parts.FREQ === 'DAILY' && !byDay.length && !parts.BYMONTHDAY && !parts.BYSETPOS) {
    return interval === 1 ? 'Daily' : interval === 2 ? 'Every other day' : null;
  }
  if (parts.FREQ === 'WEEKLY' && !parts.BYMONTHDAY && !parts.BYSETPOS) {
    const codes = byDay.length ? byDay : [WEEKDAY_CODES[weekday]];
    const idx = codes.map(c => WEEKDAY_CODES.indexOf(c));
    if (idx.some(i => i < 0)) return null;
    const days = list(idx.sort((a, b) => a - b).map(i => WEEKDAYS[i]));
    if (interval === 1) return days;
    if (interval === 2) return 'Every other ' + days;
    return 'Every ' + interval + ' weeks, ' + days;
  }
  if (parts.FREQ !== 'MONTHLY' || interval !== 1) return null;
  if (parts.BYMONTHDAY && !byDay.length && !parts.BYSETPOS) {
    const days = parts.BYMONTHDAY.split(',').map(Number);
    if (days.some(d => !(d >= 1 && d <= 31))) return null;
    return list(days.map(ordinal)) + ' of month';
  }
  if (parts.BYMONTHDAY || !byDay.length) return null;
  // "2TU,4TU", or "TU" with BYSETPOS=2 (or 2,4).
  let ords = [];
  let code = null;
  for (const d of byDay) {
    const m = /^([+-]?\d)?(SU|MO|TU|WE|TH|FR|SA)$/.exec(d);
    if (!m || (code && code !== m[2])) return null;
    code = m[2];
    if (m[1]) ords.push(Number(m[1]));
  }
  if (parts.BYSETPOS) {
    if (ords.length || byDay.length !== 1) return null;
    ords = parts.BYSETPOS.split(',').map(Number);
  }
  if (!ords.length || ords.some(n => !(n >= 1 && n <= 5) && n !== -1)) return null;
  ords = ords.filter((n, i) => ords.indexOf(n) === i)
    .sort((a, b) => (a === -1 ? 9 : a) - (b === -1 ? 9 : b));
  const words = ords.map(n => n === -1 ? 'last' : ordinal(n));
  const text = list(words) + ' ' + WEEKDAYS[WEEKDAY_CODES.indexOf(code)];
  return text.charAt(0).toUpperCase() + text.slice(1) + (ords.includes(-1) ? ' of month' : '');
}

// "1st", "2nd", "3rd", "4th", "21st".
function ordinal(n) {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? 'th' : ({1: 'st', 2: 'nd', 3: 'rd'})[n % 10] || 'th';
  return n + suffix;
}
