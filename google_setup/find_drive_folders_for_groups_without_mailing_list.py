#!/usr/bin/env python3
"""
Read-only report: for every Gather group under /groups (excluding groups
with Availability = Everybody) that has no mailing list configured, search
the Shared Drive (descending its full folder tree) for folder names that
roughly match the group's name. In addition to the strict prefix/abbreviation
match, a folder sharing just one significant word with the group name (e.g.
group 'Landscape' vs. folder 'Landscape & Grounds Photos') is also reported,
flagged as a weaker match.

Does not modify anything in Gather, Google Groups, or Drive — it only
prints a report of candidate folders per unmatched group, so a human can
decide whether to run init_google_groups_from_drive_folders.py or
init_google_groups_from_gather_gdrive_config.py against them.

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
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from google_setup.init_google_groups_from_drive_folders import (
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
from util.google_group_utils import get_credentials

_LOG_FILE = Path("find_drive_folders_log.csv")
_SCREENSHOT_DIR = Path("find_drive_folders_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


# ── Drive traversal ───────────────────────────────────────────────────────────

def walk_drive_folders(drive_service, drive_id: str) -> list[dict]:
    """Breadth-first walk of every folder in the Shared Drive.

    Returns a flat list of {"id", "name", "path"} dicts, where "path" is the
    list of ancestor folder names (not including the drive root) leading to
    and including this folder.
    """
    folders: list[dict] = []
    queue = deque([(drive_id, [])])

    while queue:
        parent_id, parent_path = queue.popleft()
        page_token = None
        while True:
            resp = drive_service.files().list(
                corpora="drive",
                driveId=drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                q=f"mimeType='application/vnd.google-apps.folder' and "
                  f"'{parent_id}' in parents and trashed=false",
                fields="nextPageToken,files(id,name)",
                pageToken=page_token,
            ).execute()
            for f in resp.get("files", []):
                path = parent_path + [f["name"]]
                folders.append({"id": f["id"], "name": f["name"], "path": path})
                queue.append((f["id"], path))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    return folders


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


# ── Main ──────────────────────────────────────────────────────────────────────

def main(base_url: str, email: str, password: str, drive_id: str, credentials_path: str):
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

        browser.close()

    log("INFO", "filter", f"{len(unlisted)} group(s) have no mailing list (excluding Availability=Everybody)")

    log("INFO", "walk_drive", f"Walking folder tree of Shared Drive {drive_id}…")
    folders = walk_drive_folders(drive_service, drive_id)
    log("INFO", "walk_drive", f"{len(folders)} folder(s) found")

    print()
    for group in unlisted:
        matches = find_matching_folders(group.name, folders)
        if not matches:
            print(f"[NO MATCH]  '{group.name}'")
            continue
        print(f"[{len(matches)} MATCH(ES)]  '{group.name}'")
        for f in matches:
            path_str = " / ".join(f["path"])
            tag = "" if f["match_kind"] == "strict" else " [weak: shared term only]"
            print(f"    {path_str}   ({f['id']}){tag}")

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
    args = parser.parse_args()

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    email, password = load_credentials()
    main(args.base_url, email, password, args.drive_id, args.credentials)


if __name__ == "__main__":
    cli()
