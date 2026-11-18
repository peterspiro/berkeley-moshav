#!/usr/bin/env python3
"""
Moves a group's Zoom meeting room to another Zoom user, to keep its
meetings from clashing with others' (a Zoom user can run only one meeting
at a time).

Lists every Zoom user a group's room may be under (ROOM_USERS in
util/zoom_utils.py, the first being the default for new rooms), the
group's current one included, and for each, the conflicts the room's
meetings would have there in the next 8 weeks: meetings in other rooms of
that user's, or scheduled in Zoom under it, that overlap one of the room's
meetings or are less than 15 minutes from it, as the group_meetings Apps
Script checks (see util/zoom_conflicts.py). Then it asks which user to
move the room to; the current one leaves it where it is.

The room is moved by asking Zoom to schedule it for the new user, which
keeps its meeting ID, passcode, and link. If Zoom refuses, or the room is
still under its old user afterwards, the script prints how to move it in
Zoom's web portal and waits while that's done. If its link has changed
anyway, the new one is written into the group's footer. Within the hour
group_meetings notices the room's new Zoom user, names it in the group's
calendar events, and checks for clashes again.

Facilitators claim host with the host key of the Zoom user the room is
under, so after a move the script reminds you to give them the new user's.

A community-wide calendar's room stays under community-zoom-hosts@, so its
admin group can't be named. The group is named by its Google Group's email
address, or by a prefix of its Google Group name or address that matches
just one group.

Requires Google API access, signed in as admin@ (see update_groups.py),
and the Zoom app's credentials in zoom_credentials (see README.md).

Usage:
    python move_zoom_room.py -g technology
    python move_zoom_room.py -g technology -n
    python move_zoom_room.py -g tech-circle@example.org -u 2
"""

import argparse
import datetime
import os
import sys
from collections.abc import Callable
from pathlib import Path

from googleapiclient.discovery import build

from util.credentials import load_zoom_credentials
from util.gather_utils import close_log, configure, init_log, log
from util.google_group_footer import (
    format_meeting_id,
    parse_footer_audience,
    parse_footer_calendar_id,
    parse_footer_zoom_url,
    replace_footer_zoom,
)
from util.google_group_utils import (
    DEFAULT_CLIENT_SECRETS_PATH,
    find_group,
    get_credentials,
    list_all_groups,
)
from util.meeting_rooms import COMMUNITY_HOST_KEY_DOC_URL, check_calendar_owner
from util.zoom_conflicts import (
    MIN_GAP_MINUTES,
    OVERLAP_WEEKS,
    Meeting,
    conflicts_under,
    describe_conflict,
    gather_meetings,
    own_conflicts,
)
from util.zoom_utils import (
    COMMUNITY_ROOMS_USER,
    MEETING_TYPE_NO_FIXED_TIME,
    ROOM_USERS,
    ROOMS_USER,
    ZoomClient,
    ZoomError,
    meeting_id_from_url,
    zoom_user_label,
)

