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
from util.google_group_utils import DOMAIN

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "maintenance_tracker" / "apps_script"
HARNESS = Path(__file__).resolve().parent / "maintenance_tracker_harness.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")

CIRCLE = "maintenance@example.org"
FORM = "https://airtable.example/form"
PROPS = {"AIRTABLE_TOKEN": "pat", "AIRTABLE_BASE_ID": "appTest", "CIRCLE_EMAIL": CIRCLE}
LINKS = {"Request form": FORM, "Status page": "https://airtable.example/status",
         "Triage view": "https://airtable.example/triage"}
DANA_LINK = FORM + "?prefill_Reporter=Dana+S&prefill_Location=Unit+411"


def tables(requests=(), updates=(), people=None, links=None):
    base = {name: [] for name in ["Locations", "People", "Requests", "Updates", "Claims", "Warranties",
                                  "Organizations", "Assets", "Maintenance plans", "Maintenance work",
                                  "Guides", "Links"]}
    base["Links"] = [{"id": f"lnk{i}", "fields": {"Name": name, "Value": value}}
                     for i, (name, value) in enumerate((LINKS if links is None else links).items())]
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
    used = {u.replace("\\'", "'") for u in used} - set(statuses) - {t["name"] for t in schema["tables"]}
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
        assert "airtable.com/app" not in text and f"@{DOMAIN}" not in text.replace(
            f"admin@{DOMAIN}", ""), path.name


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


def test_second_run_does_nothing_and_asks_airtable_little():
    state = run(tables([request(Reporter=["pDana"], Location=["locRoof"])]), "processRequests")
    # On the Free plan, updates were looked for on the first run, and aren't due again for 6 hours.
    again = run(state["tables"], "processRequests:timer", props=state["props"])
    assert again["mails"] == [] and again["calls"] == ["get Requests"]
    assert again["tables"] == state["tables"]
    later = {**state["props"], "lastUpdatesCheck": "2026-01-01T00:00:00.000Z"}
    assert run(state["tables"], "processRequests:timer", props=later)["calls"] == ["get Requests", "get Updates"]
    # On a plan with no ration of calls, they're looked for every time.
    team = {**state["props"], "AIRTABLE_PLAN": "team"}
    assert run(state["tables"], "processRequests:timer", props=team)["calls"] == ["get Requests", "get Updates"]
    # And whenever it's run by hand, which is how it's tried out.
    assert run(state["tables"], "processRequests", props=state["props"])["calls"] == ["get Requests", "get Updates"]


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


def test_script_may_fetch_only_from_airtable():
    manifest = json.loads((PROJECT / "appsscript.json").read_text())
    assert manifest["urlFetchWhitelist"] == ["https://api.airtable.com/",
                                             "https://v5.airtableusercontent.com/"]
    text = (PROJECT / "maintenance_tracker.gs").read_text()
    assert "const AIRTABLE_API = 'https://api.airtable.com/v0';" in text


# --- responsibility from the warranty ----------------------------------------

def test_responsibility_is_filled_in_from_the_warranty():
    builder = {"Warranty": ["w1"], "Warranty responsibility": ["Builder warranty"]}
    out = run(tables([acknowledged(12, **builder, **{"Status noted": "New"}),
                      acknowledged(13, **builder, Responsibility="Unknown", **{"Status noted": "New"}),
                      acknowledged(14, **builder, Responsibility="HOA", **{"Status noted": "New"}),
                      acknowledged(15, **{"Status noted": "New"})]), "processRequests")
    got = {r["id"]: r["fields"].get("Responsibility") for r in out["tables"]["Requests"]}
    assert got == {"req12": "Builder warranty", "req13": "Builder warranty", "req14": "HOA", "req15": None}
    assert out["mails"] == [] and out["tables"]["Updates"] == []
    again = run(out["tables"], "processRequests:timer", props=out["props"])
    assert again["calls"] == ["get Requests"]


def test_responsibility_is_written_with_a_status_change_and_a_new_request():
    warranty = {"Warranty": ["w1"], "Warranty responsibility": ["Manufacturer warranty"]}
    out = status_run("Triaged", "New", Title="Tap drips", **warranty)
    assert one(out)["Responsibility"] == "Manufacturer warranty"
    assert sum(call == "patch Requests" for call in out["calls"]) == 1
    out = run(tables([request(Reporter=["pDana"], **warranty)]), "processRequests")
    assert one(out)["Responsibility"] == "Manufacturer warranty"


