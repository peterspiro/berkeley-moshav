"""
Manages the "auto-managed links" block within a Google Group's custom
footer (Groups Settings API: customFooterText / includeCustomFooter).

The block links the group's other resources, under a header line naming
the group:

    --- Auto-managed links ---
    Technology Circle's:
    Gather group: https://berkeley-moshav.gather.coop/groups/1093
    Google Docs folder: https://drive.google.com/drive/folders/<id>
    Google calendar: https://calendar.google.com/calendar/u/0?cid=<base64 id>
    Zoom meeting: https://us06web.zoom.us/j/<id>?pwd=<passcode>

The calendar and Zoom lines appear only once the group has a calendar and
a meeting room.

The footer is the source of truth for which Drive item a Google Group's
membership should sync to (see groups_drive_sync.gs): rather than
maintaining a separate item_id/group_email mapping file that the Apps
Script project has to be redeployed to pick up, each group carries its own
link, so groups_drive_sync.gs can just enumerate every group in the
workspace and read the item ID straight off it. The calendar and Zoom
links are likewise the source of truth for each group's calendar and room.

A group can be linked to one of two kinds of Drive item, which the footer
distinguishes by its link label:

  * a **folder** — "Google Docs folder: <url>". Membership syncs from the
    folder's "Content manager" (fileOrganizer) permissions.
  * a **shared drive** — "Shared Drive: <url>". Membership syncs from the
    shared drive's "Viewer" (reader) permissions.

Both use the same drive.google.com/drive/folders/<id> URL shape (a shared
drive's ID is usable as a folder URL), so the label — not the URL — is what
tells the two apart. A group is only ever linked to a single item; a footer
that somehow carries both a folder link and a shared-drive link is treated
as an error rather than guessed at. Older footers put the group's name in
front of every label ("Foo Circle Google Docs folder: <url>"); the parsers
match the label wherever it appears, so those still read correctly.

The header line bakes in the group's current display name, so a rename
isn't reflected until the next time a footer-writing step runs for that
group — which happens whenever update_groups_in_google_and_hierarchy.py
syncs it, same as any other rename. Any other footer content a human wrote
is preserved — this module only ever replaces its own marked block.
"""

import base64
import binascii
import re
from urllib.parse import quote, unquote

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

_CALENDAR_LABEL = "Google calendar"
_ZOOM_LABEL = "Zoom meeting"

_CALENDAR_URL_PREFIX = "https://calendar.google.com/calendar/u/0?cid="
_CALENDAR_LINK_RE = re.compile(
    re.escape(_CALENDAR_LABEL) + r":\s*" + re.escape(_CALENDAR_URL_PREFIX)
    + r"([A-Za-z0-9%+/_=-]+)"
)
_ZOOM_LINK_RE = re.compile(re.escape(_ZOOM_LABEL) + r":\s*(https://\S+)")


