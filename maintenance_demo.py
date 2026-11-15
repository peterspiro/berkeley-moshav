#!/usr/bin/env python3
"""
Fills the maintenance tracker's test base with demo data, for showing the
tracker to the Maintenance & Warranty Circle.

It deletes every row in the test base, "Building Maintenance (test)",
except in Locations, People, and Links, and writes a made-up but consistent set:
requests at every stage (with the repeats the weekly digest looks for),
updates, warranty claims, warranties, organizations, assets, guides, and
maintenance plans and work. The companies, their contacts, and their
addresses are fictional, and each Notes field says it's demo data.

Locations and People are used as they are:

- The circle members are the people whose Circle member box is ticked in
  People (tick at least one, by hand, first). The coordinator, whose
  Coordinator box is ticked, comes first; the others follow by Name. Every
  owned request is owned by one of them, in turn, and they write the
  updates, are the organizations' contacts, and own the maintenance plans.
- Each request's reporter is someone living in the unit the demo names,
  who isn't a circle member where there's a choice. A unit with nobody in
  it leaves the reporter's name typed in instead.

So no member's name is kept in this (public) repo.

Dates are set relative to today (visits a few days ahead, updates over the
past weeks), except the warranties' start date, WARRANTY_START. Airtable
can't backdate a request's Submitted: every request shows today's date.

Every request and update is written with the maintenance_tracker Apps
Script's own markers already set (Acknowledged on, Status noted,
Member-visible status, Told reporter on, and By or By name), so that the
script, which watches the test base, sees nothing to do and sends no email.

It works only on the test base. It asks you to type the base's name before
deleting anything, unless given --yes.

Requires the Airtable token in airtable_credentials (see README.md).

Usage:
    python maintenance_demo.py -n
    python maintenance_demo.py
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from maintenance_base import TEST_BASE
from util.airtable import Airtable, AirtableError
from util.credentials import load_airtable_token

TIME_ZONE = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
# When every demo warranty's clock starts.
WARRANTY_START = date(2026, 12, 1)
KEPT_TABLES = ("Locations", "People", "Links")
# The tables written, in an order where each row's links point to rows
# already made (a link within a table is written in a second pass).
WRITE_ORDER = ("Organizations", "Warranties", "Assets", "Guides", "Maintenance plans",
               "Requests", "Maintenance work", "Claims", "Updates")

# The status page's wording, as the Apps Script's MEMBER_STATUS has it.
MEMBER_STATUS = {
    "New": "Received", "Triaged": "Being looked into",
    "Waiting on builder": "Waiting on the builder", "Waiting on vendor": "Waiting on a contractor",
    "Waiting on volunteer": "Waiting on a volunteer", "Scheduled": "Visit scheduled",
    "Done, to confirm": "Done, waiting for the reporter to confirm", "Closed": "Closed",
}


@dataclass(frozen=True)
class Ref:
    """A link to a demo row, by its table and key, resolved when written."""
    table: str
    key: str


def utc(moment: datetime) -> str:
    """A time as Airtable returns it, so Status noted matches exactly."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def format_visit(iso: str) -> str:
    """A visit's time as the Apps Script's formatVisit has it: "Wed, Nov 18, 10:00 AM"."""
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TIME_ZONE)
    return f"{d:%a}, {d:%b} {d.day}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def request_markers(fields: dict, now: str) -> dict:
    """The Apps Script's markers for a request it has already dealt with:
    see processNewRequest, statusKey, and memberStatus."""
    status = fields.get("Status") or "New"
    visit = fields.get("Visit")
    text = MEMBER_STATUS.get(status)
    if text and status == "Scheduled" and visit:
        text = f"{text} for {format_visit(visit)}"
    return {"Acknowledged on": now, "Status noted": status + (f"|{visit}" if visit else ""),
            "Member-visible status": text}


def circle_members(people: list[dict]) -> list[dict]:
    """The people ticked as circle members: coordinators first, then by Name."""
    members = [p for p in people if p["fields"].get("Circle member") and not p["fields"].get("Moved out")]
    return sorted(members, key=lambda p: (not p["fields"].get("Coordinator"), p["fields"].get("Name", "")))


