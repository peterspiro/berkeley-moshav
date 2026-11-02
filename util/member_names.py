"""
Members' names, from the Gather directory, for the circle agenda docs.

The circle_agendas Apps Script lists a circle's members (its Google Group's
addresses) in each new meeting's attendance and Presenter choices. Google
gives it names only for accounts that share them, and often just the part
of the address before the @, so it looks names up in MEMBER_NAMES_FILE, a
JSON object of email address -> name in the signed-in account's
(admin@'s) My Drive, which update_groups_in_google_and_hierarchy.py writes
on each run. Both a member's Gather email and their Google Email (the
address Gather shares Drive items with) are listed.
"""

import io
import json

from googleapiclient.http import MediaIoBaseUpload

from util.gather_utils import GatherUser, log

# Must match MEMBER_NAMES_FILE in circle_agendas/circles.gs.
MEMBER_NAMES_FILE = "Circle agenda member names.json"


def build_member_names(users: list[GatherUser]) -> dict[str, str]:
    """Email address (lowercase) -> full name, for every user with a name,
    under both their Gather email and their Google Email."""
    names: dict[str, str] = {}
    for user in users:
        if not user.full_name:
            continue
        for address in (user.email, user.google_email):
            if address:
                names[address.strip().lower()] = user.full_name
    return dict(sorted(names.items()))


def publish_member_names(drive_service, names: dict[str, str], dry_run: bool) -> None:
    """Write names to MEMBER_NAMES_FILE in the signed-in account's My Drive,
    creating it if need be; leave it alone if it already says the same."""
    content = json.dumps(names, indent=1, ensure_ascii=False, sort_keys=True)
    query = f"name = '{MEMBER_NAMES_FILE}' and 'root' in parents and trashed = false"
    existing = drive_service.files().list(q=query, fields="files(id)").execute().get("files", [])

    if existing:
        file_id = existing[0]["id"]
        current = drive_service.files().get_media(fileId=file_id).execute()
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if current == content:
            log("INFO", "member_names", f"{len(names)} member name(s) already up to date")
            return
    if dry_run:
        log("INFO", "member_names",
            f"[DRY RUN] Would write {len(names)} member name(s) to '{MEMBER_NAMES_FILE}'")
        return

    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="application/json")
    if existing:
        drive_service.files().update(fileId=existing[0]["id"], media_body=media).execute()
    else:
        drive_service.files().create(
            body={"name": MEMBER_NAMES_FILE, "mimeType": "application/json"},
            media_body=media, fields="id").execute()
    log("INFO", "member_names", f"Wrote {len(names)} member name(s) to '{MEMBER_NAMES_FILE}'")