def drive_folder_url(item_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{item_id}"


def calendar_url(calendar_id: str) -> str:
    """The link that offers to add calendar_id to the clicker's own
    Google Calendar list: the ID, base64-encoded without padding, as the
    `cid` parameter (percent-encoded, since "+" and "/" can occur)."""
    encoded = base64.b64encode(calendar_id.encode()).decode().rstrip("=")
    return _CALENDAR_URL_PREFIX + quote(encoded, safe="")


def calendar_id_from_cid(cid: str) -> str | None:
    """Decode a calendar_url() `cid` value back to the calendar ID, or
    None if it isn't valid base64. Accepts the URL-safe alphabet and
    missing padding as well."""
    cid = unquote(cid).replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(cid + "=" * (-len(cid) % 4), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None


def _item_label(item_type: str) -> str:
    try:
        return _ITEM_LABELS[item_type]
    except KeyError:
        raise ValueError(f"Unknown Drive item type {item_type!r}")


def build_footer_block(
    gather_group_id: str, item_id: str, base_url: str, group_name: str,
    item_type: str = ITEM_TYPE_FOLDER, calendar_id: str | None = None,
    zoom_url: str | None = None,
) -> str:
    """The managed block, from FOOTER_MARKER on. The calendar and Zoom
    lines are left out when calendar_id / zoom_url is None."""
    lines = [
        FOOTER_MARKER,
        f"{group_name}'s:",
        f"Gather group: {base_url}/groups/{gather_group_id}",
        f"{_item_label(item_type)}: {drive_folder_url(item_id)}",
    ]
    if calendar_id:
        lines.append(f"{_CALENDAR_LABEL}: {calendar_url(calendar_id)}")
    if zoom_url:
        lines.append(f"{_ZOOM_LABEL}: {zoom_url}")
    return "\n".join(lines)


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
    search_in = _managed_text(footer_text)
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


def _managed_text(footer_text: str | None) -> str:
    """The managed block of footer_text, or all of it if it has none."""
    text = footer_text or ""
    _, block = split_footer(text)
    return block or text


def parse_footer_calendar_id(footer_text: str | None) -> str | None:
    """Extract the calendar ID from a footer's "Google calendar" link, if
    any."""
    m = _CALENDAR_LINK_RE.search(_managed_text(footer_text))
    return calendar_id_from_cid(m.group(1)) if m else None


def parse_footer_zoom_url(footer_text: str | None) -> str | None:
    """Extract the full Zoom meeting link (passcode included) from a
    footer's "Zoom meeting" line, if any."""
    m = _ZOOM_LINK_RE.search(_managed_text(footer_text))
    return m.group(1) if m else None


def parse_footer_folder_id(footer_text: str) -> str | None:
    """Extract the Drive item ID from a footer's managed block, if any,
    regardless of whether it's a folder or shared-drive link. Retained for
    backward compatibility with callers that only want the ID."""
    link = parse_footer_link(footer_text)
    return link[0] if link else None


def footer_updates(
    current_settings: dict, gather_group_id: str, item_id: str, base_url: str,
    group_name: str, item_type: str = ITEM_TYPE_FOLDER, calendar_id: str | None = None,
    zoom_url: str | None = None,
) -> dict:
    """Return {field: new_value} for the customFooterText /
    includeCustomFooter settings, given the group's current settings (a
    Groups Settings API groups().get() result). Pure — no API calls.

    A calendar_id or zoom_url left as None keeps whatever calendar or Zoom
    link the footer already has, so a caller that doesn't know them never
    drops them."""
    current_footer = current_settings.get("customFooterText") or ""
    prefix, existing_block = split_footer(current_footer)
    if calendar_id is None:
        calendar_id = parse_footer_calendar_id(existing_block)
    if zoom_url is None:
        zoom_url = parse_footer_zoom_url(existing_block)
    desired_block = build_footer_block(
        gather_group_id, item_id, base_url, group_name, item_type, calendar_id, zoom_url
    )

    updates = {}
    if existing_block != desired_block:
        updates["customFooterText"] = f"{prefix}\n\n{desired_block}" if prefix else desired_block
    if current_settings.get("includeCustomFooter") != "true":
        updates["includeCustomFooter"] = "true"
    return updates


def compute_group_footer_updates(
    settings_service, gemail: str, gather_group_id: str, item_id: str, base_url: str,
    group_name: str, item_type: str = ITEM_TYPE_FOLDER, calendar_id: str | None = None,
    zoom_url: str | None = None,
) -> dict:
    """footer_updates() for the group's current settings, fetched from the
    API. Read-only — safe to call from a dry run. Raises if the group
    doesn't exist."""
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    return footer_updates(
        current, gather_group_id, item_id, base_url, group_name, item_type, calendar_id, zoom_url
    )


_GATHER_LINK_RE = re.compile(r"Gather group:\s*(\S+)")
_HEADER_RE = re.compile(r"^(.*)'s:$", re.MULTILINE)


def _safe_parse_footer_link(footer_text: str) -> tuple[str, str] | None | str:
    try:
        return parse_footer_link(footer_text)
    except ValueError:
        return "ambiguous"


def describe_footer_updates(current_footer: str | None, updates: dict) -> str:
    """A short, human-readable account of what footer_updates() would
    change, e.g. "Drive link -> folder <id>; turn footer on", or
    "new layout (links unchanged)". Empty if updates is empty."""
    parts = []
    if "customFooterText" in updates:
        new_text = updates["customFooterText"]
        _, old_block = split_footer(current_footer or "")
        _, new_block = split_footer(new_text)
        if not old_block:
            parts.append("add link block")
        else:
            old_link = _safe_parse_footer_link(old_block)
            new_link = parse_footer_link(new_block)
            if old_link != new_link:
                item_id, item_type = new_link
                parts.append(f"Drive link -> {item_type} {item_id}")
            old_gather = _GATHER_LINK_RE.search(old_block)
            new_gather = _GATHER_LINK_RE.search(new_block)
            if (old_gather and old_gather.group(1)) != (new_gather and new_gather.group(1)):
                parts.append(f"Gather link -> {new_gather.group(1)}")
            if parse_footer_calendar_id(old_block) != parse_footer_calendar_id(new_block):
                parts.append("calendar link")
            if parse_footer_zoom_url(old_block) != parse_footer_zoom_url(new_block):
                parts.append("Zoom link")
            if not parts:
                old_header = _HEADER_RE.search(old_block)
                new_name = _HEADER_RE.search(new_block).group(1)
                if not old_header:
                    parts.append("new layout (links unchanged)")
                elif old_header.group(1) != new_name:
                    parts.append(f"group name -> {new_name!r}")
                else:
                    parts.append("rewrite link block")
    if "includeCustomFooter" in updates:
        parts.append("turn footer on")
    return "; ".join(parts)


def ensure_group_footer(
    settings_service, gemail: str, gather_group_id: str, item_id: str, base_url: str,
    group_name: str, item_type: str = ITEM_TYPE_FOLDER, calendar_id: str | None = None,
    zoom_url: str | None = None,
) -> dict:
    """Apply the group's auto-managed footer block, preserving any other
    footer content (and, when calendar_id / zoom_url is None, the existing
    calendar / Zoom link). Return dict of {field: new_value} for changed
    fields."""
    updates = compute_group_footer_updates(
        settings_service, gemail, gather_group_id, item_id, base_url, group_name, item_type,
        calendar_id, zoom_url,
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


def fetch_footer_calendar_id(settings_service, gemail: str) -> str | None:
    """Read a group's current footer and return the calendar ID linked in
    it, if any. Read-only. Raises if the group doesn't exist."""
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    return parse_footer_calendar_id(current.get("customFooterText"))


def fetch_footer_zoom_url(settings_service, gemail: str) -> str | None:
    """Read a group's current footer and return the Zoom meeting link in
    it, if any. Read-only. Raises if the group doesn't exist."""
    current = settings_service.groups().get(groupUniqueId=gemail).execute()
    return parse_footer_zoom_url(current.get("customFooterText"))
