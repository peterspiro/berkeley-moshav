"""
Moving a group's meetings onto its new calendar.

When a group gets its own calendar (or when asked, with
update_groups_in_google_and_hierarchy.py --move-events), its meetings may
still be on other calendars. offer_event_moves() looks through every
calendar on CALENDAR_OWNER's calendar list (its own, including its primary
calendar, and the ones it's subscribed to) except the group calendars, the
all-events calendar, and Google's built-in ones, for events in the next
EVENT_WINDOW_DAYS whose titles match the group's name. It lists them all,
then asks of each whether to copy it to the group's calendar, delete it,
both, or to skip it:

- A copy keeps the title, description, location, times, and repeat rule
  (a series' copy starts at its next occurrence, and ends when it would),
  but not the guests (group_meetings invites the group) or any Zoom or Meet
  link (so group_meetings adds the group's room). Occurrences of a series
  that were moved or changed become one-off events, and cancelled ones are
  left out. Copies are tagged with the original's iCalUID, so a later run
  doesn't offer them again.
- Deleting needs edit access to the calendar the event is on. An event
  organized there is cancelled for everyone, with Google's cancellation
  emails; an invitation copy is removed from that calendar only.

An event on several of these calendars (say, organized on one, and an
invitation on the owner's primary calendar) is offered once, from the
calendar that organizes it if that's one of them. Nothing changes until the
end, when it shows what it will do and, if anything will be deleted, asks
you to type 'delete'.
"""

import datetime
import re
from collections.abc import Callable
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from util.folder_matching import find_matching_names
from util.gather_utils import log
from util.google_group_footer import format_meeting_id
from util.meeting_rooms import (
    ALL_EVENTS_CALENDAR_ID,
    CALENDAR_TIME_ZONE,
    QuitRequested,
    room_candidates,
)

EVENT_WINDOW_DAYS = 365

# Where a copy records the event it was copied from.
COPIED_FROM_KEY = "copiedFrom"

EDIT_ROLES = ("owner", "writer")

# Same links group_meetings/event_rules.gs counts as "another meeting link".
_MEETING_LINK_RE = re.compile(r"[\w.-]*zoom\.us/j/\d+|meet\.google\.com/[a-z-]+", re.I)
# Lines of a pasted Zoom or Meet invitation, removed along with the link.
_MEETING_LINE_RE = re.compile(
    r"zoom\.us/|meet\.google\.com/|^\s*(join zoom meeting|join with google meet|"
    r"meeting id\b|passcode\b|password\b|one tap mobile|dial by your location|"
    r"find your local number|or dial\b|\+\d[\d ,#*]{6,})", re.I)
_LINE_BREAK_RE = re.compile(r"\n|<br\s*/?>", re.I)
# A Zoom meeting's ID, in its link or written out ("Meeting ID: 812 3456 7890").
_ZOOM_ID_RE = re.compile(r"zoom\.us/j/(\d{9,11})|meeting id:?\s*(\d{3}[ \d]{6,10}\d)", re.I)


# ── Which calendars to look in ────────────────────────────────────────────────

def _is_builtin(calendar_id: str) -> bool:
    """Google's holiday, birthday, and week-number calendars."""
    return calendar_id.endswith("group.v.calendar.google.com") or "#contacts@" in calendar_id


