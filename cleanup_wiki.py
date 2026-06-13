#!/usr/bin/env python3
"""
One-off cleanup: removes wiki links from Gather group descriptions and
deletes per-circle wiki pages created by the old gather_groups.py.

Run once after switching to the version of gather_groups.py that no longer
inserts wiki links.

What it does:
  1. Scans every Gather group; for any group whose description ends with
       \\n.\\n<a href="...">Wiki</a>
     that suffix is stripped and the group is updated.
  2. Reads the circle spreadsheet to find expected per-circle wiki slugs
     (e.g. 'membership-wiki'), navigates to each, and deletes it.
     The main 'circle-hierarchy' wiki page is left untouched.
     Other wiki pages (not matching any circle slug) are also left untouched.

Usage:
    python cleanup_wiki.py
    python cleanup_wiki.py -n           # dry-run: log what would change
    python cleanup_wiki.py -u BASE_URL  # override Gather base URL
    python cleanup_wiki.py -s SHEET_URL # override spreadsheet
"""

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from credentials import load_credentials
from gather_utils import (
    _check_submit_errors,
    _fetch_group_detail,
    close_log,
    configure,
    fetch_all_gather_groups,
    fetch_sheet,
    init_log,
    launch_browser,
    log,
    login,
    screenshot,
)
from gather_groups import (
    DEFAULT_SHEET_URL,
    _circle_wiki_slug,
    _needs_wiki,
    parse_sheet,
)

_LOG_FILE = Path("cleanup_wiki_log.csv")
_SCREENSHOT_DIR = Path("cleanup_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)

# Matches the wiki link suffix written by the old gather_groups.py:
#   \n.\n<a href="...">Wiki</a>
# along with any surrounding whitespace.
_WIKI_LINK_RE = re.compile(
    r"\s*\n\s*\.\s*\n\s*<a\s[^>]*href=['\"][^'\"]*['\"][^>]*>\s*Wiki\s*</a>\s*$",
    re.IGNORECASE,
)


def _strip_wiki_link(description: str) -> str:
    """Remove the trailing wiki HTML link (and its separator) from a group description."""
    return _WIKI_LINK_RE.sub("", description).rstrip()


# ── Step 1: remove wiki links from group descriptions ─────────────────────────

def remove_wiki_links_from_groups(
    page, base_url: str, dry_run: bool
) -> dict[str, int]:
    stats = {"updated": 0, "skipped": 0, "failed": 0}
    gather_groups = fetch_all_gather_groups(page, base_url)
    log("INFO", "fetch_groups", f"{len(gather_groups)} groups found")

    for group in gather_groups:
        try:
            detail = _fetch_group_detail(page, base_url, group)
        except Exception as e:
            log("ERROR", "fetch_detail", group.name, str(e))
            stats["failed"] += 1
            continue

        desc = detail.description
        if not _WIKI_LINK_RE.search(desc):
            stats["skipped"] += 1
            continue

        stripped = _strip_wiki_link(desc)
        log("INFO", "strip_wiki_link", f"{group.name!r}: removing trailing wiki link")
        if dry_run:
            log("DRY-RUN", "update_group_desc", group.name)
            stats["updated"] += 1
            continue

        try:
            page.goto(
                f"{base_url}/groups/{group.group_id}/edit",
                wait_until="networkidle",
            )
            desc_el = page.locator('textarea[name="groups_group[description]"]')
            if desc_el.count() == 0:
                log("WARN", "update_group_desc", group.name,
                    "description textarea not found")
                stats["failed"] += 1
                continue
            desc_el.fill(stripped)
            page.locator('input[name="commit"]').click()
            page.wait_for_load_state("networkidle")
            err = _check_submit_errors(page)
            if err:
                screenshot(page, f"cleanup_err_{group.name[:20]}")
                log("ERROR", "update_group_desc", group.name, err[:200])
                stats["failed"] += 1
            else:
                log("INFO", "update_group_desc", f"Updated: {group.name!r}")
                stats["updated"] += 1
        except Exception as e:
            screenshot(page, f"cleanup_exc_{group.name[:20]}")
            log("ERROR", "update_group_desc", group.name, str(e))
            stats["failed"] += 1

    return stats


# ── Step 2: delete per-circle wiki pages ──────────────────────────────────────

def fetch_all_wiki_slugs(page, base_url: str) -> list[tuple[str, str]]:
    """Scrape /wiki/all and return (slug, title) for every wiki page."""
    page.goto(f"{base_url}/wiki/all", wait_until="networkidle")
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in page.locator('a[href*="/wiki/"]').all():
        href = link.get_attribute("href") or ""
        m = re.match(r"^/wiki/([^/#?]+)$", href)
        if not m:
            continue
        slug = m.group(1)
        if slug in ("all", "new") or slug in seen:
            continue
        title = link.inner_text().strip()
        if title:
            seen.add(slug)
            results.append((slug, title))
    return results


def _delete_wiki_page(
    page, base_url: str, slug: str, title: str, dry_run: bool
) -> bool:
    """Delete the wiki page at /wiki/{slug}. Returns True if deleted or absent."""
    try:
        page.goto(f"{base_url}/wiki/{slug}", wait_until="networkidle")
        page_title = page.title()
        exists = (
            "Exception" not in page_title
            and "Error" not in page_title
            and "404" not in page_title
            and page.locator("h1").count() > 0
        )
        if not exists:
            log("INFO", "delete_wiki", f"'{title}' not found, nothing to do")
            return True

        if dry_run:
            log("DRY-RUN", "delete_wiki", f"would delete: '{title}' (/wiki/{slug})")
            return True

        # Gather's Rails UI places a delete link on the page (data-method="delete")
        # or a button inside a form.  Try common selectors in order.
        delete_sel = (
            'a[data-method="delete"]',
            'a[rel="nofollow"][data-method="delete"]',
            'input[name="_method"][value="delete"] ~ input[type="submit"]',
        )
        clicked = False
        for sel in delete_sel:
            el = page.locator(sel)
            if el.count() > 0:
                # Handle any confirm() dialog that may appear
                page.once("dialog", lambda d: d.accept())
                el.first.click()
                page.wait_for_load_state("networkidle")
                clicked = True
                break

        if not clicked:
            # Fall back: look for a link whose text is "Delete"
            delete_link = page.locator('a:has-text("Delete")').first
            if delete_link.count() > 0:
                page.once("dialog", lambda d: d.accept())
                delete_link.click()
                page.wait_for_load_state("networkidle")
                clicked = True

        if not clicked:
            log("WARN", "delete_wiki",
                f"'{title}': no delete control found on /wiki/{slug}")
            screenshot(page, f"delete_wiki_noctrl_{slug[:20]}")
            return False

        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"delete_wiki_err_{slug[:20]}")
            log("ERROR", "delete_wiki", f"'{title}'", err[:200])
            return False

        log("INFO", "delete_wiki", f"Deleted: '{title}'")
        return True

    except Exception as e:
        screenshot(page, f"delete_wiki_exc_{slug[:20]}")
        log("ERROR", "delete_wiki", f"'{title}'", str(e))
        return False


