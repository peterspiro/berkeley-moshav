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
// FULL_COMMUNITY_GROUP_NAME in update_groups_in_google_and_hierarchy.py).
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
// their Gather names often aren't.
function guessGroupType(name, item) {
  if (sameName(name, ROOT_GROUP_NAME)) return {type: TYPE_CIRCLE, reason: ''};
  if (!item) {
    return {type: TYPE_OUT, reason: 'its Drive folder couldn\'t be read, to tell its type'};
  }
  if (item.isSharedDrive) {
    return {type: TYPE_OUT, reason: 'it\'s linked to a shared drive, as everybody groups are'};
  }
  const endsWith = (text, word) => new RegExp('\\b' + word + '\\s*$', 'i').test(text || '');
  if (item.inClubs || endsWith(item.name, 'Club') || endsWith(name, 'Club')) {
    return {type: TYPE_OUT, reason: 'it\'s a club'};
  }
  for (const text of [item.name, name]) {
    if (endsWith(text, 'Working Group')) return {type: TYPE_WORKING_GROUP, reason: ''};
    if (endsWith(text, 'Circle')) return {type: TYPE_CIRCLE, reason: ''};
  }
  return {type: TYPE_OUT, reason: 'its Drive folder\'s name, "' + item.name + '", doesn\'t end ' +
    'with "Circle" or "Working Group", to tell its type (rename the folder to fix this)'};
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
// of paragraphs, {bullet, runs}, a run [text, {bold, muted, link}]. place:
// {status: 'placed' | 'top' | 'unplaced' | 'out', ancestors: [{name,
// url}], children: [{name, url, type, next}], parentNext, leader,
// feedbackLink, structureUrl}, next and parentNext {text, url} (a
// meeting) or null, leader and feedbackLink the names the doc's Roles
// show ('' or ROLE_PLACEHOLDER if none).
const STRUCTURE_HINT = 'Set in the Circle Structure';
function structureRows(place) {
  const hint = label => [{runs: [[label, {bold: true}]]},
    {runs: [[STRUCTURE_HINT, {muted: true, link: place.structureUrl || null}]]}];
  const note = text => [{runs: [[text, {muted: true}]]}];
  const linked = item => [item.name, item.url ? {link: item.url} : {}];
  let parentValue;
  if (place.status === 'top') {
    parentValue = note('None: this is the top circle.');
  } else if (place.status === 'unplaced') {
    parentValue = note('Not yet placed in the Circle Structure.');
  } else if (place.status === 'out') {
    parentValue = note('Not in the Circle Structure.');
  } else {
    const path = [];
    place.ancestors.forEach((a, i) => {
      if (i) path.push([' › ', {muted: true}]);
      path.push(linked(a));
    });
    parentValue = [{runs: path}];
    if (place.parentNext) {
      parentValue.push({runs: [['Its next meeting: ', {muted: true}],
        [place.parentNext.text, place.parentNext.url ? {link: place.parentNext.url} : {}]]});
    }
    const holder = name => (name && name !== ROLE_PLACEHOLDER ? name : 'not chosen yet');
    parentValue.push({runs: [['Our links to it: ', {muted: true}], ['Leader ', {muted: true}],
      [holder(place.leader), {}], [' · Feedback Link ', {muted: true}],
      [holder(place.feedbackLink), {}]]});
  }
  const children = place.children || [];
  const subValue = children.length && place.status !== 'out'
    ? children.map(c => {
      const runs = [linked(c)];
      if (c.type === TYPE_WORKING_GROUP) runs.push([' · working group', {muted: true}]);
      if (c.next) {
        runs.push([' · next meeting ', {muted: true}]);
        runs.push([c.next.text, c.next.url ? {link: c.next.url, muted: true} : {muted: true}]);
      }
      return {bullet: true, runs};
    })
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
