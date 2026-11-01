/**
 * Doc helpers
 *
 * Shared by the scripts in this project: the template's ID, and helpers for
 * finding things in an agenda doc and describing it, through both Apps
 * Script's DocumentApp and the Docs API (the Docs advanced service), which
 * see different things: DocumentApp shows dropdown chips only as
 * UNSUPPORTED elements, while the API shows them as `dropdown`.
 */

// The circle agenda template, which build_template.gs builds. Empty until
// createTemplate has made it.
const TEMPLATE_DOC_ID = '';
// Marks what the probes insert.
const PROBE_MARK = '⟦probe⟧';

// Copies a doc to the running account's My Drive (not beside the original,
// where everyone who can see its folder would see the copy).
function copyDoc(docId, name) {
  return DriveApp.getFileById(docId).makeCopy(name, DriveApp.getRootFolder());
}

// Logs a doc's link on a line of its own, after what it's for, so it can be
// selected with a triple-click.
function logLink(label, url) {
  Logger.log(label);
  Logger.log(url);
}

// ── Describing a doc ─────────────────────────────────────────────────────────

// Lines describing the doc's first tab: each top-level element, each table
// row (its cells' text and chips), and the kinds of paragraph element the
// Docs API sees.
function describeStructure(docId) {
  const lines = [];
  const body = DocumentApp.openById(docId).getBody();
  for (let i = 0; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    const type = el.getType();
    if (type === DocumentApp.ElementType.TABLE) {
      lines.push(i + ': TABLE, ' + el.getNumRows() + ' rows, ' + countLeaves(el));
      for (let r = 0; r < el.getNumRows(); r++) {
        const row = el.getRow(r);
        const cells = [];
        for (let c = 0; c < row.getNumCells(); c++) {
          cells.push(snippet(row.getCell(c).getText(), 25));
        }
        lines.push('    row ' + r + ': ' + cells.join(' | ') + '   ' + countLeaves(row));
      }
    } else {
      const text = el.getText ? snippet(el.getText(), 50) : '';
      lines.push(i + ': ' + type + ' "' + text + '"   ' + countLeaves(el));
    }
  }

  const kinds = {};
  const examples = {};
  walkParagraphs(Docs.Documents.get(docId).body.content, para => {
    for (const pe of para.elements || []) {
      for (const k of Object.keys(pe)) {
        if (k === 'startIndex' || k === 'endIndex') continue;
        kinds[k] = (kinds[k] || 0) + 1;
        if (k !== 'textRun' && !examples[k]) {
          examples[k] = JSON.stringify(pe[k]).slice(0, 200) + '  in "' + snippet(paraText(para), 40) + '"';
        }
      }
    }
  });
  lines.push('', 'Docs API paragraph element kinds: ' + JSON.stringify(kinds));
  for (const k of Object.keys(examples)) lines.push('  ' + k + ' e.g. ' + examples[k]);
  return lines;
}


// ── Writing a report ─────────────────────────────────────────────────────────

function writeReport(docId, lines) {
  const doc = DocumentApp.openById(docId);
  const body = doc.getBody();
  body.appendParagraph('Probe report').setHeading(DocumentApp.ParagraphHeading.HEADING2);
  for (const line of lines) {
    body.appendParagraph(line).setHeading(DocumentApp.ParagraphHeading.NORMAL);
  }
  doc.saveAndClose();
}

// ── Helpers ──────────────────────────────────────────────────────────────────

// Counts the non-text leaf elements (chips and anything Apps Script can't
// represent, which it calls UNSUPPORTED) under an element, as a string.
function countLeaves(el) {
  const counts = {};
  addLeafCounts(el, counts);
  return formatCounts(counts);
}

function mergeCounts(elements) {
  const counts = {};
  for (const el of elements) addLeafCounts(el, counts);
  return formatCounts(counts);
}

function addLeafCounts(el, counts) {
  const type = String(el.getType());
  if (el.getNumChildren) {
    for (let i = 0; i < el.getNumChildren(); i++) addLeafCounts(el.getChild(i), counts);
  } else if (type !== 'TEXT') {
    counts[type] = (counts[type] || 0) + 1;
  }
}

function formatCounts(counts) {
  const keys = Object.keys(counts).sort();
  return keys.length ? '[' + keys.map(k => k + '×' + counts[k]).join(', ') + ']' : '[no chips]';
}

// The first table row (at any depth) one of whose cells' text, trimmed,
// passes test.
function findRow(body, test) {
  let result = null;
  let search = body.findElement(DocumentApp.ElementType.TABLE);
  while (search && !result) {
    const table = search.getElement().asTable();
    for (let r = 0; r < table.getNumRows() && !result; r++) {
      const row = table.getRow(r);
      for (let c = 0; c < row.getNumCells(); c++) {
        if (test(row.getCell(c).getText().trim())) result = {table, row};
      }
    }
    search = body.findElement(DocumentApp.ElementType.TABLE, search);
  }
  return result;
}

// Calls fn on every paragraph in Docs API content, including inside tables.
function walkParagraphs(content, fn) {
  for (const se of content || []) {
    if (se.paragraph) fn(se.paragraph);
    if (se.table) {
      for (const row of se.table.tableRows) {
        for (const cell of row.tableCells) walkParagraphs(cell.content, fn);
      }
    }
  }
}

// Calls fn on every table row in Docs API content, including nested tables.
function walkTableRows(content, fn) {
  for (const se of content || []) {
    if (!se.table) continue;
    for (const row of se.table.tableRows) {
      fn(row);
      for (const cell of row.tableCells) walkTableRows(cell.content, fn);
    }
  }
}

function paraText(para) {
  return (para.elements || []).map(pe => (pe.textRun ? pe.textRun.content : '')).join('');
}

function cellText(cell) {
  return (cell.content || []).filter(se => se.paragraph).map(se => paraText(se.paragraph)).join('');
}

function snippet(text, n) {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > n ? flat.slice(0, n - 1) + '…' : flat;
}

// Every tab in a Docs API document, child tabs included.
function allApiTabs(tabs) {
  return (tabs || []).flatMap(tab => [tab, ...allApiTabs(tab.childTabs)]);
}
