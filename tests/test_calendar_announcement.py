"""
Tests for util/calendar_announcement.py and the update_groups step that
sends it.

All names and addresses are fictional.
"""

import base64
import email

import pytest

import update_groups_in_google_and_hierarchy as m
import util.calendar_announcement as ca
from util.calendar_announcement import (
    ALERT_EMAIL,
    ANNOUNCEMENT_HOWTO_URL,
    announce_calendar,
    build_calendar_announcement,
)
from util.meeting_rooms import RoomLink


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(ca, "log", lambda *a, **k: None)


class FakeGmail:
    def __init__(self):
        self.sent = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):
        self.sent.append((userId, email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]))))
        return self

    def execute(self):
        return {}


def test_message():
    subject, body = build_calendar_announcement("Technology Circle")
    assert subject == "Technology Circle's new calendar and Zoom room"
    assert body.startswith("Hello Technology Circle,\n\nThe Technology Circle now has its own")
    assert "click its link in the footer" in body
    assert f"Here's how:\n{ANNOUNCEMENT_HOWTO_URL}\n" in body
    assert body.endswith(f"Questions? Reply to {ALERT_EMAIL}.\n")


def test_sends_to_list_with_replies_to_alerts():
    gmail = FakeGmail()
    announce_calendar(gmail, "Technology Circle", "tech@x.org", dry_run=False)
    (user, msg), = gmail.sent
    assert user == "me"
    assert (msg["To"], msg["Reply-To"]) == ("tech@x.org", ALERT_EMAIL)
    assert msg["Subject"] == "Technology Circle's new calendar and Zoom room"
    assert "Hello Technology Circle," in msg.get_payload()


def test_dry_run_sends_nothing(capsys):
    gmail = FakeGmail()
    announce_calendar(gmail, "Technology Circle", "tech@x.org", dry_run=True)
    assert gmail.sent == []
    out = capsys.readouterr().out
    assert '[dry-run] would email tech@x.org: "Technology Circle\'s new calendar and Zoom room"' \
        in out
    assert f"      To: tech@x.org\n      Reply-To: {ALERT_EMAIL}\n" in out
    assert "      Hello Technology Circle,\n\n      The Technology Circle now has its own" in out
    assert "(Google Groups appends the group's footer" in out


class TestAnnounceNewCalendars:
    GROUPS = {"1": {"name": "Technology Circle"}, "2": {"name": "Book Club"}}
    EMAILS = {"1": "tech@x.org", "2": "books@x.org"}
    ROOM = RoomLink("https://zoom.us/j/7?pwd=p", "135790")

    def test_only_groups_with_created_calendars_and_links(self, monkeypatch):
        monkeypatch.setattr(m, "log", lambda *a, **k: None)
        gmail = FakeGmail()
        m.announce_new_calendars(gmail, self.GROUPS, self.EMAILS, ["1", "2"],
                                 {"1": "c1", "2": "c2"}, {"1": self.ROOM}, dry_run=False)
        assert [msg["To"] for _, msg in gmail.sent] == ["tech@x.org"]

    def test_none_created_none_sent(self):
        gmail = FakeGmail()
        m.announce_new_calendars(gmail, self.GROUPS, self.EMAILS, [], {"1": "c1"},
                                 {"1": self.ROOM}, dry_run=False)
        assert gmail.sent == []


def test_step_records_only_created_calendars(monkeypatch):
    from tests.fakes_meeting_rooms import FakeCalendarService, FakeZoom
    from tests.test_group_meeting_rooms_step import _Directory, _Settings
    import util.meeting_rooms as mr
    from util.google_group_footer import build_footer_block
    from util.meeting_rooms import LinkedResources
    monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "ae")
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)
    monkeypatch.setattr(m, "log", lambda *a, **k: None)
    groups = {"1": {"name": "Technology Circle", "kind": "circle", "availability": "closed"},
              "3": {"name": "Book Club", "kind": "club", "availability": "open"}}
    footer = build_footer_block("3", "F3", "https://g", "Book Club", calendar_id="c3")
    cal = FakeCalendarService({"ae": "All Berkeley Moshav Events", "c3": "Book Club"})
    created = []
    m.ensure_group_meeting_rooms(
        _Directory(), _Settings({"books@x.org": footer}), cal, FakeZoom(), groups,
        {"1": "F1", "3": "F3"}, {"1": "tech@x.org", "3": "books@x.org"},
        lambda: LinkedResources(), False, {}, {}, created_calendar_group_ids=created)
    assert created == ["1"]


def test_step_with_announce_records_existing_calendars(monkeypatch):
    from tests.fakes_meeting_rooms import FakeCalendarService, FakeZoom
    from tests.test_group_meeting_rooms_step import _Directory, _Settings
    import util.meeting_rooms as mr
    from util.google_group_footer import build_footer_block
    from util.meeting_rooms import LinkedResources
    monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "ae")
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)
    monkeypatch.setattr(m, "log", lambda *a, **k: None)
    groups = {"3": {"name": "Book Club", "kind": "club", "availability": "open"}}
    footer = build_footer_block("3", "F3", "https://g", "Book Club", calendar_id="c3")
    cal = FakeCalendarService({"ae": "All Berkeley Moshav Events", "c3": "Book Club"})
    created = []
    m.ensure_group_meeting_rooms(
        _Directory(), _Settings({"books@x.org": footer}), cal, FakeZoom(), groups,
        {"3": "F3"}, {"3": "books@x.org"}, lambda: LinkedResources(), False, {}, {},
        created_calendar_group_ids=created, announce_calendars=True)
    assert created == ["3"]