def test_responsibility_rule():
    assert rule("responsibilityFromWarranty(r)", r={"fields": {"Warranty responsibility": []}}) is None
    assert rule("responsibilityFromWarranty(r)",
                r={"fields": {"Warranty responsibility": ["Builder warranty"],
                              "Responsibility": "Unit owner"}}) is None


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


FUTURE, PAST = "2099-03-05T18:00:00.000Z", "2020-03-05T18:00:00.000Z"


def test_a_visit_time_schedules_the_request():
    for status in ("Triaged", "Waiting on vendor", "New"):
        out = status_run(status, status, Title="Tap drips", Visit=FUTURE)
        f = one(out)
        assert f["Status"] == "Scheduled" and f["Status noted"] == "Scheduled|" + FUTURE
        assert f["Member-visible status"] == "Visit scheduled for Thu, Mar 5, 10:00 AM"
        [mail] = out["mails"]
        assert mail["subject"] == "Request R-12: visit scheduled"
        assert one(out, "Updates")["Text"] == \
            f"Status changed from {status} to Scheduled, for Thu, Mar 5, 10:00 AM."
        # And that's that: the next run finds nothing to do.
        again = run(out["tables"], "processRequests", props=out["props"])
        assert again["mails"] == [] and len(again["tables"]["Updates"]) == 1


def test_a_visit_time_doesnt_schedule_what_shouldnt_be():
    # A visit that's past (one being recorded), or on a request that's finished.
    for status, visit in (("Triaged", PAST), ("Done, to confirm", FUTURE), ("Closed", FUTURE)):
        out = status_run(status, status, Visit=visit)
        f = one(out)
        assert f["Status"] == status and f["Status noted"] == f"{status}|{visit}"
        assert out["mails"] == [] and out["tables"]["Updates"] == []
    # A request moved back from Scheduled keeps its old visit time, and isn't scheduled again by it.
    out = status_run("Waiting on vendor", "Scheduled|" + FUTURE, Visit=FUTURE)
    assert one(out)["Status"] == "Waiting on vendor" and out["mails"] == []
    assert one(out, "Updates")["Text"] == "Status changed from Scheduled to Waiting on vendor."
    # Until it's given a new one.
    state = out["tables"]
    state["Requests"][0]["fields"]["Visit"] = "2099-04-01T18:00:00.000Z"
    assert one(run(state, "processRequests", props=out["props"]))["Status"] == "Scheduled"


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
    out = status_run("Closed", "Triaged|" + PAST, Visit=PAST, Resolution="It was the washer.",
                     **{"Closed on": "2026-11-02"})
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


def test_update_made_in_airtable_is_given_its_author():
    def made_by(n, email, **fields):
        return {"id": f"upd{n}", "fields": {"Request": ["req12"], "Text": "Note",
                                            "Created by": {"id": "usr1", "email": email, "name": "X"}, **fields}}
    updates = [
        made_by(1, "Dana.S@gmail.example"),                       # either of a member's addresses
        made_by(2, "noreply@airtable.example"),                   # a form's public link: nobody
        made_by(3, "dana@example.org", **{"By name": "Maintenance tracker"}),   # the script's own
        made_by(4, "dana@example.org", By=["pGone"]),             # already says who
        made_by(5, "old@example.org"),                            # moved out
        {"id": "upd6", "fields": {"Request": ["req12"], "Text": "Note"}},
    ]
    out = run(tables([acknowledged(Status="Triaged", **{"Status noted": "Triaged"})], updates), "processRequests")
    by = {u["id"]: u["fields"].get("By") for u in out["tables"]["Updates"]}
    assert by == {"upd1": ["pDana"], "upd2": None, "upd3": None, "upd4": ["pGone"], "upd5": None, "upd6": None}
    assert out["mails"] == [] and out["calls"].count("patch Updates") == 1
    # Those that match nobody are looked at again, and nothing is written.
    again = run(out["tables"], "processRequests", props=out["props"])
    assert "patch Updates" not in again["calls"]


# --- plans -------------------------------------------------------------------

