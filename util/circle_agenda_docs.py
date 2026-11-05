"""
Each circle's and working group's agenda doc (see circle_agendas/README.md).

The Python scripts know which groups get one (circles and working groups,
not clubs or everybody groups) and where each group's Drive item is; the
circle_agendas Apps Script knows how to build the doc. So a script here
creates the doc empty, in the group's Drive item, named "<Group> Agenda",
and labels it (Drive file properties, which users don't see) with the
group's address and the state "new"; the Apps Script's buildNewCircleDocs,
run on a timer, builds every doc labelled "new" and relabels it "built".
The label is also how a group's doc is found again, so a group never gets
a second one. (The doc's Parent Circle and Sub-Circles rows come from the
Circle Structure sheet; see circle_agendas/structure.gs.)

The label is in public properties, not appProperties: appProperties are
private to the app (OAuth client) that wrote them, and the Apps Script
isn't the same app as these scripts. Docs labelled in appProperties by an
earlier version are relabelled when found.

Must match AGENDA_KEYS in circle_agendas/circle_docs.gs.
"""

from util.gather_utils import log

GROUP_KEY = "circleAgendaGroup"
STATE_KEY = "circleAgendaState"
STATE_NEW = "new"
STATE_BUILT = "built"

# The Gather group kinds that get an agenda doc ("committee" is a working
# group).
AGENDA_KINDS = {"circle", "committee"}


def agenda_doc_name(group_name: str) -> str:
    return f"{group_name} Agenda"


def find_agenda_doc(drive_service, group_email: str) -> dict | None:
    """The group's agenda doc ({id, name, properties, appProperties}),
    wherever it is, or None."""
    label = f"{{ key='{GROUP_KEY}' and value='{group_email.lower()}' }}"
    query = f"(properties has {label} or appProperties has {label}) and trashed = false"
    files = drive_service.files().list(
        q=query, corpora="allDrives", includeItemsFromAllDrives=True, supportsAllDrives=True,
        fields="files(id,name,properties,appProperties)").execute().get("files", [])
    return files[0] if files else None


def ensure_agenda_doc(drive_service, group_email: str, group_name: str, drive_item_id: str,
                      dry_run: bool) -> str | None:
    """Create the group's agenda doc, empty and labelled to be built, in its
    Drive item, unless it has one. Returns the doc's ID (None in a dry run
    that would have created one)."""
    existing = find_agenda_doc(drive_service, group_email)
    if existing:
        old_label = existing.get("appProperties") or {}
        if GROUP_KEY not in (existing.get("properties") or {}) and old_label and not dry_run:
            drive_service.files().update(
                fileId=existing["id"], body={"properties": old_label},
                supportsAllDrives=True).execute()
            log("INFO", "agenda_doc", f"Relabelled '{existing['name']}' so the Apps Script sees it")
        log("INFO", "agenda_doc",
            f"{group_email} already has '{existing['name']}' "
            f"(https://docs.google.com/document/d/{existing['id']}/edit); leaving it as it is")
        return existing["id"]
    name = agenda_doc_name(group_name)
    if dry_run:
        log("INFO", "agenda_doc", f"[DRY RUN] Would create '{name}' for {group_email}")
        return None
    properties = {GROUP_KEY: group_email.lower(), STATE_KEY: STATE_NEW}
    created = drive_service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.document",
              "parents": [drive_item_id], "properties": properties},
        supportsAllDrives=True, fields="id").execute()
    log("INFO", "agenda_doc",
        f"Created '{name}' for {group_email}: "
        f"https://docs.google.com/document/d/{created['id']}/edit (the circle_agendas "
        "Apps Script will build it within minutes)")
    return created["id"]
