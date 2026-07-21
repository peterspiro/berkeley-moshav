#!/usr/bin/env python3
"""
Changes the alias a Google Group's email address is reachable at, and
switches the corresponding Gather group's mailing list over to it.

By default the new alias is derived from the current address by dropping
its trailing "-circle"/"-work-group"/"-working-group" suffix. Pass
--new-alias to specify the new alias explicitly instead (e.g. to rename
to something unrelated to the old address).

Only berkeleymoshav.org addresses are supported — pass just the local
part (no "@domain") for both the current address and --new-alias; the
domain is always berkeleymoshav.org.

Steps:
  1. Determine the new local part — from --new-alias, or by truncating a
     recognized suffix off the current one (errors if neither applies).
  2. Validate: the original address is a real Google Group; the new
     address isn't already in use by any *other* group.
  3. Add the new address as an alias of the Google Group (the original
     address is left as the group's primary address — this only adds a
     second way to reach the same group).
  4. Find the Gather group whose mailing list currently points at the
     original address, delete that mailing list (checking "Delete this
     list?" and saving — Gather disables the name/domain fields once a
     list exists, so they can't be edited in place), and create a new one
     there using the new address's local part.

Requires Google API access (Admin Directory) — see
update_groups_in_google_and_hierarchy.py's docstring for setup.

Usage:
    python change_group_email_alias.py care-circle
    python change_group_email_alias.py landscaping-working-group -n
    python change_group_email_alias.py grounds-committee --new-alias landscaping
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
)

_LOG_FILE = Path("debug/change_group_email_alias_log.csv")
_SCREENSHOT_DIR = Path("debug/change_group_email_alias_screenshots")

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
    local_part: str, new_alias: str | None, base_url: str, email: str, password: str,
    dry_run: bool, credentials_path: str,
) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"local_part={local_part} new_alias={new_alias} dry_run={dry_run}")

    if "@" in local_part:
        sys.exit(
            f"Error: pass just the local part, not a full address ({local_part!r}) — "
            f"the domain is always @{DOMAIN}."
        )

    original_email = f"{local_part}@{DOMAIN}"

    if new_alias is not None:
        if "@" in new_alias:
            sys.exit(
                f"Error: --new-alias should be just the local part, not a full address "
                f"({new_alias!r}) — the domain is always @{DOMAIN}."
            )
        if new_alias == local_part:
            sys.exit(f"Error: --new-alias {new_alias!r} is the same as the current address.")
        new_local = new_alias
    else:
        new_local = truncate_local_part(local_part)
        if not new_local:
            sys.exit(
                f"Error: {local_part!r} doesn't end with a recognized suffix "
                f"({', '.join(SUFFIXES)}) — nothing to truncate. Use --new-alias to "
                "specify the new address explicitly."
            )
    new_email = f"{new_local}@{DOMAIN}"

    creds = get_credentials(credentials_path)
    dir_service = build("admin", "directory_v1", credentials=creds)

    original_group = get_group_by_email(dir_service, original_email)
    if original_group is None:
        sys.exit(f"Error: no Google Group found with address {original_email!r}.")

    existing = get_group_by_email(dir_service, new_email)
    if existing is not None and existing["id"] != original_group["id"]:
        sys.exit(
            f"Error: {new_email!r} is already in use by a different "
            f"Google Group ({existing['email']!r}, id={existing['id']})."
        )

    print(f"'{original_email}' -> '{new_email}'")

    if dry_run:
        if existing is None:
            print(f"[dry-run] Would add '{new_email}' as an alias of '{original_email}'.")
        else:
            print(f"[dry-run] '{new_email}' is already an alias of '{original_email}'.")
    else:
        added = add_group_alias(dir_service, original_email, new_email)
        if added:
            print(f"Added '{new_email}' as an alias of '{original_email}'.")
            log("INFO", "add_alias", f"{original_email} -> +{new_email}")
        else:
            print(f"'{new_email}' is already an alias of '{original_email}'.")

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

        print(f"Setting mailing list to '{new_email}'…")
        set_gather_group_email_list(page, base_url, group_id, new_local, DOMAIN, dry_run)

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Change the alias a Google Group's email address is reachable at "
                    "(by default dropping its -circle/-work-group/-working-group suffix, "
                    "or an explicit --new-alias) and switch the corresponding Gather "
                    "group's mailing list to it.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "local_part",
        help=f"The group's current email address, without the @{DOMAIN} domain",
    )
    parser.add_argument(
        "-a", "--new-alias", default=None,
        help=f"The new alias's local part (without the @{DOMAIN} domain). "
             "Default: the current address with its recognized suffix truncated.",
    )
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
    main(args.local_part, args.new_alias, args.base_url, email, password,
         args.dry_run, args.credentials)


if __name__ == "__main__":
    cli()
