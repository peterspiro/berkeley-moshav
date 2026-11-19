#!/usr/bin/env python3
"""
Makes a Gather member a super admin of the Google Workspace, with an
account of their own: a Cloud Identity Free user (no Gmail mailbox) whose
mail goes to their own address.

It lists the adults in the Gather directory (only those whose name
contains -m, if given) and asks which one. Then, for that member:

  1. Creates the account, named after their first name (`alex@`; -a picks
     another name), with a temporary password that must be changed at the
     first sign-in, their own email address (their Gather email, or else
     their Google Email) as its recovery email, their Gather phone as its
     recovery phone, and their Gather ID, so later runs know it's theirs.
     If the account exists already and is theirs, it only brings the
     recovery email and phone up to date.
  2. Makes the account a super admin.
  3. Removes any Google Workspace license Google gave the account
     automatically, so it stays a Cloud Identity Free user (-k keeps it).
  4. Adds the account to admin-forwarding@, which gets the filtered copies
     of admin@'s mail.
  5. Prints the Default routing rule to add in the Admin console, which
     sends the account's mail to the member's own address: Google has no
     API for routing rules. It waits while you add it.
  6. Prints the temporary password, to give the member yourself (in person,
     or by phone or text), and opens a draft email to their own address
     telling them how to sign in, which leaves the password out.

Running it again for the same member is safe: each step that's done
already is skipped (it never changes the password of an existing account).

Requires Google API access, signed in as admin@ (see update_groups.py),
with the Licensing API enabled too; it asks you to sign in again the first
time, for permission to manage users. Also requires your Gather login in
~/.gather.

Usage:
    python add_super_admin.py -n
    python add_super_admin.py -m Dana
    python add_super_admin.py -m Dana -a dana.smith
"""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from playwright.sync_api import sync_playwright

from util.credentials import load_credentials
from util.gather_utils import (GatherUser, close_log, fetch_all_gather_users, init_log,
                               launch_browser, log, login)
from util.google_group_utils import (DEFAULT_CLIENT_SECRETS_PATH, DOMAIN, SCOPES,
                                     get_credentials, list_group_members)
from util.mail_draft import open_draft
from util.super_admins import (FORWARDING_GROUP, WORKSPACE_PRODUCT, candidates,
                               default_local_part, gather_id_of, new_user_body,
                               personal_address, recovery_changes, recovery_phone,
                               routing_rule_steps, temporary_password, welcome_email)

BASE_URL = "https://berkeley-moshav.gather.coop"
ADMIN_SCOPES = SCOPES + [
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/apps.licensing",
]
# How long Google may take to give a new account a license automatically.
LICENSE_WAIT_SECONDS = 10


def fetch_users() -> list[GatherUser]:
    email, password = load_credentials()
    init_log()
    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page()
        try:
            login(page, BASE_URL, email, password)
            return fetch_all_gather_users(page, BASE_URL)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            sys.exit(f"Error: couldn't log in to Gather: {e}")
        finally:
            close_log()
            browser.close()


def choose_member(members: list[GatherUser], ask=input) -> GatherUser:
    """Ask which of `members` to make an admin."""
    if not members:
        sys.exit("No member in the Gather directory matches.")
    for i, u in enumerate(members, start=1):
        print(f"  {i}. {u.full_name}")
    while True:
        choice = ask(f"Make which member a super admin? [1-{len(members)}, or q to quit]: ").strip()
        if choice.lower() == "q":
            sys.exit("Nothing changed.")
        if choice.isdigit() and 1 <= int(choice) <= len(members):
            return members[int(choice) - 1]
        print("  Invalid choice.")


def get_account(dir_service, address: str) -> dict | None:
    """The Workspace user at `address`, or None if there's none."""
    try:
        return dir_service.users().get(userKey=address, projection="full").execute()
    except HttpError as e:
        if e.resp.status == 404:
            return None
        raise


def ensure_account(dir_service, user: GatherUser, address: str, recovery_email: str,
                   phone: str, org_unit: str, dry_run: bool) -> tuple[dict, str]:
    """Create the member's account, or bring an existing one's recovery
    email and phone up to date. Returns (account, temporary password, or ""
    if the account existed)."""
    account = get_account(dir_service, address)
    if account is None:
        password = temporary_password()
        print(f"{'Would create' if dry_run else 'Creating'} {address} for {user.full_name}.")
        if dry_run:
            return {"primaryEmail": address, "isAdmin": False}, password
        body = new_user_body(user, address, password, recovery_email, phone, org_unit)
        try:
            return dir_service.users().insert(body=body).execute(), password
        except HttpError as e:
            if e.resp.status == 409:
                sys.exit(f"Error: {address} is already in use (by a group or an alias?). "
                         f"Choose another name with -a.")
            raise

    owner = gather_id_of(account)
    if owner != user.user_id:
        whose = f"Gather member {owner}'s" if owner else "someone else's (it has no Gather ID)"
        sys.exit(f"Error: {address} exists already and is {whose}. Choose another name with -a.")
    print(f"{address} exists already.")
    changes = recovery_changes(account, recovery_email, phone)
    if changes:
        print(f"  {'Would update' if dry_run else 'Updating'} its {', '.join(changes)}.")
        if not dry_run:
            account = dir_service.users().patch(userKey=address, body=changes).execute()
    return account, ""


def ensure_super_admin(dir_service, account: dict, dry_run: bool) -> None:
    address = account["primaryEmail"]
    if account.get("isAdmin"):
        print(f"{address} is a super admin already.")
        return
    print(f"{'Would make' if dry_run else 'Making'} {address} a super admin.")
    if not dry_run:
        dir_service.users().makeAdmin(userKey=address, body={"status": True}).execute()


