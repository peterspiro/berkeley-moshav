#!/usr/bin/env python3
"""
Delete all Mailman email lists from every group in a Gather instance.

For each group that has a Mailman list, navigates to its edit page,
checks "Delete this list?", saves, and confirms the modal.

Usage:
    python delete_mailman_lists.py
    python delete_mailman_lists.py --dry-run
    python delete_mailman_lists.py -u https://other.gather.coop
"""

import argparse
import re
import sys

from playwright.sync_api import sync_playwright

from credentials import load_credentials
from gather_utils import (
    fetch_all_gather_groups,
    launch_browser,
    log,
    login,
    screenshot,
)

DEFAULT_BASE_URL = "https://berkeley-moshav.gather.coop"


def delete_mailman_list(page, base_url: str, group_id: str, group_name: str, dry_run: bool) -> bool:
    """Delete the Mailman list for one group. Returns True on success or if no list exists."""
    try:
        page.goto(f"{base_url}/groups/{group_id}/edit", wait_until="networkidle")

        delete_cb = page.locator(
            'input[type="checkbox"][name*="mailman_list_attributes"][name*="[_destroy]"]'
        )
        if delete_cb.count() == 0:
            log("INFO", "delete_list", f"{group_name} (id={group_id}): no Mailman list, skipping")
            return True

        name_el = page.locator(
            'input[name*="mailman_list_attributes"][name*="[name]"]'
        )
        list_name = name_el.first.input_value().strip() if name_el.count() > 0 else "(unknown)"

        if not list_name:
            log("INFO", "delete_list", f"{group_name} (id={group_id}): list field empty, skipping")
            return True

        domain_el = page.locator(
            'select[name*="mailman_list_attributes"][name*="[domain_id]"]'
        )
        if domain_el.count() > 0:
            selected_opt = domain_el.first.locator("option:checked")
            domain_text = selected_opt.inner_text().strip() if selected_opt.count() > 0 else ""
            if "gather.coop" not in domain_text.lower():
                log("INFO", "delete_list",
                    f"{group_name} (id={group_id}): domain '{domain_text}' is not gather.coop, skipping")
                return True

        if dry_run:
            log("DRY-RUN", "delete_list", f"{group_name} (id={group_id}): would delete list '{list_name}'")
            return True

        if not delete_cb.first.is_checked():
            page.evaluate(
                "(sel) => { const el = document.querySelector(sel); if (el) el.checked = true; }",
                'input[type="checkbox"][name*="mailman_list_attributes"][name*="[_destroy]"]',
            )

        page.locator('input[name="commit"]').click()

        page.wait_for_selector(
            'text="Are you sure you want to delete the email list?"',
            timeout=10_000,
        )
        page.locator('button:has-text("OK"), input[value="OK"]').first.click()
        page.wait_for_load_state("networkidle")

        if "groups" not in page.url or "edit" in page.url:
            screenshot(page, f"delete_list_err_{group_id}")
            log("ERROR", "delete_list", f"{group_name}: unexpected URL after confirm: {page.url}")
            return False

        log("INFO", "delete_list", f"{group_name} (id={group_id}): deleted list '{list_name}'")
        return True

    except Exception as e:
        screenshot(page, f"delete_list_exc_{group_id}")
        log("ERROR", "delete_list", f"{group_name} (id={group_id}): {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Delete all Mailman email lists from a Gather instance",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-u", "--base-url", default=DEFAULT_BASE_URL, help="Gather base URL")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Log what would be deleted without making changes")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    email, password = load_credentials()

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page()

        try:
            login(page, base_url, email, password)
        except RuntimeError as e:
            print(f"Login failed: {e}", file=sys.stderr)
            browser.close()
            sys.exit(1)

        groups = fetch_all_gather_groups(page, base_url)
        log("INFO", "start", f"{len(groups)} groups found")

        deleted = skipped = failed = 0
        for group in groups:
            ok = delete_mailman_list(page, base_url, group.group_id, group.name, args.dry_run)
            if ok:
                deleted += 1
            else:
                failed += 1

        browser.close()

    log("INFO", "done", f"deleted={deleted} failed={failed}")


if __name__ == "__main__":
    main()
