"""
Tests for group_meetings/event_rules.gs, the Apps Script project's pure
rules, run under Node (skipped if Node isn't installed).

All names, IDs, and addresses are fictional.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from util.google_group_footer import build_footer_block

RULES = Path(__file__).resolve().parent.parent / "group_meetings" / "event_rules.gs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")

CAL = "c_abc@group.calendar.google.com"
GROUP = "tech@x.org"
ALL_EVENTS = "c_all@group.calendar.google.com"
URL = "https://us06web.zoom.us/j/81234567890?pwd=enc"
CTX = {"zoomUrl": URL, "passcode": "123456", "groupEmail": GROUP,
       "allEventsCalendarId": ALL_EVENTS}


def js(expr: str, **values):
    """Evaluate expr in a context holding event_rules.gs and values (as
    JSON globals); return the result, converted from JSON."""
    prelude = "".join(f"const {k} = {json.dumps(v)};\n" for k, v in values.items())
    program = f"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({{}});
vm.runInContext(fs.readFileSync({json.dumps(str(RULES))}, 'utf8'), ctx);
const out = vm.runInContext({json.dumps(prelude + "JSON.stringify(" + expr + ")")}, ctx);
process.stdout.write(out === undefined ? 'null' : out);
"""
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def patch(event, ctx=CTX):
    return js("computeEventPatch(event, ctx)", event=event, ctx=ctx)


def done(event):
    """The event after its patch was applied, as Calendar would return it."""
    change = patch(event)
    return {**event, **change["patch"]} if change else event


class TestParseFooter:
    def test_reads_python_written_footer(self):
        footer = "Notes\n\n" + build_footer_block(
            "1", "F", "https://g", "Tech Circle", calendar_id=CAL, zoom_url=URL,
            zoom_passcode="123456")
        assert js("parseFooter(f)", f=footer) == {
            "calendarId": CAL, "zoomUrl": URL, "passcode": "123456"}

    def test_calendar_id_with_plus_and_slash(self):
        footer = build_footer_block("1", "F", "https://g", "X", calendar_id="a?>")
        assert js("parseFooter(f).calendarId", f=footer) == "a?>"

    def test_no_links(self):
        assert js("parseFooter(null)") == {"calendarId": None, "zoomUrl": None, "passcode": None}


class TestComputeEventPatch:
    def test_new_event_gets_zoom_details_and_guests(self):
        change = patch({"summary": "Meeting", "organizer": {"email": CAL}})
        p = change["patch"]
        assert p["location"] == URL
        assert p["description"] == (
            f"Join Zoom meeting: {URL}\nMeeting ID 812 3456 7890, passcode 123456\n"
            "(Added automatically from the group's Zoom room.)")
        assert p["extendedProperties"] == {"private": {"groupZoomUrl": URL}}
        assert p["attendees"] == [{"email": ALL_EVENTS}, {"email": GROUP}]
        assert change["sendUpdates"] == "all"

    def test_second_pass_changes_nothing(self):
        once = done({"summary": "Meeting", "description": "Agenda: budget"})
        assert patch(once) is None

    def test_keeps_description_and_location(self):
        p = patch({"description": "Agenda: budget", "location": "Common House"})["patch"]
        assert "location" not in p
        assert p["description"].startswith("Agenda: budget\n\nJoin Zoom meeting: ")

    def test_html_description_uses_br(self):
        p = patch({"description": "<b>Agenda</b>"})["patch"]
        assert p["description"].startswith("<b>Agenda</b><br><br>Join Zoom meeting: ")
        assert patch(done({"description": "<b>Agenda</b>"})) is None

    def test_changed_room_link_replaces_old(self):
        old = "https://us06web.zoom.us/j/81234567890?pwd=OLD"
        event = done({"description": "Agenda"})
        event = {**event, "location": old,
                 "description": event["description"].replace(URL, old),
                 "extendedProperties": {"private": {"groupZoomUrl": old}}}
        p = patch(event)["patch"]
        assert p["location"] == URL
        assert old not in p["description"] and p["description"].count(URL) == 1
        assert p["extendedProperties"]["private"]["groupZoomUrl"] == URL

    def test_no_zoom_marker(self):
        change = patch({"summary": "Walk [no zoom]", "attendees": [
            {"email": ALL_EVENTS}, {"email": GROUP}]})
        assert change is None

    def test_other_zoom_link_left_alone_but_guests_added(self):
        p = patch({"description": "Join https://zoom.us/j/99999999999"})["patch"]
        assert set(p) == {"attendees"}

    def test_typed_meet_link_left_alone(self):
        p = patch({"location": "https://meet.google.com/abc-defg-hij"})["patch"]
        assert set(p) == {"attendees"}

    def test_auto_attached_meet_still_gets_zoom(self):
        p = patch({"hangoutLink": "https://meet.google.com/abc-defg-hij",
                   "conferenceData": {"conferenceId": "abc"}})["patch"]
        assert p["location"] == URL

    def test_guests_already_invited_no_email(self):
        event = done({"summary": "Meeting"})
        event["attendees"] = [{"email": GROUP.upper()}, {"email": ALL_EVENTS}]
        assert patch(event) is None

    def test_only_all_events_missing_sends_no_email(self):
        event = done({"summary": "Meeting"})
        event["attendees"] = [{"email": GROUP}]
        change = patch(event)
        assert change["patch"]["attendees"][-1] == {"email": ALL_EVENTS}
        assert change["sendUpdates"] == "none"

    def test_no_passcode_no_id_line(self):
        p = patch({}, {**CTX, "passcode": None})["patch"]
        assert "Meeting ID" not in p["description"]