def other_calendars(calendar_service, group_calendar_ids: set[str]) -> list[dict]:
    """[{"id", "name", "role", "primary"}] for every calendar on the signed-in
    account's list except group calendars, the all-events calendar,
    Google's built-in ones, and ones shared as free/busy only."""
    calendars = []
    page_token = None
    while True:
        resp = calendar_service.calendarList().list(pageToken=page_token).execute()
        for c in resp.get("items", []):
            cid = c["id"]
            if (cid in group_calendar_ids or cid == ALL_EVENTS_CALENDAR_ID or _is_builtin(cid)
                    or c.get("accessRole") == "freeBusyReader"):  # no titles to match
                continue
            calendars.append({
                "id": cid, "name": c.get("summaryOverride") or c.get("summary") or cid,
                "role": c.get("accessRole", "reader"), "primary": bool(c.get("primary")),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            return calendars


def _list_events(calendar_service, calendar_id: str, **params) -> list[dict]:
    """The calendar's events, or [] (with a warning) if it can't be read."""
    events = []
    page_token = None
    while True:
        try:
            resp = calendar_service.events().list(
                calendarId=calendar_id, pageToken=page_token, **params).execute()
        except HttpError as err:
            log("WARN", "list_events", f"{calendar_id}: {err}")
            return []
        events += resp.get("items", [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return events


def upcoming_events(calendar_service, calendar_id: str, now: datetime.datetime) -> list[dict]:
    """The calendar's events in the next EVENT_WINDOW_DAYS, with each
    recurring series once (not per occurrence), plus its changed and
    cancelled occurrences (which have "recurringEventId")."""
    return _list_events(
        calendar_service, calendar_id, singleEvents=False, showDeleted=True,
        timeMin=now.isoformat(),
        timeMax=(now + datetime.timedelta(days=EVENT_WINDOW_DAYS)).isoformat(),
    )


def copied_event_uids(calendar_service, group_calendar_ids: set[str]) -> set[str]:
    """iCalUIDs of the events already copied to a group calendar."""
    uids = set()
    for calendar_id in group_calendar_ids:
        for e in _list_events(calendar_service, calendar_id, showDeleted=False):
            uid = ((e.get("extendedProperties") or {}).get("private") or {}).get(COPIED_FROM_KEY)
            if uid:
                uids.add(uid)
    return uids


# ── Gathering candidate events ────────────────────────────────────────────────

def _organizer_is(event: dict, calendar_id: str) -> bool:
    organizer = event.get("organizer") or {}
    return bool(organizer.get("self")) or organizer.get("email") == calendar_id


def gather_candidates(
    events_by_calendar: dict[str, list[dict]], calendars: list[dict],
    group_calendar_ids: set[str],
) -> list[dict]:
    """One candidate per event (by iCalUID) across the calendars:
    {"uid", "event", "calendar", "organized_here", "also_on", "changed",
    "cancelled"}, where event is a one-off event or a series, taken from
    the calendar that organizes it when that's one of them, and changed /
    cancelled are the series' changed and cancelled occurrences there.
    Events organized by a group calendar are left out (they're already
    the group's)."""
    by_id = {c["id"]: c for c in calendars}
    groups: dict[str, list[tuple[dict, dict]]] = {}
    exceptions: dict[tuple[str, str], list[dict]] = {}
    for calendar_id, events in events_by_calendar.items():
        for e in events:
            if e.get("recurringEventId"):
                exceptions.setdefault((calendar_id, e["recurringEventId"]), []).append(e)
            elif e.get("status") != "cancelled":
                if (e.get("organizer") or {}).get("email") in group_calendar_ids:
                    continue
                groups.setdefault(e.get("iCalUID") or e["id"], []).append(
                    (by_id[calendar_id], e))

    candidates = []
    for uid, copies in groups.items():
        calendar, event = next(((c, e) for c, e in copies if _organizer_is(e, c["id"])),
                               copies[0])
        occurrences = exceptions.get((calendar["id"], event["id"]), [])
        candidates.append({
            "uid": uid, "event": event, "calendar": calendar,
            "organized_here": _organizer_is(event, calendar["id"]),
            "also_on": [c["name"] for c, _ in copies if c is not calendar],
            "changed": [o for o in occurrences if o.get("status") != "cancelled"],
            "cancelled": [o for o in occurrences if o.get("status") == "cancelled"],
            "zoom_ids": zoom_meeting_ids(event),
        })
    return candidates


def zoom_meeting_ids(event: dict) -> set[str]:
    """IDs of the Zoom meetings the event's location, description, or
    conference details link or name."""
    texts = [event.get("location") or "", event.get("description") or ""]
    texts += [p.get("uri") or "" for p in
              (event.get("conferenceData") or {}).get("entryPoints") or []]
    return {(link or written).replace(" ", "")
            for text in texts for link, written in _ZOOM_ID_RE.findall(text)}


def matching_candidates(group_name: str, candidates: list[dict]) -> list[dict]:
    """The candidates whose titles match the group's name, soonest first."""
    items = [{"id": c["uid"], "name": c["event"].get("summary") or ""} for c in candidates]
    matched = {m["id"] for m in find_matching_names(group_name, items)}
    return sorted((c for c in candidates if c["uid"] in matched),
                  key=lambda c: _start_key(c["event"]))


# ── Describing events ─────────────────────────────────────────────────────────

_DAY_CODES = {"SU": "Sun", "MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri",
              "SA": "Sat"}
_ORDINALS = {"1": "1st", "2": "2nd", "3": "3rd", "4": "4th", "5": "5th", "-1": "last"}
_FREQ = {"DAILY": ("daily", "days"), "WEEKLY": ("weekly", "weeks"),
         "MONTHLY": ("monthly", "months"), "YEARLY": ("yearly", "years")}


def _start_key(event: dict) -> str:
    start = event.get("start") or {}
    return start.get("dateTime") or start.get("date") or ""


def _local(dt: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(dt.replace("Z", "+00:00")).astimezone(
        ZoneInfo(CALENDAR_TIME_ZONE))


def _day(d: datetime.date) -> str:
    return f"{d:%a %b} {d.day}"


def _clock(dt: datetime.datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _times(start: dict, end: dict) -> str:
    """"7:00–8:30 PM", "11:00 AM–1:00 PM", or "all day"."""
    if start.get("date"):
        return "all day"
    begin = _local(start["dateTime"])
    if not end.get("dateTime"):
        return _clock(begin)
    finish = _local(end["dateTime"])
    if begin.strftime("%p") == finish.strftime("%p"):
        return f"{_clock(begin)[:-3]}–{_clock(finish)}"
    return f"{_clock(begin)}–{_clock(finish)}"


def _first_day(start: dict) -> datetime.date:
    return (datetime.date.fromisoformat(start["date"]) if start.get("date")
            else _local(start["dateTime"]).date())


def _when(start: dict, end: dict) -> str:
    """"Mon Nov 2, 7:00–8:30 PM", "Mon Nov 2, 11:00 AM–1:00 PM", "Mon Nov
    2, all day", "Mon Nov 2 – Wed Nov 4, all day", or for a timed event
    spanning days "Mon Nov 2, 10:00 PM – Tue Nov 3, 1:00 AM"."""
    if start.get("date"):
        first = datetime.date.fromisoformat(start["date"])
        # An all-day event's end date is the day after its last day.
        last = datetime.date.fromisoformat(end.get("date") or start["date"]) \
            - datetime.timedelta(days=1)
        days = _day(first) if last <= first else f"{_day(first)} – {_day(last)}"
        return f"{days}, all day"
    begin = _local(start["dateTime"])
    if end.get("dateTime") and _local(end["dateTime"]).date() != begin.date():
        finish = _local(end["dateTime"])
        return f"{_day(begin)}, {_clock(begin)} – {_day(finish)}, {_clock(finish)}"
    return f"{_day(begin)}, {_times(start, end)}"


def describe_rrule(recurrence: list[str]) -> str:
    """A short description of an event's RRULE: "weekly on Tue",
    "monthly on the 3rd Mon", "every 2 weeks on Mon, Wed", ..."""
    rule = next((r for r in recurrence if r.startswith("RRULE:")), None)
    if rule is None:
        return "repeats"
    parts = dict(p.split("=", 1) for p in rule[len("RRULE:"):].split(";") if "=" in p)
    every, unit = _FREQ.get(parts.get("FREQ"), ("repeats", ""))
    interval = parts.get("INTERVAL", "1")
    text = every if interval == "1" or not unit else f"every {interval} {unit}"
    days = []
    for code in filter(None, parts.get("BYDAY", "").split(",")):
        m = re.fullmatch(r"([+-]?\d)?([A-Z]{2})", code)
        if m and m.group(2) in _DAY_CODES:
            ordinal = _ORDINALS.get((m.group(1) or "").lstrip("+"), "")
            days.append(f"the {ordinal} {_DAY_CODES[m.group(2)]}" if ordinal
                        else _DAY_CODES[m.group(2)])
    if days:
        return f"{text} on {', '.join(days)}"
    if parts.get("BYMONTHDAY"):
        return f"{text} on day {parts['BYMONTHDAY']}"
    return text


def describe_candidate(c: dict) -> str:
    """What, when, and where: "Finance Circle meeting   (monthly on the 2nd
    Tue, 7:00–8:30 PM, from Tue Jan 10, 2023 (a copy would start Tue Nov
    10, 2026); on Treasurer's calendar)"."""
    e = c["event"]
    if e.get("recurrence"):
        first = _first_day(e["start"])
        when = (f"{describe_rrule(e['recurrence'])}, {_times(e['start'], e.get('end') or {})}, "
                f"from {_day(first)}, {first.year}")
        copy_start = c.get("copy_start")
        if copy_start and _first_day(copy_start[0]) != first:
            day = _first_day(copy_start[0])
            when += f" (a copy would start {_day(day)}, {day.year})"
    else:
        when = _when(e["start"], e.get("end") or {})
    where = f"on {c['calendar']['name']}"
    if not c["organized_here"]:
        organizer = (e.get("organizer") or {}).get("email", "someone else")
        where += f", an invitation from {organizer}"
    if c["also_on"]:
        where += f"; also on {', '.join(c['also_on'])}"
    extra = f"; {len(c['changed'])} changed occurrence(s)" if c["changed"] else ""
    for meeting_id, topic in c.get("matching_zoom", []):
        extra += f"; in matching Zoom meeting {format_meeting_id(meeting_id)} '{topic}'"
    return f"{e.get('summary') or '(no title)'}   ({when}; {where}{extra})"


def can_delete(c: dict) -> bool:
    return c["calendar"]["role"] in EDIT_ROLES


# ── Copying ───────────────────────────────────────────────────────────────────

def strip_meeting_links(text: str | None) -> tuple[str, bool]:
    """Remove the lines of a description or location that hold a Zoom or
    Meet link or the rest of a pasted invitation (meeting ID, passcode,
    dial-in numbers). Returns (text, whether a link was removed)."""
    if not text or not _MEETING_LINK_RE.search(text):
        return text or "", False
    html = bool(re.search(r"<br\s*/?>", text, re.I))
    lines = [line for line in _LINE_BREAK_RE.split(text) if not _MEETING_LINE_RE.search(line)]
    while lines and not lines[-1].strip():
        lines.pop()
    return ("<br>" if html else "\n").join(lines).strip(), True


def _exdate(occurrence: dict) -> str:
    original = occurrence["originalStartTime"]
    if original.get("date"):
        return "EXDATE;VALUE=DATE:" + original["date"].replace("-", "")
    dt = datetime.datetime.fromisoformat(original["dateTime"].replace("Z", "+00:00"))
    return "EXDATE:" + dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _copy_body(event: dict, uid: str) -> dict:
    description, _ = strip_meeting_links(event.get("description"))
    location, _ = strip_meeting_links(event.get("location"))
    body = {
        "summary": event.get("summary", ""),
        "description": description,
        "location": location,
        "start": event["start"],
        "end": event["end"],
        "extendedProperties": {"private": {COPIED_FROM_KEY: uid}},
    }
    for key in ("transparency", "visibility", "colorId"):
        if key in event:
            body[key] = event[key]
    return body


def _shifted(start: dict, end: dict, slot: dict) -> tuple[dict, dict]:
    """start and end moved to begin at slot (an occurrence's
    originalStartTime), keeping the event's length and time zone."""
    if start.get("date"):
        days = (datetime.date.fromisoformat(end["date"])
                - datetime.date.fromisoformat(start["date"]))
        new_start = datetime.date.fromisoformat(slot["date"])
        return {"date": new_start.isoformat()}, {"date": (new_start + days).isoformat()}
    length = (datetime.datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
              - datetime.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")))
    new_start = datetime.datetime.fromisoformat(slot["dateTime"].replace("Z", "+00:00"))
    zone = {"timeZone": start["timeZone"]} if start.get("timeZone") else {}
    return ({"dateTime": new_start.isoformat(), **zone},
            {"dateTime": (new_start + length).isoformat(), **zone})


def _until(slot: dict) -> str:
    if slot.get("date"):
        return slot["date"].replace("-", "")
    dt = datetime.datetime.fromisoformat(slot["dateTime"].replace("Z", "+00:00"))
    return dt.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def plan_series_copy(calendar_service, c: dict, now: datetime.datetime) -> None:
    """For a series, set c["copy_start"] to the (start, end) of its next
    occurrence, where its copy begins, and, if the series ends after a
    number of occurrences, c["copy_until"] to its last occurrence, so the
    copy ends with it."""
    e = c["event"]
    if not e.get("recurrence"):
        return
    calendar_id = c["calendar"]["id"]
    upcoming = calendar_service.events().instances(
        calendarId=calendar_id, eventId=e["id"], timeMin=now.isoformat(),
        maxResults=1).execute().get("items", [])
    if upcoming:
        c["copy_start"] = _shifted(e["start"], e["end"], upcoming[0]["originalStartTime"])
    if any(r.startswith("RRULE:") and "COUNT=" in r for r in e["recurrence"]):
        slots, page_token = [], None
        while True:
            resp = calendar_service.events().instances(
                calendarId=calendar_id, eventId=e["id"], showDeleted=True,
                pageToken=page_token).execute()
            slots += [i["originalStartTime"] for i in resp.get("items", [])]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        if slots:
            c["copy_until"] = max(slots, key=_until)


def _copy_recurrence(c: dict) -> list[str]:
    rules = []
    for rule in c["event"]["recurrence"]:
        if rule.startswith("RRULE:") and c.get("copy_until"):
            parts = [p for p in rule[len("RRULE:"):].split(";") if not p.startswith("COUNT=")]
            rule = "RRULE:" + ";".join(parts + [f"UNTIL={_until(c['copy_until'])}"])
        rules.append(rule)
    return rules + [_exdate(o) for o in c["changed"] + c["cancelled"]]


def copy_bodies(c: dict) -> list[dict]:
    """The events to insert on the group calendar for candidate c: the
    event or series (starting at its next occurrence, if
    plan_series_copy() found one, with its changed and cancelled
    occurrences excluded), then each changed occurrence as a one-off
    event."""
    e = c["event"]
    main = _copy_body(e, c["uid"])
    if e.get("recurrence"):
        if c.get("copy_start"):
            main["start"], main["end"] = c["copy_start"]
        main["recurrence"] = _copy_recurrence(c)
    return [main] + [_copy_body(o, c["uid"]) for o in c["changed"]]


def had_meeting_link(c: dict) -> bool:
    e = c["event"]
    return any(strip_meeting_links(e.get(k))[1] for k in ("description", "location"))


# ── Asking ────────────────────────────────────────────────────────────────────

_ACTIONS = {"c": "copy", "d": "delete", "b": "both", "s": "skip"}


def _ask(label: str, deletable: bool, dry_run: bool, input_fn: Callable[[str], str]) -> str:
    keys = ["c", "d", "b", "s"] if deletable else ["c", "s"]
    names = {"c": "[c]opy to the group's calendar", "d": "[d]elete",
             "b": "[b]oth", "s": "[s]kip"}
    note = ""
    if dry_run:
        note = (" (DRY-RUN: NOTHING WILL BE COPIED OR DELETED)" if deletable
                else " (DRY-RUN: NOTHING WILL BE COPIED)")
    prefix = "[dry-run] " if dry_run else ""
    prompt = f"  {prefix}{label} — {', '.join(names[k] for k in keys)}, or [q]uit{note}: "
    while True:
        answer = input_fn(prompt).strip().lower()
        if answer in ("q", "quit"):
            raise QuitRequested
        for k in keys:
            if answer in (k, _ACTIONS[k]):
                return _ACTIONS[k]
        print("  Invalid choice.")


def decide_on_events(
    group_name: str, matches: list[dict], dry_run: bool, input_fn: Callable[[str], str] = input,
) -> tuple[list[dict], list[dict]]:
    """List the matching events, then ask about each in turn. Nothing is
    changed here: returns (events to copy, events to delete), confirmed
    by typing 'delete' when there are any to delete (except in a dry
    run). Raises QuitRequested on 'q'."""
    while True:
        print(f"  Events on other calendars matching '{group_name}':")
        for i, c in enumerate(matches, start=1):
            note = "" if can_delete(c) else " — can't be deleted (view-only calendar)"
            print(f"    {i}. {describe_candidate(c)}{note}")
        print()
        copy, delete = [], []
        for i, c in enumerate(matches, start=1):
            action = _ask(f"{i}. {c['event'].get('summary') or '(no title)'}", can_delete(c),
                          dry_run, input_fn)
            if action in ("copy", "both"):
                copy.append(c)
            if action in ("delete", "both"):
                delete.append(c)
        verb = "Would" if dry_run else "Will"
        if not copy and not delete:
            print("  Leaving them all as they are.")
            return copy, delete
        if copy:
            print(f"  {verb} copy to the '{group_name}' calendar:")
            for c in copy:
                note = " (its Zoom/Meet link left out; the group's room will be added)" \
                    if had_meeting_link(c) else ""
                print(f"    {describe_candidate(c)}{note}")
        if delete:
            print(f"  {verb} delete:")
            for c in delete:
                effect = ("cancelled for everyone invited, with Google's cancellation emails"
                          if c["organized_here"] else
                          f"removed from {c['calendar']['name']} only; the organizer's event "
                          f"stays")
                print(f"    {describe_candidate(c)} — {effect}")
        if not delete or dry_run:
            return copy, delete
        confirm = input_fn("  Type 'delete' to confirm, or anything else to go back and "
                           "choose again: ").strip().lower()
        if confirm == "delete":
            return copy, delete
        print()


# ── Doing it ──────────────────────────────────────────────────────────────────

class EventMover:
    """Offers to move events onto group calendars, reading the other
    calendars' events once per run. group_calendar_ids is called when
    first needed, and should include calendars made earlier in the run."""

    def __init__(self, calendar_service, group_calendar_ids: Callable[[], set[str]], *,
                 dry_run: bool, input_fn: Callable[[str], str] | None = None,
                 now: datetime.datetime | None = None,
                 zoom_meetings: Callable[[], list[dict]] | None = None):
        self.service = calendar_service
        self._group_calendar_ids = group_calendar_ids
        self._zoom_meetings = zoom_meetings
        self.dry_run = dry_run
        self.input_fn = input_fn or input
        self.now = now or datetime.datetime.now(datetime.timezone.utc)
        self._candidates: list[dict] | None = None
        self._done: set[str] = set()
        self._deleted: set[str] = set()

    def _load(self) -> list[dict]:
        if self._candidates is None:
            group_ids = self._group_calendar_ids()
            calendars = other_calendars(self.service, group_ids)
            events = {c["id"]: upcoming_events(self.service, c["id"], self.now)
                      for c in calendars}
            self._done = copied_event_uids(self.service, group_ids)
            self._candidates = gather_candidates(events, calendars, group_ids)
        return self._candidates

    def offer(self, group_name: str, group_calendar_id: str | None) -> None:
        """Offer to copy events matching group_name to group_calendar_id
        (None only in a dry run that would create it) and delete them from
        their calendars. Raises QuitRequested if the user quits."""
        candidates = [c for c in self._load() if c["uid"] not in self._done]
        matches = matching_candidates(group_name, candidates)
        zoom = self._matching_zoom_topics(group_name)
        for c in matches:
            c["matching_zoom"] = sorted((i, zoom[i]) for i in c["zoom_ids"] if i in zoom)
        self._plan(matches)
        matches = [c for c in matches if not _has_ended(c)]
        if not matches:
            print(f"  No events on other calendars match '{group_name}'.")
            return
        copy, delete = decide_on_events(group_name, matches, self.dry_run, self.input_fn)
        for c in copy:
            self._done.add(c["uid"])
            if self.dry_run:
                log("INFO", "would_copy_event", f"{group_name}: {c['uid']}")
                continue
            for body in copy_bodies(c):
                self.service.events().insert(
                    calendarId=group_calendar_id, body=body, sendUpdates="none").execute()
            print(f"  + copied '{c['event'].get('summary')}' to the group's calendar")
            log("INFO", "copy_event", f"{group_name}: {c['uid']} from {c['calendar']['id']}")
        for c in delete:
            self._done.add(c["uid"])
            if self.dry_run:
                log("INFO", "would_delete_event", f"{group_name}: {c['uid']}")
                continue
            self.service.events().delete(
                calendarId=c["calendar"]["id"], eventId=c["event"]["id"],
                sendUpdates="all").execute()
            self._deleted.add(c["uid"])
            print(f"  - deleted '{c['event'].get('summary')}' from {c['calendar']['name']}")
            log("INFO", "delete_event", f"{group_name}: {c['uid']} from {c['calendar']['id']}")

    def _matching_zoom_topics(self, group_name: str) -> dict[str, str]:
        """{meeting ID: topic} of the Zoom meetings whose names match the
        group's, or {} without zoom_meetings."""
        if self._zoom_meetings is None:
            return {}
        items = room_candidates(self._zoom_meetings())
        return {m["id"]: m["topic"] for m in find_matching_names(group_name, items)}

    def events_using_meeting(self, group_name: str, meeting_id: str) -> list[str]:
        """Descriptions of the not-yet-deleted events matching group_name
        whose location or description is the Zoom meeting meeting_id."""
        using = [c for c in matching_candidates(group_name, self._load())
                 if meeting_id in c["zoom_ids"] and c["uid"] not in self._deleted]
        self._plan(using)
        return [f"'{c['event'].get('summary') or '(no title)'}' on {c['calendar']['name']}"
                for c in using if not _has_ended(c)]

    def _plan(self, candidates: list[dict]) -> None:
        for c in candidates:
            if not c.get("planned"):
                plan_series_copy(self.service, c, self.now)
                c["planned"] = True


def _has_ended(c: dict) -> bool:
    """True for a series with no upcoming occurrence (see plan_series_copy).
    Listing a calendar's events by date returns some series that have
    already ended (often the first half of a series someone split with
    "this and following events")."""
    return bool(c["event"].get("recurrence")) and not c.get("copy_start")
