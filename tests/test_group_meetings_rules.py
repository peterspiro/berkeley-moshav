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
            "calendarId": CAL, "zoomUrl": URL, "passcode": "123456", "audience": None,
            "alsoInvite": []}

    def test_calendar_id_with_plus_and_slash(self):
        footer = build_footer_block("1", "F", "https://g", "X", calendar_id="a?>")
        assert js("parseFooter(f).calendarId", f=footer) == "a?>"

    def test_no_links(self):
        assert js("parseFooter(null)") == {"calendarId": None, "zoomUrl": None, "passcode": None,
                                           "audience": None, "alsoInvite": []}

    def test_reads_an_admin_groups_audience(self):
        footer = build_footer_block(
            "1", "F", "https://g", "Community Meetings", calendar_id=CAL, zoom_url=URL,
            zoom_passcode="123456", audience="All.Members@x.org")
        assert js("parseFooter(f).audience", f=footer) == "all.members@x.org"


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


FAQ = "https://docs.google.com/document/d/1rr8WqPnsLKw-tgVea40NkyTxlwLFVVLnXbL3UVFQcZY"
FAQ_LINE = f"Questions about joining? See the BM Meetings FAQ: {FAQ}"
CTX_FAQ = {**CTX, "faqUrl": FAQ}


def done_with(event, ctx):
    change = patch(event, ctx)
    return {**event, **change["patch"]} if change else event


class TestMeetingsFaq:
    def test_added_above_zoom_block(self):
        desc = patch({"description": "Agenda"}, CTX_FAQ)["patch"]["description"]
        assert desc.startswith(f"Agenda\n\n{FAQ_LINE}\n\nJoin Zoom meeting: ")

    def test_empty_description(self):
        desc = patch({}, CTX_FAQ)["patch"]["description"]
        assert desc.startswith(f"{FAQ_LINE}\n\nJoin Zoom meeting: ")

    def test_second_pass_changes_nothing(self):
        assert patch(done_with({"description": "Agenda"}, CTX_FAQ), CTX_FAQ) is None

    def test_existing_link_in_any_form_not_duplicated(self):
        event = {"description": f"See {FAQ}/edit?tab=t.0#heading=h.abc for help"}
        desc = patch(event, CTX_FAQ)["patch"]["description"]
        assert "BM Meetings FAQ" not in desc and desc.count("1rr8WqPn") == 1

    def test_html_description(self):
        desc = patch({"description": "<b>Agenda</b>"}, CTX_FAQ)["patch"]["description"]
        assert desc.startswith(f"<b>Agenda</b><br><br>{FAQ_LINE}<br><br>Join Zoom meeting: ")

    def test_deleted_line_is_added_back(self):
        event = done_with({"description": "Agenda"}, CTX_FAQ)
        event["description"] = event["description"].replace(f"{FAQ_LINE}\n\n", "")
        assert FAQ_LINE in patch(event, CTX_FAQ)["patch"]["description"]

    def test_room_link_change_keeps_faq_in_place(self):
        event = done_with({"description": "Agenda"}, CTX_FAQ)
        new = {**CTX_FAQ, "zoomUrl": "https://us06web.zoom.us/j/81234567890?pwd=NEW"}
        desc = patch(event, new)["patch"]["description"]
        assert desc.count(FAQ_LINE) == 1
        assert desc.index(FAQ_LINE) < desc.index("Join Zoom meeting")

    def test_not_added_without_zoom_room(self):
        for event in ({"summary": "Walk [no zoom]"},
                      {"description": "Join https://zoom.us/j/99999999999"}):
            change = patch(event, CTX_FAQ)
            assert change is None or "description" not in change["patch"]


class TestAgendaLink:
    AGENDA = "https://docs.google.com/document/d/AGENDA_doc-1/edit"
    LINE = f"Agenda and minutes: {AGENDA}"
    CTX = {**CTX_FAQ, "agendaUrl": AGENDA}

    def test_added_above_faq_line(self):
        desc = patch({"description": "Notes"}, self.CTX)["patch"]["description"]
        assert desc.startswith(f"Notes\n\n{self.LINE}\n\n{FAQ_LINE}\n\nJoin Zoom meeting: ")

    def test_added_once(self):
        assert patch(done_with({"description": "Notes"}, self.CTX), self.CTX) is None

    def test_not_added_when_doc_already_linked(self):
        event = {"description": "Our doc: https://docs.google.com/document/d/AGENDA_doc-1/edit?tab=t.0"}
        desc = patch(event, self.CTX)["patch"]["description"]
        assert "Agenda and minutes:" not in desc

    def test_added_at_end_without_zoom(self):
        event = {"description": "In person [no zoom]"}
        desc = patch(event, self.CTX)["patch"]["description"]
        assert desc == f"In person [no zoom]\n\n{self.LINE}"

    OLD = "https://docs.google.com/document/d/OLD_doc-0/edit"

    def test_old_doc_relinked_not_added_twice(self):
        old_ctx = {**CTX_FAQ, "agendaUrl": self.OLD}
        event = done_with({"description": "Notes"}, old_ctx)
        desc = patch(event, self.CTX)["patch"]["description"]
        assert desc.count("Agenda and minutes:") == 1
        assert self.LINE in desc and "OLD_doc" not in desc

    def test_old_doc_relinked_in_html_link(self):
        old = f'Notes<br><br>Agenda and minutes: <a href="{self.OLD}">{self.OLD}</a><br>More'
        desc = patch({"description": old + " [no zoom]"}, self.CTX)["patch"]["description"]
        assert desc == (f'Notes<br><br>Agenda and minutes: <a href="{self.AGENDA}">'
                        f'{self.AGENDA}</a><br>More [no zoom]')

    def test_other_doc_links_kept(self):
        event = {"description": f"Minutes: {self.OLD}\nAgenda and minutes: {self.OLD} [no zoom]"}
        desc = patch(event, self.CTX)["patch"]["description"]
        assert desc == f"Minutes: {self.OLD}\nAgenda and minutes: {self.AGENDA} [no zoom]"

    def test_no_agenda_doc_changes_nothing_new(self):
        assert "Agenda and minutes" not in patch({}, CTX_FAQ)["patch"]["description"]


