#!/usr/bin/env python3
"""
Truncates a Google Group's email address by dropping its trailing
"-circle"/"-work-group"/"-working-group" suffix, and switches the
corresponding Gather group's mailing list over to the shorter address.

Steps:
  1. Compute the truncated address from the given (full) group email —
     errors if it doesn't end with a recognized suffix.
  2. Validate: the original address is a real Google Group; the truncated
     address isn't already in use by any *other* group.
  3. Add the truncated address as an alias of the Google Group (the
     original address is left as the group's primary address — this only
     adds a second, shorter way to reach the same group).
  4. Find the Gather group whose mailing list currently points at the
     original address, delete that mailing list (Gather disables the
     name/domain fields once a list exists, so they can't be edited in
     place), and create a new one there using the truncated address's
     local part.

Requires Google API access (Admin Directory) — see
update_groups_in_google_and_hierarchy.py's docstring for setup.

Usage:
    python truncate_group_email.py care-circle@berkeleymoshav.org
    python truncate_group_email.py landscaping-working-group@berkeleymoshav.org -n
"""

import argparse
import os
import sys
from pathlib import Path

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from util.credentials import load_credentials
from util.gather_utils import (
    _fetch_group_detail,
    close_log,
    configure,
    destroy_gather_group_email_list,
    fetch_all_gather_groups,
    init_log,
    launch_browser,
    log,
    login,
    set_gather_group_email_list,
)
from util.google_group_utils import (
    DEFAULT_CLIENT_SECRETS_PATH,
    DOMAIN,
    add_group_alias,
    get_credentials,
    get_group_by_email,
    is_in_domain,
)

_LOG_FILE = Path("debug/truncate_group_email_log.csv")
_SCREENSHOT_DIR = Path("debug/truncate_group_email_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)

# Longest first, so e.g. "-working-group" is tried before any shorter
# suffix that might otherwise coincidentally match a substring of it.
SUFFIXES = ["-working-group", "-work-group", "-circle"]


def truncate_local_part(local: str) -> str | None:
    """Return local with a recognized suffix removed, or None if it
    doesn't end with one of SUFFIXES."""
    for suffix in SUFFIXES:
        if local.endswith(suffix):
            truncated = local[: -len(suffix)]
            return truncated or None
    return None


def find_gather_group_by_email(page, base_url: str, target_email: str) -> str | None:
    """Scan every Gather group for one whose mailing list currently
    resolves to target_email. Returns its group_id, or None."""
    target = target_email.casefold()
    for group in fetch_all_gather_groups(page, base_url):
        detail = _fetch_group_detail(page, base_url, group)
        if not detail.list_name:
            continue
        domain = detail.list_domain or DOMAIN
        if f"{detail.list_name}@{domain}".casefold() == target:
            return group.group_id
    return None


def main(
    original_email: str, base_url: str, email: str, password: str,
    dry_run: bool, credentials_path: str,
) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"original_email={original_email} dry_run={dry_run}")

    if not is_in_domain(original_email):
        sys.exit(f"Error: {original_email!r} is not a @{DOMAIN} address.")

    local, _, _ = original_email.partition("@")
    truncated_local = truncate_local_part(local)
    if not truncated_local:
        sys.exit(
            f"Error: {original_email!r} doesn't end with a recognized suffix "
            f"({', '.join(SUFFIXES)}) — nothing to truncate."
        )
    truncated_email = f"{truncated_local}@{DOMAIN}"

    creds = get_credentials(credentials_path)
    dir_service = build("admin", "directory_v1", credentials=creds)

    original_group = get_group_by_email(dir_service, original_email)
    if original_group is None:
        sys.exit(f"Error: no Google Group found with address {original_email!r}.")

    existing = get_group_by_email(dir_service, truncated_email)
    if existing is not None and existing["id"] != original_group["id"]:
        sys.exit(
            f"Error: {truncated_email!r} is already in use by a different "
            f"Google Group ({existing['email']!r}, id={existing['id']})."
        )

    print(f"'{original_email}' -> '{truncated_email}'")

    if dry_run:
        if existing is None:
            print(f"[dry-run] Would add '{truncated_email}' as an alias of '{original_email}'.")
        else:
            print(f"[dry-run] '{truncated_email}' is already an alias of '{original_email}'.")
    else:
        added = add_group_alias(dir_service, original_email, truncated_email)
        if added:
            print(f"Added '{truncated_email}' as an alias of '{original_email}'.")
            log("INFO", "add_alias", f"{original_email} -> +{truncated_email}")
        else:
            print(f"'{truncated_email}' is already an alias of '{original_email}'.")

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

        print("Searching Gather for the group using this mailing list…")
        group_id = find_gather_group_by_email(page, base_url, original_email)
        if group_id is None:
            log("WARN", "find_gather_group", original_email,
                "no Gather group currently uses this address as its mailing list")
            print(f"No Gather group found using '{original_email}' as its mailing list — "
                  "Google-side alias added, but nothing to change in Gather.")
            browser.close()
            close_log()
            return

        print(f"Found Gather group id={group_id}. Deleting its current mailing list…")
        ok = destroy_gather_group_email_list(page, base_url, group_id, dry_run)
        if not ok:
            browser.close()
            close_log()
            sys.exit("Error: failed to delete the existing mailing list — see log.")

        print(f"Setting mailing list to '{truncated_email}'…")
        set_gather_group_email_list(page, base_url, group_id, truncated_local, DOMAIN, dry_run)

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Truncate a Google Group's email address (drop its "
                    "-circle/-work-group/-working-group suffix) and switch the "
                    "corresponding Gather group's mailing list to it.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("email", help="The group's current, full email address")
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Validate and report what would change, without changing anything",
    )
    parser.add_argument(
        "-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
        help=f"Path to OAuth client secrets JSON (default: {DEFAULT_CLIENT_SECRETS_PATH})",
    )
    args = parser.parse_args()

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    email, password = load_credentials()
    main(args.email, args.base_url, email, password, args.dry_run, args.credentials)


if __name__ == "__main__":
    cli()
