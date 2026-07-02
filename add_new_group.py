#!/usr/bin/env python3
"""
Creates a new Drive folder, Google Group, and Gather group, all linked
together, and (unless --type=club) adds the new group to the Circle
Hierarchy wiki page under its parent circle.

Steps:
  1. Validate: the new group's email isn't already a key in FOLDER_IDS
     (folder_ids.gs, a {group_email: folder_id} map); the folder, Google
     Group, and Gather group don't already exist; and (unless --type=club)
     --parent-circle uniquely identifies a live entry in the Circle
     Hierarchy.
  2. Create the Drive folder, Google Group (with the usual settings), and
     Gather group (with the usual settings, except clubs get
     availability=open instead of closed).
  3. Link the folder on /gdrive/config and grant the new Gather group
     Content manager access.
  4. Wire the Gather group's mailing list to the new Google Group.
  5. Unless --type=club, add a row for the new group to the Circle
     Hierarchy, alphabetically under its parent circle.
  6. Update FOLDER_IDS and deploy it to the Apps Script project via `clasp`.

Requires Google API access (Drive, Admin Directory, Groups Settings) —
see update_groups_in_google_and_hierarchy.py's docstring for setup.

Usage:
    python add_new_group.py c "Landscape" --parent-circle "Property"
    python add_new_group.py w "Tech Support" -p "Operations"
    python add_new_group.py b "Book Club"
"""

import argparse
import os
import subprocess
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
    find_gather_group_id_by_name,
    init_log,
    launch_browser,
    log,
    login,
    set_gather_group_email_list,
)
from util.gdrive_config import (
    add_group_access_to_gdrive_item,
    create_drive_folder,
    create_gdrive_item,
    ensure_folder_name_available,
    folder_name_for_group_type,
    gdrive_item_url,
    parent_folder_id_for_group_type,
)
from util.google_group_utils import (
    DEFAULT_CLIENT_SECRETS_PATH,
    DOMAIN,
    REQUIRED_GROUP_SETTINGS,
    ensure_group_settings,
    get_credentials,
    group_display_name,
    group_email,
    group_exists,
    read_folder_ids,
    write_folder_ids,
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

_GROUPS_DRIVE_SYNC_DIR = Path(__file__).parent / "groups_drive_sync"


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


def main(
    group_type: GroupType,
    raw_name: str,
    parent_circle_prefix: str | None,
    base_url: str,
    email: str,
    password: str,
    dry_run: bool,
    credentials_path: str,
    drive_id: str,
) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"type={group_type.kind} name={raw_name!r} dry_run={dry_run}")

    kind = group_type.kind
    folder_name = folder_name_for_group_type(raw_name, kind)
    gemail = group_email(folder_name)
    gdisplay = group_display_name(folder_name)

    creds = get_credentials(credentials_path)
    drive_service = build("drive", "v3", credentials=creds)
    dir_service = build("admin", "directory_v1", credentials=creds)
    settings_service = build("groupssettings", "v1", credentials=creds)

    # ── FOLDER_IDS check (no network needed) ────────────────────────────────
    folder_ids_mapping = read_folder_ids()
    if gemail in folder_ids_mapping:
        sys.exit(f"Error: '{gemail}' is already listed in FOLDER_IDS.")

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

        if dry_run:
            print(f"[dry-run] Would create Drive folder '{folder_name}', Google Group "
                  f"'{gemail}', and Gather group '{gdisplay}' (type={group_type.label}).")
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

        # ── Create Gather group ──────────────────────────────────────────────
        availability = "open" if group_type == GroupType.CLUB else "closed"
        gather_group_id = create_gather_group(
            page, base_url, gdisplay, kind, availability=availability, dry_run=False
        )
        if not gather_group_id:
            close_log()
            browser.close()
            sys.exit("Error: failed to create the Gather group — see log.")
        print(f"Created Gather group '{gdisplay}' (id={gather_group_id}).")

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

        # ── Wire the mailing list ────────────────────────────────────────────
        set_gather_group_email_list(
            page, base_url, gather_group_id, gemail.split("@")[0], DOMAIN,
            dry_run=False, all_can_send=True,
        )
        print(f"Configured mailing list for '{gdisplay}'.")

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

    # ── Update and deploy FOLDER_IDS ─────────────────────────────────────────
    folder_ids_mapping[gemail] = folder_id
    write_folder_ids(folder_ids_mapping)
    deploy_folder_ids_to_apps_script()

    close_log()


def deploy_folder_ids_to_apps_script() -> None:
    """Push the updated folder_ids.gs to the Apps Script project via clasp."""
    try:
        result = subprocess.run(
            ["clasp", "push", "--force"],
            cwd=_GROUPS_DRIVE_SYNC_DIR,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("WARNING: `clasp` is not installed — skipping Apps Script deployment. "
              "Run `clasp push` manually from groups_drive_sync/.")
        log("WARN", "deploy", "clasp not installed")
        return

    if result.returncode != 0:
        print(f"WARNING: `clasp push` failed — deploy manually.\n{result.stderr}")
        log("ERROR", "deploy", "clasp push failed", result.stderr[:500])
        return

    print("Deployed folder_ids.gs to the Apps Script project.")
    log("INFO", "deploy", "clasp push succeeded")


def cli():
    parser = argparse.ArgumentParser(
        description="Create a new Drive folder, Google Group, and Gather group, "
                    "all linked together, and add it to the Circle Hierarchy.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "type", type=GroupType.parse,
        help="Group type — 'c'/'circle', 'w'/'working group', or 'b'/'club'",
    )
    parser.add_argument("name", help="Name of the folder and the groups")
    parser.add_argument(
        "-p", "--parent-circle", default=None,
        help="A prefix uniquely identifying the parent circle's entry in the "
             "Circle Hierarchy (required unless --type=club)",
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

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    email, password = load_credentials()
    main(
        args.type, args.name, args.parent_circle, args.base_url, email, password,
        args.dry_run, args.credentials, args.drive_id,
    )


if __name__ == "__main__":
    cli()
