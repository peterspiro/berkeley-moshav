"""
Tests for add_new_group.py's welcome message to a new group's list and the
mailto: link that opens it as a draft.

All names and addresses are fictional.
"""

from urllib.parse import parse_qs, unquote, urlsplit

from add_new_group import GROUP_CALENDAR_HOWTO_URL, build_welcome_message
from util.mail_draft import mailto_url


def _msg(**kw):
    args = dict(group_name="Landscape Circle", list_address="landscape@example.org",
                manager_first_name="Dana", sender_first_name="Sam")
    args.update(kw)
    return build_welcome_message(**args)


class TestBuildWelcomeMessage:
    def test_subject_names_group(self):
        subject, _ = _msg()
        assert subject == "Your new group: Landscape Circle"

    def test_body_covers_required_points(self):
        _, body = _msg()
        assert body.startswith("Hi Dana,\n")
        assert ("I've created the Landscape Circle group in Gather, along with a Google "
                "Drive folder for its documents, a Google calendar for its meetings, and "
                "a Zoom meeting room. Links to all of them are in the footer") in body
        assert "I've also created this email list, landscape@example.org.\n" in body
        assert ("Everyone in the Gather group receives messages sent to the list, can "
                "add and edit files in the folder, and can add and change events on the "
                "calendar. Anyone can post to the list.") in body
        assert ("Please put the group's meetings on its calendar rather than on your own. "
                f"Here's how:\n{GROUP_CALENDAR_HOWTO_URL}\n\nThe Zoom room is") in body
        assert ("To see the calendar in your own Google Calendar, click its link in the "
                "footer.") in body
        assert "its link never changes" in body
        assert ("I've made you a manager of the Gather group, which allows you to add "
                "members to it, and to make other members managers.") in body
        assert "different address for the list" in body
        assert "different name for the group" in body
        assert body.rstrip().endswith("Sam")

    def test_dropped_wording_is_gone(self):
        _, body = _msg()
        assert "with the group's calendar invited" not in body
        assert "passcode" not in body

    def test_no_rename_instructions(self):
        # Renames are requested from the sender, not done by the recipient.
        _, body = _msg()
        assert "can rename" not in body

    def test_missing_names_degrade_gracefully(self):
        _, body = _msg(manager_first_name="", sender_first_name="")
        assert body.startswith("Hi,\n")


class TestMailtoUrl:
    def test_round_trips_fields(self):
        subject, body = _msg(group_name="Café & Garden Club")
        url = mailto_url("landscape@example.org", subject, body)
        parts = urlsplit(url)
        assert parts.scheme == "mailto"
        assert unquote(parts.path) == "landscape@example.org"
        q = parse_qs(parts.query)
        assert q["subject"] == [subject]
        assert q["body"] == [body.replace("\n", "\r\n")]

    def test_reserved_characters_encoded(self):
        url = mailto_url("a@example.org", "x & y? #1", "line1\nline2")
        query = urlsplit(url).query
        assert "&" not in query.split("&body=")[0].replace("subject=", "", 1)
        assert "#" not in url and " " not in url
        assert "%0D%0A" in url


