"""
Tests for add_new_group.py's welcome message to a new group's list and the
mailto: link that opens it as a draft.

All names and addresses are fictional.
"""

from urllib.parse import parse_qs, unquote, urlsplit

from add_new_group import build_welcome_message, mailto_url


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
        assert "Please put the group's meetings on its calendar rather than on your own." in body
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
