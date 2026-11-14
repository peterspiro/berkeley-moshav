"""
Tests for the maintenance_tracker Apps Script: its rules
(tracker_rules.gs) and the script that applies them
(maintenance_tracker.gs), run under Node against stand-ins for Airtable,
Gmail, and Drive (tests/maintenance_tracker_harness.js). Skipped if Node
isn't installed.

All people, addresses, and links are fictional.
"""

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from util.airtable_schema import expected_fields, load_schema

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "maintenance_tracker"
HARNESS = Path(__file__).resolve().parent / "maintenance_tracker_harness.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")

CIRCLE = "maintenance@example.org"
FORM = "https://airtable.example/form"
PROPS = {"AIRTABLE_TOKEN": "pat", "AIRTABLE_BASE_ID": "appTest", "CIRCLE_EMAIL": CIRCLE,
         "REQUEST_FORM_URL": FORM, "STATUS_PAGE_URL": "https://airtable.example/status",
         "TRIAGE_URL": "https://airtable.example/triage"}
DANA_LINK = FORM + "?prefill_Reporter=Dana+S&prefill_Location=Unit+411"


def tables(requests=(), updates=(), people=None):
    base = {name: [] for name in ["Locations", "People", "Requests", "Updates", "Claims", "Warranties",
                                  "Organizations", "Assets", "Maintenance plans", "Maintenance work",
                                  "Guides"]}
    base["Locations"] = [{"id": "loc411", "fields": {"Name": "Unit 411", "Kind": "Unit"}},
                         {"id": "locRoof", "fields": {"Name": "Roof", "Kind": "Common"}}]
    base["People"] = people if people is not None else [
        {"id": "pDana", "fields": {"Name": "Dana S", "Full name": "Dana Smith", "Email": "dana@example.org",
                                   "Other email": "dana.s@gmail.example", "Phone": "510-555-0101",
                                   "Location": ["loc411"], "Request form link": DANA_LINK}},
        {"id": "pGone", "fields": {"Name": "Old T", "Full name": "Old Timer", "Email": "old@example.org",
                                   "Moved out": True}},
    ]
    base["Requests"] = list(requests)
    base["Updates"] = list(updates)
    return base


def request(number=12, **fields):
    base = {"Number": number, "Type": "Repair or defect", "System": "Plumbing", "Urgency": "Routine",
            "Description": "Tap drips", "Status": "New"}
    base.update(fields)
    return {"id": f"req{number}", "fields": {k: v for k, v in base.items() if v is not None}}


def acknowledged(number=12, **fields):
    """A request the script has already seen, at the status given."""
    r = request(number, Reporter=["pDana"], Location=["loc411"], **fields)
    r["fields"].setdefault("Acknowledged on", "2026-11-01T00:00:00.000Z")
    return r


