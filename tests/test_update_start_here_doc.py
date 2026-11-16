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
    people[0]["fields"].update({"Circle member": True, "Update form link": "https://x.example/u?prefill_By=Dana+S"})
    assert doc.base_values("appX", people, links) == {
        "status-page": "https://x.example/s", "base": "https://airtable.com/appX", "coordinator": "Dana",
        "update-links": "- [Dana S](https://x.example/u?prefill_By=Dana+S)"}
    for coordinators in ([], [True, True]):
        crowd = [{"fields": {"Name": f"P{i}", "Coordinator": c, "Circle member": True,
                             "Update form link": "https://x.example/u"}} for i, c in enumerate(coordinators)]
        with pytest.raises(ValueError):
            doc.base_values("appX", crowd, links)


def test_update_links_list_the_circle_by_name():
    def person(name, **fields):
        return {"fields": dict({"Name": name, "Circle member": True,
                                "Update form link": f"https://x.example/u?prefill_By={name[0]}"}, **fields)}
    people = [person("sam L"), person("Dana S"), person("Old T", **{"Moved out": True}),
              person("Pat S", **{"Circle member": False}), person("New N", **{"Update form link": ""})]
    assert doc.update_links(people) == (
        "- [Dana S](https://x.example/u?prefill_By=D)\n- [sam L](https://x.example/u?prefill_By=s)", ["New N"])
    # The list stays a list in the Doc: each item its own line, after the page's wrapped lines are joined.
    page = doc.join_wrapped_lines(doc.fill("Open\nyours:\n\n[[update-links]]\n\nUse your own.",
                                           {"update-links": doc.update_links(people)[0]}))
    assert page.split("\n")[2:4] == ["- [Dana S](https://x.example/u?prefill_By=D)",
                                     "- [sam L](https://x.example/u?prefill_By=s)"]
    coordinator = [{"fields": {"Name": "Dana S", "Coordinator": True}}]
    with pytest.raises(ValueError, match="Update form link"):
        doc.base_values("appX", coordinator, [])


def test_trial_text_is_kept_for_the_test_base_only():
    text = "# Start here{{trial: (trial)}}\n\n{{trial:**A trial.** Made-up\nrequests.}}\n\nOpen the Base{{trial: (test)}} base."
    assert doc.trial_text(text, real=False) == (
        "# Start here (trial)\n\n**A trial.** Made-up\nrequests.\n\nOpen the Base (test) base.")
    assert doc.trial_text(text, real=True) == "# Start here\n\nOpen the Base base."


def test_the_start_here_page_says_its_a_trial_unless_real():
    source = doc.SOURCE.read_text()
    trial, real = doc.trial_text(source, real=False), doc.trial_text(source, real=True)
    assert trial.startswith("# Maintenance tracker: start here (trial)\n\n**This is a trial version.**")
    assert "Building Maintenance (test) base" in trial
    assert real.startswith("# Maintenance tracker: start here\n\nEverything the circle uses")
    for page in (trial, real):
        assert "{{" not in page and "}}" not in page
    for word in ("trial", "test copy", "(test)"):
        assert word not in real, word


def test_link_keys_match_the_apps_scripts():
    assert doc.link_key("Request form") == "request-form"
    assert doc.link_key("Tracker help (who to ask)") == "tracker-help-who-to-ask"
    rules = (ROOT / "maintenance_tracker/apps_script/tracker_rules.gs").read_text()
    assert "toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')" in rules


def _seeded_link_keys():
    seeds = json.loads((ROOT / "maintenance_tracker/base/seed.json").read_text())
    return {doc.link_key(r["Name"]) for r in seeds["Links"]["rows"]}


def test_every_placeholder_and_script_link_has_a_row_in_links():
    keys = _seeded_link_keys() | {"base", "coordinator", "update-links"}
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


def test_the_doc_is_made_pageless():
    [request] = doc.pageless_request()["requests"]
    style = request["updateDocumentStyle"]
    assert style["documentStyle"]["documentFormat"]["documentMode"] == "PAGELESS"
    assert style["fields"] == "documentFormat"


def test_the_start_here_page_uses_only_short_keys():
    keys = doc.placeholders(doc.SOURCE.read_text())
    assert keys and all(k == k.lower() and " " not in k for k in keys), keys


def test_the_members_page_has_each_paragraph_on_one_line():
    # Gather keeps line breaks (see CLAUDE.md).
    text = (PAGES / "maintenance-tracker-members-page.md").read_text()
    assert doc.join_wrapped_lines(text) == text
    # Its title is a field of Gather's wiki editor, and [[...]] would be a wiki link there.
    assert not text.startswith("#") and "[[" not in text
