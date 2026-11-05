"""
Tests for circle_agendas/structure_rules.gs, the Circle Structure's rules,
run under Node (skipped if Node isn't installed).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent / "circle_agendas"
# structure_rules.gs uses ROLE_PLACEHOLDER from agenda_rules.gs.
FILES = [PROJECT / "agenda_rules.gs", PROJECT / "structure_rules.gs"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


def js(expr: str, **values):
    """Evaluate expr in a context holding the project's rules, with values
    (converted to JSON) as globals; return the result, converted from JSON."""
    program = f"""
const vm = require('vm'), fs = require('fs');
const ctx = vm.createContext({json.dumps(values)});
for (const f of {json.dumps([str(f) for f in FILES])}) vm.runInContext(fs.readFileSync(f, 'utf8'), ctx);
const out = vm.runInContext({json.dumps("JSON.stringify(" + expr + ")")}, ctx);
process.stdout.write(out === undefined ? 'null' : out);
"""
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def group(gid, name, gtype="Circle", parent=""):
    return {"id": gid, "name": name, "type": gtype, "parent": parent}


# Full Community > Land > (Garden WG > Compost WG), Tech; Admin unplaced.
GROUPS = [
    group("1", "Full Community", parent="Top of the structure"),
    group("2", "Land", parent="Full Community"),
    group("3", "Garden", "Working Group", "Land"),
    group("4", "Compost", "Working Group", "Garden"),
    group("5", "Tech", parent="Full Community"),
    group("6", "Admin", parent=""),
    group("7", "Chess", "Not in the structure", "Land"),
]
APPLIED = {"1": "top", "2": "1", "3": "2", "4": "3", "5": "1", "6": ""}


def resolve(groups, last=None):
    return js("resolveParents(groups, last)", groups=groups, last=last or {})


def with_parent(gid, parent):
    return [dict(g, parent=parent) if g["id"] == gid else g for g in GROUPS]


class TestGuessGroupType:
    @pytest.mark.parametrize("name, item, gtype", [
        ("Technology", {"name": "Technology Circle"}, "Circle"),
        ("Garden", {"name": "Garden Working Group"}, "Working Group"),
        ("Garden Working Group", {"name": "Garden stuff"}, "Working Group"),
        ("Garden", {"name": "Garden Work Group"}, "Working Group"),
        ("Welcome", {"name": "Welcome Team"}, "Working Group"),
        ("Garden Pod", {"name": "Garden Pod"}, "Working Group"),
        ("DFL", {"name": "Development, Finance, & Legal Circle (DF&L)"}, "Circle"),
        ("Welcome", {"name": "Welcome Team (WT) "}, "Working Group"),
        ("Garden", {"name": "Garden Working  Group (GWG)"}, "Working Group"),
        ("Chess", {"name": "Chess Club (CC)"}, "Not in the structure"),
        ("Odd", {"name": "Circle of Friends"}, "Not in the structure"),
        ("Odd", {"name": "Teamwork"}, "Not in the structure"),
        ("Odd", {"name": "Circle (old) stuff"}, "Not in the structure"),
        ("Chess", {"name": "Chess Club"}, "Not in the structure"),
        ("Chess", {"name": "Chess", "inClubs": True}, "Not in the structure"),
        ("Everyone", {"name": "Everyone", "isSharedDrive": True}, "Not in the structure"),
        ("Residents", {"name": "Residents Circle", "isSharedDrive": True}, "Circle"),
        ("Full Community", {"name": "Moshav", "isSharedDrive": True}, "Circle"),
        ("Odd", {"name": "Odd stuff"}, "Not in the structure"),
        ("Odd", None, "Not in the structure"),
    ])
    def test_types(self, name, item, gtype):
        assert js("guessGroupType(name, item).type", name=name, item=item) == gtype

    def test_says_why_out(self):
        reason = js("guessGroupType('Odd', {name: 'Odd stuff'}).reason")
        assert "Odd stuff" in reason and "rename the folder" in reason


class TestResolveParents:
    def test_valid_parents(self):
        result = resolve(GROUPS, APPLIED)
        assert result["parentOf"] == APPLIED
        assert result["refused"] == []

    def test_names_ignore_case_and_spaces(self):
        assert resolve(with_parent("5", "  land "), APPLIED)["parentOf"]["5"] == "2"

    def test_not_in_structure_gets_no_parent(self):
        assert "7" not in resolve(GROUPS, APPLIED)["parentOf"]

    def test_circle_under_working_group_refused(self):
        result = resolve(with_parent("5", "Garden"), APPLIED)
        assert result["parentOf"]["5"] == "1"
        assert result["refused"][0]["id"] == "5"
        assert "only be under a circle" in result["refused"][0]["reason"]
        assert result["refused"][0]["kept"] == "1"

    def test_working_group_under_working_group(self):
        assert resolve(with_parent("4", "Garden"), APPLIED)["parentOf"]["4"] == "3"

    def test_unknown_name_refused(self):
        result = resolve(with_parent("5", "Nope"), APPLIED)
        assert result["parentOf"]["5"] == "1"
        assert "no circle or working group" in result["refused"][0]["reason"]

    def test_loop_refused_and_parent_kept(self):
        # Land under Compost, which is beneath it. (A circle can't be under a
        # working group anyway, so make the loop with circles: Land under Tech,
        # and Tech under Land.)
        groups = with_parent("2", "Tech")
        groups = [dict(g, parent="Land") if g["id"] == "5" else g for g in groups]
        result = resolve(groups, APPLIED)
        assert result["parentOf"]["2"] == "1" and result["parentOf"]["5"] == "1"
        assert {r["id"] for r in result["refused"]} == {"2", "5"}
        assert "beneath itself" in result["refused"][0]["reason"]

    def test_only_the_changed_parent_of_a_loop_is_refused(self):
        groups = with_parent("3", "Compost")
        result = resolve(groups, APPLIED)
        assert result["parentOf"]["3"] == "2"
        assert [r["id"] for r in result["refused"]] == ["3"]

    def test_old_loop_is_broken(self):
        groups = with_parent("3", "Compost")
        result = resolve(groups, dict(APPLIED, **{"3": "4"}))
        assert result["parentOf"]["3"] == "" and result["parentOf"]["4"] == ""

    def test_kept_parent_that_is_gone_leaves_it_unplaced(self):
        result = resolve(with_parent("5", "Nope"), dict(APPLIED, **{"5": "99"}))
        assert result["parentOf"]["5"] == ""

    def test_root_always_on_top(self):
        groups = [dict(g, parent="Land") if g["id"] == "1" else g for g in GROUPS]
        assert resolve(groups, APPLIED)["parentOf"]["1"] == "top"

    def test_only_root_on_top(self):
        result = resolve(with_parent("5", "Top of the structure"), APPLIED)
        assert result["parentOf"]["5"] == "1"
        assert "only Full Community" in result["refused"][0]["reason"]

    def test_own_parent_refused(self):
        result = resolve(with_parent("5", "Tech"), APPLIED)
        assert result["parentOf"]["5"] == "1"


class TestTree:
    def tree(self, groups=GROUPS):
        return js("buildTree(groups, resolveParents(groups, {}).parentOf)", groups=groups)

    def test_tree(self):
        tree = self.tree()
        assert tree["rootId"] == "1"
        assert tree["children"]["1"] == ["2", "5"]
        assert tree["children"]["3"] == ["4"]
        assert set(tree["placed"]) == {"1", "2", "3", "4", "5"}
        assert tree["unplaced"] == ["6"]

    def test_subtree_of_unplaced_is_unplaced(self):
        tree = self.tree(with_parent("2", ""))
        assert set(tree["placed"]) == {"1", "5"}
        assert tree["unplaced"] == ["6", "2"]

    def test_outline(self):
        out = js("outline(buildTree(groups, resolveParents(groups, {}).parentOf))", groups=GROUPS)
        assert out["placed"] == [{"id": "1", "depth": 0}, {"id": "2", "depth": 1},
                                 {"id": "3", "depth": 2}, {"id": "4", "depth": 3},
                                 {"id": "5", "depth": 1}]
        assert out["unplaced"] == [{"id": "6", "depth": 0}]

    def test_ancestors(self):
        assert js("ancestorsOf('4', last)", last=APPLIED) == ["1", "2", "3"]
        assert js("ancestorsOf('1', last)", last=APPLIED) == []


class TestParentChoices:
    def choices(self, gid, groups=GROUPS):
        return js("""(() => {
          const tree = buildTree(groups, resolveParents(groups, {}).parentOf);
          return parentChoices(groups.find(g => g.id === id), groups, tree.children);
        })()""", groups=groups, id=gid)

    def test_circle_choices_are_circles_not_beneath_it(self):
        assert self.choices("2") == ["Admin", "Full Community", "Tech"]

    def test_working_group_choices_include_working_groups(self):
        assert self.choices("4") == ["Admin", "Full Community", "Garden", "Land", "Tech"]

    def test_working_group_not_under_its_own_working_groups(self):
        assert self.choices("3") == ["Admin", "Full Community", "Land", "Tech"]

    def test_root(self):
        assert self.choices("1") == ["Top of the structure"]

    def test_out(self):
        assert self.choices("7") == []


class TestGroupProblem:
    def problem(self, gid, groups=GROUPS, reason=""):
        return js("""(() => {
          const parentOf = resolveParents(groups, {}).parentOf;
          const byId = {};
          for (const g of groups) byId[g.id] = g;
          return groupProblem(byId[id], reason, parentOf, buildTree(groups, parentOf), byId);
        })()""", groups=groups, id=gid, reason=reason)

    def test_placed(self):
        assert self.problem("4") == ""

    def test_unplaced(self):
        assert self.problem("6") == "Not yet placed: choose its parent."

    def test_under_unplaced(self):
        assert self.problem("3", with_parent("2", "")) == (
            "Not yet placed: Land, above it, isn't placed yet.")

    def test_out(self):
        assert self.problem("7", reason="it's a club") == "Not in the structure: it's a club"


class TestStructureRows:
    PLACE = {
        "status": "placed",
        "parent": {"name": "Land", "url": "u2"},
        "children": [{"name": "Compost", "url": "u4"}, {"name": "Seeds", "url": None}],
        "structureUrl": "sheet",
        "structureDocUrl": "doc",
    }

    def text(self, paragraphs):
        return ["".join(run[0] for run in p["runs"]) for p in paragraphs]

    def test_placed(self):
        rows = js("structureRows(place)", place=self.PLACE)
        assert self.text(rows["parentLabel"]) == [
            "Parent Circle", "(Managed here. See Circle\u00a0Structure.)"]
        links = {text: style["link"] for text, style in rows["parentLabel"][1]["runs"]}
        assert links["here"] == "sheet" and links["Circle\u00a0Structure"] == "doc"
        assert links["(Managed "] is None
        assert all(style["small"] for _, style in rows["parentLabel"][1]["runs"])
        assert self.text(rows["parentValue"]) == ["Land"]
        assert rows["parentValue"][0]["runs"][0][1]["link"] == "u2"
        assert self.text(rows["subValue"]) == ["Compost", "Seeds"]
        assert all(p["bullet"] for p in rows["subValue"])

    @pytest.mark.parametrize("status, text", [
        ("top", "None: this is the top circle."),
        ("unplaced", "Not yet placed in the Circle Structure."),
        ("out", "Not in the Circle Structure.")])
    def test_not_placed(self, status, text):
        rows = js("structureRows(place)", place=dict(self.PLACE, status=status, children=[]))
        assert self.text(rows["parentValue"]) == [text]
        assert self.text(rows["subValue"]) == ["None"]


class TestSignature:
    def test_same_however_split(self):
        written = [{"runs": [["Full ", {"link": "u1"}], ["Community", {"link": "u1"}],
                             [" › ", {}], ["Land", {"link": "u2"}]]},
                   {"runs": [["Next", {}]]}]
        read = [{"runs": [["Full Community", {"link": "u1"}], [" ›  ", {}], ["Land", {"link": "u2"}]]},
                {"runs": [["Next ", {}]]}]
        assert js("paragraphsSignature(a) === paragraphsSignature(b)", a=written, b=read)

    def test_changed_link(self):
        a = [{"runs": [["Land", {"link": "u2"}]]}]
        b = [{"runs": [["Land", {"link": "u3"}]]}]
        assert not js("paragraphsSignature(a) === paragraphsSignature(b)", a=a, b=b)

    def test_no_break_space_differs(self):
        a = [{"runs": [["Circle Structure", {}]]}]
        b = [{"runs": [["Circle\u00a0Structure", {}]]}]
        assert not js("paragraphsSignature(a) === paragraphsSignature(b)", a=a, b=b)

    def test_changed_text(self):
        a = [{"runs": [["Land", {}]]}]
        b = [{"runs": [["Lands", {}]]}]
        assert not js("paragraphsSignature(a) === paragraphsSignature(b)", a=a, b=b)


def test_shared_drive_id_matches_python():
    from util.gdrive_config import DEFAULT_DRIVE_ID
    text = (PROJECT / "structure.gs").read_text()
    assert f"const SHARED_DRIVE_ID = '{DEFAULT_DRIVE_ID}';" in text


@pytest.mark.parametrize("aliases, address", [
    ([], "garden-working-group@x.org"),
    (["garden@x.org"], "garden@x.org"),
    (["garden@x.org.test-google-a.com", "g@other.org"], "garden-working-group@x.org"),
    (["longer-than-the-primary-address@x.org"], "garden-working-group@x.org")])
def test_shortest_address(aliases, address):
    assert js("shortestAddress('garden-working-group@x.org', aliases, 'x.org')",
              aliases=aliases) == address


def test_columns_by_heading():
    row = ["Group", "Type", "Parent", " email list ", "", "Gather ID"]
    assert js("columnsByHeading(row, ['Group', 'Parent', 'Email list', 'Gather ID', 'Leader'])",
              row=row) == {"Group": 1, "Parent": 3, "Email list": 4, "Gather ID": 6, "Leader": 0}


def test_guests():
    assert js("guestsOf(m, everyone, ['admin@x.org'])",
              m=["Bo@Gmail.com", "ann@x.org", "admin@x.org", "zed@y.org", "bo@gmail.com"],
              everyone=["ANN@x.org"]) == ["bo@gmail.com", "zed@y.org"]


class TestMeetingTimes:
    @pytest.mark.parametrize("rule, days", [
        ("RRULE:FREQ=WEEKLY;BYDAY=TU", "Tue"),
        ("RRULE:FREQ=WEEKLY", "Wed"),
        ("RRULE:FREQ=WEEKLY;WKST=SU;BYDAY=TH,MO", "Mon & Thu"),
        ("RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=20270101T000000Z", "Mon, Wed & Fri"),
        ("RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU", "Every other Tue"),
        ("RRULE:FREQ=WEEKLY;INTERVAL=3;BYDAY=TU", "Every 3 weeks, Tue"),
        ("RRULE:FREQ=MONTHLY;BYDAY=2TU,4TU", "2nd & 4th Tue"),
        ("RRULE:FREQ=MONTHLY;BYDAY=-1SU", "Last Sun of month"),
        ("RRULE:FREQ=MONTHLY;BYDAY=-1SU,1SU", "1st & last Sun of month"),
        ("RRULE:FREQ=MONTHLY;BYDAY=TU;BYSETPOS=3", "3rd Tue"),
        ("RRULE:FREQ=MONTHLY;BYMONTHDAY=15", "15th of month"),
        ("RRULE:FREQ=DAILY", "Daily"),
        ("RRULE:FREQ=MONTHLY;BYDAY=2TU,4WE", None),
        ("RRULE:FREQ=MONTHLY;INTERVAL=2;BYDAY=2TU", None),
        ("RRULE:FREQ=YEARLY", None),
        ("RRULE:FREQ=WEEKLY;BYMONTH=6;BYDAY=TU", None),
        (None, None),
    ])
    def test_rule_days(self, rule, days):
        assert js("ruleDays(rule, 3)", rule=rule) == days

    @pytest.mark.parametrize("start, end, text", [
        (1170, 1260, "7:30–9 pm"), (660, 750, "11 am–12:30 pm"), (1140, 1200, "7–8 pm"),
        (540, 600, "9–10 am"), (720, 780, "12–1 pm")])
    def test_clock_range(self, start, end, text):
        assert js("clockRange(s, e)", s=start, e=end) == text

    def test_series_in_order_and_fallback(self):
        series = [
            {"rule": "RRULE:FREQ=MONTHLY;BYDAY=-1SU", "weekday": 0, "start": 960, "end": 1050,
             "next": {"date": "2026-11-29", "start": 960, "end": 1050}},
            {"rule": "RRULE:FREQ=WEEKLY;BYDAY=TU", "weekday": 2, "start": 1170, "end": 1260,
             "next": {"date": "2026-11-10", "start": 1170, "end": 1260}},
            {"rule": "RRULE:FREQ=YEARLY", "weekday": 4, "start": 1140, "end": 1230,
             "next": {"date": "2026-11-12", "start": 1140, "end": 1230}},
        ]
        assert js("meetingTimesText(s, o)", s=series, o=[{"date": "2026-11-06", "start": 600,
                                                          "end": 660}]) == (
            "Last Sun of month 4–5:30 pm\nTue 7:30–9 pm\nNext: Nov 12, 7–8:30 pm")

    def test_sorted_by_text_numbers_by_value(self):
        def s(rule, date):
            return {"rule": rule, "weekday": 1, "start": 990, "end": 1080,
                    "next": {"date": date, "start": 990, "end": 1080}}
        series = [s("RRULE:FREQ=MONTHLY;BYDAY=3MO", "2026-11-16"),
                  s("RRULE:FREQ=MONTHLY;BYDAY=10MO", "2026-11-01"),
                  s("RRULE:FREQ=MONTHLY;BYDAY=1MO", "2026-12-07")]
        series[1]["rule"] = "RRULE:FREQ=WEEKLY;BYDAY=MO"
        assert js("meetingTimesText(s, [])", s=series) == (
            "1st Mon 4:30–6 pm\n3rd Mon 4:30–6 pm\nMon 4:30–6 pm")

    def test_one_offs_only_when_no_series(self):
        o = [{"date": "2026-12-01", "start": 1140, "end": 1200},
             {"date": "2026-11-12", "start": 1140, "end": 1230}]
        assert js("meetingTimesText([], o)", o=o) == "Next: Nov 12, 7–8:30 pm"
        assert js("meetingTimesText([], [])") == ""


def test_heading_width_grows_with_text():
    widths = js("['Guests', 'Email list', 'Feedback Link', 'Drive folder'].map(headingWidth)")
    assert widths[0] < widths[1] < widths[3] < widths[2]
    assert 95 <= widths[2] <= 125


def test_all_events_calendar_matches_python():
    from util.meeting_rooms import ALL_EVENTS_CALENDAR_ID
    text = (PROJECT / "structure.gs").read_text()
    assert f"'{ALL_EVENTS_CALENDAR_ID}'" in text
