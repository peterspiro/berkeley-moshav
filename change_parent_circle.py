#!/usr/bin/env python3
"""
Moves a circle or working group, with everything beneath it, to a
different parent circle on the Circle Structure wiki page, and updates the
agenda docs that name the circle's parent or sub-circles.

A group's parent is recorded only on the Circle Structure page (as the
entry it's nested under); its agenda doc has a copy of its parent's and
sub-circles' names, labelled on the doc when it was created, from which
the circle_agendas Apps Script writes About this circle.

Steps:
  1. Find the group's entry and the new parent's on the Circle Structure
     page, each by a unique prefix of its name (case-insensitive). Errors if
     either prefix matches no entry or more than one, if the group is the
     root, or if the new parent is the group itself or beneath it.
  2. Move the group's entry, with its sub-circles, under the new parent
     (alphabetically, as the page is always sorted), and save the page. If
     it's already there, the page is left as it is and the docs are still
     checked, so re-running after a failure finishes the job.
  3. Relabel the agenda docs of the group (its new parent) and of its old
     and new parents (their sub-circles) — see util/circle_agenda_docs.py.
     A built doc is also labelled for the Apps Script to rewrite its Parent
     Circle and Sub-Circles rows, which it does within 10 minutes. A group
     with no agenda doc, or no email list to find it by, is skipped.

Requires Google API access (Drive) — see
update_groups_in_google_and_hierarchy.py's docstring for setup.

Usage:
    python change_parent_circle.py Landscape -p Property
    python change_parent_circle.py "Tech Support" -p Operations -n
"""

import argparse
import os
import sys
from pathlib import Path

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from util.circle_agenda_docs import update_agenda_doc_structure
from util.credentials import load_credentials
from util.gather_utils import (
    close_log,
    configure,
    init_log,
    launch_browser,
    log,
    login,
)
from util.google_group_utils import DEFAULT_CLIENT_SECRETS_PATH, get_credentials
from util.hierarchy_wiki import (
    HierarchyNode,
    ensure_hierarchy_page,
    fetch_hierarchy_page_content,
    find_unique_node_by_prefix,
    is_descendant,
    iter_nodes,
    move_node,
    parse_hierarchy,
    render_hierarchy,
)

_LOG_FILE = Path("debug/change_parent_circle_log.csv")
_SCREENSHOT_DIR = Path("debug/change_parent_circle_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


def resolve_move(root: HierarchyNode, circle_prefix: str,
                 parent_prefix: str) -> tuple[HierarchyNode, HierarchyNode]:
    """The entries to move and to move it under, found by prefix. Raises
    ValueError if the move can't be made."""
    nodes = list(iter_nodes(root))
    node = find_unique_node_by_prefix(nodes, circle_prefix)
    new_parent = find_unique_node_by_prefix(nodes, parent_prefix)
    if node.parent is None:
        raise ValueError(f"'{node.name}' is the top of the Circle Structure; it has no parent")
    if is_descendant(new_parent, node):
        where = "itself" if new_parent is node else "beneath it"
        raise ValueError(f"'{new_parent.name}' is {where}, so can't be its parent")
    return node, new_parent


def main(circle_prefix: str, parent_prefix: str, base_url: str, dry_run: bool,
         credentials_path: str) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"circle={circle_prefix!r} parent={parent_prefix!r} dry_run={dry_run}")

    email, password = load_credentials()
    drive_service = build("drive", "v3", credentials=get_credentials(credentials_path))

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page()

        def fail(message: str) -> None:
            browser.close()
            close_log()
            sys.exit(f"Error: {message}")

        try:
            login(page, base_url, email, password)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            fail(str(e))

        root = parse_hierarchy(fetch_hierarchy_page_content(page, base_url))
        try:
            node, new_parent = resolve_move(root, circle_prefix, parent_prefix)
        except ValueError as e:
            fail(str(e))
        old_parent = node.parent

        if old_parent is new_parent:
            print(f"'{node.name}' is already under '{new_parent.name}' on the Circle "
                  "Structure page; checking the agenda docs.")
        else:
            move_node(node, new_parent)
            log("INFO", "move", f"{node.name}: {old_parent.name!r} -> {new_parent.name!r}")
            if not ensure_hierarchy_page(page, base_url, render_hierarchy(root), dry_run):
                fail("failed to update the Circle Structure page — see log.")
            prefix = "[dry-run] Would move" if dry_run else "Moved"
            print(f"{prefix} '{node.name}' from under '{old_parent.name}' to under "
                  f"'{new_parent.name}' on the Circle Structure page.")

        # The moved group's doc names its parent; each parent's names its
        # sub-circles. (When nothing moved, old_parent is new_parent.)
        doc_nodes = [node, old_parent] + ([new_parent] if new_parent is not old_parent else [])
        for doc_node in doc_nodes:
            update_agenda_doc_structure(drive_service, page, base_url, doc_node, dry_run)

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Move a circle or working group (with its sub-circles) to a different "
                    "parent circle on the Circle Structure page, and update the agenda docs "
                    "that name its parent or sub-circles.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "circle", nargs="+",
        help="A prefix uniquely identifying the group's entry on the Circle Structure page "
             "(case-insensitive; may be several unquoted words)",
    )
    parser.add_argument(
        "-p", "--parent", required=True,
        help="A prefix uniquely identifying the new parent circle's entry",
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Report what would change, without changing anything",
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

    main(" ".join(args.circle), args.parent, args.base_url, args.dry_run, args.credentials)


if __name__ == "__main__":
    cli()
