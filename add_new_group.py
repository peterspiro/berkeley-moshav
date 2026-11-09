#!/usr/bin/env python3
"""
Creates a new Drive folder, Google Group, and Gather group, all linked
together, and (unless --type=club or admin) has the new group put under its
parent circle in the Circle Structure.

With --type=admin, the new group is a community-wide calendar's admin
group (Gather type "Squad"): its Gather group, folder (under the top-level
"Meeting Admin" folder), and Google Group are named with "Admins" as the
type suffix ("Community Meetings Admins", community-meetings-admins@); its
calendar is named without it ("Community Meetings"), and its Zoom room
"Community Meetings Zoom Room", under the community-wide calendars' Zoom
user (COMMUNITY_ROOMS_USER); its footer also links the rooms' host key doc
(COMMUNITY_HOST_KEY_DOC_URL) and names the calendar's audience
(COMMUNITY_CALENDAR_AUDIENCE), the everybody group its events invite and
its reminders go to; and its welcome message tells the admins how to
schedule community-wide events.

Steps:
  1. Validate: the folder, Google Group, and Gather group don't already
     exist; (unless --type=club or admin) --parent-circle uniquely identifies a
     Gather group that can be the new group's parent (a circle, or, for a
     working group, a circle or working group); and --manager uniquely
     identifies a community member.
  2. Create the Drive folder, Google Group (with the usual settings, plus
     "Conversation history" on and content moderation open to all members),
     and Gather group (with the usual settings, except clubs get
     availability=open instead of closed) — the --manager member is added
     to the Gather group as a manager at creation, before the folder is
     linked, so Gather auto-syncs their Content-manager permission.
  3. Link the folder on /gdrive/config and grant the new Gather group
     Content manager access.
  4. Wire the Gather group's mailing list to the new Google Group (to the
     --alias address if given, else the group's primary address).
  5. Unless --type=club or admin, write the new group's parent to "Circle structure
     placements.json" in admin@'s My Drive (see
     util/circle_structure_placements.py). The circle_agendas Apps Script
     adds the group to the Circle Structure sheet within the hour (from its
     footer, step 7), under that parent, and updates the Circle Structure
     doc and the parent's agenda doc.
  6. Give the group a Google calendar, shared with the new Google Group so
     its members can add and edit events, and a permanent Zoom meeting room
     (see util/meeting_rooms.py), or on a re-run (-i), the ones its footer
     already links. Unlike update_groups.py, it doesn't look for existing
     calendars, Zoom meetings, or events with matching names: a new group
     has none.
  7. Write the new Google Group's custom footer with its "Gather group",
     "Google Docs folder", "Google calendar", and "Zoom meeting" links (see
     util/google_group_footer.py) — this is what groups_drive_sync.gs reads
     to sync membership, so no Apps Script redeploy is needed for a new
     group to start syncing.
  7b. With --agenda-doc, for a circle or working group, create its agenda
     doc, empty and labelled for the circle_agendas Apps Script to build
     (see util/circle_agenda_docs.py).
  8. Add the manager to the Google Group directly, so the list reaches
     them right away rather than after groups_drive_sync.gs's next run.
  9. Unless --no-email, open a welcome message to the new list as a draft
     in the default mail program (via a mailto: link), for the person
     running the script to review, edit if they like, and send from their
     own account. It tells the manager what was created and what they can
     do, and that they can ask the sender for a different list address or
     group name. A dry run prints the draft instead.

Requires Google API access (Drive, Admin Directory, Groups Settings,
Calendar), signed in as the calendars' owner, and the Zoom app's
credentials in zoom_credentials — see update_groups.py's
docstring and README.md for setup.

The group type (circle / working group / club / admin group) is inferred
from a name ending in the matching suffix ("Circle" / "Working Group" /
"Club" / "Admins"); pass -t/--type explicitly only when the name has no
such suffix.

The name (-N/--name, required) may be given as multiple unquoted words.

For an admin group, -A/--audience names another Google Group as the
calendar's audience (the one its events invite and its reminders go to),
for trying a community calendar out on a few people before it reaches
everyone; afterwards, change the footer's "Calendar audience:" line by
hand, or delete the test group and its calendar and room.

For an admin group, -I/--also-invite names more addresses (of any domain:
say, a mailing list outside the Workspace) that every event on its
calendar invites along with the audience, but doesn't remind; they go on
its footer's "Also invite:" line. update_groups.py -g ... -I changes them.

For an admin group, -z/--no-zoom gives its calendar no Zoom room (for
in-person events): its footer says "Zoom meeting: none", so neither this
script nor update_groups.py makes one, its events get no Zoom details, and
its welcome message says nothing of a room. To give it one later, delete
that line from its footer and run update_groups.py -g for it.

Pass --alias to also give the new Google Group a shorter second address
and point the Gather group's mailing list at it — the same effect as
running change_group_email_alias.py afterward.

Pass --short-address to make the Google Group's primary address the name
without its "Circle"/"Working Group" suffix (e.g. landscape@ rather than
landscape-circle@), with the full-name address added as an alias. The
Gather group's mailing list then uses the short address. Not available
for clubs, and can't be combined with --alias.

--manager (required) is a prefix of a member's first name, last name, or
email uniquely identifying them; they're made a manager of the new group.

Pass --idempotent to reuse any artifacts that already exist (folder,
Google Group, Gather group, alias, folder link, and the
calendar and Zoom room linked in the group's footer) rather than aborting
— useful for re-running after a partial failure. Combine it
with --no-email if the first run already opened (and you sent) the welcome
message.

Usage:
    python add_new_group.py -N Landscape Circle -p Property -m jsmith
    python add_new_group.py -N Tech Support -t w -p Operations -m Dana
    python add_new_group.py -N Book Club -m alice@example.org
    python add_new_group.py -N Landscape Circle -p Property -m jsmith --alias landscape
    python add_new_group.py -N Community Meetings -t a -m Dana
"""

