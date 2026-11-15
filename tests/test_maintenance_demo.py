"""
Tests for maintenance_demo.py: that its rows fit the base's schema, link
only to rows that exist, and carry the tracker script's markers, so the
script sends no email about them.
"""

import json
from datetime import date

import pytest

import maintenance_demo
from maintenance_demo import Ref, circle_members, demo_rows, format_visit, write
from util.airtable_schema import expected_fields, load_schema
from tests.test_maintenance_base import ROOT

TODAY = date(2026, 11, 15)
WRITABLE = {"text", "longText", "number", "date", "dateTime", "checkbox", "email", "phone", "url",
            "select", "multiSelect", "link"}


def _locations():
    rows = json.loads((ROOT / "maintenance_tracker/base/seed.json").read_text())["Locations"]["rows"]
    return [{"id": f"recL{i}", "fields": row} for i, row in enumerate(rows)]


def _people(locations):
    unit = {r["fields"]["Name"]: r["id"] for r in locations}
    people = [{"id": f"recP{i}", "fields": {"Name": f"Person {i}", "Location": [loc]}}
              for i, loc in enumerate(unit.values())]
    people[0]["fields"].update({"Circle member": True})
    people[1]["fields"].update({"Circle member": True, "Coordinator": True})
    people[2]["fields"].update({"Circle member": True})
    return people


@pytest.fixture
def demo():
    locations = _locations()
    people = _people(locations)
    return demo_rows(TODAY, people, locations), people, locations


def test_coordinator_comes_first_then_by_name():
    people = [{"id": "a", "fields": {"Name": "Zed", "Circle member": True}},
              {"id": "b", "fields": {"Name": "Amy", "Circle member": True}},
              {"id": "c", "fields": {"Name": "Max", "Circle member": True, "Coordinator": True}},
              {"id": "d", "fields": {"Name": "Bob"}},
              {"id": "e", "fields": {"Name": "Ann", "Circle member": True, "Moved out": True}}]
    assert [p["id"] for p in circle_members(people)] == ["c", "b", "a"]


def test_needs_a_circle_member():
    locations = _locations()
    with pytest.raises(ValueError):
        demo_rows(TODAY, [], locations)


def test_every_field_and_option_is_in_the_schema(demo):
    rows, _, _ = demo
    fields = expected_fields(load_schema())
    for table, table_rows in rows.items():
        for key, row in table_rows:
            for name, value in row.items():
                assert name in fields[table], f"{table}.{name}"
                field = fields[table][name]
                assert field["type"] in WRITABLE, f"{table}.{name} is computed"
                if field["type"] in ("select", "multiSelect"):
                    for v in value if isinstance(value, list) else [value]:
                        assert v in field["choices"], f"{table}.{name}: {v!r}"
                if field["type"] == "link":
                    assert all(isinstance(v, Ref) and v.table == field["table"] or
                               isinstance(v, str) and field["table"] in ("People", "Locations")
                               for v in value), f"{table}.{name}"


def test_links_name_rows_that_exist(demo):
    rows, people, locations = demo
    keys = {t: {k for k, _ in r} for t, r in rows.items()}
    ids = {r["id"] for r in people + locations}
    for table, table_rows in rows.items():
        for _, row in table_rows:
            for value in row.values():
                for v in value if isinstance(value, list) else []:
                    assert (v.key in keys[v.table]) if isinstance(v, Ref) else (v in ids or not v.startswith("rec"))


def test_owners_are_circle_members(demo):
    rows, people, _ = demo
    members = {p["id"] for p in circle_members(people)}
    owned = [r["Owner"][0] for _, r in rows["Requests"] if "Owner" in r]
    assert owned and set(owned) <= members and len(set(owned)) == 3
    assert all(set(r.get("By", [])) <= members for _, r in rows["Updates"])


def test_the_tracker_script_has_nothing_to_do(demo):
    rows, _, _ = demo
    for _, r in rows["Requests"]:
        assert r["Acknowledged on"]
        status = r.get("Status") or "New"
        assert r["Status noted"] == status + (f"|{r['Visit']}" if r.get("Visit") else "")
        if status == "Scheduled":
            assert r["Member-visible status"] == f"Visit scheduled for {format_visit(r['Visit'])}"
        assert r.get("Reporter") or r.get("Reporter name")
    for _, u in rows["Updates"]:
        assert u.get("By") or u.get("By name")
        assert not u.get("Tell the reporter") or u.get("Told reporter on")


def test_visits_are_written_as_airtable_returns_them(demo):
    rows, _, _ = demo
    visits = [r["Visit"] for _, r in rows["Requests"] if r.get("Visit")]
    assert visits and all(v.endswith(".000Z") for v in visits)
    assert format_visit("2026-11-18T18:00:00.000Z") == "Wed, Nov 18, 10:00 AM"


def test_warranties_start_on_the_set_date(demo):
    rows, _, _ = demo
    assert {r["Building start date"] for _, r in rows["Warranties"]} == {
        maintenance_demo.WARRANTY_START.isoformat()}


class _Recorder:
    def __init__(self):
        self.made = {}
        self.updated = []

    def create_records(self, base, table, records):
        made = [{"id": f"{table}:{i}", "fields": r["fields"]} for i, r in enumerate(records)]
        self.made[table] = made
        return made

    def update_records(self, base, table, records):
        self.updated += [(table, r) for r in records]


def test_write_resolves_links_and_defers_links_within_a_table(demo, capsys):
    rows, _, _ = demo
    client = _Recorder()
    write(client, "app", rows)
    for table in client.made.values():
        for record in table:
            for value in record["fields"].values():
                assert not any(isinstance(v, Ref) for v in (value if isinstance(value, list) else []))
    duplicate = [r for t, r in client.updated if t == "Requests"]
    assert len(duplicate) == 1 and list(duplicate[0]["fields"]) == ["Duplicate of"]
    assert duplicate[0]["fields"]["Duplicate of"][0].startswith("Requests:")
