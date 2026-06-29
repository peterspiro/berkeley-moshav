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

# Trailing terms stripped before matching (longer phrases before shorter).
MATCH_SUFFIXES = ["working group", "circle"]

# Small words skipped when building an abbreviation from a folder name.
ABBREV_STOP_WORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to"}

def strip_parens(name: str) -> str:
    return re.sub(r" *\([^)]*\)", "", name).strip()

def normalize_ampersand(name: str) -> str:
    return re.sub(r"\s*&\s*", " and ", name)

def to_slug(name: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", normalize_ampersand(strip_parens(name)).lower()))

def group_display_name(folder_name: str) -> str:
    return normalize_ampersand(strip_parens(folder_name))

def to_match_base(name: str) -> str:
    """Lowercase name with parens and trailing organisational terms removed, used for matching."""
    base = strip_parens(name).lower().strip()
    for suffix in MATCH_SUFFIXES:
        if re.search(r"\b" + re.escape(suffix) + r"\s*$", base):
            base = re.sub(r"\b" + re.escape(suffix) + r"\s*$", "", base).strip()
            break
    return base

def folder_abbreviation(folder_name: str) -> str:
    """Initial letter of each non-stop word in the match base, e.g. 'dfl' for 'Development, Finance, and Legal'."""
    words = re.findall(r"[a-z0-9]+", to_match_base(folder_name))
    return "".join(w[0] for w in words if w not in ABBREV_STOP_WORDS)

def group_email(folder_name: str) -> str:
    return f"{to_slug(folder_name)}@{DOMAIN}"


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

def singularize(base: str) -> str:
    """Strip trailing 's' from each word token for plural-insensitive comparison."""
    return re.sub(r"[a-z0-9]+", lambda m: m.group()[:-1] if m.group().endswith("s") and len(m.group()) > 2 else m.group(), base)

def concat(base: str) -> str:
    """Remove all non-alphanumeric characters, collapsing word boundaries."""
    return re.sub(r"[^a-z0-9]", "", base)

def folder_matches_group(group_name: str, folder_name: str) -> bool:
    """True if group_name matches folder_name by prefix or abbreviation.

    Both names are normalised (parens and trailing Circle/Working Group stripped,
    words singularized) before comparison.  A match occurs when:
      - the group's match base is a prefix of the folder's match base
        (with or without word separators, so 'jewishlife' matches 'jewish life'), or
      - the group's match base equals the folder's abbreviation (initials of
        non-stop words), e.g. 'dfl' matches 'Development, Finance, and Legal'.
    """
    group_base = singularize(to_match_base(group_name))
    folder_base = singularize(to_match_base(folder_name))
    if folder_base.startswith(group_base) or concat(folder_base).startswith(concat(group_base)):
        return True
    if group_base == folder_abbreviation(folder_name):
        return True
    return False


def find_matching_folders(group_name: str, folders: list[dict]) -> list[dict]:
    """Return all folders that match group_name."""
    return [f for f in folders if folder_matches_group(group_name, f["name"])]


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
        "-g", "--group",
        metavar="NAME_OR_EMAIL",
        help="Process only the group whose name or email matches this value (case-insensitive); skips writing folder_ids.gs",
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

    if args.group:
        needle = args.group.lower()
        groups = [g for g in groups if g["name"].lower() == needle or g["email"].lower() == needle or g["email"].lower().split("@")[0] == needle]
        if not groups:
            sys.exit(f"Error: no group found matching {args.group!r}")
        print(f"  Filtering to group: '{groups[0]['name']}' ({groups[0]['email']})")

    print()

    matched: list[tuple[dict, dict]] = []   # (group, folder)
    unmatched_groups: list[dict] = []
    errors: list[str] = []

    for group in groups:
        matching = find_matching_folders(group["name"], folders)
        if not matching:
            unmatched_groups.append(group)
            print(f"[NO MATCH]  group '{group['name']}' ({group['email']})")
            continue
        if len(matching) > 1:
            names = ", ".join(f"'{f['name']}'" for f in matching)
            msg = (
                f"group '{group['name']}' ({group['email']}) matches {len(matching)} folders: {names}"
            )
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

    if matched and not args.group:
        if args.dry_run:
            print("\n[dry-run] Would write folder_ids.gs with:")
            for _g, f in matched:
                print(f"  '{f['id']}', // {f['name']}")
        else:
            write_folder_ids(matched)


if __name__ == "__main__":
    main()
