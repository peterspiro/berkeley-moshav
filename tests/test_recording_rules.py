"""
Tests for group_meetings/recording_rules.gs, the recording links' pure
rules, run under Node with event_rules.gs (skipped if Node isn't
installed).

All names, IDs, and addresses are fictional.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from util.gdrive_config import DEFAULT_DRIVE_ID, MEETING_ADMIN_FOLDER_NAME

DIR = Path(__file__).resolve().parent.parent / "group_meetings"
FILES = [DIR / "event_rules.gs", DIR / "recording_rules.gs"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")

ROOM = "https://us06web.zoom.us/j/81234567890?pwd=enc"
OTHER_ROOM = "https://us06web.zoom.us/j/89999999999?pwd=enc"
COMMUNITY_ROOM = "https://us06web.zoom.us/j/85555555555?pwd=enc"
GROUPS = [
    {"email": "finance@x.org", "name": "Finance Circle", "aliases": ["money@x.org"],
     "calendarId": "c_fin@group.calendar.google.com", "zoomUrl": ROOM, "audience": None},
    {"email": "garden@x.org", "name": "Garden Club", "aliases": [],
     "calendarId": "c_gar@group.calendar.google.com", "zoomUrl": OTHER_ROOM, "audience": None},
    {"email": "finance-audit@x.org", "name": "Finance Circle Audit", "aliases": [],
     "calendarId": "c_aud@group.calendar.google.com", "zoomUrl": None, "audience": None},
    {"email": "cm-admins@x.org", "name": "Community Meetings Admins", "aliases": [],
     "calendarId": "c_cm@group.calendar.google.com", "zoomUrl": COMMUNITY_ROOM,
     "audience": "all.members@x.org", "calendarName": "Community Meetings"},
]
START = "2026-11-01T02:05:00Z"


def js(expr: str, **values):
    """Evaluate expr in a context holding the rules and values (as JSON
    globals); return the result, converted from JSON."""
    prelude = "".join(f"const {k} = {json.dumps(v)};\n" for k, v in values.items())
    files = json.dumps([str(f) for f in FILES])
    program = f"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({{}});
for (const f of {files}) vm.runInContext(fs.readFileSync(f, 'utf8'), ctx);
const out = vm.runInContext({json.dumps(prelude + "JSON.stringify(" + expr + ")")}, ctx);
process.stdout.write(out === undefined ? 'null' : out);
"""
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def rec(meeting_id="87777777777", topic="Some meeting", **extra):
    return {"id": "uuid1", "meetingId": meeting_id, "topic": topic,
            "start": 1761962700000, "minutes": 60, "host": "z@x.org",
            "url": "https://zoom.us/rec/share/abc?pwd=p", "passcode": "x1", **extra}


def categorize(r, event=None, titles=None, choices=None):
    return js("categorizeRecording(r, {groups, event, recurringTitles: titles, choices})",
              r=r, groups=GROUPS, event=event, titles=titles or {}, choices=choices or {})


