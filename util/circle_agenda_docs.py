"""
Each circle's and working group's agenda doc (see circle_agendas/README.md).

The Python scripts know which groups get one (circles and working groups,
not clubs or everybody groups), where each group's Drive item is, and its
parent in the Circle Structure; the circle_agendas Apps Script knows how
to build the doc. So a script here creates the doc empty, in the group's
Drive item, named "<Group> Agenda", and labels it (Drive appProperties,
which users don't see) with the group's address, its parent's name, and
the state "new"; the Apps Script's buildNewCircleDocs, run on a timer,
builds every doc labelled "new" and relabels it "built". The label is also
how a group's doc is found again, so a group never gets a second one.

Must match AGENDA_KEYS in circle_agendas/circle_docs.gs.
"""

from util.gather_utils import log

GROUP_KEY = "circleAgendaGroup"
PARENT_KEY = "circleAgendaParent"
STATE_KEY = "circleAgendaState"
STATE_NEW = "new"

# The Gather group kinds that get an agenda doc ("committee" is a working
# group).
AGENDA_KINDS = {"circle", "committee"}

# Drive allows 124 bytes for an appProperty's key and value together.
_MAX_PROPERTY_BYTES = 124


def agenda_doc_name(group_name: str) -> str:
    return f"{group_name} Agenda"


def find_agenda_doc(drive_service, group_email: str) -> dict | None:
    """The group's agenda doc ({id, name}), wherever it is, or None."""
    query = (f"appProperties has {{ key='{GROUP_KEY}' and value='{group_email.lower()}' }} "
             "and trashed = false")
    files = drive_service.files().list(
        q=query, corpora="allDrives", includeItemsFromAllDrives=True, supportsAllDrives=True,
        fields="files(id,name)").execute().get("files", [])
    return files[0] if files else None


def _fit(key: str, value: str) -> str:
    room = _MAX_PROPERTY_BYTES - len(key.encode())
    encoded = value.encode()[:room]
    return encoded.decode("utf-8", errors="ignore")


def ensure_agenda_doc(drive_service, group_email: str, group_name: str, drive_item_id: str,
                      parent_name: str | None, dry_run: bool) -> str | None:
    """Create the group's agenda doc, empty and labelled to be built, in its
    Drive item, unless it has one. Returns the doc's ID (None in a dry run
    that would have created one)."""
    existing = find_agenda_doc(drive_service, group_email)
    if existing:
        return existing["id"]
    name = agenda_doc_name(group_name)
    if dry_run:
        log("INFO", "agenda_doc", f"[DRY RUN] Would create '{name}' for {group_email}")
        return None
    properties = {GROUP_KEY: group_email.lower(), STATE_KEY: STATE_NEW}
    if parent_name:
        properties[PARENT_KEY] = _fit(PARENT_KEY, parent_name)
    created = drive_service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.document",
              "parents": [drive_item_id], "appProperties": properties},
        supportsAllDrives=True, fields="id").execute()
    log("INFO", "agenda_doc",
        f"Created '{name}' for {group_email} (id={created['id']}); the circle_agendas "
        "Apps Script will build it within minutes")
    return created["id"]