def test_plan_is_free_unless_set_to_team():
    for value, plan in [(None, "free"), ("", "free"), ("Team", "team"), (" team ", "team"), ("business", "free")]:
        props = dict(PROPS) if value is None else {**PROPS, "AIRTABLE_PLAN": value}
        assert run(tables(), expr="plan()", props=props)["result"] == plan


def test_timers_fit_the_plan():
    assert run(tables(), "installTriggers")["triggers"] == [
        "processRequests timeBased everyHours(2)",
        "housekeeping timeBased atHour(3) inTimezone(America/Los_Angeles) onWeekDay(SUNDAY)"]
    assert run(tables(), "installTriggers", props={**PROPS, "AIRTABLE_PLAN": "team"})["triggers"] == [
        "processRequests timeBased everyMinutes(10)",
        "housekeeping timeBased atHour(3) inTimezone(America/Los_Angeles) everyDays(1)"]


def test_free_plan_stays_inside_its_calls():
    # With nothing happening: a look for requests every 2 hours, for updates
    # every 6, and the weekly housekeeping (every table, and People and
    # Locations again for the form links).
    free = rule("PLANS.free")
    idle = 30 * (24 / free["checkEveryHours"] + 24 / free["updatesEveryHours"])
    housekeeping = 4.5 * (12 + 2)
    assert idle + housekeeping < 600  # of 1,000, leaving the rest for requests, and the scripts run by hand
    assert rule("updatesDue('free', t, new Date('2026-11-14T12:00:00Z'))", t="2026-11-14T06:00:30Z")
    assert not rule("updatesDue('free', t, new Date('2026-11-14T12:00:00Z'))", t="2026-11-14T08:00:00Z")
    assert rule("updatesDue('free', null, new Date())") and rule("updatesDue('team', t, new Date())", t="2099-01-01T00:00:00Z")


def test_people_and_locations_are_cached_within_a_run():
    out = run(tables([request(1, Reporter=["pDana"]), request(2, Reporter=["pDana"])]),
              "processRequests", "processRequests")
    assert out["calls"].count("get People") == 1 and out["calls"].count("get Locations") == 1
    assert out["calls"].count("get Links") == 1


def test_links_are_found_by_a_key_made_from_their_name():
    records = [{"fields": {"Name": "Request form", "Value": " https://x.example/f "}},
               {"fields": {"Name": "Triage view"}},
               {"fields": {"Name": "Tracker help (who to ask)", "Value": "Dana S"}}]
    assert rule("linksByKey(r)", r=records) == {"request-form": "https://x.example/f",
                                                 "tracker-help-who-to-ask": "Dana S"}


# --- how full the base is ----------------------------------------------------

MB = 1024 * 1024


def usage(records, megabytes, warned=0, plan="free"):
    report = rule("usageReport(c, b, p)", c={"Requests": records, "Updates": 0}, b=megabytes * MB, p=plan)
    return report, rule("usageWarning(r, w, p)", r=report, w=warned, p=plan)


def test_usage_levels():
    assert usage(699, 0)[0]["level"] == 0 and usage(699, 0)[1] is None
    assert usage(700, 0)[0]["level"] == 70
    assert usage(10, 880)[0]["level"] == 85      # attachments count as much as records
    assert usage(960, 100)[0]["level"] == 95
    assert usage(960, 900, plan="team")[0]["level"] == 0


def test_usage_warning_says_what_and_is_sent_once_a_level():
    report, mail = usage(712, 300)
    assert mail["subject"] == "Maintenance tracker: the Airtable base is 70% full"
    for text in ("Records: 712 of 1,000 (71%)", "Photos and attachments: 300 MB of 1,024 MB (29%)",
                 "the request form stops working", "AIRTABLE_PLAN property to team"):
        assert text in mail["body"]
    assert usage(712, 300, warned=70)[1] is None
    assert usage(860, 300, warned=70)[1]["subject"].endswith("85% full")
    assert "larger plan" in usage(36000, 0, plan="team")[1]["body"]


