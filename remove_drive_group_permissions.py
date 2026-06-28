"""
Remove @berkeleymoshav.org group permissions from all folders in a Google Shared Drive.

Setup:
  1. In Google Cloud Console, create a Desktop OAuth 2.0 client and download
     the JSON as client_secret.json (or pass a different path via -c).
  2. Enable the Google Drive API for the project.
  3. On first run the script opens a browser for OAuth consent and caches the
     token at ~/.google_drive_token.json for subsequent runs.
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
            creds = flow.run_local_server(port=0)
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
            fields="nextPageToken,permissions(id,emailAddress,role,type)",
            pageToken=page_token,
        ).execute()
        perms.extend(resp.get("permissions", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return perms


def main():
    parser = argparse.ArgumentParser(
        description="Revoke @berkeleymoshav.org group permissions from Shared Drive folders.",
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

    print(f"Scanning Shared Drive: {args.drive_id}")
    folders = list_all_folders(service, args.drive_id)
    print(f"Found {len(folders)} folder(s)\n")

    total_found = 0
    total_removed = 0

    for folder in folders:
        fid, fname = folder["id"], folder["name"]
        perms = list_permissions(service, fid)

        targets = [
            p for p in perms
            if p.get("type") == "group"
            and p.get("emailAddress", "").lower().endswith(f"@{TARGET_DOMAIN}")
            and p.get("emailAddress", "").lower() not in exclude
        ]

        if not targets:
            continue

        print(f"Folder: {fname} ({fid})")
        for p in targets:
            total_found += 1
            email = p["emailAddress"]
            role = p["role"]
            if args.dry_run:
                print(f"  [dry-run] would remove: {email} ({role})")
            else:
                service.permissions().delete(
                    fileId=fid,
                    permissionId=p["id"],
                    supportsAllDrives=True,
                ).execute()
                print(f"  Removed: {email} ({role})")
                total_removed += 1
        print()

    if args.dry_run:
        print(f"Dry run complete. {len(folders)} folders scanned, {total_found} permission(s) would be removed.")
    else:
        print(f"Done. {len(folders)} folders scanned, {total_removed} permission(s) removed.")


if __name__ == "__main__":
    main()
