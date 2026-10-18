"""
Tests for util.meeting_rooms: finding, choosing, or creating a group's
calendar and Zoom room, and keeping them named, shared, and configured.

All names, IDs, and addresses are fictional.
"""

import datetime

import pytest

import util.meeting_rooms as mr
from tests.fakes_meeting_rooms import OWNER, FakeCalendarService, FakeZoom, room
from util.meeting_rooms import (
    LinkedResources,
    RoomLink,
    QuitRequested,
    check_calendar_owner,
    ensure_all_events_calendar,
    ensure_group_calendar,
    ensure_zoom_room,
    list_owned_calendars,
)

GROUP = "tech-circle@berkeleymoshav.org"
WRITER_RULE = {"id": "r1", "role": "writer", "scope": {"type": "group", "value": GROUP}}
NOW = datetime.datetime(2026, 10, 17, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def quiet_log(monkeypatch):
    logged = []
    monkeypatch.setattr(mr, "log", lambda level, action, detail="", error="":
                        logged.append((level, action, detail)))
    return logged


def _inputs(*answers):
    it = iter(answers)
    return lambda prompt: next(it)


def _no_input(prompt):
    raise AssertionError(f"unexpected prompt: {prompt}")


def _calendar(svc, linked_id=None, dry_run=False, input_fn=_no_input, linked=None,
              name="Technology Circle"):
    linked = linked or LinkedResources()
    return ensure_group_calendar(
        svc, name, GROUP, linked_id,
        owned_calendars=lambda: list_owned_calendars(svc), linked=lambda: linked,
        dry_run=dry_run, input_fn=input_fn,
    )


class TestCheckCalendarOwner:
    def test_owner_ok(self):
        check_calendar_owner(FakeCalendarService())

    def test_other_account_exits(self):
        with pytest.raises(SystemExit) as exc:
            check_calendar_owner(FakeCalendarService(primary="someone@berkeleymoshav.org"))
        assert OWNER in str(exc.value)


class TestEnsureGroupCalendar:
    def test_uses_footer_calendar_without_prompting(self):
        svc = FakeCalendarService({"c1": "Technology Circle"}, {"c1": [WRITER_RULE]})
        assert _calendar(svc, "c1") == "c1"
        assert svc.writes == []

    def test_renames_and_shares_footer_calendar(self):
        svc = FakeCalendarService({"c1": "Tech Circle"})
        assert _calendar(svc, "c1") == "c1"
        assert ("rename", "c1", "Technology Circle") in svc.writes
        assert ("acl_insert", "c1",
                {"role": "writer", "scope": {"type": "group", "value": GROUP}}, True) in svc.writes

    def test_upgrades_reader_access_without_notifying(self):
        reader = {**WRITER_RULE, "role": "reader"}
        svc = FakeCalendarService({"c1": "Technology Circle"}, {"c1": [reader]})
        _calendar(svc, "c1")
        assert svc.writes == [("acl_patch", "c1", "r1", "writer")]

    def test_owner_access_left_alone(self):
        owner = {**WRITER_RULE, "role": "owner"}
        svc = FakeCalendarService({"c1": "Technology Circle"}, {"c1": [owner]})
        _calendar(svc, "c1")
        assert svc.writes == []

    def test_creates_when_nothing_matches(self):
        svc = FakeCalendarService({"other": "Garden Club"})
        cid = _calendar(svc)
        assert svc.calendars_by_id[cid] == "Technology Circle"
        assert svc.writes[0] == ("create", {"summary": "Technology Circle",
                                            "timeZone": "America/Los_Angeles"})

    def test_prompts_even_for_one_match_and_uses_choice(self):
        svc = FakeCalendarService({"c1": "Technology Circle"}, {"c1": [WRITER_RULE]})
        assert _calendar(svc, input_fn=_inputs("1")) == "c1"
        assert svc.writes == []

    def test_zero_creates_new_instead(self):
        svc = FakeCalendarService({"c1": "Technology Circle"})
        cid = _calendar(svc, input_fn=_inputs("0"))
        assert cid != "c1" and svc.writes[0][0] == "create"

    def test_missing_footer_calendar_falls_back_to_choice(self):
        svc = FakeCalendarService({"c2": "Technology Circle"}, {"c2": [WRITER_RULE]})
        assert _calendar(svc, "gone", input_fn=_inputs("1")) == "c2"

    def test_invalid_choice_reprompts(self):
        svc = FakeCalendarService({"c1": "Technology Circle"}, {"c1": [WRITER_RULE]})
        assert _calendar(svc, input_fn=_inputs("7", "x", "1")) == "c1"

    def test_quit(self):
        svc = FakeCalendarService({"c1": "Technology Circle"})
        with pytest.raises(QuitRequested):
            _calendar(svc, input_fn=_inputs("q"))

    def test_calendar_linked_to_another_group_not_offered(self):
        svc = FakeCalendarService({"c1": "Technology Circle"})
        linked = LinkedResources(calendars={"c1": "other@berkeleymoshav.org"})
        cid = _calendar(svc, linked=linked)  # no prompt: the only match is taken
        assert cid != "c1"

    def test_choice_recorded_as_taken(self):
        svc = FakeCalendarService({"c1": "Technology Circle"}, {"c1": [WRITER_RULE]})
        linked = LinkedResources()
        _calendar(svc, input_fn=_inputs("1"), linked=linked)
        assert linked.calendars == {"c1": GROUP}

    def test_all_events_calendar_never_offered(self):
        svc = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
        # "Events Circle" would otherwise match it by shared term.
        cid = _calendar(svc, name="Events Circle")
        assert cid != "ae"

    def test_dry_run_creates_nothing(self, capsys):
        svc = FakeCalendarService({"c1": "Technology Circle"})
        assert _calendar(svc, dry_run=True) is None
        assert svc.writes == []
        out = capsys.readouterr().out
        assert ("would ask whether one of these is the calendar for 'Technology Circle': "
                "'Technology Circle'") in out
        assert "would create calendar 'Technology Circle'" in out
        assert "unless one of those is chosen" in out

    def test_dry_run_reports_fixes_to_footer_calendar(self, capsys):
        svc = FakeCalendarService({"c1": "Tech Circle"})
        assert _calendar(svc, "c1", dry_run=True) == "c1"
        assert svc.writes == []
        out = capsys.readouterr().out
        assert "would rename calendar" in out and "would share with" in out


class TestEnsureAllEventsCalendar:
    def test_creates_unshared(self, monkeypatch, capsys):
        monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "")
        svc = FakeCalendarService()
        cid = ensure_all_events_calendar(
            svc, owned_calendars=lambda: list_owned_calendars(svc), dry_run=False)
        assert svc.calendars_by_id[cid] == "All Berkeley Moshav Events"
        assert not any(w[0].startswith("acl") for w in svc.writes)
        assert "Set ALL_EVENTS_CALENDAR_ID" in capsys.readouterr().out

    def test_dry_run_question_names_the_calendar(self, monkeypatch, capsys):
        monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "")
        svc = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
        assert ensure_all_events_calendar(
            svc, owned_calendars=lambda: list_owned_calendars(svc), dry_run=True) is None
        assert ("would ask whether one of these is the 'All Berkeley Moshav Events' "
                "calendar: 'All Berkeley Moshav Events'") in capsys.readouterr().out

    def test_configured_id_used(self, monkeypatch):
        monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "ae")
        svc = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
        assert ensure_all_events_calendar(
            svc, owned_calendars=lambda: [], dry_run=False) == "ae"
        assert svc.writes == []


