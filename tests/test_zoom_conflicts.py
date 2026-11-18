"""
Tests for util/zoom_conflicts.py: collecting meetings, and finding which
would clash under each Zoom user.

All names, IDs, and addresses are fictional.
"""

import datetime

from tests.fakes_meeting_rooms import FakeCalendarService, FakeZoom, room
from util.zoom_conflicts import (
    Meeting,
    conflicts_under,
    describe_conflict,
    event_meetings,
    fixed_time_meetings,
    gather_meetings,
    merge_meetings,
    own_conflicts,
    zoom_link_ids,
)

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 18, 12, tzinfo=UTC)
ONE = "one@example.org"
TWO = "two@example.org"


def at(hour, minute=0, day=24):
    return datetime.datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def meeting(key, mid, owner, start, end, title="T", places=("Somewhere",)):
    return Meeting(key, mid, owner, start, end, title, places)


def event(uid, start, end, link_id="7", **kw):
    return {"iCalUID": uid, "summary": uid,
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
            "location": f"https://us06web.zoom.us/j/{link_id}?pwd=p", **kw}


OWNERS = {"7": (ONE, 3), "8": (TWO, 3), "50": (TWO, 2)}


class TestEventMeetings:
    def test_link_ids_in_order_each_once(self):
        e = {"location": "https://zoom.us/j/8", "description": "zoom.us/j/7 and zoom.us/j/8"}
        assert zoom_link_ids(e) == ["8", "7"]

    def test_room_event_keyed_by_event(self):
        [m] = event_meetings([event("e1", at(3), at(4))], "Tea Club", OWNERS)
        assert (m.meeting_id, m.owner, m.places) == ("7", ONE, ("Tea Club",))
        assert m.key.startswith("e1@")

    def test_fixed_time_meeting_keyed_by_zoom_id(self):
        [m] = event_meetings([event("e1", at(3), at(4), link_id="50")], "Tea Club", OWNERS)
        assert m.key.startswith("zoom:50@") and m.owner == TWO

    def test_first_meeting_in_our_account(self):
        e = event("e1", at(3), at(4), link_id="999",
                  description="Or https://zoom.us/j/8")
        [m] = event_meetings([e], "Tea Club", OWNERS)
        assert m.meeting_id == "8"

    def test_skips_all_day_cancelled_and_outside_links(self):
        events = [
            {"iCalUID": "a", "start": {"date": "2026-11-24"}, "end": {"date": "2026-11-25"},
             "location": "https://zoom.us/j/7"},
            event("b", at(3), at(4), status="cancelled"),
            event("c", at(3), at(4), link_id="999"),
        ]
        assert event_meetings(events, "Tea Club", OWNERS) == []


class TestFixedTimeMeetings:
    def test_one_off_and_occurrences(self):
        account = [
            {"id": 50, "type": 2, "topic": "Call", "host_email": TWO,
             "start_time": "2026-11-24T03:00:00Z", "duration": 30},
            {"id": 51, "type": 8, "topic": "Series", "host_email": ONE, "duration": 60},
            {"id": 52, "type": 3, "topic": "Room", "host_email": ONE},
        ]
        occurrences = {"51": {"occurrences": [
            {"start_time": "2026-11-25T03:00:00Z", "duration": 45},
            {"start_time": "2026-11-26T03:00:00Z", "status": "deleted"},
            {"start_time": "2027-03-01T03:00:00Z"},  # after the window
        ]}}
        got = fixed_time_meetings(account, occurrences.get, NOW, NOW + datetime.timedelta(weeks=8))
        assert [(m.meeting_id, m.end - m.start, m.places) for m in got] == [
            ("50", datetime.timedelta(minutes=30), ("scheduled in Zoom",)),
            ("51", datetime.timedelta(minutes=45), ("scheduled in Zoom",)),
        ]


class TestMerge:
    def test_same_key_merged_with_both_places(self):
        a = meeting("k", "7", ONE, at(3), at(4), places=("Tea Club",))
        b = meeting("k", "7", ONE, at(3), at(4), places=("Garden Circle",))
        [m] = merge_meetings([a, b])
        assert m.places == ("Tea Club", "Garden Circle")


