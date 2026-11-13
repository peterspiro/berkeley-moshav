"""
Tests for copying the Gather directory into the maintenance tracker's
People table (util.maintenance_people and sync_maintenance_people.py),
against a fake Airtable. All people are fictional.
"""

import csv
import io
from datetime import date

import sync_maintenance_people
from tests.fakes_airtable import FakeAirtable
from tests.test_maintenance_base import _applied
from util.airtable_schema import load_schema
from util.gather_utils import GatherUser, fetch_all_gather_users
from util.maintenance_people import SYNCED_FIELDS, changes, location_name, people_rows

TODAY = date(2026, 11, 13)
LOCATIONS = {"Unit 108": "recA", "Unit 411": "recB"}
# The directory export's heading row, as Gather writes it.
HEADER = ('"ID",First Name,Last Name,Unit Num,Unit Suffix,Birthday,Pronouns,Email,Google Email,'
          'Paypal Email,Is Child,Full Access,Household ID,Household Name,Parents/Guardians,'
          'Mobile Phone,Home Phone,Work Phone,Join Date,Departure Date,Preferred Contact,'
          'Garage Nums,Vehicles,Keyholders,Emergency Contacts,Pets')


def _user(uid, first, last, **kw):
    return GatherUser(uid, first, last, f"{first} {last}", **kw)


DANA = _user("1", "Dana", "Smith", email="dana@example.org", google_email="dana.s@gmail.com",
             unit="411", phone="510-555-0101")
SAM = _user("2", "Sam", "Lee", email="sam@example.org", google_email="sam@example.org", unit="108")
KID = _user("3", "Kit", "Smith", child=True, unit="411")


class _Page:
    def __init__(self, text):
        self.text = text

    def evaluate(self, script, url):
        return self.text


def test_directory_export_gives_unit_phone_and_departure():
    names = next(csv.reader(io.StringIO(HEADER)))
    row = dict.fromkeys(names, "")
    row.update({"ID": "7", "First Name": "Ada", "Last Name": "Lin", "Unit Num": "411",
                "Unit Suffix": "A", "Email": "ada@example.org", "Is Child": "false",
                "Home Phone": "510-555-0102", "Departure Date": "2026-10-01"})
    out = io.StringIO()
    writer = csv.DictWriter(out, names)
    writer.writeheader()
    writer.writerow(row)
    [ada] = fetch_all_gather_users(_Page(out.getvalue()), "https://example")
    assert (ada.unit, ada.phone, ada.departure_date) == ("411A", "510-555-0102", "2026-10-01")
    row["Mobile Phone"] = "510-555-0103"
    out = io.StringIO()
    writer = csv.DictWriter(out, names)
    writer.writeheader()
    writer.writerow(row)
    assert fetch_all_gather_users(_Page(out.getvalue()), "https://example")[0].phone == "510-555-0103"


def test_synced_fields_are_people_fields():
    people = next(t for t in load_schema()["tables"] if t["name"] == "People")
    assert set(SYNCED_FIELDS) <= {f["name"] for f in people["fields"]}


def test_rows_are_adults_with_unit_linked():
    rows, warnings = people_rows([DANA, SAM, KID], LOCATIONS, TODAY)
    assert warnings == []
    assert rows == [
        {"Name": "Dana S", "Full name": "Dana Smith",
         "Email": "dana@example.org", "Other email": "dana.s@gmail.com",
         "Phone": "510-555-0101", "Location": ["recB"], "Gather ID": "1", "Moved out": False},
        # A Google Email that's the same as the email isn't repeated.
        {"Name": "Sam L", "Full name": "Sam Lee", "Email": "sam@example.org", "Other email": None,
         "Phone": None, "Location": ["recA"], "Gather ID": "2", "Moved out": False},
    ]


def test_names_are_only_as_long_as_tells_people_apart():
    # The request form shows these to anyone with its link.
    users = [DANA, SAM, KID, _user("10", "Dana", "Stone"), _user("11", "Dana", "Reyes"),
             _user("12", "sam", "lowe"), GatherUser("13", "Cher", "", "Cher")]
    names = {r["Full name"]: r["Name"] for r in people_rows(users, LOCATIONS, TODAY)[0]}
    assert names == {
        # Two would be "Dana S", and two "Sam L" (whatever the capitals).
        "Dana Smith": "Dana Smith", "Dana Stone": "Dana Stone", "Dana Reyes": "Dana R",
        "Sam Lee": "Sam Lee", "sam lowe": "sam lowe", "Cher": "Cher"}
    # A child of the same name doesn't lengthen an adult's: only adults are listed.
    kid = _user("14", "Sam", "Lang", child=True)
    assert people_rows([SAM, kid], LOCATIONS, TODAY)[0][0]["Name"] == "Sam L"


