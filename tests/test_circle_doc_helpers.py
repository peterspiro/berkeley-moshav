"""
Tests for circle_agendas/doc_helpers.gs's apiDoc, which reads a doc again
when the Docs API fails in a way that passes, run under Node (skipped if
Node isn't installed) with fake Docs and Utilities services.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HELPERS = Path(__file__).resolve().parent.parent / "circle_agendas" / "doc_helpers.gs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


def run(errors: list[str | None], options: bool = True):
    """Call apiDoc with a fake Docs.Documents.get that throws each of errors
    in turn (None: returns a doc). Returns {doc, error, calls, sleeps}."""
    program = f"""
const vm = require('vm'), fs = require('fs');
const errors = {json.dumps(errors)};
const calls = [], sleeps = [];
const ctx = vm.createContext({{
  Docs: {{Documents: {{get: (...args) => {{
    calls.push(args);
    const err = errors[calls.length - 1];
    if (err) throw new Error(err);
    return {{documentId: args[0]}};
  }}}}}},
  Utilities: {{sleep: ms => sleeps.push(ms)}},
}});
vm.runInContext(fs.readFileSync({json.dumps(str(HELPERS))}, 'utf8'), ctx);
let doc = null, error = null;
try {{
  doc = vm.runInContext({json.dumps("apiDoc('doc1'" + (", {includeTabsContent: true})" if options else ")"))}, ctx);
}} catch (e) {{
  error = e.message;
}}
process.stdout.write(JSON.stringify({{doc, error, calls, sleeps}}));
"""
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


EMPTY = "API call to docs.documents.get failed with error: Empty response"


def test_reads_once_when_it_works():
    out = run([None])
    assert out["doc"] == {"documentId": "doc1"}
    assert out["calls"] == [["doc1", {"includeTabsContent": True}]]
    assert out["sleeps"] == []


def test_passes_no_options_when_given_none():
    assert run([None], options=False)["calls"] == [["doc1"]]


@pytest.mark.parametrize("message", [
    EMPTY,
    "API call to docs.documents.get failed with error: Internal error encountered.",
    "API call to docs.documents.get failed with error: The service is currently unavailable.",
    "API call to docs.documents.get failed with error: Backend Error",
])
def test_reads_again_after_an_error_that_passes(message):
    out = run([message, None])
    assert out["doc"] == {"documentId": "doc1"}
    assert len(out["calls"]) == 2
    assert out["sleeps"] == [1000]


def test_gives_up_after_three_tries():
    out = run([EMPTY, EMPTY, EMPTY, None])
    assert out["error"] == EMPTY
    assert len(out["calls"]) == 3
    assert out["sleeps"] == [1000, 2000]


def test_doesnt_read_again_after_other_errors():
    message = "API call to docs.documents.get failed with error: Requested entity was not found."
    out = run([message, None])
    assert out["error"] == message
    assert len(out["calls"]) == 1
