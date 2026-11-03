"""
Tests for circle_agendas/agenda_rules.gs, the agenda arithmetic, run under
Node (skipped if Node isn't installed).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from util.google_group_footer import ITEM_TYPE_SHARED_DRIVE, build_footer_block

RULES = Path(__file__).resolve().parent.parent / "circle_agendas" / "agenda_rules.gs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


def js(expr: str):
    """Evaluate expr in a context holding agenda_rules.gs; return the result,
    converted from JSON."""
    program = f"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({{}});
vm.runInContext(fs.readFileSync({json.dumps(str(RULES))}, 'utf8'), ctx);
const out = vm.runInContext({json.dumps("JSON.stringify(" + expr + ")")}, ctx);
process.stdout.write(out === undefined ? 'null' : out);
"""
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


class TestScheduleTimes:
    def test_closing_round_gets_the_time_left(self):
        # 19:30, 90 minutes, items of 10, 3, 2, 3, 20, 20, 20, 5.
        result = js("scheduleTimes(1170, [10, 3, 2, 3, 20, 20, 20, 5], 90)")
        assert [js(f"formatTime({t})") for t in result["starts"]] == [
            "7:30", "7:40", "7:43", "7:45", "7:48", "8:08", "8:28", "8:48"]
        assert js(f"formatTime({result['closingStart']})") == "8:53"
        assert result["closingMinutes"] == 7
        assert js(f"formatTime({result['end']})") == "9:00"

    def test_overrun_gives_negative_closing(self):
        assert js("scheduleTimes(600, [50, 20], 60)")["closingMinutes"] == -10

    def test_no_items(self):
        assert js("scheduleTimes(600, [], 30)") == {
            "starts": [], "closingStart": 600, "closingMinutes": 30, "end": 630}


class TestFormatTime:
    @pytest.mark.parametrize("minutes, text", [
        (0, "12:00"), (545, "9:05"), (720, "12:00"), (735, "12:15"), (1170, "7:30"),
        (1440 + 30, "12:30")])
    def test_formats(self, minutes, text):
        assert js(f"formatTime({minutes})") == text


class TestParsing:
    @pytest.mark.parametrize("text, minutes", [
        ("19:30", 1170), (" 9:05 ", 545), ("7:30 pm", 1170), ("7:30PM", 1170),
        ("7 p.m.", 1140), ("12:15 am", 15), ("12 pm", 720),
        ("25:00", None), ("19", None), ("13 pm", None), ("9:75", None), ("soon", None),
        ("", None)])
    def test_parse_time(self, text, minutes):
        assert js(f"parseTime({json.dumps(text)})") == minutes

    @pytest.mark.parametrize("text, minutes", [
        ("20", 20), (" 5 min", 5), ("10 minutes", 10),
        ("about 10", None), ("1.5", None), ("", None)])
    def test_parse_minutes(self, text, minutes):
        assert js(f"parseMinutes({json.dumps(text)})") == minutes

    @pytest.mark.parametrize("text, minutes", [
        ("1.5", 90), ("2 h", 120), ("0.75 hours", 45), ("1 hour", 60),
        ("1:30", 90), (" 0:45 ", 45), ("2:00", 120),
        ("1:75", None), ("1:3", None), ("90 min", None), ("", None)])
    def test_parse_hours(self, text, minutes):
        assert js(f"parseHours({json.dumps(text)})") == minutes


class TestHeaderTimeText:
    @pytest.mark.parametrize("line, text", [
        ("Time  19:30     Place  ", "19:30"),
        ("Time 7:30 pm Place Zoom", "7:30 pm"),
        ("Time 19:30", "19:30"),
        ("Time  19:30     Duration (hours)  1.5     Place  Zoom room", "19:30"),
        ("Time  Place", ""),
        ("When?", "")])
    def test_reads_time(self, line, text):
        assert js(f"headerTimeText({json.dumps(line)})") == text

    @pytest.mark.parametrize("line, text", [
        ("Time  19:30     Duration (hours)  1.5     Place  Zoom room", "1.5"),
        ("Time 19:30 Duration 1:30", "1:30"),
        ("Time 19:30 Place Zoom", ""),
        ("Time  19:30     Duration (hours)     Place", "")])
    def test_reads_hours(self, line, text):
        assert js(f"headerHoursText({json.dumps(line)})") == text


class TestMeetingIdFromUrl:
    @pytest.mark.parametrize("url, meeting_id", [
        ("https://script.google.com/macros/s/X/exec?action=times&doc=D&meeting=ab12cd34",
         "ab12cd34"),
        ("https://x/exec?meeting=a-b_c&action=item", "a-b_c"),
        ("https://x/exec?action=times&doc=D", None),
        ("", None)])
    def test_reads_id(self, url, meeting_id):
        assert js(f"meetingIdFromUrl({json.dumps(url)})") == meeting_id


class TestFormatHours:
    @pytest.mark.parametrize("minutes, text", [(90, "1.5"), (120, "2"), (45, "0.75"), (100, "1.67")])
    def test_formats(self, minutes, text):
        assert js(f"formatHours({minutes})") == text


