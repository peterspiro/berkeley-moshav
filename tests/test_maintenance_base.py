"""
Tests for the maintenance tracker's base: the schema file, working out
what a base lacks (util.airtable_schema), and maintenance_base.py's apply
and seed, against a fake Airtable.
"""

import json
from pathlib import Path

import pytest

import maintenance_base
from tests.fakes_airtable import FakeAirtable
from util.airtable_schema import (
    FIELD_TYPES, SchemaError, expected_fields, load_schema, plan, schema_problems)

ROOT = Path(__file__).resolve().parent.parent


def _schema(*tables):
    return {"tables": list(tables)}


def _table(name, *fields, views=()):
    return {"name": name, "fields": [{"name": "Name", "type": "text"}, *fields],
            "views": list(views)}


def _applied(schema):
    client = FakeAirtable()
    maintenance_base.apply(client, "app", schema, out=lambda line: None)
    return client


# --- the repo's own files ---------------------------------------------------

def test_schema_file_is_valid_and_has_the_plans_tables():
    schema = load_schema()
    assert [t["name"] for t in schema["tables"]] == [
        "Locations", "People", "Requests", "Updates", "Claims", "Warranties",
        "Organizations", "Assets", "Maintenance plans", "Maintenance work", "Guides"]


def test_schema_file_holds_no_airtable_ids():
    # The repo is public: bases, tables, and fields are found by name.
    text = (ROOT / "maintenance" / "schema.json").read_text()
    import re
    assert not re.search(r"\b(app|tbl|fld|viw|rec)[A-Za-z0-9]{14}\b", text)


def test_request_form_can_take_a_reporter_picked_or_typed():
    # A member picks themselves from People, whose primary field (all the
    # form's link shows) is a short name; anyone else types their details.
    tables = {t["name"]: t for t in load_schema()["tables"]}
    fields = {f["name"]: f for f in tables["Requests"]["fields"]}
    assert (fields["Reporter"]["type"], fields["Reporter"]["table"]) == ("link", "People")
    assert fields["Reporter name"]["type"] == "text"
    assert fields["Reporter email"]["type"] == "email"
    assert tables["People"]["fields"][0]["name"] == "Name" and "primary" not in tables["People"]
    assert "Full name" in [f["name"] for f in tables["People"]["fields"]]


def test_seed_file_has_the_36_units_and_the_common_areas():
    seeds = json.loads((ROOT / "maintenance" / "seed.json").read_text())
    names = [r["Name"] for r in seeds["Locations"]["rows"] if r["Kind"] == "Unit"]
    assert len(names) == len(set(names)) == 36
    # Floors 1-4, stacks 1-11, without 101-107 and 405.
    assert "Unit 108" in names and "Unit 411" in names
    assert not {"Unit 101", "Unit 107", "Unit 405"} & set(names)
    unit = next(r for r in seeds["Locations"]["rows"] if r["Name"] == "Unit 411")
    assert (unit["Floor"], unit["Stack"]) == (4, 11)
    common = [r for r in seeds["Locations"]["rows"] if r["Kind"] == "Common"]
    assert len(common) == 31 and len({r["Name"] for r in common} | set(names)) == 67
    assert next(r for r in common if r["Name"] == "3rd floor walkway")["Floor"] == 3
    floors = {r["Name"]: r.get("Floor") for r in common}
    assert floors["Havdalah terrace"] == 4 and floors["Garage"] == 1
    # The stairwells, and what isn't on a floor, have none.
    assert [n for n, f in floors.items() if f is None] == [
        "Roof", "Elevator", "Stairwell - outdoor", "Stairwell - indoor", "Building exterior"]
    tables = {t["name"]: {f["name"] for f in t["fields"]} for t in load_schema()["tables"]}
    for table, spec in seeds.items():
        assert all(set(row) <= tables[table] for row in spec["rows"])


def test_airtable_credentials_file_is_git_ignored():
    from util.credentials import AIRTABLE_CREDENTIALS_FILE
    assert AIRTABLE_CREDENTIALS_FILE.resolve().parent == ROOT
    assert AIRTABLE_CREDENTIALS_FILE.name in (ROOT / ".gitignore").read_text().splitlines()


def test_airtable_token_from_variable_or_file(tmp_path, monkeypatch):
    from util.credentials import load_airtable_token
    path = tmp_path / "creds"
    path.write_text("token = patFILE\n")
    path.chmod(0o600)
    monkeypatch.delenv("AIRTABLE_TOKEN", raising=False)
    assert load_airtable_token(path) == "patFILE"
    monkeypatch.setenv("AIRTABLE_TOKEN", "proxy-injected")
    assert load_airtable_token(path) == "proxy-injected"


