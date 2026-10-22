"""
Tests for util/calendar_event_moves.py: finding a group's events on other
calendars, and copying or deleting them.

All names, IDs, and addresses are fictional.
"""

import datetime

import pytest

import update_groups_in_google_and_hierarchy as m
import util.calendar_event_moves as cem
import util.meeting_rooms as mr
from tests.fakes_meeting_rooms import OWNER, FakeCalendarService, FakeZoom
from util.calendar_event_moves import (
    EventMover,
    copy_bodies,
    describe_rrule,
    gather_candidates,
    other_calendars,
    strip_meeting_links,
)
from util.meeting_rooms import LinkedResources, QuitRequested

NOW = datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone.utc)
GROUP_CAL = "tech@group.calendar.google.com"
SHARED = "shared@group.calendar.google.com"
VIEW_ONLY = "member@gmail.com"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "ae")
    monkeypatch.setattr(cem, "ALL_EVENTS_CALENDAR_ID", "ae")
    monkeypatch.setattr(cem, "log", lambda *a, **k: None)
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)
    monkeypatch.setattr(m, "log", lambda *a, **k: None)


class _Answers:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def _event(eid, summary, calendar_id, start="2026-11-03T03:00:00Z", **kw):
    return {"id": eid, "iCalUID": f"{eid}@google.com", "summary": summary, "status": "confirmed",
            "organizer": {"email": calendar_id, "self": True},
            "start": {"dateTime": start, "timeZone": "America/Los_Angeles"},
            "end": {"dateTime": start.replace("03:00", "04:00"),
                    "timeZone": "America/Los_Angeles"}, **kw}


def _service(events=None, calendars=None):
    return FakeCalendarService(
        calendars or {"ae": "All Berkeley Moshav Events", GROUP_CAL: "Technology Circle",
                      SHARED: "Shared Events"},
        events=events, subscribed={VIEW_ONLY: ("Member's calendar", "reader")})


def _mover(svc, answers, dry_run=False):
    return EventMover(svc, lambda: {GROUP_CAL}, dry_run=dry_run, input_fn=answers, now=NOW)


class TestCalendars:
    def test_other_calendars(self):
        svc = _service()
        svc.subscribed["en.usa#holiday@group.v.calendar.google.com"] = ("Holidays", "reader")
        svc.subscribed["busy@x.org"] = ("Busy", "freeBusyReader")
        calendars = other_calendars(svc, {GROUP_CAL})
        assert [(c["id"], c["role"], c["primary"]) for c in calendars] == [
            (OWNER, "owner", True), (SHARED, "owner", False), (VIEW_ONLY, "reader", False)]


class TestCandidates:
    def test_one_per_event_preferring_the_organizers_copy(self):
        organized = _event("e1", "Technology Circle meeting", SHARED)
        invitation = {**organized, "organizer": {"email": SHARED}}
        calendars = other_calendars(_service(), {GROUP_CAL})
        (c,) = gather_candidates({OWNER: [invitation], SHARED: [organized]}, calendars,
                                 {GROUP_CAL})
        assert c["calendar"]["id"] == SHARED and c["organized_here"]
        assert c["also_on"] == [OWNER]

    def test_events_organized_by_a_group_calendar_left_out(self):
        e = _event("e1", "Technology Circle meeting", GROUP_CAL)
        e["organizer"] = {"email": GROUP_CAL}
        calendars = other_calendars(_service(), {GROUP_CAL})
        assert gather_candidates({OWNER: [e]}, calendars, {GROUP_CAL}) == []

    def test_series_with_changed_and_cancelled_occurrences(self):
        series = _event("s1", "Technology Circle", SHARED,
                        recurrence=["RRULE:FREQ=MONTHLY;BYDAY=3MO"])
        moved = {**_event("s1_x", "Technology Circle (moved)", SHARED,
                          start="2026-12-22T03:00:00Z"),
                 "recurringEventId": "s1",
                 "originalStartTime": {"dateTime": "2026-12-15T03:00:00Z"}}
        cancelled = {"id": "s1_y", "status": "cancelled", "recurringEventId": "s1",
                     "originalStartTime": {"dateTime": "2027-01-19T03:00:00Z"}}
        calendars = other_calendars(_service(), {GROUP_CAL})
        (c,) = gather_candidates({SHARED: [series, moved, cancelled]}, calendars, {GROUP_CAL})
        main, one_off = copy_bodies(c)
        assert main["recurrence"] == ["RRULE:FREQ=MONTHLY;BYDAY=3MO",
                                      "EXDATE:20261215T030000Z", "EXDATE:20270119T030000Z"]
        assert one_off["summary"] == "Technology Circle (moved)"
        assert "recurrence" not in one_off
        assert main["extendedProperties"] == one_off["extendedProperties"] == {
            "private": {"copiedFrom": "s1@google.com"}}