_LOG_FILE = Path("debug/move_zoom_room_log.csv")
_SCREENSHOT_DIR = Path("debug/move_zoom_room_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


def read_groups(dir_service, settings_service) -> list[dict]:
    """Every Google Group, with what its footer links: [{"email", "name",
    "footer", "calendar_id", "meeting_id", "audience"}]. Read-only."""
    groups = []
    for g in list_all_groups(dir_service):
        footer = settings_service.groups().get(groupUniqueId=g["email"]).execute().get(
            "customFooterText") or ""
        groups.append({
            "email": g["email"], "name": g.get("name") or g["email"], "footer": footer,
            "calendar_id": parse_footer_calendar_id(footer),
            "meeting_id": meeting_id_from_url(parse_footer_zoom_url(footer)),
            "audience": parse_footer_audience(footer),
        })
    return groups


def room_of(zoom, group: dict) -> dict:
    """The group's room, from Zoom; exits if it has none that can move."""
    email = group["email"]
    if group["audience"]:
        sys.exit(f"Error: {email} is a community-wide calendar's admin group; its room "
                 f"stays under {COMMUNITY_ROOMS_USER}.")
    if not group["meeting_id"]:
        sys.exit(f"Error: {email}'s footer has no Zoom meeting link. Run update_groups.py "
                 "to give the group a room.")
    room = zoom.get_meeting(group["meeting_id"])
    if room is None:
        sys.exit(f"Error: {email}'s Zoom room ({group['meeting_id']}) no longer exists. Run "
                 "update_groups.py -g to give the group a new room.")
    if room.get("type") != MEETING_TYPE_NO_FIXED_TIME:
        sys.exit(f"Error: {email}'s Zoom meeting ({group['meeting_id']}) isn't a "
                 "no-fixed-time room.")
    return room


def choices_for(current: str) -> list[str]:
    """The users the room can be under: ROOM_USERS, and its current user
    first if that isn't one of them."""
    return ([] if current in ROOM_USERS else [current]) + list(ROOM_USERS)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _print_conflict(a: Meeting, b: Meeting, indent: str) -> None:
    what, *lines = describe_conflict(a, b)
    print(f"{indent}- {what}")
    for line in lines:
        print(f"{indent}  {line}")


def report(group: dict, room: dict, meetings: list[Meeting], users: list[str]) -> None:
    """Print the room, its meetings, and each user's conflicts for it."""
    room_id = str(room["id"])
    current = room["host_email"].lower()
    own = [m for m in meetings if m.meeting_id == room_id]
    print(f"{group['name']}'s Zoom room ({format_meeting_id(room_id)}) is under "
          f"{zoom_user_label(current)}.")
    print(f"It has {_plural(len(own), 'meeting')} in the next {OVERLAP_WEEKS} weeks.")
    clashing = own_conflicts(meetings, room_id)
    if clashing:
        print(f"\n{_plural(len(clashing), 'conflict')} between the room's own meetings, "
              "which no move fixes:")
        for a, b in clashing:
            _print_conflict(a, b, "  ")
    print(f"\nConflicts (overlaps, or less than {MIN_GAP_MINUTES} minutes apart) under each "
          "Zoom user:")
    for i, user in enumerate(users, 1):
        notes = []
        if user == current:
            notes.append("current: choosing it changes nothing")
        if user == ROOMS_USER:
            notes.append("the default")
        note = f" ({'; '.join(notes)})" if notes else ""
        print(f"\n  {i}. {zoom_user_label(user)}{note}")
        pairs = conflicts_under(meetings, room_id, user)
        if not pairs:
            print("       no conflicts")
            continue
        print(f"       {_plural(len(pairs), 'conflict')}:")
        for a, b in pairs:
            _print_conflict(a, b, "       ")


def choose_user(users: list[str], current: str, choice: int | None,
                input_fn: Callable[[str], str]) -> str:
    """The user picked by number (choice, or asked for; Enter keeps the
    current one)."""
    if choice is not None:
        if not 1 <= choice <= len(users):
            sys.exit(f"Error: -u must be from 1 to {len(users)}.")
        return users[choice - 1]
    keep = users.index(current) + 1
    while True:
        answer = input_fn(f"\nMove the room to which Zoom user? [1-{len(users)}, "
                          f"Enter for {keep}, the current one]: ").strip()
        if not answer:
            return current
        if answer.isdigit() and 1 <= int(answer) <= len(users):
            return users[int(answer) - 1]
        print(f"  Type a number from 1 to {len(users)}.")


def manual_steps(room_id: str, current: str, target: str) -> str:
    return (
        "Move it in Zoom's web portal (zoom.us):\n"
        f"  1. Sign in as {target}. In Settings, Meeting, under Schedule Privilege, "
        f"assign {current}, so it can schedule meetings for {target}.\n"
        f"  2. Sign in as {current}. In Meetings, open room {format_meeting_id(room_id)}, "
        f"click Edit, choose {target} under Schedule For, and Save (for every "
        "occurrence, if asked).\n"
        f"  3. Optionally, as {target}, remove the scheduling privilege again.")


def _host(meeting: dict | None) -> str:
    return ((meeting or {}).get("host_email") or "").lower()


def move_room(zoom, settings_service, group: dict, room: dict, target: str,
              input_fn: Callable[[str], str]) -> None:
    """Move the room to target, check it moved with its link unchanged
    (or write the new link into the footer), and warn about the host key."""
    room_id = str(room["id"])
    current = _host(room)
    try:
        zoom.move_meeting(room_id, target)
        refused = None
    except ZoomError as err:
        refused = err
    moved = zoom.get_meeting(room_id)
    if _host(moved) != target:
        print(f"\nZoom refused to move the room: {refused}" if refused
              else "\nZoom didn't move the room.")
        log("WARN", "move_refused", f"{group['email']}: {room_id} to {target}: {refused}")
        print(manual_steps(room_id, current, target))
        while _host(moved) != target:
            answer = input_fn("\nPress Enter once it's moved (or type q to stop): ").strip()
            if answer.lower() == "q":
                sys.exit(f"Stopped: the room is still under {_host(moved) or current}.")
            moved = zoom.get_meeting(room_id)
            if _host(moved) != target:
                print(f"  It's still under {_host(moved)}.")
    log("INFO", "move_room", f"{group['email']}: {room_id} from {current} to {target}")
    print(f"\nMoved {group['name']}'s Zoom room to {zoom_user_label(target)}.")

    if moved.get("join_url") != room.get("join_url"):
        footer = replace_footer_zoom(group["footer"], moved["join_url"],
                                     moved.get("password", ""))
        settings_service.groups().update(
            groupUniqueId=group["email"], body={"customFooterText": footer}).execute()
        log("INFO", "update_footer", f"{group['email']}: new Zoom link")
        print(f"Its link changed, so the new one is now in {group['email']}'s footer: "
              f"{moved['join_url']}\nThe group's calendar events get it within the hour; "
              "update any other messages that still contain the old one.")
    else:
        print("Its link and passcode are unchanged.")

    # The link goes alone on its own line, so terminals that open it don't
    # take in any punctuation around it.
    where = (f", which is in the Community Events Zoom Host Key doc:\n{COMMUNITY_HOST_KEY_DOC_URL}"
             if target == COMMUNITY_ROOMS_USER else ".")
    print(f"\nWARNING: facilitators claim host with the host key of the Zoom user the room "
          f"is under, now {target}'s, not {current}'s. Give {group['name']}'s facilitators "
          f"{target}'s host key{where}")
    print("\nWithin the hour, group_meetings names the room's new Zoom user in the group's "
          "calendar events, and checks for clashes again.")


def main(group_query: str, user_choice: int | None, dry_run: bool, credentials_path: str,
         input_fn: Callable[[str], str] = input) -> None:
    init_log()
    log("INFO", "start", f"group={group_query!r} user={user_choice} dry_run={dry_run}")
    zoom = ZoomClient(**load_zoom_credentials())
    try:
        creds = get_credentials(credentials_path)
        calendar_service = build("calendar", "v3", credentials=creds)
        check_calendar_owner(calendar_service)
        dir_service = build("admin", "directory_v1", credentials=creds)
        settings_service = build("groupssettings", "v1", credentials=creds)
        print("Reading the groups' footers...")
        groups = read_groups(dir_service, settings_service)
        try:
            group = find_group(groups, group_query)
        except ValueError as e:
            sys.exit(f"Error: {e}")
        room = room_of(zoom, group)
        print("Reading the groups' calendars and the Zoom account's meetings...")
        meetings = gather_meetings(calendar_service, zoom, groups,
                                   datetime.datetime.now(datetime.timezone.utc))
        print()
        run(zoom, settings_service, group, room, meetings, user_choice, dry_run, input_fn)
    finally:
        close_log()


def run(zoom, settings_service, group: dict, room: dict, meetings: list[Meeting],
        user_choice: int | None, dry_run: bool, input_fn: Callable[[str], str]) -> None:
    """Report each user's conflicts, ask which to move the room to, and
    move it (unless dry_run, or it's the current one)."""
    current = _host(room)
    users = choices_for(current)
    report(group, room, meetings, users)
    if dry_run and user_choice is None:
        print("\n[dry-run] Nothing changed.")
        return
    target = choose_user(users, current, user_choice, input_fn)
    if target == current:
        print(f"No change: the room stays under {zoom_user_label(current)}.")
        log("INFO", "no_change", group["email"])
        return
    if dry_run:
        print(f"[dry-run] Would move {group['name']}'s Zoom room to {zoom_user_label(target)}.")
        log("INFO", "would_move_room", f"{group['email']}: {room['id']} to {target}")
        return
    move_room(zoom, settings_service, group, room, target, input_fn)


def cli():
    parser = argparse.ArgumentParser(
        description="Move a group's Zoom room to another Zoom user, showing the conflicts "
                    "its meetings would have under each.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-g", "--group", metavar="GROUP", required=True,
        help="The group's Google Group address, or a prefix of its name or address "
             "matching just one group",
    )
    parser.add_argument(
        "-u", "--user", metavar="N", type=int,
        help="Move the room to the Nth Zoom user listed, without asking",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="List the Zoom users and their conflicts without moving anything",
    )
    parser.add_argument(
        "-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
        help="Path to OAuth client secrets JSON",
    )
    args = parser.parse_args()
    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )
    main(args.group, args.user, args.dry_run, args.credentials)


if __name__ == "__main__":
    cli()
