"""
Shared Google API utilities for group management scripts.

Provides auth, naming helpers (mirroring groups_drive_sync.gs), folder_ids.gs
I/O, and thin wrappers around the Directory and Groups Settings APIs.
"""

import contextlib
import pickle
import re
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

DOMAIN = "berkeleymoshav.org"
WHO_CAN_POST_ANYONE_ON_WEB = "ANYONE_CAN_POST"

REQUIRED_GROUP_SETTINGS = {
    "whoCanPostMessage": "ANYONE_CAN_POST",       # Anyone on the web can post
    "whoCanViewGroup": "ALL_IN_DOMAIN_CAN_VIEW",  # Entire organization can view conversations
    "whoCanViewMembership": "ALL_IN_DOMAIN_CAN_VIEW",  # Entire organization can view members
    "allowExternalMembers": "true",               # Allow external members
}
TOKEN_PATH = Path.home() / ".google_setup_token.pkl"
FOLDER_IDS_PATH = Path(__file__).parent.parent / "groups_drive_sync" / "folder_ids.gs"
DEFAULT_CLIENT_SECRETS_PATH = Path(__file__).parent.parent / "client_secret.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/admin.directory.group",
    "https://www.googleapis.com/auth/apps.groups.settings",
]


# ── Auth ──────────────────────────────────────────────────────────────────────

class _AuthUrlFormatter:
    """Pass-through stdout wrapper that splits the OAuth URL onto its own line."""
    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        self._stream.write(re.sub(
            r"(Please visit this URL to authorize this application:) (https?://\S+)",
            r"\1\n\2",
            text,
        ))

    def flush(self):
        self._stream.flush()


def get_credentials(client_secrets_file: str):
    """Return OAuth2 credentials, refreshing or re-running the flow as needed.

    Note: if you add new API scopes, delete ~/.google_setup_token.pkl so the
    token is refreshed with the updated scope set.
    """
    creds = None
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
            with contextlib.redirect_stdout(_AuthUrlFormatter(sys.stdout)):
                creds = flow.run_local_server(port=0, open_browser=False)
        with open(TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)
    return creds


# ── Naming helpers (mirror groups_drive_sync.gs) ──────────────────────────────

def strip_parens(name: str) -> str:
    return re.sub(r" *\([^)]*\)", "", name).strip()

def normalize_ampersand(name: str) -> str:
    return re.sub(r"\s*&\s*", " and ", name)

def to_slug(name: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(
        r"[^a-z0-9]+", "-", normalize_ampersand(strip_parens(name)).lower()
    ))

def group_email(folder_name: str) -> str:
    return f"{to_slug(folder_name)}@{DOMAIN}"

def group_display_name(folder_name: str) -> str:
    return normalize_ampersand(strip_parens(folder_name))


# ── folder_ids.gs I/O ─────────────────────────────────────────────────────────

_FOLDER_IDS_HEADER = """\
/**
 * Shared list of Google Drive folder IDs to sync.
 * This file is maintained by multiple scripts — edit here only.
 *
 * Each entry should be followed by a comment with the folder's name:
 *   '1AbCdEfGhIjKlMnOpQrStUvWxYz', // My Folder Name
 */
const FOLDER_IDS = ["""


def read_folder_ids() -> list[tuple[str, str]]:
    """Return [(folder_id, folder_name), ...] parsed from folder_ids.gs.

    Only parses entries inside the FOLDER_IDS array itself, so the example
    entry in the file's leading doc comment isn't mistaken for real data.
    """
    if not FOLDER_IDS_PATH.exists():
        return []
    text = FOLDER_IDS_PATH.read_text()
    m = re.search(r"const FOLDER_IDS = \[(.*?)\];", text, re.DOTALL)
    if not m:
        return []
    return re.findall(r"'([^']+)',\s*//\s*(.+)", m.group(1))


def write_folder_ids(entries: list[tuple[str, str]]) -> None:
    """Write folder_ids.gs with the given [(folder_id, folder_name)] list."""
    lines = _FOLDER_IDS_HEADER.splitlines()
    for fid, name in entries:
        lines.append(f"  '{fid}', // {name}")
    lines += ["];", ""]
    FOLDER_IDS_PATH.write_text("\n".join(lines))
    print(f"Wrote {FOLDER_IDS_PATH} with {len(entries)} folder ID(s).")


# ── Group API helpers ─────────────────────────────────────────────────────────

def ensure_group_exists(dir_service, gemail: str, display_name: str) -> bool:
    """Create the Google Group if it doesn't exist. Return True if created."""
    try:
        dir_service.groups().get(groupKey=gemail).execute()
        return False
    except Exception as err:
        if "404" in str(err) or "Resource Not Found" in str(err):
            dir_service.groups().insert(
                body={"email": gemail, "name": display_name}
            ).execute()
            return True
        raise


def ensure_group_settings(settings_service, gemail: str) -> dict:
    """Apply REQUIRED_GROUP_SETTINGS to the group. Return dict of {field: new_value} for changed fields."""
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    updates = {k: v for k, v in REQUIRED_GROUP_SETTINGS.items() if current.get(k) != v}
    if updates:
        settings_service.groups().update(groupUniqueId=gemail, body=updates).execute()
    return updates


def ensure_who_can_post_web(settings_service, gemail: str) -> bool:
    """Deprecated shim — use ensure_group_settings instead."""
    updates = ensure_group_settings(settings_service, gemail)
    return "whoCanPostMessage" in updates