def _room(zoom, linked_url=None, dry_run=False, input_fn=_no_input, linked=None,
          name="Technology Circle"):
    linked = linked or LinkedResources()
    return ensure_zoom_room(
        zoom, name, linked_url, GROUP, rooms=zoom.list_rooms, linked=lambda: linked,
        dry_run=dry_run, input_fn=input_fn, now=NOW,
    )


class TestEnsureZoomRoom:
    def test_uses_footer_room_without_listing(self):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room")},
                        last_used={"1": "2026-10-01T00:00:00Z"})
        assert _room(zoom, "https://zoom.us/j/1?pwd=p") == RoomLink(
            "https://zoom.us/j/1?pwd=p", "135790")
        assert zoom.writes == [] and zoom.list_calls == 0

    def test_fixes_topic_and_settings(self):
        m = room(1, "Tech Meeting Room")
        m["settings"]["waiting_room"] = True
        zoom = FakeZoom({1: m})
        _room(zoom, "https://zoom.us/j/1?pwd=p")
        assert zoom.writes == [("update", "1", {
            "topic": "Technology Circle Meeting Room", "settings": {"waiting_room": False}})]

    def test_expired_room_offers_candidates_then_creates(self, capsys):
        zoom = FakeZoom({})
        link = _room(zoom, "https://zoom.us/j/1?pwd=p")
        assert zoom.writes == [("create", "Technology Circle Meeting Room")]
        assert link.join_url.startswith("https://zoom.us/j/9") and link.passcode.isdigit()
        assert "no longer exists" in capsys.readouterr().out

    def test_prompts_for_matching_room(self):
        zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room")})
        assert _room(zoom, input_fn=_inputs("1")).join_url == "https://zoom.us/j/7?pwd=p"

    def test_non_numeric_passcode_replaced_and_new_link_returned(self):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room", password="aB3xK9")})
        link = _room(zoom, "https://zoom.us/j/1?pwd=p")
        (op, mid, body), = zoom.writes
        assert (op, mid, list(body)) == ("update", "1", ["password"])
        assert body["password"].isdigit()
        assert link == RoomLink("https://zoom.us/j/1?pwd=new", body["password"])

    def test_dry_run_reports_passcode_change(self, capsys):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room", password="aB3xK9")})
        assert _room(zoom, "https://zoom.us/j/1?pwd=p", dry_run=True).passcode == "aB3xK9"
        assert zoom.writes == []
        assert "new digits-only passcode" in capsys.readouterr().out

    def test_room_linked_elsewhere_not_offered(self):
        zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room")})
        linked = LinkedResources(rooms={"7": "other@berkeleymoshav.org"})
        _room(zoom, linked=linked)
        assert zoom.writes[0] == ("create", "Technology Circle Meeting Room")

    def test_dry_run_creates_nothing(self):
        zoom = FakeZoom({})
        assert _room(zoom, dry_run=True) is None
        assert zoom.writes == []

    def test_idle_room_warns(self, quiet_log):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room")},
                        last_used={"1": "2025-11-01T00:00:00Z"})
        _room(zoom, "https://zoom.us/j/1?pwd=p")
        assert any(a == "room_idle" for _, a, _ in quiet_log)

    def test_never_used_old_room_warns(self, quiet_log):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room",
                                 created_at="2025-10-01T00:00:00Z")})
        _room(zoom, "https://zoom.us/j/1?pwd=p")
        assert any(a == "room_idle" for _, a, _ in quiet_log)

    def test_recent_room_no_warning(self, quiet_log):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room")})
        _room(zoom, "https://zoom.us/j/1?pwd=p")
        assert not any(a == "room_idle" for _, a, _ in quiet_log)

    def test_room_owned_by_someone_else_warns(self, quiet_log):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room",
                                 host_email="someone@berkeleymoshav.org")})
        _room(zoom, "https://zoom.us/j/1?pwd=p")
        assert any(a == "room_host" for _, a, _ in quiet_log)
