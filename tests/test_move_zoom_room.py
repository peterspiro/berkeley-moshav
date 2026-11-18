"""
Tests for move_zoom_room.py: which Zoom users are offered, choosing one,
and moving the room (or falling back to moving it by hand).

All names, IDs, and addresses are fictional.
"""

import datetime

import pytest

import move_zoom_room as mz
from tests.fakes_meeting_rooms import FakeZoom, room
from util.google_group_footer import build_footer_block, parse_footer_zoom_url
from util.zoom_conflicts import Meeting
from util.zoom_utils import COMMUNITY_ROOMS_USER, ROOM_USERS, ROOMS_USER, ZoomError

UTC = datetime.timezone.utc
SECOND = ROOM_USERS[1]


def at(hour, minute=0):
    return datetime.datetime(2026, 11, 24, hour, minute, tzinfo=UTC)


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(mz, "log", lambda *a, **k: None)


def _footer(url="https://zoom.us/j/7?pwd=p"):
    return "Notes\n\n" + build_footer_block("1", "F", "https://g", "Tea Club",
                                            zoom_url=url, zoom_passcode="135790")


def _group(**kw):
    return {"email": "tea@example.org", "name": "Tea Club", "footer": _footer(),
            "calendar_id": "tea-cal", "meeting_id": "7", "audience": None, **kw}


class _Settings:
    def __init__(self):
        self.updated = None

    def groups(self):
        return self

    def update(self, groupUniqueId, body):
        self.updated = body
        return self

    def execute(self):
        return self.updated


class _MovingZoom(FakeZoom):
    """Moves a meeting when asked, unless refuse is set (then raises, as
    Zoom does without scheduling privilege) or ignore is set (then does
    nothing)."""

    def __init__(self, meetings, refuse=False, ignore=False, new_link=None):
        super().__init__(meetings)
        self.refuse, self.ignore, self.new_link = refuse, ignore, new_link

    def move_meeting(self, meeting_id, user):
        self.writes.append(("move", meeting_id, user))
        if self.refuse:
            raise ZoomError('Zoom PATCH failed (400): {"code":1110}')
        if not self.ignore:
            self._move(meeting_id, user)

    def _move(self, meeting_id, user):
        self.meetings[meeting_id]["host_email"] = user
        if self.new_link:
            self.meetings[meeting_id]["join_url"] = self.new_link


def _answers(*answers):
    it = iter(answers)
    return lambda prompt: next(it)


class TestChoices:
    def test_every_room_user_offered(self):
        assert mz.choices_for(ROOMS_USER) == list(ROOM_USERS)
        assert COMMUNITY_ROOMS_USER in ROOM_USERS

    def test_unexpected_current_user_listed_first(self):
        assert mz.choices_for("other@example.org") == ["other@example.org", *ROOM_USERS]

    def test_enter_keeps_current(self):
        assert mz.choose_user(list(ROOM_USERS), SECOND, None, _answers("")) == SECOND

    def test_asks_again_after_a_bad_answer(self, capsys):
        assert mz.choose_user(list(ROOM_USERS), ROOMS_USER, None, _answers("9", "x", "2")) \
            == SECOND
        assert "Type a number" in capsys.readouterr().out

    def test_user_option(self):
        assert mz.choose_user(list(ROOM_USERS), ROOMS_USER, 3, _answers()) == ROOM_USERS[2]
        with pytest.raises(SystemExit):
            mz.choose_user(list(ROOM_USERS), ROOMS_USER, 4, _answers())


class TestRoomOf:
    def test_community_calendar_refused(self):
        with pytest.raises(SystemExit, match="community-wide"):
            mz.room_of(FakeZoom(), _group(audience="everyone@example.org"))

    def test_no_room_link(self):
        with pytest.raises(SystemExit, match="no Zoom meeting link"):
            mz.room_of(FakeZoom(), _group(meeting_id=None))

    def test_room_gone(self):
        with pytest.raises(SystemExit, match="no longer exists"):
            mz.room_of(FakeZoom(), _group())

    def test_room(self):
        assert mz.room_of(FakeZoom({"7": room(7, "Tea Club Meeting Room")}), _group())["id"] == 7