class TestCategorize:
    def test_a_groups_room(self):
        assert categorize(rec("81234567890")) == {"dest": "finance@x.org", "rule": "room"}

    def test_a_community_room(self):
        assert categorize(rec("85555555555"))["dest"] == "community"

    def test_a_choice_wins(self):
        r = rec("81234567890")
        assert categorize(r, choices={"uuid1": "garden@x.org"})["dest"] == "garden@x.org"

    def test_guests_audience_is_community(self):
        event = {"summary": "Finance Circle", "attendees": [
            {"email": "finance@x.org"}, {"email": "All.Members@x.org"}]}
        assert categorize(rec(), event) == {"dest": "community", "rule": "guests"}

    def test_guests_one_group_by_alias_or_calendar(self):
        assert categorize(rec(), {"attendees": [{"email": "money@x.org"}]})["dest"] == \
            "finance@x.org"
        event = {"attendees": [{"email": "c_gar@group.calendar.google.com"}]}
        assert categorize(rec(), event)["dest"] == "garden@x.org"

    def test_guests_two_groups_fall_through_to_the_title(self):
        event = {"summary": "Garden Club social", "attendees": [
            {"email": "finance@x.org"}, {"email": "garden@x.org"}]}
        assert categorize(rec(), event) == {"dest": "garden@x.org", "rule": "group name"}

    def test_recurring_title_ignores_case_and_spaces(self):
        titles = js("recurringTitleIndex(e)", e=[
            {"title": "BM: Member + Explorer Meetings (1st Sundays)", "dest": "community"}])
        event = {"summary": "bm:  Member + Explorer Meetings (1st Sundays) "}
        assert categorize(rec(), event, titles) == {"dest": "community",
                                                    "rule": "recurring title"}

    def test_recurring_title_from_the_zoom_topic(self):
        titles = {"weekly check-in": ["garden@x.org"]}
        assert categorize(rec(topic="Weekly Check-in"), None, titles)["dest"] == "garden@x.org"

    def test_recurring_title_on_two_sheets_is_uncategorized(self):
        titles = js("recurringTitleIndex(e)", e=[
            {"title": "Check-in", "dest": "garden@x.org"},
            {"title": "check-in", "dest": "finance@x.org"}])
        result = categorize(rec(topic="Check-in Finance Circle"), {"summary": "Check-in"}, titles)
        assert result["dest"] is None
        assert "different sheets" in result["rule"]

    def test_group_name_as_whole_words_longest_wins(self):
        assert categorize(rec(topic="Finance Circle Audit review"))["dest"] == \
            "finance-audit@x.org"
        assert categorize(rec(topic="Finance Circle: budget"))["dest"] == "finance@x.org"
        assert categorize(rec(topic="Finance Circles"))["dest"] is None

    def test_community_calendar_name(self):
        assert categorize(rec(topic="Community Meetings: November"))["dest"] == "community"

    def test_group_name_beats_keyword(self):
        assert categorize(rec(topic="Finance Circle Listening Session"))["dest"] == \
            "finance@x.org"

    def test_keywords(self):
        assert categorize(rec(topic="Listening session on parking")) == {
            "dest": "community", "rule": "keyword"}
        assert categorize(rec(topic="ASK ME ANYTHING with the board"))["dest"] == "community"

    def test_nothing_matches(self):
        assert categorize(rec(topic="Potluck"))["dest"] is None

    def test_recurring_titles_read_only_when_needed(self):
        expr = ("(() => { let calls = 0; const f = () => { calls++; return {}; };"
                " categorizeRecording(r, {groups, recurringTitles: f}); return calls; })()")
        assert js(expr, r=rec("81234567890"), groups=GROUPS) == 0
        assert js(expr, r=rec(), groups=GROUPS) == 1


class TestRecordingEvent:
    def event(self, start, end, location="https://zoom.us/j/87777777777", **extra):
        return {"start": {"dateTime": start}, "end": {"dateTime": end}, "location": location,
                **extra}

    def test_joined_early(self):
        e = self.event("2026-11-01T02:30:00Z", "2026-11-01T03:30:00Z")
        assert js("recordingEvent(r, [e])", r=rec(start=js("Date.parse(s)", s=START)), e=e) == e

    def test_too_early_or_after_its_end(self):
        start = js("Date.parse(s)", s=START)
        early = self.event("2026-11-01T03:00:00Z", "2026-11-01T04:00:00Z")
        over = self.event("2026-11-01T01:00:00Z", "2026-11-01T02:00:00Z")
        assert js("recordingEvent(r, [a, b])", r=rec(start=start), a=early, b=over) is None

    def test_needs_its_zoom_meeting(self):
        start = js("Date.parse(s)", s=START)
        e = self.event("2026-11-01T02:00:00Z", "2026-11-01T03:00:00Z", location="Room 1")
        assert js("recordingEvent(r, [e])", r=rec(start=start), e=e) is None
        e["description"] = '<a href="https://us02web.zoom.us/j/87777777777">Join</a>'
        assert js("recordingEvent(r, [e])", r=rec(start=start), e=e) == e

    def test_closest_start_wins(self):
        start = js("Date.parse(s)", s=START)
        a = self.event("2026-11-01T01:30:00Z", "2026-11-01T03:00:00Z", id="a")
        b = self.event("2026-11-01T02:00:00Z", "2026-11-01T03:00:00Z", id="b")
        assert js("recordingEvent(r, [a, b]).id", r=rec(start=start), a=a, b=b) == "b"


