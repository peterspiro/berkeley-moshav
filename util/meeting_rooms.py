"""
Each group's Google calendar and permanent Zoom meeting room.

A group's footer (util/google_group_footer.py) is the source of truth for
which calendar and room are its own. For each group,
ensure_group_calendar() and ensure_zoom_room():

  1. use the calendar/room the footer links, if it still exists;
  2. otherwise list the calendars whose names match the group's and ask
     which one is the group's (even if only one matches), with "none of
     these" creating a new one — so a calendar made by an earlier,
     interrupted run is picked up rather than duplicated. For a room, list
     every meeting in the Zoom account whose name matches, and ask of each
     whether to use it (only a no-fixed-time meeting of ROOMS_USER can be
     a room), ignore it, or delete it, then create a room unless one is
     used (decide_on_matching_meetings());
  3. then bring it into line: a calendar named after the group, and shared
     with the group's Google Group as editors ("Make changes to events") if
     it isn't shared with the group at all (any other access is left alone,
     so a circle can be restricted by hand); a room titled
     "<Group> Meeting Room" with zoom_utils.ROOM_SETTINGS.

Calendars belong to whoever creates them, and other admins can't see them
through the API, so all of them are owned by CALENDAR_OWNER, and the
scripts that call this refuse to run signed in as anyone else
(check_calendar_owner()). The Apps Scripts run as that account too.

ensure_all_events_calendar() makes the one calendar every meeting is also
invited to (ALL_EVENTS_CALENDAR_NAME), and shares it read-only, without the
usual email, with the everybody groups, so everyone can look at it (the
group_meetings clash alerts link to it).
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple
from zoneinfo import ZoneInfo

from util.folder_matching import find_matching_folders
from util.gather_utils import log
from util.google_group_footer import (
    format_meeting_id,
    parse_footer_calendar_id,
    parse_footer_zoom_url,
)
from util.google_group_utils import TOKEN_PATH, list_all_groups
from util.zoom_utils import (
    MEETING_TYPE_NO_FIXED_TIME,
    ROOM_TOPIC_SUFFIX,
    ROOMS_USER,
    ZoomClient,
    is_numeric_passcode,
    meeting_id_from_url,
    new_passcode,
    room_settings_differences,
    room_topic,
)

# The Google account that owns every group calendar and the all-events
# calendar, and runs the Apps Scripts.
CALENDAR_OWNER = "admin@berkeleymoshav.org"
CALENDAR_TIME_ZONE = "America/Los_Angeles"

ALL_EVENTS_CALENDAR_NAME = "All Berkeley Moshav Events"
# Set once the calendar existed (ensure_all_events_calendar() prints the ID
# to put here); while it's empty, every run offers to find or create it.
ALL_EVENTS_CALENDAR_ID = (
    "c_301c808f640b3a04f5344b1bbf266955d51fecaabd7835936b7591f9cff09222"
    "@group.calendar.google.com"
)

# Calendar ACL role for "Make changes to events".
GROUP_CALENDAR_ROLE = "writer"

# Zoom deletes a no-fixed-time meeting after 365 days unused; warn well
# before that so someone can use the room.
ROOM_IDLE_WARNING_DAYS = 300


class QuitRequested(Exception):
    """The user typed 'q' at a prompt."""


# ── Account check ─────────────────────────────────────────────────────────────

def signed_in_calendar_account(calendar_service) -> str:
    return calendar_service.calendars().get(calendarId="primary").execute()["id"]


def check_calendar_owner(calendar_service) -> None:
    """Exit unless the Google sign-in is CALENDAR_OWNER's."""
    account = signed_in_calendar_account(calendar_service)
    if account.lower() != CALENDAR_OWNER:
        raise SystemExit(
            f"Error: signed in to Google as {account}, but group calendars are owned by "
            f"{CALENDAR_OWNER}.\nDelete {TOKEN_PATH} and run again, signing in as "
            f"{CALENDAR_OWNER}."
        )


# ── Which calendar/room each group's footer already links ─────────────────────

