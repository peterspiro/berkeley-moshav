#!/usr/bin/env python3
"""
Creates a wiki page for a single Gather group.

The group is identified by a unique prefix of its name (case-insensitive).
The script:

  1. Resolves the group from the prefix (errors if the prefix matches zero
     or more than one group).
  2. Creates the group's wiki page if it doesn't already exist — titled
     "<Group Name> Wiki" at slug "<name-slug>-wiki". Existing pages are left
     untouched.

Only Gather access is required (no Google APIs).

Usage:
    python add_group_wiki_page.py "Landscape"
    python add_group_wiki_page.py membership -n
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from util.credentials import load_credentials
from util.gather_utils import (
    close_log,
    configure,
    fetch_all_gather_groups,
    init_log,
    launch_browser,
    log,
    login,
    resolve_group_by_prefix,
)
from util.wiki_utils import ensure_named_wiki_page, wiki_slug, wiki_title

_LOG_FILE = Path("debug/add_group_wiki_page_log.csv")
_SCREENSHOT_DIR = Path("debug/add_group_wiki_page_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


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

        title = wiki_title(group.name)
        slug = wiki_slug(group.name)
        wiki_url = f"/wiki/{slug}"

        ok = ensure_named_wiki_page(page, base_url, title, slug, "", dry_run)
        if not ok:
            browser.close()
            close_log()
            sys.exit(f"Error: failed to create wiki page '{title}' — see log.")
        print(f"Wiki page '{title}' ready at {wiki_url}")

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Create a Gather group's wiki page, identifying the group by a "
                    "unique prefix of its name.",
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
        help="Report what would change without creating the page",
    )
    args = parser.parse_args()

    main(args.prefix, args.base_url, args.dry_run)


if __name__ == "__main__":
    cli()
