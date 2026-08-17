"""
Manages the "auto-managed links" block within a Google Group's custom
footer (Groups Settings API: customFooterText / includeCustomFooter).

The footer is the source of truth for which Drive item a Google Group's
membership should sync to (see groups_drive_sync.gs): rather than
maintaining a separate item_id/group_email mapping file that the Apps
Script project has to be redeployed to pick up, each group carries its own
link, so groups_drive_sync.gs can just enumerate every group in the
workspace and read the item ID straight off it.

A group can be linked to one of two kinds of Drive item, which the footer
distinguishes by its link label:

  * a **folder** — "<Group Name> Google Docs folder: <url>". Membership
    syncs from the folder's "Content manager" (fileOrganizer) permissions.
  * a **shared drive** — "<Group Name> Shared Drive: <url>". Membership
    syncs from the shared drive's "Viewer" (reader) permissions.

Both use the same drive.google.com/drive/folders/<id> URL shape (a shared
drive's ID is usable as a folder URL), so the label — not the URL — is what
tells the two apart. A group is only ever linked to a single item; a footer
that somehow carries both a folder link and a shared-drive link is treated
as an error rather than guessed at.

The block's link labels bake in the group's current display name (there's
no way to make a plain-text footer field render a link with custom anchor
text separate from the URL, so the name has to appear alongside it). This
means a rename isn't reflected until the next time a footer-writing step
runs for that group — which happens whenever
update_groups_in_google_and_hierarchy.py syncs it, same as any other
rename. Any other footer content a human wrote is preserved — this module
only ever replaces its own marked block.
"""

import re

FOOTER_MARKER = "--- Auto-managed links ---"

# Link kinds a group can be associated with, and the footer label each uses.
ITEM_TYPE_FOLDER = "folder"
ITEM_TYPE_SHARED_DRIVE = "shared_drive"

_ITEM_LABELS = {
    ITEM_TYPE_FOLDER: "Google Docs folder",
    ITEM_TYPE_SHARED_DRIVE: "Shared Drive",
}

_DRIVE_URL_RE = r"https://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)"

# Per-type "<label>: <url>" matchers, used to read a written footer back.
_ITEM_LINK_RES = {
    item_type: re.compile(re.escape(label) + r":\s*" + _DRIVE_URL_RE)
    for item_type, label in _ITEM_LABELS.items()
}

# Kept for backward compatibility: a bare folder-URL matcher.
_FOLDER_URL_RE = re.compile(_DRIVE_URL_RE)


def drive_folder_url(item_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{item_id}"


def _item_label(item_type: str) -> str:
    try:
        return _ITEM_LABELS[item_type]
    except KeyError:
        raise ValueError(f"Unknown Drive item type {item_type!r}")


def build_footer_block(
    gather_group_id: str, item_id: str, base_url: str, group_name: str,
    item_type: str = ITEM_TYPE_FOLDER,
) -> str:
    return (
        f"{FOOTER_MARKER}\n"
        f"{group_name} Gather group: {base_url}/groups/{gather_group_id}\n"
        f"{group_name} {_item_label(item_type)}: {drive_folder_url(item_id)}"
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


def parse_footer_link(footer_text: str) -> tuple[str, str] | None:
    """Extract (item_id, item_type) from a footer's managed block, if any.

    item_type is one of ITEM_TYPE_FOLDER / ITEM_TYPE_SHARED_DRIVE, decided
    by which labeled link is present. Raises ValueError if the block
    somehow carries both a folder link and a shared-drive link — a group is
    only ever meant to be tied to a single Drive item, so an ambiguous
    footer is a hard error rather than a silent guess."""
    text = footer_text or ""
    _, block = split_footer(text)
    search_in = block or text
    found: list[tuple[str, str]] = []
    for item_type, pattern in _ITEM_LINK_RES.items():
        m = pattern.search(search_in)
        if m:
            found.append((m.group(1), item_type))
    if len(found) > 1:
        kinds = ", ".join(sorted(t for _, t in found))
        raise ValueError(
            f"Footer links to more than one Drive item ({kinds}); a group "
            f"must be tied to exactly one folder or shared drive"
        )
    return found[0] if found else None


def parse_footer_folder_id(footer_text: str) -> str | None:
    """Extract the Drive item ID from a footer's managed block, if any,
    regardless of whether it's a folder or shared-drive link. Retained for
    backward compatibility with callers that only want the ID."""
    link = parse_footer_link(footer_text)
    return link[0] if link else None


def compute_group_footer_updates(
    settings_service, gemail: str, gather_group_id: str, item_id: str, base_url: str,
    group_name: str, item_type: str = ITEM_TYPE_FOLDER,
) -> dict:
    """Return {field: new_value} for the group's customFooterText /
    includeCustomFooter settings, without applying them. Read-only — safe
    to call from a dry run. Raises if the group doesn't exist."""
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    current_footer = current.get("customFooterText") or ""
    prefix, existing_block = split_footer(current_footer)
    desired_block = build_footer_block(gather_group_id, item_id, base_url, group_name, item_type)

    updates = {}
    if existing_block != desired_block:
        updates["customFooterText"] = f"{prefix}\n\n{desired_block}" if prefix else desired_block
    if current.get("includeCustomFooter") != "true":
        updates["includeCustomFooter"] = "true"
    return updates


def ensure_group_footer(
    settings_service, gemail: str, gather_group_id: str, item_id: str, base_url: str,
    group_name: str, item_type: str = ITEM_TYPE_FOLDER,
) -> dict:
    """Apply the group's auto-managed footer block, preserving any other
    footer content. Return dict of {field: new_value} for changed fields."""
    updates = compute_group_footer_updates(
        settings_service, gemail, gather_group_id, item_id, base_url, group_name, item_type
    )
    if updates:
        settings_service.groups().update(groupUniqueId=gemail, body=updates).execute()
    return updates


def fetch_footer_link(settings_service, gemail: str) -> tuple[str, str] | None:
    """Read a group's current footer and return the (item_id, item_type)
    embedded in it, if any. Read-only. Raises if the group doesn't exist,
    or if the footer ambiguously links to more than one item."""
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    return parse_footer_link(current.get("customFooterText"))


def fetch_footer_folder_id(settings_service, gemail: str) -> str | None:
    """Read a group's current footer and return the Drive item ID embedded
    in it, if any (folder or shared drive). Read-only. Raises if the group
    doesn't exist. Retained for callers that only need the ID."""
    link = fetch_footer_link(settings_service, gemail)
    return link[0] if link else None
