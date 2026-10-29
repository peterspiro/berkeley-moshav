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


LINK = "https://calendar.google.com/calendar/u/0?cid=YzE"


def test_message():
    subject, text, rich = build_calendar_announcement("Safety and Security Circle", LINK)
    assert subject == "Safety and Security Circle's new calendar and Zoom room"
    assert text.startswith("Hello Safety and Security Circle,\n\n"
                           "You now have your own Google calendar and Zoom meeting room.\n\n"
                           "Everyone:\n    Please add the calendar as follows:\n"
                           f"    1. Click this link: {LINK}\n    2. Click Add\n\n"
                           "Meeting organizers:\n")
    assert "    1. On Google calendar, open the existing meeting for editing.\n" in text
    assert text.endswith("    5. Click Save.  You may want to choose not to send cancellation "
                         "emails to removed Google Calendar guests.\n")
    assert f'<ol><li>Click <a href="{LINK}">this link</a></li><li>Click Add</li></ol>' in rich
    assert rich.count("<li>") == 7
    assert "next to the calendar event icon and most likely currently displays your name" \
        in text
    assert (f'next to the calendar event icon <img src="cid:{ca.ICON_CID}"' in rich
            and '"> and most likely currently displays your name' in rich)
    assert "<p><b>Everyone:</b><br>" in rich and "<p><b>Meeting organizers:</b><br>" in rich


def test_html_is_escaped():
    _, _, rich = build_calendar_announcement("R&D Circle", LINK)
    assert "<p>Hello R&amp;D Circle,</p>" in rich


def test_sends_text_and_html_to_list_with_replies_to_alerts():
    gmail = FakeGmail()
    announce_calendar(gmail, "Technology Circle", "tech@x.org", "c1", dry_run=False)
    (user, msg), = gmail.sent
    assert user == "me"
    assert (msg["To"], msg["Reply-To"]) == ("tech@x.org", ALERT_EMAIL)
    assert msg["Subject"] == "Technology Circle's new calendar and Zoom room"
    plain, related = msg.get_payload()
    assert plain.get_content_type() == "text/plain"
    assert "Hello Technology Circle," in plain.get_payload(decode=True).decode()
    assert related.get_content_type() == "multipart/related"
    rich, icon = related.get_payload()
    assert rich.get_content_type() == "text/html"
    rich_html = rich.get_payload(decode=True).decode()
    assert '<a href="https://calendar.google.com/calendar/u/0?cid=YzE">this link</a>' in rich_html
    assert f'<img src="cid:{ca.ICON_CID}"' in rich_html
    assert icon.get_content_type() == "image/png"
    assert icon["Content-ID"] == f"<{ca.ICON_CID}>"
    assert icon.get_payload(decode=True) == ca.ICON_PATH.read_bytes()


def test_dry_run_shows_email_and_sends_nothing(capsys):
    gmail = FakeGmail()
    announce_calendar(gmail, "Technology Circle", "tech@x.org", None, dry_run=True)
    assert gmail.sent == []
    out = capsys.readouterr().out
    assert '[dry-run] would email tech@x.org: "Technology Circle\'s new calendar and Zoom room"' \
        in out
    assert f"      To: tech@x.org\n      Reply-To: {ALERT_EMAIL}\n" in out
    assert "      Hello Technology Circle,\n" in out
    assert "1. Click this link: (the new calendar's link)" in out
    assert "(Google Groups appends the group's footer" in out