class TestAdminGroups:
    def test_type_letter_and_kind(self):
        from add_new_group import GroupType
        assert GroupType.parse("a") is GroupType.ADMIN
        assert GroupType.parse("squad") is GroupType.ADMIN
        assert GroupType.ADMIN.kind == "squad"

    def test_type_not_inferred_from_a_name(self):
        from add_new_group import GroupType
        assert GroupType.from_name_suffix("Community Meetings") is None
        assert GroupType.from_name_suffix("Book Club") is GroupType.CLUB
        assert GroupType.from_name_suffix("Community Meetings Admins") is GroupType.ADMIN

    def test_welcome_message(self):
        from add_new_group import build_admin_welcome_message
        subject, body = build_admin_welcome_message(
            "Community Meetings", "community-meetings-admins@example.org", "Dana", "Sam",
            "all@example.org")
        assert subject == "Your new community calendar: Community Meetings"
        assert body.startswith("Hi Dana,\n\nI've created a Community Meetings calendar for "
                               "community-wide events, and this email list, "
                               "community-meetings-admins@example.org, for the people who "
                               "schedule them.")
        assert "the whole community (all@example.org) invited" in body
        assert ("- a link to the calendar's Zoom room, if it doesn't already have another "
                "Zoom or Meet link (or says [no zoom] in the description);\n") in body
        assert "unless the event says [no invite].\nSo there's no need" in body
        assert ("You can change the reminder time by adding, for example, [remind 2 hours] "
                "or [no reminder] in its description.") in body
        assert "only gives the calendar a copy" not in body
        assert "a place on the All Berkeley Moshav Events calendar" not in body
        assert 'the doc linked from the footer ("Zoom host key")' in body
        assert "I've made you a manager of the Gather group" in body
        assert body.endswith("\n\nSam\n")


def test_admin_welcome_message_without_zoom():
    from add_new_group import build_admin_welcome_message
    _, body = build_admin_welcome_message(
        "Social Events", "social-events-admins@example.org", "Dana", "Sam", "all@example.org",
        zoom=False)
    assert ("Links to the calendar, its Drive folder, and its admin group in Gather are in "
            "the footer") in body
    assert "Zoom" not in body and "host key" not in body


class TestCalendarAndRoom:
    """add_new_group.py creates a new group's calendar and room without
    looking for ones with matching names; a dry run only says so."""

    def run(self, monkeypatch, capsys, no_zoom=False, real=False, calendars=None,
            meetings=None):
        import add_new_group as ang
        import util.meeting_rooms as mr
        from tests.fakes_meeting_rooms import FakeCalendarService, FakeZoom
        from tests.test_group_meeting_rooms_step import _Settings
        monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "ae")
        monkeypatch.setattr(mr, "log", lambda *a, **k: None)
        cal = FakeCalendarService({"ae": "All Berkeley Moshav Events", **(calendars or {})})
        zoom = FakeZoom()
        found = ang.calendar_and_room(
            cal, zoom, _Settings({}), "Community Meetings", "community-meetings-admins@x.org",
            False, no_zoom, ("community-zoom-hosts@berkeleymoshav.org",), " Zoom Room",
            "Community Meetings Admins", dry_run=not real)
        return found, cal, zoom, capsys.readouterr().out

    def test_dry_run_changes_nothing(self, monkeypatch, capsys):
        found, cal, zoom, out = self.run(monkeypatch, capsys)
        assert found == {"calendar_id": None, "room": None}
        assert cal.writes == [] and zoom.writes == []
        assert "[dry-run] would create calendar 'Community Meetings'" in out
        assert "[dry-run] would create Zoom room 'Community Meetings Zoom Room'" in out
        assert "would ask" not in out

    def test_no_zoom_skips_the_room(self, monkeypatch, capsys):
        _, _, zoom, out = self.run(monkeypatch, capsys, no_zoom=True)
        assert "Zoom room" not in out and zoom.writes == []

    def test_real_run_makes_new_ones_without_matching(self, monkeypatch, capsys):
        found, cal, zoom, out = self.run(monkeypatch, capsys, real=True,
                                         calendars={"old": "Community Meetings"})
        assert found["calendar_id"] != "old" and found["room"]
        assert zoom.list_calls == 0
        assert zoom.meetings[next(iter(zoom.meetings))]["topic"] == "Community Meetings Zoom Room"
        assert "No events on other calendars" not in out


def test_admin_welcome_message_article():
    from add_new_group import build_admin_welcome_message
    _, body = build_admin_welcome_message("Open Mic", "open-mic-admins@example.org", "Dana",
                                          "Sam", "all@example.org")
    assert "I've created an Open Mic calendar for community-wide events" in body