@dataclass
class LinkedResources:
    """Calendars and rooms already linked from some group's footer, keyed
    by calendar ID / Zoom meeting ID, valued by that group's email."""
    calendars: dict[str, str] = field(default_factory=dict)
    rooms: dict[str, str] = field(default_factory=dict)


def find_linked_resources(dir_service, settings_service) -> LinkedResources:
    """Read every Google Group's footer in the domain. Read-only."""
    linked = LinkedResources()
    for group in list_all_groups(dir_service):
        email = group["email"].lower()
        footer = settings_service.groups().get(groupUniqueId=email).execute().get(
            "customFooterText")
        calendar_id = parse_footer_calendar_id(footer)
        if calendar_id:
            linked.calendars[calendar_id] = email
        meeting_id = meeting_id_from_url(parse_footer_zoom_url(footer))
        if meeting_id:
            linked.rooms[meeting_id] = email
    return linked


# ── Choosing among candidates ─────────────────────────────────────────────────

def _matching_candidates(
    group_name: str, items: list[dict], owner_by_id: dict[str, str], group_email: str,
) -> tuple[list[dict], list[dict]]:
    """Split the items ({"id", "name"}) whose names match group_name into
    (available, taken), where taken ones are linked to another group."""
    matches = find_matching_folders(group_name, items)
    available = [m for m in matches if owner_by_id.get(m["id"]) in (None, group_email)]
    taken = [{**m, "owner": owner_by_id[m["id"]]} for m in matches if m not in available]
    return available, taken


def prompt_for_candidate(
    what: str, available: list[dict], taken: list[dict], input_fn: Callable[[str], str] = input,
) -> dict | None:
    """Ask which of the available candidates to use. Returns the chosen one,
    or None to create a new one. Raises QuitRequested on 'q'."""
    if taken:
        print("  Linked to another group (not selectable):")
        for c in taken:
            print(f"    {c['name']}   ({c['id']}, linked to {c['owner']})")
    print(f"  Candidate {what}s:")
    for i, c in enumerate(available, start=1):
        print(f"    {i}. {c['name']}   ({c['id']})")
    prompt = (f"  Select [1-{len(available)}], 0 to create a new {what} instead, "
              f"or 'q' to quit: ")
    while True:
        choice = input_fn(prompt).strip().lower()
        if choice in ("q", "quit"):
            raise QuitRequested
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(available):
            return available[int(choice) - 1]
        print("  Invalid choice.")


_UNLESS_CHOSEN = ", unless one of those is chosen"


def _choose(
    what: str, target: str, available: list[dict], taken: list[dict], dry_run: bool,
    input_fn: Callable[[str], str],
) -> dict | None:
    """Pick an existing candidate, or None to create a new one. With no
    available candidates there's nothing to ask. A dry run only reports,
    naming what it's looking for as target (e.g. "the calendar for
    'Technology Circle'")."""
    if not available:
        for c in taken:
            print(f"  ('{c['name']}' matches but is linked to {c['owner']})")
        return None
    if dry_run:
        names = ", ".join(f"'{c['name']}'" for c in available)
        print(f"  [dry-run] would ask whether one of these is {target}: {names}")
        return None
    return prompt_for_candidate(what, available, taken, input_fn)


# ── Calendars ─────────────────────────────────────────────────────────────────

def _http_status(err: Exception) -> int | None:
    resp = getattr(err, "resp", None)
    return getattr(resp, "status", None)


def get_calendar(calendar_service, calendar_id: str) -> dict | None:
    """The calendar, or None if it doesn't exist or isn't visible to the
    signed-in account."""
    try:
        return calendar_service.calendars().get(calendarId=calendar_id).execute()
    except Exception as err:
        if _http_status(err) in (403, 404):
            return None
        raise


def list_owned_calendars(calendar_service) -> list[dict]:
    """[{"id", "name"}] for every calendar the signed-in account owns,
    except its primary calendar."""
    calendars = []
    page_token = None
    while True:
        resp = calendar_service.calendarList().list(
            minAccessRole="owner", pageToken=page_token,
        ).execute()
        calendars += [
            {"id": c["id"], "name": c.get("summary", "")}
            for c in resp.get("items", []) if not c.get("primary")
        ]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return calendars


