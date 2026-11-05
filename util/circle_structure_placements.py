"""
Parents for groups in the Circle Structure, from the Python scripts.

The Circle Structure is a Google Sheet that the circle_agendas Apps Script
keeps (see circle_agendas/structure.gs): its rows are the groups, which it
finds from the Google Groups' footers, and only the Coordinating Circle
changes their parents there. A script here that knows a group's parent (a
new group's, from add_new_group.py; every group's, from the old wiki page,
from seed_circle_structure.py) writes it to PLACEMENTS_FILE, a JSON object
of Gather group ID -> its parent's Gather group ID, in the signed-in
account's (admin@'s) My Drive. The Apps Script gives each group there whose
Parent is still empty that parent (if the rules allow it), and removes it
from the file.
"""

import io
import json

from googleapiclient.http import MediaIoBaseUpload

from util.gather_utils import log

# Must match PLACEMENTS_FILE in circle_agendas/structure.gs.
PLACEMENTS_FILE = "Circle structure placements.json"

# The Gather group kinds in the Circle Structure, and which kinds each may be
# under: a circle only under a circle, a working group ("committee") under
# either (as canBeUnder in circle_agendas/structure_rules.gs).
STRUCTURE_KINDS = {"circle", "committee"}
PARENT_KINDS = {"circle": {"circle"}, "committee": {"circle", "committee"}}


def check_parent_kind(child_kind: str, parent_name: str, parent_kind: str) -> None:
    """Raises ValueError unless a group of child_kind may be under one of
    parent_kind."""
    allowed = PARENT_KINDS.get(child_kind, set())
    if parent_kind not in allowed:
        what = "a circle" if allowed == {"circle"} else "a circle or working group"
        raise ValueError(f"'{parent_name}' isn't {what} (its Gather kind is "
                         f"{parent_kind or 'unknown'!r}), so it can't be the parent")


def _find_file(drive_service) -> str | None:
    query = f"name = '{PLACEMENTS_FILE}' and 'root' in parents and trashed = false"
    files = drive_service.files().list(q=query, fields="files(id)").execute().get("files", [])
    return files[0]["id"] if files else None


def read_placements(drive_service) -> dict[str, str]:
    """The placements waiting in PLACEMENTS_FILE ({} if there's none)."""
    file_id = _find_file(drive_service)
    if not file_id:
        return {}
    content = drive_service.files().get_media(fileId=file_id).execute()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return json.loads(content or "{}")


def add_placements(drive_service, placements: dict[str, str], dry_run: bool) -> None:
    """Add placements (Gather group ID -> parent's Gather group ID) to
    PLACEMENTS_FILE, creating it if need be."""
    if not placements:
        return
    if dry_run:
        log("INFO", "placements",
            f"[DRY RUN] Would write {len(placements)} parent(s) to '{PLACEMENTS_FILE}'")
        return
    file_id = _find_file(drive_service)
    merged = {**(read_placements(drive_service) if file_id else {}), **placements}
    content = json.dumps(merged, indent=1, sort_keys=True)
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="application/json")
    if file_id:
        drive_service.files().update(fileId=file_id, media_body=media).execute()
    else:
        drive_service.files().create(
            body={"name": PLACEMENTS_FILE, "mimeType": "application/json"},
            media_body=media, fields="id").execute()
    log("INFO", "placements", f"Wrote {len(placements)} parent(s) to '{PLACEMENTS_FILE}'")
