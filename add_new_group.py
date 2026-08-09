#!/usr/bin/env python3
"""
Creates a new Drive folder, Google Group, and Gather group, all linked
together, and (unless --type=club) adds the new group to the Circle
Hierarchy wiki page under its parent circle.

Steps:
  1. Validate: the folder, Google Group, and Gather group don't already
     exist; (unless --type=club) --parent-circle uniquely identifies a
     live entry in the Circle Hierarchy; and --manager uniquely identifies
     a community member.
  2. Create the Drive folder, Google Group (with the usual settings, plus
     "Conversation history" on and content moderation open to all members),
     and Gather group (with the usual settings, except clubs get
     availability=open instead of closed) — the --manager member is added
     to the Gather group as a manager at creation, before the folder is
     linked, so Gather auto-syncs their Content-manager permission.
  3. Link the folder on /gdrive/config and grant the new Gather group
     Content manager access.
  4. Wire the Gather group's mailing list to the new Google Group (to the
     --alias address if given, else the full-name address).
  5. Unless --type=club, add a row for the new group to the Circle
     Hierarchy, alphabetically under its parent circle.
  6. Write the new Google Group's custom footer with a "Gather group" /
     "Google Docs folder" link block (see util/google_group_footer.py) —
     this is what groups_drive_sync.gs reads to sync membership, so no
     Apps Script redeploy is needed for a new group to start syncing.

Requires Google API access (Drive, Admin Directory, Groups Settings) —
see update_groups_in_google_and_hierarchy.py's docstring for setup.

The group type (circle / working group / club) is inferred from a name
ending in the matching suffix ("Circle" / "Working Group" / "Club");
pass -t/--type explicitly only when the name has no such suffix.

The name may be given as multiple unquoted words.

Pass --alias to also give the new Google Group a shorter second address
and point the Gather group's mailing list at it — the same effect as
running change_group_email_alias.py afterward.

--manager (required) is a prefix of a member's first name, last name, or
email uniquely identifying them; they're made a manager of the new group.

Usage:
    python add_new_group.py Landscape Circle -p Property -m jsmith
    python add_new_group.py Tech Support -t w -p Operations -m Dana
    python add_new_group.py Book Club -m alice@example.org
    python add_new_group.py Landscape Circle -p Property -m jsmith --alias landscape
"""

import argparse
import os
import re
import sys
from enum import Enum
from pathlib import Path

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from google_setup.match_google_groups_to_drive_folders import DEFAULT_DRIVE_ID
from util.credentials import load_credentials
from util.gather_utils import (
    close_log,
    configure,
    create_gather_group,
    fetch_all_gather_groups,
    fetch_all_gather_users,
    find_gather_group_id_by_name,
    find_users_by_prefix,
    init_log,
    launch_browser,
    log,
    login,
    set_gather_group_email_list,
)
from util.gdrive_config import (
    FOLDER_TYPE_SUFFIXES,
    add_group_access_to_gdrive_item,
    create_drive_folder,
    create_gdrive_item,
    ensure_folder_name_available,
    folder_name_for_group_type,
    gdrive_item_url,
    parent_folder_id_for_group_type,
)
from util.google_group_footer import ensure_group_footer
from util.google_group_utils import (
    CONVERSATION_HISTORY_SETTINGS,
    DEFAULT_CLIENT_SECRETS_PATH,
    DOMAIN,
    MODERATION_SETTINGS,
    REQUIRED_GROUP_SETTINGS,
    add_group_alias,
    ensure_group_settings,
    get_credentials,
    group_display_name,
    group_email,
    group_exists,
)
from util.hierarchy_wiki import (
    HierarchyNode,
    ensure_hierarchy_page,
    fetch_hierarchy_page_content,
    find_unique_node_by_prefix,
    iter_nodes,
    parse_hierarchy,
    render_hierarchy,
)