def create_calendar(calendar_service, name: str) -> str:
    created = calendar_service.calendars().insert(
        body={"summary": name, "timeZone": CALENDAR_TIME_ZONE}).execute()
    return created["id"]


def group_calendar_role(calendar_service, calendar_id: str, group_email: str) -> str | None:
    """The access role the calendar's sharing gives group_email ("writer",
    "reader", ...), or None if it isn't shared with the group at all."""
    rules = calendar_service.acl().list(calendarId=calendar_id).execute().get("items", [])
    rule = next((r for r in rules
                 if r.get("scope", {}).get("type") == "group"
                 and r["scope"].get("value", "").lower() == group_email.lower()), None)
    return rule.get("role") if rule else None


def group_can_edit(role: str | None) -> bool:
    return role in (GROUP_CALENDAR_ROLE, "owner")


def ensure_calendar_shared_with_group(
    calendar_service, calendar_id: str, group_email: str, dry_run: bool,
) -> str | None:
    """Give group_email "Make changes to events" access if the calendar
    isn't shared with it at all. Returns a description of the change, or
    None.

    Any access the group already has is left as it is, so a circle can be
    restricted by hand to "See all event details", with only some members
    given edit access (see README.md). Google emails the group an
    invitation to add the calendar when the access is granted."""
    if group_calendar_role(calendar_service, calendar_id, group_email) is not None:
        return None
    change = f"share with {group_email} ({GROUP_CALENDAR_ROLE})"
    if not dry_run:
        calendar_service.acl().insert(
            calendarId=calendar_id,
            body={"role": GROUP_CALENDAR_ROLE, "scope": {"type": "group", "value": group_email}},
            sendNotifications=True,
        ).execute()
    return change


def share_calendar_quietly(
    calendar_service, calendar_id: str, calendar_name: str, group_email: str, dry_run: bool,
) -> None:
    """Give group_email "See all event details" access, without Google's
    "shared a calendar with you" email, if the calendar isn't shared with
    it at all. Members can then open the calendar's view-only link, though
    it isn't added to their own calendar lists. Any existing access is left
    alone."""
    if group_calendar_role(calendar_service, calendar_id, group_email) is not None:
        return
    prefix = "[dry-run] would " if dry_run else ""
    if not dry_run:
        calendar_service.acl().insert(
            calendarId=calendar_id,
            body={"role": "reader", "scope": {"type": "group", "value": group_email}},
            sendNotifications=False,
        ).execute()
    print(f"  {prefix}share '{calendar_name}' with {group_email} (view only, no email)")
    log("INFO", f"{'would_' if dry_run else ''}share_calendar",
        f"{calendar_name}: {group_email} (reader, no email)")


