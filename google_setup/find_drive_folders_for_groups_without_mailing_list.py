#!/usr/bin/env python3
"""
For every Gather group under /groups (excluding groups with Availability =
Everybody) that has no mailing list configured, search the Shared Drive
(descending its full folder tree) for folder names that roughly match the
group's name. In addition to the strict prefix/abbreviation match, a folder
sharing just one significant word with the group name (e.g. group
'Landscape' vs. folder 'Landscape & Grounds Photos') is also reported,
flagged as a weaker match.

For each group with candidate folders, prompts you to pick one (or skip).
If you pick a folder:
  - Errors out (and leaves Gather untouched) if that folder is already
    linked on /gdrive/config.
  - Otherwise, links the folder there and adds the group with Content
    manager access.

Setup:
  1. Ensure ~/.gather contains your Gather admin credentials (email + password).
  2. In Google Cloud Console, create a Desktop OAuth 2.0 client and download
     the JSON as client_secret.json (or pass a different path via -c).
  3. Enable the Google Drive API for the project.
  4. On first run the script prints an auth URL — paste it into a browser
     signed in as a Workspace super-admin.  The token is cached at
     ~/.google_setup_token.pkl for subsequent runs.

Usage:
    python find_drive_folders_for_groups_without_mailing_list.py -e admin@example.com -p secret
    python find_drive_folders_for_groups_without_mailing_list.py -u https://example.gather.coop \
        -d SHARED_DRIVE_ID -c client_secret.json
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from google_setup.match_google_groups_to_drive_folders import (
    ABBREV_STOP_WORDS,
    DEFAULT_DRIVE_ID,
    folder_matches_group,
    singularize,
    to_match_base,
)
from util.credentials import load_credentials
from util.gather_utils import (
    _fetch_group_detail,
    close_log,
    configure,
    fetch_all_gather_groups,
    init_log,
    launch_browser,
    log,
    login,
)
from util.gdrive_config import add_group_access_to_gdrive_item, create_gdrive_item, walk_drive_folders
from util.google_group_utils import get_credentials

_LOG_FILE = Path("debug/find_drive_folders_log.csv")
_SCREENSHOT_DIR = Path("debug/find_drive_folders_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


MIN_TERM_LENGTH = 3


def significant_words(base: str) -> set[str]:
    """Words worth matching on: no stop words, no very short/common words."""
    return {
        w for w in re.findall(r"[a-z0-9]+", base)
        if w not in ABBREV_STOP_WORDS and len(w) >= MIN_TERM_LENGTH
    }


def shares_significant_term(group_name: str, folder_name: str) -> bool:
    """True if the group and folder names share at least one significant word,
    e.g. group 'Landscape' vs. folder 'Landscape & Grounds Photos'."""
    group_words = significant_words(singularize(to_match_base(group_name)))
    folder_words = significant_words(singularize(to_match_base(folder_name)))
    return bool(group_words & folder_words)


def find_matching_folders(group_name: str, folders: list[dict]) -> list[dict]:
    """Return candidate folders, each tagged with how it matched.

    Folders satisfying the strict prefix/abbreviation rule (folder_matches_group)
    are tagged "strict"; folders that merely share one significant word with the
    group name are tagged "term" — a weaker signal meant to widen the candidate
    list for human review.
    """
    matches = []
    for f in folders:
        if folder_matches_group(group_name, f["name"]):
            matches.append({**f, "match_kind": "strict"})
        elif shares_significant_term(group_name, f["name"]):
            matches.append({**f, "match_kind": "term"})
    return matches


# ── Interactive selection ─────────────────────────────────────────────────────

def prompt_folder_choice(group_name: str, matches: list[dict]) -> dict | None:
    """Print candidate folders for group_name and prompt the user to pick one.
    Returns the chosen folder dict, or None if the user skips."""
    print(f"[{len(matches)} MATCH(ES)]  '{group_name}'")
    for i, f in enumerate(matches, start=1):
        path_str = " / ".join(f["path"])
        tag = "" if f["match_kind"] == "strict" else " [weak: shared term only]"
        print(f"    {i}. {path_str}   ({f['id']}){tag}")

    while True:
        choice = input(
            f"    Select folder to link [1-{len(matches)}], or Enter to skip: "
        ).strip()
        if choice == "":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(matches):
            return matches[int(choice) - 1]
        print("    Invalid choice.")


def link_folder_to_group(page, base_url: str, folder: dict, group_name: str, dry_run: bool) -> None:
    """Link folder onto /gdrive/config and grant group_name Content manager
    access. Prints an error and does nothing else if the folder is already
    linked there."""
    item_id, err = create_gdrive_item(page, base_url, folder["id"], dry_run=dry_run)
    if err:
        print(f"    ERROR: folder already in /gdrive/config or failed to link — {err}")
        log("ERROR", "link_folder", f"{group_name}: {folder['name']} — {err}")
        return

    if dry_run:
        print(f"    [dry-run] would link '{folder['name']}' and add "
              f"'{group_name}' as Content manager")
        return

    ok = add_group_access_to_gdrive_item(page, base_url, item_id, group_name, dry_run)
    if ok:
        print(f"    Linked '{folder['name']}' and added '{group_name}' as Content manager.")
    else:
        print(f"    ERROR: folder linked, but failed to add '{group_name}' — see log.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(base_url: str, email: str, password: str, drive_id: str,
         credentials_path: str, dry_run: bool = False):
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"base_url={base_url} drive_id={drive_id}")

    creds = get_credentials(credentials_path)
    drive_service = build("drive", "v3", credentials=creds)

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

        log("INFO", "fetch_groups", "Fetching Gather groups…")
        groups = fetch_all_gather_groups(page, base_url)
        log("INFO", "fetch_groups", f"{len(groups)} group(s) found")

        unlisted = []
        for group in groups:
            detail = _fetch_group_detail(page, base_url, group)
            if detail.availability.strip().lower() == "everybody":
                continue
            if not detail.list_name:
                unlisted.append(detail)

        log("INFO", "filter",
            f"{len(unlisted)} group(s) have no mailing list (excluding Availability=Everybody)")

        log("INFO", "walk_drive", f"Walking folder tree of Shared Drive {drive_id}…")
        folders = walk_drive_folders(drive_service, drive_id)
        log("INFO", "walk_drive", f"{len(folders)} folder(s) found")

        print()
        for group in unlisted:
            matches = find_matching_folders(group.name, folders)
            if not matches:
                print(f"[NO MATCH]  '{group.name}'")
                continue

            chosen = prompt_folder_choice(group.name, matches)
            if chosen is None:
                log("INFO", "skip", f"{group.name}: no folder selected")
                continue

            link_folder_to_group(page, base_url, chosen, group.name, dry_run)

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Find candidate Drive folders for Gather groups that have no mailing list",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-d", "--drive-id", default=DEFAULT_DRIVE_ID,
        help="Shared Drive ID to search",
    )
    parser.add_argument(
        "-c", "--credentials", default="client_secret.json",
        help="Path to OAuth client secrets JSON",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Log what would be linked without making any changes",
    )
    args = parser.parse_args()

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    email, password = load_credentials()
    main(args.base_url, email, password, args.drive_id, args.credentials, args.dry_run)


if __name__ == "__main__":
    cli()
