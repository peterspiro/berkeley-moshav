#!/usr/bin/env python3
"""
Creates a wiki page for a single Gather group and links it into the Circle
Hierarchy.

The group is identified by a unique prefix of its name (case-insensitive).
The script:

  1. Resolves the group from the prefix (errors if the prefix matches zero
     or more than one group).
  2. Creates the group's wiki page if it doesn't already exist — titled
     "<Group Name> Wiki" at slug "<name-slug>-wiki". Existing pages are left
     untouched.
  3. Adds a [Wiki] link to the group's row in the Circle Hierarchy page,
     positioned just after the [Members] and [Documents] links and before
     any other, unmanaged content on that row. Errors if the group isn't
     already in the hierarchy (run update_groups_in_google_and_hierarchy.py
     first to add it).

Only Gather access is required (no Google APIs).

Usage:
    python add_group_wiki_page.py "Landscape"
    python add_group_wiki_page.py membership -n
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from util.credentials import load_credentials
from util.gather_utils import (
    GatherGroup,
    close_log,
    configure,
    fetch_all_gather_groups,
    init_log,
    launch_browser,
    log,
    login,
)
from util.hierarchy_wiki import (
    HierarchyNode,
    ensure_hierarchy_page,
    fetch_hierarchy_page_content,
    iter_nodes,
    parse_hierarchy,
    render_hierarchy,
)
from util.wiki_utils import ensure_named_wiki_page, wiki_slug, wiki_title

_LOG_FILE = Path("debug/add_group_wiki_page_log.csv")
_SCREENSHOT_DIR = Path("debug/add_group_wiki_page_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


def resolve_group_by_prefix(groups: list[GatherGroup], prefix: str) -> GatherGroup:
    """Return the single group whose name starts with `prefix`
    (case-insensitive). Raises ValueError if zero or more than one match."""
    p = prefix.strip().casefold()
    matches = [g for g in groups if g.name.casefold().startswith(p)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"No Gather group name starts with {prefix!r}.")
    names = ", ".join(repr(g.name) for g in matches)
    raise ValueError(f"Prefix {prefix!r} matches multiple groups: {names}")


def find_node_by_group_id(root: HierarchyNode, group_id: str) -> Optional[HierarchyNode]:
    """Return the hierarchy node linked to `group_id` (via its [Members]
    URL), or None if no row references that group."""
    for node in iter_nodes(root):
        if node.group_id == group_id:
            return node
    return None


def main(prefix: str, base_url: str, dry_run: bool) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"prefix={prefix!r} dry_run={dry_run}")

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

        groups = fetch_all_gather_groups(page, base_url)
        log("INFO", "fetch_groups", f"{len(groups)} group(s) found")

        try:
            group = resolve_group_by_prefix(groups, prefix)
        except ValueError as e:
            browser.close()
            close_log()
            sys.exit(f"Error: {e}")

        print(f"Resolved '{prefix}' → '{group.name}' (id={group.group_id})")

        content = fetch_hierarchy_page_content(page, base_url)
        root = parse_hierarchy(content)

        node = find_node_by_group_id(root, group.group_id)
        if node is None:
            browser.close()
            close_log()
            sys.exit(
                f"Error: '{group.name}' (id={group.group_id}) isn't in the Circle "
                "Hierarchy yet. Run update_groups_in_google_and_hierarchy.py first "
                "to add it, then re-run this script."
            )

        title = wiki_title(group.name)
        slug = wiki_slug(group.name)
        wiki_url = f"/wiki/{slug}"

        ok = ensure_named_wiki_page(page, base_url, title, slug, "", dry_run)
        if not ok:
            browser.close()
            close_log()
            sys.exit(f"Error: failed to create wiki page '{title}' — see log.")
        print(f"Wiki page '{title}' ready at {wiki_url}")

        if node.wiki_url == wiki_url:
            print(f"'{group.name}' already links to its wiki page; nothing to change.")
            log("INFO", "wiki_link", f"{group.name}: already linked to {wiki_url}")
            browser.close()
            close_log()
            return

        action = "Updating" if node.wiki_url else "Adding"
        print(f"{action} [Wiki] link for '{group.name}' → {wiki_url}")
        log("INFO", "wiki_link", f"{group.name}: {node.wiki_url!r} -> {wiki_url!r}")
        node.wiki_url = wiki_url

        new_content = render_hierarchy(root)
        wrote = ensure_hierarchy_page(page, base_url, new_content, dry_run)
        if not wrote:
            log("ERROR", "hierarchy", "Failed to write hierarchy wiki page — see log")
            browser.close()
            close_log()
            sys.exit("Error: failed to update the Circle Hierarchy page — see log.")

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Create a Gather group's wiki page and link it into the Circle "
                    "Hierarchy, identifying the group by a unique prefix of its name.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "prefix",
        help="A unique prefix of the target group's name (case-insensitive)",
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Report what would change without creating the page or editing the hierarchy",
    )
    args = parser.parse_args()

    main(args.prefix, args.base_url, args.dry_run)


if __name__ == "__main__":
    cli()