class TestStaleAgendaLink:
    """A group whose agenda doc was trashed or deleted: agendaUrl is gone,
    and staleAgendaUrl names the doc it had."""
    GONE = "https://docs.google.com/document/d/GONE_doc-9/edit"
    OTHER = "https://docs.google.com/document/d/OTHER_doc-2/edit"
    LINE = f"Agenda and minutes: {GONE}"
    CTX = {**CTX_FAQ, "staleAgendaUrl": GONE}
    NO_ZOOM_CTX = {**CTX, "zoomUrl": None}

    def test_removed_from_event_with_zoom(self):
        event = done_with({"description": "Notes"}, {**CTX_FAQ, "agendaUrl": self.GONE})
        assert self.LINE in event["description"]
        desc = patch(event, self.CTX)["patch"]["description"]
        assert desc == done_with({"description": "Notes"}, CTX_FAQ)["description"]

    def test_removed_once(self):
        event = done_with({"description": "Notes"}, {**CTX_FAQ, "agendaUrl": self.GONE})
        assert patch(done_with(event, self.CTX), self.CTX) is None

    def test_removed_without_zoom(self):
        event = {"description": f"In person [no zoom]\n\n{self.LINE}"}
        assert patch(event, self.CTX)["patch"]["description"] == "In person [no zoom]"

    def test_removed_from_start(self):
        event = {"description": f"{self.LINE}\n\nIn person [no zoom]"}
        assert patch(event, self.CTX)["patch"]["description"] == "In person [no zoom]"

    def test_removed_from_html_link(self):
        event = {"description": (f'Notes<br><br>Agenda and minutes: <a href="{self.GONE}">'
                                 f'{self.GONE}</a><br>More [no zoom]')}
        assert patch(event, self.CTX)["patch"]["description"] == "Notes<br>More [no zoom]"

    def test_removed_with_no_zoom_room(self):
        event = {"description": f"Notes\n\n{self.LINE}"}
        change = patch(event, self.NO_ZOOM_CTX)
        assert change["patch"]["description"] == "Notes"

    def test_other_docs_kept(self):
        desc = (f"[no zoom] Minutes: {self.GONE}\n\nAgenda and minutes: {self.OTHER}\n\n"
                f"{self.LINE}")
        assert patch({"description": desc}, self.CTX)["patch"]["description"] == (
            f"[no zoom] Minutes: {self.GONE}\n\nAgenda and minutes: {self.OTHER}")

    def test_rest_of_line_kept(self):
        """Text typed after the link stays, so a "[no zoom]" there still counts."""
        event = {"description": f"Notes\n\n{self.LINE} [no zoom]"}
        assert patch(event, self.CTX)["patch"]["description"] == "Notes\n\n[no zoom]"

    def test_reminder_tag_on_line_kept(self):
        event = {"description": f"Notes\n\n{self.LINE} [remind 2 hours] [no zoom]"}
        desc = patch(event, self.CTX)["patch"]["description"]
        assert desc == "Notes\n\n[remind 2 hours] [no zoom]"
        assert js("reminderLeads(e, null).leads", e={"description": desc}) == [2 * 3600 * 1000]

    def test_rest_of_html_line_kept(self):
        event = {"description": (f'Notes<br>Agenda and minutes: <a href="{self.GONE}">'
                                 f'{self.GONE}</a> [no zoom]<br>More')}
        assert patch(event, self.CTX)["patch"]["description"] == (
            "Notes<br>[no zoom]<br>More")

    def test_nothing_to_remove(self):
        change = patch({"description": "In person [no zoom]"}, self.CTX)
        assert "description" not in change["patch"]

    def test_current_doc_takes_precedence(self):
        """A group that has a doc again relinks the line instead."""
        ctx = {**self.CTX, "agendaUrl": self.OTHER}
        desc = patch({"description": f"{self.LINE} [no zoom]"}, ctx)["patch"]["description"]
        assert desc == f"Agenda and minutes: {self.OTHER} [no zoom]"


