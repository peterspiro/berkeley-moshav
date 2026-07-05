"""
One-off setup script: reads Gather's Google Drive Settings (/gdrive/config)
and for each folder listed there:

  1. Creates the corresponding Google Group if it doesn't exist.
  2. Sets the group's "Who can post" to "Anyone on the web" if not already set.
  3. If the folder's Drive ID is known (scraped straight from the
     /gdrive/config row — not every row exposes one), ensures the group's
     custom footer links to it (see util/google_group_footer.py). Rows
     with no resolvable Drive ID are reported, not silently skipped; run
     update_groups_in_google_and_hierarchy.py afterward to resolve those
     via its fuller Drive-folder-ID resolution chain.
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
    log_noop,
    login,
    set_gather_group_email_list,
)
from util.google_group_footer import compute_group_footer_updates, ensure_group_footer
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
)
from util.gdrive_config import scrape_gdrive_config

BASE_URL = "https://berkeley-moshav.gather.coop"

_LOG_FILE_PATH = __import__("pathlib").Path("debug/gdrive_groups_log.csv")
_SCREENSHOT_DIR = __import__("pathlib").Path("debug/gdrive_groups_screenshots")

configure(_LOG_FILE_PATH, _SCREENSHOT_DIR)


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
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Only log actual changes (or, in dry-run, changes that would be "
                             "made) — suppress already-correct/no-op status lines")
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

        for entry in entries:
            fname = entry["folder_name"]
            gid = entry["group_id"]
            folder_id = entry.get("google_file_id")
            gemail = group_email(fname)
            gdisplay = group_display_name(fname)
            list_local = to_slug_local(gemail)

            log_noop(args.quiet, "INFO", "process", f"Folder '{fname}' → group {gemail}")

            # 1. Ensure Google Group exists
            if args.dry_run:
                already_exists = group_exists(dir_service, gemail)
                if already_exists:
                    log_noop(args.quiet, "INFO", "google_group",
                              f"  Group {gemail} already exists (no change)")
                else:
                    log("INFO", "google_group", f"  [dry-run] would create group {gemail}")
            else:
                already_exists = not ensure_group_exists(dir_service, gemail, gdisplay)
                if already_exists:
                    log_noop(args.quiet, "INFO", "google_group", f"  Group {gemail} already exists")
                else:
                    log("INFO", "google_group", f"  Group {gemail} created")

            # 2. Ensure required group settings
            if args.dry_run:
                if already_exists:
                    updates = compute_group_settings_updates(settings_service, gemail)
                    if updates:
                        log("INFO", "google_group", f"  [dry-run] would update settings: {updates}")
                    else:
                        log_noop(args.quiet, "INFO", "google_group",
                                  "  Settings already correct (no change)")
                else:
                    log("INFO", "google_group",
                        f"  [dry-run] would apply settings once created: {REQUIRED_GROUP_SETTINGS}")
            else:
                updates = ensure_group_settings(settings_service, gemail)
                if updates:
                    log("INFO", "google_group", f"  Updated settings: {updates}")
                else:
                    log_noop(args.quiet, "INFO", "google_group", "  Settings already correct")

            # 3. Ensure the group's footer links to its Drive folder, if known
            if not folder_id:
                log_noop(args.quiet, "INFO", "footer",
                          f"  No known Drive folder ID for '{fname}' — skipping footer link "
                          "(run update_groups_in_google_and_hierarchy.py to resolve it)")
            elif args.dry_run:
                if already_exists:
                    updates = compute_group_footer_updates(
                        settings_service, gemail, gid, folder_id, BASE_URL, gdisplay
                    )
                    if updates:
                        log("INFO", "footer", f"  [dry-run] would update footer: {updates}")
                    else:
                        log_noop(args.quiet, "INFO", "footer", "  Footer already correct (no change)")
                else:
                    log("INFO", "footer", "  [dry-run] would set footer once group is created")
            else:
                updates = ensure_group_footer(settings_service, gemail, gid, folder_id, BASE_URL, gdisplay)
                if updates:
                    log("INFO", "footer", f"  Updated footer: {updates}")
                else:
                    log_noop(args.quiet, "INFO", "footer", "  Footer already correct")

            # 4. Set Gather group email list
            set_gather_group_email_list(page, BASE_URL, gid, list_local, DOMAIN, args.dry_run, args.quiet)

        context.close()


def to_slug_local(gemail: str) -> str:
    """Extract the local part (before @) from a group email address."""
    return gemail.split("@")[0]


if __name__ == "__main__":
    main()
