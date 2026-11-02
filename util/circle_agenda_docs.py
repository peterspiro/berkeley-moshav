"""
Each circle's and working group's agenda doc (see circle_agendas/README.md).

The Python scripts know which groups get one (circles and working groups,
not clubs or everybody groups), where each group's Drive item is, and its
parent in the Circle Structure; the circle_agendas Apps Script knows how
to build the doc. So a script here creates the doc empty, in the group's
Drive item, named "<Group> Agenda", and labels it (Drive file properties,
which users don't see) with the group's address, its parent's name, its
sub-circles' names (up to MAX_SUB_CIRCLES), and the state "new"; the Apps Script's buildNewCircleDocs, run on a timer,
builds every doc labelled "new" and relabels it "built". The label is also
how a group's doc is found again, so a group never gets a second one.

The label is in public properties, not appProperties: appProperties are
private to the app (OAuth client) that wrote them, and the Apps Script
isn't the same app as these scripts. Docs labelled in appProperties by an
earlier version are relabelled when found.

Must match AGENDA_KEYS in circle_agendas/circle_docs.gs, which links each
parent and sub-circle named to its folder, found by name among the groups'
footers.

When a circle moves to another parent (change_parent_circle.py),
relabel_agenda_doc_structure relabels the docs of the circle and of its
old and new parents with their new parent and sub-circles, and labels
each built one to have those rows of About this circle rewritten.
"""

from util.gather_utils import log

GROUP_KEY = "circleAgendaGroup"
PARENT_KEY = "circleAgendaParent"
STATE_KEY = "circleAgendaState"
# Sub-circles' names go in SUB_KEY + "1", SUB_KEY + "2", and so on.
SUB_KEY = "circleAgendaSub"
MAX_SUB_CIRCLES = 12
STATE_NEW = "new"
STATE_BUILT = "built"
# Set on a built doc whose parent or sub-circles have changed, so the Apps
# Script rewrites those rows of About this circle (and then removes it).
REFRESH_KEY = "circleAgendaRefresh"
REFRESH_STRUCTURE = "structure"

# The Gather group kinds that get an agenda doc ("committee" is a working
# group).
AGENDA_KINDS = {"circle", "committee"}

# Drive allows 124 bytes for an appProperty's key and value together.
_MAX_PROPERTY_BYTES = 124


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


def _fit(key: str, value: str) -> str:
    room = _MAX_PROPERTY_BYTES - len(key.encode())
    encoded = value.encode()[:room]
    return encoded.decode("utf-8", errors="ignore")


def ensure_agenda_doc(drive_service, group_email: str, group_name: str, drive_item_id: str,
                      parent_name: str | None, dry_run: bool,
                      sub_names: list[str] = ()) -> str | None:
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
    if parent_name:
        properties[PARENT_KEY] = _fit(PARENT_KEY, parent_name)
    for i, sub_name in enumerate(sub_names[:MAX_SUB_CIRCLES], start=1):
        properties[f"{SUB_KEY}{i}"] = _fit(f"{SUB_KEY}{i}", sub_name)
    created = drive_service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.document",
              "parents": [drive_item_id], "properties": properties},
        supportsAllDrives=True, fields="id").execute()
    log("INFO", "agenda_doc",
        f"Created '{name}' for {group_email}: "
        f"https://docs.google.com/document/d/{created['id']}/edit (the circle_agendas "
        "Apps Script will build it within minutes)")
    return created["id"]


def structure_properties(parent_name: str | None, sub_names: list[str]) -> dict:
    """The parent and sub-circle labels for a doc, with None (which Drive
    takes as "remove") for each label it no longer has."""
    properties = {PARENT_KEY: _fit(PARENT_KEY, parent_name) if parent_name else None}
    for i in range(1, MAX_SUB_CIRCLES + 1):
        key = f"{SUB_KEY}{i}"
        properties[key] = _fit(key, sub_names[i - 1]) if i <= len(sub_names) else None
    return properties


def relabel_agenda_doc_structure(drive_service, group_email: str, parent_name: str | None,
                                 sub_names: list[str], dry_run: bool) -> str:
    """Label the group's agenda doc with its parent's and sub-circles'
    names, and, if it's built, label it to have the Apps Script rewrite
    the Parent Circle and Sub-Circles rows of About this circle. Returns
    "no doc", "unchanged", or "relabelled" (in a dry run, what it would
    do)."""
    doc = find_agenda_doc(drive_service, group_email)
    if doc is None:
        log("INFO", "agenda_doc", f"{group_email} has no agenda doc; nothing to relabel")
        return "no doc"
    current = doc.get("properties") or {}
    label = current if GROUP_KEY in current else {**(doc.get("appProperties") or {}), **current}
    wanted = structure_properties(parent_name, list(sub_names))
    changed = {k: v for k, v in wanted.items() if label.get(k) != v}
    if not changed and GROUP_KEY in current:
        log("INFO", "agenda_doc", f"'{doc['name']}' already names its parent and sub-circles")
        return "unchanged"
    body = {**label, **changed} if GROUP_KEY not in current else dict(changed)
    state = label.get(STATE_KEY)
    if state == STATE_BUILT:
        body[REFRESH_KEY] = REFRESH_STRUCTURE
    elif state != STATE_NEW:
        log("WARN", "agenda_doc",
            f"'{doc['name']}' is labelled {state!r}, not built, so its About this circle "
            "won't be rewritten; check it by hand")
    if dry_run:
        log("INFO", "agenda_doc", f"[DRY RUN] Would relabel '{doc['name']}': {changed}")
        return "relabelled"
    drive_service.files().update(fileId=doc["id"], body={"properties": body},
                                 supportsAllDrives=True).execute()
    log("INFO", "agenda_doc", f"Relabelled '{doc['name']}': {changed}")
    return "relabelled"
