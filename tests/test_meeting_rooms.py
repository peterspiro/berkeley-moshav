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
    describe_schedule,
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

    def test_renames_and_shares_footer_calendar_without_googles_email(self):
        svc = FakeCalendarService({"c1": "Tech Circle"})
        assert _calendar(svc, "c1") == "c1"
        assert ("rename", "c1", "Technology Circle") in svc.writes
        assert ("acl_insert", "c1",
                {"role": "writer", "scope": {"type": "group", "value": GROUP}}, False) in svc.writes

    @pytest.mark.parametrize("role", ["reader", "freeBusyReader"])
    def test_restricted_access_left_alone(self, role):
        # A circle restricted by hand: the group only views; some members edit.
        rules = [{**WRITER_RULE, "role": role},
                 {"id": "r2", "role": "writer", "scope": {"type": "user", "value": "dana@gmail.com"}}]
        svc = FakeCalendarService({"c1": "Technology Circle"}, {"c1": rules})
        assert _calendar(svc, "c1") == "c1"
        assert svc.writes == []

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

    def test_creates_with_calendar_name(self):
        svc = FakeCalendarService({})
        cid = ensure_group_calendar(
            svc, "Technology", GROUP, None, owned_calendars=lambda: list_owned_calendars(svc),
            linked=LinkedResources, dry_run=False, input_fn=_no_input,
            calendar_name="Technology Circle")
        assert svc.calendars_by_id[cid] == "Technology Circle"

    def test_other_names_not_renamed(self):
        svc = FakeCalendarService({"c1": "Technology Circle"}, {"c1": [WRITER_RULE]})
        ensure_group_calendar(
            svc, "Technology", GROUP, "c1", owned_calendars=lambda: list_owned_calendars(svc),
            linked=LinkedResources, dry_run=False, other_names=("Technology Circle",))
        assert svc.writes == []

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

    def test_shared_quietly_with_reader_groups(self, monkeypatch):
        monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "ae")
        svc = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
        ensure_all_events_calendar(svc, owned_calendars=lambda: [], dry_run=False,
                                   reader_groups=["all@x.org"])
        assert svc.writes == [("acl_insert", "ae", {
            "role": "reader", "scope": {"type": "group", "value": "all@x.org"}}, False)]
        # Already shared: nothing more, and any existing access is kept.
        ensure_all_events_calendar(svc, owned_calendars=lambda: [], dry_run=False,
                                   reader_groups=["all@x.org"])
        assert len(svc.writes) == 1

    def test_reader_groups_dry_run(self, monkeypatch, capsys):
        monkeypatch.setattr(mr, "ALL_EVENTS_CALENDAR_ID", "ae")
        svc = FakeCalendarService({"ae": "All Berkeley Moshav Events"})
        ensure_all_events_calendar(svc, owned_calendars=lambda: [], dry_run=True,
                                   reader_groups=["all@x.org"])
        assert svc.writes == []
        assert "would share 'All Berkeley Moshav Events' with all@x.org (view only, no email)" \
            in capsys.readouterr().out

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
        zoom, name, linked_url, GROUP, meetings=zoom.list_account_meetings, linked=lambda: linked,
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
        assert _room(zoom, input_fn=_inputs("u")).join_url == "https://zoom.us/j/7?pwd=p"

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

    def test_room_moved_to_second_zoom_user_does_not_warn(self, quiet_log):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room",
                                 host_email="berkeley@urbanmoshav.org")})
        _room(zoom, "https://zoom.us/j/1?pwd=p")
        assert not any(a == "room_host" for _, a, _ in quiet_log)

    def test_second_zoom_users_room_can_be_used(self):
        zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room",
                                 host_email="berkeley@urbanmoshav.org")})
        assert _room(zoom, input_fn=_inputs("u")).join_url == "https://zoom.us/j/7?pwd=p"

    def test_room_owned_by_someone_else_warns(self, quiet_log):
        zoom = FakeZoom({1: room(1, "Technology Circle Meeting Room",
                                 host_email="someone@berkeleymoshav.org")})
        _room(zoom, "https://zoom.us/j/1?pwd=p")
        assert any(a == "room_host" for _, a, _ in quiet_log)


