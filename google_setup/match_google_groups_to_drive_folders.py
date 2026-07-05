"""
One-off setup script: match Google Groups to top-level Shared Drive folders,
and rename each group so its email and display name conform to the sync
naming rules.

This script has no notion of a Gather group ID, so it can't populate a
group's custom footer (which needs both a Gather group link and a Drive
folder link — see util/google_group_footer.py) on its own. Footer
maintenance is owned by update_groups_in_google_and_hierarchy.py, which has
the full Gather context; run that afterward to wire up the folder link for
any group matched here.

The naming rules mirror groups_drive_sync.gs:
  - Strip parenthesized expressions from the folder name
  - Convert & to "and"
  - Lowercase, trim, collapse non-alphanumeric runs to hyphens
  - Group email  → <slug>@DOMAIN
  - Display name → folder name with parenthesized expressions stripped & normalized

Setup:
  1. In Google Cloud Console, create a Desktop OAuth 2.0 client and download
     the JSON as client_secret.json (or pass a different path via -c).
  2. Enable the Google Drive API, Admin SDK (Directory API), and Groups Settings
     API for the project.
  3. On first run the script prints an auth URL — paste it into a browser
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

from googleapiclient.discovery import build

from util.folder_matching import folder_matches_group
from util.google_group_utils import (
    DEFAULT_CLIENT_SECRETS_PATH,
    DOMAIN,
    REQUIRED_GROUP_SETTINGS,
    ensure_group_settings,
    get_credentials,
    group_display_name,
    group_email,
)

DEFAULT_DRIVE_ID = "0AFqC2xo9aTgPUk9PVA"

# ── Google API helpers ────────────────────────────────────────────────────────

def list_all_groups(dir_service) -> list[dict]:
    groups = []
    page_token = None
    while True:
        resp = dir_service.groups().list(
            domain=DOMAIN,
            maxResults=200,
            pageToken=page_token,
            fields="nextPageToken,groups(id,email,name)",
        ).execute()
        groups.extend(resp.get("groups", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return groups


def list_top_level_folders(drive_service, drive_id: str) -> list[dict]:
    folders = []
    page_token = None
    while True:
        resp = drive_service.files().list(
            corpora="drive",
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            q=f"mimeType='application/vnd.google-apps.folder' and '{drive_id}' in parents and trashed=false",
            fields="nextPageToken,files(id,name)",
            pageToken=page_token,
        ).execute()
        folders.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return folders


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Match Google Groups to Shared Drive folders and rename groups to "
                    "conform to sync naming rules.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-d", "--drive-id", default=DEFAULT_DRIVE_ID,
                        help=f"Shared Drive ID (default: {DEFAULT_DRIVE_ID})")
    parser.add_argument("-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
                        help=f"Path to OAuth client secrets JSON (default: {DEFAULT_CLIENT_SECRETS_PATH})")
    parser.add_argument("-g", "--group", metavar="PREFIX",
                        help="Process only the group whose name starts with this prefix "
                             "(case-insensitive, must be unique)")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Print planned changes without renaming groups")
    args = parser.parse_args()

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    creds = get_credentials(args.credentials)
    drive_service = build("drive", "v3", credentials=creds)
    dir_service = build("admin", "directory_v1", credentials=creds)
    settings_service = build("groupssettings", "v1", credentials=creds)

    print(f"Listing top-level folders in Shared Drive {args.drive_id}…")
    folders = list_top_level_folders(drive_service, args.drive_id)
    print(f"  {len(folders)} folder(s) found.")

    print(f"\nListing Google Groups for {DOMAIN}…")
    groups = list_all_groups(dir_service)
    print(f"  {len(groups)} group(s) found.")

    if args.group:
        prefix = args.group.lower()
        groups = [g for g in groups if g["name"].lower().startswith(prefix)]
        if not groups:
            sys.exit(f"Error: no group name starts with {args.group!r}")
        if len(groups) > 1:
            names = ", ".join(f"'{g['name']}'" for g in groups)
            sys.exit(f"Error: {args.group!r} is ambiguous — matches {names}")
        print(f"  Filtering to group: '{groups[0]['name']}' ({groups[0]['email']})")

    print()

    matched: list[tuple[dict, dict]] = []
    unmatched_groups: list[dict] = []
    errors: list[str] = []

    for group in groups:
        matching = [f for f in folders if folder_matches_group(group["name"], f["name"])]
        if not matching:
            unmatched_groups.append(group)
            print(f"[NO MATCH]  group '{group['name']}' ({group['email']})")
            continue
        if len(matching) > 1:
            names = ", ".join(f"'{f['name']}'" for f in matching)
            msg = f"group '{group['name']}' ({group['email']}) matches {len(matching)} folders: {names}"
            errors.append(msg)
            print(f"[AMBIGUOUS] {msg}")
            continue

        folder = matching[0]
        new_email = group_email(folder["name"])
        new_name = group_display_name(folder["name"])
        rename_needed = (
            group["email"].lower() != new_email.lower()
            or group["name"] != new_name
        )

        tag = "[dry-run] " if args.dry_run else ""
        print(f"[MATCH]  '{group['name']}' ({group['email']}) → folder '{folder['name']}' ({folder['id']})")
        if rename_needed:
            print(f"         {tag}rename: '{group['name']}' → '{new_name}' / {group['email']} → {new_email}")
        else:
            print(f"         (no rename needed)")

        if rename_needed and not args.dry_run:
            dir_service.groups().update(
                groupKey=group["id"],
                body={"email": new_email, "name": new_name},
            ).execute()

        if args.dry_run:
            print(f"         {tag}would apply settings: {REQUIRED_GROUP_SETTINGS}")
        else:
            updates = ensure_group_settings(settings_service, new_email)
            if updates:
                print(f"         Updated settings: {updates}")
            else:
                print(f"         Settings already correct")

        matched.append((group, folder))

    if errors:
        print(f"\nError: {len(errors)} ambiguous match(es) — resolve ties before proceeding:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    print(f"\nSummary: {len(matched)} matched, {len(unmatched_groups)} unmatched.")

    if unmatched_groups:
        print("Unmatched groups:")
        for g in unmatched_groups:
            print(f"  {g['name']} ({g['email']})")

    if matched:
        print("\nRun update_groups_in_google_and_hierarchy.py to wire up each matched "
              "group's footer link to its folder.")


if __name__ == "__main__":
    main()