class TestDescriptions:
    def test_describe_rrule(self):
        assert describe_rrule(["RRULE:FREQ=WEEKLY;BYDAY=TU"]) == "weekly on Tue"
        assert describe_rrule(["RRULE:FREQ=MONTHLY;BYDAY=3MO"]) == "monthly on the 3rd Mon"
        assert describe_rrule(["RRULE:FREQ=MONTHLY;BYDAY=-1TH"]) == "monthly on the last Thu"
        assert describe_rrule(["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE"]) == \
            "every 2 weeks on Mon, Wed"
        assert describe_rrule(["RRULE:FREQ=MONTHLY;BYMONTHDAY=5"]) == "monthly on day 5"
        assert describe_rrule(["EXDATE:20261215T030000Z"]) == "repeats"

    def test_when(self):
        w = cem._when
        assert w({"dateTime": "2026-11-03T03:00:00Z"}, {"dateTime": "2026-11-03T04:30:00Z"}) \
            == "Mon Nov 2, 7:00–8:30 PM"
        assert w({"dateTime": "2026-11-02T19:00:00Z"}, {"dateTime": "2026-11-02T21:00:00Z"}) \
            == "Mon Nov 2, 11:00 AM–1:00 PM"
        assert w({"dateTime": "2026-11-03T06:00:00Z"}, {"dateTime": "2026-11-03T09:00:00Z"}) \
            == "Mon Nov 2, 10:00 PM – Tue Nov 3, 1:00 AM"
        assert w({"date": "2026-11-02"}, {"date": "2026-11-03"}) == "Mon Nov 2, all day"
        assert w({"date": "2026-11-02"}, {"date": "2026-11-05"}) == \
            "Mon Nov 2 – Wed Nov 4, all day"

    def test_strip_meeting_links(self):
        text = ("Agenda in the folder.\n\nJoin Zoom Meeting\nhttps://us06web.zoom.us/j/123?pwd=x"
                "\n\nMeeting ID: 123\nPasscode: 456\nOne tap mobile\n+16699006833,,123# US")
        assert strip_meeting_links(text) == ("Agenda in the folder.", True)
        assert strip_meeting_links("Notes<br>https://meet.google.com/abc-defg-hij<br>Bye") == \
            ("Notes<br>Bye", True)
        assert strip_meeting_links("No links here") == ("No links here", False)
        assert strip_meeting_links(None) == ("", False)

    def test_copy_leaves_out_guests_and_links(self):
        e = _event("e1", "Technology Circle", SHARED,
                   attendees=[{"email": "a@x.org"}], location="https://zoom.us/j/123",
                   description="Hi\nhttps://zoom.us/j/123")
        calendars = other_calendars(_service(), {GROUP_CAL})
        (c,) = gather_candidates({SHARED: [e]}, calendars, {GROUP_CAL})
        (body,) = copy_bodies(c)
        assert "attendees" not in body
        assert (body["description"], body["location"]) == ("Hi", "")


