#!/usr/bin/env python3
"""
Copies each group's parent from the old Circle Structure wiki page in
Gather into the Circle Structure sheet that replaces it (see
circle_agendas/structure.gs). Run it once, before running
setupCircleStructure (so the agenda docs never say their circle isn't
placed), or after.

It reads the page's tree, and writes each group's parent, by Gather group
ID, to "Circle structure placements.json" in admin@'s My Drive (see
util/circle_structure_placements.py). When setupCircleStructure fills in the
sheet, or within 10 minutes if it already has, the circle_agendas Apps
Script gives each group in the sheet whose Parent is still empty its
parent from there, if the rules allow it (the sheet's Problems column and
the Parent cells' notes say when they don't), and rewrites the Circle
Structure doc and the agenda docs' Parent Circle and Sub-Circles rows. A
group already given a parent in the sheet keeps it.

Rows without a Gather group ([Members] link), and the rows under them, are
listed and skipped.

Requires Gather credentials (see README.md) and Google sign-in as
admin@berkeleymoshav.org (see update_groups.py's
docstring).

Usage:
    python seed_circle_structure.py -n
    python seed_circle_structure.py
"""

import argparse
import os
import sys
from pathlib import Path

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from util.circle_structure_placements import PLACEMENTS_FILE, add_placements
from util.credentials import load_credentials
from util.gather_utils import close_log, configure, init_log, launch_browser, log, login
from util.google_group_utils import DEFAULT_CLIENT_SECRETS_PATH, get_credentials
from util.hierarchy_wiki import HierarchyNode, fetch_hierarchy_page_content, iter_nodes, parse_hierarchy
from util.meeting_rooms import check_calendar_owner

_LOG_FILE = Path("debug/seed_circle_structure_log.csv")
_SCREENSHOT_DIR = Path("debug/seed_circle_structure_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


def placements_from_tree(root: HierarchyNode) -> tuple[dict[str, str], list[str]]:
    """({group ID: its parent's group ID}, for every row but the root whose
    row and parent's row both name a Gather group; [names of the rows
    skipped because one of them doesn't])."""
    placements: dict[str, str] = {}
    skipped: list[str] = []
    for node in iter_nodes(root):
        if node is root:
            continue
        if node.group_id and node.parent.group_id:
            placements[node.group_id] = node.parent.group_id
        else:
            skipped.append(node.name)
    return placements, skipped


def main(base_url: str, dry_run: bool, credentials_path: str) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"base_url={base_url} dry_run={dry_run}")
    creds = get_credentials(credentials_path)
    drive_service = build("drive", "v3", credentials=creds)
    # The placements file must be in admin@'s My Drive, where the Apps
    # Script looks.
    check_calendar_owner(build("calendar", "v3", credentials=creds))

    email, password = load_credentials()
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
        root = parse_hierarchy(fetch_hierarchy_page_content(page, base_url))
        browser.close()

    placements, skipped = placements_from_tree(root)
    for name in skipped:
        print(f"Skipped '{name}': its row, or its parent's, has no [Members] link.")
    add_placements(drive_service, placements, dry_run)
    verb = "[dry-run] Would write" if dry_run else "Wrote"
    print(f"{verb} {len(placements)} parent(s) to '{PLACEMENTS_FILE}'. The circle_agendas "
          "Apps Script gives them to the groups in the Circle Structure sheet within 10 "
          "minutes; check its Problems column after.")
    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Copy each group's parent from the old Circle Structure wiki page into "
                    "the Circle Structure sheet",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-u", "--base-url", default="https://berkeley-moshav.gather.coop",
                        help="Gather base URL")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Show what would be written without writing it")
    parser.add_argument("-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
                        help="Path to OAuth client secrets JSON")
    args = parser.parse_args()
    if not os.path.exists(args.credentials):
        sys.exit(f"Error: credentials file not found: {args.credentials}")
    main(args.base_url, args.dry_run, args.credentials)


if __name__ == "__main__":
    cli()