import argparse
import os
import re
import sys
from enum import Enum
from pathlib import Path

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from util.calendar_announcement import (
    GROUP_CALENDAR_HOWTO_URL,
    admin_calendar_paragraphs,
    who_sees_events,
)
from util.circle_agenda_docs import (
    AGENDA_KINDS,
    agenda_doc_name,
    ensure_agenda_doc,
)
from util.circle_structure_placements import add_placements, check_parent_kind
from util.credentials import load_credentials, load_zoom_credentials
from util.gather_utils import (
    _fetch_group_detail,
    close_log,
    configure,
    create_gather_group,
    ensure_gather_group_manager,
    fetch_all_gather_groups,
    fetch_all_gather_users,
    find_gather_group_id_by_name,
    find_users_by_prefix,
    init_log,
    launch_browser,
    log,
    login,
    resolve_group_by_prefix,
    set_gather_group_email_list,
)
from util.gdrive_config import (
    ADMIN_GROUP_KIND,
    DEFAULT_DRIVE_ID,
    FOLDER_TYPE_SUFFIXES,
    PARENT_FOLDER_NAMES,
    add_group_access_to_gdrive_item,
    create_drive_folder,
    create_gdrive_item,
    ensure_folder_name_available,
    find_folder_id_under_parent,
    folder_name_for_group_type,
    calendar_name_for_group,
    parent_folder_id_for_group_type,
    scrape_gdrive_config,
)
from util.google_group_footer import (
    ensure_group_footer,
    fetch_footer_calendar_id,
    fetch_footer_zoom_url,
    parse_footer_audience,
    parse_footer_no_zoom,
)
from util.google_group_utils import (
    CONVERSATION_HISTORY_SETTINGS,
    DEFAULT_CLIENT_SECRETS_PATH,
    DOMAIN,
    MODERATION_SETTINGS,
    REQUIRED_GROUP_SETTINGS,
    add_group_alias,
    ensure_group_member,
    ensure_group_settings,
    get_credentials,
    get_group_by_email,
    group_display_name,
    group_email,
    group_exists,
    also_invite_addresses,
    is_in_domain,
    truncate_local_part,
)
from util.mail_draft import open_draft
from util.meeting_rooms import (
    COMMUNITY_CALENDAR_AUDIENCE,
    COMMUNITY_HOST_KEY_DOC_URL,
    LinkedResources,
    QuitRequested,
    check_calendar_owner,
    ensure_group_calendar,
    ensure_zoom_room,
)
from util.zoom_utils import (
    COMMUNITY_ROOM_TOPIC_SUFFIX,
    COMMUNITY_ROOM_USERS,
    ROOM_TOPIC_SUFFIX,
    ROOM_USERS,
    ZoomClient,
)
_LOG_FILE = Path("debug/add_new_group_log.csv")

# Added to the errors about something that already exists, which is
# usually left from an earlier, interrupted run.
REUSE_HINT = (" If an earlier run of this script made it, run again with -i/--idempotent "
              "to reuse it.")

