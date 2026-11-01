"""
Tests for circle_agendas/agenda_rules.gs, the agenda arithmetic, run under
Node (skipped if Node isn't installed).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

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
            "19:30", "19:40", "19:43", "19:45", "19:48", "20:08", "20:28", "20:48"]
        assert js(f"formatTime({result['closingStart']})") == "20:53"
        assert result["closingMinutes"] == 7
        assert js(f"formatTime({result['end']})") == "21:00"

    def test_overrun_gives_negative_closing(self):
        assert js("scheduleTimes(600, [50, 20], 60)")["closingMinutes"] == -10

    def test_no_items(self):
        assert js("scheduleTimes(600, [], 30)") == {
            "starts": [], "closingStart": 600, "closingMinutes": 30, "end": 630}


class TestFormatTime:
    @pytest.mark.parametrize("minutes, text", [
        (0, "0:00"), (545, "9:05"), (1170, "19:30"), (1440 + 30, "0:30")])
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
        ("90 min", None), ("", None)])
    def test_parse_hours(self, text, minutes):
        assert js(f"parseHours({json.dumps(text)})") == minutes