class TestZoomLinkToFollow:
    def desc(self, description):
        return patch({"description": description})["patch"]["description"]

    def test_last_line_removed(self):
        assert self.desc("Agenda\n\nZoom link to follow.").startswith(
            "Agenda\n\nJoin Zoom meeting: ")

    def test_middle_line_removed(self):
        assert self.desc("Agenda\nZoom link to follow\nBring snacks").startswith(
            "Agenda\nBring snacks\n\nJoin Zoom meeting: ")

    def test_first_line_removed(self):
        assert self.desc("(Zoom link to follow)\n\nAgenda").startswith(
            "Agenda\n\nJoin Zoom meeting: ")

    def test_only_line_removed(self):
        assert self.desc("zoom details will follow").startswith("Join Zoom meeting: ")

    def test_html_line_removed_keeping_tags(self):
        assert self.desc("Agenda<br><br><b>Zoom link to follow</b><br>More").startswith(
            "Agenda<b></b><br>More<br><br>Join Zoom meeting: ")

    def test_html_element_removed(self):
        assert self.desc("<p>Agenda</p><p>Zoom link to follow</p>").startswith(
            "<p>Agenda</p><p></p><br><br>Join Zoom meeting: ")

    def test_mention_within_a_line_kept(self):
        text = "Agenda; Zoom link to follow by email"
        assert self.desc(text).startswith(text + "\n\nJoin Zoom meeting: ")

    def test_second_pass_changes_nothing(self):
        assert patch(done({"description": "Agenda\n\nZoom link to follow"})) is None

    def test_kept_when_no_zoom(self):
        assert patch({"description": "Zoom link to follow [no zoom]"},
                     {**CTX, "groupEmail": None, "allEventsCalendarId": None}) is None


class TestZoomUser:
    USER_LINE = "Zoom user: 2 (Jane Doe) (meetings with the same Zoom user can't overlap)"

    def test_named_in_block(self):
        desc = patch({}, {**CTX, "zoomUser": "2 (Jane Doe)"})["patch"]["description"]
        assert desc == (
            f"Join Zoom meeting: {URL}\nMeeting ID 812 3456 7890, passcode 123456\n"
            f"{self.USER_LINE}\n(Added automatically from the group's Zoom room.)")

    def test_added_to_existing_block(self):
        event = done({"description": "Agenda"})
        desc = patch(event, {**CTX, "zoomUser": "2 (Jane Doe)"})["patch"]["description"]
        assert desc.startswith("Agenda\n\nJoin Zoom meeting: ") and self.USER_LINE in desc

    def test_changed_user_replaces_old(self):
        event = done_with({"description": "Agenda"}, {**CTX, "zoomUser": "1 (Ann Lee)"})
        desc = patch(event, {**CTX, "zoomUser": "2 (Jane Doe)"})["patch"]["description"]
        assert self.USER_LINE in desc and "Zoom user: 1" not in desc

    def test_unknown_keeps_existing(self):
        ctx = {**CTX, "zoomUser": "2 (Jane Doe)"}
        event = done_with({"description": "Agenda"}, ctx)
        assert patch(event, {k: v for k, v in ctx.items() if k != "zoomUser"}) is None

    def test_unknown_without_existing_omits_line(self):
        assert "Zoom user" not in patch({})["patch"]["description"]

    def test_none_removes_line(self):
        event = done_with({"description": "Agenda"}, {**CTX, "zoomUser": "2"})
        desc = patch(event, {**CTX, "zoomUser": None})["patch"]["description"]
        assert "Zoom user" not in desc

    def test_second_pass_changes_nothing(self):
        ctx = {**CTX, "zoomUser": "2"}
        assert patch(done_with({"description": "<b>Agenda</b>"}, ctx), ctx) is None

    @pytest.mark.parametrize("names, label", [
        ({"b@x.org": "Jane Doe"}, "2 (Jane Doe)"), ({}, "2")])
    def test_label_numbered_user(self, names, label):
        assert js("zoomUserLabel('b@x.org', n, m)", n={"b@x.org": "2"}, m=names) == label

    @pytest.mark.parametrize("names, label", [
        ({"c@x.org": "Ann Lee"}, "Ann Lee"), ({}, "c@x.org")])
    def test_label_other_user(self, names, label):
        assert js("zoomUserLabel('c@x.org', n, m)", n={"b@x.org": "2"}, m=names) == label

    def test_second_pass_with_name_changes_nothing(self):
        ctx = {**CTX, "zoomUser": "2 (Jane Doe)"}
        assert patch(done_with({"description": "<b>Agenda</b>"}, ctx), ctx) is None


class TestInvitationCopy:
    def test_own_event_is_not_a_copy(self):
        assert js("isInvitationCopy(e, cal, [])", e={"organizer": {"email": CAL}}, cal=CAL) is False

    def test_someone_elses_is(self):
        assert js("isInvitationCopy(e, cal, [])",
                  e={"organizer": {"email": "dana@gmail.com"}}, cal=CAL) is True

    def test_another_managed_calendar_is_not(self):
        assert js("isInvitationCopy(e, cal, ids)", e={"organizer": {"email": "c_other@x"}},
                  cal=CAL, ids=["c_other@x"]) is False


class TestOtherCalendarEvents:
    """An event organized by one of the account's other calendars gets only
    the all-events calendar (computeEventPatch with no group or room)."""

    def test_gets_all_events_calendar_without_email(self):
        change = patch({"summary": "Potluck", "description": "Bring a dish",
                        "attendees": [{"email": "dana@gmail.com"}]},
                       {"allEventsCalendarId": ALL_EVENTS})
        assert change == {"patch": {"attendees": [{"email": "dana@gmail.com"},
                                                  {"email": ALL_EVENTS}]},
                          "sendUpdates": "none"}

    def test_already_invited_changes_nothing(self):
        assert patch({"attendees": [{"email": ALL_EVENTS.upper()}]},
                     {"allEventsCalendarId": ALL_EVENTS}) is None


