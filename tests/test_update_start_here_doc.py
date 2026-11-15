"""
Tests for update_start_here_doc.py's filling in of the start-here page, and
checks of the two pages' sources in maintenance_tracker/pages/.
"""

import json
import re

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


def test_values_come_from_the_base():
    people = [{"fields": {"Name": "Dana S", "Full name": "Dana Smith", "Coordinator": True}},
              {"fields": {"Name": "Old T", "Coordinator": True, "Moved out": True}},
              {"fields": {"Name": "Sam L"}}]
    links = [{"fields": {"Name": "Status page", "Value": " https://x.example/s "}},
             {"fields": {"Name": "Update form"}}]
    assert doc.base_values("appX", people, links) == {
        "status-page": "https://x.example/s", "base": "https://airtable.com/appX", "coordinator": "Dana Smith"}
    for coordinators in ([], [True, True]):
        crowd = [{"fields": {"Name": f"P{i}", "Coordinator": c}} for i, c in enumerate(coordinators)]
        with pytest.raises(ValueError):
            doc.base_values("appX", crowd, links)


def test_link_keys_match_the_apps_scripts():
    assert doc.link_key("Request form") == "request-form"
    assert doc.link_key("Tracker help (who to ask)") == "tracker-help-who-to-ask"
    rules = (ROOT / "maintenance_tracker/apps_script/tracker_rules.gs").read_text()
    assert "toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')" in rules


def _seeded_link_keys():
    seeds = json.loads((ROOT / "maintenance_tracker/base/seed.json").read_text())
    return {doc.link_key(r["Name"]) for r in seeds["Links"]["rows"]}


def test_every_placeholder_and_script_link_has_a_row_in_links():
    keys = _seeded_link_keys() | {"base", "coordinator"}
    assert set(doc.placeholders(doc.SOURCE.read_text())) <= keys
    script = (ROOT / "maintenance_tracker/apps_script/maintenance_tracker.gs").read_text()
    used = set(re.findall(r"\blink\('([^']+)'", script))
    assert used and used <= _seeded_link_keys()


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
