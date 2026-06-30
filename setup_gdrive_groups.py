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
import re
import sys
import time

from playwright.sync_api import sync_playwright
from googleapiclient.discovery import build

from credentials import load_credentials
from gather_utils import (
    configure,
    init_log,
    launch_browser,
    log,
    login,
    screenshot,
    _check_submit_errors,
)
from google_group_utils import (
    DOMAIN,
    REQUIRED_GROUP_SETTINGS,
    ensure_group_exists,
    ensure_group_settings,
    get_credentials,
    group_display_name,
    group_email,
    read_folder_ids,
    write_folder_ids,
)

BASE_URL = "https://berkeley-moshav.gather.coop"

_LOG_FILE_PATH = __import__("pathlib").Path("gdrive_groups_log.csv")
_SCREENSHOT_DIR = __import__("pathlib").Path("gdrive_groups_screenshots")

configure(_LOG_FILE_PATH, _SCREENSHOT_DIR)


# ── Gather scraping ───────────────────────────────────────────────────────────

def scrape_gdrive_config(page, base_url: str) -> list[dict]:
    """
    Navigate to /gdrive/config and return a list of entries for the Folders
    section only (not Shared Drives), each a dict with:
      folder_name – display name of the folder (plain text on page)
      group_id    – Gather group ID associated with this folder
      group_name  – Gather group name

    The page has a single <table> with <tr class="heading"> rows separating
    sections (Shared Drives / Folders / Files).  Each data row has the folder
    name as plain text in the first <td> and a /groups/{id} link in the
    third <td>.
    """
    page.goto(f"{base_url}/gdrive/config", wait_until="networkidle")

    entries = []
    in_folders_section = False

    for row in page.locator("table tbody tr").all():
        # Section heading row — track which section we're in
        heading = row.locator("h2")
        if heading.count() > 0:
            in_folders_section = heading.first.inner_text().strip() == "Folders"
            continue

        if not in_folders_section:
            continue

        # Group link — rows without one are header/empty rows
        group_link = row.locator("a[href*='/groups/']")
        if group_link.count() == 0:
            continue
        group_href = group_link.first.get_attribute("href") or ""
        gm = re.search(r"/groups/(\d+)", group_href)
        if not gm:
            continue
        group_id = gm.group(1)
        group_name = group_link.first.inner_text().strip()

        # Folder name is plain text in the first <td>
        cells = row.locator("td").all()
        if not cells:
            continue
        folder_name = cells[0].inner_text().strip()
        if not folder_name:
            continue

        entries.append(dict(
            folder_name=folder_name,
            group_id=group_id,
            group_name=group_name,
        ))

    return entries


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
      - Set the list address local part
      - Select the domain (DOMAIN) from the domain selector
      - Check "All community members can send to list?"
    """
    page.goto(f"{base_url}/groups/{group_id}/edit", wait_until="networkidle")

    # Dump HTML once to verify selectors
    html_path = _SCREENSHOT_DIR / f"group_{group_id}_edit.html"
    if not html_path.exists():
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        html_path.write_text(page.content())
        log("INFO", "set_email_list", f"Page HTML dumped to {html_path}")

    # Local part of the email list address
    name_input = page.locator('input[name*="mailman_list_attributes"][name*="[name]"]')
    if name_input.count() == 0:
        log("WARN", "set_email_list", f"group {group_id}: mailman name field not found")
        return

    current_name = name_input.first.input_value().strip()
    if current_name == list_local_part:
        log("INFO", "set_email_list", f"group {group_id}: list name already '{list_local_part}'")
    else:
        log("INFO", "set_email_list",
            f"group {group_id}: setting list name '{current_name}' → '{list_local_part}'")

    # Domain selector — selects which @domain the list lives under.
    # NOTE: verify the selector against the actual page if this doesn't work.
    domain_select = page.locator('select[name*="mailman_list_attributes"][name*="[domain]"]')

    # "All community members can send to list?" checkbox
    # NOTE: verify the selector against the actual page if this doesn't work.
    everyone_checkbox = page.locator(
        'input[type="checkbox"][name*="mailman_list_attributes"][name*="[everyone_can_post]"]'
    )

    if dry_run:
        domain_val = domain_select.first.input_value() if domain_select.count() > 0 else "(unknown)"
        checked = everyone_checkbox.first.is_checked() if everyone_checkbox.count() > 0 else False
        log("INFO", "set_email_list",
            f"[dry-run] group {group_id}: would set name='{list_local_part}' "
            f"domain='{DOMAIN}' everyone_can_post=True "
            f"(current domain='{domain_val}' everyone_can_post={checked})")
        return

    name_input.first.fill(list_local_part)

    if domain_select.count() > 0:
        domain_select.first.select_option(DOMAIN)
    else:
        log("WARN", "set_email_list", f"group {group_id}: domain selector not found")

    if everyone_checkbox.count() > 0:
        if not everyone_checkbox.first.is_checked():
            everyone_checkbox.first.check()
    else:
        log("WARN", "set_email_list", f"group {group_id}: everyone_can_post checkbox not found")

    submit = page.locator('input[type="submit"], button[type="submit"]')
    submit.first.click(timeout=10_000)
    page.wait_for_load_state("networkidle", timeout=30_000)

    err = _check_submit_errors(page)
    if err:
        screenshot(page, f"group_{group_id}_error")
        log("ERROR", "set_email_list", f"group {group_id}: form error — {err}")
    else:
        log("INFO", "set_email_list",
            f"group {group_id}: list set to {list_local_part}@{DOMAIN}, everyone_can_post=True")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync Gather Drive config to Google Groups and update Gather group email lists.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-c", "--credentials", default="client_secret.json",
                        help="Path to OAuth client secrets JSON (default: client_secret.json)")
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
                log("INFO", "google_group", f"  [dry-run] would ensure group {gemail} exists")
            else:
                created = ensure_group_exists(dir_service, gemail, gdisplay)
                log("INFO", "google_group",
                    f"  Group {gemail} {'created' if created else 'already exists'}")

            # 3. Ensure required group settings
            if args.dry_run:
                log("INFO", "google_group",
                    f"  [dry-run] would apply settings: {REQUIRED_GROUP_SETTINGS}")
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