class TestOtherOwnedCalendars:
    ADMIN = "admin@x.org"

    def other(self, items, skip=()):
        return js("otherOwnedCalendars(items, account, skip)",
                  items=items, account=self.ADMIN, skip=list(skip))

    def test_primary_and_owned_secondary_calendars(self):
        items = [
            {"id": self.ADMIN, "summary": self.ADMIN, "accessRole": "owner", "primary": True},
            {"id": "c_misc@group.calendar.google.com", "summary": "Misc",
             "summaryOverride": "Misc (admin)", "accessRole": "owner"},
        ]
        assert self.other(items) == [
            {"id": self.ADMIN, "name": self.ADMIN},
            {"id": "c_misc@group.calendar.google.com", "name": "Misc (admin)"}]

    def test_skips_group_and_all_events_calendars(self):
        items = [{"id": CAL, "summary": "Tech", "accessRole": "owner"},
                 {"id": ALL_EVENTS, "summary": "All", "accessRole": "owner"}]
        assert self.other(items, [CAL.upper(), ALL_EVENTS]) == []

    def test_skips_calendars_it_does_not_own(self):
        items = [{"id": "c_r@group.calendar.google.com", "summary": "R", "accessRole": "writer"},
                 {"id": "dana@x.org", "summary": "Dana", "accessRole": "owner"}]
        assert self.other(items) == []


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


OPTS = {"alertEmail": "alerts@x.org", "techName": "Technology Circle",
        "allEventsUrl": "https://cal/all", "minGapMinutes": 15}
MIN = 60 * 1000


def message(a, b):
    return js("overlapMessage({a: a, b: b}, Object.assign(o, {formatRange: (s, e) => "
              "`${s / 60000}-${e / 60000}`}))", a=a, b=b, o=OPTS)


def group_meeting(title, group, start, end):
    return meeting(title, "u1", start * MIN, end * MIN, title=title,
                   groups=[f"{group.split()[0].lower()}@x.org"], names=[group])


def zoom_meeting(title, start, end):
    return meeting(title, "u1", start * MIN, end * MIN, title=title, groups=[], names=[])


class TestOverlapMessage:
    def test_two_groups_overlap(self):
        msg = message(group_meeting("Finance meeting", "Finance Circle", 0, 90),
                      group_meeting("Care meeting", "Care Circle", 60, 120))
        assert msg["to"] == ["finance@x.org", "care@x.org"]
        assert msg["cc"] == ["alerts@x.org"]
        assert msg["subject"] == 'Zoom meetings overlap: "Finance meeting" and "Care meeting"'
        assert msg["body"] == (
            "Hi Finance Circle and Care Circle,\n\n"
            "These two meetings overlap:\n"
            '  - "Finance meeting" (Finance Circle), 0-90\n'
            '  - "Care meeting" (Care Circle), 60-120\n\n'
            "Both meetings use the same Zoom user account, which can host only one meeting "
            "at a time, so the second meeting won't be able to start while the first is "
            "still going.\n\n"
            "Your options are:\n"
            "- Request the Tech Circle to move one of your two groups' Zoom rooms to our "
            "second Zoom user account.  This may or may not be possible, depending on your "
            "meeting schedule.\n"
            "- Hold one of these meetings on a personal or work Zoom account instead: "
            "replace the Zoom link in its calendar event with that account's link, and "
            "delete the group room's \"Join Zoom meeting\" details from the event's "
            "description.\n"
            "- Agree between you to move one of these meetings, and change its time on its "
            "group calendar. To find a free time, check the All Berkeley Moshav Events "
            "calendar, which shows every group's meetings:\nhttps://cal/all\n\n"
            "This message was sent automatically. Questions? Reply to alerts@x.org.\n")

    def test_two_groups_too_close(self):
        msg = message(group_meeting("Finance meeting", "Finance Circle", 0, 90),
                      group_meeting("Care meeting", "Care Circle", 100, 120))
        assert msg["subject"].startswith("Zoom meetings too close together: ")
        assert "These two meetings are only 10 minutes apart:" in msg["body"]
        assert "so if the first runs over, the second won't be able to start until it ends." \
            in msg["body"]
        assert ("change its time on its group calendar, leaving at least 15 minutes "
                "between them.") in msg["body"]

    def test_back_to_back(self):
        msg = message(group_meeting("A", "Finance Circle", 0, 60),
                      group_meeting("B", "Care Circle", 60, 90))
        assert "These two meetings are back to back:" in msg["body"]

    def test_same_group_twice(self):
        msg = message(group_meeting("A", "Finance Circle", 0, 60),
                      group_meeting("B", "Finance Circle", 30, 90))
        assert msg["to"] == ["finance@x.org"]
        assert msg["body"].startswith("Hi Finance Circle,\n")
        assert "move one of the groups' Zoom rooms" not in msg["body"]
        assert "move one of your" not in msg["body"]
        assert "- Hold one of these meetings on a personal or work Zoom account" in msg["body"]
        assert "- Move one of them to another time on the Finance Circle calendar." \
            in msg["body"]

    def test_group_and_zoom_meeting(self):
        msg = message(group_meeting("Finance meeting", "Finance Circle", 0, 90),
                      zoom_meeting("Board training", 60, 120))
        assert msg["to"] == ["finance@x.org", "alerts@x.org"]
        assert msg["cc"] == []
        assert msg["body"].startswith("Hi Technology Circle and Finance Circle,\n")
        assert '  - "Board training" (a meeting scheduled in our Zoom account), 60-120' \
            in msg["body"]
        assert ("- Request the Tech Circle to move the Finance Circle's Zoom room to our "
                "second Zoom user account.") in msg["body"]
        assert ("for the Finance Circle meeting, replace the Zoom link in its calendar event "
                "with that account's link") in msg["body"]
        assert ("for the other meeting, schedule it on that account and delete it from our "
                "Zoom account.") in msg["body"]
        assert ("- Agree between you which meeting moves to another time. The Finance Circle "
                "meeting can be moved on the Finance Circle calendar; the other meeting can "
                "be moved in Zoom.") in msg["body"]

    def test_two_zoom_meetings(self):
        msg = message(zoom_meeting("A", 0, 60), zoom_meeting("B", 30, 90))
        assert msg["to"] == ["alerts@x.org"] and msg["cc"] == []
        assert msg["body"].startswith("Hi Technology Circle,\n")
        assert "second Zoom user account" not in msg["body"]
        assert ("- Hold one of these meetings on a personal or work Zoom account instead: "
                "schedule it on that account and delete it from our Zoom account.") in msg["body"]
        assert "- Move one of them to another time in Zoom." in msg["body"]

    def test_joint_meeting_names_both_groups(self):
        joint = group_meeting("Joint", "Finance Circle", 0, 60)
        joint["groups"].append("care@x.org")
        joint["names"].append("Care Circle")
        msg = message(joint, group_meeting("C", "Garden Club", 30, 90))
        assert msg["body"].startswith("Hi Finance Circle, Care Circle and Garden Club,\n")
        assert '  - "Joint" (Finance Circle and Care Circle), 0-60' in msg["body"]
        assert "move one of your groups' Zoom rooms" in msg["body"]

    def test_every_case_lists_options(self):
        for msg in (message(group_meeting("A", "Finance Circle", 0, 60),
                            group_meeting("B", "Care Circle", 30, 90)),
                    message(group_meeting("A", "Finance Circle", 0, 60),
                            zoom_meeting("B", 30, 90)),
                    message(group_meeting("A", "Finance Circle", 0, 60),
                            group_meeting("B", "Finance Circle", 30, 90)),
                    message(zoom_meeting("A", 0, 60), zoom_meeting("B", 30, 90))):
            assert "Your options are:\n- " in msg["body"]
            assert "personal or work Zoom account" in msg["body"]
            assert "To find a free time" in msg["body"]