def workspace_licenses(licensing, customer_id: str, address: str) -> list[dict]:
    """The Workspace licenses assigned to `address`."""
    found, token = [], None
    while True:
        resp = licensing.licenseAssignments().listForProduct(
            productId=WORKSPACE_PRODUCT, customerId=customer_id, maxResults=1000, pageToken=token,
        ).execute()
        found += [a for a in resp.get("items", []) if a.get("userId", "").lower() == address.lower()]
        token = resp.get("nextPageToken")
        if not token:
            return found


def remove_workspace_licenses(licensing, account: dict, created: bool, dry_run: bool,
                              wait: float = LICENSE_WAIT_SECONDS) -> None:
    """Remove any Workspace license from the account, leaving it a Cloud
    Identity Free user."""
    address = account["primaryEmail"]
    if not account.get("customerId"):  # an account a dry run would create
        print(f"Would remove any Workspace license Google gives {address}.")
        return
    if created:
        time.sleep(wait)
    try:
        licenses = workspace_licenses(licensing, account["customerId"], address)
        for lic in licenses:
            print(f"{'Would remove' if dry_run else 'Removing'} its license "
                  f"{lic.get('skuName') or lic['skuId']}.")
            if not dry_run:
                licensing.licenseAssignments().delete(
                    productId=lic["productId"], skuId=lic["skuId"], userId=address).execute()
    except HttpError as e:
        print(f"Warning: couldn't check {address}'s licenses ({e.resp.status}: {e._get_reason()}).\n"
              f"  In the Admin console, check it has no Google Workspace license (Users → "
              f"{address} → Licenses). To let the script check, enable the Enterprise License "
              f"Manager API in the Google Cloud project of client_secret.json.")
        return
    if not licenses:
        print(f"{address} has no Workspace license.")


def ensure_forwarding_member(dir_service, address: str, dry_run: bool) -> None:
    members = {m["email"].lower() for m in list_group_members(dir_service, FORWARDING_GROUP)}
    if address.lower() in members:
        print(f"{address} is on admin-forwarding@ already.")
        return
    print(f"{'Would add' if dry_run else 'Adding'} {address} to admin-forwarding@.")
    if not dry_run:
        dir_service.members().insert(
            groupKey=FORWARDING_GROUP, body={"email": address, "role": "MEMBER"}).execute()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-m", "--member", default="",
                        help="list only the members whose name contains this")
    parser.add_argument("-a", "--address",
                        help="the new account's name, before the @ (default: the member's first name)")
    parser.add_argument("-e", "--personal-email",
                        help="the member's own address, if not the one in Gather")
    parser.add_argument("-o", "--org-unit", default="/",
                        help="the organizational unit for a new account (default: the top one)")
    parser.add_argument("-k", "--keep-license", action="store_true",
                        help="don't remove a Workspace license Google gives the account")
    parser.add_argument("-n", "--dry-run", action="store_true", help="change nothing")
    parser.add_argument("-c", "--client-secrets", default=str(DEFAULT_CLIENT_SECRETS_PATH),
                        help="OAuth client secrets file (default: client_secret.json)")
    args = parser.parse_args()

    if not Path(args.client_secrets).exists():
        sys.exit(f"Error: {args.client_secrets} not found (see README.md, Setup).")

    user = choose_member(candidates(fetch_users(), date.today(), args.member))
    personal = args.personal_email or personal_address(user)
    if not personal:
        sys.exit(f"Error: {user.full_name} has no email address outside the Workspace in Gather; "
                 f"give one with -e.")
    phone = recovery_phone(user.phone)
    if not phone:
        print(f"Warning: {user.full_name}'s phone in Gather ({user.phone or 'none'}) can't be "
              f"used; the account gets no recovery phone.")
    local = (args.address or default_local_part(user)).split("@")[0].lower()
    if not local:
        sys.exit("Error: give the account's name with -a.")
    address = f"{local}@{DOMAIN}"

    creds = get_credentials(args.client_secrets, ADMIN_SCOPES)
    dir_service = build("admin", "directory_v1", credentials=creds)
    licensing = build("licensing", "v1", credentials=creds)

    account, password = ensure_account(dir_service, user, address, personal, phone,
                                       args.org_unit, args.dry_run)
    ensure_super_admin(dir_service, account, args.dry_run)
    if not args.keep_license:
        remove_workspace_licenses(licensing, account, bool(password), args.dry_run)
    ensure_forwarding_member(dir_service, address, args.dry_run)

    print(f"\nIf there's no Default routing rule for {address} yet, add one:")
    print("\n".join(routing_rule_steps(address, personal)))
    if args.dry_run:
        return
    input("Press Enter once the rule is saved (or if it was there already): ")

    subject, body = welcome_email(user, address)
    if password:
        print(f"\nThe temporary password is: {password}\n"
              f"Give it to {user.first_name} yourself (in person, or by phone or text), "
              f"not in the email. It must be changed at the first sign-in.")
    else:
        print(f"\n{address} existed already, so its password wasn't changed. If "
              f"{user.first_name} needs a new one, reset it in the Admin console.")
    if not open_draft(personal, subject, body):
        print(f"\nCouldn't open a mail program. Send this to {personal}:\n\n"
              f"Subject: {subject}\n\n{body}")
    else:
        print(f"Opened a draft email to {personal}, telling them how to sign in.")


if __name__ == "__main__":
    main()
