"""
Manages the "auto-managed links" block within a Google Group's custom
footer (Groups Settings API: customFooterText / includeCustomFooter).

The footer is the source of truth for which Drive folder a Google Group's
membership should sync to (see groups_drive_sync.gs): rather than
maintaining a separate folder_id/group_email mapping file that the Apps
Script project has to be redeployed to pick up, each group carries its own
folder link, so groups_drive_sync.gs can just enumerate every group in the
workspace and read the folder ID straight off it.

The block's link labels are fixed ("Gather Group" / "Google Docs folder"),
never the group/folder's current display name, so renames never require
touching the footer. Any other footer content a human wrote is preserved —
this module only ever replaces its own marked block.
"""

import re

FOOTER_MARKER = "--- Auto-managed links ---"

_FOLDER_URL_RE = re.compile(r"https://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)")


def drive_folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def build_footer_block(gather_group_id: str, folder_id: str, base_url: str) -> str:
    return (
        f"{FOOTER_MARKER}\n"
        f"Gather Group: {base_url}/groups/{gather_group_id}\n"
        f"Google Docs folder: {drive_folder_url(folder_id)}"
    )


def split_footer(footer_text: str) -> tuple[str, str]:
    """Split footer_text into (preserved_prefix, managed_block), where
    managed_block is everything from FOOTER_MARKER onward, or "" if the
    marker isn't present. preserved_prefix has trailing blank lines
    stripped so re-assembling it with the block doesn't accumulate them."""
    idx = footer_text.find(FOOTER_MARKER)
    if idx == -1:
        return footer_text, ""
    return footer_text[:idx].rstrip("\n"), footer_text[idx:]


def parse_footer_folder_id(footer_text: str) -> str | None:
    """Extract the Drive folder ID from a footer's managed block, if any."""
    m = _FOLDER_URL_RE.search(footer_text or "")
    return m.group(1) if m else None


def compute_group_footer_updates(
    settings_service, gemail: str, gather_group_id: str, folder_id: str, base_url: str,
) -> dict:
    """Return {field: new_value} for the group's customFooterText /
    includeCustomFooter settings, without applying them. Read-only — safe
    to call from a dry run. Raises if the group doesn't exist."""
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    current_footer = current.get("customFooterText") or ""
    prefix, existing_block = split_footer(current_footer)
    desired_block = build_footer_block(gather_group_id, folder_id, base_url)

    updates = {}
    if existing_block != desired_block:
        updates["customFooterText"] = f"{prefix}\n\n{desired_block}" if prefix else desired_block
    if current.get("includeCustomFooter") != "true":
        updates["includeCustomFooter"] = "true"
    return updates


def ensure_group_footer(
    settings_service, gemail: str, gather_group_id: str, folder_id: str, base_url: str,
) -> dict:
    """Apply the group's auto-managed footer block, preserving any other
    footer content. Return dict of {field: new_value} for changed fields."""
    updates = compute_group_footer_updates(settings_service, gemail, gather_group_id, folder_id, base_url)
    if updates:
        settings_service.groups().update(groupUniqueId=gemail, body=updates).execute()
    return updates


def fetch_footer_folder_id(settings_service, gemail: str) -> str | None:
    """Read a group's current footer and return the Drive folder ID
    embedded in it, if any. Read-only. Raises if the group doesn't exist."""
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    return parse_footer_folder_id(current.get("customFooterText"))