def demo_rows(today: date, people: list[dict], locations: list[dict]) -> dict[str, list[tuple[str, dict]]]:
    """Every demo row, as {table: [(key, fields)]}. Links to People and
    Locations are record IDs; links to other demo rows are Refs."""
    owners = circle_members(people)
    if not owners:
        raise ValueError("No one in People is ticked as a circle member.")
    now = utc(datetime.now(tz=UTC))
    loc_ids = {r["fields"].get("Name"): r["id"] for r in locations}

    def day(offset: int) -> str:
        return (today + timedelta(days=offset)).isoformat()

    def at(offset: int, clock: str) -> str:
        return utc(datetime.combine(today + timedelta(days=offset), time.fromisoformat(clock), TIME_ZONE))

    def loc(name: str) -> list[str]:
        return [loc_ids[name]]

    def owner(n: int) -> list[str]:
        return [owners[n % len(owners)]["id"]]

    def owner_name(n: int) -> str:
        return owners[n % len(owners)]["fields"].get("Name", "")

    def reporter(unit: str) -> dict:
        here = [p for p in people if loc_ids.get(unit) in (p["fields"].get("Location") or [])
                and not p["fields"].get("Moved out")]
        here.sort(key=lambda p: (bool(p["fields"].get("Circle member")), p["fields"].get("Name", "")))
        if here:
            return {"Reporter": [here[0]["id"]]}
        return {"Reporter name": f"Resident of {unit}"}

    org = lambda k: [Ref("Organizations", k)]
    war = lambda k: [Ref("Warranties", k)]
    asset = lambda k: [Ref("Assets", k)]
    guide = lambda k: [Ref("Guides", k)]
    plan = lambda k: [Ref("Maintenance plans", k)]
    req = lambda k: [Ref("Requests", k)]
    first_year_ends = WARRANTY_START.replace(year=WARRANTY_START.year + 1)
    demo = "Demo data."

    # --- Organizations ---------------------------------------------------
    builder_email = "warranty@eastshore-builders.example"
    orgs = [
        ("builder", {"Company": "Eastshore Builders", "Role": "General contractor",
                     "Trades": ["Plumbing", "Electrical & lighting", "Windows & exterior doors",
                                "Interior doors & hardware", "Cabinets & counters", "Floors", "Walls & paint",
                                "Roof & envelope"],
                     "Contact name": "Marcus Lee", "Phone": "(510) 555-0100", "Email": builder_email,
                     "Licence": "CSLB #900001", "Our contact": owner(2),
                     "Notes": f"{demo} The building's general contractor. Marcus is the warranty contact they "
                              "named in writing; claims go to him by email, copied to the project manager."}),
        ("plumber", {"Company": "Grizzly Peak Plumbing", "Role": "Subcontractor", "Trades": ["Plumbing"],
                     "Contact name": "Rosa Alvarez", "Phone": "(510) 555-0111", "Email": "service@gpplumbing.example",
                     "After-hours phone": "(510) 555-0112", "Licence": "CSLB #900002",
                     "Notes": f"{demo} Did the rough and finish plumbing. Warranty work goes through Eastshore, "
                              "not to them directly."}),
        ("electrician", {"Company": "Spruce Street Electric", "Role": "Subcontractor",
                         "Trades": ["Electrical & lighting", "HVAC & ventilation"], "Contact name": "Ken Okafor",
                         "Phone": "(510) 555-0120", "Email": "office@spruce-electric.example",
                         "Licence": "CSLB #900003",
                         "Notes": f"{demo} Installed the lighting, the panels, and the units' bath fans."}),
        ("elevator", {"Company": "Ascend Elevator Co.", "Role": "Manufacturer", "Trades": ["Elevator"],
                      "Contact name": "Carla Nguyen", "Phone": "(800) 555-0130", "After-hours phone": "(800) 555-0131",
                      "Email": "service@ascend-elevator.example", "Our contact": owner(0),
                      "Under contract until": day(227),
                      "Notes": f"{demo} Made and services the elevator. Monthly maintenance visit under contract; "
                               "call the after-hours line if anyone is trapped."}),
        ("roofer", {"Company": "Summit Roofing Systems", "Role": "Manufacturer", "Trades": ["Roof & envelope"],
                    "Contact name": "Warranty department", "Phone": "(800) 555-0140",
                    "Email": "warranty@summitroofing.example",
                    "Notes": f"{demo} Roof membrane manufacturer. The 20-year warranty needs the drains kept "
                             "clear, and a record of it."}),
        ("windows", {"Company": "Clearview Windows", "Role": "Manufacturer", "Trades": ["Windows & exterior doors"],
                     "Contact name": "Jen Park", "Phone": "(800) 555-0150", "Email": "claims@clearview-windows.example",
                     "Notes": f"{demo} Windows and balcony doors."}),
        ("mechanical", {"Company": "Bayside Mechanical", "Role": "Service vendor",
                        "Trades": ["HVAC & ventilation", "Plumbing"], "Contact name": "Tom Reyes",
                        "Phone": "(510) 555-0160", "After-hours phone": "(510) 555-0161",
                        "Email": "dispatch@baysidemech.example", "Our contact": owner(1),
                        "Under contract until": day(136),
                        "Notes": f"{demo} Services the central water heaters. Came out the same day for the "
                                 "no-hot-water call."}),
        ("fire", {"Company": "Firewatch Alarm & Sprinkler", "Role": "Service vendor", "Trades": ["Fire & life safety"],
                  "Contact name": "Priya Nair", "Phone": "(510) 555-0170", "After-hours phone": "(510) 555-0171",
                  "Email": "service@firewatch.example", "Our contact": owner(2), "Under contract until": day(77),
                  "Notes": f"{demo} Monitoring, and the annual fire alarm and sprinkler inspection."}),
        ("landscape", {"Company": "Greenline Landscape", "Role": "Service vendor", "Trades": ["Site & landscape"],
                       "Contact name": "Luis Romero", "Phone": "(510) 555-0180", "Email": "luis@greenline.example",
                       "Our contact": owner(0), "Notes": f"{demo} Courtyard plantings and irrigation, quarterly."}),
        ("access", {"Company": "Keystone Access Systems", "Role": "Service vendor", "Trades": ["Access & intercom"],
                    "Contact name": "Dev Patel", "Phone": "(510) 555-0190", "Email": "support@keystone-access.example",
                    "Notes": f"{demo} Intercom, fobs, and the garage gate."}),
        ("attorney", {"Company": "Hartley & Moss LLP", "Role": "Consultant", "Trades": ["Other"],
                      "Contact name": "A. Hartley", "Phone": "(510) 555-0195", "Email": "ahartley@hartleymoss.example",
                      "Our contact": owner(2),
                      "Notes": f"{demo} Construction-defect attorney: reviewing the claim letter, and what counts "
                               "as notice."}),
    ]

    # --- Warranties ------------------------------------------------------
    start = WARRANTY_START.isoformat()
    builder_claim = (f"Email Marcus Lee at Eastshore ({builder_email}), from the circle's address, with the "
                     "claim's Draft and photos. Ask for a reply confirming receipt.")
    warranties = [
        ("one-year", {"Name": "Builder one-year workmanship warranty", "Responsibility": "Builder warranty", "Warrantor": org("builder"),
                      "Held by": "Association", "Scope": "Common", "Clock starts": "One date for the building",
                      "Building start date": start, "Term (months)": 12,
                      "Systems": ["Windows & exterior doors", "Interior doors & hardware", "Cabinets & counters",
                                  "Floors", "Walls & paint", "Roof & envelope", "Access & intercom",
                                  "Site & landscape"],
                      "How to claim": builder_claim,
                      "Notes": f"{demo} Ends {first_year_ends:%B} {first_year_ends.day}, {first_year_ends.year}: "
                               "the walk-through before then is in Maintenance plans."}),
        ("two-year", {"Name": "Builder two-year systems warranty", "Responsibility": "Builder warranty", "Warrantor": org("builder"),
                      "Held by": "Association", "Scope": "Common", "Clock starts": "One date for the building",
                      "Building start date": start, "Term (months)": 24,
                      "Systems": ["Plumbing", "Electrical & lighting", "HVAC & ventilation"],
                      "How to claim": builder_claim, "Notes": demo}),
        ("structural", {"Name": "Builder ten-year structural warranty", "Responsibility": "Builder warranty", "Warrantor": org("builder"),
                        "Held by": "Unknown", "Scope": "Common", "Clock starts": "Unknown",
                        "Building start date": start, "Term (months)": 120, "Systems": ["Roof & envelope", "Other"],
                        "Notes": f"{demo} Whether it runs from the building's date or each closing is a question "
                                 "for the attorney."}),
        ("elevator", {"Name": "Elevator manufacturer warranty", "Responsibility": "Manufacturer warranty", "Warrantor": org("elevator"),
                      "Held by": "Association", "Scope": "Common", "Clock starts": "One date for the building",
                      "Building start date": start, "Term (months)": 24, "Systems": ["Elevator"],
                      "How to claim": "Call the service desk, then confirm by email.", "Notes": demo}),
        ("roof", {"Name": "Roof membrane manufacturer warranty", "Responsibility": "Manufacturer warranty", "Warrantor": org("roofer"),
                  "Held by": "Association", "Scope": "Common", "Clock starts": "One date for the building",
                  "Building start date": start, "Term (months)": 240, "Systems": ["Roof & envelope"],
                  "Notes": f"{demo} Void unless the drains are cleared twice a year: keep the Maintenance work "
                           "records."}),
        ("windows", {"Name": "Window and balcony door warranty", "Responsibility": "Manufacturer warranty", "Warrantor": org("windows"),
                     "Held by": "Each owner", "Scope": "Unit", "Clock starts": "Each unit's closing",
                     "Building start date": start, "Term (months)": 120, "Systems": ["Windows & exterior doors"],
                     "Notes": f"{demo} Hardware (latches, rollers) is covered for 2 years only."}),
        ("appliances", {"Name": "Appliance manufacturer warranty", "Responsibility": "Manufacturer warranty", "Held by": "Each owner", "Scope": "Unit",
                        "Clock starts": "Each unit's closing", "Building start date": start, "Term (months)": 12,
                        "Systems": ["Appliances"],
                        "How to claim": "The owner registers and claims with the maker; the circle can help.",
                        "Notes": demo}),
    ]

    # --- Assets ----------------------------------------------------------
    assets = [
        ("fan", {"Name": "Bath exhaust fan", "Kind": "Unit fixture or appliance", "System": "HVAC & ventilation",
                 "Make": "Breezeline", "Model": "BL-80Q", "Installed by": org("electrician"),
                 "Where exactly": "Bathroom ceiling", "Warranties": war("two-year"),
                 "Notes": f"{demo} The same model in every unit."}),
        ("sink", {"Name": "Kitchen sink and drain", "Kind": "Unit fixture or appliance", "System": "Plumbing",
                  "Make": "Norda", "Model": "Undermount 3018, P-trap kit", "Installed by": org("plumber"),
                  "Warranties": war("two-year"), "Notes": demo}),
        ("dishwasher", {"Name": "Dishwasher", "Kind": "Unit fixture or appliance", "System": "Appliances",
                        "Make": "Norda", "Model": "DW-24S", "Warranties": war("appliances"), "Notes": demo}),
        ("hood", {"Name": "Range hood", "Kind": "Unit fixture or appliance", "System": "Appliances",
                  "Make": "Norda", "Model": "RH-30", "Warranties": war("appliances"),
                  "Notes": f"{demo} Mesh filter, dishwasher-safe."}),
        ("window", {"Name": "Casement window", "Kind": "Unit fixture or appliance",
                    "System": "Windows & exterior doors", "Make": "Clearview", "Model": "CV-300 casement",
                    "Installed by": org("builder"), "Warranties": war("windows") + war("one-year"), "Notes": demo}),
        ("alarm", {"Name": "Smoke and CO alarm", "Kind": "Unit fixture or appliance", "System": "Fire & life safety",
                   "Make": "SafeHaus", "Model": "SH-2 combo, hardwired", "Installed by": org("electrician"),
                   "Notes": f"{demo} 10-year sealed battery backup."}),
        ("elevator", {"Name": "Passenger elevator", "Kind": "Equipment", "System": "Elevator",
                      "Location": loc("Elevator"), "Make": "Ascend", "Model": "A-2500 MRL", "Serial": "AE-25-00417",
                      "Installed by": org("elevator"), "Warranties": war("elevator"),
                      "Notes": f"{demo} Controller in the elevator room."}),
        ("heaters", {"Name": "Central water heaters", "Kind": "Equipment", "System": "Plumbing",
                     "Location": loc("Boiler room"), "Make": "ThermaPro", "Model": "TP-199, two in series",
                     "Serial": "TP199-1123, TP199-1124", "Installed by": org("plumber"), "Notes": demo}),
        ("panel", {"Name": "Fire alarm panel", "Kind": "Equipment", "System": "Fire & life safety",
                   "Location": loc("Electrical room"), "Make": "SafeHaus", "Model": "FACP-500",
                   "Installed by": org("fire"), "Notes": demo}),
        ("roof", {"Name": "Roof membrane and drains", "Kind": "Building element", "System": "Roof & envelope",
                  "Location": loc("Roof") + loc("Havdalah terrace"), "Make": "Summit", "Model": "TPO 60 mil",
                  "Installed by": org("builder"), "Warranties": war("roof"),
                  "Notes": f"{demo} Four roof drains, two of them on the terrace."}),
        ("gate", {"Name": "Garage gate and opener", "Kind": "Equipment", "System": "Access & intercom",
                  "Location": loc("Garage"), "Make": "Keystone", "Model": "Slide 900", "Installed by": org("access"),
                  "Notes": demo}),
        ("irrigation", {"Name": "Courtyard irrigation controller", "Kind": "Equipment", "System": "Site & landscape",
                        "Location": loc("Courtyard"), "Make": "RainWise", "Model": "RW-8 zone",
                        "Where exactly": "Courtyard, grey box on the east wall", "Notes": demo}),
        ("main-shutoff", {"Name": "Main water shutoff", "Kind": "Shutoff", "System": "Plumbing",
                          "Location": loc("Garage"),
                          "Where exactly": "Garage, north wall, red handle behind the bike racks", "Notes": demo}),
        ("unit-shutoff", {"Name": "Unit water shutoff", "Kind": "Shutoff", "System": "Plumbing",
                          "Where exactly": "Behind the access panel at the back of the laundry closet",
                          "Notes": f"{demo} One in each unit."}),
    ]

    # --- Guides ----------------------------------------------------------
    guides = [
        ("leak", {"Title": "Water leak: what to do first", "Kind": "Emergency", "Systems": ["Plumbing"],
                  "Assets": asset("main-shutoff") + asset("unit-shutoff"), "Reviewed on": day(-45)}),
        ("gfci", {"Title": "Resetting a GFCI outlet", "Kind": "How to", "Systems": ["Electrical & lighting"],
                  "Reviewed on": day(-64)}),
        ("hood", {"Title": "Cleaning the range hood filter", "Kind": "How to", "Systems": ["Appliances"],
                  "Assets": asset("hood"), "Reviewed on": day(-64)}),
        ("gate", {"Title": "Using the garage gate remote and fob", "Kind": "How to",
                  "Systems": ["Access & intercom"], "Assets": asset("gate")}),
        ("fans", {"Title": "Cleaning bath fan grilles", "Kind": "Maintenance procedure",
                  "Systems": ["HVAC & ventilation"], "Assets": asset("fan"), "Reviewed on": day(-87)}),
        ("drains", {"Title": "Clearing the roof and terrace drains", "Kind": "Maintenance procedure",
                    "Systems": ["Roof & envelope"], "Assets": asset("roof"), "Reviewed on": day(-224)}),
        ("alarm-test", {"Title": "Monthly fire alarm test", "Kind": "Maintenance procedure",
                        "Systems": ["Fire & life safety"], "Assets": asset("panel")}),
    ]

    # --- Maintenance plans -----------------------------------------------
    walk_through = first_year_ends - timedelta(days=30)
    plans = [
        ("elevator", {"Task": "Elevator maintenance visit", "Asset": asset("elevator"), "Kind": "Maintenance",
                      "Every (months)": 1, "Done by": "Vendor", "Vendor": org("elevator"), "Required by": "Code",
                      "Owner": owner(0), "First due": day(-278)}),
        ("fire", {"Task": "Fire alarm and sprinkler inspection", "Asset": asset("panel"), "Kind": "Inspection",
                  "Every (months)": 12, "Done by": "Vendor", "Vendor": org("fire"), "Required by": "Code",
                  "Owner": owner(2), "First due": day(66)}),
        ("alarm-test", {"Task": "Monthly fire alarm test", "Asset": asset("panel"), "Kind": "Test",
                        "Every (months)": 1, "Done by": "Volunteers", "Required by": "Good practice",
                        "Procedure": guide("alarm-test"), "Owner": owner(2), "First due": day(-259)}),
        ("drains", {"Task": "Clear roof and terrace drains", "Asset": asset("roof"), "Kind": "Maintenance",
                    "Every (months)": 6, "Season": ["Spring", "Fall"], "Done by": "Volunteers",
                    "Required by": "Warranty", "Procedure": guide("drains"), "Owner": owner(0),
                    "First due": day(-214)}),
        ("fans", {"Task": "Clean bath fan grilles (all units)", "Asset": asset("fan"), "Kind": "Maintenance",
                  "Every (months)": 6, "Done by": "Volunteers", "Required by": "Manufacturer",
                  "Procedure": guide("fans"), "Owner": owner(0), "First due": day(-123)}),
        ("heaters", {"Task": "Flush central water heaters", "Asset": asset("heaters"), "Kind": "Maintenance",
                     "Every (months)": 12, "Done by": "Vendor", "Vendor": org("mechanical"),
                     "Required by": "Manufacturer", "Owner": owner(1), "First due": day(56)}),
        ("irrigation", {"Task": "Irrigation check and winterize", "Asset": asset("irrigation"), "Kind": "Maintenance",
                        "Every (months)": 3, "Done by": "Vendor", "Vendor": org("landscape"),
                        "Required by": "Good practice", "Owner": owner(0), "First due": day(-259)}),
        ("walk-through", {"Task": "Walk-through before the one-year warranty ends", "Kind": "Inspection",
                          "Done by": "Volunteers", "Required by": "Warranty", "Owner": owner(2),
                          "First due": walk_through.isoformat()}),
    ]

    # --- Requests --------------------------------------------------------
    # The owners: 0 is the coordinator, 1 and 2 the next circle members.
    # The bath fans (four units) and the stack 4 sink drains are the
    # repeats the weekly digest looks for.
    def request(unit: str, **fields) -> dict:
        fields = {k.replace("_", " "): v for k, v in fields.items()}
        return {**reporter(unit), **fields}

    gate_visit, dishwasher_visit = at(3, "10:00"), at(-3, "13:00")
    requests = [
        ("fan207", request("Unit 207", Title="Bath fan rattles", Location=loc("Unit 207"), Room="Bath",
                           Description="The bathroom fan has started making a loud rattling noise when it runs, "
                                       "worse in the morning. It still pulls air.",
                           Type="Repair or defect", System="HVAC & ventilation", Component=asset("fan"),
                           Urgency="Routine", Status="Triaged", Owner=owner(0), Responsibility="Builder warranty",
                           Warranty=war("two-year"), In_my_unit=True, **{"May enter if I'm out": True})),
        ("fan302", request("Unit 302", Title="Bath fan rattles", Location=loc("Unit 302"), Room="Bath",
                           Description="Our bathroom fan rattles and buzzes. Sounds like something loose.",
                           Type="Repair or defect", System="HVAC & ventilation", Component=asset("fan"),
                           Urgency="Routine", Status="Triaged", Owner=owner(0), Responsibility="Builder warranty",
                           Warranty=war("two-year"), In_my_unit=True)),
        ("fan108", request("Unit 108", Title="Bath fan noisy, weak airflow", Location=loc("Unit 108"), Room="Bath",
                           Description="Fan is noisy and the mirror stays fogged long after a shower.",
                           Type="Repair or defect", System="HVAC & ventilation", Component=asset("fan"),
                           Urgency="Routine", Status="Waiting on volunteer", Owner=owner(0),
                           Responsibility="Builder warranty", Warranty=war("two-year"), In_my_unit=True,
                           Access_notes="Weekday afternoons are best. Cat: keep the door closed.")),
        ("fan404", request("Unit 404", Location=loc("Unit 404"), Room="Bath",
                           Description="Bathroom fan is rattling. A neighbour said theirs does it too.",
                           Type="Repair or defect", System="HVAC & ventilation", Urgency="Routine",
                           In_my_unit=True)),
        ("leak404", request("Unit 404", Title="Kitchen sink drain leaks into cabinet", Location=loc("Unit 404"),
                            Room="Kitchen", Description="Water collects under the kitchen sink after running the "
                                                        "dishwasher. The cabinet floor is getting soft.",
                            Type="Repair or defect", System="Plumbing", Component=asset("sink"),
                            Urgency="Urgent (days)", Status="Waiting on builder", Owner=owner(1),
                            Responsibility="Builder warranty", Warranty=war("two-year"), In_my_unit=True,
                            **{"May enter if I'm out": True})),
        ("leak304", request("Unit 304", Title="Ceiling stain under the kitchen sink of the unit above",
                            Location=loc("Unit 304"), Room="Kitchen",
                            Description="A brown stain on the kitchen ceiling, growing. It's right below the sink "
                                        "in 404.",
                            Type="Repair or defect", System="Plumbing", Urgency="Urgent (days)",
                            Status="Waiting on builder", Owner=owner(1), Responsibility="Builder warranty",
                            Warranty=war("two-year"), In_my_unit=True)),
        ("leak204", request("Unit 204", Title="Kitchen sink drain leaks into cabinet", Location=loc("Unit 204"),
                            Room="Kitchen", Description="Slow drip at the drain joint under the kitchen sink. Put a "
                                                        "bowl under it.",
                            Type="Repair or defect", System="Plumbing", Component=asset("sink"), Urgency="Routine",
                            Status="Waiting on builder", Owner=owner(1), Responsibility="Builder warranty",
                            Warranty=war("two-year"), In_my_unit=True)),
        ("window403", request("Unit 403", Title="Bedroom window won't latch", Location=loc("Unit 403"),
                              Room="Bedroom", Description="The bedroom casement window doesn't close fully; the "
                                                          "latch won't catch, so it can't be locked.",
                              Type="Repair or defect", System="Windows & exterior doors", Component=asset("window"),
                              Urgency="Urgent (days)", Status="Waiting on builder", Owner=owner(2),
                              Responsibility="Builder warranty", Warranty=war("one-year"), In_my_unit=True)),
        ("elevator", request("Unit 401", Title="Elevator stops between floors", Location=loc("Elevator"),
                             Description="Twice this week the elevator stopped a foot short of the 3rd floor and the "
                                         "doors opened anyway. Someone could trip.",
                             Type="Repair or defect", System="Elevator", Component=asset("elevator"),
                             Urgency="Urgent (days)", Status="Waiting on vendor", Owner=owner(0),
                             Responsibility="Manufacturer warranty", Warranty=war("elevator"),
                             Vendor=org("elevator"))),
        ("elevator2", request("Unit 408", Title="Elevator not level at 3rd floor", Location=loc("Elevator"),
                              Description="Elevator stopped below the floor again today.", Type="Repair or defect",
                              System="Elevator", Urgency="Urgent (days)", Status="Duplicate", Owner=owner(0),
                              Duplicate_of=req("elevator"))),
        ("gate", request("Unit 407", Title="Garage gate remote works only up close", Location=loc("Garage"),
                         Description="My gate remote only works from right in front of the gate. A new battery "
                                     "didn't help.",
                         Type="Repair or defect", System="Access & intercom", Component=asset("gate"),
                         Urgency="Routine", Status="Scheduled", Owner=owner(2), Responsibility="HOA",
                         Vendor=org("access"), Visit=gate_visit)),
        ("terrace", request("Unit 311", Title="Water ponding by terrace drain", Location=loc("Havdalah terrace"),
                            Description="After the rain there's a big puddle near the north drain on the Havdalah "
                                        "terrace that takes days to go.",
                            Type="Repair or defect", System="Roof & envelope", Component=asset("roof"),
                            Urgency="Routine", Status="Triaged", Owner=owner(0), Responsibility="Builder warranty",
                            Warranty=war("one-year"))),
        ("floor307", request("Unit 307", Title="Gaps opening between floorboards", Location=loc("Unit 307"),
                             Room="Living", Description="Gaps up to 1/8\" between the wood floorboards in the "
                                                        "living room, along the window wall.",
                             Type="Repair or defect", System="Floors", Urgency="Routine", Status="Triaged",
                             Owner=owner(2), Responsibility="Builder warranty", Warranty=war("one-year"),
                             In_my_unit=True)),
        ("dishwasher", request("Unit 407", Title="Dishwasher not draining", Location=loc("Unit 407"),
                               Room="Kitchen", Description="Standing water at the bottom of the dishwasher after "
                                                           "every cycle.",
                               Type="Repair or defect", System="Appliances", Component=asset("dishwasher"),
                               Urgency="Routine", Status="Done, to confirm", Owner=owner(1),
                               Responsibility="Manufacturer warranty", Warranty=war("appliances"),
                               Visit=dishwasher_visit, In_my_unit=True,
                               Resolution="The drain hose was kinked behind the unit at installation. Straightened "
                                          "and secured it; ran a cycle, and it drained.")),
        ("faucet", request("Unit 301", Title="Community kitchen faucet drips", Location=loc("Community kitchen"),
                           Description="The big faucet at the community kitchen sink drips constantly.",
                           Type="Repair or defect", System="Plumbing", Urgency="Routine",
                           Status="Waiting on volunteer", Owner=owner(1), Responsibility="HOA")),
        ("water-heater", request("Unit 206", Title="No hot water in the building", Location=loc("Unit 206"),
                                 Room="Other or whole unit",
                                 Description="No hot water at all this morning, in the kitchen or the shower.",
                                 Type="Repair or defect", System="Plumbing", Component=asset("heaters"),
                                 Urgency="Emergency", Status="Closed", Owner=owner(1), Responsibility="HOA",
                                 Vendor=org("mechanical"), Closed_on=day(-18), In_my_unit=True,
                                 Resolution="Building-wide: a water heater's igniter failed. Bayside replaced it the "
                                            "same afternoon, under their service contract.")),
        ("balcony205", request("Unit 205", Title="Balcony door leaks in heavy rain", Location=loc("Unit 205"),
                               Room="Balcony", Description="Water comes in under the balcony door when it rains "
                                                           "hard. The floor by the door is wet.",
                               Type="Repair or defect", System="Windows & exterior doors", Urgency="Urgent (days)",
                               Status="Closed", Owner=owner(2), Responsibility="Builder warranty",
                               Warranty=war("one-year"), Closed_on=day(-37), In_my_unit=True,
                               Resolution="Eastshore reset the threshold and resealed the sill pan, under warranty. "
                                          "No leak in the two storms since.")),
        ("lobby", request("Unit 310", Title="Lobby light flickers", Location=loc("Lobby"),
                          Description="One of the ceiling lights in the lobby flickers.", Type="Repair or defect",
                          System="Electrical & lighting", Urgency="Routine", Status="Closed", Owner=owner(2),
                          Responsibility="HOA", Closed_on=day(-11),
                          Resolution="Replaced the LED driver with the spare from the storage room.")),
        ("gfci", request("Unit 203", Title="Bathroom outlet dead", Location=loc("Unit 203"), Room="Bath",
                         Description="The outlet by the bathroom sink stopped working. How do I get it back on? Is "
                                     "it the breaker?",
                         Type="How do I…?", System="Electrical & lighting", Urgency="Routine", Status="Closed",
                         Owner=owner(2), Responsibility="Unit owner", Closed_on=day(-9), In_my_unit=True,
                         Answered_by=guide("gfci"),
                         Resolution="A GFCI outlet had tripped: pressed Reset on it. Sent the guide.")),
        ("range-hood", request("Unit 301", Title="How to clean the range hood filter", Location=loc("Unit 301"),
                               Room="Kitchen", Description="How do I take out and clean the filter in the range "
                                                           "hood? It's getting greasy.",
                               Type="How do I…?", System="Appliances", Urgency="Routine", Status="Closed",
                               Owner=owner(1), Responsibility="Unit owner", Closed_on=day(-13), In_my_unit=True,
                               Answered_by=guide("hood"), Resolution="Sent the guide.")),
        ("bike-hooks", request("Unit 302", Title="More wall hooks in the bike room", Location=loc("Bike room"),
                               Description="Suggestion: add wall hooks for bikes in the bike room. The floor racks "
                                           "are full.",
                               Type="Suggestion", System="Other", Urgency="Routine", Status="Declined",
                               Owner=owner(0), Responsibility="HOA",
                               Resolution="Passed to the Facilities circle: furnishing the common rooms is theirs, "
                                          "not a repair.")),
        ("smoke208", request("Unit 208", Location=loc("Unit 208"), Room="Entry",
                             Description="The smoke alarm in the hallway chirps every minute or so. I can't reach it "
                                         "to check it.",
                             Type="Repair or defect", System="Fire & life safety", Urgency="Urgent (days)",
                             In_my_unit=True, **{"May enter if I'm out": True})),
        ("irrigation", request("Unit 209", Location=loc("Courtyard"),
                               Description="One of the drip lines in the courtyard bed by the bench is spraying water "
                                           "onto the path.",
                               Type="Repair or defect", System="Site & landscape", Urgency="Routine")),
        ("door402", request("Unit 402", Location=loc("Unit 402"), Room="Entry",
                            Description="Our front door lock is stiff and the key sticks. Sometimes it takes a few "
                                        "tries.",
                            Type="Repair or defect", System="Interior doors & hardware", Urgency="Routine",
                            In_my_unit=True)),
    ]
    for _, fields in requests:
        fields.update(request_markers(fields, now))
    owner_of = {key: fields.get("Owner") for key, fields in requests}

    # --- Maintenance work ------------------------------------------------
    work = [
        ("elevator-1", {"Plan": plan("elevator"), "Due": day(-36), "Status": "Done", "Done on": day(-37),
                        "Done by": "Ascend Elevator Co.", "Notes": "Routine. No issues found."}),
        ("elevator-2", {"Plan": plan("elevator"), "Due": day(-5), "Status": "Done", "Done on": day(-4),
                        "Done by": "Ascend Elevator Co.",
                        "Notes": "Releveling fault logged twice; the technician adjusted it, and a part is on order.",
                        "Found": req("elevator")}),
        ("elevator-3", {"Plan": plan("elevator"), "Due": day(25), "Status": "Scheduled",
                        "Scheduled for": at(24, "09:00")}),
        ("drains-1", {"Plan": plan("drains"), "Due": day(-214), "Status": "Done", "Done on": day(-217),
                      "Done by": f"{owner_name(0)}, {owner_name(2)}",
                      "Notes": "All four drains clear. Photos taken."}),
        ("drains-2", {"Plan": plan("drains"), "Due": day(-31), "Status": "Done", "Done on": day(-28),
                      "Done by": f"{owner_name(0)}, {owner_name(1)}",
                      "Notes": "Drains clear, but water ponds near the north terrace drain: logged a request.",
                      "Found": req("terrace")}),
        ("alarm-test-1", {"Plan": plan("alarm-test"), "Due": day(-14), "Status": "Done", "Done on": day(-14),
                          "Done by": owner_name(2), "Notes": "All horns and strobes worked."}),
        ("alarm-test-2", {"Plan": plan("alarm-test"), "Due": day(16), "Status": "Due"}),
        ("fans-1", {"Plan": plan("fans"), "Due": day(-123), "Status": "Skipped",
                    "Notes": "Not enough volunteers in the summer; moved to the fall."}),
        ("fans-2", {"Plan": plan("fans"), "Due": day(5), "Status": "Scheduled", "Scheduled for": at(7, "10:00"),
                    "Notes": "Check the rattling fans while we're there."}),
        ("irrigation-1", {"Plan": plan("irrigation"), "Due": day(16), "Status": "Due"}),
        ("heaters-1", {"Plan": plan("heaters"), "Due": day(56), "Status": "Due"}),
        ("walk-through", {"Plan": plan("walk-through"), "Due": walk_through.isoformat(), "Status": "Due",
                          "Notes": "Every unit and common area, with the checklist. Anything found goes in as a "
                                   "request before the one-year warranty ends."}),
    ]

    # --- Claims ----------------------------------------------------------
    sent_to = f"Marcus Lee, {builder_email}"
    claims = [
        ("leaks", {"Title": "Kitchen sink drains leak, stack 4",
                   "Requests": req("leak404") + req("leak304") + req("leak204"), "Warranty": war("two-year"),
                   "Warrantor": org("builder"), "Scope": "Systemic", "Status": "Submitted",
                   "Submitted on": day(-20), "Submitted how": "Email", "Sent to": sent_to}),
        ("window", {"Title": "Bedroom window latch, Unit 403", "Requests": req("window403"),
                    "Warranty": war("one-year"), "Warrantor": org("builder"), "Scope": "Unit",
                    "Status": "Acknowledged", "Submitted on": day(-38), "Submitted how": "Email", "Sent to": sent_to,
                    "Their reference": "EB-W-2291", "Acknowledged on": day(-33), "Promised by": day(-9),
                    "Response": "Marcus: a replacement latch is on order from Clearview; the installer will "
                                "schedule once it arrives."}),
        ("balcony", {"Title": "Balcony door leak, Unit 205", "Requests": req("balcony205"),
                     "Warranty": war("one-year"), "Warrantor": org("builder"), "Scope": "Unit", "Status": "Accepted",
                     "Submitted on": day(-74), "Submitted how": "Email", "Sent to": sent_to,
                     "Their reference": "EB-W-2240", "Acknowledged on": day(-72), "Promised by": day(-46),
                     "Work done on": day(-52), "Response": "We will reset the threshold and reseal the sill pan.",
                     "Accepted by": owner_of["balcony205"], "Accepted on": day(-37)}),
        ("elevator", {"Title": "Elevator stops short of floor", "Requests": req("elevator"),
                      "Warranty": war("elevator"), "Warrantor": org("elevator"), "Scope": "Common element",
                      "Status": "Drafting"}),
    ]
    claim_request = {key: fields["Requests"][0].key for key, fields in claims}

    # --- Updates ---------------------------------------------------------
    # Each is written by its request's owner, or for a claim, by the owner
    # of the claim's first request; status changes by the script.
    def update(on: str, kind: str, offset: int, clock: str, text: str, tell: bool = False) -> dict:
        when = at(offset, clock)
        fields = {"Kind": kind, "When": when, "Text": text}
        if on in claim_request:
            fields["Claim"] = [Ref("Claims", on)]
            on = claim_request[on]
        else:
            fields["Request"] = req(on)
        if kind == "Status change":
            fields["By name"] = "Maintenance tracker"
        else:
            fields["By"] = owner_of[on]
        if tell:
            fields["Tell the reporter"] = True
            fields["Told reporter on"] = when
        return fields

    def status(on: str, offset: int, clock: str, before: str, after: str, visit: str | None = None) -> dict:
        tail = f", for {format_visit(visit)}" if visit else ""
        return update(on, "Status change", offset, clock, f"Status changed from {before} to {after}{tail}.")

    updates = [
        status("fan207", -12, "11:10", "New", "Triaged"),
        update("fan207", "Note", -12, "11:15", "Third rattling fan this month (207, 302, 108), all the same model. "
               "Watching for more before claiming it as one systemic problem."),
        status("fan302", -10, "08:40", "New", "Triaged"),
        status("fan108", -9, "09:00", "New", "Triaged"),
        update("fan108", "Note", -9, "09:05", "Need someone with a ladder to take off the grille and photograph the "
               "fan housing, for the claim."),
        status("fan108", -9, "09:06", "Triaged", "Waiting on volunteer"),
        status("leak404", -26, "11:00", "New", "Triaged"),
        update("leak404", "Visit", -25, "10:30", "Looked under the sink with the reporter: the drain's slip joint "
               "weeps when the dishwasher empties. A towel under it for now.", tell=True),
        update("leak304", "Note", -24, "10:00", "The stain is directly under 404's sink. Same stack as 204's leak: "
               "making one systemic claim."),
        status("leak404", -20, "12:00", "Triaged", "Waiting on builder"),
        update("leaks", "Email", -20, "12:05", "Sent the claim to Marcus Lee, with photos from all three units."),
        update("leaks", "Call", -3, "15:20", "Left a voicemail for Marcus: no acknowledgement yet after 17 days."),
        status("window403", -40, "09:30", "New", "Triaged"),
        status("window403", -38, "11:00", "Triaged", "Waiting on builder"),
        update("window", "Email", -33, "16:45", "Marcus acknowledged: the latch is on order, promised in about "
               "three weeks."),
        update("window403", "Email", -5, "10:00", "Eastshore missed the date they promised. Asked Marcus for a "
               "new one.", tell=True),
        status("elevator", -6, "08:30", "New", "Waiting on vendor"),
        update("elevator", "Call", -6, "08:45", "Called Ascend. They'll look at it at the monthly visit.", tell=True),
        update("elevator", "Visit", -4, "14:00", "The technician adjusted the leveling and ordered a sensor. They'll "
               "come back when it arrives, in about two weeks.", tell=True),
        status("elevator2", -3, "11:00", "New", "Duplicate"),
        status("gate", -5, "12:00", "New", "Triaged"),
        status("gate", -2, "09:00", "Triaged", "Scheduled", visit=gate_visit),
        update("terrace", "Note", -28, "12:00", "Found during the fall drain clearing. Photos are with the "
               "maintenance work record."),
        status("terrace", -27, "09:00", "New", "Triaged"),
        status("floor307", -13, "10:00", "New", "Triaged"),
        update("floor307", "Note", -13, "10:05", "Measured: 2 to 3 mm gaps over about 3 m. Adding it to the list "
               "for the one-year walk-through."),
        status("dishwasher", -6, "10:00", "New", "Scheduled", visit=dishwasher_visit),
        update("dishwasher", "Visit", -3, "13:40", "The drain hose was kinked behind the unit. Fixed; it drained "
               "fine."),
        status("dishwasher", -3, "13:45", "Scheduled", "Done, to confirm"),
        status("faucet", -7, "10:00", "New", "Waiting on volunteer"),
        update("faucet", "Note", -7, "10:05", "Probably the cartridge. Need someone handy to swap it; the part is "
               "about $30."),
        update("water-heater", "Call", -18, "07:20", "Called Bayside's after-hours line."),
        update("water-heater", "Visit", -18, "13:00", "Bayside replaced the igniter on heater 1. Hot water was back "
               "by 2 pm.", tell=True),
        status("water-heater", -18, "14:00", "Waiting on vendor", "Closed"),
        update("balcony205", "Visit", -52, "11:00", "Eastshore reset the threshold and resealed the sill pan."),
        update("balcony", "Note", -37, "09:00", "No leaks through two storms. Accepted the work."),
        status("balcony205", -37, "09:05", "Done, to confirm", "Closed"),
        status("lobby", -11, "16:00", "Triaged", "Closed"),
        update("gfci", "Email", -9, "09:00", "It's a GFCI outlet: press the Reset button on the outlet itself (it "
               "may be the one by the other sink). The guide is in the handbook.", tell=True),
        status("gfci", -9, "09:10", "New", "Closed"),
        update("range-hood", "Email", -13, "12:00", "Sent the range hood guide: the filter pops out with the tab at "
               "the front, and goes in the dishwasher.", tell=True),
        status("range-hood", -13, "12:05", "New", "Closed"),
        status("bike-hooks", -14, "10:00", "New", "Declined"),
    ]

    return {
        "Organizations": orgs, "Warranties": warranties, "Assets": assets, "Guides": guides,
        "Maintenance plans": plans, "Requests": requests, "Maintenance work": work, "Claims": claims,
        "Updates": [(str(i), fields) for i, fields in enumerate(updates)],
    }