def test_backup_warns_the_circle_as_the_base_fills():
    photo = {"id": "att1", "url": "https://files.example/1", "filename": "a.jpg", "size": 900 * MB}
    state = tables([request(1, Photos=[photo]), request(2, Photos=[photo])])  # one photo, linked twice
    out = run(state, "backup")
    [mail] = out["mails"]
    assert mail["to"] == CIRCLE and "85% full" in mail["subject"] and "900 MB of 1,024 MB" in mail["body"]
    assert out["props"]["usageWarned"] == "85"
    # Not again at the same level; and the level falls back when the base empties.
    again = run(state, "backup", props=out["props"])
    assert again["mails"] == []
    emptied = run(tables(), "backup", props=out["props"])
    assert emptied["mails"] == [] and emptied["props"]["usageWarned"] == "0"


# --- the weekly digest -------------------------------------------------------

NOW = "2026-11-15T20:00:00.000Z"  # a Sunday, noon in the building


def days_ago(n):
    from datetime import datetime, timedelta, timezone
    then = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc) - timedelta(days=n)
    return then.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def digest_of(requests, updates=(), assets=(), locations=None, plan="free", claims=()):
    data = tables(requests, updates)
    data["Assets"] = list(assets)
    data["Claims"] = list(claims)
    if locations is not None:
        data["Locations"] = locations
    return rule("digest(d, new Date(n), c)", d=data, n=NOW,
                c={"timeZone": "America/Los_Angeles", "triageUrl": "https://airtable.example/triage", "plan": plan})


def old(number, status, age=30, **fields):
    return request(number, Status=status, Submitted=days_ago(age), Location=["loc411"], **fields)


def section(body, title):
    """The lines under a section's heading, without their indent."""
    lines = body.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith(title))
    out = []
    for l in lines[start + 1:]:
        if not l.startswith("  "):
            break
        out.append(l.strip())
    return out


def test_digest_is_nothing_when_nothing_is_open_or_new():
    assert digest_of([]) is None
    assert digest_of([old(1, "Closed"), old(2, "Declined")]) is None


def test_digest_sections():
    requests = [
        request(1, Status="New", Submitted=days_ago(2), Location=["loc411"], Description="Tap  drips\nbadly"),
        old(2, "New", 9, Title="Door sticks"),
        old(3, "Triaged", 5, Title="Fan rattles"),                      # no owner; new this week
        old(4, "Scheduled", 20, Title="No time", Owner=["pDana"]),
        old(5, "Scheduled", 20, Title="Missed", Owner=["pDana"], Visit=days_ago(3)),
        old(6, "Scheduled", 40, Title="Booked", Owner=["pDana"], Visit=days_ago(-10)),
        old(7, "Done, to confirm", 30, Title="Fixed?", Owner=["pDana"]),
        old(8, "Waiting on builder", 60, Title="Stalled", Owner=["pDana"]),
        old(9, "Waiting on vendor", 60, Title="Moving", Owner=["pDana"]),
        old(10, "Closed", 3, Title="Quick one"),
    ]
    updates = [{"id": "u1", "fields": {"Request": ["req7"], "Logged": days_ago(8)}},
               {"id": "u2", "fields": {"Request": ["req8"], "Logged": days_ago(15)}},
               {"id": "u3", "fields": {"Request": ["req9"], "Logged": days_ago(13)}}]
    mail = digest_of(requests, updates)
    assert mail["subject"] == "Maintenance digest: 3 new, 2 to triage, 9 open"
    body = mail["body"]
    assert section(body, "New this week (3)") == [
        "R-3 Unit 411: Fan rattles - Triaged", "R-10 Unit 411: Quick one - Closed", "R-1 Unit 411: Tap drips badly"]
    assert section(body, "To triage (2)") == [
        "R-2 Unit 411: Door sticks - waiting 9 days", "R-1 Unit 411: Tap drips badly - waiting 2 days"]
    assert section(body, "No owner (1)") == ["R-3 Unit 411: Fan rattles - Triaged"]
    assert section(body, "Scheduled, but with no visit time (1)") == ["R-4 Unit 411: No time (Dana S)"]
    assert section(body, "Visit has passed") == ["R-5 Unit 411: Missed (Dana S) - visit was Thu, Nov 12, 12:00 PM"]
    assert section(body, "Done, and waiting to be confirmed") == ["R-7 Unit 411: Fixed? (Dana S) - for 8 days"]
    # Not the scheduled ones, which have their own sections, nor the one touched 13 days ago.
    assert section(body, "Gone quiet") == ["R-8 Unit 411: Stalled (Dana S) - Waiting on builder, 15 days"]
    assert "Repeats" not in body
    assert ("Open requests: 9 (2 New, 1 Triaged, 1 Waiting on builder, 1 Waiting on vendor, "
            "3 Scheduled, 1 Done, to confirm)") in body
    assert "The base holds 20 of the 1,000 records its plan allows, and 0 MB of 1,024 MB of attachments." in body
    assert body.endswith("Triage: https://airtable.example/triage")