class TestParseCircleFooter:
    CAL = "c_abc123@group.calendar.google.com"
    ZOOM = "https://us06web.zoom.us/j/81234567890?pwd=enc"

    def test_reads_python_written_folder_footer(self):
        footer = "Notes\n\n" + build_footer_block(
            "1093", "FOLDER_id-1", "https://g", "Technology Circle",
            calendar_id=self.CAL, zoom_url=self.ZOOM, zoom_passcode="123456")
        assert js(f"parseCircleFooter({json.dumps(footer)})") == {
            "name": "Technology Circle", "driveItemId": "FOLDER_id-1",
            "calendarId": self.CAL, "zoomUrl": self.ZOOM}

    def test_reads_shared_drive_footer_without_calendar(self):
        footer = build_footer_block("7", "0AFqDRIVE", "https://g", "Garden Pod",
                                    item_type=ITEM_TYPE_SHARED_DRIVE)
        assert js(f"parseCircleFooter({json.dumps(footer)})") == {
            "name": "Garden Pod", "driveItemId": "0AFqDRIVE", "calendarId": None,
            "zoomUrl": None}

    def test_no_managed_block(self):
        assert js("parseCircleFooter('Just notes')") == {
            "name": None, "driveItemId": None, "calendarId": None, "zoomUrl": None}


class TestAboutReorder:
    OLD = ["Aims & Domain", "Parent Circle", "Sub-Circles", "Zoom room",
           "Circle email address", "Google Drive Folder", "Policies folder",
           "Other Key Links"]

    def test_old_drive_folder_labels_get_the_new_name(self):
        assert js("aboutLabel('Circle Google Drive Folder')") == "Google Drive Folder"
        assert js("aboutLabel('Circle Drive Folder\\nmore')") == "Google Drive Folder"
        assert js("aboutLabel(' Zoom room ')") == "Zoom room"

    def test_moves_only_the_rows_the_scripts_fill_in(self):
        assert js(f"aboutReorder({json.dumps(self.OLD)})")["move"] == [
            "Parent Circle", "Sub-Circles", "Zoom room", "Circle email address",
            "Google Drive Folder"]

    def test_moving_them_gives_the_new_order(self):
        plan = js(f"aboutReorder({json.dumps(self.OLD)})")
        kept = [label for label in self.OLD if label not in plan["move"]]
        # A doc as old as OLD lacks the rows added later.
        target = [label for label in js("ABOUT_LABELS") if label not in js("ABOUT_ADDED_LATER")]
        assert [label for label in target if label not in plan["move"]] == kept

    def test_already_in_order(self):
        assert js("aboutReorder(ABOUT_LABELS)") == {"move": [], "order": js("ABOUT_LABELS")}

    def test_unexpected_rows(self):
        labels = self.OLD[:-1] + ["Notes"]
        error = js(f"aboutReorder({json.dumps(labels)})")["error"]
        assert "missing: Other Key Links" in error and "unexpected: Notes" in error

    def test_rows_added_later_may_be_missing(self):
        assert js("aboutReorder(ABOUT_LABELS.filter(l => !ABOUT_ADDED_LATER.includes(l)))")[
            "move"] == []
        plan = js(f"aboutReorder({json.dumps(self.OLD)})")
        assert "error" not in plan
        assert not set(plan["move"]) & {"Circle calendar", "Remind before meetings"}
        assert "Circle calendar" not in plan["order"]

    def test_new_rows_come_below_zoom_room(self):
        labels = js("ABOUT_LABELS")
        i = labels.index("Zoom room")
        assert labels[i + 1:i + 3] == ["Circle calendar", "Remind before meetings"]

    def test_where_to_add_the_new_rows(self):
        layout2 = js("ABOUT_LABELS.filter(l => !ABOUT_ADDED_LATER.includes(l))")
        adds = js(f"aboutRowsToAdd({json.dumps(layout2)})")
        assert adds == [{"label": "Circle calendar", "index": 3},
                        {"label": "Remind before meetings", "index": 4}]
        labels = list(layout2)
        for add in adds:
            labels.insert(add["index"], add["label"])
        assert labels == js("ABOUT_LABELS")
        assert js("aboutRowsToAdd(ABOUT_LABELS)") == []
        with_calendar = [l for l in js("ABOUT_LABELS") if l != "Remind before meetings"]
        assert js(f"aboutRowsToAdd({json.dumps(with_calendar)})") == [
            {"label": "Remind before meetings", "index": 4}]
        assert "Zoom room" in js(
            f"aboutRowsToAdd({json.dumps([l for l in layout2 if l != 'Zoom room'])})")["error"]

    def test_calendar_url_matches_the_footer(self):
        from util.google_group_footer import calendar_url
        for cal in ["c_abc123@group.calendar.google.com", "a?>", "ab", "abc", "abcd",
                    "share@berkeleymoshav.org"]:
            assert js(f"calendarUrl({json.dumps(cal)})") == calendar_url(cal)

    def test_reminder_default_and_examples_parse_as_meant(self):
        # The default, and each example in the label's grey line, reads as
        # the setting it shows.
        group_rules = RULES.parent.parent / "group_meetings" / "event_rules.gs"
        hint = js("REMINDER_HINT")
        examples = hint.split("e.g. ")[1].split("; ")
        assert examples[-1] == "none"
        texts = [js("REMINDER_DEFAULT_TEXT")] + examples
        program = f"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({{}});
vm.runInContext(fs.readFileSync({json.dumps(str(group_rules))}, 'utf8'), ctx);
process.stdout.write(JSON.stringify({json.dumps(texts)}.map(t =>
  vm.runInContext('parseReminderLeads(' + JSON.stringify(t) + ')', ctx))));
"""
        out = json.loads(subprocess.run(["node", "-e", program], capture_output=True, text=True,
                                        check=True).stdout)
        hour = 60 * 60 * 1000
        assert out == [{"leads": [24 * hour]}, {"leads": [48 * hour]}, {"leads": [90 * 60 * 1000]},
                       {"leads": [48 * hour, hour]}, {"leads": []}]

    def test_hand_filled_rows_out_of_order(self):
        labels = ["Policies folder"] + [l for l in self.OLD if l != "Policies folder"]
        assert "another order" in js(f"aboutReorder({json.dumps(labels)})")["error"]