# --- checking a schema -------------------------------------------------------

def test_choice_list_is_filled_in(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps({
        "choice_lists": {"systems": ["Plumbing", "Other"]},
        "tables": [_table("A", {"name": "System", "type": "select", "choices": "@systems"})]}))
    assert load_schema(path)["tables"][0]["fields"][1]["choices"] == ["Plumbing", "Other"]
    path.write_text(json.dumps({
        "tables": [_table("A", {"name": "System", "type": "select", "choices": "@nope"})]}))
    with pytest.raises(SchemaError):
        load_schema(path)


@pytest.mark.parametrize("schema, word", [
    (_schema(_table("A"), _table("A")), "Two tables"),
    (_schema({"name": "A", "fields": [{"name": "F", "type": "formula", "formula": "1"}]}), "primary"),
    (_schema(_table("A", {"name": "name", "type": "text"})), "twice"),
    (_schema(_table("A", {"name": "X", "type": "rating"})), "unknown type"),
    (_schema(_table("A", {"name": "X", "type": "select", "choices": ["a", "a"]})), "distinct"),
    (_schema(_table("A", {"name": "X", "type": "link", "table": "B", "inverse": "As"})), "isn't in the file"),
    (_schema(_table("A", {"name": "X", "type": "link", "table": "A"})), "inverse"),
    (_schema(_table("A", {"name": "X", "type": "formula"})), "formula"),
    (_schema({**_table("A"), "primary": "Label"}), "isn't one of its fields"),
    # The inverse would land on a field B already has, or on another inverse.
    (_schema(_table("A", {"name": "X", "type": "link", "table": "B", "inverse": "Name"}), _table("B")),
     "already a field"),
    (_schema(_table("A", {"name": "X", "type": "link", "table": "B", "inverse": "As"},
                    {"name": "Y", "type": "link", "table": "B", "inverse": "As"}), _table("B")),
     "already a field"),
    (_schema(_table("A", views=[{"name": "V", "type": "grid"}, {"name": "V", "type": "grid"}])), "view twice"),
])
def test_schema_problems(schema, word):
    assert any(word in p for p in schema_problems(schema))


def test_expected_fields_include_inverses():
    schema = _schema(_table("A", {"name": "B", "type": "link", "table": "B", "inverse": "As"}),
                     _table("B"))
    assert expected_fields(schema)["B"]["As"]["table"] == "A"


# --- apply -------------------------------------------------------------------

def test_apply_builds_the_whole_schema_file_and_is_then_in_step():
    schema = load_schema()
    client = _applied(schema)
    p = plan(schema, client.tables("app"))
    assert p.actions == []
    # Only what the API can't do is left: the views, and making a formula
    # the primary field.
    assert p.by_hand and all("view" in line or "primary" in line for line in p.by_hand)
    assert p.notes == []
    wanted = expected_fields(schema)
    for t in client.tables("app"):
        assert {f["name"] for f in t["fields"]} == set(wanted[t["name"]])
        assert t["fields"][0]["id"] == t["primaryFieldId"]
    # A second run does nothing.
    before = len(client.calls)
    maintenance_base.apply(client, "app", schema, out=lambda line: None)
    assert len(client.calls) == before


def test_apply_types_fields_and_names_inverses():
    schema = load_schema()
    client = _applied(schema)
    tables = {t["name"]: {f["name"]: f for f in t["fields"]} for t in client.tables("app")}
    ids = {t["name"]: t["id"] for t in client.tables("app")}
    for t in schema["tables"]:
        for f in t["fields"]:
            assert tables[t["name"]][f["name"]]["type"] == FIELD_TYPES[f["type"]][0]
    # A link's other side has the schema's name and points back.
    assert tables["People"]["Requests owned"]["options"]["linkedTableId"] == ids["Requests"]
    assert tables["Requests"]["Duplicates"]["options"]["linkedTableId"] == ids["Requests"]
    assert tables["Requests"]["Label"]["options"]["formula"].startswith('"R-"')


def test_a_table_is_created_with_only_its_primary_field():
    # Airtable refuses a new table with a formula or created-time field.
    client = _applied(_schema(_table("A", {"name": "Made", "type": "createdTime"},
                                     {"name": "F", "type": "formula", "formula": "{Name}"})))
    assert [f["name"] for f in client.tables("app")[0]["fields"]] == ["Name", "Made", "F"]