class _Answers:
    """Answers prompts in order, recording them."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def _weekly(mid, topic, host="zoomhosts@berkeleymoshav.org"):
    return room(mid, topic, type=8, host_email=host,
                recurrence={"type": 2, "repeat_interval": 1, "weekly_days": "3"},
                occurrences=[{"start_time": "2026-10-14T02:00:00Z", "status": "available"},
                             {"start_time": "2026-10-21T02:00:00Z", "status": "available"}])


class TestMatchingMeetings:
    def test_lists_all_before_asking(self, capsys):
        zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room"),
                         8: _weekly(8, "Technology Circle weekly")})
        answers = _Answers("u", "i")
        _room(zoom, input_fn=answers)
        out = capsys.readouterr().out
        assert "1. Technology Circle Meeting Room   (7, zoomhosts@berkeleymoshav.org, " \
               "no fixed time)" in out
        assert "2. Technology Circle weekly   (8, zoomhosts@berkeleymoshav.org, " \
               "weekly on Tue, 7:00 PM, next Tue Oct 20)" in out
        assert answers.prompts[0] == \
            "  1. Technology Circle Meeting Room — [u]se, [i]gnore, [d]elete, or [q]uit: "
        assert out.index("2. Technology Circle weekly") < out.index("Will use 7 as the room")

    def test_fixed_time_or_other_owner_cannot_be_used(self):
        zoom = FakeZoom({8: _weekly(8, "Technology Circle weekly"),
                         9: room(9, "Technology Circle Meeting Room", host_email="b@x.org")})
        answers = _Answers("u", "i", "i", "i")
        _room(zoom, input_fn=answers)
        assert all("[u]se" not in p for p in answers.prompts)
        assert answers.prompts[1] == answers.prompts[0]  # "u" was invalid
        assert zoom.writes == [("create", "Technology Circle Meeting Room")]

    def test_delete_after_confirming_then_create(self):
        zoom = FakeZoom({8: _weekly(8, "Technology Circle weekly")})
        answers = _Answers("d", "delete")
        link = _room(zoom, input_fn=answers)
        assert zoom.writes == [("delete", "8"), ("create", "Technology Circle Meeting Room")]
        assert link.join_url.startswith("https://zoom.us/j/9")

    def test_delete_and_use_another(self, capsys):
        zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room"),
                         8: _weekly(8, "Technology Circle weekly")})
        link = _room(zoom, input_fn=_Answers("u", "d", "delete"))
        assert zoom.writes == [("delete", "8")]
        assert link.join_url == "https://zoom.us/j/7?pwd=p"
        out = capsys.readouterr().out
        assert "Will use 7 as the room, and delete:" in out
        assert "8  Technology Circle weekly (weekly on Tue, 7:00 PM, next Tue Oct 20; " \
               "its link will stop working)" in out

    def test_not_confirming_asks_again(self):
        zoom = FakeZoom({8: _weekly(8, "Technology Circle weekly")})
        answers = _Answers("d", "no", "i")
        _room(zoom, input_fn=answers)
        assert zoom.writes == [("create", "Technology Circle Meeting Room")]
        assert len(answers.prompts) == 3

    def test_switching_use_asks_about_the_first(self):
        zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room"),
                         8: room(8, "Technology Circle Zoom")})
        answers = _Answers("u", "u", "y", "d", "delete")
        link = _room(zoom, input_fn=answers)
        assert answers.prompts[2] == ("  You already chose to use 1. Technology Circle Meeting "
                                      "Room. Use this one instead? [y/n]: ")
        assert answers.prompts[3] == ("  1. Technology Circle Meeting Room is now unused — "
                                      "[i]gnore, [d]elete, or [q]uit: ")
        assert zoom.writes[0] == ("delete", "7")
        assert link.join_url == "https://zoom.us/j/8?pwd=p"

    def test_declining_switch_asks_again_without_use(self):
        zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room"),
                         8: room(8, "Technology Circle Zoom")})
        answers = _Answers("u", "u", "n", "i")
        link = _room(zoom, input_fn=answers)
        assert answers.prompts[3] == "  2. Technology Circle Zoom — [i]gnore, [d]elete, or [q]uit: "
        assert zoom.writes == []
        assert link.join_url == "https://zoom.us/j/7?pwd=p"

    def test_quit_changes_nothing(self):
        zoom = FakeZoom({8: _weekly(8, "Technology Circle weekly")})
        with pytest.raises(QuitRequested):
            _room(zoom, input_fn=_Answers("q"))
        assert zoom.writes == []

    def test_dry_run_asks_but_changes_nothing(self, capsys, quiet_log):
        zoom = FakeZoom({8: _weekly(8, "Technology Circle weekly")})
        answers = _Answers("d")
        assert _room(zoom, dry_run=True, input_fn=answers) is None
        assert zoom.writes == []
        assert answers.prompts == [
            "  [dry-run] 1. Technology Circle weekly — [i]gnore, "
            "[d]elete (DRY-RUN: DELETION WON'T OCCUR), or [q]uit: "]
        out = capsys.readouterr().out
        assert "Would create a new room, 'Technology Circle Meeting Room', and delete:" in out
        assert ("INFO", "would_delete_meeting",
                "Technology Circle: 8 'Technology Circle weekly'") in quiet_log

    def test_linked_elsewhere_listed_not_asked(self, capsys):
        zoom = FakeZoom({7: room(7, "Technology Circle Meeting Room"),
                         8: room(8, "Technology Circle Zoom")})
        linked = LinkedResources(rooms={"8": "other@berkeleymoshav.org"})
        answers = _Answers("u")
        _room(zoom, input_fn=answers, linked=linked)
        assert len(answers.prompts) == 1
        assert "linked to other@berkeleymoshav.org, not selectable" in capsys.readouterr().out


class TestDescribeSchedule:
    def test_kinds(self):
        assert describe_schedule({"type": 3}, NOW) == "no fixed time"
        assert describe_schedule({"type": 2, "start_time": "2026-10-31T00:00:00Z"}, NOW) == \
            "one-off, Fri Oct 30, 5:00 PM"
        monthly = {"type": 8, "recurrence": {"type": 3, "monthly_week": 2, "monthly_week_day": 3},
                   "occurrences": [{"start_time": "2026-11-11T03:00:00Z"}]}
        assert describe_schedule(monthly, NOW) == \
            "monthly on the 2nd Tue, 7:00 PM, next Tue Nov 10"
        biweekly = {"type": 8, "recurrence": {"type": 2, "repeat_interval": 2,
                                              "weekly_days": "2,4"}}
        assert describe_schedule(biweekly, NOW) == \
            "every 2 weeks on Mon, Wed, no upcoming occurrences"
        assert describe_schedule({"type": 8, "recurrence": {"type": 1}, "occurrences": [
            {"start_time": "2026-10-18T16:00:00Z", "status": "deleted"},
            {"start_time": "2026-10-19T16:00:00Z"}]}, NOW) == "daily, 9:00 AM, next Mon Oct 19"


class TestAcronymMatching:
    def test_zoom_meeting_named_with_acronym_offered(self):
        zoom = FakeZoom({7: room(7, "DF&L Meeting Room")})
        link = ensure_zoom_room(
            zoom, "Development, Finance, & Legal Circle", None, GROUP,
            meetings=zoom.list_account_meetings, linked=lambda: LinkedResources(),
            dry_run=False, input_fn=_inputs("u"), now=NOW)
        assert link.join_url == "https://zoom.us/j/7?pwd=p"

    def test_calendar_named_with_acronym_offered(self):
        svc = FakeCalendarService({"c1": "DF&L"})
        assert ensure_group_calendar(
            svc, "Development, Finance, & Legal Circle", GROUP, None,
            owned_calendars=lambda: mr.list_owned_calendars(svc),
            linked=lambda: LinkedResources(), dry_run=False, input_fn=_inputs("1")) == "c1"