def ensure_group_calendar(
    calendar_service, group_name: str, group_email: str, linked_calendar_id: str | None, *,
    owned_calendars: Callable[[], list[dict]], linked: Callable[[], LinkedResources],
    dry_run: bool, input_fn: Callable[[str], str] = input,
) -> str | None:
    """Return the group's calendar ID, finding, choosing, or creating it
    (see the module docstring), and make sure it's named after the group
    and shared with group_email (if it isn't at all). owned_calendars and linked are called
    only when a choice has to be made. Returns None only in a dry run that
    would create a calendar. Raises QuitRequested if the user quits."""
    group_email = group_email.lower()
    calendar = get_calendar(calendar_service, linked_calendar_id) if linked_calendar_id else None
    if calendar is None:
        if linked_calendar_id:
            print(f"'{group_name}': its footer's calendar ({linked_calendar_id}) no longer "
                  f"exists or isn't owned by {CALENDAR_OWNER}.")
        else:
            print(f"'{group_name}' has no calendar linked in its footer.")
        items = [c for c in owned_calendars()
                 if c["id"] != ALL_EVENTS_CALENDAR_ID and c["name"] != ALL_EVENTS_CALENDAR_NAME]
        available, taken = _matching_candidates(
            group_name, items, linked().calendars, group_email)
        chosen = _choose("calendar", f"the calendar for '{group_name}'", available, taken,
                         dry_run, input_fn)
        if chosen:
            calendar = {"id": chosen["id"], "summary": chosen["name"]}
            linked().calendars[chosen["id"]] = group_email
        elif dry_run:
            print(f"  [dry-run] would create calendar '{group_name}' and share it with "
                  f"{group_email}{_UNLESS_CHOSEN if available else ''}")
            log("INFO", "would_create_calendar", f"{group_name} ({group_email})")
            return None
        else:
            calendar_id = create_calendar(calendar_service, group_name)
            calendar = {"id": calendar_id, "summary": group_name}
            linked().calendars[calendar_id] = group_email
            print(f"  + created calendar '{group_name}' ({calendar_id})")
            log("INFO", "create_calendar", f"{group_name}: {calendar_id}")

    calendar_id = calendar["id"]
    prefix = "[dry-run] would " if dry_run else ""
    if calendar.get("summary") != group_name:
        if not dry_run:
            calendar_service.calendars().patch(
                calendarId=calendar_id, body={"summary": group_name}).execute()
        print(f"  {prefix}rename calendar '{calendar.get('summary')}' -> '{group_name}'")
        log("INFO", f"{'would_' if dry_run else ''}rename_calendar",
            f"{calendar.get('summary')!r} -> {group_name!r} ({calendar_id})")

    change = ensure_calendar_shared_with_group(calendar_service, calendar_id, group_email, dry_run)
    if change:
        print(f"  {prefix}{change} (calendar '{group_name}')")
        log("INFO", f"{'would_' if dry_run else ''}share_calendar", f"{group_name}: {change}")
    return calendar_id


def ensure_all_events_calendar(
    calendar_service, *, owned_calendars: Callable[[], list[dict]], dry_run: bool,
    reader_groups: list[str] = (), input_fn: Callable[[str], str] = input,
) -> str | None:
    """Return the all-events calendar's ID, finding, choosing, or creating
    it while ALL_EVENTS_CALENDAR_ID isn't set yet, and share it read-only
    with each of reader_groups that it isn't shared with at all (see
    share_calendar_quietly). Returns None in a dry run that would create
    it, or if the configured calendar is missing."""
    if ALL_EVENTS_CALENDAR_ID:
        if get_calendar(calendar_service, ALL_EVENTS_CALENDAR_ID) is None:
            log("WARN", "all_events_calendar",
                f"ALL_EVENTS_CALENDAR_ID {ALL_EVENTS_CALENDAR_ID} not found")
            return None
        for group_email in reader_groups:
            share_calendar_quietly(
                calendar_service, ALL_EVENTS_CALENDAR_ID, ALL_EVENTS_CALENDAR_NAME,
                group_email, dry_run)
        return ALL_EVENTS_CALENDAR_ID

    print(f"The '{ALL_EVENTS_CALENDAR_NAME}' calendar isn't configured yet.")
    available, taken = _matching_candidates(
        ALL_EVENTS_CALENDAR_NAME, owned_calendars(), {}, "")
    chosen = _choose("calendar", f"the '{ALL_EVENTS_CALENDAR_NAME}' calendar", available, taken,
                     dry_run, input_fn)
    if chosen:
        calendar_id = chosen["id"]
    elif dry_run:
        print(f"  [dry-run] would create calendar '{ALL_EVENTS_CALENDAR_NAME}'"
              f"{_UNLESS_CHOSEN if available else ''}")
        log("INFO", "would_create_calendar", ALL_EVENTS_CALENDAR_NAME)
        return None
    else:
        calendar_id = create_calendar(calendar_service, ALL_EVENTS_CALENDAR_NAME)
        print(f"  + created calendar '{ALL_EVENTS_CALENDAR_NAME}' ({calendar_id})")
        log("INFO", "create_calendar", f"{ALL_EVENTS_CALENDAR_NAME}: {calendar_id}")
    print(f"  Set ALL_EVENTS_CALENDAR_ID = {calendar_id!r} in util/meeting_rooms.py.")
    return calendar_id


# ── Zoom rooms ────────────────────────────────────────────────────────────────

