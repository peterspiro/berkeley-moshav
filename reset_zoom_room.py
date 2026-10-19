#!/usr/bin/env python3
"""
Changes a group's Zoom meeting room passcode, for when its link has
leaked.

Gives the group's room (the one linked in its Google Group's footer) a new
random digits-only passcode. The room keeps its meeting ID, but its join
link changes, since the link embeds the passcode; the script writes the
new link and passcode into the footer and prints them. Within the hour
the group_meetings Apps Script puts the new link in the group's calendar
events; other messages that contain the old link need updating by hand.

The rooms' shared host key isn't changed here; see README.md ("Changing
the host key") for doing that in Zoom's web portal.

The group is named by its Google Group's email address, or by a prefix of
its Google Group name or address that matches just one group.

Requires Google API access (see update_groups_in_google_and_hierarchy.py)
and the Zoom app's credentials in zoom_credentials (see README.md).

Usage:
    python reset_zoom_room.py -g tech-circle@berkeleymoshav.org
    python reset_zoom_room.py -g technology -n
"""

import argparse
import os
import sys
from pathlib import Path

from googleapiclient.discovery import build

from util.credentials import load_zoom_credentials
from util.gather_utils import close_log, configure, init_log, log
from util.google_group_footer import parse_footer_zoom_url, replace_footer_zoom
from util.google_group_utils import DEFAULT_CLIENT_SECRETS_PATH, get_credentials, list_all_groups
from util.zoom_utils import ZoomClient, meeting_id_from_url, new_passcode

_LOG_FILE = Path("debug/reset_zoom_room_log.csv")
_SCREENSHOT_DIR = Path("debug/reset_zoom_room_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


def find_group(groups: list[dict], query: str) -> dict:
    """The one group whose address is query (with or without the domain),
    or else whose name or address starts with query (case-insensitive).
    Raises ValueError if none or several match."""
    q = query.casefold()
    exact = [g for g in groups
             if g["email"].casefold() == q or g["email"].casefold().split("@")[0] == q]
    if len(exact) == 1:
        return exact[0]
    matches = [g for g in groups
               if g.get("name", "").casefold().startswith(q) or g["email"].casefold().startswith(q)]
    if not matches:
        raise ValueError(f"no Google Group's name or address starts with {query!r}")
    if len(matches) > 1:
        names = ", ".join(f"{g.get('name')} <{g['email']}>" for g in matches)
        raise ValueError(f"{query!r} matches several Google Groups: {names}")
    return matches[0]


def reset_room_passcode(settings_service, zoom: ZoomClient, group: dict, dry_run: bool) -> None:
    email = group["email"]
    footer = settings_service.groups().get(groupUniqueId=email).execute().get(
        "customFooterText") or ""
    meeting_id = meeting_id_from_url(parse_footer_zoom_url(footer))
    if not meeting_id:
        sys.exit(f"Error: {email}'s footer has no Zoom meeting link. Run "
                 "update_groups_in_google_and_hierarchy.py to give the group a room.")
    if zoom.get_meeting(meeting_id) is None:
        sys.exit(f"Error: {email}'s Zoom room ({meeting_id}) no longer exists. Run "
                 "update_groups_in_google_and_hierarchy.py -g to give the group a new room.")

    if dry_run:
        print(f"[dry-run] Would give Zoom room {meeting_id} a new passcode and write its "
              f"new link into {email}'s footer.")
        log("INFO", "would_reset_passcode", f"{email}: {meeting_id}")
        return

    passcode = new_passcode()
    zoom.set_passcode(meeting_id, passcode)
    join_url = zoom.get_meeting(meeting_id)["join_url"]
    log("INFO", "reset_passcode", f"{email}: {meeting_id}")
    settings_service.groups().update(
        groupUniqueId=email,
        body={"customFooterText": replace_footer_zoom(footer, join_url, passcode)},
    ).execute()
    log("INFO", "update_footer", f"{email}: new Zoom link")
    print(f"New passcode for {group.get('name')}'s Zoom room {meeting_id}: {passcode}")
    print(f"New link (it and the passcode are now in {email}'s footer): {join_url}")
    print("The group's calendar events get the new link within the hour; update any "
          "other messages that still contain the old one.")


def main(group_query: str, dry_run: bool, credentials_path: str) -> None:
    init_log()
    log("INFO", "start", f"group={group_query!r} dry_run={dry_run}")
    zoom = ZoomClient(**load_zoom_credentials())
    try:
        creds = get_credentials(credentials_path)
        dir_service = build("admin", "directory_v1", credentials=creds)
        settings_service = build("groupssettings", "v1", credentials=creds)
        try:
            group = find_group(list_all_groups(dir_service), group_query)
        except ValueError as e:
            sys.exit(f"Error: {e}")
        reset_room_passcode(settings_service, zoom, group, dry_run)
    finally:
        close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Give a group's Zoom room a new passcode (and so a new link).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-g", "--group", metavar="GROUP", required=True,
        help="The group's Google Group address, or a prefix of its name or address "
             "matching just one group",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Report what would change without changing anything",
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
    main(args.group, args.dry_run, args.credentials)


if __name__ == "__main__":
    cli()
