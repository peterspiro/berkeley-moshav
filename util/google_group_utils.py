"""
Shared Google API utilities for group management scripts.

Provides auth, naming helpers (mirroring groups_drive_sync.gs), and thin
wrappers around the Directory and Groups Settings APIs. The
folder-ID-to-group mapping itself lives in each group's own custom footer
(see util/google_group_footer.py), not in this module.
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

# Settings enforced on every group a script touches (not just newly-created
# ones, unlike REQUIRED_GROUP_SETTINGS which is only applied at creation):
#   - "Conversation history" turned on (isArchived).
#   - Content moderation open to all members (whoCanModerateContent), so any
#     group member can moderate content rather than only owners/managers.
CONVERSATION_HISTORY_SETTINGS = {"isArchived": "true"}
MODERATION_SETTINGS = {"whoCanModerateContent": "ALL_MEMBERS"}
TOKEN_PATH = Path.home() / ".google_setup_token.pkl"
DEFAULT_CLIENT_SECRETS_PATH = Path(__file__).parent.parent / "client_secret.json"

SCOPES = [
    # Not drive.readonly: update_groups_in_google_and_hierarchy.py creates
    # new Drive folders (files().create()), which needs write access.
    "https://www.googleapis.com/auth/drive",
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

    If the cached token was issued for a narrower scope set than SCOPES
    currently requires (e.g. after adding a new API scope to this file),
    it's discarded and the auth flow re-run automatically rather than
    failing with a confusing 403 "insufficient authentication scopes".
    """
    creds = None
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
        if creds and set(SCOPES) - set(creds.scopes or []):
            creds = None
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

def is_in_domain(email: str, domain: str = DOMAIN) -> bool:
    """True if email is a well-formed address (exactly one '@') whose
    domain matches `domain`, case-insensitively."""
    parts = email.split("@")
    return len(parts) == 2 and parts[1].lower() == domain.lower()

def group_display_name(folder_name: str) -> str:
    return normalize_ampersand(strip_parens(folder_name))


# ── Group API helpers ─────────────────────────────────────────────────────────

def group_exists(dir_service, gemail: str) -> bool:
    """Return True if gemail already exists as a Google Group. Read-only —
    safe to call from a dry run."""
    try:
        dir_service.groups().get(groupKey=gemail).execute()
        return True
    except Exception as err:
        if "404" in str(err) or "Resource Not Found" in str(err):
            return False
        raise


def get_group_by_email(dir_service, gemail: str) -> dict | None:
    """Return {'id', 'email', 'name'} for the Google Group at gemail, or
    None if no such group exists. Read-only."""
    try:
        resp = dir_service.groups().get(groupKey=gemail).execute()
    except Exception as err:
        if "404" in str(err) or "Resource Not Found" in str(err):
            return None
        raise
    return {"id": resp.get("id"), "email": resp.get("email"), "name": resp.get("name")}


def list_group_aliases(dir_service, gemail: str) -> list[str]:
    """Return the group's current alias addresses. Read-only."""
    resp = dir_service.groups().aliases().list(groupKey=gemail).execute()
    return [a["alias"] for a in resp.get("aliases", [])]


def add_group_alias(dir_service, gemail: str, alias: str) -> bool:
    """Add alias as an alias address of the group at gemail. Return True
    if added, False if it was already an alias of this group."""
    if alias.lower() in {a.lower() for a in list_group_aliases(dir_service, gemail)}:
        return False
    dir_service.groups().aliases().insert(groupKey=gemail, body={"alias": alias}).execute()
    return True


def ensure_group_exists(dir_service, gemail: str, display_name: str) -> bool:
    """Create the Google Group if it doesn't exist. Return True if created."""
    if group_exists(dir_service, gemail):
        return False
    dir_service.groups().insert(body={"email": gemail, "name": display_name}).execute()
    return True


def compute_group_settings_updates(settings_service, gemail: str, required: dict = None) -> dict:
    """Return {field: new_value} for settings that differ from `required`
    (default REQUIRED_GROUP_SETTINGS), without applying them. Read-only —
    safe to call from a dry run. Raises if the group doesn't exist."""
    if required is None:
        required = REQUIRED_GROUP_SETTINGS
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    return {k: v for k, v in required.items() if current.get(k) != v}


def ensure_group_settings(settings_service, gemail: str, required: dict = None) -> dict:
    """Apply `required` settings (default REQUIRED_GROUP_SETTINGS) to the
    group. Return dict of {field: new_value} for changed fields."""
    updates = compute_group_settings_updates(settings_service, gemail, required)
    if updates:
        settings_service.groups().update(groupUniqueId=gemail, body=updates).execute()
    return updates


def ensure_who_can_post_web(settings_service, gemail: str) -> bool:
    """Deprecated shim — use ensure_group_settings instead."""
    updates = ensure_group_settings(settings_service, gemail)
    return "whoCanPostMessage" in updates