def test_digest_with_nothing_needing_attention():
    mail = digest_of([old(1, "Triaged", 10, Title="In hand", Owner=["pDana"])])
    assert mail["subject"] == "Maintenance digest: 1 open"
    assert mail["body"].startswith("Nothing needs attention this week.\n\nOpen requests: 1 (1 Triaged)")


def test_digest_repeats():
    locations = [{"id": f"loc{n}", "fields": {"Name": f"Unit {n}", "Stack": n % 100}}
                 for n in (204, 304, 404, 211, 311, 108)] + [{"id": "locRoof", "fields": {"Name": "Roof"}}]
    assets = [{"id": "fan", "fields": {"Name": "Bath fan FV-08"}}]

    def at(number, unit, **fields):
        base = {"Status": "Triaged", "Submitted": days_ago(20), "Location": [f"loc{unit}"]}
        return request(number, **{**base, **fields})

    requests = [
        # One fan model in three units (two requests from one of them), whatever the room.
        at(1, 204, Component=["fan"], System="HVAC & ventilation", Room="Bath"),
        at(2, 211, Component=["fan"], System="HVAC & ventilation", Room="Bath"),
        at(3, 211, Component=["fan"], System="HVAC & ventilation", Room="Laundry"),
        at(4, 108, Component=["fan"], System="HVAC & ventilation", Status="Closed"),
        # The same system and room in three units, with no component set.
        at(5, 204, System="Electrical & lighting", Room="Kitchen"),
        at(6, 311, System="Electrical & lighting", Room="Kitchen"),
        at(7, 108, System="Electrical & lighting", Room="Kitchen"),
        # Plumbing twice in stack 4: enough, for plumbing. Electrical twice in stack 11: not.
        at(8, 304, System="Plumbing", Room="Bath"),
        at(9, 404, System="Plumbing", Room="Kitchen"),
        # Not counted: a duplicate, a question, one that's too old, one in only two places.
        at(10, 304, Component=["fan"], Status="Duplicate"),
        at(11, 404, Component=["fan"], Type="How do I…?"),
        at(12, 311, Component=["fan"], Submitted=days_ago(200)),
        at(13, 204, System="Floors", Room="Entry"), at(14, 211, System="Floors", Room="Entry"),
    ]
    body = digest_of(requests, assets=assets, locations=locations)["body"]
    assert section(body, "Repeats: the same thing in several places (3)") == [
        "Bath fan FV-08: 3 places - Unit 108 (R-4); Unit 204 (R-1); Unit 211 (R-2, R-3)",
        "Electrical & lighting, Kitchen: 3 places - Unit 108 (R-7); Unit 204 (R-5); Unit 311 (R-6)",
        "Plumbing in stack 4: 2 places - Unit 304 (R-8); Unit 404 (R-9)",
    ]


def claim(number, status, **fields):
    return {"id": f"claim{number}", "fields": {"Number": number, "Status": status, **fields}}


def test_digest_warranty_deadlines():
    def due(number, in_days, status="Triaged", **fields):
        # As the base's formula gives it: a date at midnight UTC.
        deadline = days_ago(-in_days)[:10] + "T00:00:00.000Z"
        return old(number, status, Title=f"T{number}", Owner=["pDana"], **{"Claim deadline": deadline}, **fields)

    requests = [due(1, 91), due(2, 90), due(3, 12), due(4, 0), due(5, -3),
                due(6, 5, Claims=["claim1"]),               # claimed
                due(7, 5, Claims=["claim2"]),               # only drafted
                due(8, 5, Claims=["claim3"]),               # withdrawn
                due(9, 5, status="Closed"),                 # no longer open
                old(10, "Triaged", Title="No warranty", Owner=["pDana"])]
    claims = [claim(1, "Submitted", **{"Submitted on": days_ago(1)[:10]}), claim(2, "Drafting"), claim(3, "Withdrawn")]
    body = digest_of(requests, claims=claims)["body"]
    assert section(body, "Warranty deadline within 90 days, and no claim made (6)") == [
        "R-5 Unit 411: T5 (Dana S) - claim by Nov 12, 2026 (PASSED 3 days ago)",
        "R-4 Unit 411: T4 (Dana S) - claim by Nov 15, 2026 (TODAY)",
        "R-7 Unit 411: T7 (Dana S) - claim by Nov 20, 2026 (5 days left)",
        "R-8 Unit 411: T8 (Dana S) - claim by Nov 20, 2026 (5 days left)",
        "R-3 Unit 411: T3 (Dana S) - claim by Nov 27, 2026 (12 days left)",
        "R-2 Unit 411: T2 (Dana S) - claim by Feb 13, 2027 (90 days left)",
    ]