# Who a Google Group's whoCanPostMessage setting lets post, for the warning
# about an admin group's audience.
AUDIENCE_POSTERS = {
    "ALL_MEMBERS_CAN_POST": "its members",
    "ALL_MANAGERS_CAN_POST": "its managers",
    "ALL_OWNERS_CAN_POST": "its owners",
    "NONE_CAN_POST": "no one",
}
_SCREENSHOT_DIR = Path("debug/add_new_group_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


class GroupType(Enum):
    CIRCLE = ("circle", "circle", "c")
    WORKING_GROUP = ("working group", "committee", "w")
    CLUB = ("club", "club", "b")
    ADMIN = ("admin", ADMIN_GROUP_KIND, "a")  # a community-wide calendar's admins

    def __init__(self, label: str, kind: str, letter: str):
        self.label = label
        self.kind = kind
        self.letter = letter

    @classmethod
    def parse(cls, raw: str) -> "GroupType":
        raw = raw.strip().lower()
        for member in cls:
            if raw == member.letter or raw == member.label or raw == member.kind:
                return member
        choices = ", ".join(f"{m.letter} ({m.label})" for m in cls)
        raise argparse.ArgumentTypeError(f"invalid type {raw!r} — choose one of: {choices}")

    @classmethod
    def from_name_suffix(cls, name: str) -> "GroupType | None":
        """Infer the type from a name ending in the type's folder suffix
        ("Circle"/"Working Group"/"Club"/"Admins"), or None if it ends with
        none."""
        for member in cls:
            suffix = FOLDER_TYPE_SUFFIXES[member.kind]
            if re.search(r"\b" + re.escape(suffix) + r"\s*$", name, re.IGNORECASE):
                return member
        return None


def build_welcome_message(
    group_name: str, list_address: str, manager_first_name: str, sender_first_name: str,
) -> tuple[str, str]:
    """Return (subject, body) of the welcome message to a new group's list.
    The links it refers to are in the Google Group's custom footer, which
    Google Groups appends when it delivers the message."""
    subject = f"Your new group: {group_name}"
    greeting = f"Hi {manager_first_name}," if manager_first_name else "Hi,"
    body = (
        f"{greeting}\n"
        "\n"
        f"I've created the {group_name} group in Gather, along with a Google "
        "Drive folder for its documents, a Google calendar for its meetings, and "
        "a Zoom meeting room. Links to all of them are in the footer at the "
        f"bottom of this message. I've also created this email list, {list_address}.\n"
        "\n"
        "Everyone in the Gather group receives messages sent to the list, can add "
        "and edit files in the folder, and can add and change events on the "
        "calendar. Anyone can post to the list.\n"
        "\n"
        "To see the calendar in your own Google Calendar, click its link in the "
        "footer.\n"
        "\n"
        "Please put the group's meetings on its calendar rather than on your own. "
        f"Here's how:\n{GROUP_CALENDAR_HOWTO_URL}\n"
        "\n"
        "The Zoom room is the group's permanent meeting place: its link never "
        "changes, and anyone with the link can join, with or without a host.\n"
        "\n"
        "I've made you a manager of the Gather group, which allows you to add "
        "members to it, and to make other members managers.\n"
        "\n"
        "If you'd like a different address for the list, such as a shorter one, "
        "or a different name for the group, just let me know.\n"
        "\n"
        f"{sender_first_name}\n"
    )
    return subject, body


def _a(word: str) -> str:
    """The indefinite article for word: "an" before a vowel ("an Open Mic
    calendar"), else "a"."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def build_admin_welcome_message(
    calendar_name: str, list_address: str, manager_first_name: str, sender_first_name: str,
    audience: str, zoom: bool = True,
) -> tuple[str, str]:
    """Return (subject, body) of the welcome message to a new community-wide
    calendar's admin group's list: what was created, and how community-wide
    events are scheduled (admin_calendar_paragraphs()). Without zoom, for a
    calendar with no Zoom room, nothing is said of one."""
    subject = f"Your new community calendar: {calendar_name}"
    greeting = f"Hi {manager_first_name}," if manager_first_name else "Hi,"
    body = "\n\n".join([
        greeting,
        f"I've created {_a(calendar_name)} {calendar_name} calendar for community-wide "
        "events, and this "
        f"email list, {list_address}, for the people who schedule them. Links to the "
        f"calendar, {'its Zoom room, ' if zoom else ''}its Drive folder, and its admin "
        "group in Gather are in the footer at the bottom of this message.",
        "I've made you a manager of the Gather group, which allows you to add members to "
        "it, and to make other members managers. Everyone in it receives messages sent to "
        f"this list, and {who_sees_events(calendar_name)}",
        *admin_calendar_paragraphs(calendar_name, audience, zoom),
        "If you'd like a different address for the list, or a different name for the "
        "calendar, just let me know.",
        sender_first_name,
    ]) + "\n"
    return subject, body


def print_welcome_message(to: str, subject: str, body: str) -> None:
    print(f"To: {to}")
    print(f"Subject: {subject}")
    print()
    print(body.rstrip("\n"))
    print()
    print("(Google Groups appends the footer, with the Gather group, Drive folder, "
          "calendar, and Zoom room links, when it delivers the message.)")


def calendar_and_room(
    calendar_service, zoom: ZoomClient, settings_service, cal_name: str, gemail: str,
    google_group_exists: bool, no_zoom: bool, room_users: tuple[str, ...], room_suffix: str,
    gdisplay: str, dry_run: bool,
) -> dict:
    """Give the new group its calendar and (unless no_zoom) its Zoom room,
    named cal_name: the ones its footer already links (on a re-run, -i), or
    new ones (see util/meeting_rooms.py). Unlike update_groups.py, it
    doesn't look for existing calendars, Zoom meetings, or events with
    matching names, since a new group has none. A dry run only says what
    it would create. Returns {"calendar_id", "room"}, either None if not
    made (in a dry run, or if the user quit, which is reported)."""
    found = {"calendar_id": None, "room": None}
    linked_calendar_id = linked_zoom_url = None
    if google_group_exists:
        linked_calendar_id = fetch_footer_calendar_id(settings_service, gemail)
        linked_zoom_url = fetch_footer_zoom_url(settings_service, gemail)
    # No candidates to match against, so anything the footer doesn't link
    # is created.
    linked = LinkedResources()
    try:
        found["calendar_id"] = ensure_group_calendar(
            calendar_service, cal_name, gemail, linked_calendar_id,
            owned_calendars=lambda: [], linked=lambda: linked, dry_run=dry_run,
        )
        if not no_zoom:
            found["room"] = ensure_zoom_room(
                zoom, cal_name, linked_zoom_url, gemail,
                meetings=lambda: [], linked=lambda: linked, dry_run=dry_run,
                room_users=room_users, topic_suffix=room_suffix,
            )
    except QuitRequested:
        log("INFO", "quit", "User quit during the calendar/Zoom room step")
        print(f"Skipped the rest of the calendar/Zoom room setup. Run "
              f"'python update_groups.py -g \"{gdisplay}\"' "
              "later to finish it.")
    return found


def main(
    group_type: GroupType,
    raw_name: str,
    parent_circle_prefix: str | None,
    manager_prefix: str,
    base_url: str,
    email: str,
    password: str,
    dry_run: bool,
    credentials_path: str,
    drive_id: str,
    alias: str | None = None,
    short_address: bool = False,
    idempotent: bool = False,
    send_email: bool = True,
    agenda_doc: bool = False,
    audience_override: str | None = None,
    no_zoom: bool = False,
    also_invite: list[str] | None = None,
) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start",
        f"type={group_type.kind} name={raw_name!r} manager={manager_prefix!r} "
        f"alias={alias!r} short_address={short_address} dry_run={dry_run} idempotent={idempotent} "
        f"send_email={send_email}")

    kind = group_type.kind
    is_admin_group = group_type == GroupType.ADMIN
    folder_name = folder_name_for_group_type(raw_name, kind)
    gemail = group_email(folder_name)
    gdisplay = group_display_name(folder_name)
    # The calendar and room are named cal_name: gdisplay, or for an admin
    # group, the calendar's name, without "Admins".
    cal_name = calendar_name_for_group(gdisplay, kind)
    room_users = COMMUNITY_ROOM_USERS if is_admin_group else ROOM_USERS
    room_suffix = COMMUNITY_ROOM_TOPIC_SUFFIX if is_admin_group else ROOM_TOPIC_SUFFIX
    host_key_url = COMMUNITY_HOST_KEY_DOC_URL if is_admin_group and not no_zoom else None

    # The Google Group's primary address is normally the full name; --alias
    # adds a second (shorter) address to reach it, and makes the Gather
    # group's mailing list use that alias instead of the full-name local
    # part — mirroring what change_group_email_alias.py does after the fact.
    # Email local parts are case-insensitive, so normalize to lowercase.
    #
    # --short-address instead makes the truncated name the primary address,
    # with the full name as the alias; the mailing list uses the primary.
    if short_address:
        short_local = truncate_local_part(gemail.split("@")[0])
        if not short_local:
            sys.exit(f"Error: --short-address: {gemail!r} has no 'Circle'/'Working "
                     "Group' suffix to drop.")
        alias_email = gemail
        gemail = f"{short_local}@{DOMAIN}"
        list_local = short_local
    else:
        alias = alias.lower() if alias else None
        alias_email = f"{alias}@{DOMAIN}" if alias else None
        list_local = alias if alias else gemail.split("@")[0]

    creds = get_credentials(credentials_path)
    drive_service = build("drive", "v3", credentials=creds)
    dir_service = build("admin", "directory_v1", credentials=creds)
    settings_service = build("groupssettings", "v1", credentials=creds)
    calendar_service = build("calendar", "v3", credentials=creds)
    check_calendar_owner(calendar_service)
    zoom = ZoomClient(**load_zoom_credentials())

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page()

        try:
            login(page, base_url, email, password)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            close_log()
            browser.close()
            sys.exit(1)

        # In idempotent mode, artifacts that already exist are reused instead
        # of aborting — so a re-run after a partial failure can finish the
        # remaining steps. These flags record what was found up front.
        def fail(msg: str) -> None:
            close_log()
            browser.close()
            sys.exit(f"Error: {msg}")

        # ── Validate: Drive folder ──────────────────────────────────────────
        try:
            parent_id = parent_folder_id_for_group_type(drive_service, drive_id, kind)
        except ValueError as e:
            fail(str(e))

        existing_folder_id = find_folder_id_under_parent(
            drive_service, drive_id, parent_id, folder_name)
        if existing_folder_id and not idempotent:
            fail(f"A folder named {folder_name!r} already exists here "
                 f"(id={existing_folder_id}).{REUSE_HINT}")

        # ── Validate: Google Group ──────────────────────────────────────────
        google_group_exists = group_exists(dir_service, gemail)
        if google_group_exists and not idempotent:
            fail(f"Google Group '{gemail}' already exists.{REUSE_HINT}")

        if alias_email:
            if alias_email.lower() == gemail.lower():
                fail(f"alias '{alias_email}' is the same as the group's primary address.")
            alias_group = get_group_by_email(dir_service, alias_email)
            if alias_group:
                primary_group = get_group_by_email(dir_service, gemail)
                # An alias that already resolves to *this* group is fine to
                # reuse in idempotent mode; one pointing elsewhere never is.
                same_group = (
                    idempotent and primary_group
                    and alias_group.get("id") == primary_group.get("id")
                )
                if not same_group:
                    fail(f"'{alias_email}' is already in use by another Google Group.")

        # ── Validate: an admin group's audience ─────────────────────────────
        # The Google Group its events invite and its reminders go to must
        # exist, and should accept the reminders admin@ sends it.
        if is_admin_group:
            audience_email = audience_override or COMMUNITY_CALENDAR_AUDIENCE
            if get_group_by_email(dir_service, audience_email) is None:
                fail(f"the audience, '{audience_email}', isn't a Google Group.")
            who_can_post = settings_service.groups().get(
                groupUniqueId=audience_email).execute().get("whoCanPostMessage")
            if who_can_post not in ("ANYONE_CAN_POST", "ALL_IN_DOMAIN_CAN_POST"):
                posters = AUDIENCE_POSTERS.get(who_can_post, who_can_post)
                print(f"WARNING: only {posters} can post to '{audience_email}', so the "
                      "reminders admin@ sends it may bounce. Let anyone in the organization "
                      "post (in its Google Group settings, Who can post).")
                log("WARN", "audience_posting", f"{audience_email}: {who_can_post}")

        # ── Validate: Gather group ──────────────────────────────────────────
        existing_gather_id = find_gather_group_id_by_name(page, base_url, gdisplay)
        if existing_gather_id and not idempotent:
            fail(f"a Gather group named '{gdisplay}' already exists "
                 f"(id={existing_gather_id}).{REUSE_HINT}")

        # ── Validate: parent circle ──────────────────────────────────────────
        parent_group = None
        if group_type not in (GroupType.CLUB, GroupType.ADMIN):
            if not parent_circle_prefix:
                close_log()
                browser.close()
                sys.exit("Error: --parent-circle is required unless --type=club or admin.")
            try:
                parent_group = resolve_group_by_prefix(
                    fetch_all_gather_groups(page, base_url), parent_circle_prefix)
                parent_kind = (_fetch_group_detail(page, base_url, parent_group).kind
                               or "").strip().lower()
                check_parent_kind(kind, parent_group.name, parent_kind)
            except ValueError as e:
                close_log()
                browser.close()
                sys.exit(f"Error: {e}")

        # ── Validate: manager ────────────────────────────────────────────────
        gather_users = fetch_all_gather_users(page, base_url)
        matches = find_users_by_prefix(gather_users, manager_prefix)
        if not matches:
            close_log()
            browser.close()
            sys.exit(f"Error: no community member's first name, last name, or email "
                     f"starts with {manager_prefix!r}.")
        if len(matches) > 1:
            names = ", ".join(f"{u.full_name} <{u.email}>" for u in matches[:10])
            more = "" if len(matches) <= 10 else f", … ({len(matches)} total)"
            close_log()
            browser.close()
            sys.exit(f"Error: {manager_prefix!r} matches multiple members: {names}{more}. "
                     "Use a longer, unique prefix.")
        manager = matches[0]

        # ── Welcome message ──────────────────────────────────────────────────
        # Signed with the first name of the Gather user running the script
        # (left blank to fill in if their login email matches no member).
        sender = next(
            (u for u in gather_users if u.email and u.email.lower() == email.lower()), None)
        list_address = f"{list_local}@{DOMAIN}"
        audience = None
        # A re-run (-i) keeps a footer's "Zoom meeting: none".
        if is_admin_group and google_group_exists and not no_zoom and parse_footer_no_zoom(
                settings_service.groups().get(groupUniqueId=gemail).execute()
                .get("customFooterText")):
            no_zoom = True
            host_key_url = None
        if is_admin_group:
            if audience_override:
                audience = audience_override.lower()
            elif google_group_exists:
                audience = parse_footer_audience(settings_service.groups().get(
                    groupUniqueId=gemail).execute().get("customFooterText"))
            audience = audience or COMMUNITY_CALENDAR_AUDIENCE
            subject, body = build_admin_welcome_message(
                cal_name, list_address, manager.first_name,
                sender.first_name if sender else "", audience, zoom=not no_zoom)
        else:
            subject, body = build_welcome_message(
                gdisplay, list_address, manager.first_name, sender.first_name if sender else "")

        if dry_run:
            if idempotent and (existing_folder_id or google_group_exists or existing_gather_id):
                reused = ", ".join(filter(None, [
                    "Drive folder" if existing_folder_id else "",
                    "Google Group" if google_group_exists else "",
                    "Gather group" if existing_gather_id else "",
                ]))
                print(f"[dry-run] Would reuse existing: {reused}.")
            where = (f" in '{PARENT_FOLDER_NAMES[kind]}'" if kind in PARENT_FOLDER_NAMES else "")
            print(f"[dry-run] Would create Drive folder '{folder_name}'{where}, Google Group "
                  f"'{gdisplay}' <{gemail}>, and Gather group '{gdisplay}' "
                  f"(type={group_type.label}).")
            print(f"[dry-run] Would add {manager.full_name} <{manager.email}> as a manager "
                  "of the new Gather group.")
            if alias_email:
                print(f"[dry-run] Would add alias '{alias_email}' to the Google Group and "
                      f"set the Gather group's mailing list to '{list_local}@{DOMAIN}'.")
            if no_zoom:
                print("[dry-run] Would give it no Zoom room (its footer would say "
                      "\"Zoom meeting: none\").")
            if audience:
                print(f"[dry-run] Would name {audience} as the calendar's audience in the "
                      "footer: its events invite it, and its reminders go to it.")
            if also_invite:
                print(f"[dry-run] Would have its events also invite {', '.join(also_invite)}.")
            if parent_group is not None:
                print(f"[dry-run] Would have it put under '{parent_group.name}' in the "
                      "Circle Structure.")
            print(f"[dry-run] Would add {manager.email} to the Google Group.")
            calendar_and_room(
                calendar_service, zoom, settings_service, cal_name, gemail,
                google_group_exists, no_zoom, room_users, room_suffix, gdisplay, dry_run=True)
            if send_email:
                print("[dry-run] Would open this welcome message as a draft in your "
                      "mail program:\n")
                print_welcome_message(list_address, subject, body)
            close_log()
            browser.close()
            return

        # ── Create Drive folder (or reuse an existing one) ───────────────────
        if existing_folder_id:
            folder_id = existing_folder_id
            log("INFO", "reuse_folder", f"Reusing '{folder_name}' (id={folder_id})")
            print(f"Reusing existing Drive folder '{folder_name}' (id={folder_id}).")
        else:
            folder_id = create_drive_folder(drive_service, folder_name, parent_id)
            log("INFO", "create_folder", f"Created '{folder_name}' (id={folder_id})")
            print(f"Created Drive folder '{folder_name}' (id={folder_id}).")

        # ── Create Google Group (or reuse an existing one) ───────────────────
        if google_group_exists:
            log("INFO", "reuse_google_group", gemail)
            print(f"Reusing existing Google Group '{gemail}'.")
        else:
            dir_service.groups().insert(
                body={"email": gemail, "name": gdisplay}
            ).execute()
            log("INFO", "create_google_group", gemail)
            print(f"Created Google Group '{gemail}'.")

        ensure_group_settings(settings_service, gemail, REQUIRED_GROUP_SETTINGS)
        ensure_group_settings(settings_service, gemail, CONVERSATION_HISTORY_SETTINGS)
        ensure_group_settings(settings_service, gemail, MODERATION_SETTINGS)

        if alias_email:
            add_group_alias(dir_service, gemail, alias_email)
            log("INFO", "add_alias", f"{gemail} -> +{alias_email}")
            print(f"Added alias '{alias_email}' to Google Group '{gemail}'.")

        # ── Create Gather group (with the manager as an inline member) ───────
        # Adding the manager now — before the Drive folder is linked below —
        # lets Gather auto-sync their folder permission when the link is made.
        availability = "open" if group_type == GroupType.CLUB else "closed"
        if existing_gather_id:
            gather_group_id = existing_gather_id
            ensure_gather_group_manager(page, base_url, gather_group_id, manager, dry_run=False)
            print(f"Reusing existing Gather group '{gdisplay}' (id={gather_group_id}); "
                  f"ensured {manager.full_name} is a manager.")
        else:
            gather_group_id = create_gather_group(
                page, base_url, gdisplay, kind, availability=availability,
                members=[(manager, True)], dry_run=False,
            )
            if not gather_group_id:
                fail("failed to create the Gather group — see log.")
            print(f"Created Gather group '{gdisplay}' (id={gather_group_id}) "
                  f"with manager {manager.full_name}.")

        # ── Link folder on /gdrive/config (or reuse an existing link) ────────
        existing_item_id = None
        if idempotent:
            for entry in scrape_gdrive_config(page, base_url):
                if entry.get("google_file_id") == folder_id:
                    existing_item_id = entry.get("item_id")
                    break
        if existing_item_id:
            print(f"Folder '{folder_name}' is already linked on /gdrive/config "
                  f"(item_id={existing_item_id}).")
            ok = add_group_access_to_gdrive_item(
                page, base_url, existing_item_id, gdisplay, dry_run=False)
            if ok:
                print(f"Ensured '{gdisplay}' has Content manager access.")
            else:
                print(f"ERROR: failed to add '{gdisplay}' to the linked folder — see log.")
        else:
            item_id, err = create_gdrive_item(page, base_url, folder_id, dry_run=False)
            if err:
                log("ERROR", "link_folder", f"{folder_name}: {err}")
                print(f"ERROR: failed to link folder on /gdrive/config — {err}")
            else:
                ok = add_group_access_to_gdrive_item(page, base_url, item_id, gdisplay, dry_run=False)
                if ok:
                    print(f"Linked '{folder_name}' and added '{gdisplay}' as Content manager.")
                else:
                    print(f"ERROR: folder linked, but failed to add '{gdisplay}' — see log.")

        # ── Wire the mailing list (to the alias, if given) ───────────────────
        set_gather_group_email_list(
            page, base_url, gather_group_id, list_local, DOMAIN,
            dry_run=False, all_can_send=True,
        )
        print(f"Configured mailing list for '{gdisplay}' as '{list_local}@{DOMAIN}'.")

        # ── Calendar and Zoom room (or the ones its footer already links) ───
        found = calendar_and_room(
            calendar_service, zoom, settings_service, cal_name, gemail,
            google_group_exists, no_zoom, room_users, room_suffix, gdisplay, dry_run=False)
        calendar_id, room = found["calendar_id"], found["room"]

        # ── Write the Google Group's footer link block ───────────────────────
        ensure_group_footer(settings_service, gemail, gather_group_id, folder_id, base_url,
                            gdisplay, calendar_id=calendar_id,
                            zoom_url=room.join_url if room else None,
                            zoom_passcode=room.passcode if room else None, audience=audience,
                            host_key_url=host_key_url, no_zoom=no_zoom or None,
                            also_invite=also_invite)
        print(f"Set footer links for '{gemail}'.")

        # ── Have it put under its parent in the Circle Structure ─────────────
        if parent_group is not None:
            try:
                add_placements(drive_service, {gather_group_id: parent_group.group_id},
                               dry_run=False)
                print(f"'{gdisplay}' will be put under '{parent_group.name}' in the Circle "
                      "Structure within the hour.")
            except Exception as e:
                log("ERROR", "placements", str(e))
                print(f"ERROR: couldn't write its parent for the Circle Structure ({e}); ask "
                      f"the Coordinating Circle to put it under '{parent_group.name}'.")

        # ── With --agenda-doc, give it an agenda doc, for the Apps Script to build ─
        if agenda_doc and kind in AGENDA_KINDS:
            try:
                ensure_agenda_doc(drive_service, gemail, gdisplay, folder_id, dry_run=False)
                print(f"Created '{agenda_doc_name(gdisplay)}', to be built within minutes.")
            except Exception as e:
                log("ERROR", "agenda_doc", str(e))
                print(f"ERROR: failed to create the agenda doc: {e}")

        # ── Add the manager to the Google Group ──────────────────────────────
        # groups_drive_sync.gs would add them on its next hourly run (they're
        # a Content manager of the folder via the Gather group); doing it now
        # means the list reaches them straight away, welcome message included.
        if not manager.email:
            log("WARN", "add_manager_to_google_group", f"{manager.full_name} has no email")
            print(f"WARNING: {manager.full_name} has no email in Gather, so they weren't "
                  f"added to '{gemail}'.")
        else:
            try:
                added = ensure_group_member(dir_service, gemail, manager.email)
                log("INFO", "add_manager_to_google_group",
                    f"{manager.email} -> {gemail} ({'added' if added else 'already a member'})")
                print(f"{'Added' if added else 'Already a member:'} {manager.email} "
                      f"{'to' if added else 'of'} '{gemail}'.")
            except Exception as e:
                log("ERROR", "add_manager_to_google_group", f"{manager.email} -> {gemail}: {e}")
                print(f"ERROR: failed to add {manager.email} to '{gemail}': {e}")

        # ── Open the welcome message as a draft ──────────────────────────────
        if send_email:
            if open_draft(list_address, subject, body):
                log("INFO", "welcome_draft", f"opened draft to {list_address}")
                print(f"Opened a draft to {list_address} in your mail program — "
                      "review it and click Send.")
            else:
                log("WARN", "welcome_draft", "couldn't open a mail program")
                print("Couldn't open a draft in a mail program. Send this message "
                      "yourself:\n")
                print_welcome_message(list_address, subject, body)

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Create a new Drive folder, Google Group, and Gather group, "
                    "all linked together, and put it under its parent in the Circle Structure.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-N", "--name", nargs="+", required=True,
        help="Name of the folder and the groups (multiple words are joined "
             "with spaces, so quotes aren't needed)",
    )
    parser.add_argument(
        "-t", "--type", type=GroupType.parse, default=None,
        help="Group type — 'c'/'circle', 'w'/'working group', 'b'/'club', or 'a'/'admin' "
             "(a community-wide calendar's admin group, its calendar named without "
             "'Admins'). Only required if the name doesn't end with a type suffix "
             "('Circle'/'Working Group'/'Club'/'Admins'), from which it's otherwise inferred.",
    )
    parser.add_argument(
        "-p", "--parent-circle", default=None,
        help="A prefix uniquely identifying the parent's Gather group: a circle, or, "
             "for a working group, a circle or working group (required unless --type=club "
             "or admin)",
    )
    parser.add_argument(
        "-m", "--manager", required=True,
        help="A prefix of a community member's first name, last name, or email "
             "that uniquely identifies them. They're added to the new Gather "
             "group as a manager (and thus given Content-manager access to the "
             "Drive folder when it's linked).",
    )
    parser.add_argument(
        "-a", "--alias", default=None,
        help=f"A shorter alias's local part (without the @{DOMAIN} domain). The "
             "Google Group is still created at its full-name address; this alias "
             "is added as a second address for it, and the Gather group's mailing "
             "list is set to the alias — same effect as running "
             "change_group_email_alias.py afterward.",
    )
    parser.add_argument(
        "-s", "--short-address", action="store_true",
        help="Make the Google Group's primary address the name without its "
             "'Circle'/'Working Group' suffix (e.g. landscape@ rather than "
             "landscape-circle@), and add the full-name address as an alias. The "
             "Gather group's mailing list uses the short address. Not for clubs; "
             "can't be combined with --alias.",
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Validate and report what would be created, without creating anything",
    )
    parser.add_argument(
        "-i", "--idempotent", action="store_true",
        help="Reuse any artifacts (Drive folder, Google Group, Gather group, "
             "alias, folder link, hierarchy entry) that already exist instead "
             "of aborting — lets a re-run finish after a partial failure.",
    )
    parser.add_argument(
        "-E", "--no-email", action="store_true",
        help="Don't open the welcome message to the new list as a draft (e.g. on "
             "an --idempotent re-run after the first run's message was sent)",
    )
    parser.add_argument(
        "-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
        help=f"Path to OAuth client secrets JSON (default: {DEFAULT_CLIENT_SECRETS_PATH})",
    )
    parser.add_argument(
        "-d", "--drive-id", default=DEFAULT_DRIVE_ID,
        help="Shared Drive ID to create the new folder in",
    )
    parser.add_argument(
        "--agenda-doc", action="store_true",
        help="For a circle or working group, also create its agenda doc, for the "
             "circle_agendas Apps Script to build",
    )
    parser.add_argument(
        "-A", "--audience", metavar="ADDRESS", default=None,
        help="For an admin group, the Google Group its calendar's events invite and its "
             f"reminders go to, instead of {COMMUNITY_CALENDAR_AUDIENCE} (say, a small "
             "group, to try a community calendar out without emailing everyone); its "
             f"address, or just the part before @{DOMAIN}",
    )
    parser.add_argument(
        "-z", "--no-zoom", action="store_true",
        help="For an admin group, give its calendar no Zoom room, for a calendar of "
             "in-person events: its footer says \"Zoom meeting: none\", so the scripts never "
             "make one, and its events get no Zoom details",
    )
    parser.add_argument(
        "-I", "--also-invite", metavar="ADDRESSES", action="append", default=None,
        help="For an admin group, more addresses (any domain; just the name for "
             f"@{DOMAIN}) that every event on its calendar invites along with its "
             "audience, but doesn't remind; separate several with commas, or give the "
             "option again",
    )
    args = parser.parse_args()

    name = " ".join(args.name)
    group_type = args.type or GroupType.from_name_suffix(name)
    if group_type is None:
        sys.exit(
            f"Error: couldn't infer the group type from {name!r} — it doesn't end "
            "with 'Circle', 'Working Group', 'Club', or 'Admins'. Pass -t/--type explicitly."
        )

    also_invite = None
    if args.also_invite:
        if group_type != GroupType.ADMIN:
            sys.exit("Error: --also-invite only applies to admin groups (-t a).")
        try:
            also_invite = also_invite_addresses(args.also_invite)
        except ValueError as e:
            sys.exit(f"Error: --also-invite: {e}.")
    if args.no_zoom and group_type != GroupType.ADMIN:
        sys.exit("Error: --no-zoom only applies to admin groups (-t a).")
    if args.audience and group_type != GroupType.ADMIN:
        sys.exit("Error: --audience only applies to admin groups (-t a).")
    if args.audience and "@" not in args.audience:
        args.audience = f"{args.audience}@{DOMAIN}"
    if args.audience and not is_in_domain(args.audience):
        sys.exit(f"Error: --audience must be an @{DOMAIN} address (or just its name).")

    if args.short_address and args.alias:
        sys.exit("Error: --short-address and --alias can't be combined.")
    if args.short_address and group_type in (GroupType.CLUB, GroupType.ADMIN):
        sys.exit("Error: --short-address only applies to circles and working groups.")

    if args.alias and "@" in args.alias:
        sys.exit(
            f"Error: --alias should be just the local part, not a full address "
            f"({args.alias!r}) — the domain is always @{DOMAIN}."
        )

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    email, password = load_credentials()
    main(
        group_type, name, args.parent_circle, args.manager, args.base_url, email,
        password, args.dry_run, args.credentials, args.drive_id, alias=args.alias,
        short_address=args.short_address, idempotent=args.idempotent, send_email=not args.no_email,
        agenda_doc=args.agenda_doc, audience_override=args.audience, no_zoom=args.no_zoom,
        also_invite=also_invite,
    )


if __name__ == "__main__":
    cli()