class RoomLink(NamedTuple):
    """What a group's footer shows of its room."""
    join_url: str
    passcode: str


def _room_candidates(meetings: list[dict]) -> list[dict]:
    """Meetings as {"id", "name", "topic", "host_email"}, named by topic
    without " Meeting Room" so they match against group names."""
    out = []
    for m in meetings:
        topic = m.get("topic", "")
        name = topic[:-len(ROOM_TOPIC_SUFFIX)] if topic.endswith(ROOM_TOPIC_SUFFIX) else topic
        out.append({"id": str(m["id"]), "name": name, "topic": topic,
                    "host_email": m.get("host_email") or ""})
    return out


def _can_be_room(meeting: dict) -> bool:
    """Only a no-fixed-time meeting of ROOMS_USER can be a group's room (a
    meeting's type can't be changed, nor its owner)."""
    return (meeting.get("type") == MEETING_TYPE_NO_FIXED_TIME
            and (meeting.get("host_email") or "").lower() == ROOMS_USER)


# ── Describing a meeting's schedule ──

_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]  # Zoom numbers them 1-7
_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", -1: "last"}


def _local_time(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(
        ZoneInfo(CALENDAR_TIME_ZONE))


def _clock(dt: datetime.datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _day(dt: datetime.datetime) -> str:
    return f"{dt:%a %b} {dt.day}"


def _next_start(meeting: dict, now: datetime.datetime) -> datetime.datetime | None:
    starts = [o["start_time"] for o in meeting.get("occurrences") or []
              if o.get("start_time") and o.get("status") != "deleted"]
    if not starts and meeting.get("start_time"):
        starts = [meeting["start_time"]]
    upcoming = sorted(t for t in map(_local_time, starts) if t >= now)
    return upcoming[0] if upcoming else None


def _recurrence_text(recurrence: dict) -> str:
    kind, n = recurrence.get("type"), recurrence.get("repeat_interval") or 1
    if kind == 1:
        return "daily" if n == 1 else f"every {n} days"
    if kind == 2:
        every = "weekly" if n == 1 else f"every {n} weeks"
        days = [_WEEKDAYS[int(d) - 1] for d in str(recurrence.get("weekly_days") or "").split(",")
                if d.strip().isdigit() and 1 <= int(d) <= 7]
        return f"{every} on {', '.join(days)}" if days else every
    if kind == 3:
        every = "monthly" if n == 1 else f"every {n} months"
        if recurrence.get("monthly_day"):
            return f"{every} on day {recurrence['monthly_day']}"
        weekday = recurrence.get("monthly_week_day")
        if weekday in range(1, 8):
            week = _ORDINALS.get(recurrence.get("monthly_week"), "")
            return f"{every} on the {week} {_WEEKDAYS[weekday - 1]}"
        return every
    return "recurring"


def describe_schedule(meeting: dict, now: datetime.datetime) -> str:
    """When the meeting (a GET /meetings result) happens, in the community's
    time zone: "no fixed time", "weekly on Tue, 7:00 PM, next Tue Nov 11",
    "one-off, Thu Oct 30, 5:00 PM", ..."""
    kind = meeting.get("type")
    if kind == MEETING_TYPE_NO_FIXED_TIME:
        return "no fixed time"
    if kind == 8:  # recurring with fixed times
        repeats = _recurrence_text(meeting.get("recurrence") or {})
        upcoming = _next_start(meeting, now)
        if upcoming is None:
            return f"{repeats}, no upcoming occurrences"
        return f"{repeats}, {_clock(upcoming)}, next {_day(upcoming)}"
    if meeting.get("start_time"):
        start = _local_time(meeting["start_time"])
        return f"one-off, {_day(start)}, {_clock(start)}"
    return f"Zoom meeting type {kind}"


# ── Deciding what to do with the meetings that match a group ──

def _ask_action(label: str, can_use: bool, input_fn: Callable[[str], str],
                prefix: str = "") -> str:
    """Ask what to do with one meeting: "use", "ignore", or "delete".
    Raises QuitRequested on 'q'."""
    actions = (["use"] if can_use else []) + ["ignore", "delete"]
    names = [f"[{a[0]}]{a[1:]}" for a in actions]
    if prefix:  # a dry run
        names[-1] += " (DRY-RUN: DELETION WON'T OCCUR)"
    prompt = f"  {prefix}{label} — {', '.join(names)}, or [q]uit: "
    while True:
        answer = input_fn(prompt).strip().lower()
        if answer in ("q", "quit"):
            raise QuitRequested
        for action in actions:
            if answer in (action, action[0]):
                return action
        print("  Invalid choice.")


def _ask_yes_no(prompt: str, input_fn: Callable[[str], str]) -> bool:
    while True:
        answer = input_fn(prompt).strip().lower()
        if answer in ("y", "yes", "n", "no"):
            return answer.startswith("y")
        print("  Please answer y or n.")


def _candidate_line(c: dict) -> str:
    return f"{c['topic']}   ({format_meeting_id(c['id'])}, {c['host_email']}, {c['schedule']})"


def decide_on_matching_meetings(
    group_name: str, available: list[dict], taken: list[dict], dry_run: bool,
    input_fn: Callable[[str], str] = input,
) -> tuple[dict | None, list[dict]]:
    """List the meetings matching the group's name, then ask about each in
    turn whether to use it as the group's room (only a meeting that
    _can_be_room()), ignore it, or delete it. Choosing to use a second
    meeting asks to confirm the switch, and what to do with the first.
    Nothing is changed here: returns (the meeting to use or None, the
    meetings to delete), confirmed by typing 'delete' when there are any
    (except in a dry run). Raises QuitRequested on 'q'.

    available and taken are candidates with "schedule" (describe_schedule)
    and "can_use" set; taken ones are linked to another group and only
    listed."""
    room = room_topic(group_name)
    prefix = "[dry-run] " if dry_run else ""
    while True:
        print("  Matching Zoom meetings:")
        for i, c in enumerate(available, start=1):
            print(f"    {i}. {_candidate_line(c)}")
        for c in taken:
            print(f"       {_candidate_line(c)} — linked to {c['owner']}, not selectable")
        print()

        actions: dict[int, str] = {}
        used: int | None = None
        for i, c in enumerate(available, start=1):
            label = f"{i}. {c['topic']}"
            action = _ask_action(label, c["can_use"], input_fn, prefix)
            if action == "use" and used is not None:
                previous = f"{used}. {available[used - 1]['topic']}"
                if _ask_yes_no(f"  {prefix}You already chose to use {previous}. "
                               f"Use this one instead? [y/n]: ", input_fn):
                    actions[used] = _ask_action(f"{previous} is now unused", False, input_fn,
                                                prefix)
                else:
                    action = _ask_action(label, False, input_fn, prefix)
            actions[i] = action
            if action == "use":
                used = i

        use = available[used - 1] if used else None
        delete = [available[i - 1] for i, a in sorted(actions.items()) if a == "delete"]
        verb = "Would" if dry_run else "Will"
        if use:
            summary = f"  {verb} use {format_meeting_id(use['id'])} as the room"
        else:
            summary = f"  {verb} create a new room, '{room}'"
        if not delete:
            print(summary + ".")
            return use, delete
        print(summary + ", and delete:")
        for c in delete:
            print(f"    {format_meeting_id(c['id'])}  {c['topic']} "
                  f"({c['schedule']}; its link will stop working)")
        if dry_run:
            return use, delete
        confirm = input_fn("  Type 'delete' to confirm, or anything else to go back and "
                           "choose again: ").strip().lower()
        if confirm == "delete":
            return use, delete
        print()


def _warn_if_idle(zoom: ZoomClient, group_name: str, meeting: dict, now: datetime.datetime) -> None:
    """Warn if the room hasn't been used in ROOM_IDLE_WARNING_DAYS, since
    Zoom deletes it after a year unused."""
    last = zoom.last_used(str(meeting["id"])) or meeting.get("created_at")
    if not last:
        return
    when = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
    idle = (now - when).days
    if idle >= ROOM_IDLE_WARNING_DAYS:
        log("WARN", "room_idle",
            f"{group_name}: room {meeting['id']} unused for {idle} days — Zoom deletes it "
            f"after 365; start it once to keep it")


def ensure_zoom_room(
    zoom: ZoomClient, group_name: str, linked_join_url: str | None, group_email: str, *,
    meetings: Callable[[], list[dict]], linked: Callable[[], LinkedResources], dry_run: bool,
    input_fn: Callable[[str], str] = input, now: datetime.datetime | None = None,
) -> RoomLink | None:
    """Return the group's room's join link and passcode, finding, choosing,
    or creating the room (see the module docstring), and make sure its
    topic and settings are the standard ones and its passcode is digits
    only (replacing one that isn't changes the link). meetings (every
    scheduled meeting in the Zoom account) and linked are called only when
    there's no room in the footer. Returns None only in a dry
    run that would create a room. Raises QuitRequested if the user quits."""
    group_email = group_email.lower()
    topic = room_topic(group_name)
    meeting_id = meeting_id_from_url(linked_join_url)
    meeting = zoom.get_meeting(meeting_id) if meeting_id else None
    created = False
    if meeting is None:
        if meeting_id:
            print(f"'{group_name}': its footer's Zoom room ({meeting_id}) no longer exists "
                  f"(deleted, or expired after a year unused).")
        else:
            print(f"'{group_name}' has no Zoom room linked in its footer.")
        available, taken = _matching_candidates(
            group_name, _room_candidates(meetings()), linked().rooms, group_email)
        when = now or datetime.datetime.now(datetime.timezone.utc)
        for c in available + taken:
            details = zoom.get_meeting(c["id"]) or {}
            c["details"] = {**details, "host_email": c["host_email"]}
            c["schedule"] = describe_schedule(details, when)
            c["can_use"] = _can_be_room(c["details"])
        use, delete = (decide_on_matching_meetings(group_name, available, taken, dry_run,
                                                   input_fn)
                       if available else (None, []))
        if not available:
            for c in taken:
                print(f"  ('{c['topic']}' matches but is linked to {c['owner']})")
        for c in delete:
            if dry_run:
                log("INFO", "would_delete_meeting", f"{group_name}: {c['id']} {c['topic']!r}")
            else:
                zoom.delete_meeting(c["id"])
                print(f"  - deleted Zoom meeting {format_meeting_id(c['id'])} '{c['topic']}'")
                log("INFO", "delete_meeting", f"{group_name}: {c['id']} {c['topic']!r}")
        if use:
            meeting = use["details"]
            linked().rooms[use["id"]] = group_email
        elif dry_run:
            if not available:
                print(f"  [dry-run] would create Zoom room '{topic}'")
            log("INFO", "would_create_room", group_name)
            return None
        else:
            meeting = zoom.create_room(topic)
            created = True
            linked().rooms[str(meeting["id"])] = group_email
            print(f"  + created Zoom room '{topic}' ({meeting['id']})")
            log("INFO", "create_room", f"{group_name}: {meeting['id']}")

    if meeting.get("host_email") and meeting["host_email"].lower() != ROOMS_USER:
        log("WARN", "room_host",
            f"{group_name}: room {meeting['id']} belongs to {meeting['host_email']}, "
            f"not {ROOMS_USER}")

    body = room_settings_differences(meeting, topic)
    described = dict(body)
    if not is_numeric_passcode(meeting.get("password")):
        body["password"] = new_passcode()
        described["password"] = "(new digits-only passcode)"
    if body:
        prefix = "[dry-run] would " if dry_run else ""
        print(f"  {prefix}update Zoom room {meeting['id']}: {described}")
        log("INFO", f"{'would_' if dry_run else ''}update_room",
            f"{group_name}: {meeting['id']} {described}")
        if not dry_run:
            zoom.update_meeting(str(meeting["id"]), body)
            if "password" in body:  # the link embeds the passcode
                meeting = zoom.get_meeting(str(meeting["id"]))

    if not created:
        _warn_if_idle(zoom, group_name, meeting, now or datetime.datetime.now(datetime.timezone.utc))
    return RoomLink(meeting["join_url"], meeting.get("password", ""))