class TestRecordingLine:
    def test_plain_description(self):
        assert js("addRecordingLine('Agenda', r)", r=rec()) == (
            "Recording: https://zoom.us/rec/share/abc?pwd=p (passcode x1)\n\nAgenda")

    def test_html_and_empty_descriptions(self):
        assert js("addRecordingLine('<b>Hi</b>', r)", r=rec()).endswith("<br><br><b>Hi</b>")
        assert js("addRecordingLine('', r)", r=rec()).startswith("Recording: ")

    def test_second_recording_joins_the_first(self):
        first = js("addRecordingLine('Agenda', r)", r=rec())
        second = js("addRecordingLine(d, r)", d=first,
                    r=rec(url="https://zoom.us/rec/share/def", passcode=""))
        assert second == "Recording: https://zoom.us/rec/share/def\n" + first

    def test_already_linked(self):
        d = '<a href="https://zoom.us/rec/share/abc?pwd=p&amp;x=1">rec</a>'
        assert js("addRecordingLine(d, r)", d=d, r=rec()) is None


class TestFromZoom:
    def test_reads_a_recording(self):
        m = {"uuid": "u==", "id": 81234567890, "topic": "T", "start_time": START,
             "duration": 75, "share_url": "https://zoom.us/rec/share/abc",
             "password": "x1", "recording_play_passcode": "enc+code"}
        assert js("recordingFromZoom(m, 'Z@X.org')", m=m) == {
            "id": "u==", "meetingId": "81234567890", "topic": "T",
            "start": js("Date.parse(s)", s=START), "minutes": 75, "host": "z@x.org",
            "url": "https://zoom.us/rec/share/abc?pwd=enc%2Bcode", "passcode": "x1"}

    def test_ready_once_every_file_is(self):
        assert js("recordingReady(m)", m={"recording_files": [
            {"status": "completed"}, {}]}) is True
        assert js("recordingReady(m)", m={"recording_files": [
            {"status": "completed"}, {"status": "processing"}]}) is False
        assert js("recordingReady(m)", m={}) is False

    def test_uuid_path(self):
        assert js("uuidPath('abc==')") == "abc%3D%3D"
        assert js("uuidPath('/ab//c')") == "%252Fab%252F%252Fc"


class TestSheets:
    def test_row(self):
        assert js("recordingRow(r, 'Title').slice(1)", r=rec(minutes=65)) == [
            "Title", "https://zoom.us/rec/share/abc?pwd=p", "x1", "1:05", "Some meeting", "uuid1"]

    def test_headings_match_rows(self):
        assert js("RECORDINGS_HEADINGS.length") == len(js("recordingRow(r, 't')", r=rec()))
        assert js("UNASSIGNED_HEADINGS[UNASSIGNED_WHY_COLUMN]") == "Why"

    def test_override(self):
        for typed, dest in [("Finance Circle", "finance@x.org"), (" money@X.org ", "finance@x.org"),
                            ("Community", "community"), ("community meetings", "community"),
                            ("Community Meetings Admins", "community"), ("Nope", None),
                            ("", None)]:
            assert js("overrideDestination(t, groups)", t=typed, groups=GROUPS) == dest, typed