class TestTextToHtml:
    def test_paragraphs_lists_and_links(self):
        html = js("textToHtml(t)", t=(
            "Hi A & B,\n\nThese two meetings overlap:\n  - \"X\" (A), 1\n  - \"Y\" (B), 2\n\n"
            "Your options are:\n- One.\n- Two:\nhttps://cal/all?a=1&b=2\n\nBye.\n"))
        assert html == (
            "<p>Hi A &amp; B,</p>\n"
            "<p>These two meetings overlap:</p><ul><li>&quot;X&quot; (A), 1</li>"
            "<li>&quot;Y&quot; (B), 2</li></ul>\n"
            "<p>Your options are:</p><ul><li>One.</li><li>Two:<br>"
            '<a href="https://cal/all?a=1&amp;b=2">https://cal/all?a=1&amp;b=2</a></li></ul>\n'
            "<p>Bye.</p>")

    def test_long_lines_stay_whole(self):
        line = "word " * 40
        assert js("textToHtml(t)", t=line) == f"<p>{line.strip()}</p>"


def test_merge_keeps_every_group_name():
    merged = js("mergeMeetings(x, y)",
                x={"key": "k", "groups": ["a@x"], "names": ["A"], "title": "T"},
                y={"key": "k", "groups": ["b@x"], "names": ["B"], "title": "T"})
    assert merged["groups"] == ["a@x", "b@x"] and merged["names"] == ["A", "B"]


def test_zoom_user_numbers_cover_room_users():
    from util.zoom_utils import ROOM_USERS
    script = (RULES.parent / "group_meetings.gs").read_text()
    numbers = script.split("const ZOOM_USER_NUMBERS = {", 1)[1].split("};", 1)[0]
    for user in ROOM_USERS:
        assert f"'{user}':" in numbers


MIN = 60 * 1000
HOUR = 60 * MIN
DAY = 24 * HOUR


class TestParseReminderLeads:
    @pytest.mark.parametrize("text, leads", [
        ("24 hours before", [DAY]),
        ("24h", [DAY]),
        ("1 day", [DAY]),
        ("2 days, 1 hour", [2 * DAY, HOUR]),
        ("1 hour and 2 days", [2 * DAY, HOUR]),
        ("1 hour 30 minutes", [90 * MIN]),
        ("1h30m", [90 * MIN]),
        ("1.5 hours", [90 * MIN]),
        ("an hour", [HOUR]),
        ("half an hour", [30 * MIN]),
        ("90 minutes before each meeting", [90 * MIN]),
        ("Two days.", [2 * DAY]),
        ("“2 days, 1 hour”", [2 * DAY, HOUR]),
        ('"90 minutes"', [90 * MIN]),
    ])
    def test_lengths_of_time(self, text, leads):
        assert js("parseReminderLeads(t)", t=text) == {"leads": leads}

    @pytest.mark.parametrize("text", ["none", "None.", "“none”", "no", "off", "No reminders",
                                      "never"])
    def test_none(self, text):
        assert js("parseReminderLeads(t)", t=text) == {"leads": []}

    @pytest.mark.parametrize("text", ["soon", "24", "2 months", "", "the day before at noon"])
    def test_unreadable(self, text):
        assert "error" in js("parseReminderLeads(t)", t=text)

    def test_more_than_two_weeks(self):
        assert "two weeks" in js("parseReminderLeads('3 weeks')")["error"]


def test_format_lead():
    assert js("[DAY, 2 * DAY, 90 * MIN, 15 * MIN, HOUR].map(formatLead)",
              DAY=DAY, MIN=MIN, HOUR=HOUR) == [
        "24 hours", "2 days", "1 hour 30 minutes", "15 minutes", "1 hour"]


