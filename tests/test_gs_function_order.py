"""
The Apps Script projects' functions are in alphabetical order within each
.gs file, so the editor's function menu (which lists them in file order)
is easy to search.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GS_FILES = sorted(p for p in ROOT.glob("*/*.gs") if p.parent.name != "archive")


@pytest.mark.parametrize("path", GS_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_functions_in_alphabetical_order(path):
    names = re.findall(r"^function\s+(\w+)", path.read_text(), re.MULTILINE)
    assert names == sorted(names, key=str.lower)