class TestReport:
    ITEM = {"title": "Finance Circle", "when": "Sat Oct 31, 7:05 PM\u20138:05 PM", "topic": "F",
            "host": "z@x.org", "url": "https://zoom.us/rec/share/abc", "rule": "room",
            "sheet": "Finance Circle Meeting Recordings", "sheetUrl": "https://s/fin",
            "unassigned": False, "event": "link added", "doc": "no tab for 2026-10-31"}
    LOST = {**ITEM, "title": "Potluck", "rule": "no rule matched its title", "unassigned": True,
            "sheet": "Community Meeting Recordings, Unassigned tab", "sheetUrl": "https://s/com",
            "event": "no event found", "doc": None}

    def report(self, items, backfill=False):
        return js("recordingsReport(items, {alertEmail: 'a@x.org', backfill: b})",
                  items=items, b=backfill)

    def test_says_how_each_was_assigned(self):
        r = self.report([self.ITEM])
        assert r["to"] == "a@x.org"
        assert r["subject"] == "1 Zoom recording linked"
        body = r["body"]
        assert "Sheet: Finance Circle Meeting Recordings (held in its Zoom room)" in body
        assert "Calendar event: link added" in body
        assert "Agenda doc: no tab for 2026-10-31" in body
        assert "- Finance Circle Meeting Recordings: https://s/fin" in body
        assert "Group column" not in body

    def test_unassigned_first_with_how_to_assign(self):
        r = self.report([self.ITEM, self.LOST], backfill=True)
        assert r["subject"] == "Recordings backfill: 2 Zoom recordings linked, 1 unassigned"
        body = r["body"]
        assert body.index("Potluck") < body.index('"Finance Circle"')
        assert ("UNASSIGNED: Community Meeting Recordings, Unassigned tab "
                "(no rule matched its title)") in body
        assert "Agenda doc" not in body.split('"Finance Circle"')[0]
        assert "Group column" in body
        assert "- Community Meeting Recordings: https://s/com" in body

    def test_moved_and_already_listed(self):
        moved = {**self.ITEM, "moved": True, "event": None, "doc": None,
                 "sheet": "Garden Club Meeting Recordings (already listed)"}
        body = self.report([moved])["body"]
        assert "(already listed) (moved off the Unassigned tab)" in body
        assert "- Garden Club Meeting Recordings: https://s/fin" in body

    def test_describe_rule(self):
        assert js("describeRule('keyword')") == \
            'its title says "listening session" or "ask me anything"'
        assert js("describeRule('something else')") == "something else"


def test_month_windows():
    now = js("Date.parse('2026-02-10T00:00:00Z')")
    assert js("monthWindows('2025-11-15', now)", now=now) == [
        {"month": "2025-11", "from": "2025-11-01", "to": "2025-11-30"},
        {"month": "2025-12", "from": "2025-12-01", "to": "2025-12-31"},
        {"month": "2026-01", "from": "2026-01-01", "to": "2026-01-31"},
        {"month": "2026-02", "from": "2026-02-01", "to": "2026-02-28"},
    ]


def test_format_length():
    assert js("[formatLength(0), formatLength(59.6), formatLength(125)]") == [
        "0:00", "1:00", "2:05"]


def test_project_compiles_together():
    """The project's files share one scope: they must parse, with no name
    declared twice."""
    files = json.dumps([str(f) for f in sorted(DIR.glob("*.gs"))])
    program = f"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({{}});
for (const f of {files}) vm.runInContext(fs.readFileSync(f, 'utf8'), ctx, {{filename: f}});
"""
    subprocess.run(["node", "-e", program], check=True)


def test_shared_drive_matches_python():
    text = (DIR / "recordings.gs").read_text()
    assert f"const SHARED_DRIVE_ID = '{DEFAULT_DRIVE_ID}';" in text
    assert f"const MEETING_ADMIN_FOLDER_NAME = '{MEETING_ADMIN_FOLDER_NAME}';" in text