class TestOffer:
    def test_lists_then_copies_and_deletes_after_confirming(self, capsys):
        e = _event("e1", "Technology Circle meeting", SHARED)
        svc = _service({SHARED: [e]})
        answers = _Answers("b", "delete")
        _mover(svc, answers).offer("Technology Circle", GROUP_CAL)
        out = capsys.readouterr().out
        assert ("1. Technology Circle meeting   (Mon Nov 2, 7:00–8:00 PM; on Shared Events)"
                in out)
        assert "cancelled for everyone invited, with Google's cancellation emails" in out
        assert answers.prompts[0] == (
            "  1. Technology Circle meeting — [c]opy to the group's calendar, [d]elete, "
            "[b]oth, [s]kip, or [q]uit: ")
        (insert, delete) = svc.writes
        assert insert[:2] == ("insert_event", GROUP_CAL) and insert[3] == "none"
        assert delete == ("delete_event", SHARED, "e1", "all")

    def test_series_copy_starts_at_next_occurrence(self, capsys):
        series = _event("s1", "Technology Circle", SHARED, start="2023-01-17T03:00:00Z",
                        recurrence=["RRULE:FREQ=MONTHLY;BYDAY=3MO"])
        series["end"]["dateTime"] = "2023-01-17T04:30:00Z"
        past = {"originalStartTime": {"dateTime": "2026-09-22T02:00:00Z"},
                "start": {"dateTime": "2026-09-22T02:00:00Z"}}
        nxt = {"originalStartTime": {"dateTime": "2026-11-17T03:00:00Z"},
               "start": {"dateTime": "2026-11-17T03:00:00Z"}}
        svc = _service({SHARED: [series]})
        svc.instances_by_series[(SHARED, "s1")] = [past, nxt]
        _mover(svc, _Answers("c")).offer("Technology Circle", GROUP_CAL)
        assert ("(monthly on the 3rd Mon, 7:00–8:30 PM, from Mon Jan 16, 2023 "
                "(a copy would start Mon Nov 16, 2026); on Shared Events)"
                in capsys.readouterr().out)
        (_, _, body, _), = svc.writes
        assert body["start"] == {"dateTime": "2026-11-17T03:00:00+00:00",
                                 "timeZone": "America/Los_Angeles"}
        assert body["end"]["dateTime"] == "2026-11-17T04:30:00+00:00"
        assert body["recurrence"] == ["RRULE:FREQ=MONTHLY;BYDAY=3MO"]

    def test_ended_series_left_out(self, capsys):
        ended = _event("s1", "Technology Circle", SHARED, start="2025-08-19T03:00:00Z",
                       recurrence=["RRULE:FREQ=MONTHLY;UNTIL=20260601T000000Z;BYDAY=3MO"])
        svc = _service({SHARED: [ended]})
        svc.instances_by_series[(SHARED, "s1")] = [
            {"originalStartTime": {"dateTime": "2026-05-19T03:00:00Z"},
             "start": {"dateTime": "2026-05-19T03:00:00Z"}}]
        _mover(svc, _no_input).offer("Technology Circle", GROUP_CAL)
        assert "No events on other calendars match" in capsys.readouterr().out

    def test_series_ending_after_a_count_ends_on_its_last_occurrence(self):
        series = _event("s1", "Technology Circle", SHARED, start="2026-09-01T03:00:00Z",
                        recurrence=["RRULE:FREQ=WEEKLY;COUNT=10;BYDAY=MO"])
        slots = [{"originalStartTime": {"dateTime": f"2026-{d}T03:00:00Z"},
                  "start": {"dateTime": f"2026-{d}T03:00:00Z"}}
                 for d in ("09-01", "10-27", "11-03")]
        svc = _service({SHARED: [series]})
        svc.instances_by_series[(SHARED, "s1")] = slots
        _mover(svc, _Answers("c")).offer("Technology Circle", GROUP_CAL)
        (_, _, body, _), = svc.writes
        assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261103T030000Z"]
        assert body["start"]["dateTime"] == "2026-10-27T03:00:00+00:00"

    def test_view_only_calendar_offers_copy_or_skip(self, capsys):
        e = _event("e1", "Technology Circle meeting", VIEW_ONLY)
        answers = _Answers("d", "c")
        svc = _service({VIEW_ONLY: [e]})
        _mover(svc, answers).offer("Technology Circle", GROUP_CAL)
        assert answers.prompts[0].endswith("[c]opy to the group's calendar, [s]kip, "
                                           "or [q]uit: ")
        assert "can't be deleted (view-only calendar)" in capsys.readouterr().out
        assert [w[0] for w in svc.writes] == ["insert_event"]

    def test_invitation_copy_deleted_only_from_that_calendar(self, capsys):
        e = {**_event("e1", "Technology Circle meeting", OWNER),
             "organizer": {"email": "someone@gmail.com"}}
        svc = _service({OWNER: [e]})
        _mover(svc, _Answers("d", "delete")).offer("Technology Circle", GROUP_CAL)
        out = capsys.readouterr().out
        assert "an invitation from someone@gmail.com" in out
        assert "removed from admin@berkeleymoshav.org only; the organizer's event stays" in out

    def test_not_confirming_asks_again(self):
        svc = _service({SHARED: [_event("e1", "Technology Circle meeting", SHARED)]})
        answers = _Answers("d", "no", "s")
        _mover(svc, answers).offer("Technology Circle", GROUP_CAL)
        assert svc.writes == [] and len(answers.prompts) == 3

    def test_quit_changes_nothing(self):
        svc = _service({SHARED: [_event("e1", "Technology Circle meeting", SHARED)]})
        with pytest.raises(QuitRequested):
            _mover(svc, _Answers("q")).offer("Technology Circle", GROUP_CAL)
        assert svc.writes == []

    def test_dry_run_asks_but_changes_nothing(self, capsys):
        svc = _service({SHARED: [_event("e1", "Technology Circle meeting", SHARED)]})
        answers = _Answers("b")
        _mover(svc, answers, dry_run=True).offer("Technology Circle", None)
        assert svc.writes == []
        assert answers.prompts[0].startswith("  [dry-run] 1. Technology Circle meeting")
        assert answers.prompts[0].endswith("(DRY-RUN: NOTHING WILL BE COPIED OR DELETED): ")
        assert "Would copy to the 'Technology Circle' calendar:" in capsys.readouterr().out

    def test_dry_run_view_only_note_mentions_copying_only(self):
        svc = _service({VIEW_ONLY: [_event("e1", "Technology Circle meeting", VIEW_ONLY)]})
        answers = _Answers("s")
        _mover(svc, answers, dry_run=True).offer("Technology Circle", None)
        assert answers.prompts[0].endswith(
            "[c]opy to the group's calendar, [s]kip, or [q]uit "
            "(DRY-RUN: NOTHING WILL BE COPIED): ")

    def test_already_copied_not_offered(self, capsys):
        e = _event("e1", "Technology Circle meeting", SHARED)
        copy = {"id": "c1", "summary": e["summary"],
                "extendedProperties": {"private": {"copiedFrom": "e1@google.com"}}}
        svc = _service({SHARED: [e], GROUP_CAL: [copy]})
        _mover(svc, _no_input).offer("Technology Circle", GROUP_CAL)
        assert "No events on other calendars match 'Technology Circle'." in \
            capsys.readouterr().out

    def test_offered_once_per_run(self):
        e = _event("e1", "Technology Circle meeting", SHARED)
        svc = _service({SHARED: [e]})
        mover = _mover(svc, _Answers("c"))
        mover.offer("Technology Circle", GROUP_CAL)
        mover.offer("Technology Circle", GROUP_CAL)  # nothing left to ask about
        assert [w[0] for w in svc.writes] == ["insert_event"]

    def test_unreadable_calendar_skipped(self, capsys):
        svc = _service({SHARED: [_event("e1", "Technology Circle meeting", SHARED)]})
        svc.subscribed["gone@x.org"] = ("Gone", "reader")
        real_list = svc.events().list

        def list_events(calendarId, **kw):
            if calendarId == "gone@x.org":
                from tests.fakes_meeting_rooms import _http_error
                raise _http_error(404)
            return real_list(calendarId, **kw)
        svc.events = lambda: type("E", (), {"list": staticmethod(list_events)})()
        _mover(svc, _Answers("s")).offer("Technology Circle", GROUP_CAL)
        assert "1. Technology Circle meeting" in capsys.readouterr().out

    def test_event_titled_with_acronym_offered(self):
        svc = _service({SHARED: [_event("e1", "DF&L budget review", SHARED)]})
        answers = _Answers("s")
        _mover(svc, answers).offer("Development, Finance, & Legal Circle", GROUP_CAL)
        assert answers.prompts[0].startswith("  1. DF&L budget review — ")

    def test_unrelated_events_not_offered(self, capsys):
        svc = _service({SHARED: [_event("e1", "Book Club", SHARED)]})
        _mover(svc, _no_input).offer("Technology Circle", GROUP_CAL)
        assert "No events" in capsys.readouterr().out


def _no_input(prompt):
    raise AssertionError(f"unexpected prompt: {prompt}")


class TestUpdateGroupsStep:
    GROUPS = {"1": {"name": "Technology Circle", "kind": "circle", "availability": "closed"}}

    def _run(self, svc, move_events, monkeypatch, answers):
        monkeypatch.setattr("builtins.input", answers)
        from tests.test_group_meeting_rooms_step import _Directory, _Settings
        m.ensure_group_meeting_rooms(
            _Directory(), _Settings({}), svc, FakeZoom(), self.GROUPS, {"1": "F1"},
            {"1": "tech@x.org"}, lambda: LinkedResources(), False, {}, {},
            move_events=move_events,
        )

    def test_offered_when_calendar_created(self, monkeypatch):
        svc = FakeCalendarService(
            {"ae": "All Berkeley Moshav Events", SHARED: "Shared Events"},
            events={SHARED: [_event("e1", "Technology Circle meeting", SHARED,
                                    start="2027-03-01T03:00:00Z")]})
        answers = _Answers("c")
        self._run(svc, False, monkeypatch, answers)
        (insert,) = [w for w in svc.writes if w[0] == "insert_event"]
        assert insert[1].startswith("new") and len(answers.prompts) == 1