class TestInvitationCopy:
    def test_own_event_is_not_a_copy(self):
        assert js("isInvitationCopy(e, cal, [])", e={"organizer": {"email": CAL}}, cal=CAL) is False

    def test_someone_elses_is(self):
        assert js("isInvitationCopy(e, cal, [])",
                  e={"organizer": {"email": "dana@gmail.com"}}, cal=CAL) is True

    def test_another_managed_calendar_is_not(self):
        assert js("isInvitationCopy(e, cal, ids)", e={"organizer": {"email": "c_other@x"}},
                  cal=CAL, ids=["c_other@x"]) is False


def meeting(key, owner, start, end, **kw):
    return {"key": key, "owner": owner, "start": start, "end": end, "groups": [], **kw}


class TestFindOverlaps:
    def test_same_owner_overlap(self):
        conflicts = js("findOverlaps(m)", m=[
            meeting("a", "u1", 0, 60), meeting("b", "u1", 30, 90)])
        assert [(c["a"]["key"], c["b"]["key"]) for c in conflicts] == [("a", "b")]

    def test_back_to_back_is_fine(self):
        assert js("findOverlaps(m)", m=[meeting("a", "u1", 0, 60), meeting("b", "u1", 60, 90)]) == []

    def test_less_than_gap_apart_is_flagged(self):
        conflicts = js("findOverlaps(m, 15)", m=[meeting("a", "u1", 0, 60), meeting("b", "u1", 70, 90)])
        assert [(c["a"]["key"], c["b"]["key"]) for c in conflicts] == [("a", "b")]

    def test_exactly_gap_apart_is_fine(self):
        assert js("findOverlaps(m, 15)", m=[meeting("a", "u1", 0, 60), meeting("b", "u1", 75, 90)]) == []

    def test_gap_measured_from_the_later_end(self):
        # "a" runs past "b", so "c" is measured from a's end, not b's.
        conflicts = js("findOverlaps(m, 15)", m=[
            meeting("a", "u1", 0, 100), meeting("b", "u1", 10, 20), meeting("c", "u1", 105, 120)])
        assert sorted((c["a"]["key"], c["b"]["key"]) for c in conflicts) == [("a", "b"), ("a", "c")]

    def test_gap_applies_only_to_same_owner(self):
        assert js("findOverlaps(m, 15)", m=[meeting("a", "u1", 0, 60), meeting("b", "u2", 65, 90)]) == []

    def test_different_owners_fine(self):
        assert js("findOverlaps(m)", m=[meeting("a", "u1", 0, 60), meeting("b", "u2", 30, 90)]) == []

    def test_same_meeting_seen_twice_is_merged(self):
        conflicts = js("findOverlaps(m)", m=[
            meeting("a", "u1", 0, 60, groups=["g1"]), meeting("a", "u1", 0, 60, groups=["g2"]),
            meeting("b", "u1", 30, 90, groups=["g3"])])
        assert len(conflicts) == 1
        assert conflicts[0]["a"]["groups"] == ["g1", "g2"]

    def test_three_way(self):
        conflicts = js("findOverlaps(m)", m=[
            meeting("a", "u1", 0, 100), meeting("b", "u1", 10, 20), meeting("c", "u1", 50, 60)])
        assert sorted((c["a"]["key"], c["b"]["key"]) for c in conflicts) == [("a", "b"), ("a", "c")]

    def test_conflict_id_is_order_independent(self):
        a, b = meeting("a", "u1", 0, 60), meeting("b", "u1", 30, 90)
        assert js("conflictId({a: x, b: y}) === conflictId({a: y, b: x})", x=a, y=b) is True


def test_zoom_ids_in_event():
    event = {"location": "https://us06web.zoom.us/j/111?pwd=x",
             "description": "or https://zoom.us/j/222 and again https://zoom.us/j/111"}
    assert js("zoomIdsInEvent(e)", e=event) == ["111", "222"]