def test_digest_claims_waiting_on_the_builder():
    claims = [
        claim(1, "Submitted", Title="Taps", **{"Submitted on": "2026-11-01"}),          # 14 days: listed
        claim(2, "Submitted", Title="Fans", **{"Submitted on": "2026-11-02"}),          # 13 days: not yet
        claim(3, "Acknowledged", Title="Roof", **{"Submitted on": "2026-10-01", "Promised by": "2026-11-14"}),
        claim(4, "Scheduled", Title="Door", **{"Promised by": "2026-11-15"}),           # today: not past
        claim(5, "Work done", Title="Done", **{"Promised by": "2026-10-01"}),           # nothing owed
        claim(6, "Drafting", Title="Draft", **{"Promised by": "2026-10-01"}),
    ]
    body = digest_of([old(1, "Triaged", Owner=["pDana"], Title="x")], claims=claims)["body"]
    assert section(body, "Claims the builder hasn't acknowledged in 14 days (1)") == ["C-1: Taps - sent Nov 1, 2026"]
    assert section(body, "Claims past the date the builder promised (1)") == ["C-3: Roof - promised by Nov 14, 2026"]


def test_digest_repeats_leave_out_what_a_systemic_claim_covers():
    locations = [{"id": f"loc{n}", "fields": {"Name": f"Unit {n}"}} for n in (201, 202, 203, 204)]

    def fan(number, unit, **fields):
        return request(number, Status="Triaged", Submitted=days_ago(5), Location=[f"loc{unit}"],
                       System="HVAC & ventilation", Room="Bath", **fields)

    requests = [fan(1, 201, Claims=["claim1"]), fan(2, 202, Claims=["claim1"]), fan(3, 203, Claims=["claim1"]),
                fan(4, 204)]
    for status, repeats in (("Submitted", False), ("Denied", True)):
        body = digest_of(requests, locations=locations, claims=[claim(1, status, Scope="Systemic")])["body"]
        assert ("Repeats" in body) == repeats
    # A claim for one unit doesn't cover the building.
    assert "Repeats" in digest_of(requests, locations=locations, claims=[claim(1, "Submitted", Scope="Unit")])["body"]


def test_digest_is_due_on_sundays_once():
    tz = "America/Los_Angeles"
    assert rule("digestDue(new Date(n), null, tz)", n=NOW, tz=tz)
    assert not rule("digestDue(new Date(n), l, tz)", n=NOW, l="2026-11-15T11:00:00.000Z", tz=tz)  # 3 am that day
    assert rule("digestDue(new Date(n), l, tz)", n=NOW, l=days_ago(7), tz=tz)
    assert not rule("digestDue(new Date(n), null, tz)", n="2026-11-16T20:00:00.000Z", tz=tz)       # a Monday
    # Sunday evening here is already Monday in UTC.
    assert rule("digestDue(new Date(n), null, tz)", n="2026-11-16T04:00:00.000Z", tz=tz)


def test_send_digest_emails_the_circle():
    out = run(tables([request(1, Submitted=days_ago(1), Location=["loc411"])]), "sendDigest")
    [mail] = out["mails"]
    assert mail["to"] == CIRCLE and mail["subject"].startswith("Maintenance digest: ")
    assert out["props"]["lastDigest"]
    assert run(tables(), "sendDigest")["mails"] == []


