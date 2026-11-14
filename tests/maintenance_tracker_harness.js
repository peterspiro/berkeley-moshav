/**
 * Runs the maintenance_tracker Apps Script under Node, against stand-ins
 * for Airtable, Gmail, Drive, and the script's properties
 * (tests/test_maintenance_tracker.py).
 *
 * Reads JSON on stdin: { tables: { name: [{ id, fields }] }, props: {},
 * run: [function names], expr: optional expression to evaluate first }.
 * Writes { tables, mails, props, drive, calls, result } as JSON.
 *
 * The stand-in Airtable returns every record of a table whatever formula
 * is asked for: the script's rules, not its formulas, decide what's done.
 */
const vm = require('vm'), fs = require('fs'), path = require('path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dir = path.join(__dirname, '..', 'maintenance_tracker');

const tables = input.tables || {};
const props = Object.assign({}, input.props);
const mails = [], calls = [], triggers = [], cache = {};
let nextId = 1;

function airtableFetch(url, options) {
  const rest = url.slice('https://api.airtable.com/v0/'.length);
  const [pathPart] = rest.split('?');
  const [base, table, recordId] = pathPart.split('/').map(decodeURIComponent);
  const method = (options.method || 'get').toLowerCase();
  calls.push(`${method} ${table}${recordId ? '/record' : ''}`);
  if (options.headers.Authorization !== 'Bearer ' + props.AIRTABLE_TOKEN || base !== props.AIRTABLE_BASE_ID) {
    return reply(403, { error: 'wrong token or base' });
  }
  if (!tables[table]) return reply(404, { error: 'NOT_FOUND ' + table });
  const body = options.payload ? JSON.parse(options.payload) : null;
  if (method === 'get' && recordId) {
    const found = tables[table].find(r => r.id === recordId);
    return found ? reply(200, found) : reply(404, { error: 'NOT_FOUND' });
  }
  if (method === 'get') return reply(200, { records: tables[table] });
  if (body.records.length > 10) return reply(422, { error: 'too many records' });
  if (method === 'post') {
    const made = body.records.map(r => ({ id: `recNew${nextId++}`, fields: r.fields }));
    made.forEach(r => tables[table].push(r));
    return reply(200, { records: made });
  }
  if (method === 'patch') {
    body.records.forEach(change => {
      const record = tables[table].find(r => r.id === change.id);
      Object.keys(change.fields).forEach(k => {
        // Airtable leaves out a field that's been emptied.
        if (change.fields[k] === null) delete record.fields[k]; else record.fields[k] = change.fields[k];
      });
    });
    return reply(200, { records: [] });
  }
  return reply(405, {});
}

function reply(code, body) {
  return { getResponseCode: () => code, getContentText: () => JSON.stringify(body) };
}

// Drive, as the advanced service's Files: only what the script itself created.
const driveFiles = [];
const Files = {
  create: (resource, blob) => {
    const file = Object.assign({ id: `file${nextId++}`, parents: ['myDrive'] }, resource);
    if (blob) file.content = blob.content;
    driveFiles.push(file);
    return file;
  },
  update: (resource, id) => Object.assign(driveFiles.find(f => f.id === id), resource),
  list: args => {
    const parent = /^'([^']+)' in parents and trashed = false$/.exec(args.q)[1];
    return { files: driveFiles.filter(f => f.parents[0] === parent && !f.trashed) };
  },
};

function tree(parent) {
  const out = {};
  driveFiles.filter(f => f.parents[0] === parent && !f.trashed).forEach(f => {
    if (f.mimeType === 'application/vnd.google-apps.folder') out[f.name + '/'] = tree(f.id);
    else out[f.name] = f.content;
  });
  return out;
}

const ctx = vm.createContext({
  console: { log: () => {}, error: () => {} },
  Intl: Intl, JSON: JSON, Date: Date,
  UrlFetchApp: {
    fetch: (url, options) => url.startsWith('https://api.airtable.com/')
      ? airtableFetch(url, options || {})
      : { getBlob: () => { const b = { content: 'bytes of ' + url, setName: n => { b.name = n; return b; } }; return b; } },
  },
  MailApp: { sendEmail: m => { mails.push(m); } },
  PropertiesService: { getScriptProperties: () => ({
    getProperty: k => (k in props ? props[k] : null),
    setProperty: (k, v) => { props[k] = v; },
  }) },
  LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} }) },
  Drive: { Files: Files },
  Utilities: {
    formatDate: (d, tz) => new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(d),
    newBlob: (content, type, name) => ({ content: content, name: name }),
  },
  CacheService: { getScriptCache: () => ({
    get: k => (k in cache ? cache[k] : null),
    put: (k, v) => { cache[k] = v; },
    remove: k => { delete cache[k]; },
  }) },
  ScriptApp: {
    WeekDay: { SUNDAY: 'SUNDAY' },
    getProjectTriggers: () => [],
    deleteTrigger: () => {},
    // A trigger is recorded as the chain of calls that built it.
    newTrigger: name => {
      const chain = [name];
      const builder = new Proxy({}, { get: (_, method) => (...args) => {
        if (method === 'create') { triggers.push(chain.join(' ')); return null; }
        chain.push(args.length ? `${String(method)}(${args.join(',')})` : String(method));
        return builder;
      } });
      return builder;
    },
  },
});
['tracker_rules.gs', 'maintenance_tracker.gs'].forEach(f =>
  vm.runInContext(fs.readFileSync(path.join(dir, f), 'utf8'), ctx, { filename: f }));

let result = null;
if (input.values) Object.keys(input.values).forEach(k => { ctx[k] = input.values[k]; });
if (input.expr) result = vm.runInContext(`JSON.stringify(${input.expr})`, ctx);
(input.run || []).forEach(name => vm.runInContext(`${name}()`, ctx));
process.stdout.write(JSON.stringify({
  tables: tables, mails: mails, props: props, triggers: triggers, drive: tree('myDrive'), calls: calls,
  result: result === undefined || result === null ? null : JSON.parse(result),
}));