_LOG_FILE = Path("debug/add_new_group_log.csv")
_SCREENSHOT_DIR = Path("debug/add_new_group_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


class GroupType(Enum):
    CIRCLE = ("circle", "circle", "c")
    WORKING_GROUP = ("working group", "committee", "w")
    CLUB = ("club", "club", "b")

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
        ("Circle"/"Working Group"/"Club"), or None if it ends with none."""
        for member in cls:
            suffix = FOLDER_TYPE_SUFFIXES[member.kind]
            if re.search(r"\b" + re.escape(suffix) + r"\s*$", name, re.IGNORECASE):
                return member
        return None


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
) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start",
        f"type={group_type.kind} name={raw_name!r} manager={manager_prefix!r} "
        f"alias={alias!r} dry_run={dry_run}")

    kind = group_type.kind
    folder_name = folder_name_for_group_type(raw_name, kind)
    gemail = group_email(folder_name)
    gdisplay = group_display_name(folder_name)

    # The Google Group's primary address is always the full name; --alias
    # adds a second (shorter) address to reach it, and makes the Gather
    # group's mailing list use that alias instead of the full-name local
    # part — mirroring what change_group_email_alias.py does after the fact.
    # Email local parts are case-insensitive, so normalize to lowercase.
    alias = alias.lower() if alias else None
    alias_email = f"{alias}@{DOMAIN}" if alias else None
    list_local = alias if alias else gemail.split("@")[0]

    creds = get_credentials(credentials_path)
    drive_service = build("drive", "v3", credentials=creds)
    dir_service = build("admin", "directory_v1", credentials=creds)
    settings_service = build("groupssettings", "v1", credentials=creds)

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

        # ── Validate: Drive folder ──────────────────────────────────────────
        try:
            parent_id = parent_folder_id_for_group_type(drive_service, drive_id, kind)
        except ValueError as e:
            close_log()
            browser.close()
            sys.exit(f"Error: {e}")
        try:
            ensure_folder_name_available(drive_service, drive_id, parent_id, folder_name)
        except ValueError as e:
            close_log()
            browser.close()
            sys.exit(f"Error: {e}")

        # ── Validate: Google Group ──────────────────────────────────────────
        if group_exists(dir_service, gemail):
            close_log()
            browser.close()
            sys.exit(f"Error: Google Group '{gemail}' already exists.")

        if alias_email:
            if alias_email.lower() == gemail.lower():
                close_log()
                browser.close()
                sys.exit(f"Error: --alias {alias!r} is the same as the group's primary address.")
            if group_exists(dir_service, alias_email):
                close_log()
                browser.close()
                sys.exit(f"Error: '{alias_email}' is already in use by another Google Group.")

        # ── Validate: Gather group ──────────────────────────────────────────
        existing_gather_id = find_gather_group_id_by_name(page, base_url, gdisplay)
        if existing_gather_id:
            close_log()
            browser.close()
            sys.exit(
                f"Error: a Gather group named '{gdisplay}' already exists "
                f"(id={existing_gather_id})."
            )

        # ── Validate: parent circle ──────────────────────────────────────────
        parent_node = None
        root = None
        if group_type != GroupType.CLUB:
            if not parent_circle_prefix:
                close_log()
                browser.close()
                sys.exit("Error: --parent-circle is required unless --type=club.")

            current_content = fetch_hierarchy_page_content(page, base_url)
            root = parse_hierarchy(current_content)
            all_nodes = list(iter_nodes(root))
            try:
                parent_node = find_unique_node_by_prefix(all_nodes, parent_circle_prefix)
            except ValueError as e:
                close_log()
                browser.close()
                sys.exit(f"Error: {e}")

            if not parent_node.group_id:
                close_log()
                browser.close()
                sys.exit(
                    f"Error: hierarchy entry '{parent_node.name}' has no linked Gather group."
                )
            live_group_ids = {g.group_id for g in fetch_all_gather_groups(page, base_url)}
            if parent_node.group_id not in live_group_ids:
                close_log()
                browser.close()
                sys.exit(
                    f"Error: parent circle '{parent_node.name}' (group id="
                    f"{parent_node.group_id}) no longer exists in Gather."
                )

        # ── Validate: manager ────────────────────────────────────────────────
        matches = find_users_by_prefix(fetch_all_gather_users(page, base_url), manager_prefix)
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

        if dry_run:
            print(f"[dry-run] Would create Drive folder '{folder_name}', Google Group "
                  f"'{gemail}', and Gather group '{gdisplay}' (type={group_type.label}).")
            print(f"[dry-run] Would add {manager.full_name} <{manager.email}> as a manager "
                  "of the new Gather group.")
            if alias_email:
                print(f"[dry-run] Would add alias '{alias_email}' to the Google Group and "
                      f"set the Gather group's mailing list to '{list_local}@{DOMAIN}'.")
            if parent_node is not None:
                print(f"[dry-run] Would add it to the Circle Hierarchy under "
                      f"'{parent_node.name}'.")
            close_log()
            browser.close()
            return

        # ── Create Drive folder ──────────────────────────────────────────────
        folder_id = create_drive_folder(drive_service, folder_name, parent_id)
        log("INFO", "create_folder", f"Created '{folder_name}' (id={folder_id})")
        print(f"Created Drive folder '{folder_name}' (id={folder_id}).")

        # ── Create Google Group ──────────────────────────────────────────────
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
        gather_group_id = create_gather_group(
            page, base_url, gdisplay, kind, availability=availability,
            members=[(manager, True)], dry_run=False,
        )
        if not gather_group_id:
            close_log()
            browser.close()
            sys.exit("Error: failed to create the Gather group — see log.")
        print(f"Created Gather group '{gdisplay}' (id={gather_group_id}) "
              f"with manager {manager.full_name}.")

        # ── Link folder on /gdrive/config ────────────────────────────────────
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

        # ── Write the Google Group's footer link block ───────────────────────
        ensure_group_footer(settings_service, gemail, gather_group_id, folder_id, base_url, gdisplay)
        print(f"Set footer links for '{gemail}'.")

        # ── Add to the Circle Hierarchy ──────────────────────────────────────
        if parent_node is not None:
            new_node = HierarchyNode(
                name=gdisplay,
                group_id=gather_group_id,
                members_url=f"/groups/{gather_group_id}",
                documents_url=gdrive_item_url(folder_id),
                parent=parent_node,
            )
            parent_node.children.append(new_node)
            new_content = render_hierarchy(root)
            ok = ensure_hierarchy_page(page, base_url, new_content, dry_run=False)
            if ok:
                print(f"Added '{gdisplay}' to the Circle Hierarchy under '{parent_node.name}'.")
            else:
                log("ERROR", "hierarchy", "Failed to write hierarchy wiki page — see log")
                print("ERROR: failed to update the Circle Hierarchy wiki page — see log.")

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Create a new Drive folder, Google Group, and Gather group, "
                    "all linked together, and add it to the Circle Hierarchy.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "name", nargs="+",
        help="Name of the folder and the groups (multiple words are joined "
             "with spaces, so quotes aren't needed)",
    )
    parser.add_argument(
        "-t", "--type", type=GroupType.parse, default=None,
        help="Group type — 'c'/'circle', 'w'/'working group', or 'b'/'club'. "
             "Only required if the name doesn't end with a type suffix "
             "('Circle'/'Working Group'/'Club'), from which it's otherwise inferred.",
    )
    parser.add_argument(
        "-p", "--parent-circle", default=None,
        help="A prefix uniquely identifying the parent circle's entry in the "
             "Circle Hierarchy (required unless --type=club)",
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
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Validate and report what would be created, without creating anything",
    )
    parser.add_argument(
        "-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
        help=f"Path to OAuth client secrets JSON (default: {DEFAULT_CLIENT_SECRETS_PATH})",
    )
    parser.add_argument(
        "-d", "--drive-id", default=DEFAULT_DRIVE_ID,
        help="Shared Drive ID to create the new folder in",
    )
    args = parser.parse_args()

    name = " ".join(args.name)
    group_type = args.type or GroupType.from_name_suffix(name)
    if group_type is None:
        sys.exit(
            f"Error: couldn't infer the group type from {name!r} — it doesn't end "
            "with 'Circle', 'Working Group', or 'Club'. Pass -t/--type explicitly."
        )

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
    )


if __name__ == "__main__":
    cli()
