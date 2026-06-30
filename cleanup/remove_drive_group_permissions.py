"""
Remove @berkeleymoshav.org group permissions from all folders in a Google Shared Drive.

Setup:
  1. In Google Cloud Console, create a Desktop OAuth 2.0 client and download
     the JSON as client_secret.json (or pass a different path via -c).
  2. Enable the Google Drive API for the project.
  3. On first run the script prints an auth URL — paste it into a browser signed
     in as a Workspace user. The token is cached at ~/.google_drive_token.json
     for subsequent runs.
"""

import argparse
import os
import pickle
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_PATH = Path.home() / ".google_drive_token.json"
DEFAULT_DRIVE_ID = "0AFqC2xo9aTgPUk9PVA"
TARGET_DOMAIN = "berkeleymoshav.org"


def get_credentials(client_secrets_file: str):
    creds = None
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=False)
        with open(TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)
    return creds


def list_all_folders(service, drive_id: str) -> list[dict]:
    folders = []
    page_token = None
    while True:
        resp = service.files().list(
            corpora="drive",
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            q="mimeType='application/vnd.google-apps.folder'",
            fields="nextPageToken,files(id,name)",
            pageToken=page_token,
        ).execute()
        folders.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return folders


def list_permissions(service, file_id: str) -> list[dict]:
    perms = []
    page_token = None
    while True:
        resp = service.permissions().list(
            fileId=file_id,
            supportsAllDrives=True,
            fields="nextPageToken,permissions(id,emailAddress,role,type,permissionDetails)",
            pageToken=page_token,
        ).execute()
        perms.extend(resp.get("permissions", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return perms


def is_explicit(perm: dict) -> bool:
    """Return True if the permission is explicitly set on this item (not merely inherited)."""
    details = perm.get("permissionDetails", [])
    if not details:
        return True
    return any(not d.get("inherited", False) for d in details)


def process_item(service, item_id: str, item_label: str, exclude: set, dry_run: bool) -> tuple[int, int]:
    perms = list_permissions(service, item_id)
    targets = [
        p for p in perms
        if p.get("type") == "group"
        and p.get("emailAddress", "").lower().endswith(f"@{TARGET_DOMAIN}")
        and p.get("emailAddress", "").lower() not in exclude
        and is_explicit(p)
    ]
    if not targets:
        return 0, 0

    found = len(targets)
    removed = 0
    print(item_label)
    for p in targets:
        email = p["emailAddress"]
        role = p["role"]
        if dry_run:
            print(f"  [dry-run] would remove: {email} ({role})")
        else:
            service.permissions().delete(
                fileId=item_id,
                permissionId=p["id"],
                supportsAllDrives=True,
            ).execute()
            print(f"  Removed: {email} ({role})")
            removed += 1
    print()
    return found, removed


def main():
    parser = argparse.ArgumentParser(
        description="Revoke @berkeleymoshav.org group permissions from a Shared Drive and its folders.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-d", "--drive-id",
        default=DEFAULT_DRIVE_ID,
        help=f"Shared Drive ID (default: {DEFAULT_DRIVE_ID})",
    )
    parser.add_argument(
        "-e", "--exclude",
        nargs="+",
        metavar="EMAIL",
        default=[],
        help="@berkeleymoshav.org addresses to preserve (space-separated)",
    )
    parser.add_argument(
        "-c", "--credentials",
        default="client_secret.json",
        help="Path to OAuth client secrets JSON (default: client_secret.json)",
    )
    parser.add_argument(
        "-f", "--folder",
        metavar="PREFIX",
        default=None,
        help="Restrict to a single folder whose name starts with PREFIX (case-insensitive)",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Print what would be removed without making changes",
    )
    args = parser.parse_args()

    exclude = {e.lower() for e in args.exclude}

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    creds = get_credentials(args.credentials)
    service = build("drive", "v3", credentials=creds)

    print(f"Scanning Shared Drive: {args.drive_id}\n")

    total_found = 0
    total_removed = 0

    # Check the shared drive root itself (skipped when restricting to a single folder)
    if args.folder is None:
        drive_name = service.drives().get(driveId=args.drive_id, fields="name").execute().get("name", args.drive_id)
        f, r = process_item(service, args.drive_id, f"Shared Drive root: {drive_name} ({args.drive_id})", exclude, args.dry_run)
        total_found += f
        total_removed += r

    # Check all folders within the drive
    folders = list_all_folders(service, args.drive_id)

    if args.folder is not None:
        prefix = args.folder.lower()
        matches = [f for f in folders if f["name"].lower().startswith(prefix)]
        if not matches:
            sys.exit(f"Error: no folder found with name starting with {args.folder!r}")
        if len(matches) > 1:
            names = ", ".join(f["name"] for f in matches)
            sys.exit(f"Error: prefix {args.folder!r} matches multiple folders: {names}")
        folders = matches

    print(f"Found {len(folders)} folder(s)\n")
    for folder in folders:
        f, r = process_item(service, folder["id"], f"Folder: {folder['name']} ({folder['id']})", exclude, args.dry_run)
        total_found += f
        total_removed += r

    if args.dry_run:
        print(f"Dry run complete. {len(folders)} folders scanned, {total_found} permission(s) would be removed.")
    else:
        print(f"Done. {len(folders)} folders scanned, {total_removed} permission(s) removed.")


if __name__ == "__main__":
    main()