def test_housekeeping_reads_each_table_once():
    out = run(tables(people=[{"id": "p2", "fields": {"Name": "Sam L"}}]), "housekeeping")
    gets = [c for c in out["calls"] if c.startswith("get ")]
    assert sorted(gets) == sorted(f"get {t}" for t in out["tables"])
    # The backup has the link that the same run wrote.
    [folder] = out["drive"].values()
    [day] = [v for k, v in folder.items() if k != "Attachments/"]
    assert "prefill_Reporter=Sam+L" in day["People.json"]


# --- maintenance work --------------------------------------------------------

def plan(key, **fields):
    return {"id": key, "fields": {k.replace("_", " "): v for k, v in fields.items()}}


def work(key, plan_id, status=None, **fields):
    made = {"Plan": [plan_id], **{k.replace("_", " "): v for k, v in fields.items()}}
    if status:
        made["Status"] = status
    return {"id": key, "fields": made}


def work_due(plans, occurrences=()):
    data = {"Maintenance plans": plans, "Maintenance work": list(occurrences)}
    return [w["fields"] for w in rule("maintenanceWorkDue(data)", data=data)]


def test_a_plan_with_no_occurrence_gets_its_first():
    monthly = {"Every (months)": 1}
    assert work_due([plan("alarm", First_due="2027-01-20", **monthly)]) == [
        {"Plan": ["alarm"], "Due": "2027-01-20", "Status": "Due"}]
    assert work_due([plan("alarm", **monthly)]) == []  # no First due yet


def test_the_next_occurrence_follows_the_last_ones_due_date():
    drains = plan("drains", First_due="2026-04-15", **{"Every (months)": 6})
    done = [work("w1", "drains", "Done", Due="2026-04-15", Done_on="2026-04-12"),
            work("w2", "drains", "Done", Due="2026-10-15", Done_on="2026-11-20")]  # late: the plan keeps its dates
    assert work_due([drains], done) == [{"Plan": ["drains"], "Due": "2027-04-15", "Status": "Due"}]
    assert work_due([drains], done[:1] + [work("w2", "drains", "Skipped", Due="2026-10-15")]) == [
        {"Plan": ["drains"], "Due": "2027-04-15", "Status": "Due"}]
    # With no Due, the day it was done is counted from.
    assert work_due([drains], [work("w1", "drains", "Done", Done_on="2026-05-02")])[0]["Due"] == "2026-11-02"
    assert work_due([drains], [work("w1", "drains", "Done")]) == []


def test_a_plan_with_an_occurrence_waiting_gets_no_other():
    fans = plan("fans", First_due="2026-07-15", **{"Every (months)": 6})
    skipped = work("w1", "fans", "Skipped", Due="2026-07-15")
    for waiting in [work("w2", "fans", "Due", Due="2027-01-15"),
                    work("w2", "fans", "Scheduled", Due="2026-11-20"),  # moved by hand
                    work("w2", "fans", Due="2027-01-15")]:  # no Status
        assert work_due([fans], [skipped, waiting]) == []


def test_a_plan_with_no_interval_happens_once():
    walk = plan("walk", First_due="2027-11-01")
    assert [w["Due"] for w in work_due([walk])] == ["2027-11-01"]
    assert work_due([walk], [work("w1", "walk", "Done", Due="2027-11-01")]) == []
    assert work_due([plan("walk", First_due="2027-11-01", **{"Every (months)": 0})],
                    [work("w1", "walk", "Done", Due="2027-11-01")]) == []


def test_months_are_added_to_a_date():
    assert rule("addMonths('2026-11-10', 1)") == "2026-12-10"
    assert rule("addMonths('2026-12-10', 1)") == "2027-01-10"
    assert rule("addMonths('2026-08-31', 6)") == "2027-02-28"  # no February 31st
    assert rule("addMonths('2027-08-31', 6)") == "2028-02-29"
    assert rule("addMonths('2026-03-01', 36)") == "2029-03-01"