def docs_api_doc(rows):
    """A Docs API document with one two-column table of rows (label, value)."""
    def cell(text):
        return {"content": [{"paragraph": {"elements": [{"textRun": {"content": line + "\n"}}]}}
                            for line in text.split("\n")]}
    return {"body": {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": "About this circle\n"}}]}},
        {"table": {"tableRows": [{"tableCells": [cell(a), cell(b)]} for a, b in rows]}},
    ]}}


class TestDocRowValue:
    def test_the_whole_value_cell(self):
        doc = docs_api_doc([("Zoom room", "x"),
                            ("Remind before meetings\ne.g. 2 days; none", "\n2 hours\n")])
        assert js("docRowValue(d, 'Remind before meetings')", d=doc) == "2 hours"
        doc = docs_api_doc([("Remind before meetings", "2 days\n 1 hour ")])
        assert js("docRowValue(d, 'Remind before meetings')", d=doc) == "2 days, 1 hour"

    def test_empty_and_missing(self):
        doc = docs_api_doc([("Remind before meetings", "")])
        assert js("docRowValue(d, 'Remind before meetings')", d=doc) == ""
        assert js("docRowValue(d, 'Nope')", d=doc) is None
        assert js("docRowValue({}, 'Nope')") is None


class TestReminderLeads:
    def test_default(self):
        assert js("reminderLeads({}, null)") == {"leads": [DAY], "source": "default"}

    def test_group_setting(self):
        assert js("reminderLeads({}, '2 hours')") == {"leads": [2 * HOUR], "source": "group"}
        assert js("reminderLeads({}, 'none')") == {"leads": [], "source": "group"}

    def test_empty_group_setting_is_the_default_quietly(self):
        assert js("reminderLeads({}, ' ')") == {"leads": [DAY], "source": "default"}

    def test_event_tag_wins(self):
        event = {"summary": "Meeting", "description": "Bring snacks [remind 1 hour, 1 day]"}
        assert js("reminderLeads(e, 'none')", e=event) == {
            "leads": [DAY, HOUR], "source": "event"}
        assert js("reminderLeads(e, '2 hours')", e={"summary": "X [No Reminder]"}) == {
            "leads": [], "source": "event"}
        assert js("reminderLeads(e, null)", e={"description": "<p>[reminder: none]</p>"}) == {
            "leads": [], "source": "event"}

    def test_tag_typed_in_calendars_editor(self):
        # The editor saves spaces as &nbsp;, and may wrap the tag in tags.
        for description in ["Notes<br>[remind&nbsp;15 minutes]",
                            "<p>[remind&nbsp;<b>15</b>&nbsp;minutes]</p>",
                            "[no&nbsp;reminder]"]:
            leads = js("reminderLeads(e, null)", e={"description": description})["leads"]
            assert leads == ([] if "no" in description else [15 * MIN]), description

    def test_unreadable_setting_falls_back_and_says_so(self):
        result = js("reminderLeads({}, 'whenever')")
        assert result["leads"] == [DAY] and result["source"] == "default"
        assert "Remind before meetings row" in result["error"] and "whenever" in result["error"]

    def test_unreadable_tag_falls_back_to_the_group(self):
        result = js("reminderLeads(e, '2 hours')", e={"description": "[remind soonish]"})
        assert result["leads"] == [2 * HOUR] and result["source"] == "group"
        assert "[remind soonish]" in result["error"]


class TestDueReminderLeads:
    START = 100 * DAY

    def due(self, now, leads=(DAY,), created=0, grace=HOUR):
        return js("dueReminderLeads(s, c, l, n, g)", s=self.START, c=created, l=list(leads),
                  n=now, g=grace)

    def test_due_from_its_time_for_the_grace_period(self):
        assert self.due(self.START - DAY - MIN) == []
        assert self.due(self.START - DAY) == [DAY]
        assert self.due(self.START - DAY + 59 * MIN) == [DAY]
        assert self.due(self.START - DAY + HOUR) == []

    def test_not_for_a_meeting_created_after_it_was_due(self):
        assert self.due(self.START - DAY + MIN, created=self.START - DAY + MIN) == []
        assert self.due(self.START - DAY + MIN, created=None) == [DAY]

    def test_not_once_the_meeting_started(self):
        assert self.due(self.START, leads=[0]) == []

    def test_shortest_first_when_several_are_due(self):
        assert self.due(self.START - HOUR, leads=[HOUR, HOUR + 30 * MIN, DAY]) == [
            HOUR, HOUR + 30 * MIN]


class TestReminderTiming:
    START = 100 * DAY

    def timing(self, now, created=0):
        return js("reminderTiming(s, c, l, n, g)", s=self.START, c=created, l=DAY, n=now, g=HOUR)

    def test_each_state(self):
        assert self.timing(self.START - 2 * DAY) == "upcoming"
        assert self.timing(self.START - DAY) == "due"
        assert self.timing(self.START - DAY + 2 * HOUR) == "missed"
        assert self.timing(self.START - HOUR, created=self.START - 2 * HOUR) == "created after due"
        assert self.timing(self.START) == "started"