def _meetings():
    """The room's meeting, and one under the second user that overlaps it."""
    return [
        Meeting("a", "7", ROOMS_USER, at(3), at(4), "Tea", ("Tea Club",)),
        Meeting("b", "8", SECOND, at(3, 30), at(5), "Garden", ("Garden Circle",)),
    ]


class TestRun:
    def test_report_lists_each_users_conflicts(self, capsys):
        zoom = _MovingZoom({"7": room(7, "Tea Club Meeting Room")})
        mz.run(zoom, _Settings(), _group(), zoom.meetings["7"], _meetings(), None, True,
               _answers())
        out = capsys.readouterr().out
        assert f"1. Zoom user 1, {ROOMS_USER} (current: choosing it changes nothing; " \
               "the default)" in out
        assert f"2. Zoom user 2, {SECOND}\n       1 conflict:\n       - overlap:" in out
        assert '"Garden" (Garden Circle)' in out
        assert "[dry-run] Nothing changed." in out
        assert zoom.writes == []

    def test_choosing_current_changes_nothing(self, capsys):
        zoom = _MovingZoom({"7": room(7, "Tea Club Meeting Room")})
        mz.run(zoom, _Settings(), _group(), zoom.meetings["7"], _meetings(), None, False,
               _answers("1"))
        assert "No change" in capsys.readouterr().out
        assert zoom.writes == []

    def test_dry_run_with_user_moves_nothing(self, capsys):
        zoom = _MovingZoom({"7": room(7, "Tea Club Meeting Room")})
        mz.run(zoom, _Settings(), _group(), zoom.meetings["7"], _meetings(), 2, True, _answers())
        assert "[dry-run] Would move" in capsys.readouterr().out
        assert zoom.writes == []

    def test_moves_and_warns_about_host_key(self, capsys):
        zoom = _MovingZoom({"7": room(7, "Tea Club Meeting Room")})
        settings = _Settings()
        mz.run(zoom, settings, _group(), dict(zoom.meetings["7"]), _meetings(), None, False,
               _answers("2"))
        out = capsys.readouterr().out
        assert zoom.writes == [("move", "7", SECOND)]
        assert zoom.meetings["7"]["host_email"] == SECOND
        assert "link and passcode are unchanged" in out
        assert "WARNING: facilitators claim host with the host key" in out
        assert f"{SECOND}'s host key." in out
        assert settings.updated is None

    def test_community_user_warning_names_the_host_key_doc(self, capsys):
        zoom = _MovingZoom({"7": room(7, "Tea Club Meeting Room")})
        mz.run(zoom, _Settings(), _group(), dict(zoom.meetings["7"]), _meetings(), 3, False,
               _answers())
        assert "Community Events Zoom Host Key doc" in capsys.readouterr().out

    def test_changed_link_written_to_footer(self):
        zoom = _MovingZoom({"7": room(7, "Tea Club Meeting Room")},
                           new_link="https://zoom.us/j/7?pwd=NEW")
        settings = _Settings()
        mz.run(zoom, settings, _group(), dict(zoom.meetings["7"]), _meetings(), 2, False,
               _answers())
        footer = settings.updated["customFooterText"]
        assert parse_footer_zoom_url(footer) == "https://zoom.us/j/7?pwd=NEW"
        assert footer.startswith("Notes")

    def test_refused_move_waits_for_manual_move(self, capsys):
        zoom = _MovingZoom({"7": room(7, "Tea Club Meeting Room")}, refuse=True)

        def answer(prompt):
            # The first Enter comes too soon; then it's moved in the portal.
            if answer.calls:
                zoom._move("7", SECOND)
            answer.calls += 1
            return ""
        answer.calls = 0

        mz.run(zoom, _Settings(), _group(), dict(zoom.meetings["7"]), _meetings(), 2, False,
               answer)
        out = capsys.readouterr().out
        assert "Zoom refused to move the room" in out and "1110" in out
        assert "Schedule Privilege" in out and "Schedule For" in out
        assert f"It's still under {ROOMS_USER}." in out
        assert "Moved Tea Club's Zoom room" in out

    def test_ignored_move_can_be_stopped(self):
        zoom = _MovingZoom({"7": room(7, "Tea Club Meeting Room")}, ignore=True)
        with pytest.raises(SystemExit, match="still under"):
            mz.run(zoom, _Settings(), _group(), dict(zoom.meetings["7"]), _meetings(), 2, False,
                   _answers("q"))