def resolve(value, ids: dict[str, dict[str, str]]):
    """A field's value with its Refs replaced by record IDs."""
    if isinstance(value, list):
        return [ids[v.table][v.key] if isinstance(v, Ref) else v for v in value]
    return value


def write(airtable: Airtable, base: str, rows: dict[str, list[tuple[str, dict]]]) -> None:
    """Create the rows, table by table in WRITE_ORDER, linking each to the
    rows it names. A link to a row in the same table is set afterwards."""
    ids: dict[str, dict[str, str]] = {}
    for table in WRITE_ORDER:
        later = []
        records = []
        for key, fields in rows[table]:
            now = {}
            for name, value in fields.items():
                if isinstance(value, list) and any(isinstance(v, Ref) and v.table == table for v in value):
                    later.append((key, name, value))
                else:
                    now[name] = resolve(value, ids)
            records.append({"fields": now})
        made = airtable.create_records(base, table, records)
        ids[table] = {key: record["id"] for (key, _), record in zip(rows[table], made)}
        if later:
            airtable.update_records(base, table, [
                {"id": ids[table][key], "fields": {name: resolve(value, ids)}} for key, name, value in later])
        print(f"{table}: {len(made)} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="show what would be written, and change nothing")
    parser.add_argument("--yes", action="store_true",
                        help=f"don't ask before deleting the rows in {TEST_BASE!r}")
    args = parser.parse_args()

    airtable = Airtable(load_airtable_token())
    try:
        base = airtable.find_base(TEST_BASE)
        tables = [t["name"] for t in airtable.tables(base)]
        people = airtable.list_records(base, "People")
        locations = airtable.list_records(base, "Locations")
        rows = demo_rows(datetime.now(TIME_ZONE).date(), people, locations)
    except (AirtableError, ValueError) as e:
        sys.exit(f"Error: {e}")

    members = circle_members(people)
    print("Circle members: " + ", ".join(
        p["fields"].get("Name", "") + (" (coordinator)" if p["fields"].get("Coordinator") else "")
        for p in members))
    for table in WRITE_ORDER:
        print(f"  {table}: {len(rows[table])} rows")
    if args.dry_run:
        return
    if not args.yes and input(f"This deletes every row in {TEST_BASE!r} except in "
                              f"{', '.join(KEPT_TABLES)}. Type its name to go on: ").strip() != TEST_BASE:
        sys.exit("Nothing changed.")

    try:
        for table in reversed(WRITE_ORDER):
            if table in tables:
                old = [r["id"] for r in airtable.list_records(base, table, ["Number"] if table == "Updates" else None)]
                airtable.delete_records(base, table, old)
        write(airtable, base, rows)
    except AirtableError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