def test_unit_suffix_falls_back_to_the_unit_and_unknown_units_warn():
    assert location_name("411A", LOCATIONS) == "Unit 411"
    assert location_name("", LOCATIONS) is None
    rows, warnings = people_rows([_user("4", "Pat", "Roe", unit="999")], LOCATIONS, TODAY)
    assert rows[0]["Location"] == []
    assert warnings == ["Pat Roe: no Locations row for unit '999'."]
    # Someone Gather places in a unit that isn't a number doesn't live in
    # the building: copied without a location, and without a warning.
    rows, warnings = people_rows([_user("6", "Con", "Sultant", unit="Consultants")], LOCATIONS, TODAY)
    assert rows[0]["Location"] == [] and warnings == []


def test_moved_out_once_the_departure_date_has_passed():
    def moved(d):
        return people_rows([_user("5", "Lee", "Ray", departure_date=d)], LOCATIONS, TODAY)[0][0]["Moved out"]
    assert not moved("")
    assert moved("2026-11-13") and moved("2025-01-01")
    assert not moved("2027-01-01")
    assert moved("sometime")  # unreadable: assume it has


def test_changes_add_update_and_mark_moved_out():
    rows, _ = people_rows([DANA, SAM], LOCATIONS, TODAY)
    records = [
        # Dana as she is, as Airtable returns her: empty fields left out.
        {"id": "r1", "fields": {"Name": "Dana S", "Full name": "Dana Smith", "Email": "dana@example.org",
                                "Other email": "dana.s@gmail.com", "Phone": "510-555-0101",
                                "Location": ["recB"], "Gather ID": "1", "Circle member": True}},
        # Gone from the directory.
        {"id": "r2", "fields": {"Name": "Old Timer", "Gather ID": "9"}},
        # Already marked, and one added by hand: both left alone.
        {"id": "r3", "fields": {"Name": "Long Gone", "Gather ID": "8", "Moved out": True}},
        {"id": "r4", "fields": {"Name": "Outside Helper"}},
    ]
    to_write, lines = changes(rows, records)
    assert lines == ["Add Sam L.", "Mark Old Timer as moved out (not in the directory)."]
    assert to_write == [rows[1], {"Gather ID": "9", "Moved out": True}]
    records[0]["fields"]["Phone"] = "old"
    records[0]["fields"]["Location"] = ["recA"]
    assert changes(rows, records)[1][0] == "Update Dana S: Phone, Location."


def test_sync_writes_people_and_keeps_the_circles_own_fields():
    client = _applied(load_schema())
    client.upsert_records("app", "Locations", "Name", [{"Name": "Unit 411"}, {"Name": "Unit 108"}])
    quiet = lambda line: None
    sync_maintenance_people.sync(client, "app", [DANA, KID], dry_run=True, today=TODAY, out=quiet)
    assert client.list_records("app", "People") == []
    sync_maintenance_people.sync(client, "app", [DANA, KID], dry_run=False, today=TODAY, out=quiet)
    [dana] = client.list_records("app", "People")
    unit = next(r["id"] for r in client.list_records("app", "Locations") if r["fields"]["Name"] == "Unit 411")
    assert dana["fields"]["Location"] == [unit]
    dana["fields"]["Coordinator"] = True  # set by the circle, in Airtable

    # Nothing changed: nothing written.
    before = len(client.calls)
    sync_maintenance_people.sync(client, "app", [DANA], dry_run=False, today=TODAY, out=quiet)
    assert len(client.calls) == before

    # Dana leaves the directory, Sam joins.
    lines = []
    sync_maintenance_people.sync(client, "app", [SAM], dry_run=False, today=TODAY, out=lines.append)
    people = {r["fields"]["Name"]: r["fields"] for r in client.list_records("app", "People")}
    assert people["Dana S"]["Moved out"] is True and people["Dana S"]["Coordinator"] is True
    assert people["Dana S"]["Email"] == "dana@example.org"
    assert "Sam L" in people
    assert lines[-1] == "1 adult(s) in the directory; 2 row(s) written."