class TestDraft:
    def test_opens_draft_instead_of_sending(self, monkeypatch, capsys):
        monkeypatch.setattr(ca, "log", lambda *a, **k: None)
        opened = []
        monkeypatch.setattr(ca, "open_draft", lambda *args: opened.append(args) or True)
        gmail = FakeGmail()
        announce_calendar(gmail, "Technology Circle", "tech@x.org", "c1", dry_run=False,
                          draft=True)
        assert gmail.sent == []
        (to, subject, body), = opened
        assert to == "tech@x.org"
        assert subject == "Technology Circle's new calendar and Zoom room"
        assert body.startswith("Hello Technology Circle,\n")
        assert "Click this link: https://calendar.google.com/calendar/u/0?cid=YzE" in body
        assert "opened a draft to tech@x.org in your mail program" in capsys.readouterr().out

    def test_no_mail_program_shows_the_email(self, monkeypatch, capsys):
        monkeypatch.setattr(ca, "log", lambda *a, **k: None)
        monkeypatch.setattr(ca, "open_draft", lambda *args: False)
        announce_calendar(FakeGmail(), "Technology Circle", "tech@x.org", "c1",
                          dry_run=False, draft=True)
        out = capsys.readouterr().out
        assert "couldn't open a draft in a mail program" in out
        assert "      Hello Technology Circle,\n" in out and "Reply-To" not in out

    def test_dry_run_opens_nothing(self, monkeypatch, capsys):
        monkeypatch.setattr(ca, "log", lambda *a, **k: None)
        monkeypatch.setattr(ca, "open_draft", lambda *args: pytest.fail("opened a draft"))
        announce_calendar(FakeGmail(), "Technology Circle", "tech@x.org", None,
                          dry_run=True, draft=True)
        out = capsys.readouterr().out
        assert "[dry-run] would open a draft to tech@x.org in your mail program" in out
        assert "Reply-To" not in out

    def test_step_passes_draft_through(self, monkeypatch):
        calls = []
        monkeypatch.setattr(m, "announce_calendar", lambda *args: calls.append(args))
        m.announce_new_calendars(None, {"1": {"name": "Membership", "kind": "circle"}},
                                 {"1": "membership@x.org"}, ["1"], {"1": "c1"},
                                 {"1": RoomLink("https://zoom.us/j/7?pwd=p", "135790")},
                                 dry_run=False, draft=True)
        assert calls == [(None, "Membership Circle", "membership@x.org", "c1", False, True)]


class TestAnnounceNewCalendars:
    GROUPS = {"1": {"name": "Technology Circle", "kind": "circle"},
              "2": {"name": "Book Club", "kind": "club"}}
    EMAILS = {"1": "tech@x.org", "2": "books@x.org"}
    ROOM = RoomLink("https://zoom.us/j/7?pwd=p", "135790")

    def test_only_groups_with_created_calendars_and_links(self, monkeypatch):
        monkeypatch.setattr(m, "log", lambda *a, **k: None)
        gmail = FakeGmail()
        m.announce_new_calendars(gmail, self.GROUPS, self.EMAILS, ["1", "2"],
                                 {"1": "c1", "2": "c2"}, {"1": self.ROOM}, dry_run=False)
        assert [msg["To"] for _, msg in gmail.sent] == ["tech@x.org"]

    def test_names_the_group_by_its_full_name(self, monkeypatch):
        monkeypatch.setattr(m, "log", lambda *a, **k: None)
        gmail = FakeGmail()
        groups = {"1": {"name": "Membership", "kind": "circle"}}
        m.announce_new_calendars(gmail, groups, self.EMAILS, ["1"], {"1": "c1"},
                                 {"1": self.ROOM}, dry_run=False)
        (_, msg), = gmail.sent
        assert msg["Subject"] == "Membership Circle's new calendar and Zoom room"
        plain, _ = msg.get_payload()
        assert "Hello Membership Circle," in plain.get_payload(decode=True).decode()

    def test_pod_gets_no_circle(self, monkeypatch):
        monkeypatch.setattr(m, "log", lambda *a, **k: None)
        gmail = FakeGmail()
        groups = {"1": {"name": "Garden Pod", "kind": "circle"}}
        m.announce_new_calendars(gmail, groups, self.EMAILS, ["1"], {"1": "c1"},
                                 {"1": self.ROOM}, dry_run=False)
        (_, msg), = gmail.sent
        assert msg["Subject"] == "Garden Pod's new calendar and Zoom room"

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