def test_new_field_and_new_choices_are_added_to_an_existing_base():
    old = _schema(_table("A", {"name": "S", "type": "select", "choices": ["x"]}))
    client = _applied(old)
    new = _schema(_table("A", {"name": "S", "type": "select", "choices": ["x", "y"]},
                         {"name": "T", "type": "text"}))
    p = plan(new, client.tables("app"))
    assert sorted(a["do"] for a in p.actions) == ["add_choices", "create_field"]
    assert [a["choices"] for a in p.actions if a["do"] == "add_choices"] == [["y"]]
    maintenance_base.apply(client, "app", new, out=lambda line: None)
    assert plan(new, client.tables("app")).in_step


def test_inverse_left_with_airtables_name_is_renamed():
    # As if a run stopped between creating a link and naming its inverse.
    schema = _schema(_table("A", {"name": "B", "type": "link", "table": "B", "inverse": "As"}),
                     _table("B"))
    client = _applied(schema)
    b = next(t for t in client.tables("app") if t["name"] == "B")
    next(f for f in b["fields"] if f["name"] == "As")["name"] = "A"
    p = plan(schema, client.tables("app"))
    assert [(a["do"], a["from"], a["to"]) for a in p.actions] == [("rename_field", "A", "As")]
    maintenance_base.apply(client, "app", schema, out=lambda line: None)
    assert plan(schema, client.tables("app")).in_step


def test_nothing_is_deleted_or_retyped_only_reported():
    client = _applied(_schema(_table("A", {"name": "X", "type": "text"},
                                     {"name": "S", "type": "select", "choices": ["x", "old"]}),
                              _table("Extra")))
    new = _schema(_table("A", {"name": "S", "type": "select", "choices": ["x"]},
                         views=[{"name": "Triage", "type": "grid"}]))
    # X is now a number in the file; the base's is text.
    new["tables"][0]["fields"].append({"name": "X", "type": "number"})
    p = plan(new, client.tables("app"))
    assert p.actions == []
    assert any("A.X should be number" in line for line in p.by_hand)
    assert any("'Triage'" in line for line in p.by_hand)
    assert any("'Extra'" in n for n in p.notes)
    assert any("old" in n for n in p.notes)


def test_formula_primary_is_asked_for_by_hand_until_made():
    # A request shows as "R-3 Bath fan rattles" wherever it's linked, not "3".
    schema = load_schema()
    assert {t["name"] for t in schema["tables"] if t.get("primary") == "Label"} == {"Requests", "Claims"}
    client = FakeAirtable()
    wanted = ["Requests: make 'Label' the primary field (it's 'Number').",
              "Claims: make 'Label' the primary field (it's 'Number')."]
    # Asked for before the table exists, and after apply.
    assert all(line in plan(schema, client.tables("app")).by_hand for line in wanted)
    maintenance_base.apply(client, "app", schema, out=lambda line: None)
    assert all(line in plan(schema, client.tables("app")).by_hand for line in wanted)
    client.change_primary_by_hand("Requests", "Label")
    client.change_primary_by_hand("Claims", "Label")
    assert not any("primary" in line for line in plan(schema, client.tables("app")).by_hand)


def test_wrong_primary_field_is_reported():
    client = _applied(_schema(_table("A")))
    other = _schema({"name": "A", "fields": [{"name": "Number", "type": "autoNumber"}]})
    p = plan(other, client.tables("app"))
    assert any("primary" in line for line in p.by_hand)


def test_missing_link_whose_inverse_exists_is_left_to_a_person():
    client = _applied(_schema(_table("A"), _table("B", {"name": "As", "type": "text"})))
    schema = _schema(_table("A", {"name": "B", "type": "link", "table": "B", "inverse": "As"}),
                     _table("B"))
    p = plan(schema, client.tables("app"))
    assert p.actions == []
    assert any("by hand" in line for line in p.by_hand)


# --- seed --------------------------------------------------------------------

def test_seed_adds_missing_rows_and_updates_the_rest():
    client = _applied(load_schema())
    seeds = {"Locations": {"key": "Name", "rows": [
        {"Name": "Unit 108", "Kind": "Unit", "Floor": 1, "Stack": 8},
        {"Name": "Unit 201", "Kind": "Unit", "Floor": 2, "Stack": 1}]}}
    client.upsert_records("app", "Locations", "Name", [{"Name": "Unit 108", "Floor": 9}])
    lines = []
    maintenance_base.seed(client, "app", seeds, dry_run=True, out=lines.append)
    assert lines == ["Locations: 1 to add, 1 already there."]
    assert len(client.list_records("app", "Locations")) == 1
    maintenance_base.seed(client, "app", seeds, dry_run=False, out=lines.append)
    rows = {r["fields"]["Name"]: r["fields"] for r in client.list_records("app", "Locations")}
    assert rows["Unit 108"]["Floor"] == 1 and set(rows) == {"Unit 108", "Unit 201"}
