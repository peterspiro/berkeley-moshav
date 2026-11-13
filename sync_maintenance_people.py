#!/usr/bin/env python3
"""
Copies the community's members from Gather into the maintenance tracker's
People table in Airtable.

Each adult in the Gather directory gets a row in People: their name, email
addresses (their Gather email, and their Google Email if it's different),
phone (mobile, or else home), and their unit, as a link to its row in
Locations. Run it when members join, move, or leave; it adds new people
and updates changed ones, matching them by Gather ID.

Nobody is deleted, since requests link to people: someone whose departure
date has passed, or who's no longer in the directory, is marked Moved out.
The Circle member and Coordinator boxes, and rows added in Airtable by
hand for people outside the community, are left alone. A member whose unit
number has no row in Locations is listed, and copied without one; someone
whose unit in Gather isn't a number (such as "Consultants") is copied
without one, and not listed.

It works on the test base, "Building Maintenance (test)", unless given
--real (see maintenance_base.py). The test base then holds real members'
contact details, which Claude Code cloud sessions can read; run it there
with -n if that's not wanted.

Requires your Gather login in ~/.gather and the Airtable token in
airtable_credentials (see README.md).

Usage:
    python sync_maintenance_people.py -n
    python sync_maintenance_people.py
    python sync_maintenance_people.py --real
"""

import argparse
import sys
from datetime import date

from playwright.sync_api import sync_playwright

from maintenance_base import REAL_BASE, TEST_BASE
from util.airtable import Airtable, AirtableError
from util.credentials import load_airtable_token, load_credentials
from util.gather_utils import close_log, fetch_all_gather_users, init_log, launch_browser, log, login
from util.maintenance_people import KEY, changes, people_rows


def sync(client, base: str, users, dry_run: bool, today: date, out=print) -> None:
    """Bring People in `base` up to date with the directory's `users`."""
    locations = {r["fields"]["Name"]: r["id"]
                 for r in client.list_records(base, "Locations", ["Name"]) if r["fields"].get("Name")}
    rows, warnings = people_rows(users, locations, today)
    to_write, lines = changes(rows, client.list_records(base, "People"))
    for line in lines + warnings:
        out(line)
    out(f"{len(rows)} adult(s) in the directory; {len(to_write)} row(s) "
        f"{'would be' if dry_run else ''} written.".replace("  ", " "))
    if to_write and not dry_run:
        client.upsert_records(base, "People", KEY, to_write)


def fetch_users(base_url: str):
    email, password = load_credentials()
    init_log()
    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page()
        try:
            login(page, base_url, email, password)
            return fetch_all_gather_users(page, base_url)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            sys.exit(f"Error: couldn't log in to Gather: {e}")
        finally:
            close_log()
            browser.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--real", action="store_true",
                        help=f"work on {REAL_BASE!r} instead of {TEST_BASE!r}")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="show what would change, and change nothing")
    parser.add_argument("-u", "--base-url", default="https://berkeley-moshav.gather.coop",
                        help="Gather base URL")
    args = parser.parse_args(argv)

    base_name = REAL_BASE if args.real else TEST_BASE
    try:
        client = Airtable(load_airtable_token())
        base = client.find_base(base_name)
        print(f"Base: {base_name}")
        users = fetch_users(args.base_url.rstrip("/"))
        sync(client, base, users, args.dry_run, date.today())
    except AirtableError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
