"""
One-off setup script: reads Gather's Google Drive Settings (/gdrive/config)
and for each folder listed there:

  1. Adds the folder to FOLDER_IDS in folder_ids.gs if not already present.
  2. Creates the corresponding Google Group if it doesn't exist.
  3. Sets the group's "Who can post" to "Anyone on the web" if not already set.
  4. Edits the Gather group to set the Google Group email as its email list,
     check "All community members can send to list?", and submit the form.

Setup:
  1. Ensure ~/.gather contains your Gather admin credentials (email + password).
  3. In Google Cloud Console, create a Desktop OAuth 2.0 client and download
     the JSON as client_secret.json (or pass a different path via -c).
  4. Enable Admin SDK (Directory API) and Groups Settings API for the project.
  5. On first run the script prints an auth URL — paste it into a browser
     signed in as a Workspace super-admin.  The token is cached at
     ~/.google_setup_token.pkl for subsequent runs.
  Note: if you add new API scopes, delete ~/.google_setup_token.pkl so the
  token is refreshed with the updated scope set.
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from googleapiclient.discovery import build

from util.credentials import load_credentials
from util.gather_utils import (
    configure,
    init_log,
    launch_browser,
    log,
    login,
    screenshot,
    _check_submit_errors,
)
from util.google_group_utils import (
    DEFAULT_CLIENT_SECRETS_PATH,
    DOMAIN,
    REQUIRED_GROUP_SETTINGS,
    compute_group_settings_updates,
    ensure_group_exists,
    ensure_group_settings,
    get_credentials,
    group_display_name,
    group_email,
    group_exists,
    read_folder_ids,
    write_folder_ids,
)
from util.gdrive_config import scrape_gdrive_config

BASE_URL = "https://berkeley-moshav.gather.coop"

_LOG_FILE_PATH = __import__("pathlib").Path("debug/gdrive_groups_log.csv")
_SCREENSHOT_DIR = __import__("pathlib").Path("debug/gdrive_groups_screenshots")

configure(_LOG_FILE_PATH, _SCREENSHOT_DIR)


# ── Gather group edit ─────────────────────────────────────────────────────────

def set_gather_group_email_list(
    page,
    base_url: str,
    group_id: str,
    list_local_part: str,
    dry_run: bool,
) -> None:
    """
    Open the Gather group edit page and configure the email list:
      - Set the list address local part and domain (only when fields are enabled,
        i.e. the list has not yet been created — Gather disables them after creation)
      - Check "All community members can send to list?"
    """
    page.goto(f"{base_url}/groups/{group_id}/edit", wait_until="networkidle")

    # Name field — name="groups_group[mailman_list_attributes][name]"
    # Disabled once the list exists; Gather won't let it be changed after creation.
    name_input = page.locator('#groups_group_mailman_list_attributes_name')
    if name_input.count() == 0:
        log("WARN", "set_email_list", f"group {group_id}: mailman name field not found")
        return

    name_disabled = name_input.first.is_disabled()
    current_name = name_input.first.input_value().strip()

    # Domain select — name="groups_group[mailman_list_attributes][domain_id]"
    # Also disabled once list exists. Select by option label (the domain text), not value (numeric ID).
    domain_select = page.locator('#groups_group_mailman_list_attributes_domain_id')

    # "All community members can send to list?" checkbox
    everyone_checkbox = page.locator('#groups_group_mailman_list_attributes_all_cmty_members_can_send')

    if dry_run:
        checked = everyone_checkbox.first.is_checked() if everyone_checkbox.count() > 0 else False
        changes = []
        if name_disabled:
            if current_name != list_local_part:
                log("WARN", "set_email_list",
                    f"group {group_id}: name field disabled but value '{current_name}' ≠ '{list_local_part}'")
        else:
            # Mirrors the live branch below: the domain is always (re)submitted
            # here (not conditionally compared), since this path only runs
            # while the list hasn't been created yet.
            if current_name != list_local_part:
                changes.append(f"name: '{current_name}' → '{list_local_part}'")
            if domain_select.count() > 0:
                changes.append(f"domain → '{DOMAIN}'")
            else:
                log("WARN", "set_email_list", f"group {group_id}: domain selector not found")
        if not checked:
            changes.append("everyone_can_post: False → True")

        if changes:
            log("INFO", "set_email_list",
                f"[dry-run] group {group_id}: would change {', '.join(changes)}")
        else:
            log("INFO", "set_email_list", f"group {group_id}: email list settings already correct")
        return

    changed = False

    if not name_disabled:
        if current_name != list_local_part:
            name_input.first.fill(list_local_part)
            changed = True
        if domain_select.count() > 0:
            domain_select.first.select_option(label=DOMAIN)
            changed = True
        else:
            log("WARN", "set_email_list", f"group {group_id}: domain selector not found")
    elif current_name != list_local_part:
        log("WARN", "set_email_list",
            f"group {group_id}: name field disabled but value '{current_name}' ≠ '{list_local_part}'")

    if everyone_checkbox.count() > 0:
        if not everyone_checkbox.first.is_checked():
            # The checkbox is CSS-hidden; set it directly via JS to bypass visibility checks.
            page.evaluate(
                "document.getElementById"
                "('groups_group_mailman_list_attributes_all_cmty_members_can_send').checked = true"
            )
            changed = True
    else:
        log("WARN", "set_email_list", f"group {group_id}: all_cmty_members_can_send checkbox not found")

    if not changed:
        log("INFO", "set_email_list", f"group {group_id}: email list settings already correct")
        return

    page.locator('input[type="submit"]').first.click(timeout=10_000)
    page.wait_for_load_state("networkidle", timeout=30_000)

    err = _check_submit_errors(page)
    if err:
        screenshot(page, f"group_{group_id}_error")
        log("ERROR", "set_email_list", f"group {group_id}: form error — {err}")
    else:
        log("INFO", "set_email_list",
            f"group {group_id}: email list settings updated")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync Gather Drive config to Google Groups and update Gather group email lists.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
                        help=f"Path to OAuth client secrets JSON (default: {DEFAULT_CLIENT_SECRETS_PATH})")
    parser.add_argument("-f", "--folder", metavar="PREFIX",
                        help="Process only the folder whose name starts with this prefix "
                             "(case-insensitive, must be unique)")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Print planned changes without modifying anything")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Run browser in headless mode (default: True)")
    args = parser.parse_args()

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    # Google API services
    creds = get_credentials(args.credentials)
    dir_service = build("admin", "directory_v1", credentials=creds)
    settings_service = build("groupssettings", "v1", credentials=creds)

    # Gather credentials
    gather_email, gather_password = load_credentials()

    init_log()

    with sync_playwright() as pw:
        context = launch_browser(pw, headless=args.headless)
        page = context.new_page()
        login(page, BASE_URL, gather_email, gather_password)

        log("INFO", "scrape", f"Reading {BASE_URL}/gdrive/config…")
        entries = scrape_gdrive_config(page, BASE_URL)
        log("INFO", "scrape", f"Found {len(entries)} folder(s) in Drive config.")

        if not entries:
            sys.exit("No entries found on /gdrive/config — check page selectors.")

        # Apply --folder filter
        if args.folder:
            prefix = args.folder.lower()
            matching = [e for e in entries if e["folder_name"].lower().startswith(prefix)]
            if not matching:
                sys.exit(f"Error: no folder name starts with {args.folder!r}")
            if len(matching) > 1:
                names = ", ".join(f"'{e['folder_name']}'" for e in matching)
                sys.exit(f"Error: {args.folder!r} is ambiguous — matches {names}")
            entries = matching

        # Current FOLDER_IDS state — build name→id lookup for cross-referencing
        folder_ids_entries = list(read_folder_ids())  # preserve current order
        existing_ids = {fid for fid, _ in folder_ids_entries}
        name_to_fid = {name: fid for fid, name in folder_ids_entries}
        folder_ids_dirty = False

        for entry in entries:
            fname = entry["folder_name"]
            gid = entry["group_id"]
            gemail = group_email(fname)
            gdisplay = group_display_name(fname)
            list_local = to_slug_local(gemail)

            log("INFO", "process", f"Folder '{fname}' → group {gemail}")

            # 1. Add to FOLDER_IDS if we know the Drive folder ID
            fid = name_to_fid.get(fname)
            if fid is None:
                log("INFO", "folder_ids",
                    f"  '{fname}' not in folder_ids.gs (no Drive folder ID available on page)")
            elif fid not in existing_ids:
                log("INFO", "folder_ids", f"  Adding '{fname}' ({fid}) to folder_ids.gs")
                if not args.dry_run:
                    folder_ids_entries.append((fid, fname))
                    existing_ids.add(fid)
                    folder_ids_dirty = True
            else:
                log("INFO", "folder_ids", f"  '{fname}' already in folder_ids.gs")

            # 2. Ensure Google Group exists
            if args.dry_run:
                already_exists = group_exists(dir_service, gemail)
                if already_exists:
                    log("INFO", "google_group", f"  Group {gemail} already exists (no change)")
                else:
                    log("INFO", "google_group", f"  [dry-run] would create group {gemail}")
            else:
                already_exists = not ensure_group_exists(dir_service, gemail, gdisplay)
                log("INFO", "google_group",
                    f"  Group {gemail} {'already exists' if already_exists else 'created'}")

            # 3. Ensure required group settings
            if args.dry_run:
                if already_exists:
                    updates = compute_group_settings_updates(settings_service, gemail)
                    if updates:
                        log("INFO", "google_group", f"  [dry-run] would update settings: {updates}")
                    else:
                        log("INFO", "google_group", "  Settings already correct (no change)")
                else:
                    log("INFO", "google_group",
                        f"  [dry-run] would apply settings once created: {REQUIRED_GROUP_SETTINGS}")
            else:
                updates = ensure_group_settings(settings_service, gemail)
                if updates:
                    log("INFO", "google_group", f"  Updated settings: {updates}")
                else:
                    log("INFO", "google_group", "  Settings already correct")

            # 4. Set Gather group email list
            set_gather_group_email_list(page, BASE_URL, gid, list_local, args.dry_run)

        # Write updated folder_ids.gs if any new entries were added
        if folder_ids_dirty:
            write_folder_ids(folder_ids_entries)
            log("INFO", "folder_ids", f"Wrote folder_ids.gs with {len(folder_ids_entries)} entries.")

        context.close()


def to_slug_local(gemail: str) -> str:
    """Extract the local part (before @) from a group email address."""
    return gemail.split("@")[0]


if __name__ == "__main__":
    main()