def test_housekeeping_creates_the_occurrences_and_backs_them_up():
    state = tables()
    state["Maintenance plans"] = [plan("alarm", Task="Alarm test", First_due="2026-12-01", **{"Every (months)": 1}),
                                  plan("drains", Task="Clear drains", **{"Every (months)": 6})]
    state["Maintenance work"] = [work("w1", "drains", "Done", Due="2026-10-15")]
    out = run(state, "housekeeping")
    made = [w["fields"] for w in out["tables"]["Maintenance work"][1:]]
    assert made == [{"Plan": ["alarm"], "Due": "2026-12-01", "Status": "Due"},
                    {"Plan": ["drains"], "Due": "2027-04-15", "Status": "Due"}]
    assert out["calls"].count("post Maintenance work") == 1
    [folder] = out["drive"].values()
    [day] = [v for k, v in folder.items() if k != "Attachments/"]
    assert len(json.loads(day["Maintenance work.json"])) == 3
    # Nothing more is made while those wait to be done.
    again = run(out["tables"], "createMaintenanceWork")
    assert len(again["tables"]["Maintenance work"]) == 3 and "post Maintenance work" not in again["calls"]


# --- form links --------------------------------------------------------------

def test_form_link_is_the_tested_shape():
    assert rule("formLink(u, 'Pat S', 'Unit 411')", u=FORM) == \
        FORM + "?prefill_Reporter=Pat+S&prefill_Location=Unit+411"
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
    links = {k: v for k, v in LINKS.items() if k != "Request form"}
    assert run(tables(people=people, links=links), "writeFormLinks")["calls"] == ["get Links"]


def test_circle_members_get_a_link_to_the_update_form():
    update_form = "https://airtable.example/update"
    people = [
        {"id": "p1", "fields": {"Name": "Dana S", "Circle member": True, "Request form link": "keep"}},
        {"id": "p2", "fields": {"Name": "Sam L", "Update form link": "https://stale"}},
        {"id": "p3", "fields": {"Name": "Old T", "Circle member": True, "Moved out": True,
                                "Update form link": "https://stale"}},
    ]
    # Only the update form's address is set: the request form's links are left alone.
    links = {**{k: v for k, v in LINKS.items() if k != "Request form"}, "Update form": update_form}
    out = run(tables(people=people, links=links), "writeFormLinks")
    fields = {p["id"]: p["fields"] for p in out["tables"]["People"]}
    assert fields["p1"]["Update form link"] == update_form + "?prefill_By=Dana+S"
    assert fields["p1"]["Request form link"] == "keep"
    assert "Update form link" not in fields["p2"] and "Update form link" not in fields["p3"]
    # Both set: one write to a person covers both links.
    out = run(tables(people=[{"id": "p1", "fields": {"Name": "Dana S", "Circle member": True}}],
                     links={**LINKS, "Update form": update_form}), "writeFormLinks")
    assert set(out["tables"]["People"][0]["fields"]) >= {"Request form link", "Update form link"}
    assert out["calls"].count("patch People") == 1


# --- backup ------------------------------------------------------------------

def test_housekeeping_writes_links_and_backs_up():
    out = run(tables(people=[{"id": "p2", "fields": {"Name": "Sam L"}}]), "housekeeping")
    assert out["tables"]["People"][0]["fields"]["Request form link"] == FORM + "?prefill_Reporter=Sam+L"
    assert "Building Maintenance backup/" in out["drive"]


def test_backup_saves_every_table_and_each_attachment_once():
    photo = {"id": "att1", "url": "https://files.example/1", "filename": "leak.jpg"}
    state = tables([request(Photos=[photo])])
    # Twice in a day: the second run's files replace the first's.
    out = run(state, "backup", "backup")
    assert out["mails"] == []  # nowhere near full
    # In a folder the script made for itself, which it remembers.
    [folder] = out["drive"]
    assert folder == "Building Maintenance backup/" and out["props"]["backupFolderId"]
    backup = out["drive"][folder]
    [day] = [k for k in backup if re.fullmatch(r"\d{4}-\d\d-\d\d/", k)]
    assert sorted(backup[day]) == sorted(f"{t}.json" for t in state)
    saved = json.loads(backup[day]["Requests.json"])
    assert saved[0]["fields"]["Description"] == "Tap drips"
    assert backup["Attachments/"] == {"att1 leak.jpg": "bytes of https://files.example/1"}


def test_script_may_touch_only_the_drive_files_it_makes():
    manifest = json.loads((PROJECT / "appsscript.json").read_text())
    drive = [s for s in manifest["oauthScopes"] if "drive" in s]
    assert drive == ["https://www.googleapis.com/auth/drive.file"]
    # DriveApp needs the whole of Drive; the script uses the Drive service instead.
    assert "DriveApp" not in (PROJECT / "maintenance_tracker.gs").read_text().replace("DriveApp needs", "")
