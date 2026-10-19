"""
Each group's Google calendar and permanent Zoom meeting room.

A group's footer (util/google_group_footer.py) is the source of truth for
which calendar and room are its own. For each group,
ensure_group_calendar() and ensure_zoom_room():

  1. use the calendar/room the footer links, if it still exists;
  2. otherwise list the calendars/rooms whose names match the group's and
     ask which one is the group's (even if only one matches), with "none
     of these" creating a new one — so a calendar or room made by an
     earlier, interrupted run is picked up rather than duplicated;
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
invited to (ALL_EVENTS_CALENDAR_NAME). It starts out shared with no one.
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from util.folder_matching import find_matching_folders
from util.gather_utils import log
from util.google_group_footer import parse_footer_calendar_id, parse_footer_zoom_url
from util.google_group_utils import TOKEN_PATH, list_all_groups
from util.zoom_utils import (
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
    input_fn: Callable[[str], str] = input,
) -> str | None:
    """Return the all-events calendar's ID, finding, choosing, or creating
    it while ALL_EVENTS_CALENDAR_ID isn't set yet. It isn't shared with
    anyone here. Returns None in a dry run that would create it, or if the
    configured calendar is missing."""
    if ALL_EVENTS_CALENDAR_ID:
        if get_calendar(calendar_service, ALL_EVENTS_CALENDAR_ID) is None:
            log("WARN", "all_events_calendar",
                f"ALL_EVENTS_CALENDAR_ID {ALL_EVENTS_CALENDAR_ID} not found")
            return None
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


def _room_candidates(rooms: list[dict]) -> list[dict]:
    """Rooms as {"id", "name"}, named by topic without " Meeting Room" so
    they match against group names."""
    out = []
    for r in rooms:
        topic = r.get("topic", "")
        name = topic[:-len(ROOM_TOPIC_SUFFIX)] if topic.endswith(ROOM_TOPIC_SUFFIX) else topic
        out.append({"id": str(r["id"]), "name": name, "topic": topic})
    return out


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
    rooms: Callable[[], list[dict]], linked: Callable[[], LinkedResources], dry_run: bool,
    input_fn: Callable[[str], str] = input, now: datetime.datetime | None = None,
) -> RoomLink | None:
    """Return the group's room's join link and passcode, finding, choosing,
    or creating the room (see the module docstring), and make sure its
    topic and settings are the standard ones and its passcode is digits
    only (replacing one that isn't changes the link). rooms and linked are
    called only when a choice has to be made. Returns None only in a dry
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
            group_name, _room_candidates(rooms()), linked().rooms, group_email)
        chosen = _choose("Zoom room", f"the Zoom room for '{group_name}'", available, taken,
                         dry_run, input_fn)
        if chosen:
            meeting = zoom.get_meeting(chosen["id"])
            linked().rooms[chosen["id"]] = group_email
        elif dry_run:
            print(f"  [dry-run] would create Zoom room '{topic}'"
                  f"{_UNLESS_CHOSEN if available else ''}")
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