def run(state, *functions, props=None, expr=None, values=None):
    payload = {"tables": state, "props": PROPS if props is None else props, "run": list(functions),
               "expr": expr, "values": values or {}}
    result = subprocess.run(["node", str(HARNESS)], input=json.dumps(payload),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


def rule(expr, **values):
    return run(tables(), expr=expr, values=values)["result"]


def one(out, table="Requests"):
    [record] = out["tables"][table]
    return record["fields"]


# --- the files ---------------------------------------------------------------

def test_every_field_and_table_the_script_names_is_in_the_schema():
    schema = load_schema()
    fields = {name for table in expected_fields(schema).values() for name in table}
    used = set()
    for path in PROJECT.glob("*.gs"):
        text = path.read_text()
        used |= set(re.findall(r"""[\w\]]\[\s*'((?:[^'\\]|\\.)+)'\s*\]""", text))  # f['Name']
        used |= set(re.findall(r"""^\s*'((?:[^'\\]|\\.)+)':""", text, re.MULTILINE))  # 'Name': value
        used |= set(re.findall(r"(?<!\$)\{([A-Z][\w ]+)\}", text))  # {Name}, in Airtable formulas
    statuses = next(f["choices"] for t in schema["tables"] if t["name"] == "Requests"
                    for f in t["fields"] if f["name"] == "Status")
    # The member-status table's keys are Status options, not fields.
    used = {u.replace("\\'", "'") for u in used} - set(statuses)
    assert used <= fields, sorted(used - fields)
    listed = re.search(r"const TABLES = \[(.*?)\];", (PROJECT / "maintenance_tracker.gs").read_text(), re.S)
    assert re.findall(r"'([^']+)'", listed.group(1)) == [t["name"] for t in schema["tables"]]


def test_statuses_and_urgencies_match_the_schema():
    requests = {f["name"]: f for t in load_schema()["tables"] if t["name"] == "Requests" for f in t["fields"]}
    statuses = rule("Object.keys(MEMBER_STATUS)")
    assert set(statuses) == set(requests["Status"]["choices"]) - {"Duplicate", "Declined"}
    assert set(rule("URGENT")) < set(requests["Urgency"]["choices"])


def test_no_ids_or_links_in_the_project():
    # The repo is public: the base, its form, and the circle's address are Script Properties.
    for path in PROJECT.iterdir():
        text = path.read_text()
        assert not re.search(r"\b(app|tbl|pat)[A-Za-z0-9]{14}\b", text), path.name
        assert "airtable.com/app" not in text and "@berkeleymoshav.org" not in text.replace(
            "admin@berkeleymoshav.org", ""), path.name


# --- new requests ------------------------------------------------------------

def test_new_request_from_a_member_in_their_unit():
    out = run(tables([request(Reporter=["pDana"], **{"In my unit": True, "Room": "Kitchen"})]),
              "processRequests")
    f = one(out)
    assert f["Location"] == ["loc411"]
    assert f["Acknowledged on"] and f["Status noted"] == "New" and f["Member-visible status"] == "Received"
    [mail] = out["mails"]
    assert (mail["to"], mail["subject"], mail["replyTo"]) == ("dana@example.org", "Request R-12 received", CIRCLE)
    assert mail["name"] == "Berkeley Moshav Maintenance"
    body = mail["body"]
    assert body.startswith("Hi Dana,\n")
    assert "Where: Unit 411, Kitchen" in body and '"Tap drips"' in body
    assert "https://airtable.example/status" in body and DANA_LINK in body


def test_second_run_does_nothing_and_asks_airtable_twice():
    state = run(tables([request(Reporter=["pDana"], Location=["locRoof"])]), "processRequests")
    again = run(state["tables"], "processRequests", props=state["props"])
    assert again["mails"] == [] and again["calls"] == ["get Requests", "get Updates"]
    assert again["tables"] == state["tables"]


def test_a_chosen_location_wins_over_in_my_unit():
    out = run(tables([request(Reporter=["pDana"], Location=["locRoof"], **{"In my unit": True})]),
              "processRequests")
    assert one(out)["Location"] == ["locRoof"]
    assert "Where: Roof" in out["mails"][0]["body"]


def test_typed_reporter_is_linked_by_either_address():
    out = run(tables([request(**{"Reporter name": "D. Smith", "Reporter email": " Dana.S@gmail.example ",
                                 "In my unit": True})]), "processRequests")
    f = one(out)
    assert f["Reporter"] == ["pDana"] and f["Location"] == ["loc411"]
    # They're emailed at the address Gather has, as any member is.
    assert out["mails"][0]["to"] == "dana@example.org"


def test_typed_reporter_who_isnt_a_member():
    out = run(tables([request(**{"Reporter name": "Pat Roe", "Reporter email": "pat@elsewhere.example",
                                 "In my unit": True})]), "processRequests")
    f = one(out)
    assert "Reporter" not in f and "Location" not in f and f["Acknowledged on"]
    [mail] = out["mails"]
    assert mail["to"] == "pat@elsewhere.example" and mail["body"].startswith("Hi Pat,")
    assert "prefill" not in mail["body"]
    # Nor is someone who's moved out linked, though the address was theirs.
    out = run(tables([request(**{"Reporter name": "Old", "Reporter email": "old@example.org"})]),
              "processRequests")
    assert "Reporter" not in one(out)


def test_request_with_no_address_is_still_acknowledged():
    out = run(tables([request(**{"Reporter name": "Someone"})]), "processRequests")
    assert out["mails"] == [] and one(out)["Acknowledged on"]


def test_urgent_request_alerts_the_circle():
    out = run(tables([request(Reporter=["pDana"], Location=["loc411"], Urgency="Emergency",
                              Photos=[{"id": "att1", "url": "https://x/1", "filename": "a.jpg"}],
                              **{"May enter if I'm out": True, "Access notes": "door is open"})]),
              "processRequests")
    ack, alert = out["mails"]
    assert ack["to"] == "dana@example.org"
    assert (alert["to"], alert["subject"]) == (CIRCLE, "[Emergency] R-12 Unit 411: Plumbing")
    for text in ("Reported by Dana Smith, 510-555-0101, dana@example.org.", "May enter if they're out.",
                 "Access: door is open", "1 photo(s)", "Triage: https://airtable.example/triage"):
        assert text in alert["body"]
    out = run(tables([request(Urgency="Urgent (days)", **{"In my unit": True})]), "processRequests")
    [alert] = out["mails"]
    assert alert["subject"] == "[Urgent] R-12: Plumbing"
    assert "the unit isn't known" in alert["body"] and "gave no name" in alert["body"]


def test_a_failed_run_leaves_the_request_to_try_again():
    props = {k: v for k, v in PROPS.items() if k != "CIRCLE_EMAIL"}
    payload = {"tables": tables([request(Reporter=["pDana"])]), "props": props, "run": ["processRequests"]}
    failed = subprocess.run(["node", str(HARNESS)], input=json.dumps(payload), capture_output=True, text=True)
    assert failed.returncode != 0 and "Set the Script Property CIRCLE_EMAIL" in failed.stderr


def test_all_mail_can_be_sent_to_one_address_for_testing():
    out = run(tables([request(Reporter=["pDana"], Urgency="Emergency")]), "processRequests",
              props={**PROPS, "SEND_ALL_MAIL_TO": "tester@example.org"})
    assert [(m["to"], m["subject"].split("]")[0]) for m in out["mails"]] == [
        ("tester@example.org", "[test, for dana@example.org"),
        ("tester@example.org", f"[test, for {CIRCLE}")]


def test_emergency_note_is_added_when_set():
    out = run(tables([request(Reporter=["pDana"])]), "processRequests",
              props={**PROPS, "EMERGENCY_NOTE": "In an emergency, call 510-555-0199."})
    assert out["mails"][0]["body"].endswith("In an emergency, call 510-555-0199.")


# --- status changes ----------------------------------------------------------

def status_run(status, noted, **fields):
    return run(tables([acknowledged(Status=status, **{"Status noted": noted}, **fields)]), "processRequests")


def test_triaged_is_recorded_without_an_email():
    out = status_run("Triaged", "New", Title="Tap drips")
    assert out["mails"] == []
    f = one(out)
    assert f["Status noted"] == "Triaged" and f["Member-visible status"] == "Being looked into"
    update = one(out, "Updates")
    assert update["Request"] == ["req12"] and update["Kind"] == "Status change"
    assert update["Text"] == "Status changed from New to Triaged." and update["By name"] == "Maintenance tracker"


def test_scheduled_visit_is_emailed_and_again_if_it_moves():
    out = status_run("Scheduled", "Triaged", Title="Tap drips", Visit="2026-11-20T18:00:00.000Z")
    [mail] = out["mails"]
    assert mail["subject"] == "Request R-12: visit scheduled"
    assert "your request R-12 (Tap drips) is scheduled for Fri, Nov 20, 10:00 AM." in mail["body"]
    assert "make sure someone's home" in mail["body"]
    f = one(out)
    assert f["Member-visible status"] == "Visit scheduled for Fri, Nov 20, 10:00 AM"
    assert f["Status noted"] == "Scheduled|2026-11-20T18:00:00.000Z"

    state = copy.deepcopy(out["tables"])
    state["Requests"][0]["fields"].update({"Visit": "2026-11-21T01:30:00.000Z", "May enter if I'm out": True})
    out = run(state, "processRequests", props=out["props"])
    [mail] = out["mails"]
    assert "Fri, Nov 20, 5:30 PM" in mail["body"] and "nobody needs to be home" in mail["body"]
    assert [u["fields"]["Text"] for u in out["tables"]["Updates"]] == [
        "Status changed from Triaged to Scheduled, for Fri, Nov 20, 10:00 AM.",
        "Visit moved to Fri, Nov 20, 5:30 PM."]


def test_scheduled_without_a_time_waits_for_one():
    out = status_run("Scheduled", "Triaged")
    assert out["mails"] == [] and one(out)["Member-visible status"] == "Visit scheduled"


def test_done_asks_whether_its_fixed_and_closing_then_says_nothing():
    out = status_run("Done, to confirm", "Scheduled|2026-11-20T18:00:00.000Z")
    [mail] = out["mails"]
    assert mail["subject"] == "Request R-12: is it fixed?" and "reply to this email" in mail["body"]
    out = status_run("Closed", "Done, to confirm")
    assert out["mails"] == []
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", one(out)["Closed on"])


def test_closed_without_done_is_emailed_with_the_resolution():
    out = status_run("Closed", "Triaged", Resolution="It was the washer.", **{"Closed on": "2026-11-02"})
    [mail] = out["mails"]
    assert mail["subject"] == "Request R-12 closed" and "It was the washer." in mail["body"]
    assert one(out)["Closed on"] == "2026-11-02"  # a date already there is kept


def test_declined_comes_off_the_status_page():
    out = status_run("Declined", "New", **{"Member-visible status": "Received"})
    assert out["mails"] == [] and "Member-visible status" not in one(out)


# --- updates -----------------------------------------------------------------

def test_update_marked_for_the_reporter_is_emailed_once():
    updates = [{"id": "upd1", "fields": {"Request": ["req12"], "Text": "Plumber is booked.",
                                         "Tell the reporter": True}},
               {"id": "upd2", "fields": {"Request": ["req12"], "Text": "Called the builder."}}]
    out = run(tables([acknowledged(Status="Triaged", Title="Tap drips", **{"Status noted": "Triaged"})],
                     updates), "processRequests")
    [mail] = out["mails"]
    assert (mail["to"], mail["subject"]) == ("dana@example.org", "Request R-12: an update")
    assert "An update on your request R-12 (Tap drips):\n\nPlumber is booked." in mail["body"]
    told = {u["id"]: "Told reporter on" in u["fields"] for u in out["tables"]["Updates"]}
    assert told == {"upd1": True, "upd2": False}
    again = run(out["tables"], "processRequests", props=out["props"])
    assert again["mails"] == []


# --- form links --------------------------------------------------------------

def test_form_link_is_the_tested_shape():
    assert rule("formLink(u, 'Peter S', 'Unit 411')", u=FORM) == \
        FORM + "?prefill_Reporter=Peter+S&prefill_Location=Unit+411"
    assert rule("formLink(u, \"Zoë O'Neil\", null)", u=FORM + "?x=1") == \
        FORM + "?x=1&prefill_Reporter=Zo%C3%AB+O'Neil"


def test_form_links_are_written_where_missing_or_stale():
    people = [
        {"id": "p1", "fields": {"Name": "Dana S", "Location": ["loc411"], "Request form link": DANA_LINK}},
        {"id": "p2", "fields": {"Name": "Sam L"}},
        {"id": "p3", "fields": {"Name": "Old T", "Moved out": True, "Request form link": "https://old"}},
        {"id": "p4", "fields": {"Name": "Dana Stone", "Request form link": FORM + "?prefill_Reporter=Dana+S"}},
    ]
    out = run(tables(people=people), "writeFormLinks")
    links = {p["id"]: p["fields"].get("Request form link") for p in out["tables"]["People"]}
    assert links == {"p1": DANA_LINK, "p2": FORM + "?prefill_Reporter=Sam+L", "p3": None,
                     "p4": FORM + "?prefill_Reporter=Dana+Stone"}
    assert out["calls"].count("patch People") == 1
    assert "patch People" not in run(out["tables"], "writeFormLinks")["calls"]
    props = {k: v for k, v in PROPS.items() if k != "REQUEST_FORM_URL"}
    assert run(tables(people=people), "writeFormLinks", props=props)["calls"] == []


# --- backup ------------------------------------------------------------------

def test_backup_saves_every_table_and_each_attachment_once():
    photo = {"id": "att1", "url": "https://files.example/1", "filename": "leak.jpg"}
    state = tables([request(Photos=[photo])])
    props = {**PROPS, "BACKUP_FOLDER_ID": "folder1"}
    out = run(state, "backup", "backup", props=props)
    [day] = [k for k in out["drive"] if re.fullmatch(r"\d{4}-\d\d-\d\d/", k)]
    assert sorted(out["drive"][day]) == sorted(f"{t}.json" for t in state)
    saved = json.loads(out["drive"][day]["Requests.json"])
    assert saved[0]["fields"]["Description"] == "Tap drips"
    assert out["drive"]["Attachments/"] == {"att1 leak.jpg": "bytes of https://files.example/1"}
    assert run(state, "backup")["drive"] == {}  # no folder set: nothing saved
