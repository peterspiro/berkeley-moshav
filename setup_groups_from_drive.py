"""
One-off setup script: match Google Groups to top-level Shared Drive folders,
rename each group so its email and display name conform to the sync naming
rules, and initialize folder_ids.gs with the matched folder IDs.

The naming rules mirror groups_drive_sync.gs:
  - Strip parenthesized expressions from the folder name
  - Lowercase, trim, collapse non-alphanumeric runs to hyphens
  - Group email  → <slug>@DOMAIN
  - Display name → folder name with parenthesized expressions stripped

Setup:
  1. In Google Cloud Console, create a Desktop OAuth 2.0 client and download
     the JSON as client_secret.json (or pass a different path via -c).
  2. Enable the Google Drive API and Admin SDK (Directory API) for the project.
  3. On first run the script prints an auth URL — paste it into a browser
     signed in as a Workspace super-admin.  The token is cached at
     ~/.google_setup_token.pkl for subsequent runs.
"""

import argparse
import difflib
import os
import pickle
import re
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/admin.directory.group",
]
TOKEN_PATH = Path.home() / ".google_setup_token.pkl"
FOLDER_IDS_PATH = Path(__file__).parent / "folder_ids.gs"

DEFAULT_DRIVE_ID = "0AFqC2xo9aTgPUk9PVA"
DOMAIN = "berkeleymoshav.org"  # edit to match your Workspace domain

# Minimum similarity ratio (0–1) to accept a folder match
MIN_MATCH_RATIO = 0.5


# ── Auth ─────────────────────────────────────────────────────────────────────

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


# ── Naming helpers (mirror groups_drive_sync.gs) ─────────────────────────────

# Trailing terms stripped before matching (case-insensitive, order matters —
# longer phrases before shorter ones that share a prefix).
MATCH_SUFFIXES = ["working group", "circle"]

def strip_parens(name: str) -> str:
    return re.sub(r" *\([^)]*\)", "", name).strip()

def to_slug(name: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", strip_parens(name).lower()))

def to_match_slug(name: str) -> str:
    """Slug used only for matching: also strips trailing organisational terms."""
    slug = to_slug(name)
    for suffix in MATCH_SUFFIXES:
        suffix_slug = re.sub(r"[^a-z0-9]+", "-", suffix)
        if slug.endswith("-" + suffix_slug):
            slug = slug[: -(len(suffix_slug) + 1)]
            break
    return slug

def group_email(folder_name: str) -> str:
    return f"{to_slug(folder_name)}@{DOMAIN}"

def group_display_name(folder_name: str) -> str:
    return strip_parens(folder_name)


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
    """Return folders whose only parent is the shared drive root."""
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


# ── Matching ──────────────────────────────────────────────────────────────────

def best_folder_match(group_name: str, folders: list[dict], min_ratio: float) -> tuple[dict | None, float, list[dict]]:
    """Return (best_folder, best_ratio, all_folders_above_min_ratio).

    If more than one folder scores above min_ratio the caller should treat it
    as an ambiguous error — the third element contains all such folders.
    """
    group_slug = to_match_slug(group_name)
    scored = [
        (difflib.SequenceMatcher(None, group_slug, to_match_slug(f["name"])).ratio(), f)
        for f in folders
    ]
    above = [(r, f) for r, f in scored if r >= min_ratio]
    above.sort(key=lambda x: x[0], reverse=True)
    best_ratio = above[0][0] if above else max((r for r, _ in scored), default=0.0)
    if len(above) == 1:
        return above[0][1], above[0][0], above
    return None, best_ratio, [f for _, f in above]


# ── folder_ids.gs writer ──────────────────────────────────────────────────────

def write_folder_ids(matches: list[tuple[dict, dict]]):
    """Write folder_ids.gs with matched folder IDs annotated with folder names."""
    lines = [
        "/**",
        " * Shared list of Google Drive folder IDs to sync.",
        " * This file is maintained by multiple scripts — edit here only.",
        " *",
        " * Each entry should be followed by a comment with the folder's name:",
        " *   '1AbCdEfGhIjKlMnOpQrStUvWxYz', // My Folder Name",
        " */",
        "const FOLDER_IDS = [",
    ]
    for _group, folder in matches:
        lines.append(f"  '{folder['id']}', // {folder['name']}")
    lines += ["];", ""]
    FOLDER_IDS_PATH.write_text("\n".join(lines))
    print(f"\nWrote {FOLDER_IDS_PATH} with {len(matches)} folder ID(s).")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Match Google Groups to Shared Drive folders, rename groups to conform "
                    "to sync naming rules, and initialize folder_ids.gs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-d", "--drive-id",
        default=DEFAULT_DRIVE_ID,
        help=f"Shared Drive ID (default: {DEFAULT_DRIVE_ID})",
    )
    parser.add_argument(
        "-c", "--credentials",
        default="client_secret.json",
        help="Path to OAuth client secrets JSON (default: client_secret.json)",
    )
    parser.add_argument(
        "-m", "--min-match",
        type=float,
        default=MIN_MATCH_RATIO,
        metavar="RATIO",
        help=f"Minimum similarity ratio 0–1 to accept a match (default: {MIN_MATCH_RATIO})",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Print planned changes without renaming groups or writing folder_ids.gs",
    )
    args = parser.parse_args()

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    creds = get_credentials(args.credentials)
    drive_service = build("drive", "v3", credentials=creds)
    dir_service = build("admin", "directory_v1", credentials=creds)

    print(f"Listing top-level folders in Shared Drive {args.drive_id}…")
    folders = list_top_level_folders(drive_service, args.drive_id)
    print(f"  {len(folders)} folder(s) found.")

    print(f"\nListing Google Groups for {DOMAIN}…")
    groups = list_all_groups(dir_service)
    print(f"  {len(groups)} group(s) found.")

    print()

    matched: list[tuple[dict, dict]] = []   # (group, folder)
    unmatched_groups: list[dict] = []
    errors: list[str] = []

    for group in groups:
        folder, ratio, above = best_folder_match(group["name"], folders, args.min_match)
        if not above:
            unmatched_groups.append(group)
            print(f"[NO MATCH]  group '{group['name']}' ({group['email']})  best ratio={ratio:.2f}")
            continue
        if folder is None:
            names = ", ".join(f"'{f['name']}'" for f in above)
            msg = (
                f"group '{group['name']}' ({group['email']}) matches {len(above)} folders "
                f"above threshold: {names}"
            )
            errors.append(msg)
            print(f"[AMBIGUOUS] {msg}")
            continue

        new_email = group_email(folder["name"])
        new_name = group_display_name(folder["name"])
        rename_needed = (
            group["email"].lower() != new_email.lower()
            or group["name"] != new_name
        )

        tag = "[dry-run] " if args.dry_run else ""
        print(
            f"[MATCH {ratio:.2f}]  '{group['name']}' ({group['email']})\n"
            f"            → folder '{folder['name']}' ({folder['id']})"
        )
        if rename_needed:
            print(f"            {tag}rename: '{group['name']}' → '{new_name}' / {group['email']} → {new_email}")
        else:
            print(f"            (no rename needed)")

        if rename_needed and not args.dry_run:
            dir_service.groups().update(
                groupKey=group["id"],
                body={"email": new_email, "name": new_name},
            ).execute()

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
        if args.dry_run:
            print("\n[dry-run] Would write folder_ids.gs with:")
            for _g, f in matched:
                print(f"  '{f['id']}', // {f['name']}")
        else:
            write_folder_ids(matched)


if __name__ == "__main__":
    main()
