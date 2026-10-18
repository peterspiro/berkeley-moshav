"""
Tests for update_groups_in_google_and_hierarchy.ensure_group_meeting_rooms
(which groups get a calendar and Zoom room) and for the footer step writing
their links.

All names, IDs, and addresses are fictional.
"""

import pytest

import update_groups_in_google_and_hierarchy as m
import util.meeting_rooms as mr
from tests.fakes_meeting_rooms import FakeCalendarService, FakeZoom, room
from util.google_group_footer import (
    ITEM_TYPE_FOLDER,
    build_footer_block,
    parse_footer_calendar_id,
    parse_footer_zoom_url,
)
from util.meeting_rooms import LinkedResources, RoomLink

GROUPS = {
    "1": {"name": "Technology Circle", "kind": "circle", "availability": "closed"},
    "2": {"name": "Full Community", "kind": "circle", "availability": "everybody"},
    "3": {"name": "Book Club", "kind": "club", "availability": "open"},
    "4": {"name": "No List Circle", "kind": "circle", "availability": "closed"},
    "5": {"name": "No Folder Circle", "kind": "circle", "availability": "closed"},
}
EMAILS = {"1": "tech@x.org", "2": "all@x.org", "3": "books@x.org", "5": "nofolder@x.org"}
FOLDERS = {"1": "F1", "2": "F2", "3": "F3", "4": "F4"}


class _Settings:
    def __init__(self, footers):
        self.footers = footers
        self.updated = {}
        self._op = None

    def groups(self):
        return self

    def get(self, groupUniqueId):
        self._op = ("get", groupUniqueId, None)
        return self

    def update(self, groupUniqueId, body):
        self._op = ("update", groupUniqueId, body)
        return self

    def execute(self):
        op, email, body = self._op
        if op == "get":
            return {"customFooterText": self.footers.get(email, ""), "includeCustomFooter": "true"}
        self.updated[email] = body
        return body


class _Directory:
    """Directory API stand-in: groups().get(groupKey=...) resolves aliases
    ({alias: primary}) and returns any other address as its own primary."""

    def __init__(self, aliases=None):
        self.aliases = aliases or {}
        self._key = None

    def groups(self):
        return self

    def get(self, groupKey):
        self._key = groupKey
        return self

    def execute(self):
        email = self.aliases.get(self._key, self._key)
        return {"id": email, "email": email, "name": email}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "ae")
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)
    monkeypatch.setattr(m, "log", lambda *a, **k: None)


def _run(settings, cal, zoom, dry_run=False):
    cals, urls = {}, {}
    m.ensure_group_meeting_rooms(
        _Directory(), settings, cal, zoom, GROUPS, FOLDERS, EMAILS, lambda: LinkedResources(),
        dry_run, cals, urls,
    )
    return cals, urls


def test_only_footered_non_everybody_groups_get_rooms():
    cal = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
    zoom = FakeZoom()
    cals, urls = _run(_Settings({}), cal, zoom)
    assert set(cals) == set(urls) == {"1", "3"}
    topics = sorted(w[1] for w in zoom.writes if w[0] == "create")
    assert topics == ["Book Club Meeting Room", "Technology Circle Meeting Room"]


def test_footer_links_reused():
    footer = build_footer_block("1", "F1", "https://g", "Technology Circle",
                                calendar_id="c1", zoom_url="https://zoom.us/j/7?pwd=p")
    cal = FakeCalendarService(
        {"ae": "All Berkeley Moshav Events", "c1": "Technology Circle"},
        {"c1": [{"id": "r", "role": "writer", "scope": {"type": "group", "value": "tech@x.org"}}]})
    zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room")},
                    last_used={"7": "2026-10-01T00:00:00Z"})
    groups = {"1": GROUPS["1"]}
    cals, urls = {}, {}
    m.ensure_group_meeting_rooms(
        _Directory(), _Settings({"tech@x.org": footer}), cal, zoom, groups, FOLDERS, EMAILS,
        lambda: LinkedResources(), False, cals, urls,
    )
    assert cals == {"1": "c1"} and urls == {"1": RoomLink("https://zoom.us/j/7?pwd=p", "135790")}
    assert cal.writes == [] and zoom.writes == []


def test_dry_run_changes_nothing():
    cal = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
    zoom = FakeZoom()
    assert _run(_Settings({}), cal, zoom, dry_run=True) == ({}, {})
    assert cal.writes == [] and zoom.writes == []


def test_footer_step_writes_calendar_and_zoom_links(capsys):
    old = build_footer_block("1", "F1", "https://g", "Technology Circle")
    settings = _Settings({"tech@x.org": old})
    m.ensure_group_footers(
        settings, "https://g", {"1": GROUPS["1"]}, FOLDERS, {"1": ITEM_TYPE_FOLDER}, EMAILS,
        False, {"1": "c1@group.calendar.google.com"},
        {"1": RoomLink("https://zoom.us/j/7?pwd=p", "135790")},
    )
    footer = settings.updated["tech@x.org"]["customFooterText"]
    assert parse_footer_calendar_id(footer) == "c1@group.calendar.google.com"
    assert parse_footer_zoom_url(footer) == "https://zoom.us/j/7?pwd=p"
    assert "passcode 135790" in footer
    assert "calendar link; Zoom link" in capsys.readouterr().out


def test_calendar_shared_with_primary_address_not_alias():
    cal = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
    cals, urls = {}, {}
    m.ensure_group_meeting_rooms(
        _Directory({"tech@x.org": "technology-circle@x.org"}), _Settings({}), cal, FakeZoom(),
        {"1": GROUPS["1"]}, FOLDERS, EMAILS, lambda: LinkedResources(), False, cals, urls,
    )
    shared = [w[2]["scope"]["value"] for w in cal.writes if w[0] == "acl_insert"]
    assert shared == ["technology-circle@x.org"]


def test_status_line_for_every_group(capsys):
    cal = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
    _run(_Settings({}), cal, FakeZoom())
    out = capsys.readouterr().out
    assert "'Technology Circle': calendar and Zoom room in place" in out
    assert "'Book Club': calendar and Zoom room in place" in out
    assert "'Full Community': no calendar or Zoom room (an everybody group)" in out
    assert "'No List Circle': no calendar or Zoom room yet (no email list)" in out
    assert "'No Folder Circle': no calendar or Zoom room yet (no Drive folder)" in out


def test_dry_run_status_says_what_would_be_created(capsys):
    cal = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
    _run(_Settings({}), cal, FakeZoom(), dry_run=True)
    out = capsys.readouterr().out
    assert ("'Technology Circle': calendar would be created; Zoom room would be created"
            in out)