class TestConflicts:
    ROOM = "7"

    def test_overlap_with_target_users_meeting(self):
        mine = meeting("a", "7", ONE, at(3), at(4))
        theirs = meeting("b", "8", TWO, at(3, 30), at(5))
        assert conflicts_under([mine, theirs], self.ROOM, TWO) == [(mine, theirs)]
        assert conflicts_under([mine, theirs], self.ROOM, ONE) == []

    def test_gap_under_15_minutes_clashes_15_does_not(self):
        mine = meeting("a", "7", ONE, at(3), at(4))
        close = meeting("b", "8", TWO, at(4, 14), at(5))
        clear = meeting("c", "8", TWO, at(4, 15), at(5))
        assert conflicts_under([mine, close, clear], self.ROOM, TWO) == [(mine, close)]

    def test_earlier_meeting_first(self):
        mine = meeting("a", "7", ONE, at(5), at(6))
        theirs = meeting("b", "8", TWO, at(4), at(5))
        assert conflicts_under([mine, theirs], self.ROOM, TWO) == [(theirs, mine)]

    def test_rooms_own_meetings_not_counted_against_current_user(self):
        a = meeting("a", "7", ONE, at(3), at(4))
        b = meeting("b", "7", ONE, at(3, 30), at(5))
        assert conflicts_under([a, b], self.ROOM, ONE) == []
        assert own_conflicts([a, b], self.ROOM) == [(a, b)]

    def test_user_compared_case_insensitively(self):
        mine = meeting("a", "7", ONE, at(3), at(4))
        theirs = meeting("b", "8", TWO, at(3), at(4))
        assert conflicts_under([mine, theirs], self.ROOM, TWO.upper())


class TestDescribe:
    def test_overlap(self):
        a = meeting("a", "7", ONE, at(3), at(4), title="Tea", places=("Tea Club",))
        b = meeting("b", "8", TWO, at(3, 30), at(5), title="Call", places=("scheduled in Zoom",))
        what, line_a, line_b = describe_conflict(a, b)
        assert what == "overlap:"
        # 03:00 UTC on Nov 24 is 7:00 PM on Nov 23 in California.
        assert line_a == '  "Tea" (Tea Club), Mon Nov 23, 7:00 PM–8:00 PM'
        assert line_b.startswith('  "Call" (scheduled in Zoom), ')

    def test_apart_and_back_to_back(self):
        a = meeting("a", "7", ONE, at(3), at(4))
        assert describe_conflict(a, meeting("b", "8", TWO, at(4), at(5)))[0] == "back to back:"
        assert describe_conflict(a, meeting("b", "8", TWO, at(4, 1), at(5)))[0] \
            == "only 1 minute apart:"
        assert describe_conflict(a, meeting("b", "8", TWO, at(4, 10), at(5)))[0] \
            == "only 10 minutes apart:"


class TestGather:
    def test_events_and_zoom_meetings_together(self):
        zoom = FakeZoom({
            "7": room(7, "Tea Club Meeting Room", host_email=ONE),
            "50": {"id": 50, "type": 2, "topic": "Call", "host_email": ONE,
                   "start_time": at(3, 30).isoformat(), "duration": 60},
        })
        calendar = FakeCalendarService(events={
            "tea-cal": [event("e1", at(3), at(4))],
            "garden-cal": [event("e1", at(3), at(4))],  # the same event, invited
        })
        groups = [{"name": "Tea Club", "calendar_id": "tea-cal"},
                  {"name": "Garden Circle", "calendar_id": "garden-cal"},
                  {"name": "No Calendar", "calendar_id": None}]
        got = gather_meetings(calendar, zoom, groups, NOW)
        assert [(m.meeting_id, m.places) for m in got] == [
            ("7", ("Tea Club", "Garden Circle")), ("50", ("scheduled in Zoom",))]