class TestReminderMessage:
    OPTS = {"groupEmail": GROUP, "groupName": "Tech Circle", "when": "Tue Nov 3, 7:00 PM–8:00 PM",
            "lead": DAY, "agendaUrl": "https://docs.google.com/document/d/DOC/edit",
            "zoomUrl": URL, "passcode": "123456", "source": "group"}

    def test_meeting_details_and_how_to_change(self):
        event = {"summary": "Meeting", "location": URL, "htmlLink": "https://cal/e",
                 "description": "Agenda and minutes: https://docs.google.com/document/d/DOC/edit"}
        m = js("reminderMessage(e, o)", e=event, o=self.OPTS)
        assert m["to"] == GROUP
        assert m["subject"] == 'Reminder: "Meeting", Tue Nov 3, 7:00 PM–8:00 PM'
        body = m["body"]
        assert f"  Join: {URL}\n  Meeting ID 812 3456 7890, passcode 123456\n" in body
        assert "  Agenda and minutes: https://docs.google.com/document/d/DOC/edit\n" in body
        assert "  Calendar event: https://cal/e\n" in body
        assert "24 hours before the meeting" in body
        assert '"Remind before meetings" under About this circle' in body
        assert "[no reminder]" in body and "Couldn't read" not in body

    def test_no_agenda_doc_or_zoom(self):
        opts = {**self.OPTS, "agendaUrl": None}
        m = js("reminderMessage(e, o)", e={"summary": "Walk", "location": "The park"}, o=opts)
        assert "  Where: The park\n" in m["body"] and "About this circle" not in m["body"]
        assert "Agenda and minutes" not in m["body"]

    def test_says_what_it_couldnt_read(self):
        opts = {**self.OPTS, "source": "default",
                "error": "the agenda doc's Remind before meetings row: \"x\" isn't a length of time"}
        body = js("reminderMessage({}, o)", o=opts)["body"]
        assert "(Couldn't read the agenda doc's Remind before meetings row" in body


def test_reminder_row_label_matches_the_agenda_doc():
    agenda_rules = (RULES.parent.parent / "circle_agendas" / "agenda_rules.gs").read_text()
    assert "const REMINDER_ROW_LABEL = 'Remind before meetings';" in agenda_rules
    assert js("REMINDER_ROW_LABEL") == "Remind before meetings"


def test_admin_calendar_name():
    assert js("adminCalendarName('Community Meetings Admins')") == "Community Meetings"
    assert js("adminCalendarName('Social Events')") == "Social Events"


class TestNoInvite:
    def test_group_not_invited(self):
        change = patch({"summary": "Movie night [no invite]"})
        assert change["patch"]["attendees"] == [{"email": ALL_EVENTS}]
        assert change["sendUpdates"] == "none"

    def test_marker_in_html_description(self):
        change = patch({"summary": "Movie night", "description": "<b>[no invite]</b>"})
        assert change["patch"]["attendees"] == [{"email": ALL_EVENTS}]

    def test_group_already_invited_stays(self):
        event = {"summary": "Movie night [no invite]",
                 "attendees": [{"email": GROUP}, {"email": ALL_EVENTS}]}
        assert "attendees" not in (patch(event) or {}).get("patch", {})

    def test_no_reminder_unless_tagged(self):
        r = js("reminderLeads(e, null)", e={"summary": "Movie night [no invite]"})
        assert r == {"leads": [], "source": "event"}
        r = js("reminderLeads(e, null)",
               e={"summary": "Movie night [no invite]", "description": "[remind 2 hours]"})
        assert r == {"leads": [2 * 60 * 60 * 1000], "source": "event"}


class TestCommunityReminder:
    def test_goes_to_the_audience(self):
        opts = {"groupEmail": "community-meetings-admins@x.org",
                "groupName": "Community Meetings Admins", "when": "Tue Nov 4, 7:00–8:30 PM",
                "lead": 24 * 60 * 60 * 1000, "zoomUrl": URL, "passcode": "123456",
                "source": "default", "audience": "all@x.org",
                "calendarName": "Community Meetings"}
        event = {"summary": "Community meeting", "location": URL,
                 "htmlLink": "https://cal/e"}
        m = js("reminderMessage(e, o)", e=event, o=opts)
        assert m["to"] == "all@x.org"
        assert m["subject"] == 'Reminder: "Community meeting", Tue Nov 4, 7:00–8:30 PM'
        assert m["body"] == (
            "Hi everyone,\n\n"
            "A reminder of this community event:\n\n"
            '  "Community meeting"\n'
            "  Tue Nov 4, 7:00–8:30 PM\n"
            f"  Join: {URL}\n"
            "  Meeting ID 812 3456 7890, passcode 123456\n"
            "  Calendar event: https://cal/e\n\n"
            "This reminder was sent automatically, 24 hours before the event, for the "
            "Community Meetings calendar. Replies go to its admins.\n")


def community_meeting(title, calendar, start, end):
    return meeting(title, "u3", start * MIN, end * MIN, title=title,
                   groups=[f"{calendar.split()[0].lower()}-admins@x.org"],
                   names=[f"{calendar} admins"], calendars=[calendar], community=True)


