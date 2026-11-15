"""
Tests for update_start_here_doc.py's filling in of the start-here page, and
checks of the two pages' sources in maintenance_tracker/pages/.
"""

import pytest

import update_start_here_doc as doc
from tests.test_maintenance_base import ROOT

PAGES = ROOT / "maintenance_tracker" / "pages"


def test_placeholders_are_filled_and_missing_ones_named():
    text = "Ask [[coordinator]] or see [the page]([[status-page]]); [[coordinator]] again."
    assert doc.placeholders(text) == ["coordinator", "status-page"]
    links = {"coordinator": "Dana S", "status-page": "https://example.org/s"}
    assert doc.fill(text, links) == "Ask Dana S or see [the page](https://example.org/s); Dana S again."
    with pytest.raises(KeyError) as e:
        doc.fill(text, {"coordinator": "Dana S"})
    assert e.value.args[0] == ["status-page"]


def test_links_file():
    assert doc.read_links("# links\n\ncoordinator = Dana S\nbase = https://x.example/a=b\n") == {
        "coordinator": "Dana S", "base": "https://x.example/a=b"}
    with pytest.raises(ValueError):
        doc.read_links("coordinator Dana S\n")
    with pytest.raises(ValueError):
        doc.read_links("coordinator =\n")


def test_wrapped_lines_are_joined_but_not_headings_lists_or_tables():
    text = ("# Title\n\nOne paragraph\nwrapped.\n\n- An item\n  wrapped.\n- Another\n\n"
            "| a | b |\n|---|---|\n| c | d |\n\nAfter.\n")
    assert doc.join_wrapped_lines(text) == (
        "# Title\n\nOne paragraph wrapped.\n\n- An item wrapped.\n- Another\n\n"
        "| a | b |\n|---|---|\n| c | d |\n\nAfter.\n")


def test_the_start_here_page_uses_only_short_keys():
    keys = doc.placeholders(doc.SOURCE.read_text())
    assert keys and all(k == k.lower() and " " not in k for k in keys), keys


def test_the_members_page_has_each_paragraph_on_one_line():
    # Gather keeps line breaks (see CLAUDE.md).
    text = (PAGES / "maintenance-tracker-members-page.md").read_text()
    assert doc.join_wrapped_lines(text) == text