def delete_circle_wiki_pages(
    page, base_url: str, circles, all_wiki_slugs: list[tuple[str, str]], dry_run: bool
) -> dict[str, int]:
    stats = {"deleted": 0, "not_found": 0, "failed": 0}

    target_slugs: set[str] = {
        _circle_wiki_slug(circle.name)
        for circle in circles
        if _needs_wiki(circle)
    }

    skipped = [(slug, title) for slug, title in all_wiki_slugs if slug not in target_slugs]
    if skipped:
        log("INFO", "wiki_kept", f"{len(skipped)} wiki page(s) will NOT be deleted:")
        for slug, title in sorted(skipped, key=lambda x: x[1].lower()):
            log("INFO", "wiki_kept", f'  /wiki/{slug}  “{title}”')

    for circle in circles:
        if not _needs_wiki(circle):
            continue
        slug = _circle_wiki_slug(circle.name)
        title = f"{circle.name} Wiki"
        ok = _delete_wiki_page(page, base_url, slug, title, dry_run)
        if ok:
            stats["deleted"] += 1
        else:
            stats["failed"] += 1
    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main(
    base_url: str,
    email: str,
    password: str,
    sheet_url: str = DEFAULT_SHEET_URL,
    dry_run: bool = False,
):
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"base_url={base_url} dry_run={dry_run}")

    csv_text = fetch_sheet(sheet_url)
    circles = parse_sheet(csv_text)
    log("INFO", "parse_sheet", f"{len(circles)} circles parsed")

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

        log("INFO", "step1", "Removing wiki links from group descriptions")
        group_stats = remove_wiki_links_from_groups(page, base_url, dry_run)
        log("INFO", "step1_done", str(group_stats))

        log("INFO", "step2", "Deleting per-circle wiki pages")
        all_wiki_slugs = fetch_all_wiki_slugs(page, base_url)
        log("INFO", "fetch_wikis", f"{len(all_wiki_slugs)} wiki pages found")
        wiki_stats = delete_circle_wiki_pages(page, base_url, circles, all_wiki_slugs, dry_run)
        log("INFO", "step2_done", str(wiki_stats))

        browser.close()

    log("INFO", "done", f"groups={group_stats} wikis={wiki_stats}")
    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Remove wiki links from group descriptions and delete circle wiki pages",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-s", "--sheet-url", default=DEFAULT_SHEET_URL,
        help="Google Sheets URL or local CSV path",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Log what would change without making any changes",
    )
    args = parser.parse_args()
    email, password = load_credentials()
    main(args.base_url, email, password, args.sheet_url, args.dry_run)


if __name__ == "__main__":
    cli()