class TestCommunityOverlapMessage:
    def test_two_community_calendars(self):
        msg = message(community_meeting("Community meeting", "Community Meetings", 0, 90),
                      community_meeting("Movie night", "Social Events", 60, 120))
        assert msg["to"] == ["community-admins@x.org", "social-admins@x.org"]
        assert msg["cc"] == ["alerts@x.org"]
        assert msg["body"] == (
            "Hi Community Meetings admins and Social Events admins,\n\n"
            "These two meetings overlap:\n"
            '  - "Community meeting" (Community Meetings calendar), 0-90\n'
            '  - "Movie night" (Social Events calendar), 60-120\n\n'
            "Both meetings use the same Zoom user account, which can host only one meeting "
            "at a time, so the second meeting won't be able to start while the first is "
            "still going.\n\n"
            "Your options are:\n"
            "- Hold one of these meetings on a personal or work Zoom account instead: "
            "replace the Zoom link in its calendar event with that account's link, and "
            "delete the room's \"Join Zoom meeting\" details from the event's "
            "description.\n"
            "- Agree between you to move one of these meetings, and change its time on its "
            "calendar. To find a free time, check the All Berkeley Moshav Events "
            "calendar, which shows every group's meetings:\nhttps://cal/all\n\n"
            "This message was sent automatically. Questions? Reply to alerts@x.org.\n")

    def test_one_community_calendar(self):
        msg = message(community_meeting("Community meeting", "Community Meetings", 0, 90),
                      community_meeting("Town hall", "Community Meetings", 100, 120))
        assert msg["to"] == ["community-admins@x.org"]
        assert msg["body"].startswith("Hi Community Meetings admins,\n\n"
                                      "These two meetings are only 10 minutes apart:")
        assert ("- Move one of them to another time on the Community Meetings calendar, "
                "leaving at least 15 minutes between them.") in msg["body"]
        assert "second Zoom user" not in msg["body"]

    def test_community_and_zoom_meeting(self):
        msg = message(community_meeting("Community meeting", "Community Meetings", 0, 90),
                      zoom_meeting("Webinar", 60, 120))
        assert msg["to"] == ["community-admins@x.org", "alerts@x.org"]
        body = msg["body"]
        assert body.startswith("Hi Technology Circle and Community Meetings admins,")
        assert "second Zoom user" not in body
        assert ("for the Community Meetings meeting, replace the Zoom link") in body
        assert ("The Community Meetings meeting can be moved on the Community Meetings "
                "calendar; the other meeting can be moved in Zoom.") in body


class TestNoZoomMarker:
    @pytest.mark.parametrize("event", [
        {"summary": "Picnic [No Zoom]"},
        {"description": "Bring food. [no zoom]"},
        {"description": "<p>Bring food.</p><p>[no&nbsp;zoom]</p>"},
        {"description": "[no&#160;zoom]"},
        {"description": "[no zoom]"},
        {"description": "<b>[no zoom]</b>"},
    ])
    def test_recognized(self, event):
        assert js("hasNoZoomMarker(e)", e=event) is True

    def test_not_in_location(self):
        assert js("hasNoZoomMarker(e)", e={"location": "[no zoom]"}) is False

    def test_formatting_inside_the_tag(self):
        assert js("hasNoZoomMarker(e)", e={"description": "[no <b>zoom</b>]"}) is True

    def test_no_room_added(self):
        assert "description" not in (patch({"description": "[no&nbsp;zoom]"}) or {}).get("patch", {})


class TestCalendarWithoutZoom:
    """A community calendar made with --no-zoom: its footer says "Zoom
    meeting: none", so it has no room, and its events only get guests."""

    FOOTER = build_footer_block("1", "F", "https://g", "Social Events Admins", calendar_id=CAL,
                                no_zoom=True, audience="all@x.org")

    def test_no_room_read(self):
        assert js("parseFooter(f)", f=self.FOOTER) == {
            "calendarId": CAL, "zoomUrl": None, "passcode": None, "audience": "all@x.org",
            "alsoInvite": []}

    def test_event_gets_only_guests(self):
        links = js("parseFooter(f)", f=self.FOOTER)
        ctx = {"zoomUrl": links["zoomUrl"], "passcode": links["passcode"],
               "groupEmail": links["audience"], "allEventsCalendarId": ALL_EVENTS, "faqUrl": None}
        change = patch({"summary": "Picnic", "description": "Bring food."}, ctx)
        assert change == {"patch": {"attendees": [{"email": ALL_EVENTS}, {"email": "all@x.org"}]},
                          "sendUpdates": "all"}


class TestAlsoInvite:
    FOOTER = build_footer_block("1", "F", "https://g", "Community Meetings Admins",
                                calendar_id=CAL, zoom_url=URL, zoom_passcode="123456",
                                audience="all@x.org",
                                also_invite=["neighbors@example.org", "pat@example.com"])
    CTX = {**CTX, "groupEmail": "all@x.org", "alsoInvite": ["neighbors@example.org",
                                                           "pat@example.com"]}

    def test_read(self):
        assert js("parseFooter(f).alsoInvite", f=self.FOOTER) == [
            "neighbors@example.org", "pat@example.com"]

    def test_invited_with_the_audience(self):
        change = patch({"summary": "Town hall"}, self.CTX)
        assert [a["email"] for a in change["patch"]["attendees"]] == [
            ALL_EVENTS, "all@x.org", "neighbors@example.org", "pat@example.com"]
        assert change["sendUpdates"] == "all"

    def test_only_missing_ones_added_and_emailed(self):
        event = {"summary": "Town hall", "attendees": [
            {"email": ALL_EVENTS}, {"email": "all@x.org"}, {"email": "Pat@Example.com"}]}
        change = patch(event, self.CTX)
        assert change["patch"]["attendees"][-1] == {"email": "neighbors@example.org"}
        assert len(change["patch"]["attendees"]) == 4 and change["sendUpdates"] == "all"

    def test_not_with_no_invite(self):
        change = patch({"summary": "Town hall [no invite]"}, self.CTX)
        assert change["patch"]["attendees"] == [{"email": ALL_EVENTS}]
        assert change["sendUpdates"] == "none"
