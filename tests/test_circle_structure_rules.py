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
        ("Chess", {"name": "Chess Club"}, "Not in the structure"),
        ("Chess", {"name": "Chess", "inClubs": True}, "Not in the structure"),
        ("Everyone", {"name": "Everyone", "isSharedDrive": True}, "Not in the structure"),
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
        "ancestors": [{"name": "Full Community", "url": "u1"}, {"name": "Land", "url": "u2"}],
        "children": [{"name": "Compost", "url": "u4", "type": "Working Group",
                      "next": {"text": "Thu, Nov 12", "url": "e4"}},
                     {"name": "Seeds", "url": None, "type": "Circle", "next": None}],
        "parentNext": {"text": "Mon, Nov 9", "url": "e2"},
        "leader": "Ann", "feedbackLink": "—", "structureUrl": "doc",
    }

    def text(self, paragraphs):
        return ["".join(run[0] for run in p["runs"]) for p in paragraphs]

    def test_placed(self):
        rows = js("structureRows(place)", place=self.PLACE)
        assert self.text(rows["parentLabel"]) == ["Parent Circle", "Set in the Circle Structure"]
        assert rows["parentLabel"][1]["runs"][0][1]["link"] == "doc"
        assert self.text(rows["parentValue"]) == [
            "Full Community › Land", "Its next meeting: Mon, Nov 9",
            "Our links to it: Leader Ann · Feedback Link not chosen yet"]
        assert self.text(rows["subValue"]) == [
            "Compost · working group · next meeting Thu, Nov 12", "Seeds"]
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

    def test_changed_text(self):
        a = [{"runs": [["Land", {}]]}]
        b = [{"runs": [["Lands", {}]]}]
        assert not js("paragraphsSignature(a) === paragraphsSignature(b)", a=a, b=b)
